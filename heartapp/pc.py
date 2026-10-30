"""PC 会话的参考基线；只描述心率变化，不推断情绪。"""
from __future__ import annotations

from statistics import median
from typing import Iterable, Optional


def session_baseline(samples: Iterable[object]) -> Optional[float]:
    """开始 30 秒内至少 20 条、跨度 25 秒且无长断流的读数中位数。"""
    points = sorted((float(s.t_offset), int(s.bpm)) for s in samples
                    if 0 <= s.t_offset <= 30 and s.bpm > 0
                    and getattr(s, "contact", None) is not False)
    if len(points) < 20 or points[-1][0] - points[0][0] < 25:
        return None
    if any(b[0] - a[0] > 5 for a, b in zip(points, points[1:])):
        return None
    return float(median(b for _, b in points))
