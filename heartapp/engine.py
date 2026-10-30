"""采集引擎：把「数据源 → 记录 → 数据库」串起来，并让界面保持响应。

线程模型（重要）
----------------
* 一个守护线程运行 asyncio 事件循环；扫描、连接、收数据、**所有会话状态变更**都在
  这个循环里执行。
* 界面（Tk 主线程）**绝不**直接碰 asyncio 对象，只做两件事：
  1. 调 :meth:`Engine.submit` 把协程投递到循环线程；
  2. 用 :meth:`Engine.poll_events` 取事件（内部是 ``queue.Queue``）。
* 为什么连「开始/停止记录」也要丢回循环线程：``_pending`` 缓冲和 ``_session_id``
  由收数据的回调读写，如果主线程同时改，就会出现丢数据或重复落库。让它们共用同一
  个线程是消灭这类竞态最省事也最可靠的办法。

事件类型（``poll_events`` 产出的 ``(kind, payload)``）
------------------------------------------------------
====================  ==================================================
``status``            ``{"state": str, "detail": str}``
``sample``            ``{"sample": HeartRateSample}``
``error``             ``{"message": str}``
``devices``           ``{"devices": [DiscoveredDevice, ...]}``
``session_started``   ``{"session_id": int, "started_at": datetime, ...}``
``session_ended``     ``{"session_id": int, "stats": SessionStats}``
``log``               ``{"message": str}``
====================  ==================================================
"""

from __future__ import annotations

import asyncio
import inspect
import queue
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from . import ble, metrics
from .hr import HeartRateSample
from .source import (
    STATE_ERROR,
    STATE_IDLE,
    HeartRateSource,
    HeartRateSourceError,
)
from .store import HeartRateStore

#: 攒够多少条采样再落库（一次事务写多条，比逐条写快很多）
FLUSH_EVERY = 8

#: 最长多久强制落库一次，避免低频采样时数据长时间只在内存里
FLUSH_MAX_SECONDS = 5.0

Event = Tuple[str, Dict[str, Any]]


class EngineNotStartedError(RuntimeError):
    """引擎线程还没起来。"""


class Engine:
    """采集引擎。所有公开方法都可以从任意线程调用。"""

    def __init__(
        self,
        store: HeartRateStore,
        *,
        on_event: Optional[Callable[[Event], None]] = None,
    ) -> None:
        self.store = store
        self._sink = on_event
        self._events: "queue.Queue[Event]" = queue.Queue()

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_ready = threading.Event()

        self._stop_event: Optional[asyncio.Event] = None
        self._run_task: Optional[asyncio.Task] = None
        self._source: Optional[HeartRateSource] = None
        self._source_kind = "none"
        self._device_name: Optional[str] = None
        self._device_address: Optional[str] = None

        # 以下状态只在引擎循环线程中被读写
        self._session_id: Optional[int] = None
        self._session_start: Optional[datetime] = None
        self._session_clock = 0.0
        self._pending: List[Tuple[datetime, float, int, Optional[bool], Optional[float]]] = []
        self._last_flush = 0.0
        self._device_lock = threading.RLock()

        self.state = STATE_IDLE
        self.state_detail = "未连接"
        #: 最近一次错误的文字。普通属性，跨线程读取安全；供 wait_for_state 判断失败
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> "Engine":
        """启动引擎线程。可重复调用。"""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._loop_ready.clear()
        self._thread = threading.Thread(
            target=self._thread_main, name="heartapp-engine", daemon=True
        )
        self._thread.start()
        if not self._loop_ready.wait(timeout=10.0):
            raise EngineNotStartedError("引擎线程启动超时")
        return self

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:  # noqa: BLE001
                pass
            finally:
                loop.close()

    def shutdown(self, timeout: float = 10.0) -> None:
        """停止引擎。会先收尾正在记录的会话，保证数据落库。"""
        thread, loop = self._thread, self._loop
        if thread is None or loop is None:
            return
        if not thread.is_alive():
            self._thread = self._loop = None
            return
        try:
            if self.is_recording:
                self.stop_recording(wait=True, timeout=timeout)
            self.stop_source(wait=True, timeout=timeout)
        except Exception:  # noqa: BLE001
            pass
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass
        thread.join(timeout=timeout)
        self._thread = None
        self._loop = None

    # ------------------------------------------------------------------
    # 线程调度
    # ------------------------------------------------------------------

    @property
    def _on_engine_thread(self) -> bool:
        return threading.current_thread() is self._thread

    def _ensure_started(self) -> None:
        if self._loop is None or not self._loop.is_running():
            if self._on_engine_thread:
                raise EngineNotStartedError("不能在引擎线程内启动引擎")
            self.start()

    def submit(self, coro: Any) -> Future:
        """把协程投递到引擎循环，返回 ``concurrent.futures.Future``。"""
        self._ensure_started()
        loop = self._loop
        assert loop is not None
        return asyncio.run_coroutine_threadsafe(coro, loop)

    async def _invoke(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """在引擎循环里执行 ``fn``。

        ``fn`` 既可能是普通函数（如 ``_start_recording_impl``），也可能是协程函数
        （如 ``connect_simulator`` 内部的 ``_impl``）。这里统一处理，避免出现
        「协程被创建但从没被 await」这种静默失效。
        """
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def _dispatch(
        self,
        fn: Callable[..., Any],
        *args: Any,
        wait: bool = False,
        result_timeout: float = 20.0,
        **kwargs: Any,
    ) -> Any:
        """在引擎循环线程执行 ``fn``。

        ``wait=True`` 时阻塞取回结果（CLI/测试用）；否则立即返回，结果通过事件暴露，
        失败则发 ``error`` 事件。``result_timeout`` 只约束「等结果」这件事，
        其余关键字参数原样传给 ``fn``。
        """
        if self._on_engine_thread:
            # 已经在线程里就直接跑，避免自等死锁
            return fn(*args, **kwargs)

        future = self.submit(self._invoke(fn, *args, **kwargs))
        if wait:
            return future.result(timeout=result_timeout)

        def _done(fut: Future) -> None:
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001
                self._emit("error", message=f"{exc}")

        future.add_done_callback(_done)
        return None

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    def _emit(self, kind: str, **payload: Any) -> None:
        if kind == "error":
            self.last_error = str(payload.get("message", ""))
        event = (kind, payload)
        if self._sink is not None:
            try:
                self._sink(event)
            except Exception:  # noqa: BLE001
                pass
        self._events.put(event)

    def poll_events(self, max_items: int = 500) -> List[Event]:
        out: List[Event] = []
        for _ in range(max_items):
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                break
        return out

    def drain_events(self) -> None:
        self.poll_events(max_items=100000)

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        task = self._run_task
        return task is not None and not task.done()

    @property
    def is_recording(self) -> bool:
        return self._session_id is not None

    @property
    def session_id(self) -> Optional[int]:
        return self._session_id

    @property
    def source_kind(self) -> str:
        return self._source_kind

    @property
    def device_name(self) -> Optional[str]:
        return self._device_name

    def _status(self, state: str, detail: str) -> None:
        self.state = state
        self.state_detail = detail
        self._emit("status", state=state, detail=detail)

    # ------------------------------------------------------------------
    # 数据源：内部实现（都在引擎线程执行）
    # ------------------------------------------------------------------

    async def _async_stop_source(self, timeout: float = 8.0) -> None:
        task = self._run_task
        if self._stop_event is not None:
            self._stop_event.set()
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
        self._run_task = None

    async def _async_start_source(
        self,
        source: HeartRateSource,
        *,
        kind: str,
        device_name: Optional[str],
        device_address: Optional[str],
    ) -> None:
        if self._session_id is not None and self.store.pc_context(self._session_id):
            raise ValueError("请先结束当前 PC 会话，再更换数据源，避免混合模拟和真实数据。")
        await self._async_stop_source()
        self._stop_event = asyncio.Event()
        self._source = source
        self._source_kind = kind
        self._device_name = device_name
        self._device_address = device_address

        stop_event = self._stop_event

        async def _runner() -> None:
            try:
                await source.run(self._on_sample, stop_event, self._status)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._status(STATE_ERROR, f"数据源异常：{exc}")
                self._emit("error", message=f"数据源异常：{exc}")

        self._run_task = asyncio.get_running_loop().create_task(_runner())

    # ------------------------------------------------------------------
    # 数据源：公开接口
    # ------------------------------------------------------------------

    def scan(self, timeout: float = 8.0, *, include_all: bool = True) -> None:
        """后台扫描设备，结果通过 ``devices`` 事件返回。"""

        async def _impl() -> None:
            self.last_error = None
            self._status("scanning", f"正在扫描附近蓝牙设备（{timeout:.0f} 秒）…")
            try:
                devices = await ble.scan_devices(timeout=timeout, include_all=include_all)
            except Exception as exc:  # noqa: BLE001
                self._status(STATE_ERROR, f"扫描失败：{exc}")
                self._emit("error", message=self._bluetooth_hint(exc))
                self._emit("devices", devices=[])
                return
            if devices:
                hr_count = sum(1 for d in devices if d.has_heart_rate)
                detail = f"扫描到 {len(devices)} 个设备，其中 {hr_count} 个广播包包含心率服务"
                if hr_count == 0:
                    detail += "。没有 ♥ 标记也可以选择设备连接后检查心率服务。"
            else:
                detail = "没有扫描到任何设备。请确认电脑蓝牙已打开、手表已开启「心率广播」。"
            self._status(STATE_IDLE, detail)
            self._emit("devices", devices=devices)

        self._dispatch(_impl)

    def connect_device(self, target: Union[ble.DiscoveredDevice, str]) -> None:
        """连接设备（``DiscoveredDevice`` 对象，或名称/地址字符串）。"""

        async def _impl() -> None:
            self.last_error = None
            try:
                if isinstance(target, ble.DiscoveredDevice):
                    device = target
                else:
                    self._status("connecting", f"正在查找 “{target}” …")
                    device = await ble.find_device(str(target))
                name = device.name or device.address
                source = ble.BleHeartRateSource(device, display_name=name)
                await self._async_start_source(
                    source, kind="ble", device_name=name, device_address=device.address
                )
            except HeartRateSourceError as exc:
                self._status(STATE_ERROR, str(exc))
                self._emit("error", message=str(exc))
            except Exception as exc:  # noqa: BLE001
                self._status(STATE_ERROR, f"连接失败：{exc}")
                self._emit("error", message=self._bluetooth_hint(exc))

        self._dispatch(_impl)

    def connect_simulator(self, profile: str = "interval") -> None:
        """使用内置模拟数据源（不需要手表）。"""
        from .simulator import PROFILES, SimulatedHeartRateSource

        if profile not in PROFILES:
            profile = "interval"
        source = SimulatedHeartRateSource(profile=profile)

        async def _impl() -> None:
            self.last_error = None
            await self._async_start_source(
                source,
                kind="sim",
                device_name=source.label,
                device_address="SIMULATED",
            )

        self._dispatch(_impl)

    def stop_source(self, *, wait: bool = False, timeout: float = 8.0) -> None:
        """停止当前数据源。**不**会结束正在进行的记录（会话会被正常收尾）。"""
        self._dispatch(
            self._async_stop_source,
            timeout=timeout,
            wait=wait,
            result_timeout=timeout + 5.0,
        )

    def wait_for_state(self, states: Tuple[str, ...], timeout: float = 25.0) -> bool:
        """阻塞等待引擎进入某个状态（CLI/测试用）。

        **不会**消费事件队列。早期版本在这里调 ``poll_events``，结果把状态事件从
        队列里抽干了，界面随后就再也看不到「已连接」这类事件。等待状态只读
        ``state`` / ``last_error`` 这两个普通属性即可。
        """
        if isinstance(states, str):
            states = (states,)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.state in states:
                return True
            if self.last_error is not None or self.state == STATE_ERROR:
                return False
            time.sleep(0.05)
        return self.state in states

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------

    def _start_recording_impl(self, note: Optional[str]) -> Optional[int]:
        if self._session_id is not None:
            return self._session_id
        started_at = datetime.now()
        session_id = self.store.start_session(
            started_at=started_at,
            device_name=self._device_name,
            device_address=self._device_address,
            source=self._source_kind,
            note=note,
        )
        self._session_id = session_id
        self._session_start = started_at
        self._session_clock = time.monotonic()
        self._pending = []
        self._last_flush = time.monotonic()
        self._emit(
            "session_started",
            session_id=session_id,
            started_at=started_at,
            device_name=self._device_name,
            source=self._source_kind,
        )
        return session_id

    def _stop_recording_impl(self) -> Optional[metrics.SessionStats]:
        if self._session_id is None:
            return None
        session_id = self._session_id
        self._flush()
        self.store.end_session(session_id, ended_at=datetime.now())
        stats = self.session_stats(session_id)
        self._session_id = None
        self._session_start = None
        self._emit("session_ended", session_id=session_id, stats=stats)
        return stats

    def start_recording(
        self, note: Optional[str] = None, *, wait: bool = False, timeout: float = 15.0
    ) -> Optional[int]:
        """开始记录。``wait=True`` 时返回 session id，否则结果通过 ``session_started`` 事件给出。"""
        return self._dispatch(
            self._start_recording_impl, note, wait=wait, result_timeout=timeout
        )

    def stop_recording(
        self, *, wait: bool = False, timeout: float = 15.0
    ) -> Optional[metrics.SessionStats]:
        """结束记录。``wait=True`` 时返回统计，否则通过 ``session_ended`` 事件给出。"""
        return self._dispatch(self._stop_recording_impl, wait=wait, result_timeout=timeout)

    def start_pc_recording(self, title: str, activity: str) -> None:
        def impl() -> None:
            if self.is_recording:
                return
            sid = self._start_recording_impl(None)
            self.store.set_pc_context(sid, title.strip() or "未命名 PC 会话", activity)
        self._dispatch(impl)

    def mark_event(self, label: str = "标记") -> None:
        def impl() -> None:
            if self._session_id is None:
                return
            offset = max(0.0, time.monotonic() - self._session_clock)
            self.store.add_event(self._session_id, offset, label)
            self._emit("marker", t_offset=offset, label=label.strip()[:120])
        self._dispatch(impl)

    def _flush(self) -> None:
        if not self._pending or self._session_id is None:
            return
        batch = self._pending
        try:
            self.store.add_samples(self._session_id, batch)
        except Exception as exc:  # noqa: BLE001
            self._emit("error", message=f"写入数据库失败：{exc}")
            raise
        self._pending = []
        self._last_flush = time.monotonic()

    def _on_sample(self, sample: HeartRateSample) -> None:
        if self._session_id is not None:
            sample.t_offset = max(0.0, time.monotonic() - self._session_clock)
        self._emit("sample", sample=sample)
        if self._session_id is None or self._session_start is None:
            return
        if sample.bpm <= 0:
            return
        rr_first = sample.rr_intervals_ms[0] if sample.rr_intervals_ms else None
        t_offset = sample.t_offset
        self._pending.append((sample.ts, t_offset, sample.bpm, sample.contact, rr_first))
        now = time.monotonic()
        if len(self._pending) >= FLUSH_EVERY or (now - self._last_flush) >= FLUSH_MAX_SECONDS:
            self._flush()

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def max_hr(self) -> int:
        override = self.store.get_int_setting("max_hr", 0)
        if override > 0:
            return override
        age = self.store.get_int_setting("age", metrics.DEFAULT_AGE)
        return metrics.estimate_max_hr(age)

    def session_stats(self, session_id: int) -> metrics.SessionStats:
        samples = self.store.session_samples(session_id)
        return metrics.compute_stats(samples, max_hr=self.max_hr(), session_id=session_id)

    # ------------------------------------------------------------------
    # 错误提示
    # ------------------------------------------------------------------

    @staticmethod
    def _bluetooth_hint(exc: Exception) -> str:
        text = str(exc)
        lowered = text.lower()
        if "turned off" in lowered or "radio" in lowered and "bluetooth" in lowered:
            return f"{text}\n提示：Windows 的蓝牙可能没打开，请到「设置 → 蓝牙和其他设备」开启。"
        if "access is denied" in lowered or "0x80070005" in lowered:
            return (
                f"{text}\n提示：系统拒绝了蓝牙访问。请到「设置 → 隐私和安全性 → 蓝牙」"
                "允许应用访问蓝牙。"
            )
        if "winrt" in lowered or "windows.devices" in lowered:
            return f"{text}\n提示：bleak 的 Windows 组件异常，可重装依赖（见 README）。"
        return text
