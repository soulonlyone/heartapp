"""服务端面板的配置与后台生命周期；不依赖 Qt、BLE 或数据库。"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass
import errno
import json
from pathlib import Path
import threading
from typing import List, Optional

from .lobby import Relay
from .relay import start_server
from .room_protocol import MAX_ROOM_SIZE, server_url


@dataclass(frozen=True)
class ServerSettings:
    scope: str = "lan"
    port: int = 8765
    max_clients: int = 20
    room_size: int = 4
    public_url: str = ""

    def validate(self) -> None:
        if self.scope not in ("lan", "local"):
            raise ValueError("请选择局域网或仅本机监听。")
        for value, low, high, message in (
            (self.port, 1, 65535, "端口应为 1～65535。"),
            (self.max_clients, 1, 10000, "连接上限应为 1～10000。"),
            (self.room_size, 1, MAX_ROOM_SIZE, f"房间人数应为 1～{MAX_ROOM_SIZE}。"),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError(message)
        if self.public_url and not self.public_address.startswith("wss://"):
            raise ValueError("公网地址请填写 https:// 或 wss:// 地址。")

    @property
    def host(self) -> str:
        return "0.0.0.0" if self.scope == "lan" else "127.0.0.1"

    @property
    def public_address(self) -> str:
        value = self.public_url.strip()
        if not value:
            return ""
        return server_url(value if "://" in value else "wss://" + value)


def load_settings(path: Path) -> tuple:
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("配置格式错误")
        settings = ServerSettings(**{key: values[key] for key in asdict(ServerSettings()) if key in values})
        if not isinstance(settings.public_url, str):
            raise ValueError("公网地址格式错误")
        settings.validate()
        return settings, ""
    except FileNotFoundError:
        return ServerSettings(), ""
    except (OSError, ValueError, TypeError):
        return ServerSettings(), "无法读取原配置，已使用默认值；请确认设置后再启动。"


def save_settings(path: Path, settings: ServerSettings) -> None:
    """原子替换无凭据的配置；保存失败向界面报告，不启动服务。"""
    settings.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def server_error(exc: Exception) -> str:
    code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
    if code in (errno.EADDRINUSE, 10048):
        return "端口已被占用。请换一个端口，或先停止已有的服务端／后台自启服务。"
    if code in (errno.EACCES, 10013):
        return "系统拒绝监听此端口。请检查端口保留规则，或更换端口后重试。"
    return f"服务未能运行（{type(exc).__name__}）。请检查配置和本机网络后重试。"


class ServerController:
    """仅管理本面板启动的实例，不接管或停止其他进程。"""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events = deque(maxlen=128)
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop: Optional[asyncio.Event] = None
        self._stop_requested = threading.Event()
        self._active = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def _emit(self, kind: str, **payload) -> None:
        with self._lock:
            self._events.append((kind, payload))

    def poll(self) -> List[tuple]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events

    def start(self, settings: ServerSettings, password: str) -> bool:
        settings.validate()
        if not password or len(password) > 256:
            raise ValueError("请设置 1～256 个字符的访问密码。")
        with self._lock:
            if self._active:
                return False
            self._active = True
            self._events.clear()
            self._stop_requested.clear()
        self._thread = threading.Thread(target=self._worker, args=(settings, password), daemon=True,
                                        name="HeartApp relay panel")
        try:
            self._thread.start()
        except RuntimeError:
            with self._lock:
                self._active = False
            raise
        return True

    def stop(self) -> None:
        self._stop_requested.set()
        with self._lock:
            loop, stop = self._loop, self._stop
            if loop is not None and stop is not None:
                loop.call_soon_threadsafe(stop.set)

    def _worker(self, settings: ServerSettings, password: str) -> None:
        try:
            asyncio.run(self._serve(settings, password))
        except Exception as exc:
            # 线程边界：显式上报失败，恢复可重试状态，不泄露密码或原始消息。
            self._emit("error", message=server_error(exc))
        finally:
            with self._lock:
                self._active = False
                self._events.append(("stopped", {}))

    async def _serve(self, settings: ServerSettings, password: str) -> None:
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()
            stop = self._stop
        tasks = []
        try:
            if self._stop_requested.is_set():
                return
            relay = Relay(max_clients=settings.max_clients, room_size=settings.room_size, password=password)
            async with await start_server(relay, settings.host, settings.port):
                self._emit("running")

                async def statistics():
                    while True:
                        online = sum(m.socket is not None and m.ready for m in relay.sessions.values())
                        self._emit("stats", online=online, rooms=len(relay.rooms))
                        await asyncio.sleep(1)

                if self._stop_requested.is_set():
                    stop.set()
                tasks = [asyncio.create_task(stop.wait()), asyncio.create_task(relay.ticker()),
                         asyncio.create_task(statistics())]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with self._lock:
                self._loop = None
                self._stop = None
