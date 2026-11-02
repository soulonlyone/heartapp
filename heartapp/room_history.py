"""房间内的临时曲线缓存；不写数据库，不发送历史。"""
from __future__ import annotations

from collections import deque
from typing import Dict, Optional


class RoomHistory:
    def __init__(self) -> None:
        self.started = 0.0
        self.points: Dict[str, deque] = {}
        self.sequences = {}

    def clear(self, now: float = 0.0) -> None:
        self.started = now
        self.points.clear()
        self.sequences.clear()

    def append(self, key: str, stamp: float, bpm: Optional[int]) -> None:
        values = self.points.setdefault(key, deque(maxlen=7201))
        if values and stamp < values[-1][0]:
            return
        if values and bpm is None and values[-1][1] is None:
            return
        values.append((stamp, bpm))
        self.prune(stamp)

    def prune(self, now: float) -> None:
        for values in self.points.values():
            while values and values[0][0] < now - 1800:
                values.popleft()

    def snapshot(self, members: list, self_id: str, now: float) -> None:
        active = {str(m.get("id")) for m in members if m.get("id") != self_id}
        for key in list(self.points):
            if key != "self" and key not in active:
                self.points.pop(key, None)
                self.sequences.pop(key, None)
        for member in members:
            key = str(member.get("id"))
            if key == self_id:
                continue
            bpm = member.get("bpm")
            valid = member.get("sharing") and type(bpm) is int and 1 <= bpm <= 300
            if not valid:
                self.append(key, now, None)
                continue
            sequence = member.get("sample_seq")
            if sequence is not None and self.sequences.get(key) == sequence:
                continue
            age = member.get("age_ms", 0)
            age = max(0, min(5000, age)) if type(age) in (int, float) else 0
            self.append(key, now - age / 1000, bpm)
            self.sequences[key] = sequence
        self.prune(now)

    def gap(self, now: float) -> None:
        for key in list(self.points):
            self.append(key, now, None)


def segments(points: list) -> list:
    """空读数和超过 5 秒的间隔都切断线段。"""
    result, current = [], []
    for stamp, value in points:
        if value is None:
            if current:
                result.append(current)
                current = []
            continue
        if current and stamp - current[-1][0] > 5:
            result.append(current)
            current = []
        current.append((stamp, value))
    if current:
        result.append(current)
    return result
