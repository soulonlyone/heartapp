"""服务器登录会话、房间目录与心率转发，全部为内存状态。"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import hmac
import secrets
import time
from typing import Dict, Optional

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed
from .room_protocol import CODE_ALPHABET, VERSION, decode, encode, room_code


@dataclass
class Member:
    socket: Optional[ServerConnection]
    name: str
    id: str = field(default_factory=lambda: secrets.token_hex(8))
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    room: str = ""
    offline_since: Optional[float] = None
    bpm: Optional[int] = None
    simulated: bool = False
    sharing: bool = False
    updated: float = 0.0
    sequence: int = 0
    ready: bool = False


class Relay:
    def __init__(self, *, max_clients: int = 100, room_size: int = 4, password: str = "") -> None:
        self.max_clients, self.room_size, self.password = max_clients, room_size, password
        self.rooms: Dict[str, Dict[str, Member]] = {}
        self.room_names: Dict[str, str] = {}
        self.sessions: Dict[str, Member] = {}
        self.connections = set()
        self.attempts: Dict[str, deque] = {}

    def snapshot(self, code: str) -> dict:
        now = time.monotonic()
        members = []
        for member in self.rooms.get(code, {}).values():
            online = member.socket is not None and member.ready
            fresh = online and now - member.updated <= 5
            members.append(dict(id=member.id, name=member.name,
                                bpm=member.bpm if fresh and member.sharing else None,
                                sharing=member.sharing, simulated=member.simulated, connected=online,
                                sample_seq=member.sequence, age_ms=round((now - member.updated) * 1000) if fresh else None))
        return dict(type="snapshot", room=code, members=members)

    def directory(self) -> dict:
        rooms = [dict(room=code, name=self.room_names[code], count=len(members),
                      online=sum(m.socket is not None and m.ready for m in members.values()), capacity=self.room_size)
                 for code, members in self.rooms.items()]
        return dict(type="rooms", rooms=rooms[:100], total=len(rooms))

    async def send(self, socket: Optional[ServerConnection], payload: dict) -> None:
        if socket is None:
            return
        try:
            await asyncio.wait_for(socket.send(encode(payload)), timeout=2)
        except (ConnectionClosed, asyncio.TimeoutError):
            await socket.close(code=1013, reason="接收超时")

    async def broadcast(self, code: str) -> None:
        payload = self.snapshot(code)
        await asyncio.gather(*(self.send(m.socket, payload) for m in list(self.rooms.get(code, {}).values()) if m.ready))

    async def list_all(self) -> None:
        payload = self.directory()
        await asyncio.gather(*(self.send(m.socket, payload) for m in list(self.sessions.values()) if m.socket and m.ready))

    async def leave_room(self, member: Member) -> None:
        code, member.room = member.room, ""
        member.bpm, member.sharing = None, False
        if code in self.rooms:
            self.rooms[code].pop(member.id, None)
            if self.rooms[code]:
                await self.broadcast(code)
            else:
                self.rooms.pop(code, None)
                self.room_names.pop(code, None)

    async def ticker(self) -> None:
        ticks = 0
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            expired = [m for m in self.sessions.values() if m.offline_since is not None and now - m.offline_since > 30]
            for member in expired:
                self.sessions.pop(member.token, None)
                await self.leave_room(member)
            await asyncio.gather(*(self.broadcast(code) for code in list(self.rooms)))
            ticks += 1
            if expired or ticks % 5 == 0:
                await self.list_all()
            for ip, attempts in list(self.attempts.items()):
                if not attempts or now - attempts[-1] > 60:
                    self.attempts.pop(ip, None)

    async def reject(self, socket: ServerConnection, message: str) -> None:
        await self.send(socket, dict(type="error", message=message))
        await socket.close(code=1008, reason="请求被拒绝")

    async def command(self, member: Member, packet: dict) -> None:
        action = packet.get("type")
        try:
            if action == "list":
                await self.send(member.socket, self.directory())
                return
            if action == "leave":
                await self.leave_room(member)
                await self.send(member.socket, dict(type="left"))
                await self.list_all()
                return
            if action not in ("create", "join"):
                raise ValueError("无效的房间操作")
            if member.room:
                raise ValueError("请先退出当前房间")
            if action == "create":
                title = packet.get("title") or f"{member.name}的房间"
                if not isinstance(title, str) or not 1 <= len(title.strip()) <= 32 or any(ord(c) < 32 for c in title):
                    raise ValueError("房间名称需要 1～32 个可见字符")
                code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
                while code in self.rooms:
                    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
                self.rooms[code], self.room_names[code] = {}, title.strip()
            else:
                code = room_code(packet.get("room", ""))
                if code not in self.rooms:
                    raise ValueError("房间不存在或已结束，请刷新列表")
                if len(self.rooms[code]) >= self.room_size:
                    raise ValueError("房间已满（含正在重连的席位）")
            member.room = code
            self.rooms[code][member.id] = member
            await self.welcome(member)
            await self.broadcast(code)
            await self.list_all()
        except (ValueError, TypeError, AttributeError) as exc:
            await self.send(member.socket, dict(type="command_error", message=str(exc)))

    async def welcome(self, member: Member) -> None:
        await self.send(member.socket, dict(type="welcome", version=VERSION, room=member.room,
                                           room_name=self.room_names.get(member.room, ""),
                                           self_id=member.id, capacity=self.room_size))

    async def handler(self, socket: ServerConnection) -> None:
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
        member = None
        logged_out = False
        try:
            hello = decode(await asyncio.wait_for(socket.recv(), 10))
            if hello.get("version") != VERSION:
                await self.reject(socket, "联机版本不兼容，请同时更新客户端和服务端")
                return
            if hello.get("type") != "login":
                await self.reject(socket, "请先登录服务器")
                return
            token = hello.get("resume_token", "")
            member = self.sessions.get(token) if isinstance(token, str) else None
            if member is not None and (member.socket is not None or member.offline_since is None or now - member.offline_since > 30):
                member = None
            if member is None:
                if self.password and not hmac.compare_digest(str(hello.get("password", "")).encode(), self.password.encode()):
                    await self.reject(socket, "服务器访问密码错误")
                    return
                name = hello.get("name", "")
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 24 or any(ord(c) < 32 for c in name):
                    await self.reject(socket, "昵称需要 1～24 个可见字符")
                    return
                if len(self.sessions) >= self.max_clients * 2:
                    await self.reject(socket, "登录会话已满，请稍后重试")
                    return
                member = Member(socket=socket, name=name.strip())
                self.sessions[member.token] = member
            else:
                member.socket, member.offline_since = socket, None
                member.ready = False
            await self.send(socket, dict(type="login_ok", version=VERSION, self_id=member.id,
                                        resume_token=member.token, room=member.room))
            member.ready = True
            if member.room:
                await self.welcome(member)
                await self.broadcast(member.room)
            await self.send(socket, self.directory())
            rate = deque()
            async for raw in socket:
                now = time.monotonic()
                while rate and now - rate[0] > 2:
                    rate.popleft()
                rate.append(now)
                if len(rate) > 8:
                    await self.reject(socket, "发送过于频繁")
                    break
                packet = decode(raw)
                if packet.get("type") == "logout":
                    logged_out = True
                    break
                if packet.get("type") != "reading":
                    await self.command(member, packet)
                    continue
                bpm = packet.get("bpm")
                if (bpm is not None and (type(bpm) is not int or not 1 <= bpm <= 300)) or type(packet.get("sharing")) is not bool or type(packet.get("simulated")) is not bool:
                    await self.reject(socket, "无效的心率消息")
                    break
                if member.room:
                    member.sharing = packet["sharing"]
                    member.simulated = packet["simulated"]
                    member.bpm = bpm if member.sharing else None
                    member.updated, member.sequence = now, member.sequence + 1
        except (ValueError, TypeError, AttributeError, asyncio.TimeoutError):
            await self.reject(socket, "消息格式无效或握手超时")
        except ConnectionClosed:
            pass
        finally:
            self.connections.discard(socket)
            if member is not None and member.socket is socket:
                member.socket, member.bpm = None, None
                member.ready = False
                if logged_out:
                    self.sessions.pop(member.token, None)
                    await self.leave_room(member)
                else:
                    member.offline_since = time.monotonic()
                    if member.room:
                        await self.broadcast(member.room)
                await self.list_all()
