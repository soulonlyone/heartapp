"""服务端配置：单一 JSON 文件。

旧版把配置拆在两处：`/etc/heartapp-relay.env`（密码/端口）和 systemd 单元的
`ExecStart`（监听地址/容量）。两处不一致就会出问题，所以要收敛成一个文件。

新路径：`/etc/heartapp-relay/config.json`，权限 0640（root + 服务用户），
进程启动时 `--config` 指过来，不再依赖 EnvironmentFile。
"""
from __future__ import annotations

from contextlib import contextmanager
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
from typing import Any, Iterator

CONFIG_DIR = Path('/etc/heartapp-relay')
CONFIG_PATH = CONFIG_DIR / 'config.json'

#: 旧版配置位置；只在迁移时读取。
LEGACY_ENV_PATH = Path('/etc/heartapp-relay.env')
LEGACY_UNIT_PATH = Path('/etc/systemd/system/heartapp-relay.service')
LEGACY_ENV_HEADER = '# HeartApp managed configuration v1'

DEFAULTS: dict[str, str] = {
    'host': '127.0.0.1',
    'port': '8765',
    'max_clients': '20',
    'room_size': '4',
    'user': 'heartapp',
    'password': '',
    # 公网 IP：填了就启用自动证书并监听 443；留空表示走本机/反向代理。
    'public_ip': '',
}

#: 本机监听地址（不需要明文许可）。
LOOPBACK = ('127.0.0.1', 'localhost', '::1', '[::1]')

MIN_PASSWORD_LENGTH = 8
WEAK_PASSWORDS = {
    'password', 'passw0rd', '12345678', '123456789', '87654321',
    'qwertyui', 'abcdefgh', '11111111', '88888888', 'heartapp',
}

_VERSION_PATTERN = re.compile(r'^\d+(?:\.\d+){1,3}$')


def validate(config: dict[str, str]) -> None:
    """校验配置；不合法时抛 ValueError（中文信息直接给用户看）。"""
    public_ip = (config.get('public_ip') or '').strip()
    if public_ip:
        address = ipaddress.ip_address(public_ip)
        if not address.is_global:
            raise ValueError('自动证书需要公网 IP 地址')
        if config['port'] == '80':
            raise ValueError('TCP 80 用于证书验证，请选择其他服务端口')

    for key, low, high in (('port', 1, 65535), ('max_clients', 1, 10000), ('room_size', 1, 128)):
        value = config[key]
        if not value.isascii() or not value.isdigit() or not low <= int(value) <= high:
            raise ValueError(f'{key} 必须是 {low}～{high} 的整数')

    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*\$?', config['user']):
        raise ValueError('系统运行用户名无效')
    if not re.fullmatch(r'[A-Za-z0-9_.:%-]+', config['host']):
        raise ValueError('监听地址无效')

    password = config['password']
    if not password or not password.isprintable():
        raise ValueError('访问密码不能为空，也不能包含换行等控制字符')
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f'访问密码至少 {MIN_PASSWORD_LENGTH} 位')
    if len(set(password)) == 1:
        raise ValueError('访问密码不能是同一个字符重复')
    if password.lower() in WEAK_PASSWORDS:
        raise ValueError('这个密码太常见，换一个')


def uses_tls(config: dict[str, str]) -> bool:
    return bool((config.get('public_ip') or '').strip())


def needs_plaintext(config: dict[str, str]) -> bool:
    """非环回监听且没有证书时，服务端必须显式允许明文。"""
    return config['host'] not in LOOPBACK and not uses_tls(config)


def connect_address(config: dict[str, str]) -> str:
    """客户端应该填的地址（给安装完成的提示用）。"""
    if uses_tls(config):
        ip = config['public_ip']
        if ':' in ip:
            ip = f'[{ip}]'
        port = f':{config["port"]}' if config['port'] != '443' else ''
        return f'wss://{ip}{port}'
    host = config['host']
    if host in ('0.0.0.0', '::'):
        host = '服务器IP'
    return f'ws://{host}:{config["port"]}'


def _legacy_env_key(key: str) -> str:
    return 'HEARTAPP_RELAY_' + key.upper()


def _read_legacy() -> dict[str, str] | None:
    """读旧版 .env / systemd ExecStart，转成新结构。没有旧配置时返回 None。"""
    if not LEGACY_ENV_PATH.exists() and not LEGACY_UNIT_PATH.exists():
        return None
    found: dict[str, str] = {}
    if LEGACY_UNIT_PATH.exists():
        # 旧版可能把 host/port 等直接写在 ExecStart 里。
        text = LEGACY_UNIT_PATH.read_text(encoding='utf-8', errors='replace')
        match = re.search(r'^ExecStart=(.+)$', text, re.M)
        if match:
            args = shlex.split(match.group(1))
            for key in ('host', 'port', 'max_clients', 'room_size'):
                flag = '--' + key.replace('_', '-')
                if flag in args and args.index(flag) + 1 < len(args):
                    value = args[args.index(flag) + 1]
                    if not value.startswith('$'):
                        found[key] = value
        match = re.search(r'^User=(.+)$', text, re.M)
        if match:
            found['user'] = match.group(1).strip()
    if LEGACY_ENV_PATH.exists():
        text = LEGACY_ENV_PATH.read_text(encoding='utf-8', errors='replace')
        managed = text.startswith(LEGACY_ENV_HEADER + '\n')
        # 旧键 tls_ip 对应新键 public_ip（方向别写反）
        aliases = {'public_ip': 'tls_ip'}
        for line in text.splitlines():
            name, separator, value = line.partition('=')
            if not separator:
                continue
            for key in DEFAULTS:
                target = key
                if name == _legacy_env_key(key) or name == _legacy_env_key(aliases.get(key, key)):
                    if managed:
                        parts = shlex.split(value)
                        if len(parts) != 1:
                            raise ValueError('旧配置格式无效，请先备份再重试')
                        found[target] = parts[0]
                    else:
                        found[target] = value
    if not found:
        return None
    config = dict(DEFAULTS)
    config.update({k: v for k, v in found.items() if k in DEFAULTS})
    # 旧版用 tls_ip 表示公网 IP。
    legacy_tls = found.get('tls_ip')
    if legacy_tls and not config['public_ip']:
        config['public_ip'] = legacy_tls
    return config


def load(path: Path = CONFIG_PATH) -> dict[str, str]:
    """读配置；没有新文件时尝试从旧版迁移（只读，不落盘）。"""
    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('配置文件格式无效')
        config = dict(DEFAULTS)
        config.update({k: str(v) for k, v in data.items() if k in DEFAULTS})
        return config
    legacy = _read_legacy()
    return legacy if legacy is not None else dict(DEFAULTS)


def save(config: dict[str, str], path: Path = CONFIG_PATH, *, group: str | None = None) -> None:
    """原子写入；权限 0640，属主 root、属组为服务用户（进程要读密码）。"""
    validate(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=str(path.parent),
            prefix='.heartapp-config-', delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        if group:
            try:
                import grp
                os.chown(temporary, 0, grp.getgrnam(group).gr_gid)
            except (KeyError, OSError):
                pass
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def redacted(config: dict[str, str]) -> dict[str, str]:
    """给 status/config show 用：不打印密码明文。"""
    shown = dict(config)
    if shown.get('password'):
        shown['password'] = '********'
    return shown


@contextmanager
def terminal() -> Iterator[None]:
    """curl | bash 时标准输入是脚本，提示必须从控制终端读取。"""
    import sys

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
