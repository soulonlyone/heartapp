"""自包含服务端统一入口，复用现有房间服务、配置向导和证书管理器。"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import socket
import ssl
import sys
import os
import subprocess
from pathlib import Path

from . import __version__


def manager_module():
    return helper_module('manage_linux')


def helper_module(name: str):
    if importlib.util.find_spec('heartapp._server'):
        return importlib.import_module('heartapp._server.' + name)
    filename = {'manage_linux': 'manage-linux.py', 'ip_tls': 'ip-tls.py'}[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent.parent / 'server' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def health() -> int:
    config = manager_module().load_settings()
    address = config['host']
    if address in ('0.0.0.0', '::'):
        address = '::1' if address == '::' else '127.0.0.1'
    with socket.create_connection((address, int(config['port'])), timeout=5) as connection:
        if config['tls_ip']:
            context = ssl.create_default_context()
            if importlib.util.find_spec('certifi'):
                import certifi
                context.load_verify_locations(cafile=certifi.where())
            connection = context.wrap_socket(connection, server_hostname=config['tls_ip'])
        with connection:
            connection.sendall(b'GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
            response = connection.recv(4096)
            if b' 200 ' not in response.split(b'\r\n', 1)[0]:
                raise ValueError('健康检查失败')
    print('HeartApp 服务可用，证书校验通过' if config['tls_ip'] else 'HeartApp 本机服务可用')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='HeartApp 独立联机服务端与管理工具')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('action', nargs='?', default='menu',
                        choices=('run', 'menu', 'configure', 'get', 'certificate', 'acme', 'health', 'firewall'))
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(arguments[:1])
    rest = arguments[1:]
    if args.action == 'acme':
        from certbot._internal.main import main as certbot_main
        return certbot_main(rest)
    if args.action == 'run':
        from .relay import main as relay_main
        original = sys.argv
        try:
            sys.argv = [sys.argv[0]] + rest
            relay_main()
            return 0
        finally:
            sys.argv = original
    if args.action == 'certificate':
        original = sys.argv
        try:
            sys.argv = [sys.argv[0]] + rest
            return helper_module('ip_tls').main()
        finally:
            sys.argv = original
    if args.action == 'health':
        try:
            return health()
        except (OSError, ValueError) as exc:
            print(f'服务检查失败：{exc}', file=sys.stderr)
            return 1
    if args.action == 'firewall':
        try:
            if os.geteuid() != 0:
                raise PermissionError('修改防火墙需要 sudo')
            helper_module('ip_tls').open_port(manager_module().load_settings()['port'])
            return 0
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f'防火墙配置失败：{exc}', file=sys.stderr)
            return 1
    return manager_module().main([args.action] + rest)


if __name__ == '__main__':
    sys.exit(main())
