"""自包含服务端统一入口。

    heartapp-server install      安装 / 重新配置（支持 --dry-run）
    heartapp-server uninstall    卸载
    heartapp-server status       查看配置、服务与健康状态
    heartapp-server config       查看/修改配置（show / set）
    heartapp-server cert         证书（setup / renew / status）
    heartapp-server run          前台运行转发服务（systemd 用）
    heartapp-server menu         交互菜单
    heartapp-server health       本机健康检查

安装逻辑在 server_install，配置在 server_config，证书在 server_tls，
systemd/防火墙在 server_system —— 不再依赖任何 bash 安装脚本。
"""
from __future__ import annotations

import argparse
import getpass
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

from . import __version__
from . import server_config as cfg
from . import server_install as installer
from . import server_system as system


# ---------------------------------------------------------------------------
# 交互输入
# ---------------------------------------------------------------------------


def _echo_stream():
    """提示写到可写终端：terminal() 把 sys.stdin 换成了只读的 /dev/tty。"""
    return getattr(sys, 'stderr', None) or sys.stdout


def _masked_input(prompt: str) -> str:
    """读密码并回显 *（getpass 完全不回显，用户看不出输了几位）。"""
    stream = sys.stdin
    try:
        fd = stream.fileno()
        interactive = stream.isatty()
    except (AttributeError, OSError, ValueError):
        interactive = False
    if not interactive:
        return getpass.getpass(prompt)
    try:
        import termios
        import tty
        import codecs
    except ImportError:
        return getpass.getpass(prompt)

    echo = _echo_stream()
    echo.write(prompt)
    echo.flush()
    previous = termios.tcgetattr(fd)
    typed: list = []
    decoder = codecs.getincrementaldecoder('utf-8')(errors='ignore')
    try:
        tty.setcbreak(fd)
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                raise EOFError
            for char in decoder.decode(chunk):
                if char in ('\r', '\n'):
                    echo.write('\n')
                    echo.flush()
                    return ''.join(typed)
                if char in ('\x7f', '\x08'):
                    if typed:
                        typed.pop()
                        echo.write('\b \b')
                        echo.flush()
                    continue
                if char == '\x04' and not typed:
                    raise EOFError
                if char == '\x03':
                    raise KeyboardInterrupt
                typed.append(char)
                echo.write('*')
                echo.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


def _ask(prompt: str, default: str = '') -> str:
    suffix = f' [{default}]' if default else ''
    answer = input(f'{prompt}{suffix}：').strip()
    return answer or default


def _read_password(existing: str = '') -> str:
    prompt = (
        '服务器访问密码（客户端联机时填它；输入显示 *，直接回车保留原密码）：'
        if existing
        else '服务器访问密码（客户端联机时填它；输入显示 *）：'
    )
    while True:
        value = _masked_input(prompt)
        if not value and existing:
            return existing
        try:
            cfg.validate(dict(cfg.DEFAULTS, password=value))
        except ValueError as exc:
            print(exc)
            continue
        if value == _masked_input('再输入一次确认：'):
            return value
        print('两次密码不一致，请重新输入。')


def _find_package_root() -> Path:
    """自包含安装包里找 runtime/heartapp-server 所在的根目录。"""
    for parent in Path(__file__).resolve().parents:
        if (parent / 'runtime' / system.LAUNCHER_NAME).is_file():
            return parent
    raise installer.InstallError(
        '找不到安装包目录（runtime/heartapp-server）。请使用自包含安装包，'
        '而不是从源码树直接运行 install。'
    )


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def _apply_overrides(config: dict, args: argparse.Namespace) -> dict:
    result = dict(config)
    for key in ('host', 'port', 'max_clients', 'room_size', 'user', 'public_ip'):
        value = getattr(args, key, None)
        if value is not None:
            result[key] = str(value)
    password = getattr(args, 'password', None) or os.environ.get('HEARTAPP_INSTALL_PASSWORD')
    if password is not None:
        result['password'] = password
    return result


def _interactive(config: dict) -> dict:
    with cfg.terminal():
        print('\nHeartApp 服务端安装向导')
        print('这个密码用于客户端登录本服务器（不是房间密码），所有联机的人都要填同一个。')
        config['password'] = _read_password(config['password'])
        public_ip = _ask(
            '公网加密连接 IP（填了自动申请证书、监听 443；留空走本机或反向代理）',
            config['public_ip'],
        )
        config['public_ip'] = public_ip.strip()
        if config['public_ip'] and config['port'] == '8765':
            config['port'] = '443'
        config['port'] = _ask('服务端口', config['port'])
        if config['public_ip']:
            config['host'] = '::' if ':' in config['public_ip'] else '0.0.0.0'
            print('将申请公网 IP 证书并自动续期，请在云安全组放行 TCP 80 和服务端口。')
        cfg.validate(config)
        print(f"\n监听 {config['host']}，端口 {config['port']}。")
        print('安装或升级会重启服务，现有临时房间会清空。')
        answer = input('确认安装并启动服务？ [y/N]：').strip().lower()
        if answer not in ('y', 'yes'):
            raise SystemExit('已取消，原配置保留。')
    return config


def install_action(args: argparse.Namespace) -> int:
    try:
        config = _apply_overrides(cfg.load(), args)
        if not args.non_interactive:
            config = _interactive(config)
        else:
            cfg.validate(config)
        package_root = (Path(args.package_root).resolve()
                        if args.package_root else _find_package_root())
        version = args.version or __version__
        return installer.install(
            package_root, config,
            open_port=not args.no_open_port,
            dry_run=args.dry_run,
            version=version,
        )
    except (installer.InstallError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'安装失败：{exc}', file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# 其它子命令
# ---------------------------------------------------------------------------


def uninstall_action(args: argparse.Namespace) -> int:
    try:
        return installer.uninstall(
            keep_config=args.keep_config,
            open_port=not args.no_open_port,
            remove_user=args.remove_user,
            dry_run=args.dry_run,
        )
    except (installer.InstallError, OSError, subprocess.SubprocessError) as exc:
        print(f'卸载失败：{exc}', file=sys.stderr)
        return 1


def status_action(_args: argparse.Namespace) -> int:
    return installer.status()


def config_action(args: argparse.Namespace) -> int:
    if args.config_action == 'show':
        config = cfg.load()
        print(f'配置文件：{cfg.CONFIG_PATH}')
        for key, value in cfg.redacted(config).items():
            print(f'  {key:12} {value}')
        return 0
    if args.config_action == 'set':
        if not args.key or args.value is None:
            print('用法：heartapp-server config set <键> <值>', file=sys.stderr)
            return 2
        if args.key not in cfg.DEFAULTS:
            print(f'未知键：{args.key}（可用：{", ".join(cfg.DEFAULTS)}）', file=sys.stderr)
            return 2
        config = cfg.load()
        config[args.key] = args.value
        try:
            cfg.save(config, group=config['user'])
        except ValueError as exc:
            print(f'配置无效：{exc}', file=sys.stderr)
            return 1
        print(f'已更新 {args.key}；重启服务后生效：sudo systemctl restart {system.SERVICE}')
        return 0
    print('用法：heartapp-server config show|set <键> <值>', file=sys.stderr)
    return 2


def cert_action(args: argparse.Namespace) -> int:
    from . import server_tls

    config = cfg.load()
    try:
        if args.cert_action == 'setup':
            if not cfg.uses_tls(config):
                print('没有配置公网 IP，无需证书', file=sys.stderr)
                return 1
            server_tls.setup(config, user=config['user'], open_port=not args.no_open_port)
            print('证书已部署')
            return 0
        if args.cert_action == 'renew':
            return server_tls.renew()
        print(f'证书目录：{server_tls.CURRENT}')
        print('启用：' + ('是' if cfg.uses_tls(config) else '否'))
        if server_tls.CURRENT.is_symlink():
            print(f'当前代：{os.readlink(server_tls.CURRENT)}')
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'证书操作失败：{exc}', file=sys.stderr)
        return 1


def health_action(_args: argparse.Namespace) -> int:
    config = cfg.load()
    ok, message = system.health(config, retries=2)
    if ok:
        print(f'HeartApp 服务可用（{message}）')
        return 0
    print(f'服务检查失败：{message}', file=sys.stderr)
    return 1


def run_action(args: argparse.Namespace) -> int:
    from .relay import main as relay_main

    if bool(args.cert) != bool(args.key):
        print('--cert 与 --key 必须同时提供', file=sys.stderr)
        return 2

    config = cfg.load(Path(args.config)) if getattr(args, 'config', '') else cfg.load()
    argv = ['heartapp-relay',
            '--host', config['host'],
            '--port', config['port'],
            '--max-clients', config['max_clients'],
            '--room-size', config['room_size']]
    if config['password']:
        os.environ.setdefault('HEARTAPP_RELAY_PASSWORD', config['password'])
    if cfg.needs_plaintext(config):
        argv.append('--allow-plaintext')
    if args.cert and args.key:
        argv += ['--cert', args.cert, '--key', args.key]
    original = sys.argv
    try:
        sys.argv = argv
        relay_main()
    finally:
        sys.argv = original
    return 0


def menu_action(_args: argparse.Namespace) -> int:
    with cfg.terminal():
        while True:
            config = cfg.load()
            print(f"\nHeartApp 服务端 · {config['host']}:{config['port']}"
                  f"{'（证书）' if cfg.uses_tls(config) else '（明文）'}")
            print('1 状态   2 启动   3 停止   4 重启   5 查看日志   6 卸载   0 退出')
            choice = input('请选择：').strip()
            if choice == '0':
                return 0
            if choice == '1':
                installer.status()
            elif choice in ('2', '3', '4'):
                action = {'2': 'start', '3': 'stop', '4': 'restart'}[choice]
                system.systemctl(action, system.SERVICE, check=False)
                print(f'已执行 {action}')
            elif choice == '5':
                subprocess.run(['journalctl', '-u', system.SERVICE, '-n', '50', '--no-pager'],
                               check=False)
            elif choice == '6':
                answer = input('确认卸载？配置会保留 [y/N]：').strip().lower()
                if answer in ('y', 'yes'):
                    installer.uninstall(keep_config=True)
                    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='HeartApp 自包含联机服务端',
        epilog='卸载：sudo heartapp-server uninstall；状态：heartapp-server status',
    )
    parser.add_argument('--version', action='version', version=f'HeartApp server {__version__}')
    parser.add_argument('action', nargs='?', default='status',
                        choices=('install', 'uninstall', 'status', 'config', 'cert',
                                 'health', 'run', 'menu', 'acme'))
    parser.add_argument('--config', help='run 时读取的配置文件路径')
    parser.add_argument('--cert', help='run 时使用的证书')
    parser.add_argument('--key', help='run 时使用的私钥')
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == 'acme':
        # 证书模块内部用 `heartapp-server acme ...` 调内置 Certbot。
        try:
            from certbot._internal.main import main as certbot_main
        except ImportError:
            print('这个构建没有内置 Certbot（只有自包含安装包支持证书）', file=sys.stderr)
            return 1
        return certbot_main(arguments[1:])

    if arguments and arguments[0] == 'run':
        sub = argparse.ArgumentParser(prog='heartapp-server run')
        sub.add_argument('--config', default='')
        sub.add_argument('--cert')
        sub.add_argument('--key')
        return run_action(sub.parse_args(arguments[1:]))
    args = parser.parse_args(arguments[:1])
    rest = arguments[1:]

    if args.action == 'install':
        sub = argparse.ArgumentParser(prog='heartapp-server install')
        sub.add_argument('--password')
        sub.add_argument('--host')
        sub.add_argument('--port')
        sub.add_argument('--max-clients')
        sub.add_argument('--room-size')
        sub.add_argument('--user')
        sub.add_argument('--public-ip')
        sub.add_argument('--package-root')
        sub.add_argument('--version')
        sub.add_argument('--non-interactive', action='store_true')
        sub.add_argument('--no-open-port', action='store_true')
        sub.add_argument('--dry-run', action='store_true')
        return install_action(sub.parse_args(rest))

    if args.action == 'uninstall':
        sub = argparse.ArgumentParser(prog='heartapp-server uninstall')
        sub.add_argument('--keep-config', action='store_true')
        sub.add_argument('--remove-user', action='store_true')
        sub.add_argument('--no-open-port', action='store_true')
        sub.add_argument('--dry-run', action='store_true')
        return uninstall_action(sub.parse_args(rest))

    if args.action == 'status':
        return status_action(args)

    if args.action == 'config':
        sub = argparse.ArgumentParser(prog='heartapp-server config')
        sub.add_argument('config_action', nargs='?', default='show',
                         choices=('show', 'set'))
        sub.add_argument('key', nargs='?')
        sub.add_argument('value', nargs='?')
        return config_action(sub.parse_args(rest))

    if args.action == 'cert':
        sub = argparse.ArgumentParser(prog='heartapp-server cert')
        sub.add_argument('cert_action', nargs='?', default='status',
                         choices=('setup', 'renew', 'status'))
        sub.add_argument('--no-open-port', action='store_true')
        return cert_action(sub.parse_args(rest))

    if args.action == 'health':
        return health_action(args)
    if args.action == 'run':
        return run_action(args)
    if args.action == 'menu':
        return menu_action(args)
    return 2


if __name__ == '__main__':
    sys.exit(main())
