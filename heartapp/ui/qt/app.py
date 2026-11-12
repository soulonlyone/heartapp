"""Qt 版主界面。

架构和 Tkinter 版一致：界面只通过 ``engine.poll_events()`` 拿事件，
所有蓝牙/异步操作都在引擎自己的线程里，界面线程不碰 asyncio。

窗口的 paintEvent 画背景壁纸，三块玻璃面板是它的子控件；
面板只画自己的圆角区域，圆角外透明 → 和背景无缝，投影也能正常溢出。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import deque
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import APP_NAME, __version__
from ...metrics import format_duration
from ...paths import icon_path
from ...simulator import DEFAULT_PROFILE, PROFILES
from ...store import HeartRateStore, RecordingAlreadyOpenError, recording_lock
from ...engine import Engine
from .editorial import (
    INK,
    MUTED,
    PAPER,
    RULE,
    Button,
    GlobalHotkey,
    Overlay,
    ensure_light_palette,
)
from .editorial_layout import EditorialLayout
from .chrome import FramelessWindow

def _default_session_note(moment: "datetime | None" = None) -> str:
    """默认会话标题。

    不要把中文放进 strftime 的格式串：Windows 上 strftime 会用系统 ANSI 代码页
    编码整个格式串，英文（cp1252）等区域的机器会直接抛 UnicodeEncodeError，
    表现为一点「开始记录」就崩溃。这里用纯 ASCII 格式串再拼接。
    """
    now = moment or datetime.now()
    return "游戏记录 %02d-%02d %02d:%02d" % (now.month, now.day, now.hour, now.minute)


from .room_panel import RoomActions, RoomOverlay

#: 实时趋势图显示最近多少秒
LIVE_WINDOW_S = 300.0
#: 事件轮询间隔
POLL_MS = 120
#: 趋势图重绘间隔
CHART_MS = 500
#: 背景重建防抖
BACKDROP_DEBOUNCE_MS = 90

TREE_HEADERS = ("开始时间", "时长", "会话 / 设备", "来源", "样本", "平均", "最高")
#: 左栏宽度有限，宽度总和必须小于左栏，否则会出现横向滚动条。
#: 日期列给足 136px，否则会被截成「2026-10-17 ...」。
#: 「会话 / 设备」列放会话标题，比原先的设备名列需要更多空间。
TREE_WIDTHS = (136, 50, 104, 44, 42, 40, 40)
SOURCE_LABELS = {"ble": "手表", "sim": "模拟", "none": "-"}

COMBO_QSS = f"""
QComboBox {{
    background: #FFFFFF;
    border: 1px solid {RULE};
    border-radius: 9px;
    padding: 2px 10px;
    color: {INK};
    min-height: 26px;
}}
QComboBox:hover {{ border-color: {RULE}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: #FFFFFF;
    border: 1px solid {RULE};
    border-radius: 8px;
    selection-background-color: #F1F1F4;
    selection-color: {INK};
    color: {INK};
    outline: none;
    padding: 4px;
}}
"""

TABLE_QSS = f"""
QTableWidget {{
    background: #FFFFFF;
    alternate-background-color: #FBFBFC;
    border: none;
    color: {INK};
    gridline-color: transparent;
    selection-background-color: #F1F1F4;
    selection-color: {INK};
    outline: none;
}}
QHeaderView::section {{
    background: #FFFFFF;
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {RULE};
    padding: 8px 4px;
    font-weight: 600;
}}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #DCDCE3; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


class _Dialogs:
    """弹窗代理。

    Qt 的 QMessageBox 会开**嵌套事件循环**并阻塞调用方，自动化测试/截图脚本
    会直接卡死在弹窗上。``quiet=True`` 时把提示降级为无操作、确认一律返回 True。
    """

    quiet = False

    @classmethod
    def info(cls, parent, title, text, *args, **kwargs) -> None:
        if cls.quiet:
            return
        QMessageBox.information(parent, title, text)

    @classmethod
    def warn(cls, parent, title, text, *args, **kwargs) -> None:
        if cls.quiet:
            return
        QMessageBox.warning(parent, title, text)

    @classmethod
    def error(cls, parent, title, text, *args, **kwargs) -> None:
        if cls.quiet:
            return
        QMessageBox.critical(parent, title, text)

    @classmethod
    def ask(cls, parent, title, text, *, default=QMessageBox.StandardButton.NoButton) -> bool:
        if cls.quiet:
            return True
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        return QMessageBox.question(parent, title, text, buttons, default) == QMessageBox.StandardButton.Yes


class HeartAppWindow(RoomActions, EditorialLayout, FramelessWindow):
    """主窗口。"""

    def __init__(self, store: HeartRateStore, engine: Engine, *, db_path: Optional[Path] = None) -> None:
        super().__init__()
        # 固定浅色调色板：系统深色模式下 Qt 默认文字色是白色，而 QSS 把背景写死成
        # 暖白纸，会让没显式设色的控件变成白字白底（历史列表曾整行不可见）
        ensure_light_palette()
        self.store = store
        self.engine = engine
        self.db_path = Path(db_path) if db_path else Path(store.db_path)

        # 运行态
        self._devices: List[object] = []
        self._live: Deque[Tuple[float, int]] = deque(maxlen=4000)
        self._last_sample = None
        self._last_reading_clock = None
        self._session_points = []
        self._last_session_id = None
        self._pc_clock = 0.0
        self._chart_dirty = False
        self._last_chart_ms = 0.0

        # 当前记录
        self._session_id: Optional[int] = None
        self._session_started: Optional[datetime] = None
        self._rec_sum = 0
        self._rec_n = 0
        self._rec_min: Optional[int] = None
        self._rec_max: Optional[int] = None

        # 历史
        self._sessions: List[object] = []
        self._selected_session_id: Optional[int] = None

        self._pending_autorecord = False
        self._last_device_address = self.store.get_setting("last_device_address")
        self._connecting_last_device = False
        self._scan_running = False
        self._max_hr = self.engine.max_hr()
        self._closed = False
        self._closing = False
        self._close_ready = False
        self._loading_overlay_preset = False
        self._init_room()

        self.setWindowTitle(f"{APP_NAME} — PC 心率监控")
        self.resize(860, 600)
        self.setMinimumSize(780, 575)

        self._build_ui()
        self._update_state_pill()
        self._refresh_history()
        self._overlay = Overlay(self.store)
        self._room_overlay = RoomOverlay(self.store, appearance=self._overlay.appearance)
        self._overlay_preview_window = Overlay(self.store, appearance=self._overlay.appearance)
        self._refresh_overlay_preview()
        self._room_overlay.set_scale(self._overlay_scale.value())
        for overlay in (self._overlay, self._room_overlay):
            overlay.set_curve_seconds(self._overlay_curve.currentData())
        self._overlay.set_passthrough(self._passthrough.isChecked())
        self._room_overlay.set_passthrough(self._passthrough.isChecked())
        self._room_overlay.setWindowOpacity(self.store.get_int_setting("overlay_opacity", 92) / 100)
        self._overlay_hotkey = GlobalHotkey(self._toggle_active_overlay)
        if not self._overlay_hotkey.registered:
            self._hotkey_hint.setText("快捷键不可用或被占用，请使用此页按钮显示 / 隐藏小窗")

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(POLL_MS)

    # ==================================================================
    # 背景
    # ==================================================================

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(PAPER))

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            self._poll_room()
            for kind, payload in self.engine.poll_events():
                try:
                    self._handle_event(kind, payload)
                except Exception as exc:  # noqa: BLE001
                    self._status_label.setText(f"界面错误：{exc}")

            now = time.monotonic() * 1000.0
            if self._chart_dirty and now - self._last_chart_ms >= CHART_MS:
                self._last_chart_ms = now
                self._chart_dirty = False
                self._draw_live_chart()

            self._update_stat_labels()
            self._update_pc_reading()
        except Exception as exc:  # noqa: BLE001
            self._status_label.setText(f"界面错误：{exc}")

    def _handle_event(self, kind: str, payload: Dict) -> None:
        if kind == "status":
            state = payload.get("state", "")
            detail = payload.get("detail", "")
            self._status_label.setText(detail or " ")
            self._connection_note.setText(detail or " ")
            self._update_state_pill(state)
            if self._last_sample is not None:
                self.bpm_panel.set_state(
                    bpm=self._last_sample.bpm,
                    max_hr=self._max_hr,
                    contact=self._last_sample.contact,
                )
            if state in ("connected", "connecting", "reconnecting"):
                self._connect_btn.set_text("断开")
            elif state in ("stopped", "idle", "error"):
                self._connect_btn.set_text("连接")
            if self._connecting_last_device and state in ("connected", "error", "reconnecting", "stopped"):
                self._connecting_last_device = False
                if state in ("error", "reconnecting") and not self._closing:
                    self._show_connection()
            if state == "connected" and self._pending_autorecord:
                self._pending_autorecord = False
                self.engine.start_pc_recording(_default_session_note(), "游戏")
            address = payload.get("device_address")
            if address and address != self._last_device_address:
                self.store.set_setting("last_device_address", address)
                self._last_device_address = address

        elif kind == "sample":
            sample = payload.get("sample")
            if sample is None:
                return
            self._last_sample = sample
            received = payload.get("received_at", time.monotonic())
            if sample.bpm > 0 and sample.contact is not False:
                self._last_reading_clock = received
                self._live.append((received, int(sample.bpm)))
                if self._room_code:
                    self._room_history.append("self", received, int(sample.bpm))
                if self._session_id is not None:
                    self._session_points.append((sample.t_offset, sample.bpm))
                    self._rec_sum += sample.bpm
                    self._rec_n += 1
                    self._rec_min = sample.bpm if self._rec_min is None else min(self._rec_min, sample.bpm)
                    self._rec_max = sample.bpm if self._rec_max is None else max(self._rec_max, sample.bpm)
            self._chart_dirty = True

        elif kind == "devices":
            devices = payload.get("devices") or []
            self._scan_running = False
            self._scan_btn.setEnabled(not self._sim_chip.isChecked())
            detail = payload.get("error") or payload.get("detail")
            if detail:
                self._connection_note.setText(detail)
                self._status_label.setText(detail)
            if payload.get("error") and not self._closing:
                _Dialogs.warn(self, APP_NAME, detail)
            self._devices = list(devices)
            self._device_combo.clear()
            for device in devices:
                mark = "  ♥" if getattr(device, "has_heart_rate", False) else ""
                self._device_combo.addItem(f"{device.label}{mark}", device)
            if devices:
                heart = [d for d in devices if d.has_heart_rate]
                if heart:
                    self._device_combo.setCurrentIndex(devices.index(heart[0]))

        elif kind == "error":
            message = payload.get("message", "")
            self._status_label.setText(message)
            if message and not self._closing:
                _Dialogs.warn(self, APP_NAME, message)

        elif kind == "shutdown_failed":
            self._closing = False
            self.setEnabled(True)
            detail = payload.get("message", "")
            if self.engine.is_recording:
                message = "记录尚未保存完成，已取消退出。请检查磁盘空间或文件权限后重试。"
                self._status_label.setText("保存失败，已取消退出；记录仍保留在内存中，请重试。")
            else:
                message = "记录已保存，但程序未能完成退出。请稍后重试。"
                self._status_label.setText("未能退出，请稍后重试。")
            message += f"\n\n{detail}"
            _Dialogs.error(self, APP_NAME, message)

        elif kind == "shutdown_ready":
            self._close_ready = True
            self.close()

        elif kind == "session_started":
            self._session_id = int(payload.get("session_id"))
            self._session_started = payload.get("started_at") or datetime.now()
            self._pc_clock = payload.get("clock", time.monotonic())
            self._session_points = []
            self._last_session_id = self._session_id
            self._view_record_btn.hide()
            self._session_caption.setText(f"{self._session_started:%Y-%m-%d  %H:%M} 开始 · 数据保存在本机")
            self._live_range.setCurrentIndex(2)
            self.live_chart.reset_view()
            self._sim_chip.setEnabled(False)
            self._profile_combo.setEnabled(False)
            self._device_combo.setEnabled(False)
            self._rec_sum = 0
            self._rec_n = 0
            self._rec_min = None
            self._rec_max = None
            self._record_label.setText("●  模拟记录中" if self.engine.source_kind == "sim" else "●  记录中")
            self._update_state_pill()

        elif kind == "session_ended":
            self._sim_chip.setEnabled(True)
            self._profile_combo.setEnabled(True)
            self._on_sim_toggled()
            stats = payload.get("stats")
            self._session_id = None
            self._session_started = None
            self._record_label.setText("●  本次已保存")
            self._view_record_btn.show()
            self._update_state_pill()
            if stats is not None:
                self._history_summary.setText(self._format_stats(stats))
                self._status_label.setText("本次记录已保存，可在「回看」查看完整曲线。")
            self._refresh_history()

    def _update_state_pill(self, state: Optional[str] = None) -> None:
        state = state if state is not None else self.engine.state
        texts = {"connected": "●  模拟已连接" if self.engine.source_kind == "sim" else "●  手表已连接",
                 "connecting": "◌  正在连接", "reconnecting": "◌  正在重连", "scanning": "◌  正在扫描",
                 "error": "●  连接异常"}
        self._device_status.setText(texts.get(state, "●  手表未连接"))
        recording = self._session_id is not None
        connecting = state in ("connecting", "reconnecting")
        can_choose = not (recording or connecting or state == "connected")
        remembered = bool(self._last_device_address) and not self._sim_chip.isChecked()
        self._choose_device_btn.setVisible(can_choose and remembered)
        if recording:
            action = "停止记录"
        elif connecting:
            action = "正在重连…" if state == "reconnecting" else "正在连接…"
        elif state == "connected":
            action = "开始记录"
        else:
            action = "连接上次设备" if remembered else "连接手表"
        self._record_btn.setText(action)
        self._record_btn.set_variant("danger" if recording else "filled")
        self._record_btn.setEnabled(recording or not connecting)
        can_disconnect = state in ("connected", "reconnecting")
        self._disconnect_btn.setVisible(can_disconnect)
        self._room_disconnect_btn.setVisible(can_disconnect)

    def _selected_profile_key(self) -> str:
        label = self._profile_combo.currentText()
        for key, text in PROFILES.items():
            if text == label:
                return key
        return DEFAULT_PROFILE

    def _on_sim_toggled(self) -> None:
        simulated = self._sim_chip.isChecked()
        self._device_combo.setEnabled(not simulated)
        self._scan_btn.setEnabled(not simulated and not self._scan_running)
        self._update_state_pill()

    def on_overlay_toggle(self) -> None:
        self._overlay.setVisible(not self._overlay.isVisible())

    def _set_overlay_opacity(self, value: int) -> None:
        if self._loading_overlay_preset:
            return
        self._overlay.setWindowOpacity(value / 100)
        self._room_overlay.setWindowOpacity(value / 100)
        self.store.set_setting("overlay_opacity", str(value))
        self._refresh_overlay_preview()

    def _set_overlay_scale(self, value: int) -> None:
        if self._loading_overlay_preset:
            return
        self._overlay.set_scale(value)
        self._room_overlay.set_scale(value)
        self._refresh_overlay_preview()

    def _set_overlay_curve(self) -> None:
        if self._loading_overlay_preset:
            return
        seconds = self._overlay_curve.currentData()
        self._overlay.set_curve_seconds(seconds)
        self._room_overlay.set_curve_seconds(seconds)
        self.store.set_setting("overlay_curve_seconds", str(seconds))
        self._refresh_overlay_preview()

    def _refresh_overlay_preview(self) -> None:
        preview = self._overlay_preview_window
        preview.set_scale(self._overlay_scale.value(), persist=False)
        preview.set_curve_seconds(self._overlay_curve.currentData())
        now = time.monotonic()
        values = (71, 72, 71, 75, 73, 80, 78, 83, 82, 76, 77, 74)
        interval = (preview.curve_seconds or 15) / (len(values) + 1)
        preview.set_reading(74, "模拟数据", [(now - (len(values) - i) * interval, bpm)
                                             for i, bpm in enumerate(values)])
        source = preview.grab()
        pixmap = QPixmap(source.size())
        pixmap.setDevicePixelRatio(source.devicePixelRatio())
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setOpacity(self._opacity.value() / 100)
        painter.drawPixmap(0, 0, source)
        painter.end()
        dpr = source.devicePixelRatio()
        bounds = QSize(min(source.width(), round(300 * dpr)), min(source.height(), round(100 * dpr)))
        self._overlay_preview.setPixmap(
            pixmap.scaled(bounds, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )
        surface = "#E7E2D9" if self._overlay_preview_light.isChecked() else "#232935"
        self._overlay_preview.setStyleSheet(f"background: {surface}; border-radius: 8px;")
        for key, button in self._overlay_color_buttons.items():
            color = preview.appearance.colors[key].name().upper()
            button.setToolTip(f"当前颜色 {color}，点击更改")
            button.setStyleSheet(
                f"QPushButton {{ background: {color}; border: 1px solid {RULE}; border-radius: 18px; }}"
                "QPushButton:hover, QPushButton:focus { border: 2px solid #60A5FA; }"
                "QPushButton:disabled { background: #F0EDE7; border-style: dashed; }"
            )
        image_path = self.store.get_setting("overlay_background_image", "")
        using_image = not preview.appearance.image.isNull()
        self._overlay_clear_image.setVisible(bool(image_path))
        self._overlay_image_button.setText("更换图片" if image_path else "选择图片")
        self._overlay_color_buttons["background"].setEnabled(not using_image)
        if using_image:
            self._overlay_background_status.setText("当前背景 · 图片")
            self._overlay_background_status.setToolTip(Path(image_path).name if image_path else "")
            self._overlay_color_buttons["background"].setToolTip("移除图片后可调整纯色背景")
        elif image_path:
            self._overlay_background_status.setText("图片不可用 · 纯色")
            self._overlay_background_status.setToolTip("")
        else:
            self._overlay_background_status.setText("当前背景 · 纯色")
            self._overlay_background_status.setToolTip("")

    def _refresh_overlay_appearance(self) -> None:
        self._overlay.update()
        self._room_overlay.update()
        self._refresh_overlay_preview()

    def _update_overlay_style_values(self) -> None:
        if self._loading_overlay_preset:
            return
        appearance = self._overlay.appearance
        appearance.background_opacity = self._overlay_background_opacity.value()
        appearance.line_width = self._overlay_line_width.value()
        self.store.set_setting("overlay_background_opacity", str(appearance.background_opacity))
        self.store.set_setting("overlay_line_width", str(appearance.line_width))
        self._refresh_overlay_appearance()

    def _choose_overlay_color(self, key: str) -> None:
        from PySide6.QtWidgets import QColorDialog, QGridLayout, QPushButton, QWidgetAction

        anchor = self._overlay_color_buttons[key]
        current = self._overlay.appearance.colors[key]
        menu = QMenu(anchor)
        palette = QWidget(menu)
        grid = QGridLayout(palette)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setSpacing(7)
        presets = (
            PAPER, "#FFFFFF", INK, "#10243A", "#344054", "#6B7280",
            "#F13D2B", "#FF9F43", "#F7D154", "#4DEBC0", "#60A5FA", "#B794F6",
        )
        def select_color(color: QColor) -> None:
            menu.close()
            self._set_overlay_color(key, color)

        for index, hex_color in enumerate(presets):
            color = QColor(hex_color)
            swatch = QPushButton(palette)
            swatch.setFixedSize(30, 30)
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.setToolTip(hex_color)
            swatch.setAccessibleName(hex_color)
            border = "2px solid #60A5FA" if color == current else f"1px solid {RULE}"
            swatch.setStyleSheet(
                f"QPushButton {{ background: {hex_color}; border: {border}; border-radius: 6px; }}"
                f"QPushButton:hover, QPushButton:focus {{ border: 2px solid #60A5FA; }}"
            )
            swatch.clicked.connect(lambda _checked=False, selected=color: select_color(selected))
            grid.addWidget(swatch, index // 6, index % 6)
        swatches = QWidgetAction(menu)
        swatches.setDefaultWidget(palette)
        menu.addAction(swatches)
        custom = menu.addAction("自定义颜色…")
        popup_x = min(0, anchor.width() - menu.sizeHint().width())
        if menu.exec(anchor.mapToGlobal(QPoint(popup_x, anchor.height()))) == custom:
            color = QColorDialog.getColor(current, self, "选择小窗颜色")
            if color.isValid():
                self._set_overlay_color(key, color)

    def _set_overlay_color(self, key: str, color: QColor) -> None:
        self.store.set_setting(f"overlay_color_{key}", color.name())
        self._overlay.appearance.colors[key] = color
        self._refresh_overlay_appearance()

    def _choose_overlay_image(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, "选择小窗背景图片", "", "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return
        image = self._overlay.appearance.load_image(path)
        if image.isNull():
            _Dialogs.warn(self, APP_NAME, "无法读取这张图片。请选一张有效的图片。")
            return
        self.store.set_setting("overlay_background_image", path)
        self._overlay.appearance.image = image
        self._refresh_overlay_appearance()

    def _clear_overlay_image(self) -> None:
        self.store.set_setting("overlay_background_image", "")
        self._overlay.appearance.image = QPixmap()
        self._refresh_overlay_appearance()

    def _overlay_preset_values(self) -> Dict[str, str]:
        appearance = self._overlay.appearance
        return {
            "overlay_opacity": str(self._opacity.value()),
            "overlay_scale": str(self._overlay_scale.value()),
            "overlay_curve_seconds": str(self._overlay_curve.currentData()),
            "overlay_passthrough": "1" if self._passthrough.isChecked() else "0",
            "overlay_background_opacity": str(self._overlay_background_opacity.value()),
            "overlay_line_width": str(self._overlay_line_width.value()),
            "overlay_background_image": self.store.get_setting("overlay_background_image") or "",
            **{f"overlay_color_{key}": color.name() for key, color in appearance.colors.items()},
            "overlay_x": str(self._overlay.x()),
            "overlay_y": str(self._overlay.y()),
            "room_overlay_x": str(self._room_overlay.x()),
            "room_overlay_y": str(self._room_overlay.y()),
        }

    def _show_overlay_presets(self) -> None:
        raw = self.store.get_setting("overlay_presets", "{}")
        try:
            presets = json.loads(raw)
            if not isinstance(presets, dict):
                raise ValueError
        except ValueError:
            _Dialogs.warn(self, APP_NAME, "无法读取小窗预设。")
            return

        button = self._overlay_presets_btn
        menu = QMenu(button)
        save = menu.addAction("保存当前设置…")
        names = sorted(presets, key=str.casefold)
        apply_actions = {}
        delete_actions = {}
        if names:
            menu.addSeparator()
            for name in names:
                apply_actions[menu.addAction(name.replace("&", "&&"))] = name
            remove = menu.addMenu("删除预设")
            for name in names:
                delete_actions[remove.addAction(name.replace("&", "&&"))] = name
        choice = menu.exec(button.mapToGlobal(QPoint(0, button.height())))
        if choice == save:
            self._save_overlay_preset(presets)
        elif choice in apply_actions:
            name = apply_actions[choice]
            self._apply_overlay_preset(name, presets[name])
        elif choice in delete_actions:
            name = delete_actions[choice]
            if _Dialogs.ask(self, "删除小窗预设", f"要删除「{name}」吗？", default=QMessageBox.StandardButton.No):
                del presets[name]
                self.store.set_setting("overlay_presets", json.dumps(presets, ensure_ascii=False))
        menu.deleteLater()

    def _save_overlay_preset(self, presets: dict) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, accepted = QInputDialog.getText(self, "保存小窗预设", "预设名称")
        name = name.strip()
        if not accepted or not name:
            return
        if len(name) > 32 or not name.isprintable():
            _Dialogs.warn(self, APP_NAME, "预设名称请使用 1～32 个可见字符。")
            return
        if name in presets and not _Dialogs.ask(
            self, "覆盖小窗预设", f"要用当前设置覆盖「{name}」吗？", default=QMessageBox.StandardButton.No
        ):
            return
        presets[name] = self._overlay_preset_values()
        self.store.set_setting("overlay_presets", json.dumps(presets, ensure_ascii=False))
        self._status_label.setText(f"已保存小窗预设「{name}」。")

    def _apply_overlay_preset(self, name: str, saved: dict) -> None:
        sliders = {
            "overlay_opacity": self._opacity,
            "overlay_scale": self._overlay_scale,
            "overlay_background_opacity": self._overlay_background_opacity,
            "overlay_line_width": self._overlay_line_width,
        }
        appearance = self._overlay.appearance
        try:
            values = {key: saved[key] for key in self._overlay_preset_values()}
            numbers = {key: int(values[key]) for key in sliders}
            if any(not slider.minimum() <= numbers[key] <= slider.maximum()
                   for key, slider in sliders.items()):
                raise ValueError
            seconds = int(values["overlay_curve_seconds"])
            curve_index = self._overlay_curve.findData(seconds)
            colors = {key: QColor(values[f"overlay_color_{key}"]) for key in appearance.COLORS}
            if curve_index < 0 or any(not color.isValid() for color in colors.values()):
                raise ValueError
            image_path = values["overlay_background_image"]
            if not isinstance(image_path, str) or values["overlay_passthrough"] not in ("0", "1"):
                raise ValueError
            positions = {}
            for prefix, overlay in (("overlay", self._overlay), ("room_overlay", self._room_overlay)):
                x, y = int(values[f"{prefix}_x"]), int(values[f"{prefix}_y"])
                point = QPoint(x, y)
                screen = QApplication.screenAt(point)
                if screen is None or not screen.availableGeometry().contains(point + QPoint(40, 20)):
                    point = self.screen().availableGeometry().topLeft() + QPoint(24, 24)
                positions[overlay] = point
                values[f"{prefix}_x"], values[f"{prefix}_y"] = str(point.x()), str(point.y())
        except (KeyError, TypeError, ValueError, OverflowError):
            _Dialogs.warn(self, APP_NAME, f"小窗预设「{name}」的设置无效。")
            return

        image = appearance.load_image(image_path) if image_path else QPixmap()
        try:
            self.store.set_settings(values)
        except sqlite3.Error as exc:
            _Dialogs.warn(self, APP_NAME, f"无法应用小窗预设：{exc}")
            return
        self._loading_overlay_preset = True
        try:
            for key, slider in sliders.items():
                slider.setValue(numbers[key])
            self._overlay_curve.setCurrentIndex(curve_index)
            self._passthrough.setChecked(values["overlay_passthrough"] == "1")
        finally:
            self._loading_overlay_preset = False

        appearance.colors.update(colors)
        appearance.image = image
        appearance.background_opacity = numbers["overlay_background_opacity"]
        appearance.line_width = numbers["overlay_line_width"]
        self._overlay.set_scale(numbers["overlay_scale"], persist=False)
        self._room_overlay.set_scale(numbers["overlay_scale"])
        for overlay, point in positions.items():
            overlay.set_curve_seconds(seconds)
            overlay.setWindowOpacity(numbers["overlay_opacity"] / 100)
            overlay.set_passthrough(self._passthrough.isChecked())
            overlay.move(point)
        self._refresh_overlay_appearance()
        self._status_label.setText(f"已应用小窗预设「{name}」。")

    def _reset_overlay_settings(self) -> None:
        self._opacity.setValue(92)
        self._overlay_scale.setValue(100)
        self._overlay_curve.setCurrentIndex(self._overlay_curve.findData(60))
        self._passthrough.setChecked(False)
        self._overlay_background_opacity.setValue(100)
        self._overlay_line_width.setValue(1)
        for key, default in self._overlay.appearance.COLORS.items():
            self._set_overlay_color(key, QColor(default))
        self._clear_overlay_image()

    def _set_overlay_passthrough(self, enabled: bool) -> None:
        if self._loading_overlay_preset:
            return
        self._overlay.set_passthrough(enabled)
        self._room_overlay.set_passthrough(enabled)
        self.store.set_setting("overlay_passthrough", "1" if enabled else "0")

    def _update_pc_reading(self) -> None:
        self._publish_room_reading()
        self._sync_room_home()
        fresh = (self._last_reading_clock is not None
                 and time.monotonic() - self._last_reading_clock <= 5
                 and self.engine.state == "connected")
        sample = self._last_sample
        bpm = sample.bpm if fresh and sample and sample.contact is not False else None
        prefix = "模拟数据 · " if self.engine.source_kind == "sim" else ""
        if bpm:
            detail = "实时心率"
        elif self.engine.is_recording:
            detail = ("记录中 · 设备已断开" if self.engine.state == "stopped"
                      else "记录中 · 等待心率恢复")
        elif self.engine.state == "connected":
            detail = "等待手表传来心率"
        elif self.engine.state in ("connecting", "reconnecting"):
            detail = "正在等待设备连接"
        else:
            detail = "先在手表上开启心率广播"
        self._reading_detail.setText(prefix + detail)
        self.bpm_panel.set_state(bpm=bpm)
        self._overlay.set_reading(bpm, prefix + ("记录中" if self.engine.is_recording else detail),
                                  [(t, b) for t, b in self._live if t >= time.monotonic() - 60])
        if self.engine.is_recording or self._live:
            self._chart_dirty = True

    def on_scan(self) -> None:
        if self._scan_running:
            return
        if self._sim_chip.isChecked():
            _Dialogs.info(self, APP_NAME,
                                    "当前是模拟模式，不需要扫描设备。\n再点一下「模拟模式」取消即可扫描真实手表。")
            return
        self._scan_running = True
        self._scan_btn.setEnabled(False)
        self._connection_note.setText("正在扫描附近蓝牙设备…")
        self.engine.scan(timeout=8.0)

    def on_connect_last_device(self) -> None:
        if not self._last_device_address or self._connecting_last_device or self.engine.is_running:
            return
        self._connecting_last_device = True
        self._update_state_pill("connecting")
        self.engine.connect_device(self._last_device_address)

    def on_disconnect(self) -> None:
        message = "要断开当前心率设备吗？"
        if self.engine.is_recording:
            message += "\n\n本次记录会继续计时，断开期间不会收到心率。"
        if _Dialogs.ask(self, "断开连接", message, default=QMessageBox.StandardButton.No):
            self.engine.stop_source()

    def on_connect_toggle(self) -> None:
        if self.engine.is_running:
            self.on_disconnect()
            return
        if self._sim_chip.isChecked():
            self.engine.connect_simulator(self._selected_profile_key())
            return
        data = self._device_combo.currentData()
        text = self._device_combo.currentText().strip()
        if data is not None:
            self.engine.connect_device(data)
        elif text:
            self.engine.connect_device(text)
        else:
            _Dialogs.info(
                self, APP_NAME,
                "请先点「扫描设备」选一个设备，或直接输入手表的蓝牙名称。\n\n"
                "⚠ 手表端请先打开「心率广播 / 心率分享」。",
            )

    def on_record_toggle(self) -> None:
        if self.engine.is_recording:
            if _Dialogs.ask(
                self, "停止记录", "要停止并保存本次心率记录吗？",
                default=QMessageBox.StandardButton.No,
            ):
                self.engine.stop_recording()
            return
        if self.engine.state != "connected":
            if self._last_device_address and not self._sim_chip.isChecked():
                self.on_connect_last_device()
            else:
                self._show_connection()
            return
        self.engine.start_pc_recording(_default_session_note(), "游戏")

    def on_export_current(self, *, directory: Optional[Path] = None) -> None:
        if self._session_id is None and self._last_session_id is None:
            _Dialogs.info(self, APP_NAME, "请先记录一段心率，再导出曲线。")
            return
        self._export_session(self._session_id or self._last_session_id, directory=directory)

    def on_export_selected(self, *, directory: Optional[Path] = None) -> None:
        if self._selected_session_id is None:
            _Dialogs.info(self, APP_NAME, "请先在左侧选择一次记录。")
            return
        self._export_session(self._selected_session_id, directory=directory)

    def _export_button(self, *, variant: str, height: int, current: bool) -> "Button":
        """带下拉菜单的导出按钮。

        明确给两条路：
        * 「另存为…」—— 弹系统保存对话框，自己选位置；
        * 「导出到数据目录」—— 完全不碰系统对话框，直接写进 exe 旁边的 data\，
          这条路径永远有写权限（数据库就在那）。
        """
        button = Button("导出 CSV")
        button.setFixedHeight(height)
        button.set_variant(variant)
        menu = QMenu(button)
        menu.setStyleSheet(
            f"""
            QMenu {{
                background: #FFFFFF;
                border: 1px solid {RULE};
                border-radius: 10px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 8px 20px;
                border-radius: 6px;
                color: {INK};
            }}
            QMenu::item:selected {{ background: #F1F1F4; }}
            """
        )
        save_as = QAction("另存为…", menu)
        save_as.triggered.connect(
            lambda: (self.on_export_current() if current else self.on_export_selected())
        )
        menu.addAction(save_as)

        to_data = QAction("导出到数据目录", menu)
        to_data.triggered.connect(
            lambda: (
                self.on_export_current(directory=self.db_path.parent)
                if current
                else self.on_export_selected(directory=self.db_path.parent)
            )
        )
        menu.addAction(to_data)

        button.setMenu(menu)
        button.setToolTip(
            "「另存为…」可以自己选位置；没有写权限时用「导出到数据目录」，"
            "那条路一定有权限"
        )
        return button

    def _export_session(self, session_id: int, *,
                        directory: Optional[Path] = None) -> None:
        """导出 CSV。

        ``directory`` 不为空时直接写到该目录（不走保存对话框），用于
        「目标位置没权限」时的兜底。
        """
        from PySide6.QtWidgets import QFileDialog

        session = self.store.get_session(session_id)
        if session is None:
            _Dialogs.warn(self, APP_NAME, "这条记录已经不存在了。")
            return

        default_name = f"心率记录_{session.started_at:%Y%m%d_%H%M%S}.csv"

        if directory is not None:
            target_path = str(Path(directory) / default_name)
        else:
            target_path, _ = QFileDialog.getSaveFileName(
                self, "导出为 CSV",
                str(self.db_path.parent / default_name),
                "CSV 文件 (*.csv)",
            )
            if not target_path:
                return

        try:
            target = self.store.export_csv(session_id, target_path)

        except (PermissionError, OSError) as exc:
            self._offer_fallback(session_id, target_path, exc)
            return
        except Exception as exc:  # noqa: BLE001
            _Dialogs.error(self, APP_NAME, f"导出失败：{exc}\n\n文件：{target_path}")
            return

        if _Dialogs.ask(self, APP_NAME,
                        f"已导出 {self.store.sample_count(session_id)} 条数据：\n{target}\n\n"
                        f"要打开所在文件夹吗？"):
            self._open_folder(Path(target).parent)

    def _offer_fallback(self, session_id: int, failed_path: str, exc: Exception) -> None:
        """目标位置写不进去时，解释原因并改存到程序自己的数据目录。"""
        data_dir = self.db_path.parent
        message = (
            f"没有权限写入这个位置：\n{failed_path}\n\n"
            f"系统返回：{exc}\n\n"
            f"常见原因：\n"
            f"① Windows「受控文件夹访问」拦截了未签名程序写入桌面/文档；\n"
            f"② 目标文件夹是只读的（例如 C:\\ 根目录、Program Files）。\n\n"
            f"程序自己的数据目录一定有写权限：\n{data_dir}\n\n"
            f"是否改为导出到数据目录？"
        )
        if _Dialogs.ask(self, APP_NAME, message):
            self._export_session(session_id, directory=data_dir)

    def _open_folder(self, folder: Path) -> None:
        import os

        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            _Dialogs.warn(self, APP_NAME, f"打不开目录：{exc}\n\n路径：{folder}")

    def on_delete_selected(self) -> None:
        if self._selected_session_id is None:
            _Dialogs.info(self, APP_NAME, "请先在左侧选择一次记录。")
            return
        if self._selected_session_id == self.engine.session_id:
            _Dialogs.warn(self, APP_NAME, "请先停止记录，再删除这次会话。")
            return
        if not _Dialogs.ask(self, APP_NAME, "确定删除这条记录吗？此操作不可撤销。"):
            return
        if self._last_session_id == self._selected_session_id:
            self._last_session_id = None
            self._view_record_btn.hide()
        self.store.delete_session(self._selected_session_id)
        self._selected_session_id = None
        self._history_summary.setText("选择左侧的一次记录查看详情。")
        self.history_chart.clear()
        self.history_chart.markers = []
        self._refresh_history()

    def on_open_data_dir(self) -> None:
        self._open_folder(self.db_path.parent)

    def on_settings(self) -> None:
        SettingsDialog(self, self.store, on_saved=self._on_settings_saved)

    def _on_settings_saved(self) -> None:
        self._max_hr = self.engine.max_hr()
        self._draw_live_chart()
        self._update_stat_labels()

    # ==================================================================
    # 统计与图表
    # ==================================================================

    def _draw_live_chart(self) -> None:
        now = time.monotonic()
        if self._room_code:
            self._live_chart_stack.setCurrentIndex(1)
            self._refresh_room_graph(now)
            return
        self._live_chart_stack.setCurrentIndex(0)
        window = self._live_range.currentData()
        if window == 0 and self._session_points:
            elapsed = now - self._pc_clock if self.engine.is_recording else self._session_points[-1][0]
            self.live_chart.set_points(self._session_points, axis="elapsed", window=max(60, elapsed))
        else:
            window = window or 300
            self.live_chart.set_points([(t - now, b) for t, b in self._live if t >= now - window],
                                       window=window, axis="live")

    def _update_stat_labels(self) -> None:
        elapsed = max(0, time.monotonic() - self._pc_clock) if self.engine.is_recording else None
        if elapsed is not None:
            self._stat_values["duration"].setText(format_duration(elapsed))
        if self._rec_n:
            values = {"avg": f"{self._rec_sum / self._rec_n:.0f}",
                      "max": str(self._rec_max), "min": str(self._rec_min)}
            for key, value in values.items():
                self._stat_values[key].setText(value)

    # ==================================================================
    # 历史
    # ==================================================================

    def _refresh_history(self) -> None:
        sessions = self.store.list_sessions(limit=500)
        aggregates = self.store.session_bpm_aggregate([s.id for s in sessions])
        self._sessions = sessions
        self.tree.blockSignals(True)
        self.tree.setRowCount(len(sessions))
        for row, session in enumerate(sessions):
            avg, low, high, count = aggregates.get(session.id, (None, None, None, 0))
            source = SOURCE_LABELS.get(session.source, session.source)
            values = (f"{session.started_at:%m-%d  %H:%M}  ·  {source}",
                      format_duration(session.duration_s) if session.ended_at else "记录中",
                      f"{avg:.0f} / {high}" if avg is not None else "—")
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(f"{session.started_at:%Y-%m-%d %H:%M:%S} · {session.device_name or source}")
                self.tree.setItem(row, col, item)
        self.tree.blockSignals(False)

    def on_view_last_record(self) -> None:
        self._tabbar.select(1)
        for row, session in enumerate(self._sessions):
            if session.id == self._last_session_id:
                self.tree.selectRow(row)
                self.tree.scrollToItem(self.tree.item(row, 0))
                break

    def on_history_select(self) -> None:
        rows = self.tree.selectionModel().selectedRows()
        if not rows:
            return
        index = rows[0].row()
        if index >= len(self._sessions):
            return
        session = self._sessions[index]
        self._selected_session_id = session.id

        samples = self.store.session_samples(session.id)
        stats = self.engine.session_stats(session.id)
        self._history_summary.setText(
            f"记录 #{session.id} · {session.started_at:%Y-%m-%d %H:%M:%S}"
            f"{'' if session.ended_at else '（进行中）'}\n" + self._format_stats(stats)
        )

        self.history_chart.reset_view()
        valid_samples = [sample for sample in samples if sample.bpm > 0 and sample.contact is not False]
        self.history_chart.set_points(
            [(sample.t_offset, sample.bpm) for sample in valid_samples],
            axis="elapsed",
            sample_times=[sample.ts for sample in valid_samples],
        )
        source = SOURCE_LABELS.get(session.source, session.source)
        self._history_summary.setText(f"{session.started_at:%Y-%m-%d  %H:%M}  ·  {source}\n"
                                      + self._format_stats(stats))

    @staticmethod
    def _format_stats(stats) -> str:
        if stats is None or not stats.sample_count:
            return "没有采样数据。"

        def num(value, digits: int = 0) -> str:
            return "--" if value is None else f"{value:.{digits}f}"

        lines = [
            f"时长 {stats.duration_text} · 采样 {stats.sample_count} 条"
            + (f"（无读数 {stats.dropped_sample_count} 条）" if stats.dropped_sample_count else ""),
            f"平均 {num(stats.avg_bpm, 1)} bpm · 最高 {num(stats.max_bpm)} · "
            f"最低 {num(stats.min_bpm)}",
        ]
        return "\n".join(lines)

    # ==================================================================
    # 关闭
    # ==================================================================

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._close_ready and (self.engine.is_started or self.engine.is_recording):
            event.ignore()
            if self._closing:
                return
            if self.engine.is_recording and not _Dialogs.ask(
                self, APP_NAME, "正在记录中，保存本次记录并退出吗？\n下次打开可在「回看」中查看。"
            ):
                return
            self._closing = True
            self.setEnabled(False)
            self._status_label.setText("正在保存记录并退出…")
            try:
                self.engine.request_shutdown()
            except Exception as exc:  # noqa: BLE001
                self._handle_event("shutdown_failed", {"message": str(exc)})
            return
        self._closed = True
        self._room_client.leave()
        self._room_overlay.close()
        self._overlay_hotkey.close()
        self._overlay.close()
        self._overlay_preview_window.close()
        self._poll_timer.stop()
        super().closeEvent(event)


class SettingsDialog(QWidget):
    """心率区间设置。"""

    def __init__(self, parent: QWidget, store: HeartRateStore, *, on_saved=None) -> None:
        super().__init__(parent, Qt.WindowType.Dialog)
        self.store = store
        self.on_saved = on_saved
        self.setWindowTitle("设置")
        self.setFixedWidth(420)
        self.setStyleSheet("background: #FBF8FA;")

        from ...metrics import DEFAULT_AGE, estimate_max_hr

        age = store.get_int_setting("age", DEFAULT_AGE)
        max_hr = store.get_int_setting("max_hr", 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        from PySide6.QtWidgets import QLineEdit

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("年龄（用于推算最大心率）"))
        self._age_edit = QLineEdit(str(age))
        self._age_edit.setFixedWidth(80)
        row1.addWidget(self._age_edit)
        row1.addStretch(1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("最大心率（留空 = 用 220-年龄）"))
        self._max_edit = QLineEdit(str(max_hr) if max_hr > 0 else "")
        self._max_edit.setFixedWidth(80)
        row2.addWidget(self._max_edit)
        row2.addStretch(1)
        layout.addLayout(row2)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color: {MUTED};")
        layout.addWidget(self._hint)

        db_label = QLabel(f"数据库：{store.db_path}")
        db_label.setWordWrap(True)
        db_label.setStyleSheet(f"color: {MUTED}; font-size: 8pt;")
        layout.addWidget(db_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = Button("取消")
        cancel.setFixedHeight(38)
        cancel.clicked.connect(self.close)
        buttons.addWidget(cancel)
        save = Button("保存", primary=True)
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        self._estimate = estimate_max_hr
        self._default_age = DEFAULT_AGE
        self._age_edit.setParent(self)
        self._update_hint()
        self._age_edit.textChanged.connect(self._update_hint)
        self._max_edit.textChanged.connect(self._update_hint)
        self.show()

    def _update_hint(self) -> None:
        try:
            age = int(float(self._age_edit.text() or self._default_age))
        except ValueError:
            age = self._default_age
        raw = self._max_edit.text().strip()
        if raw:
            try:
                self._hint.setText(f"当前使用最大心率 {int(float(raw))} bpm，各区间按它的百分比划分。")
                return
            except ValueError:
                pass
        self._hint.setText(
            f"按年龄 {age} 推算最大心率 ≈ {self._estimate(age)} bpm。\n"
            "区间：Z1 50-60% / Z2 60-70% / Z3 70-80% / Z4 80-90% / Z5 90%+"
        )

    def _save(self) -> None:
        try:
            age = int(float(self._age_edit.text() or self._default_age))
        except ValueError:
            _Dialogs.warn(self, "设置", "年龄必须是数字。")
            return
        raw = self._max_edit.text().strip()
        max_hr = 0
        if raw:
            try:
                max_hr = int(float(raw))
            except ValueError:
                _Dialogs.warn(self, "设置", "最大心率必须是数字，或留空。")
                return
        self.store.set_setting("age", str(age))
        self.store.set_setting("max_hr", str(max_hr))
        if self.on_saved is not None:
            self.on_saved()
        self.close()


def _choose_startup_database() -> Optional[Path]:
    from PySide6.QtCore import QSettings

    from ...paths import default_db_path, legacy_db_path, migrate_database

    target = default_db_path()
    source = legacy_db_path(target)
    if source is None:
        return target
    preferences = QSettings("HeartApp", "DatabaseMigration")
    # 用户可能在多个文件夹保留旧版，分别记住已处理的数据库。
    from hashlib import sha256

    key = sha256(str(source.resolve()).encode("utf-8")).hexdigest()
    if target.exists():
        if preferences.value(key, False, type=bool):
            return target
        if _Dialogs.ask(
            None, "发现两份记录",
            f"本次打开旧数据库？\n{source}\n\n"
            f"选择「否」则使用新位置，并记住此选择：\n{target}\n\n"
            "两份数据库会分别保留。",
        ):
            return source
        preferences.setValue(key, True)
        return target
    if not _Dialogs.ask(
        None, "发现旧版记录",
        f"从以下位置复制记录和设置？\n{source}\n\n"
        f"新位置：\n{target}\n\n"
        "选择「否」可退出，稍后再迁移。原数据库会保留。",
    ):
        return None
    try:
        migrate_database(source, target)
    except (OSError, sqlite3.Error) as exc:
        _Dialogs.error(None, "迁移失败", f"{exc}\n\n原数据库保留在：\n{source}")
        return None
    preferences.setValue(key, True)
    return target


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    import argparse

    parser = argparse.ArgumentParser(prog="heartapp-gui", description=f"{APP_NAME} 图形界面")
    parser.add_argument("--db", default=None)
    parser.add_argument("--simulate", action="store_true")
    args = parser.parse_args(argv)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)

    icon = icon_path()
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    db_path = Path(args.db) if args.db else _choose_startup_database()
    if db_path is None:
        return 0
    try:
        with recording_lock(db_path), closing(HeartRateStore(db_path)) as store:
            return _run_window(app, store, simulate=args.simulate)
    except RecordingAlreadyOpenError as exc:
        _Dialogs.warn(None, APP_NAME, str(exc))
        return 1


def _run_window(app: QApplication, store: HeartRateStore, *, simulate: bool) -> int:
    store.close_stale_sessions()
    engine = Engine(store)
    engine.start()

    window = HeartAppWindow(store, engine)
    window.show()

    if simulate:
        window._sim_chip.setChecked(True)
        window._on_sim_toggled()
        engine.connect_simulator(DEFAULT_PROFILE)

    try:
        code = app.exec()
    finally:
        engine.shutdown()
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
