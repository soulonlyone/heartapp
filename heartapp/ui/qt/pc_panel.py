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


#: 全局标记快捷键：Ctrl+Alt+F8（不监听、不记录任何其他键盘输入）
WM_HOTKEY = 0x0312
MARKER_HOTKEY_ID = 0x4841
#: MOD_CONTROL(0x0002) | MOD_ALT(0x0001) | MOD_NOREPEAT(0x4000)，按住不重复触发
MARKER_HOTKEY_MODIFIERS = 0x4003
MARKER_HOTKEY_VK = 0x77  # VK_F8


def is_marker_hotkey(msg_id: int, wparam: int) -> bool:
    """判断一条 Win32 消息是不是我们的标记快捷键。

    抽成纯函数是为了**能测**：真实消息依赖 QApplication、系统热键注册和一次真实按键，
    自动化里没法按全局快捷键，但消息匹配逻辑可以单独验证。
    """
    return msg_id == WM_HOTKEY and wparam == MARKER_HOTKEY_ID


class MarkerHotkey(QAbstractNativeEventFilter):
    """Windows 注册 Ctrl+Alt+F8，不监听或记录其他键盘输入。"""

    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
        self.callback = callback
        self.registered = False
        app = QApplication.instance()
        # 没有 QApplication 时（例如单元测试）不注册，安全降级
        if sys.platform == "win32" and app is not None:
            self.registered = bool(
                ctypes.windll.user32.RegisterHotKey(
                    None, MARKER_HOTKEY_ID, MARKER_HOTKEY_MODIFIERS, MARKER_HOTKEY_VK
                )
            )
            if self.registered:
                app.installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32":
            from ctypes.wintypes import MSG

            msg = MSG.from_address(int(message))
            if is_marker_hotkey(int(msg.message), int(msg.wParam)):
                self.callback()
                return True, 0
        return False, 0

    def close(self) -> None:
        """释放注册。可重复调用。"""
        if not self.registered:
            return
        ctypes.windll.user32.UnregisterHotKey(None, MARKER_HOTKEY_ID)
        app = QApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self)
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
        self.baseline_settled = False
        self.events.clear()
        self.active = True
        self.reference.setText("正在建立基线 · 前 30 秒请保持平常状态")

    def sample(self, sample: HeartRateSample) -> None:
        if not self.active:
            return
        if sample.t_offset <= 30:
            self.points.append(sample)
        # 基线只在 30 秒后结算一次：起始区间已确定，再算结果也不会变
        if not self.baseline_settled and sample.t_offset >= 30:
            self.baseline = session_baseline(self.points)
            self.baseline_settled = True
        if self.baseline is not None:
            self.reference.setText(f"本次基线 {self.baseline:.0f} bpm · 起始有效读数中位数")
        elif self.baseline_settled:
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
