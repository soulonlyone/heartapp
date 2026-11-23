"""公网 IP 证书：用内置 Certbot 申请 Let's Encrypt 的短有效期 IP 证书。

HTTP-01 验证必须独占 TCP 80；端口被占用（例如已有 nginx）时直接给出明确错误，
而不是让 certbot 打一堆日志。证书按「代」存放，`current` 指向当前一代，
续期失败保留旧证书。
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import time

from . import server_config as cfg
from . import server_system as system

CURRENT = system.TLS_DIR / 'current'
CERT_NAME = 'fullchain.pem'
KEY_NAME = 'privkey.pem'


def _launcher() -> Path:
    """优先用打包内的 heartapp-server 入口（带内置 certbot）。"""
    candidate = system.CURRENT / system.LAUNCHER_NAME
    if candidate.is_file():
        return candidate
    return Path(sys.executable)


def _certbot_command() -> list[str]:
    launcher = _launcher()
    if launcher.name == system.LAUNCHER_NAME:
        return [str(launcher), 'acme']
    return [sys.executable, '-m', 'certbot']


def certificate_name(config: dict) -> str:
    return 'heartapp-' + config['public_ip'].replace(':', '-')


def _acme_args() -> list[str]:
    return ['--config-dir', str(system.ACME_DIR), '--work-dir', str(system.ACME_WORK),
            '--logs-dir', str(system.ACME_LOGS)]


def _check_port_80(config: dict) -> None:
    if config['port'] == '80':
        raise ValueError('TCP 80 用于证书验证，请选择其他服务端口')
    family = socket.AF_INET6 if ':' in config['public_ip'] else socket.AF_INET
    with socket.socket(family) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('::' if family == socket.AF_INET6 else '0.0.0.0', 80))
        except OSError as exc:
            raise ValueError(
                'TCP 80 已占用，无法完成证书验证。'
                '如果这台机器已经跑着 nginx 等网站，请改用反向代理模式（安装时不填公网 IP）'
            ) from exc


def setup(config: dict, *, user: str, open_port: bool = True) -> tuple[str, str]:
    """申请并部署证书；返回 (证书路径, 私钥路径)。"""
    if sys.version_info < (3, 10):
        raise ValueError('自动 IP 证书需要 Python 3.10+（请使用自包含安装包）')
    _check_port_80(config)

    if open_port:
        system.firewall_open('80')
        system.firewall_open(config['port'])
    print(f'  请确认云安全组已放行 TCP 80 和 {config["port"]}', flush=True)

    system.ACME_DIR.mkdir(parents=True, exist_ok=True)
    system.ACME_WORK.mkdir(parents=True, exist_ok=True)
    system.ACME_LOGS.mkdir(parents=True, exist_ok=True)

    print('  向 Let\'s Encrypt 申请 IP 证书（需要几十秒）…', flush=True)
    command = _certbot_command() + _acme_args() + [
        'certonly', '--standalone', '--preferred-challenges', 'http',
        '--required-profile', 'shortlived', '--ip-address', config['public_ip'],
        '--cert-name', certificate_name(config), '--non-interactive',
        '--server', 'https://acme-v02.api.letsencrypt.org/directory',
        '--agree-tos', '--register-unsafely-without-email',
        '--keep-until-expiring',
    ]
    try:
        subprocess.run(command, check=True, timeout=300)
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            '证书申请失败：请确认云安全组放行 TCP 80、80 端口未被占用、'
            '服务器能访问 acme-v02.api.letsencrypt.org'
        ) from exc

    cert, key = deploy(config, user=user, restart=False)
    _install_renewal(user=user)
    return cert, key


def _source_dir(config: dict) -> Path:
    return system.ACME_DIR / 'live' / certificate_name(config)


def deploy(config: dict, *, user: str, restart: bool) -> tuple[str, str]:
    """验证并原子切换证书；返回新证书与私钥路径。"""
    source = _source_dir(config)
    chain = source / CERT_NAME
    private = source / KEY_NAME
    for path in (chain, private):
        if not path.exists():
            raise ValueError(f'证书文件缺失：{path}')

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(chain, private)
    except ssl.SSLError as exc:
        raise ValueError(f'证书不可用：{exc}') from exc

    decoded = ssl._ssl._test_decode_cert(str(chain))
    addresses = [ipaddress.ip_address(value) for kind, value in decoded.get('subjectAltName', ())
                 if kind == 'IP Address']
    if ipaddress.ip_address(config['public_ip']) not in addresses:
        raise ssl.CertificateError('证书的 IP 地址与配置不一致')
    if ssl.cert_time_to_seconds(decoded['notAfter']) <= time.time() + 3600:
        raise ValueError('证书剩余有效期不足一小时')
    if 'notBefore' in decoded and ssl.cert_time_to_seconds(decoded['notBefore']) > time.time():
        raise ValueError('证书尚未生效，请检查系统时间')

    generation = system.TLS_DIR / f'cert-{int(time.time())}'
    generation.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(chain, generation / CERT_NAME)
    shutil.copyfile(private, generation / KEY_NAME)
    os.chmod(generation / CERT_NAME, 0o644)
    os.chmod(generation / KEY_NAME, 0o640)
    try:
        import grp
        gid = grp.getgrnam(user).gr_gid
        os.chown(generation, 0, gid)
        os.chown(generation / CERT_NAME, 0, gid)
        os.chown(generation / KEY_NAME, 0, gid)
        os.chown(system.TLS_DIR, 0, gid)
        os.chmod(system.TLS_DIR, 0o750)
    except (KeyError, OSError):
        pass

    previous = os.readlink(CURRENT) if CURRENT.is_symlink() else None
    temporary_link = system.TLS_DIR / '.current-new'
    temporary_link.unlink(missing_ok=True)
    temporary_link.symlink_to(generation)
    os.replace(temporary_link, CURRENT)

    # 保留上一代便于排障，避免每六天累积一堆私钥。
    for old in system.TLS_DIR.glob('cert-*'):
        if old.name not in (generation.name, Path(previous).name if previous else None):
            shutil.rmtree(old, ignore_errors=True)

    if restart:
        system.systemctl('restart', system.SERVICE, check=False)
    cert = str(CURRENT / CERT_NAME)
    key = str(CURRENT / KEY_NAME)
    return cert, key


def _install_renewal(*, user: str) -> None:
    launcher = _launcher()
    command = [str(launcher), 'cert', 'renew'] if launcher.name == system.LAUNCHER_NAME else [
        sys.executable, '-m', 'heartapp.server_cli', 'cert', 'renew']
    system.RENEW_SERVICE.write_text(
        '[Unit]\nDescription=HeartApp IP certificate renewal\n'
        'Wants=network-online.target\nAfter=network-online.target\n'
        '[Service]\nType=oneshot\nTimeoutStartSec=300\n'
        f'ExecStart={shlex.join(command)}\n', encoding='utf-8')
    system.RENEW_TIMER.write_text(
        '[Unit]\nDescription=Check HeartApp IP certificate every 12 hours\n'
        '[Timer]\nOnCalendar=*-*-* 00,12:00:00\nRandomizedDelaySec=1800\n'
        'Persistent=true\n[Install]\nWantedBy=timers.target\n', encoding='utf-8')
    system.systemctl('daemon-reload', check=False)
    system.systemctl('enable', '--now', 'heartapp-ip-renew.timer', check=False)


def renew() -> int:
    """续期检查；证书有更新时重启服务让新证书生效。"""
    config = cfg.load()
    if not cfg.uses_tls(config):
        print('未启用证书模式，无需续期')
        return 0
    command = _certbot_command() + _acme_args() + [
        'renew', '--cert-name', certificate_name(config), '--quiet',
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    output = ((result.stdout or '') + (result.stderr or '')).strip()
    if result.returncode != 0:
        print(f'续期失败（保留当前证书）：{output}', file=sys.stderr)
        return 1
    if 'not due for renewal' in output.lower() or not output:
        print('证书仍在有效期内，无需更新')
        return 0
    try:
        _cert, _key = deploy(config, user=config['user'], restart=True)
    except (ValueError, ssl.CertificateError) as exc:
        print(f'新证书部署失败（保留当前证书）：{exc}', file=sys.stderr)
        return 1
    print('证书已更新并重启服务')
    return 0
