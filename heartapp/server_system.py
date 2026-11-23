"""服务端的系统集成：路径约定、systemd 单元、防火墙、健康检查。

安装/卸载/状态都走这里，避免再出现「bash 拼 unit 字符串」那种分散逻辑。
"""
from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import time
from pathlib import Path

APP_ROOT = Path('/opt/heartapp-relay')
RELEASES = APP_ROOT / 'releases'
CURRENT = APP_ROOT / 'current'
UNIT_PATH = Path('/etc/systemd/system/heartapp-relay.service')
SERVICE = 'heartapp-relay'

TLS_DIR = Path('/etc/heartapp-tls')
ACME_DIR = Path('/etc/heartapp-acme')
ACME_WORK = Path('/var/lib/heartapp-acme')
ACME_LOGS = Path('/var/log/heartapp-acme')
RENEW_SERVICE = Path('/etc/systemd/system/heartapp-ip-renew.service')
RENEW_TIMER = Path('/etc/systemd/system/heartapp-ip-renew.timer')

LAUNCHER_NAME = 'heartapp-server'
SYSTEM_BIN = Path('/usr/local/bin') / LAUNCHER_NAME


def run(args: list, timeout: int = 120, *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in args], timeout=timeout, check=check)


def systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run(['systemctl', *args], 60, check=check)


def has_systemd() -> bool:
    return shutil.which('systemctl') is not None and Path('/run/systemd/system').is_dir()


def render_unit(config: dict, *, cert: str | None = None, key: str | None = None,
                version: str = '') -> str:
    """生成 systemd 单元。

    - 密码等机密只在 config.json 里，单元不含 EnvironmentFile；
    - 低端口（<1024，含证书模式的 443）只授 CAP_NET_BIND_SERVICE，
      其余能力全部丢弃；
    - 服务只需要读文件，ProtectSystem=strict 不允许写 /usr /etc。
    """
    # 统一用 POSIX 路径：单元文件只会在 Linux 上生效，不能写出反斜杠。
    exec_args = [(CURRENT / LAUNCHER_NAME).as_posix(), 'run', '--config',
                 '/etc/heartapp-relay/config.json']
    if cert and key:
        exec_args += ['--cert', cert, '--key', key]
    capabilities = ''
    if int(config['port']) < 1024 or (cert and key):
        capabilities = 'CAP_NET_BIND_SERVICE'
    lines = [
        '[Unit]',
        f'Description=HeartApp relay (self-hosted heart-rate rooms){f" v{version}" if version else ""}',
        'After=network-online.target',
        'Wants=network-online.target',
        '',
        '[Service]',
        'Type=simple',
        f'User={config["user"]}',
        f'Group={config["user"]}',
        f'WorkingDirectory={CURRENT.as_posix()}',
        'ExecStart=' + ' '.join(exec_args),
        'Restart=on-failure',
        'RestartSec=3',
        '',
        '# 加固：只读文件系统、丢弃多余能力',
        'NoNewPrivileges=true',
        f'AmbientCapabilities={capabilities}',
        f'CapabilityBoundingSet={capabilities}',
        'PrivateTmp=true',
        'ProtectSystem=strict',
        'ProtectHome=true',
        'ProtectKernelTunables=true',
        'ProtectKernelModules=true',
        'ProtectControlGroups=true',
        'RestrictAddressFamilies=AF_INET AF_INET6',
        'LockPersonality=true',
        '',
        '[Install]',
        'WantedBy=multi-user.target',
        '',
    ]
    return '\n'.join(lines)


def write_unit(text: str) -> None:
    UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIT_PATH.write_text(text, encoding='utf-8')


def remove_unit() -> None:
    for path in (UNIT_PATH, RENEW_SERVICE, RENEW_TIMER):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def firewall_is_active() -> str | None:
    """返回 'firewalld' / 'ufw' / None。"""
    if shutil.which('firewall-cmd'):
        try:
            if run(['firewall-cmd', '--state'], 20, check=False).returncode == 0:
                return 'firewalld'
        except (OSError, subprocess.SubprocessError):
            pass
    if shutil.which('ufw'):
        try:
            result = run(['ufw', 'status'], 20, check=False)
            if 'Status: active' in (result.stdout or ''):
                return 'ufw'
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def firewall_open(port: str, *, permanent: bool = True) -> bool:
    backend = firewall_is_active()
    if backend == 'firewalld':
        rule = f'{port}/tcp'
        if permanent:
            run(['firewall-cmd', '--permanent', '--add-port', rule], 30, check=False)
        run(['firewall-cmd', '--add-port', rule], 30, check=False)
        run(['firewall-cmd', '--reload'], 30, check=False)
        return True
    if backend == 'ufw':
        run(['ufw', 'allow', f'{port}/tcp'], 30, check=False)
        return True
    return False


def firewall_close(port: str) -> bool:
    backend = firewall_is_active()
    if backend == 'firewalld':
        rule = f'{port}/tcp'
        run(['firewall-cmd', '--permanent', '--remove-port', rule], 30, check=False)
        run(['firewall-cmd', '--remove-port', rule], 30, check=False)
        run(['firewall-cmd', '--reload'], 30, check=False)
        return True
    if backend == 'ufw':
        run(['ufw', 'delete', 'allow', f'{port}/tcp'], 30, check=False)
        return True
    return False


def _probe_host(config: dict) -> str:
    host = config['host']
    if host in ('0.0.0.0', '::'):
        return '::1' if host == '::' else '127.0.0.1'
    return host


def health(config: dict, *, timeout: float = 5.0, retries: int = 1,
           delay: float = 0.6) -> tuple[bool, str]:
    """连一次 /healthz；返回 (是否正常, 说明)。

    证书模式下按系统 CA 校验，顺便验证证书链是否正确。
    """
    host = _probe_host(config)
    port = int(config['port'])
    last_error = ''
    for attempt in range(max(1, retries)):
        if attempt:
            time.sleep(delay)
        try:
            with socket.create_connection((host, port), timeout=timeout) as raw:
                connection = raw
                if cert_enabled(config):
                    context = ssl.create_default_context()
                    try:
                        import certifi  # 打包内自带，用于更完整的根证书集
                        context.load_verify_locations(cafile=certifi.where())
                    except (ImportError, OSError):
                        pass
                    connection = context.wrap_socket(raw, server_hostname=config['public_ip'])
                with connection:
                    connection.settimeout(timeout)
                    connection.sendall(
                        b'GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                    payload = b''
                    while True:
                        chunk = connection.recv(4096)
                        if not chunk:
                            break
                        payload += chunk
                status_line = payload.split(b'\r\n', 1)[0]
                if b' 200 ' not in status_line:
                    raise ValueError(f'健康检查返回 {status_line.decode("latin-1", "replace")}')
                return True, '证书校验通过' if cert_enabled(config) else '本机服务可用'
        except (OSError, ValueError, ssl.SSLError) as exc:
            last_error = str(exc)
    return False, last_error


def cert_enabled(config: dict) -> bool:
    return bool((config.get('public_ip') or '').strip())
