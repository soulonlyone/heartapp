"""独立网络线程，联机故障不影响采集和本地保存。"""
from __future__ import annotations

import asyncio
from collections import deque
import threading
import time
from typing import Optional

from .room_protocol import VERSION, decode, encode, room_code, server_url


class RoomClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events = deque(maxlen=100)
        self._stop = threading.Event()
        self._thread = None
        self._generation = 0
        self._reading = (None, False, False, 0.0)

    def _emit(self, generation: int, kind: str, **payload) -> None:
        with self._lock:
            if generation == self._generation:
                self._events.append((kind, payload))

    def poll_events(self) -> list:
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events

    def publish(self, bpm: Optional[int], *, sharing: bool, simulated: bool) -> None:
        if type(bpm) is not int or not 1 <= bpm <= 300:
            bpm = None
        with self._lock:
            self._reading = (bpm if sharing else None, bool(sharing), bool(simulated), time.monotonic())

    def connect(self, address: str, name: str, *, code: str = "", password: str = "") -> None:
        address = server_url(address)
        name = name.strip()
        if not 1 <= len(name) <= 24 or any(ord(c) < 32 for c in name):
            raise ValueError("请输入 1～24 个可见字符作为昵称")
        code = room_code(code) if code else ""
        self.leave()
        stop = self._stop = threading.Event()
        generation = self._generation
        self._thread = threading.Thread(target=self._run, args=(generation, stop, address, name, code, password),
                                        name="heartapp-room", daemon=True)
        self._thread.start()

    def leave(self) -> None:
        self._stop.set()
        with self._lock:
            self._generation += 1
            self._events.clear()
            self._reading = (None, False, False, 0.0)

    def _run(self, generation: int, stop: threading.Event, address: str, name: str, code: str, password: str) -> None:
        try:
            asyncio.run(self._session(generation, stop, address, name, code, password))
        except Exception as exc:  # noqa: BLE001
            # 网络线程最后一道边界；只通知联机页，不退出采集或 GUI。
            self._emit(generation, "room_error", message=f"联机连接失败：{type(exc).__name__}")

    async def _session(self, generation: int, stop: threading.Event, address: str,
                       name: str, code: str, password: str) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed

        joined, retry = False, 0
        while not stop.is_set():
            self._emit(generation, "room_status", message="正在重新连接房间…" if joined else "正在连接服务器…")
            try:
                async with connect(address, compression=None, max_size=16384, max_queue=4, open_timeout=5,
                                   close_timeout=1, ping_interval=20, ping_timeout=20, proxy=None) as socket:
                    if stop.is_set():
                        return
                    await socket.send(encode(dict(type="join" if code else "create", version=VERSION,
                                                  name=name, room=code, password=password)))
                    welcome = decode(await asyncio.wait_for(socket.recv(), 8))
                    if welcome.get("type") == "error":
                        self._emit(generation, "room_error", message=str(welcome.get("message", "连接被拒绝")))
                        return
                    if welcome.get("type") != "welcome" or welcome.get("version") != VERSION:
                        raise ValueError("联机协议不兼容")
                    code = room_code(welcome["room"])
                    joined, retry = True, 0
                    self._emit(generation, "room_welcome", **welcome)
                    next_send, last_sharing = 0.0, None
                    while not stop.is_set():
                        with self._lock:
                            bpm, sharing, simulated, stamp = self._reading
                        now = time.monotonic()
                        if now >= next_send or sharing != last_sharing:
                            await socket.send(encode(dict(type="reading", bpm=bpm if now - stamp <= 5 else None,
                                                          sharing=sharing, simulated=simulated)))
                            next_send, last_sharing = now + 1, sharing
                        try:
                            packet = decode(await asyncio.wait_for(socket.recv(), .25))
                        except asyncio.TimeoutError:
                            continue
                        if packet.get("type") == "error":
                            self._emit(generation, "room_error", message=str(packet.get("message", "房间请求失败")))
                            return
                        if packet.get("type") == "snapshot":
                            members = packet.get("members")
                            if not isinstance(members, list) or len(members) > 4 or any(not isinstance(m, dict) for m in members):
                                raise ValueError("房间数据无效")
                            self._emit(generation, "room_snapshot", members=members)
            except (OSError, ConnectionClosed, asyncio.TimeoutError):
                if not joined:
                    self._emit(generation, "room_error", message="无法连接服务器，请检查地址、端口、证书或网络")
                    return
                retry += 1
                if retry > 5:
                    self._emit(generation, "room_error", message="重连失败，请重新加入房间；本地记录不受影响")
                    return
                self._emit(generation, "room_status", message="连接已中断，正在重连；本地记录继续", clear=True)
                deadline = time.monotonic() + min(2 ** (retry - 1), 8)
                while not stop.is_set() and time.monotonic() < deadline:
                    await asyncio.sleep(.1)
