"""D 版数字海报：单一暖白画布、衬线数字、朱红数据曲线。"""
from __future__ import annotations

import bisect
import ctypes
import math
import sys
from typing import Callable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QRegion,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QListView,
    QPushButton,
    QStyle,
    QStyleFactory,
    QStyledItemDelegate,
    QWidget,
)

from ...metrics import format_duration

PAPER = "#FBF9F4"
INK = "#191916"
MUTED = "#838079"
RULE = "#E4E0D8"
RED = "#F13D2B"
Point = Tuple[float, float]

_palette_applied = False


def ensure_light_palette(app: Optional[QApplication] = None) -> None:
    """把应用调色板固定成「暖白纸面」。

    D 版是**刻意固定的浅色设计**：背景在 QSS 里写死成 PAPER。但如果跟随系统主题，
    系统处于深色模式时 Qt 的默认文字色是白色，于是所有「没有显式设文字色」的控件
    （最典型是 QTableWidget 的单元格）就变成白字白底，整个历史列表消失。

    这里用 Fusion 风格 + 显式浅色调色板把底色和文字色都钉死，
    以后新增控件即使忘了写 color，也不会再出现白字白底。
    幂等：重复调用只生效一次。
    """
    global _palette_applied
    if _palette_applied:
        return
    app = app or QApplication.instance()
    if app is None:
        return

    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        app.setStyle(fusion)

    pal = app.palette()
    for role, value in (
        (QPalette.ColorRole.Window, PAPER),
        (QPalette.ColorRole.WindowText, INK),
        (QPalette.ColorRole.Base, PAPER),
        (QPalette.ColorRole.AlternateBase, "#F6F2EA"),
        (QPalette.ColorRole.Text, INK),
        (QPalette.ColorRole.Button, PAPER),
        (QPalette.ColorRole.ButtonText, INK),
        (QPalette.ColorRole.BrightText, RED),
        (QPalette.ColorRole.Highlight, RED),
        (QPalette.ColorRole.HighlightedText, "#FFFFFF"),
        (QPalette.ColorRole.ToolTipBase, PAPER),
        (QPalette.ColorRole.ToolTipText, INK),
        (QPalette.ColorRole.PlaceholderText, MUTED),
        (QPalette.ColorRole.Link, RED),
    ):
        pal.setColor(role, QColor(value))
    app.setPalette(pal)
    _palette_applied = True


def serif(size: int) -> QFont:
    available = set(QFontDatabase.families())
    family = next((f for f in ("Bodoni MT", "Georgia", "Times New Roman") if f in available), "serif")
    font = QFont(family)
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.Normal)
    return font


def label(text: str, size: int = 13, muted: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setStyleSheet(f"color: {MUTED if muted else INK}; background: transparent;")
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(size)
    widget.setFont(font)
    return widget


class Button(QPushButton):
    def __init__(self, text: str, *, primary: bool = False) -> None:
        super().__init__(text)
        self.primary = primary
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)
        self.set_variant("primary" if primary else "text")

    def set_variant(self, variant: str) -> None:
        self.primary = variant in ("danger", "primary", "filled")
        self.setStyleSheet(f"""
            QPushButton {{ color: {'white' if self.primary else INK};
                background: {RED if self.primary else 'transparent'};
                border: {'none' if self.primary else '1px solid ' + RULE};
                border-radius: 18px; padding: 7px 18px; font-size: 13px; }}
            QPushButton:hover {{ background: {'#D93223' if self.primary else '#F0ECE4'}; }}
            QPushButton:disabled {{ color: #AAA69E; background: #F0EDE7; }}
        """)

    def set_text(self, text: str) -> None:
        self.setText(text)


class MenuItem(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver))
        rect = QRectF(option.rect).adjusted(4, 2, -4, -2)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#F8EAE4"))
            painter.drawRoundedRect(rect, 8, 8)
        painter.setFont(option.font)
        painter.setPen(QColor(INK))
        painter.drawText(rect.adjusted(12, 0, -26, 0), Qt.AlignmentFlag.AlignVCenter, str(index.data()))
        if selected:
            painter.setBrush(QColor(RED))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(rect.right() - 15, rect.center().y()), 3, 3)
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(38)
        return size


class CapsuleCombo(QComboBox):
    """胶囊选择器，自绘细箭头，弹出菜单使用独立的圆角行。"""
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(36)
        self.setMinimumWidth(154)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(QFont("Microsoft YaHei UI", 10))
        view = QListView()
        view.setMouseTracking(True)
        view.setItemDelegate(MenuItem(view))
        view.setStyleSheet(
            f"QListView {{ background: {PAPER}; border: 1px solid {RULE}; "
            "border-radius: 12px; padding: 5px; outline: none; }"
        )
        self.setView(view)
        # 必须在首次显示、创建原生窗口之前启用透明；显示后设置在 Windows 上不可靠。
        container = view.window()
        container.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        container.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        container.setObjectName("capsulePopup")
        container.setStyleSheet("#capsulePopup { background: transparent; border: none; }")
        view.setAutoFillBackground(False)
        view.viewport().setAutoFillBackground(False)
        container.installEventFilter(self)
        self._open = False

    def eventFilter(self, watched, event) -> bool:
        if watched is self.view().window() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            # QSS 圆角不会裁掉窗口及子控件，实际窗口也需要圆角轮廓，防止底层矩形露黑角。
            outline = QPainterPath()
            outline.addRoundedRect(QRectF(watched.rect()), 12, 12)
            watched.setMask(QRegion(outline.toFillPolygon().toPolygon()))
        return super().eventFilter(watched, event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(RED if self._open else RULE), 1))
        painter.setBrush(QColor("#F4F0E9" if self.underMouse() else PAPER))
        painter.drawRoundedRect(QRectF(.5, .5, self.width() - 1, 35), 18, 18)
        painter.setFont(self.font())
        painter.setPen(QColor(INK))
        text = painter.fontMetrics().elidedText(self.currentText(), Qt.TextElideMode.ElideRight, self.width() - 52)
        painter.drawText(QRectF(17, 0, self.width() - 52, 36), Qt.AlignmentFlag.AlignVCenter, text)
        x, y = self.width() - 23, 18
        path = QPainterPath(QPointF(x - 4, y + (2 if self._open else -2)))
        path.lineTo(x, y + (-2 if self._open else 2))
        path.lineTo(x + 4, y + (2 if self._open else -2))
        painter.setPen(QPen(QColor(MUTED), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def showPopup(self) -> None:
        self.view().setMinimumWidth(max(190, self.width()))
        self._open = True
        super().showPopup()
        self.update()

    def hidePopup(self) -> None:
        self._open = False
        super().hidePopup()
        self.update()


class BrandMark(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(54, 32)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(RED))
        painter.drawEllipse(QPointF(7, 17), 5, 5)
        path = QPainterPath(QPointF(23, 17))
        for x, y in ((29, 17), (32, 8), (36, 25), (40, 14), (43, 17), (51, 17)):
            path.lineTo(x, y)
        painter.setPen(QPen(QColor(RED), 1.3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)


class Navigation(QWidget):
    changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        from PySide6.QtWidgets import QHBoxLayout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        self.buttons = []
        self.index = 0
        for index, text in enumerate(("实时", "回看", "小窗")):
            button = QPushButton(text)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedSize(52, 36)
            button.clicked.connect(lambda checked=False, i=index: self.select(i))
            layout.addWidget(button)
            self.buttons.append(button)
        self.select(0)

    def select(self, index: int) -> None:
        self.index = index
        for i, button in enumerate(self.buttons):
            button.setStyleSheet(f"background: transparent; color: {INK}; font-size: 14px;"
                                f"border: none; border-bottom: 2px solid {RED if i == index else 'transparent'};")
        self.changed.emit(index)


class Reading(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.bpm = None
        self.setMinimumSize(320, 205)

    def set_state(self, *, bpm: Optional[int] = None, **kwargs) -> None:
        self.bpm = bpm
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(INK if self.bpm else MUTED))
        size = min(210, int(self.height() * .98), int(self.width() * .50))
        painter.setFont(serif(size))
        text = str(self.bpm) if self.bpm else "—"
        text_width = painter.fontMetrics().horizontalAdvance(text)
        if text_width > self.width() - 78:
            size = int(size * (self.width() - 78) / text_width)
            painter.setFont(serif(size))
        bounds = QRectF(0, -14, self.width() - 72, self.height() + 5)
        painter.drawText(bounds, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         str(self.bpm) if self.bpm else "—")
        width = painter.fontMetrics().horizontalAdvance(str(self.bpm) if self.bpm else "—")
        painter.setFont(serif(30))
        painter.drawText(QPointF(min(width + 6, self.width() - 65), self.height() * .72), "bpm")


class Curve(QWidget):
    """在原始数据上分段，再压缩绘制；滚轮缩放、拖动平移、悬停读数。"""
    def __init__(self, *, title: str = "心率曲线") -> None:
        super().__init__()
        self.points: List[Point] = []
        self.markers = []  # 旧记录仍可由数据层读取；本界面不生成手动标记。
        self.axis = "elapsed"
        self.window = None
        self.title = title
        self.empty_text = "连接手表，开始记录你的心率变化"
        self.view = None
        self._hover = None
        self._drag = None
        self._times = []
        self.setMinimumHeight(190)
        self.setMouseTracking(True)

    def set_points(self, points: Sequence[Point], *, window=None, axis="elapsed", title=None, **kwargs) -> None:
        self.points = sorted(points)
        self._times = [p[0] for p in self.points]
        self.axis, self.window = axis, window
        if title is not None:
            self.title = title
        self.update()

    def clear(self) -> None:
        self.points = []
        self._times = []
        self.view = None
        self._hover = None
        self.update()

    def full_range(self) -> Point:
        if self.axis == "live":
            return -(self.window or 300), 0
        return 0, max(60, self.window or 0, self.points[-1][0] if self.points else 60)

    def reset_view(self) -> None:
        self.view = None
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(40, 32, max(10, self.width() - 60), max(10, self.height() - 64))

    def _range(self) -> Point:
        return self.view or self.full_range()

    def _clamp(self, left: float, right: float) -> Point:
        lo, hi = self.full_range()
        width = min(hi - lo, max(10, right - left))
        left = min(max(lo, left), hi - width)
        return left, left + width

    def wheelEvent(self, event) -> None:
        if not self.points:
            return
        left, right = self._range()
        plot = self._plot()
        factor = .75 if event.angleDelta().y() > 0 else 1.333333
        fraction = max(0., min(1., (event.position().x() - plot.left()) / plot.width()))
        anchor = left + fraction * (right - left)
        width = max(10, (right - left) * factor)
        self.view = self._clamp(anchor - fraction * width, anchor + (1 - fraction) * width)
        self.update()
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._plot().contains(event.position()):
            self._drag = (event.position().x(), self._range())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        self.unsetCursor()

    def mouseMoveEvent(self, event) -> None:
        self._hover = event.position() if self._plot().contains(event.position()) else None
        if self._drag:
            x, (left, right) = self._drag
            shift = (x - event.position().x()) / self._plot().width() * (right - left)
            self.view = self._clamp(left + shift, right + shift)
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset_view()

    def segments(self) -> List[List[Point]]:
        """断流信息来自原始采样，不能用压缩后的点间隔判断。"""
        result: List[List[Point]] = []
        for point in self.points:
            if not result or point[0] - result[-1][-1][0] > 5:
                result.append([])
            result[-1].append(point)
        return result

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot()
        left, right = self._range()
        first = max(0, bisect.bisect_left(self._times, left) - 1)
        last = bisect.bisect_right(self._times, right) + 1
        visible = self.points[first:last]
        values = [p[1] for p in visible] or [60, 110]
        low = max(20, math.floor((min(values) - 12) / 20) * 20)
        high = max(low + 60, math.ceil((max(values) + 12) / 20) * 20)
        def px(t):
            return plot.left() + (t - left) / (right - left) * plot.width()
        def py(b):
            return plot.bottom() - (b - low) / (high - low) * plot.height()
        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor(MUTED))
        painter.drawText(QPointF(0, 16), "bpm")
        painter.setPen(QPen(QColor(RULE), 1, Qt.PenStyle.DotLine))
        for value in range(int(low), int(high) + 1, 20):
            y = py(value)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(MUTED))
            painter.drawText(QRectF(0, y - 9, 30, 18), Qt.AlignmentFlag.AlignRight, str(value))
            painter.setPen(QPen(QColor(RULE), 1, Qt.PenStyle.DotLine))
        for i in range(7):
            t = left + (right - left) * i / 6
            x = px(t)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(QColor(MUTED))
            text = format_duration(abs(t))
            if t < 0:
                text = "−" + text
            painter.drawText(QRectF(x - 38, plot.bottom() + 10, 76, 20), Qt.AlignmentFlag.AlignCenter, text)
            painter.setPen(QPen(QColor(RULE), 1, Qt.PenStyle.DotLine))
        if not self.points:
            painter.setPen(QColor(MUTED))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return
        painter.save()
        painter.setClipRect(plot.adjusted(-1, -1, 1, 1))
        for segment in self.segments():
            # 按屏幕像素保留局部极值；长记录也不会被错误截断。
            buckets = {}
            for t, b in segment:
                if left - 5 <= t <= right + 5:
                    buckets.setdefault(int(px(t)), []).append((t, b))
            points = []
            for bucket in buckets.values():
                chosen = {bucket[0], bucket[-1], min(bucket, key=lambda p: p[1]), max(bucket, key=lambda p: p[1])}
                points.extend(sorted(chosen))
            if not points:
                continue
            line = QPainterPath(QPointF(px(points[0][0]), py(points[0][1])))
            for t, b in points[1:]:
                line.lineTo(px(t), py(b))
            area = QPainterPath(line)
            area.lineTo(px(points[-1][0]), plot.bottom())
            area.lineTo(px(points[0][0]), plot.bottom())
            area.closeSubpath()
            gradient = QLinearGradient(0, plot.top(), 0, plot.bottom())
            gradient.setColorAt(0, QColor(241, 61, 43, 35))
            gradient.setColorAt(1, QColor(241, 61, 43, 0))
            painter.fillPath(area, gradient)
            painter.setPen(QPen(QColor(RED), 1.6))
            painter.drawPath(line)
        peak = max(visible, key=lambda p: p[1]) if visible else None
        if peak and left <= peak[0] <= right:
            x, y = px(peak[0]), py(peak[1])
            painter.setBrush(QColor(RED))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(x, y), 3, 3)
            painter.setFont(serif(16))
            painter.setPen(QColor(RED))
            painter.drawText(QRectF(x - 28, y - 25, 56, 20), Qt.AlignmentFlag.AlignCenter, f"{peak[1]:.0f}")
        painter.restore()
        if self._hover:
            t = left + (self._hover.x() - plot.left()) / plot.width() * (right - left)
            index = bisect.bisect_left(self._times, t)
            choices = [i for i in (index - 1, index) if 0 <= i < len(self.points)]
            if choices:
                nearest = min(choices, key=lambda i: abs(self.points[i][0] - t))
                stamp, bpm = self.points[nearest]
                # 缺失区间不显示插值读数。
                if abs(stamp - t) <= 5:
                    x = px(stamp)
                    painter.setPen(QPen(QColor(MUTED), 1))
                    painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
                    box = QRectF(min(max(plot.left(), x - 65), plot.right() - 130), plot.top() + 8, 130, 38)
                    painter.fillRect(box, QColor(PAPER))
                    painter.setFont(QFont("Segoe UI", 10))
                    painter.setPen(QColor(INK))
                    painter.drawText(box, Qt.AlignmentFlag.AlignCenter,
                                     f"{format_duration(abs(stamp))}  ·  {bpm:.0f} bpm")


class GlobalHotkey(QAbstractNativeEventFilter):
    """Ctrl+Alt+H 全局显示/隐藏小窗。只注册热键，不监听任何其他键盘输入。"""

    ID = 0x4842
    #: MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
    MODIFIERS = 0x4003
    VK = 0x48  # VK_H

    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
        self.callback = callback
        self.registered = False
        app = QApplication.instance()
        # 没有 QApplication 时（例如单元测试）安全跳过，不注册也不炸
        if sys.platform == "win32" and app is not None:
            self.registered = bool(
                ctypes.windll.user32.RegisterHotKey(None, self.ID, self.MODIFIERS, self.VK)
            )
            if self.registered:
                app.installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32":
            from ctypes.wintypes import MSG

            msg = MSG.from_address(int(message))
            if int(msg.message) == 0x0312 and int(msg.wParam) == self.ID:
                self.callback()
                return True, 0
        return False, 0

    def close(self) -> None:
        """释放注册。可重复调用。"""
        if not self.registered:
            return
        ctypes.windll.user32.UnregisterHotKey(None, self.ID)
        app = QApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self)
        self.registered = False


class Overlay(QWidget):
    def __init__(self, parent: QWidget, store) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.store = store
        self.bpm = None
        self.detail = "等待读数"
        self.points = []
        self._drag = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.set_scale(store.get_int_setting("overlay_scale", 100), persist=False)
        screen = QApplication.primaryScreen().availableGeometry()
        x = store.get_int_setting("overlay_x", screen.right() - 360)
        y = store.get_int_setting("overlay_y", screen.top() + 80)
        available = [s.availableGeometry() for s in QApplication.screens()]
        if not any(r.contains(x + 40, y + 20) for r in available):
            x, y = screen.right() - 360, screen.top() + 80
        self.move(x, y)
        self.setWindowOpacity(store.get_int_setting("overlay_opacity", 92) / 100)

    def set_scale(self, percent: int, *, persist: bool = True) -> None:
        percent = max(75, min(150, percent))
        self.resize(round(320 * percent / 100), round(84 * percent / 100))
        if persist:
            self.store.set_setting("overlay_scale", str(percent))
        self.update()

    def set_reading(self, bpm: Optional[int], detail: str, points=()) -> None:
        self.bpm, self.detail, self.points = bpm, detail, list(points)
        self.update()

    def set_passthrough(self, enabled: bool) -> None:
        shown = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
        if shown:
            self.show()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.scale(self.width() / 320, self.height() / 84)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(218, 213, 204, 180), 1))
        p.setBrush(QColor(251, 249, 244, 240))
        p.drawRoundedRect(QRectF(1, 1, 318, 82), 9, 9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(RED if self.bpm else MUTED))
        p.drawEllipse(QPointF(20, 34), 4, 4)
        p.setFont(serif(55))
        p.setPen(QColor(INK))
        p.drawText(QRectF(34, -2, 94, 64), Qt.AlignmentFlag.AlignVCenter,
                   str(self.bpm) if self.bpm else "—")
        p.setFont(QFont("Segoe UI", 10))
        p.drawText(QPointF(125, 44), "bpm")
        p.setPen(QPen(QColor(RULE), 1))
        p.drawLine(166, 18, 166, 56)
        p.setPen(QPen(QColor(RED), 1.1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if len(self.points) > 1:
            start, end = self.points[0][0], self.points[-1][0]
            low, high = min(b for _, b in self.points) - 5, max(b for _, b in self.points) + 5
            line = QPainterPath()
            for i, (t, b) in enumerate(self.points):
                pt = QPointF(182 + (t - start) / max(1, end - start) * 112, 54 - (b - low) / (high - low) * 30)
                if i == 0 or t - self.points[i - 1][0] > 5:
                    line.moveTo(pt)
                else:
                    line.lineTo(pt)
            p.drawPath(line)
        p.setPen(QColor(MUTED))
        p.setFont(QFont("Microsoft YaHei UI", 8))
        p.drawText(QRectF(15, 61, 285, 17), Qt.AlignmentFlag.AlignLeft, self.detail)
        p.drawText(QRectF(297, 3, 16, 18), Qt.AlignmentFlag.AlignCenter, "×")

    def mousePressEvent(self, event) -> None:
        if event.position().x() / self.width() >= 294 / 320 and event.position().y() / self.height() < 26 / 84:
            self.hide()
        elif event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None:
            self._drag = None
            self.store.set_setting("overlay_x", str(self.x()))
            self.store.set_setting("overlay_y", str(self.y()))
