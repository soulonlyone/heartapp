"""Qt 版控件：白卡、分段控件、极细表盘、干净图表。

视觉原则见 ``theme.py``：近中性底 + 纯白卡 + 发丝描边 + 极轻投影 + 唯一强调色。

技术要点：Qt 的子控件默认不绘制背景（透明），所以文字/按钮会自然叠在父控件
画出的卡片之上一层，不需要手工对齐颜色。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...metrics import ZONE_DEFINITIONS, format_duration, zone_for_bpm
from . import theme as T
from .theme import (  # noqa: F401  (re-export 给界面层)
    ACCENT,
    ACCENT_SOFT,
    BACKDROP_STOPS,
    BORDER,
    BORDER_STRONG,
    DIVIDER,
    FAINT,
    GLASS_ALPHA,
    INK,
    INK_2,
    INK_3,
    INK_4,
    INK_SOLID,
    INK_SOLID_HOVER,
    MUTED,
    ON_PRIMARY,
    ON_PRIMARY_CONTAINER,
    ON_SURFACE,
    ON_SURFACE_VARIANT,
    OUTLINE,
    PRIMARY,
    PRIMARY_CONTAINER,
    RADIUS_CARD,
    RADIUS_PANEL,
    RADIUS_SMALL,
    SECONDARY_CONTAINER,
    SPACE,
    STATE_CONNECTED,
    STATE_CONNECTING,
    STATE_ERROR,
    STATE_IDLE,
    STATE_RECORDING,
    SUCCESS,
    SURFACE,
    SURFACE_SUNK,
    TEXT,
    WARNING,
    ZONE_COLORS,
    ZONE_SOFT,
    blur_region,
    color,
    darken,
    font,
    font_px,
    lighten,
    mix,
    mono_font,
    paint_card,
    paint_shadow,
    rounded_path,
    spaced_font,
    ui_family,
)

ZONE_ORDER: List[str] = ["静息"] + [name for name, _, _ in ZONE_DEFINITIONS]

#: 卡片为投影预留的内边距
SHADOW_INSET = 6

_backdrop_pixmap: Optional[QPixmap] = None


def set_backdrop(pixmap: Optional[QPixmap]) -> None:
    global _backdrop_pixmap
    _backdrop_pixmap = pixmap


def backdrop_pixmap() -> Optional[QPixmap]:
    return _backdrop_pixmap


def window_rect(widget: QWidget) -> QRect:
    try:
        top_left = widget.mapTo(widget.window(), QPoint(0, 0))
        return QRect(top_left, widget.size())
    except Exception:  # noqa: BLE001
        return QRect(0, 0, widget.width(), widget.height())


def color_for_bpm(bpm: Optional[float], max_hr: int) -> QColor:
    if not bpm or bpm <= 0:
        return color(INK_3)
    return color(ZONE_COLORS.get(zone_for_bpm(bpm, max_hr), INK_2))


def zone_label_for_bpm(bpm: Optional[float], max_hr: int) -> str:
    if not bpm or bpm <= 0:
        return "无读数"
    return zone_for_bpm(bpm, max_hr)


# ---------------------------------------------------------------------------
# 卡片
# ---------------------------------------------------------------------------


class Card(QWidget):
    """纯白圆角卡片：发丝描边 + 极轻投影。内容通过 ``layout()`` 放进来。

    ``tone`` 大于 0 时会往"下沉底色"靠，用来做卡片内的次级容器。
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        radius: int = RADIUS_CARD,
        padding: int = 18,
        tone: float = 0.0,
        shadow: bool = True,
        spacing: int = 0,
        inset: int = SHADOW_INSET,
    ) -> None:
        super().__init__(parent)
        self._radius = float(radius)
        self._tone = max(0.0, min(1.0, tone))
        self._shadow = shadow
        self._inset = inset
        box = QVBoxLayout(self)
        box.setContentsMargins(inset + padding, inset + padding,
                               inset + padding, inset + padding)
        box.setSpacing(spacing)

    @property
    def fill(self) -> QColor:
        return mix(SURFACE, SURFACE_SUNK, self._tone)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(
            self._inset, self._inset, -self._inset, -self._inset
        )
        if self._shadow:
            paint_shadow(painter, rect, self._radius, spread=10.0)
        paint_card(painter, rect, self._radius, fill=self.fill.name())


#: 兼容旧名字
GlassPanel = Card
TonalPanel = Card


class Divider(QWidget):
    """1px 分隔线。"""

    def __init__(self, parent: Optional[QWidget] = None, *, inset: int = 0) -> None:
        super().__init__(parent)
        self._inset = inset
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(
            QRectF(self._inset, 0, self.width() - self._inset * 2, 1), color(DIVIDER)
        )


# ---------------------------------------------------------------------------
# 按钮
# ---------------------------------------------------------------------------

_VARIANT_ALIASES = {
    "Primary.TButton": "primary",
    "Stop.TButton": "danger",
    "Accent.TButton": "primary",
    "filled": "primary",
    "tonal": "secondary",
    "outlined": "secondary",
    "text": "text",
}


class M3Button(QPushButton):
    """胶囊按钮。变体：primary（近黑实心）/ secondary（白底描边）/ text / danger。"""

    def __init__(
        self,
        text: str = "",
        parent: Optional[QWidget] = None,
        *,
        variant: str = "primary",
        height: int = 38,
        min_width: int = 0,
        font_role: str = "bodyStrong",
    ) -> None:
        super().__init__(text, parent)
        self._variant = _VARIANT_ALIASES.get(variant, variant)
        self._height = height
        self._role = font_role
        self.setFixedHeight(height)
        if min_width:
            self.setMinimumWidth(min_width)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(font(font_role))
        self._apply()

    def set_variant(self, variant: str) -> None:
        variant = _VARIANT_ALIASES.get(variant, variant)
        if variant != self._variant:
            self._variant = variant
            self._apply()

    def set_text(self, text: str) -> None:
        self.setText(text)

    def _apply(self) -> None:
        radius = self._height // 2
        family = ui_family()
        size = T.TYPE_SCALE.get(self._role, (10, None))[0]

        if self._variant == "primary":
            bg, fg, border = INK_SOLID, "#FFFFFF", INK_SOLID
            hover, press = INK_SOLID_HOVER, "#3A3D45"
        elif self._variant == "danger":
            bg, fg, border = ACCENT, "#FFFFFF", ACCENT
            hover, press = T.ACCENT_PRESSED, "#8E0E32"
        elif self._variant == "secondary":
            bg, fg, border = SURFACE, INK, BORDER_STRONG
            hover, press = "#F6F6F8", "#EDEDF1"
        else:  # text
            bg, fg, border = "transparent", INK_2, "transparent"
            hover, press = "#F1F1F4", "#E9E9EE"

        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: {radius}px;
                padding: 0 20px;
                font-family: "{family}";
                font-size: {size}pt;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:pressed {{ background: {press}; }}
            QPushButton:disabled {{
                background: #F4F4F6;
                color: {INK_4};
                border-color: {BORDER};
            }}
        """)


class M3Chip(QPushButton):
    """筛选芯片：选中时整颗变近黑，比淡彩更有"高级感"。"""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None,
                 *, height: int = 34) -> None:
        super().__init__(text, parent)
        self._base_text = text
        self.setCheckable(True)
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(font("label"))
        radius = height // 2
        family = ui_family()
        self.setStyleSheet(f"""
            QPushButton {{
                background: {SURFACE};
                color: {INK_2};
                border: 1px solid {BORDER_STRONG};
                border-radius: {radius}px;
                padding: 0 16px;
                font-family: "{family}";
                font-size: 9pt;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: #F6F6F8; color: {INK}; }}
            QPushButton:checked {{
                background: #EFEFF3;
                color: {INK};
                border-color: #DCDCE3;
            }}
        """)
        self.toggled.connect(self._sync_check_text)

    def _sync_check_text(self, checked: bool) -> None:
        super().setText(("✓  " + self._base_text) if checked else self._base_text)


class StatusPill(QLabel):
    """状态胶囊：浅色底 + 同色小圆点 + 同色文字，克制不刺眼。"""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFont(font("label"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state(text, STATE_IDLE)

    def set_state(self, text: str, base: str) -> None:
        c = QColor(base)
        soft = mix(base, "#FFFFFF", 0.90)
        self.setText(text)
        self.setStyleSheet(
            f"QLabel {{ background: {soft.name()}; color: {c.name()};"
            f" border-radius: 12px; padding: 5px 12px; }}"
        )


#: 兼容旧名字
Pill = StatusPill


# ---------------------------------------------------------------------------
# 分段控件（iOS 风格）
# ---------------------------------------------------------------------------


class SegmentedTabs(QWidget):
    """两段式分段控件：灰色轨道 + 白色滑块。"""

    changed = Signal(int)

    def __init__(self, labels: Sequence[str], parent: Optional[QWidget] = None,
                 *, height: int = 36) -> None:
        super().__init__(parent)
        self._labels = list(labels)
        self._index = 0
        self._height = height
        self._hover = -1
        self.setFixedHeight(height + 8)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        metrics = QFontMetrics(font("bodyStrong"))
        self._seg_w = max(96, max(metrics.horizontalAdvance(s) for s in self._labels) + 44)
        self._track_h = height
        self.setFixedWidth(self._seg_w * len(self._labels) + 8)

    # -- 对外 -------------------------------------------------------------

    def index(self) -> int:
        return self._index

    def select(self, index: int) -> None:
        index = max(0, min(len(self._labels) - 1, int(index)))
        if index != self._index:
            self._index = index
            self.update()
            self.changed.emit(index)

    # -- 交互 -------------------------------------------------------------

    def _hit(self, x: float) -> int:
        idx = int((x - 4) // self._seg_w)
        return max(0, min(len(self._labels) - 1, idx))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.select(self._hit(event.position().x()))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        idx = self._hit(event.position().x())
        if idx != self._hover:
            self._hover = idx
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = -1
        self.update()

    # -- 绘制 -------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        top = (self.height() - self._track_h) / 2.0
        track = QRectF(4, top, self._seg_w * len(self._labels), self._track_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color("#EFEFF3"))
        painter.drawRoundedRect(track, self._track_h / 2.0, self._track_h / 2.0)

        thumb = QRectF(
            4 + self._index * self._seg_w + 2, top + 2,
            self._seg_w - 4, self._track_h - 4,
        )
        # 滑块投影
        for i in (3, 2, 1):
            grow = i * 1.2
            painter.setBrush(color("#1A1B20", 0.012 * i))
            painter.drawRoundedRect(thumb.adjusted(-grow * 0.3, grow * 0.2,
                                                   grow * 0.3, grow * 0.5),
                                    thumb.height() / 2 + grow * 0.3,
                                    thumb.height() / 2 + grow * 0.3)
        painter.setBrush(color(SURFACE))
        painter.setPen(QPen(color("#E4E4EA"), 0.8))
        painter.drawRoundedRect(thumb, thumb.height() / 2, thumb.height() / 2)

        font_normal = font("bodyStrong")
        for idx, label in enumerate(self._labels):
            seg = QRectF(4 + idx * self._seg_w, top, self._seg_w, self._track_h)
            active = idx == self._index
            painter.setFont(font_normal)
            painter.setPen(color(INK if active else (INK_2 if idx == self._hover else INK_3)))
            painter.drawText(seg, Qt.AlignmentFlag.AlignCenter, label)


#: 兼容旧名字
M3TabBar = SegmentedTabs


# ---------------------------------------------------------------------------
# 表盘
# ---------------------------------------------------------------------------


class BpmGauge(QWidget):
    """270° 细环表盘。数字用墨色而不是区间色 —— 更克制，也更衬得出彩色标签。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.bpm: Optional[int] = None
        self.max_hr = 190
        self.contact: Optional[bool] = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(240, 210)

    def set_state(self, *, bpm: Optional[int], max_hr: int,
                  contact: Optional[bool] = None) -> None:
        self.bpm = bpm
        self.max_hr = max_hr or 190
        self.contact = contact
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w, h = self.width(), self.height()
        margin = max(14.0, min(w, h) * 0.045)
        size = max(150.0, min(w, h) - margin * 2)
        thickness = max(9.0, size * 0.056)
        cx, cy = w / 2.0, h / 2.0 + size * 0.01
        radius = size / 2.0 - thickness / 2.0
        arc = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        start, sweep = 225.0, -270.0

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color("#EDEDF1"), thickness, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        painter.drawArc(arc, int(start * 16), int(sweep * 16))

        shown = self.bpm if (self.bpm and self.bpm > 0) else None
        tint = color_for_bpm(shown, self.max_hr)
        lo, hi = 40.0, max(80.0, self.max_hr * 1.05)
        ratio = 0.0 if shown is None else max(0.0, min(1.0, (shown - lo) / (hi - lo)))

        if ratio > 0.002:
            painter.setPen(QPen(tint, thickness, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap))
            painter.drawArc(arc, int(start * 16), int(sweep * ratio * 16))

        # 大数字（墨色）+ 单位
        painter.setPen(color(INK if shown is not None else INK_4))
        painter.setFont(font_px(max(40, int(size * 0.30)), QFont.Weight.DemiBold))
        painter.drawText(
            QRectF(cx - size / 2, cy - size * 0.30, size, size * 0.36),
            Qt.AlignmentFlag.AlignCenter,
            str(shown) if shown is not None else "--",
        )
        painter.setPen(color(INK_3))
        painter.setFont(font("caption"))
        painter.drawText(
            QRectF(cx - size / 2, cy + size * 0.09, size, size * 0.12),
            Qt.AlignmentFlag.AlignCenter, "bpm",
        )

        # 区间标签：浅底 + 同色文字的小胶囊
        label = zone_label_for_bpm(shown, self.max_hr)
        pill_font = font("label")
        fm = QFontMetrics(pill_font)
        pill_w = fm.horizontalAdvance(label) + 26
        pill_h = 22.0
        pill = QRectF(cx - pill_w / 2, cy + size * 0.22, pill_w, pill_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color(ZONE_SOFT.get(label, "#F1F2F5")))
        painter.drawRoundedRect(pill, pill_h / 2, pill_h / 2)
        painter.setPen(tint if shown is not None else color(INK_3))
        painter.setFont(pill_font)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)

        # 贴合状态
        if self.contact is not None:
            text = "已贴合" if self.contact else "未贴合"
            dot = SUCCESS if self.contact else WARNING
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color(dot))
            base_y = h - 16
            painter.drawEllipse(QPointF(cx - 24, base_y), 3.0, 3.0)
            painter.setPen(color(INK_3))
            painter.setFont(font("caption"))
            painter.drawText(
                QRectF(cx - 16, base_y - 9, 80, 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text,
            )


# ---------------------------------------------------------------------------
# 趋势图
# ---------------------------------------------------------------------------


class TrendChart(QWidget):
    """心率折线：细网格 + 强调色曲线 + 渐隐填充。不画色带，保持干净。"""

    def __init__(self, parent: Optional[QWidget] = None, *, title: str = "") -> None:
        super().__init__(parent)
        self.points: List[Tuple[float, float]] = []
        self.window: Optional[float] = None
        self.axis = "live"
        self.max_hr: Optional[int] = None
        self.title = title
        self.empty_text = "暂无数据"
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_points(self, points: Sequence[Tuple[float, float]], *,
                   window: Optional[float] = None, axis: str = "live",
                   max_hr: Optional[int] = None, title: Optional[str] = None,
                   empty_text: Optional[str] = None) -> None:
        self.points = list(points)
        self.window = window
        self.axis = axis
        self.max_hr = max_hr
        if title is not None:
            self.title = title
        if empty_text is not None:
            self.empty_text = empty_text
        self.update()

    def clear(self) -> None:
        self.points = []
        self.update()

    def _x_range(self) -> Tuple[float, float]:
        if not self.points:
            if self.axis == "live":
                return (-(self.window or 300.0), 0.0)
            return (0.0, 60.0)
        xs = [p[0] for p in self.points]
        if self.axis == "live":
            return (0.0 - (self.window or max(60.0, -min(xs))), 0.0)
        left = min(0.0, min(xs))
        right = max(xs)
        if right - left < 10.0:
            right = left + 10.0
        return (left, right)

    def _y_range(self) -> Tuple[float, float]:
        if not self.points:
            return (50.0, 150.0)
        ys = [p[1] for p in self.points]
        lo, hi = min(ys), max(ys)
        pad = max(8.0, (hi - lo) * 0.22)
        lo, hi = lo - pad, hi + pad
        if hi - lo < 40.0:
            mid = (lo + hi) / 2.0
            lo, hi = mid - 20.0, mid + 20.0
        step = 20.0
        lo = max(30.0, int(lo / step) * step)
        hi = (int(hi / step) + 1) * step
        return (float(lo), float(hi))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        pad_l, pad_r, pad_t, pad_b = 44, 16, 30, 24
        w, h = self.width(), self.height()
        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b
        if plot_w <= 10 or plot_h <= 10:
            return

        x_lo, x_hi = self._x_range()
        y_lo, y_hi = self._y_range()
        if x_hi <= x_lo:
            x_hi = x_lo + 1.0
        if y_hi <= y_lo:
            y_hi = y_lo + 1.0

        def px(x: float) -> float:
            return pad_l + (x - x_lo) / (x_hi - x_lo) * plot_w

        def py(y: float) -> float:
            return pad_t + (y_hi - y) / (y_hi - y_lo) * plot_h

        grid = color("#EDEDF1")
        painter.setPen(QPen(grid, 1.0))
        step = 20.0
        value = int(y_lo / step) * step
        while value <= y_hi + step * 0.5:
            if value >= y_lo:
                yy = round(py(value)) + 0.5
                painter.setPen(QPen(grid, 1.0))
                painter.drawLine(QPointF(pad_l, yy), QPointF(pad_l + plot_w, yy))
                painter.setPen(color(INK_4))
                painter.setFont(mono_font(8))
                painter.drawText(QRectF(pad_l - 40, yy - 8, 34, 16),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                 str(int(value)))
            value += step

        span = x_hi - x_lo
        tick = span / 6.0
        for candidate in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900):
            if span / candidate <= 8:
                tick = float(candidate)
                break
        t = int(x_lo / tick) * tick
        while t <= x_hi + 1e-9:
            if t >= x_lo - 1e-9:
                xx = round(px(t)) + 0.5
                painter.setPen(QPen(grid, 1.0))
                painter.drawLine(QPointF(xx, pad_t), QPointF(xx, pad_t + plot_h))
                label = format_duration(abs(t))
                if self.axis == "live" and t < 0:
                    label = "-" + label
                painter.setPen(color(INK_4))
                painter.setFont(mono_font(8))
                painter.drawText(QRectF(xx - 28, pad_t + plot_h + 4, 56, 16),
                                 Qt.AlignmentFlag.AlignCenter, label)
            t += tick

        if self.title:
            painter.setPen(color(INK_2))
            painter.setFont(font("section"))
            painter.drawText(QRectF(pad_l, 4, plot_w, 20),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             self.title)

        if not self.points:
            painter.setPen(color(INK_4))
            painter.setFont(font("body"))
            painter.drawText(QRectF(pad_l, pad_t, plot_w, plot_h),
                             Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return

        pts = [QPointF(px(x), py(max(y_lo, min(y_hi, y)))) for x, y in self.points]

        if len(pts) >= 2:
            # 渐变填充：从强调色 14% 淡到全透明
            area = QPainterPath()
            area.moveTo(pts[0].x(), pad_t + plot_h)
            for point in pts:
                area.lineTo(point)
            area.lineTo(pts[-1].x(), pad_t + plot_h)
            area.closeSubpath()
            gradient = QLinearGradient(0, pad_t, 0, pad_t + plot_h)
            top_color = QColor(ACCENT)
            top_color.setAlphaF(0.16)
            bottom_color = QColor(ACCENT)
            bottom_color.setAlphaF(0.0)
            gradient.setColorAt(0.0, top_color)
            gradient.setColorAt(1.0, bottom_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(gradient))
            painter.drawPath(area)

            line = QPainterPath(pts[0])
            for point in pts[1:]:
                line.lineTo(point)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(ACCENT), 1.8, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(line)

        if len(self.points) >= 5:
            avg = sum(p[1] for p in self.points) / len(self.points)
            if y_lo <= avg <= y_hi:
                yy = round(py(avg)) + 0.5
                painter.setPen(QPen(color("#D3D3DB"), 1.0, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(pad_l, yy), QPointF(pad_l + plot_w, yy))
                painter.setPen(color(INK_4))
                painter.setFont(font("micro"))
                painter.drawText(QRectF(pad_l + 6, yy - 16, 90, 14),
                                 Qt.AlignmentFlag.AlignLeft, f"均值 {avg:.0f}")

        last = pts[-1]
        painter.setBrush(color("#FFFFFF"))
        painter.setPen(QPen(QColor(ACCENT), 2.0))
        painter.drawEllipse(last, 3.6, 3.6)
        painter.setPen(color(ACCENT))
        painter.setFont(font("label"))
        near_right = last.x() > pad_l + plot_w - 60
        painter.drawText(
            QRectF(last.x() - (64 if near_right else -10), last.y() - 22, 58, 16),
            Qt.AlignmentFlag.AlignRight if near_right else Qt.AlignmentFlag.AlignLeft,
            f"{self.points[-1][1]:.0f}",
        )


# ---------------------------------------------------------------------------
# 心率区间条
# ---------------------------------------------------------------------------


class ZoneBar(QWidget):
    """区间分布：一条细色条 + 三列极简图例（色块 + 名称 + 时长）。"""

    ROW_H = 20

    def __init__(self, parent: Optional[QWidget] = None,
                 *, title: str = "心率区间") -> None:
        super().__init__(parent)
        self.zone_seconds: Dict[str, float] = {}
        self.max_hr = 190
        self.title = title
        self.setMinimumHeight(96)

    def set_zones(self, zone_seconds: Dict[str, float], *, max_hr: int = 190,
                  title: Optional[str] = None) -> None:
        self.zone_seconds = dict(zone_seconds or {})
        self.max_hr = max_hr or 190
        if title is not None:
            self.title = title
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w, h = self.width(), self.height()
        x0, x1 = 0.0, float(w)
        y = 0.0
        total = sum(self.zone_seconds.values())

        painter.setPen(color(INK_2))
        painter.setFont(font("section"))
        painter.drawText(QRectF(x0, y, x1 - x0, 18),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.title)
        if total > 0:
            painter.setPen(color(INK_4))
            painter.setFont(font("micro"))
            painter.drawText(QRectF(x0, y, x1 - x0, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"共 {format_duration(total)}")
        y += 22

        bar_h = 8.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color("#EFEFF3"))
        painter.drawRoundedRect(QRectF(x0, y, x1 - x0, bar_h), bar_h / 2, bar_h / 2)

        if total <= 0:
            painter.setPen(color(INK_4))
            painter.setFont(font("caption"))
            painter.drawText(QRectF(x0, y, x1 - x0, bar_h),
                             Qt.AlignmentFlag.AlignCenter, "暂无数据")
            return

        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(x0, y, x1 - x0, bar_h), bar_h / 2, bar_h / 2)
        painter.setClipPath(clip)
        cursor = x0
        for name in ZONE_ORDER:
            seconds = self.zone_seconds.get(name, 0.0)
            if seconds <= 0:
                continue
            seg = (seconds / total) * (x1 - x0)
            painter.fillRect(QRectF(cursor, y, seg, bar_h),
                             color(ZONE_COLORS.get(name, INK_3)))
            cursor += seg
        painter.restore()

        y += bar_h + 14
        cols, gap = 3, 16.0
        col_w = max(84.0, (x1 - x0 - gap * (cols - 1)) / cols)
        for idx, name in enumerate(ZONE_ORDER):
            col, row = idx % cols, idx // cols
            bx = x0 + col * (col_w + gap)
            row_y = y + row * self.ROW_H + self.ROW_H / 2.0
            if row_y > h - 4:
                break
            seconds = self.zone_seconds.get(name, 0.0)
            swatch = QRectF(bx, row_y - 3.5, 7, 7)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color(ZONE_COLORS.get(name, INK_3) if seconds > 0
                                   else "#DCDCE3"))
            painter.drawEllipse(swatch)

            painter.setFont(font("caption"))
            painter.setPen(color(INK_2 if seconds > 0 else INK_4))
            painter.drawText(QRectF(bx + 14, row_y - 8, 74, 16),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             name)
            # 时长紧跟名称，不要右对齐到列边（那样会和名称脱节）
            time_x = bx + min(max(0.0, col_w - 62), 88)
            painter.setFont(mono_font(8))
            painter.setPen(color(INK_3 if seconds > 0 else INK_4))
            painter.drawText(QRectF(time_x, row_y - 8, 58, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             format_duration(seconds))


# ---------------------------------------------------------------------------
# 文本助手
# ---------------------------------------------------------------------------


def section_label(text: str, parent: Optional[QWidget] = None) -> QLabel:
    label = QLabel(text, parent)
    label.setFont(font("section"))
    label.setStyleSheet(f"color: {INK_2}; background: transparent;")
    return label


def muted_label(text: str = "", parent: Optional[QWidget] = None,
                role: str = "caption") -> QLabel:
    label = QLabel(text, parent)
    label.setFont(font(role))
    label.setStyleSheet(f"color: {INK_3}; background: transparent;")
    return label


def title_label(text: str, parent: Optional[QWidget] = None) -> QLabel:
    label = QLabel(text, parent)
    label.setFont(spaced_font("appTitle", -0.15))
    label.setStyleSheet(f"color: {INK}; background: transparent;")
    return label
