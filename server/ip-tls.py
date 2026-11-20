#!/usr/bin/env python3
"""申请、部署和续期 Linux 服务端的公网 IP 证书。"""
from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import shlex

APP = Path('/opt/heartapp-relay')
ACME = Path('/etc/heartapp-acme')
CERTBOT = Path('/opt/heartapp-acme/bin/certbot')
TLS = Path('/etc/heartapp-tls')
UNITS = Path('/etc/systemd/system')


def run(args: list[str], timeout: int = 300) -> None:
    subprocess.run(args, check=True, timeout=timeout)


def settings() -> dict:
    if not bundled_launcher() and (APP / 'manage-linux.py').is_file():
        spec = importlib.util.spec_from_file_location('manager', APP / 'manage-linux.py')
        manager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(manager)
    else:
        from heartapp.server_cli import manager_module
        manager = manager_module()
    config = manager.load_settings()
    address = ipaddress.ip_address(config['tls_ip'])
    if not address.is_global:
        raise ValueError('自动证书需要公网 IP')
    return config


def certbot_args() -> list[str]:
    launcher = bundled_launcher()
    command = [str(launcher), 'acme'] if launcher else [str(CERTBOT)]
    return command + ['--config-dir', str(ACME), '--work-dir',
            '/var/lib/heartapp-acme', '--logs-dir', '/var/log/heartapp-acme']


def bundled_launcher():
    candidate = Path(sys.executable).resolve().parents[2] / 'heartapp-server'
    if candidate.is_file():
        stable = APP / 'current/heartapp-server'
        return stable if stable.is_file() else candidate
    return None


def certificate_name(config: dict) -> str:
    return 'heartapp-' + config['tls_ip'].replace(':', '-')


def deploy(config: dict, *, restart: bool) -> None:
    """先验证证书，再原子切换证书和私钥所在的同一个目录。"""
    source = ACME / 'live' / certificate_name(config)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(source / 'fullchain.pem', source / 'privkey.pem')
    # 检查 IP SAN 和有效期，避免把错误身份的证书装到服务上。
    decoded = ssl._ssl._test_decode_cert(str(source / 'fullchain.pem'))
    addresses = [ipaddress.ip_address(value) for kind, value in decoded.get('subjectAltName', ())
                 if kind == 'IP Address']
    if ipaddress.ip_address(config['tls_ip']) not in addresses:
        raise ssl.CertificateError('证书的 IP 地址与服务器配置不一致')
    import time
    if ssl.cert_time_to_seconds(decoded['notAfter']) <= time.time() + 3600:
        raise ValueError('证书剩余有效期不足一小时')
    if 'notBefore' in decoded and ssl.cert_time_to_seconds(decoded['notBefore']) > time.time():
        raise ValueError('证书尚未生效，请检查系统时间')
    import grp
    group = grp.getgrnam(config['user']).gr_gid
    TLS.mkdir(mode=0o750, exist_ok=True)
    os.chown(TLS, 0, group)
    os.chmod(TLS, 0o750)
    current = TLS / 'current'
    if current.is_symlink() and current.stat().st_gid == group and all(
            (current / name).stat().st_gid == group
            and (current / name).read_bytes() == (source / name).read_bytes()
            for name in ('fullchain.pem', 'privkey.pem')):
        return
    previous = os.readlink(current) if current.is_symlink() else None
    with tempfile.TemporaryDirectory(prefix='.new-', dir=TLS) as temp:
        staging = Path(temp)
        for filename in ('fullchain.pem', 'privkey.pem'):
            target = staging / filename
            target.write_bytes((source / filename).read_bytes())
            os.chown(target, 0, group)
            os.chmod(target, 0o640)
        os.chown(staging, 0, group)
        os.chmod(staging, 0o750)
        generation = TLS / ('cert-' + staging.name.removeprefix('.new-'))
        staging.rename(generation)
    link = TLS / '.current-new'
    link.unlink(missing_ok=True)
    link.symlink_to(generation.name)
    os.replace(link, current)
    try:
        if restart:
            run(['systemctl', 'restart', 'heartapp-relay'], 30)
            run(['systemctl', 'is-active', '--quiet', 'heartapp-relay'], 10)
    except (OSError, subprocess.SubprocessError):
        if previous:
            link.symlink_to(previous)
            os.replace(link, current)
            run(['systemctl', 'restart', 'heartapp-relay'], 30)
        raise
    # 保留上一代，便于排障，避免每六天累积一套私钥。
    for old in TLS.glob('cert-*'):
        if old.name not in (generation.name, previous):
            shutil.rmtree(old)


def open_port(port: str) -> None:
    if shutil.which('firewall-cmd') and subprocess.run(
            ['firewall-cmd', '--state'], capture_output=True).returncode == 0:
        run(['firewall-cmd', '--permanent', '--add-port=' + port + '/tcp'], 30)
        run(['firewall-cmd', '--add-port=' + port + '/tcp'], 30)
    elif shutil.which('ufw') and 'Status: active' in subprocess.run(
            ['ufw', 'status'], capture_output=True, text=True).stdout:
        run(['ufw', 'allow', port + '/tcp'], 30)


def setup(config: dict, *, firewall: bool) -> None:
    if sys.version_info < (3, 10):
        raise ValueError('自动 IP 证书需要 Python 3.10+，请升级服务器 Python 后重新安装')
    if config['port'] == '80':
        raise ValueError('TCP 80 用于证书验证，请选择其他服务端口')
    # 独立验证服务器使用 80，不停止占用端口的现有网站。
    family = socket.AF_INET6 if ':' in config['tls_ip'] else socket.AF_INET
    with socket.socket(family) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('::' if family == socket.AF_INET6 else '0.0.0.0', 80))
        except OSError as exc:
            raise ValueError('TCP 80 无法用于证书验证，请检查现有网站或端口占用') from exc
    if firewall:
        open_port('80')
        open_port(config['port'])
    print('请确认云安全组已放行 TCP 80 和 ' + config['port'], flush=True)
    launcher = bundled_launcher()
    if not launcher:
        if not CERTBOT.exists():
            run([sys.executable, '-m', 'venv', str(CERTBOT.parent.parent)])
        install = [str(CERTBOT.parent / 'python'), '-m', 'pip', 'install',
                   '--disable-pip-version-check', '--no-input', '--timeout', '15',
                   '--retries', '1', 'certbot>=5.4,<6']
        try:
            run(install)
        except subprocess.CalledProcessError:
            run(install + ['-i', 'https://pypi.tuna.tsinghua.edu.cn/simple'])
    # 此安装器使用独立 ACME 目录，只操作本程序的证书。
    run(certbot_args() + ['certonly', '--standalone', '--preferred-challenges', 'http',
                         '--required-profile', 'shortlived', '--ip-address', config['tls_ip'],
                         '--cert-name', certificate_name(config), '--non-interactive',
                         '--server', 'https://acme-v02.api.letsencrypt.org/directory',
                         '--agree-tos', '--register-unsafely-without-email',
                         '--keep-until-expiring'], 240)
    deploy(config, restart=False)
    renewal = [str(launcher), 'certificate', 'renew'] if launcher else [
        str(APP / 'venv/bin/python'), str(APP / 'ip-tls.py'), 'renew']
    (UNITS / 'heartapp-ip-renew.service').write_text(
        '[Unit]\nDescription=HeartApp IP certificate renewal\n'
        'Wants=network-online.target\nAfter=network-online.target\n'
        '[Service]\nType=oneshot\nTimeoutStartSec=300\n'
        f'ExecStart={shlex.join(renewal)}\n', encoding='utf-8')
    (UNITS / 'heartapp-ip-renew.timer').write_text(
        '[Unit]\nDescription=Check HeartApp IP certificate every 12 hours\n'
        '[Timer]\nOnCalendar=*-*-* 00,12:00:00\nRandomizedDelaySec=1800\n'
        'Persistent=true\n[Install]\nWantedBy=timers.target\n', encoding='utf-8')
    run(['systemctl', 'daemon-reload'], 30)
    run(['systemctl', 'enable', '--now', 'heartapp-ip-renew.timer'], 30)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('setup', 'renew', 'deploy'))
    parser.add_argument('--no-open-port', action='store_true')
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise ValueError('请使用 sudo 运行')
        import fcntl
        # 安装和定时续期共用一把锁，避免同时切换证书。
        with open('/run/heartapp-ip-tls.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            config = settings()
            if args.action == 'setup':
                setup(config, firewall=not args.no_open_port)
            elif args.action == 'deploy':
                deploy(config, restart=True)
            else:
                run(certbot_args() + ['renew', '--cert-name', certificate_name(config),
                                     '--non-interactive', '--no-random-sleep-on-renew'], 240)
                deploy(config, restart=True)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'IP 证书操作失败：{exc}。检查 TCP 80 入站、证书机构网络连接和续期日志。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
