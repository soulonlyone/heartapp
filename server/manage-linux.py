#!/usr/bin/env python3
"""Linux/systemd 安装向导和终端管理菜单，仅依赖 Python 标准库。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import getpass
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

ENV_FILE = Path('/etc/heartapp-relay.env')
UNIT_FILE = Path('/etc/systemd/system/heartapp-relay.service')
SERVICE = 'heartapp-relay'
HEADER = '# HeartApp managed configuration v1'
DEFAULTS = dict(host='0.0.0.0', port='8765', max_clients='20', room_size='4', user='heartapp', password='')


def env_key(key):
    return 'HEARTAPP_RELAY_' + key.upper()


def load_settings():
    settings = dict(DEFAULTS)
    # 接续旧版将端口等参数直接写在 ExecStart 中的安装。
    if UNIT_FILE.exists():
        text = UNIT_FILE.read_text(encoding='utf-8')
        match = re.search(r'^ExecStart=(.+)$', text, re.M)
        if match:
            args = shlex.split(match[1])
            for key in ('host', 'port', 'max_clients', 'room_size'):
                flag = '--' + key.replace('_', '-')
                if flag in args and args.index(flag) + 1 < len(args):
                    value = args[args.index(flag) + 1]
                    if not value.startswith('$'):
                        settings[key] = value
        match = re.search(r'^User=(.+)$', text, re.M)
        if match:
            settings['user'] = match[1].strip()
    if ENV_FILE.exists():
        text = ENV_FILE.read_text(encoding='utf-8')
        managed = text.startswith(HEADER + '\n')
        for line in text.splitlines():
            name, separator, value = line.partition('=')
            if separator:
                for key in settings:
                    if name == env_key(key):
                        # 不执行 shell，不展开 $、反引号或命令替换。
                        if managed:
                            parts = shlex.split(value)
                            if len(parts) != 1:
                                raise ValueError('配置格式无效，请恢复配置备份')
                            settings[key] = parts[0]
                        else:
                            settings[key] = value
    return settings


def validate(settings):
    for key, low, high in (('port', 1, 65535), ('max_clients', 1, 10000), ('room_size', 1, 128)):
        if not settings[key].isascii() or not settings[key].isdigit() or not low <= int(settings[key]) <= high:
            raise ValueError(f'{key} 必须是 {low}～{high} 的整数')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*\$?', settings['user']):
        raise ValueError('系统运行用户名无效')
    if not re.fullmatch(r'[A-Za-z0-9_.:%-]+', settings['host']):
        raise ValueError('监听地址无效')
    if not settings['password'] or not settings['password'].isprintable():
        raise ValueError('访问密码不能为空，也不能包含换行等控制字符')


def save_settings(settings):
    validate(settings)
    lines = [HEADER]
    for key, value in settings.items():
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(f'{env_key(key)}="{escaped}"')
    # 同目录原子替换，避免中断时写出半份配置。
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=ENV_FILE.parent,
                                         prefix='.heartapp-', delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write('\n'.join(lines) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, ENV_FILE)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def terminal():
    """curl | bash 的标准输入是脚本，提示必须从控制终端读取。"""
    original = sys.stdin
    if original.isatty():
        yield
        return
    try:
        handle = open('/dev/tty', 'r', encoding='utf-8', buffering=1)
    except OSError as exc:
        raise ValueError('需要交互终端。请通过 SSH 登录后安装，自动部署请用 --non-interactive。') from exc
    try:
        sys.stdin = handle
        yield
    finally:
        sys.stdin = original
        handle.close()


def read_password(existing=''):
    # 这是「客户端登录服务器」用的访问密码，不是房间密码；安装时必须设置。
    prompt = (
        '服务器访问密码（客户端在「联机房间」登录时填它；输入时不显示，直接回车保留原密码）：'
        if existing
        else '服务器访问密码（客户端在「联机房间」登录时填它；输入时不显示）：'
    )
    while True:
        value = getpass.getpass(prompt)
        if not value and existing:
            return existing
        candidate = dict(DEFAULTS, password=value)
        try:
            validate(candidate)
        except ValueError as exc:
            print(exc)
            continue
        if value == getpass.getpass('再输入一次确认：'):
            return value
        print('两次密码不一致，请重新输入。')


def read_number(settings, key, label):
    while True:
        value = input(f'{label} [{settings[key]}]：').strip() or settings[key]
        try:
            validate(dict(DEFAULTS, password='check', **{key: value}))
        except ValueError as exc:
            print(exc)
            continue
        return value


def confirm(message):
    return input(message + ' [y/N]：').strip().lower() in ('y', 'yes')


def configure(args):
    settings = load_settings()
    for key in DEFAULTS:
        value = getattr(args, key, None)
        if value is not None:
            settings[key] = value
    password = os.environ.get('HEARTAPP_INSTALL_PASSWORD')
    if password is not None:
        settings['password'] = password
    if not args.non_interactive:
        with terminal():
            print('\nHeartApp 服务端安装向导')
            print('这个密码用于客户端登录本服务器（不是房间密码），所有联机的人都要填同一个。')
            settings['password'] = read_password(settings['password'])
            settings['port'] = read_number(settings, 'port', '服务端口')
            validate(settings)
            print(f"\n监听地址 {settings['host']}，端口 {settings['port']}。")
            print('访问密码已设置——客户端登录服务器时填它。')
            print('安装或升级会重启服务，现有临时房间会清空。')
            if not confirm('确认安装并启动服务？'):
                print('已取消，原配置保留。')
                return 1
    save_settings(settings)
    return 0


def systemctl(action):
    subprocess.run(['systemctl', action, SERVICE], check=True)


def menu():
    if not ENV_FILE.exists():
        raise ValueError('尚未安装，请先运行安装向导。')
    with terminal():
        while True:
            settings = load_settings()
            print(f"\nHeartApp 服务端 · 端口 {settings['port']}")
            print('1 查看状态   2 启动   3 停止   4 重启\n5 修改访问密码   6 修改端口   7 查看最近日志   8 修改每房人数   0 退出')
            choice = input('请选择：').strip()
            try:
                if choice == '0':
                    return 0
                if choice == '1':
                    subprocess.run(['systemctl', '--no-pager', 'status', SERVICE], check=False)
                elif choice == '2':
                    systemctl('start')
                elif choice in ('3', '4'):
                    if confirm('此操作会清空临时房间，继续？'):
                        systemctl('stop' if choice == '3' else 'restart')
                elif choice in ('5', '6', '8'):
                    if choice == '5':
                        settings['password'] = read_password()
                    elif choice == '6':
                        settings['port'] = read_number(settings, 'port', '服务端口')
                    else:
                        settings['room_size'] = read_number(settings, 'room_size', '每房人数上限')
                    if confirm('保存配置并重启服务？现有临时房间会清空'):
                        save_settings(settings)
                        systemctl('restart')
                        print('配置已保存，服务已重启。')
                        if choice == '6':
                            print('请同步调整防火墙和客户端地址。')
                        elif choice == '8':
                            print(f"每房人数上限为 {min(int(settings['room_size']), int(settings['max_clients']))} 人。")
                elif choice == '7':
                    subprocess.run(['journalctl', '-u', SERVICE, '-n', '50', '--no-pager'], check=True)
            except (OSError, ValueError, subprocess.CalledProcessError) as exc:
                print(f'操作未完成：{exc}。请查看状态或日志后重试。')


def main(argv=None):
    parser = argparse.ArgumentParser(description='HeartApp Linux 服务端安装向导与管理菜单')
    parser.add_argument('action', nargs='?', choices=('menu', 'configure', 'get'), default='menu')
    parser.add_argument('--key', choices=('host', 'port', 'max_clients', 'room_size', 'user'))
    parser.add_argument('--non-interactive', action='store_true', help='自动部署，使用已有配置或显式参数')
    for key in ('host', 'port', 'max_clients', 'room_size', 'user'):
        parser.add_argument('--' + key.replace('_', '-'))
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise ValueError('请使用 sudo heartapp-server。')
        if args.action == 'configure':
            return configure(args)
        if args.action == 'get':
            if not args.key:
                parser.error('get 需要 --key')
            print(load_settings()[args.key])
            return 0
        return menu()
    except (OSError, ValueError, EOFError, subprocess.CalledProcessError) as exc:
        print(f'操作未完成：{exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\n已取消。', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
