"""Qt 版主界面。

架构和 Tkinter 版一致：界面只通过 ``engine.poll_events()`` 拿事件，
所有蓝牙/异步操作都在引擎自己的线程里，界面线程不碰 asyncio。

窗口的 paintEvent 画背景壁纸，三块玻璃面板是它的子控件；
面板只画自己的圆角区域，圆角外透明 → 和背景无缝，投影也能正常溢出。
"""

from __future__ import annotations

import sys
import time
from collections import deque
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
from ...store import HeartRateStore
from ...engine import Engine
from . import theme as T
from . import widgets as W
from .editorial import GlobalHotkey, Overlay, PAPER, ensure_light_palette
from .editorial_layout import EditorialLayout
from .chrome import FramelessWindow

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
    border: 1px solid {T.OUTLINE};
    border-radius: 9px;
    padding: 2px 10px;
    color: {T.INK};
    min-height: 26px;
}}
QComboBox:hover {{ border-color: {T.BORDER_STRONG}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: #FFFFFF;
    border: 1px solid {T.BORDER};
    border-radius: 8px;
    selection-background-color: #F1F1F4;
    selection-color: {T.INK};
    color: {T.INK};
    outline: none;
    padding: 4px;
}}
"""

TABLE_QSS = f"""
QTableWidget {{
    background: #FFFFFF;
    alternate-background-color: #FBFBFC;
    border: none;
    color: {T.INK};
    gridline-color: transparent;
    selection-background-color: #F1F1F4;
    selection-color: {T.INK};
    outline: none;
}}
QHeaderView::section {{
    background: #FFFFFF;
    color: {T.INK_3};
    border: none;
    border-bottom: 1px solid {T.DIVIDER};
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
    def ask(cls, parent, title, text, *args, **kwargs) -> bool:
        if cls.quiet:
            return True
        return QMessageBox.question(parent, title, text) == QMessageBox.StandardButton.Yes


class HeartAppWindow(EditorialLayout, FramelessWindow):
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
        self._scan_running = False
        self._max_hr = self.engine.max_hr()
        self._closed = False
        self._closing = False
        self._close_ready = False

        self.setWindowTitle(f"{APP_NAME} — PC 心率监控")
        self.resize(1120, 780)
        self.setMinimumSize(920, 680)

        self._build_ui()
        self._refresh_history()
        self._overlay = Overlay(self.store)
        self._overlay.set_passthrough(self._passthrough.isChecked())
        self._overlay_hotkey = GlobalHotkey(self.on_overlay_toggle)
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
            if state == "connected" and self._pending_autorecord:
                self._pending_autorecord = False
                self.engine.start_pc_recording(datetime.now().strftime("游戏记录 %m-%d %H:%M"), "游戏")

        elif kind == "sample":
            sample = payload.get("sample")
            if sample is None:
                return
            self._last_sample = sample
            received = payload.get("received_at", time.monotonic())
            if sample.bpm > 0 and sample.contact is not False:
                self._last_reading_clock = received
                self._live.append((received, int(sample.bpm)))
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
            self._scan_btn.setEnabled(True)
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
            message = "记录尚未保存完成，已取消退出。请检查磁盘空间或文件权限后重试。\n\n" + payload.get("message", "")
            self._status_label.setText("保存失败，已取消退出；记录仍保留在内存中，请重试。")
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
            self._record_btn.setText("停止记录")
            self._record_btn.set_variant("danger")
            self._record_label.setText("●  模拟记录中" if self.engine.source_kind == "sim" else "●  记录中")
            self._update_state_pill()

        elif kind == "session_ended":
            self._sim_chip.setEnabled(True)
            self._profile_combo.setEnabled(True)
            self._on_sim_toggled()
            stats = payload.get("stats")
            self._session_id = None
            self._session_started = None
            self._record_btn.setText("开始记录")
            self._record_btn.set_variant("filled")
            self._record_label.setText("●  本次已保存")
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
        self._device_status.setText(texts.get(state, "●  连接手表"))

    def _selected_profile_key(self) -> str:
        label = self._profile_combo.currentText()
        for key, text in PROFILES.items():
            if text == label:
                return key
        return DEFAULT_PROFILE

    def _on_sim_toggled(self) -> None:
        simulated = self._sim_chip.isChecked()
        self._device_combo.setEnabled(not simulated)
        self._scan_btn.setEnabled(not simulated)

    def on_overlay_toggle(self) -> None:
        self._overlay.setVisible(not self._overlay.isVisible())

    def _set_overlay_opacity(self, value: int) -> None:
        self._overlay.setWindowOpacity(value / 100)
        self.store.set_setting("overlay_opacity", str(value))

    def _set_overlay_passthrough(self, enabled: bool) -> None:
        self._overlay.set_passthrough(enabled)
        self.store.set_setting("overlay_passthrough", "1" if enabled else "0")

    def _update_pc_reading(self) -> None:
        fresh = (self._last_reading_clock is not None
                 and time.monotonic() - self._last_reading_clock <= 5
                 and self.engine.state == "connected")
        sample = self._last_sample
        bpm = sample.bpm if fresh and sample and sample.contact is not False else None
        prefix = "模拟数据 · " if self.engine.source_kind == "sim" else ""
        detail = "实时心率" if bpm else "等待有效读数 / 读数已过期"
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
        self.engine.scan(timeout=8.0)

    def on_connect_toggle(self) -> None:
        if self.engine.is_running:
            self.engine.stop_source()
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
            self.engine.stop_recording()
            return
        if self.engine.state != "connected":
            self._status_label.setText("请先连接手表，或在连接面板选择模拟演示。")
            self._show_connection()
            return
        self.engine.start_pc_recording(datetime.now().strftime("游戏记录 %m-%d %H:%M"), "游戏")

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

    def _export_button(self, *, variant: str, height: int, current: bool) -> "W.M3Button":
        """带下拉菜单的导出按钮。

        明确给两条路：
        * 「另存为…」—— 弹系统保存对话框，自己选位置；
        * 「导出到数据目录」—— 完全不碰系统对话框，直接写进 exe 旁边的 data\，
          这条路径永远有写权限（数据库就在那）。
        """
        button = W.M3Button("导出 CSV", variant=variant, height=height)
        menu = QMenu(button)
        menu.setStyleSheet(
            f"""
            QMenu {{
                background: #FFFFFF;
                border: 1px solid {T.BORDER};
                border-radius: 10px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 8px 20px;
                border-radius: 6px;
                color: {T.INK};
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
        self.history_chart.set_points([(s.t_offset, s.bpm) for s in samples if s.bpm > 0 and s.contact is not False],
                                      axis="elapsed")
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
        self._overlay_hotkey.close()
        self._overlay.close()
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
        self._hint.setStyleSheet(f"color: {T.MUTED};")
        layout.addWidget(self._hint)

        db_label = QLabel(f"数据库：{store.db_path}")
        db_label.setWordWrap(True)
        db_label.setStyleSheet(f"color: {T.FAINT}; font-size: 8pt;")
        layout.addWidget(db_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = W.M3Button("取消", variant="text", height=38)
        cancel.clicked.connect(self.close)
        buttons.addWidget(cancel)
        save = W.M3Button("保存", variant="filled", height=38)
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


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    import argparse

    parser = argparse.ArgumentParser(prog="heartapp-gui", description=f"{APP_NAME} 图形界面")
    parser.add_argument("--db", default=None)
    parser.add_argument("--simulate", action="store_true")
    args = parser.parse_args(argv)

    from ...paths import default_db_path

    db_path = Path(args.db) if args.db else default_db_path()

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)

    icon = icon_path()
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    store = HeartRateStore(db_path)
    store.close_stale_sessions()
    engine = Engine(store)
    engine.start()

    window = HeartAppWindow(store, engine, db_path=db_path)
    window.show()

    if args.simulate:
        window._sim_chip.setChecked(True)
        window._on_sim_toggled()
        engine.connect_simulator(DEFAULT_PROFILE)

    try:
        code = app.exec()
    finally:
        engine.shutdown()
        store.close()
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
