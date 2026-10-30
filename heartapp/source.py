"""心率数据源的公共接口。

``BleHeartRateSource``（真实手表）和 ``SimulatedHeartRateSource``（模拟）实现同一
套接口，因此界面、记录逻辑和测试都不需要区分数据从哪来。
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional, Protocol, runtime_checkable

from .hr import HeartRateSample, HeartRateMeasurement  # noqa: F401  (re-export)

#: 数据源回调：每拿到一个有效心率就调用一次（在事件循环线程中同步执行，要快）
EmitFn = Callable[[HeartRateSample], None]

#: 状态回调：``on_status(state, detail)``
StatusFn = Callable[[str, str], None]

#: 运行状态取值
STATE_IDLE = "idle"
STATE_SCANNING = "scanning"
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_RECONNECTING = "reconnecting"
STATE_STOPPED = "stopped"
STATE_ERROR = "error"


class HeartRateSourceError(RuntimeError):
    """数据源相关错误的基类。"""


class DeviceNotFoundError(HeartRateSourceError):
    """扫描不到指定设备。"""


class NoHeartRateServiceError(HeartRateSourceError):
    """连上了设备，但设备没有标准心率服务/特征。

    最常见原因是手表上的「心率广播」没有打开。
    """


@runtime_checkable
class HeartRateSource(Protocol):
    """一个能持续产出心率的数据源。"""

    kind: str
    label: str

    async def run(
        self,
        emit: EmitFn,
        stop: asyncio.Event,
        on_status: Optional[StatusFn] = None,
    ) -> None:
        """持续产出心率，直到 ``stop`` 被置位。

        实现方负责自己的重连与错误恢复：``run`` 正常返回即表示收到停止信号，
        不应因为一次连接失败而抛出。
        """
        ...


async def sleep_or_stop(seconds: float, stop: asyncio.Event) -> bool:
    """可被 ``stop`` 提前打断的 sleep。返回 True 表示是被停止打断的。"""
    if seconds <= 0:
        return stop.is_set()
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


def make_status_callback(on_status: Optional[StatusFn]) -> StatusFn:
    if on_status is None:
        return lambda _state, _detail: None
    return on_status
