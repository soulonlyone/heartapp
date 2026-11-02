"""联机协议 v1：只共享当前读数，不传设备地址和历史记录。"""
from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit

VERSION = 1
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def server_url(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        value = "wss://" + value
    parts = urlsplit(value)
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme, parts.scheme)
    if scheme not in ("ws", "wss") or not parts.hostname:
        raise ValueError("请输入有效的服务器地址，例如 https://hr.example.com 或 ws://127.0.0.1:8765")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("服务器地址不能包含账号、密码、查询参数或片段")
    try:
        parts.port
    except ValueError as exc:
        raise ValueError("服务器端口无效") from exc
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/") or "/", "", ""))


def room_code(value: str) -> str:
    code = value.strip().upper().replace("-", "").replace(" ", "")
    if len(code) != 8 or any(c not in CODE_ALPHABET for c in code):
        raise ValueError("房间码应为 8 位字母或数字")
    return code


def decode(message: str) -> dict:
    value = json.loads(message)
    if not isinstance(value, dict):
        raise ValueError("消息必须是对象")
    return value


def encode(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
