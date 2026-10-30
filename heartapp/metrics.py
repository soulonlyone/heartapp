"""心率统计：均值/极值、静息心率、心率区间时长、HRV(RMSSD)。

对「手表蓝牙心率广播」的诚实说明
--------------------------------
广播只给出逐秒左右的心率值，RR 间期字段不是所有手表都会发。因此：

* 均值/极值/区间时长 —— 可靠；
* RMSSD（HRV）—— 只有在拿到足够多 RR 间期时才计算，且**仅供参考**，
  因为广播采样率远低于逐拍心电/光电容积的采样率。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

#: 相邻采样间隔超过该秒数就不再计入区间时长，避免把「离开/断连」的时间算进某个区间
MAX_GAP_S = 10.0

REST_LABEL = "静息"

#: (名称, 占最大心率下限, 上限)
ZONE_DEFINITIONS: Tuple[Tuple[str, float, float], ...] = (
    ("Z1 恢复", 0.50, 0.60),
    ("Z2 燃脂", 0.60, 0.70),
    ("Z3 有氧", 0.70, 0.80),
    ("Z4 无氧", 0.80, 0.90),
    ("Z5 极限", 0.90, 1.00),
)

DEFAULT_AGE = 30
DEFAULT_RESTING_HR = 60


def estimate_max_hr(age: int) -> int:
    """Tanaka 公式的常用简化式 220 - 年龄。"""
    age = max(10, min(100, int(age)))
    return 220 - age


def zone_names() -> List[str]:
    return [REST_LABEL] + [name for name, _, _ in ZONE_DEFINITIONS]


def zone_for_bpm(bpm: float, max_hr: int) -> str:
    if max_hr <= 0:
        return REST_LABEL
    ratio = bpm / max_hr
    if ratio < ZONE_DEFINITIONS[0][1]:
        return REST_LABEL
    for name, low, high in ZONE_DEFINITIONS:
        if low <= ratio < high:
            return name
    return ZONE_DEFINITIONS[-1][0]


# ---------------------------------------------------------------------------
# 基础统计
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionStats:
    session_id: Optional[int]
    sample_count: int
    duration_s: float
    avg_bpm: Optional[float]
    min_bpm: Optional[int]
    max_bpm: Optional[int]
    resting_bpm: Optional[float]
    first_bpm: Optional[int]
    last_bpm: Optional[int]
    rmssd_ms: Optional[float]
    rr_count: int
    zone_seconds: Dict[str, float]
    max_hr: int
    valid_sample_count: int = 0
    dropped_sample_count: int = 0

    @property
    def has_data(self) -> bool:
        return self.sample_count > 0

    @property
    def duration_text(self) -> str:
        return format_duration(self.duration_s)

    def zone_share(self) -> Dict[str, float]:
        total = sum(self.zone_seconds.values())
        if total <= 0:
            return {k: 0.0 for k in self.zone_seconds}
        return {k: v / total for k, v in self.zone_seconds.items()}


@dataclass(frozen=True)
class _Point:
    t: float
    bpm: int


def format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def rmssd(rr_intervals_ms: Sequence[float], *, min_count: int = 5) -> Optional[float]:
    """相邻 RR 间期差值的均方根。样本不足时返回 None。"""
    values = [float(v) for v in rr_intervals_ms if v and 200.0 <= float(v) <= 3000.0]
    if len(values) < min_count:
        return None
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    if not diffs:
        return None
    return math.sqrt(sum(d * d for d in diffs) / len(diffs))


def _resting_from_points(points: Sequence[_Point], *, duration_s: float) -> Optional[float]:
    """静息心率：优先取「滑动 60 秒窗口均值」的最小值，样本不够时退化为最小值。"""
    if not points:
        return None
    bpms = [p.bpm for p in points]
    if duration_s >= 60.0 and len(points) >= 10:
        best: Optional[float] = None
        left = 0
        window: List[int] = []
        running = 0
        for right, p in enumerate(points):
            window.append(p.bpm)
            running += p.bpm
            while points[right].t - points[left].t > 60.0 and left < right:
                running -= window.pop(0)
                left += 1
            if points[right].t - points[left].t >= 55.0 and len(window) >= 5:
                avg = running / len(window)
                best = avg if best is None else min(best, avg)
        if best is not None:
            return best
    return float(min(bpms))


def time_in_zones(
    samples: Iterable[Tuple[float, int]], max_hr: int
) -> Dict[str, float]:
    """按采样间隔加权统计各心率区间时长（秒）。

    每个采样点代表「从它到下一个采样点」这段时间的心率；最后一个采样点没有后续
    区间，因此不计入。这样 ``sum(结果) == duration_s``（无断连时），是个干净且
    可验证的不变量，也避免凭空捏造一段时间。
    """
    points = sorted(
        (_Point(float(t), int(b)) for t, b in samples),
        key=lambda p: p.t,
    )
    result: Dict[str, float] = {name: 0.0 for name in zone_names()}
    for i in range(len(points) - 1):
        dt = points[i + 1].t - points[i].t
        if dt <= 0:
            continue
        dt = min(dt, MAX_GAP_S)
        result[zone_for_bpm(points[i].bpm, max_hr)] += dt
    return result


def compute_stats(
    samples: Iterable[object],
    *,
    max_hr: int = 190,
    session_id: Optional[int] = None,
) -> SessionStats:
    """由 :class:`~heartapp.store.SampleRow` 或 ``HeartRateSample`` 序列计算统计。

    兼容两种对象：需要有 ``t_offset``、``bpm``、``rr_ms``/``rr_intervals_ms`` 属性。
    """
    points: List[_Point] = []
    rr_values: List[float] = []
    dropped = 0
    valid = 0

    for s in samples:
        bpm = int(getattr(s, "bpm", 0) or 0)
        t = float(getattr(s, "t_offset", 0.0) or 0.0)
        if bpm <= 0:
            dropped += 1
            continue
        valid += 1
        points.append(_Point(t, bpm))
        rr = _extract_rr(s)
        if rr is not None:
            rr_values.append(rr)

    points.sort(key=lambda p: p.t)
    bpms = [p.bpm for p in points]
    duration = (points[-1].t - points[0].t) if len(points) >= 2 else 0.0

    return SessionStats(
        session_id=session_id,
        sample_count=valid + dropped,
        valid_sample_count=valid,
        dropped_sample_count=dropped,
        duration_s=duration,
        avg_bpm=(sum(bpms) / len(bpms)) if bpms else None,
        min_bpm=min(bpms) if bpms else None,
        max_bpm=max(bpms) if bpms else None,
        resting_bpm=_resting_from_points(points, duration_s=duration),
        first_bpm=bpms[0] if bpms else None,
        last_bpm=bpms[-1] if bpms else None,
        rmssd_ms=rmssd(rr_values),
        rr_count=len(rr_values),
        zone_seconds=time_in_zones(((p.t, p.bpm) for p in points), max_hr),
        max_hr=max_hr,
    )


def _extract_rr(sample: object) -> Optional[float]:
    """从采样对象里取出一个代表性的 RR 间期。"""
    rr = getattr(sample, "rr_ms", None)
    if rr:
        return float(rr)
    intervals = getattr(sample, "rr_intervals_ms", None)
    if intervals:
        return float(intervals[0])
    return None


def rolling_average(values: Sequence[float], window: int) -> List[float]:
    """长度为 len(values) 的滑动平均，窗口不足时用已有数据的均值。"""
    if window <= 1 or not values:
        return [float(v) for v in values]
    out: List[float] = []
    running = 0.0
    buf: List[float] = []
    for v in values:
        buf.append(float(v))
        running += float(v)
        if len(buf) > window:
            running -= buf.pop(0)
        out.append(running / len(buf))
    return out


def downsample(
    samples: Sequence[object], max_points: int
) -> List[object]:
    """把长会话抽稀到 ``max_points`` 个点，用于画图。"""
    if max_points <= 0 or len(samples) <= max_points:
        return list(samples)
    step = len(samples) / float(max_points)
    picked = []
    last_index = -1
    for i in range(max_points):
        idx = min(len(samples) - 1, int(i * step))
        if idx != last_index:
            picked.append(samples[idx])
            last_index = idx
    if picked and picked[-1] is not samples[-1]:
        picked.append(samples[-1])
    return picked
