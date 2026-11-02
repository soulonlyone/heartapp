"""可自部署的心率房间转发服务：python -m heartapp.relay。"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass, field
import hmac
import logging
import os
import secrets
import signal
import ssl
import time
from typing import Dict, Optional

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.http11 import Request, Response
from websockets.exceptions import ConnectionClosed

from .room_protocol import CODE_ALPHABET, VERSION, decode, encode, room_code


@dataclass
class Member:
    socket: ServerConnection
    name: str
    id: str = field(default_factory=lambda: secrets.token_hex(8))
    bpm: Optional[int] = None
    simulated: bool = False
    sharing: bool = False
    updated: float = 0.0
    sequence: int = 0


class Relay:
    def __init__(self, *, max_clients: int = 100, room_size: int = 4, password: str = "") -> None:
        self.max_clients = max_clients
        self.room_size = room_size
        self.password = password
        self.rooms: Dict[str, Dict[str, Member]] = {}
        self.connections = set()
        self.attempts: Dict[str, deque] = {}

    def snapshot(self, code: str) -> dict:
        now = time.monotonic()
        members = []
        for member in self.rooms.get(code, {}).values():
            fresh = now - member.updated <= 5
            members.append(dict(id=member.id, name=member.name,
                                bpm=member.bpm if fresh and member.sharing else None,
                                sharing=member.sharing, simulated=member.simulated,
                                sample_seq=member.sequence, age_ms=round((now - member.updated) * 1000) if fresh else None))
        return dict(type="snapshot", room=code, members=members)

    async def send(self, socket: ServerConnection, payload: dict) -> None:
        try:
            await asyncio.wait_for(socket.send(encode(payload)), timeout=2)
        except (ConnectionClosed, asyncio.TimeoutError):
            await socket.close(code=1013, reason="接收超时")

    async def broadcast(self, code: str) -> None:
        payload = self.snapshot(code)
        await asyncio.gather(*(self.send(m.socket, payload) for m in list(self.rooms.get(code, {}).values())))

    async def ticker(self) -> None:
        while True:
            await asyncio.sleep(1)
            await asyncio.gather(*(self.broadcast(code) for code in list(self.rooms)))
            now = time.monotonic()
            for ip, attempts in list(self.attempts.items()):
                if not attempts or now - attempts[-1] > 60:
                    self.attempts.pop(ip, None)

    async def reject(self, socket: ServerConnection, message: str) -> None:
        await self.send(socket, dict(type="error", message=message))
        await socket.close(code=1008, reason="请求被拒绝")

    async def handler(self, socket: ServerConnection) -> None:
        # 原始来源地址；反向代理后会共用连接限额，不信任任意 X-Forwarded-For。
        ip = str(socket.remote_address[0]) if socket.remote_address else "unknown"
        now = time.monotonic()
        attempts = self.attempts.setdefault(ip, deque())
        while attempts and now - attempts[0] > 60:
            attempts.popleft()
        if len(self.connections) >= self.max_clients or len(attempts) >= 30:
            await self.reject(socket, "服务器繁忙或连接过于频繁，请稍后重试")
            return
        attempts.append(now)
        self.connections.add(socket)
        code, member = "", None
        try:
            hello = decode(await asyncio.wait_for(socket.recv(), 10))
            if hello.get("version") != VERSION:
                await self.reject(socket, "联机版本不兼容，请更新客户端或服务端")
                return
            if self.password and not hmac.compare_digest(str(hello.get("password", "")).encode(), self.password.encode()):
                await self.reject(socket, "服务器访问密码错误")
                return
            name = hello.get("name", "")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 24 or any(ord(c) < 32 for c in name):
                await self.reject(socket, "昵称需要 1～24 个可见字符")
                return
            if hello.get("type") == "create":
                code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
                while code in self.rooms:
                    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
                self.rooms[code] = {}
            elif hello.get("type") == "join":
                code = room_code(hello.get("room", ""))
                if code not in self.rooms:
                    await self.reject(socket, "房间不存在或已结束，请确认房间码及服务器地址")
                    return
            else:
                await self.reject(socket, "无效的房间请求")
                return
            if len(self.rooms[code]) >= self.room_size:
                await self.reject(socket, "房间已满")
                return
            member = Member(socket=socket, name=name.strip())
            self.rooms[code][member.id] = member
            await self.send(socket, dict(type="welcome", version=VERSION, room=code, self_id=member.id,
                                         capacity=self.room_size))
            await self.broadcast(code)
            rate = deque()
            async for raw in socket:
                now = time.monotonic()
                while rate and now - rate[0] > 2:
                    rate.popleft()
                rate.append(now)
                if len(rate) > 6:
                    await self.reject(socket, "发送过于频繁")
                    break
                packet = decode(raw)
                bpm = packet.get("bpm")
                if packet.get("type") != "reading" or (bpm is not None and (type(bpm) is not int or not 1 <= bpm <= 300)):
                    await self.reject(socket, "无效的心率消息")
                    break
                if type(packet.get("sharing")) is not bool or type(packet.get("simulated")) is not bool:
                    await self.reject(socket, "无效的共享状态")
                    break
                member.sharing = packet["sharing"]
                member.simulated = packet["simulated"]
                member.bpm = bpm if member.sharing else None
                member.updated = now
                member.sequence += 1
        except (ValueError, TypeError, AttributeError, asyncio.TimeoutError):
            await self.reject(socket, "消息格式无效或握手超时")
        except ConnectionClosed:
            pass  # 正常离开或网络中断；finally 清理成员与空房间。
        finally:
            self.connections.discard(socket)
            if member is not None and code in self.rooms:
                self.rooms[code].pop(member.id, None)
                if self.rooms[code]:
                    await self.broadcast(code)
                else:
                    self.rooms.pop(code, None)


async def start_server(relay: Relay, host: str = "127.0.0.1", port: int = 8765,
                       *, tls: Optional[ssl.SSLContext] = None) -> Server:
    def health(connection: ServerConnection, request: Request) -> Optional[Response]:
        if request.path == "/healthz":
            return connection.respond(200, "HeartApp relay v1 OK\n")
        if request.path != "/":
            return connection.respond(404, "Not found\n")
        return None

    return await serve(relay.handler, host, port, ssl=tls, compression=None, max_size=2048,
                       max_queue=4, write_limit=16384, open_timeout=10, close_timeout=2,
                       ping_interval=20, ping_timeout=20, origins=[None], process_request=health)


async def run(args: argparse.Namespace) -> None:
    tls = None
    if args.cert and args.key:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(args.cert, args.key)
    relay = Relay(max_clients=args.max_clients, room_size=args.room_size,
                  password=os.environ.get("HEARTAPP_RELAY_PASSWORD", ""))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    if os.name != "nt":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
    async with await start_server(relay, args.host, args.port, tls=tls):
        ticker = asyncio.create_task(relay.ticker())
        print(f"HeartApp relay v1 · {args.host}:{args.port} · max {args.max_clients} · room {args.room_size}", flush=True)
        try:
            await stop.wait()
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="HeartApp 自部署心率转发服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--max-clients", type=int, default=100)
    parser.add_argument("--room-size", type=int, default=4)
    parser.add_argument("--cert", help="TLS 证书文件")
    parser.add_argument("--key", help="TLS 私钥文件")
    args = parser.parse_args()
    if not 1 <= args.room_size <= 4 or not 1 <= args.max_clients <= 10000:
        parser.error("房间人数应为 1～4，总连接上限应为 1～10000")
    if bool(args.cert) != bool(args.key):
        parser.error("--cert 与 --key 必须同时提供")
    # 不记录 WebSocket 消息、昵称、房间码或心率。
    logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
