"""模拟数据源。

存在的理由
----------
1. **不需要手表就能验证整套软件**（落库、画图、统计、导出）。
2. 手表不在身边、蓝牙没开、或者想先看看界面长什么样的时候，直接就能用。
3. 自动化测试依赖它，不需要真人戴着表。

生成的心率不是纯随机数：带热身爬升、呼吸性窦性心律不齐（RSA）、
缓慢漂移、以及偶发的「测不到脉搏」丢帧，尽量接近真实手表广播的样子。
"""

from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime
from typing import Optional, Tuple

from .hr import HeartRateSample
from .source import (
    STATE_CONNECTED,
    STATE_STOPPED,
    EmitFn,
    StatusFn,
    make_status_callback,
    sleep_or_stop,
)

PROFILES = {
    "rest": "静息（坐着不动，约 60-75）",
    "workout": "有氧运动（热身→稳定 135 左右→放松）",
    "interval": "间歇训练（高低强度交替）",
}

DEFAULT_PROFILE = "interval"


def _target_bpm(profile: str, t: float, rng: random.Random) -> float:
    """给定已运行秒数，返回当前的目标心率。"""
    if profile == "rest":
        base = 66.0 + 4.0 * math.sin(2 * math.pi * t / 180.0)
        # 偶尔一次深呼吸，心率轻微下探
        base -= 3.0 * max(0.0, math.sin(2 * math.pi * t / 75.0)) ** 4
        return base

    if profile == "workout":
        if t < 90.0:  # 热身
            progress = t / 90.0
            return 78.0 + (138.0 - 78.0) * (progress ** 0.7)
        if t < 600.0:  # 稳定有氧，带缓慢波动
            return 138.0 + 6.0 * math.sin(2 * math.pi * t / 210.0) - 4.0 * ((t - 90.0) / 510.0)
        # 放松
        cool = min(1.0, (t - 600.0) / 120.0)
        return 134.0 - 50.0 * cool

    # interval：60 秒一个循环，25 秒高强度 / 35 秒恢复
    phase = t % 60.0
    if phase < 6.0:  # 爬升
        return 118.0 + (168.0 - 118.0) * (phase / 6.0)
    if phase < 25.0:  # 高强度
        return 168.0 + 5.0 * math.sin(2 * math.pi * (phase - 6.0) / 12.0)
    if phase < 33.0:  # 回落
        return 168.0 - (168.0 - 122.0) * ((phase - 25.0) / 8.0)
    return 122.0 + 4.0 * math.sin(2 * math.pi * (phase - 33.0) / 27.0)  # 恢复


class SimulatedHeartRateSource:
    """以固定频率产出模拟心率的假手表。"""

    kind = "sim"

    def __init__(
        self,
        profile: str = DEFAULT_PROFILE,
        *,
        interval: float = 1.0,
        seed: Optional[int] = None,
        display_name: str = "模拟手表",
        dropout_rate: float = 0.004,
    ) -> None:
        self.profile = profile if profile in PROFILES else DEFAULT_PROFILE
        self.interval = max(0.1, float(interval))
        self.display_name = display_name
        self.dropout_rate = max(0.0, min(0.5, float(dropout_rate)))
        self._rng = random.Random(seed)

    @property
    def label(self) -> str:
        return self.display_name

    async def run(
        self,
        emit: EmitFn,
        stop: asyncio.Event,
        on_status: Optional[StatusFn] = None,
    ) -> None:
        status = make_status_callback(on_status)
        rng = self._rng
        status(
            STATE_CONNECTED,
            f"模拟数据源已启动（{PROFILES.get(self.profile, self.profile)}）",
        )

        started = asyncio.get_running_loop().time()
        smoothed: Optional[float] = None

        while not stop.is_set():
            elapsed = asyncio.get_running_loop().time() - started
            target = _target_bpm(self.profile, elapsed, rng)

            # 一阶低通，让曲线连续；再叠加逐拍噪声
            smoothed = target if smoothed is None else smoothed + (target - smoothed) * 0.35
            bpm = smoothed + rng.gauss(0.0, 1.6)
            bpm_int = int(round(max(35.0, min(200.0, bpm))))

            contact = True
            if rng.random() < self.dropout_rate:
                # 模拟手表短暂测不到脉搏：真实设备会发 0 bpm / 未贴合
                contact = rng.random() < 0.5
                bpm_int = 0

            rr_intervals = ()
            if bpm_int > 0:
                mean_rr = 60000.0 / bpm_int
                # 呼吸性窦性心律不齐：吸气略快、呼气略慢
                rsa = 22.0 * math.sin(2 * math.pi * elapsed / 4.5)
                rr_intervals = tuple(
                    max(250.0, mean_rr + rsa * (-1 if i % 2 else 1) + rng.gauss(0.0, 12.0))
                    for i in range(2)
                )

            emit(
                HeartRateSample(
                    ts=datetime.now(),
                    bpm=bpm_int,
                    contact=contact,
                    rr_intervals_ms=rr_intervals,
                    source=self.kind,
                )
            )

            if await sleep_or_stop(self.interval, stop):
                break

        status(STATE_STOPPED, "模拟数据源已停止")
