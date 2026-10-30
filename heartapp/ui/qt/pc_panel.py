"""PC 会话面板、事件标记和置顶小窗。"""
from __future__ import annotations

import ctypes
import sys
from typing import Callable, List, Optional
from ...hr import HeartRateSample

from PySide6.QtCore import QAbstractNativeEventFilter, Qt
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QVBoxLayout, QWidget

from ...pc import session_baseline
from ...metrics import format_duration
from . import theme as T


class MarkerHotkey(QAbstractNativeEventFilter):
    """Windows 注册 Ctrl+Alt+F8，不监听或记录其他键盘输入。"""
    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
        self.callback = callback
        self.registered = False
        if sys.platform == "win32":
            self.registered = bool(ctypes.windll.user32.RegisterHotKey(None, 0x4841, 0x4003, 0x77))
            if self.registered:
                QApplication.instance().installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32":
            from ctypes.wintypes import MSG
            msg = MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == 0x4841:
                self.callback()
                return True, 0
        return False, 0

    def close(self) -> None:
        if self.registered:
            ctypes.windll.user32.UnregisterHotKey(None, 0x4841)
            QApplication.instance().removeNativeEventFilter(self)
            self.registered = False


class HeartOverlay(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("HeartApp · PC 心率")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(240, 140)
        layout = QVBoxLayout(self)
        self.value = QLabel("-- bpm")
        self.value.setFont(T.font_px(32))
        self.detail = QLabel("等待手表读数")
        self.detail.setWordWrap(True)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        self.setStyleSheet(f"background: #FFFFFF; color: {T.INK};")

    def set_reading(self, bpm: Optional[int], detail: str) -> None:
        self.value.setText(f"{bpm} bpm" if bpm else "-- bpm")
        self.detail.setText(detail)


class PCSessionPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"QLabel, QListWidget {{ color: {T.INK}; background: transparent; }}")
        self.points: List[HeartRateSample] = []
        self.baseline: Optional[float] = None
        self.active = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.reference = QLabel("本次基线 -- · 开始会话后采集前 30 秒")
        self.reference.setWordWrap(True)
        self.reference.setFont(T.font("body"))
        layout.addWidget(self.reference)
        self.change = QLabel("等待有效心率 · 不根据心率推断情绪")
        self.change.setWordWrap(True)
        self.change.setFont(T.font("caption"))
        layout.addWidget(self.change)
        self.events = QListWidget()
        self.events.setMinimumHeight(48)
        self.events.setStyleSheet(f"border: none; background: transparent; color: {T.INK_2};")
        layout.addWidget(self.events, 1)

    def reset(self) -> None:
        self.points = []
        self.baseline = None
        self.events.clear()
        self.active = True
        self.reference.setText("正在建立基线 · 前 30 秒请保持平常状态")

    def sample(self, sample: HeartRateSample) -> None:
        if not self.active:
            return
        if sample.t_offset <= 30:
            self.points.append(sample)
        if self.baseline is None and sample.t_offset >= 30:
            self.baseline = session_baseline(self.points)
        if self.baseline is not None:
            self.reference.setText(f"本次基线 {self.baseline:.0f} bpm · 起始有效读数中位数")
        elif sample.t_offset > 30:
            self.reference.setText("本次基线不可用 · 起始有效读数不足或有断流")

    def marker(self, offset: float, label: str) -> None:
        self.events.addItem(f"{format_duration(offset)}  {label}")
        self.events.scrollToBottom()



class PCReading(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"QLabel {{ color: {T.INK}; background: transparent; }}")
        layout = QVBoxLayout(self)
        layout.addStretch()
        title = QLabel("当前心率")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        self.number = QLabel("--")
        self.number.setFont(T.font_px(64))
        self.number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.number)
        caption = QLabel("bpm · PC 会话监控")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(caption)
        layout.addStretch()

    def set_state(self, *, bpm: Optional[int] = None, max_hr: Optional[int] = None,
                  contact: Optional[bool] = None) -> None:
        self.number.setText(str(bpm) if bpm and contact is not False else "--")
