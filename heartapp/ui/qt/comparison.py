"""共享坐标轴的多人曲线与可开关图例。"""
from __future__ import annotations

import bisect
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton

from .editorial import Curve, INK, MUTED, PAPER, RED, RULE, serif
from ...metrics import format_duration
from ...room_history import segments

COLORS = (RED, "#267B78", "#526C9B", "#A77A32")


class MemberLegend(QPushButton):
    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key
        self.member = {}
        self.color = RED
        self.setCheckable(True)
        self.setChecked(True)
        self.setMinimumWidth(140)
        self.setFixedHeight(65)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击显示 / 隐藏该成员的曲线")

    def set_member(self, member: dict, color: str) -> None:
        self.member, self.color = member, color
        self.setAccessibleName("显示曲线：" + str(member.get("name", "玩家")))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.color if self.isChecked() else MUTED)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(7, 15), 3.5, 3.5)
        p.setFont(QFont("Microsoft YaHei UI", 9))
        p.setPen(QColor(INK if self.isChecked() else MUTED))
        name = str(self.member.get("name", "玩家")) + (" · 我" if self.key == "self" else "")
        p.drawText(QPointF(20, 19), p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, self.width() - 42))
        bpm = self.member.get("bpm")
        text = str(bpm) if type(bpm) is int and 1 <= bpm <= 300 else "—"
        p.setFont(serif(30))
        p.setPen(color)
        p.drawText(QPointF(20, 53), text)
        width = p.fontMetrics().horizontalAdvance(text)
        p.setFont(QFont("Microsoft YaHei UI", 8))
        p.setPen(QColor(MUTED))
        detail = "模拟" if self.member.get("simulated") else "bpm"
        if self.key == "self" and not self.member.get("sharing"):
            detail += " · 仅本机"
        p.drawText(QPointF(26 + width, 51), detail)
        p.setPen(QPen(color if self.isChecked() else QColor(RULE), 1))
        p.drawLine(0, 63, self.width() - 12, 63)


class ComparisonCurve(Curve):
    view_changed = Signal()

    def __init__(self) -> None:
        super().__init__(title="共同心率曲线")
        self.series = {}
        self._series_times = {}

    def set_series(self, series: dict, window: float) -> None:
        self.series = {key: dict(item, points=sorted(item["points"], key=lambda p: p[0])) for key, item in series.items()}
        self._series_times = {key: [t for t, _ in item["points"]] for key, item in self.series.items()}
        points = sorted((t, b) for s in self.series.values() for t, b in s["points"] if b is not None)
        self.set_points(points, axis="live", window=window)

    def values_at(self, stamp: float) -> list:
        result = []
        for key, item in self.series.items():
            index = bisect.bisect_right(self._series_times[key], stamp) - 1
            choices = [i for i in (index, index + 1) if 0 <= i < len(item["points"])]
            if choices:
                nearest = min(choices, key=lambda i: abs(item["points"][i][0] - stamp))
                t, bpm = item["points"][nearest]
                if bpm is not None and abs(t - stamp) <= 1.5:
                    result.append((item, bpm))
        return result

    def wheelEvent(self, event) -> None:
        super().wheelEvent(event)
        self.view_changed.emit()

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        if self._drag:
            self.view_changed.emit()

    def reset_view(self) -> None:
        super().reset_view()
        self.view_changed.emit()

    def summary(self) -> str:
        left, right = self._range()
        texts = []
        for item in self.series.values():
            values = [b for t, b in item["points"] if left <= t <= right and b is not None]
            if values:
                texts.append(f"{item['name']}  {sum(values) / len(values):.0f} / {max(values):.0f}")
        return "窗口采样均值 / 峰值：  " + "    ·    ".join(texts) if texts else "等待共享读数，曲线从加入房间后开始积累。"

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot, (left, right) = self._plot(), self._range()
        visible = [b for item in self.series.values() for t, b in item["points"]
                   if left <= t <= right and b is not None]
        low = max(20, math.floor((min(visible or [60]) - 12) / 20) * 20)
        high = max(low + 60, math.ceil((max(visible or [110]) + 12) / 20) * 20)
        def px(t):
            return plot.left() + (t - left) / (right - left) * plot.width()
        def py(b):
            return plot.bottom() - (b - low) / (high - low) * plot.height()
        p.setFont(QFont("Segoe UI", 9))
        p.setPen(QColor(MUTED))
        p.drawText(QPointF(0, 16), "bpm")
        for value in range(int(low), int(high) + 1, 20):
            y = py(value)
            p.setPen(QPen(QColor(RULE), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(0, y - 9, 30, 18), Qt.AlignmentFlag.AlignRight, str(value))
        for i in range(7):
            t = left + (right - left) * i / 6
            x = px(t)
            p.setPen(QPen(QColor(RULE), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            p.setPen(QColor(MUTED))
            text = "现在" if abs(t) < .5 else "−" + format_duration(abs(t))
            p.drawText(QRectF(x - 38, plot.bottom() + 10, 76, 20), Qt.AlignmentFlag.AlignCenter, text)
        p.save()
        p.setClipRect(plot.adjusted(-1, -1, 1, 1))
        for item in self.series.values():
            color = QColor(item["color"])
            p.setPen(QPen(color, 1.8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for segment in segments(item["points"]):
                buckets = {}
                for t, b in segment:
                    if left - 5 <= t <= right + 5:
                        buckets.setdefault(int(px(t)), []).append((t, b))
                chosen = []
                for bucket in buckets.values():
                    chosen.extend(sorted({bucket[0], bucket[-1], min(bucket, key=lambda v: v[1]), max(bucket, key=lambda v: v[1])}))
                if not chosen:
                    continue
                path = QPainterPath(QPointF(px(chosen[0][0]), py(chosen[0][1])))
                for t, b in chosen[1:]:
                    path.lineTo(px(t), py(b))
                p.drawPath(path)
                if len(chosen) == 1:
                    p.setBrush(color)
                    p.drawEllipse(QPointF(px(chosen[0][0]), py(chosen[0][1])), 2, 2)
                    p.setBrush(Qt.BrushStyle.NoBrush)
        p.restore()
        if not visible:
            p.setPen(QColor(MUTED))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "等待房间内的心率变化")
        if self._hover:
            t = left + (self._hover.x() - plot.left()) / plot.width() * (right - left)
            p.setPen(QPen(QColor(MUTED), 1))
            p.drawLine(QPointF(px(t), plot.top()), QPointF(px(t), plot.bottom()))
            rows = self.values_at(t)
            box = QRectF(min(max(plot.left(), px(t) + 12), plot.right() - 240), plot.top() + 8, 240, 28 + 23 * len(rows))
            p.fillRect(box, QColor(PAPER))
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.setPen(QColor(MUTED))
            p.drawText(QPointF(box.left() + 12, box.top() + 18), "−" + format_duration(abs(t)))
            for i, (item, bpm) in enumerate(rows):
                p.setPen(QColor(item["color"]))
                name = p.fontMetrics().elidedText(item["name"], Qt.TextElideMode.ElideRight, 140)
                p.drawText(QRectF(box.left() + 12, box.top() + 29 + i * 23, 150, 20), name)
                p.drawText(QRectF(box.right() - 72, box.top() + 29 + i * 23, 60, 20), Qt.AlignmentFlag.AlignRight, f"{bpm} bpm")
