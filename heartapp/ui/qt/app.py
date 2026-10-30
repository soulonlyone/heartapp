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
from .pc_panel import PCSessionPanel, PCReading, HeartOverlay, MarkerHotkey
from ...pc import session_baseline

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


class HeartAppWindow(QWidget):
    """主窗口。"""

    def __init__(self, store: HeartRateStore, engine: Engine, *, db_path: Optional[Path] = None) -> None:
        super().__init__()
        self.store = store
        self.engine = engine
        self.db_path = Path(db_path) if db_path else Path(store.db_path)

        # 运行态
        self._devices: List[object] = []
        self._live: Deque[Tuple[float, int]] = deque(maxlen=4000)
        self._last_sample = None
        self._last_reading_clock = None
        self._pc_markers = []
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

        self._backdrop_pixmap: Optional[QPixmap] = None
        self._backdrop_timer = QTimer(self)
        self._backdrop_timer.setSingleShot(True)
        self._backdrop_timer.timeout.connect(self._rebuild_backdrop)

        self.setWindowTitle(f"{APP_NAME} — PC 心率监控")
        self.resize(1160, 880)
        self.setMinimumSize(980, 700)

        self._build_ui()
        self._refresh_history()
        self._overlay = HeartOverlay(self)
        self._marker_hotkey = MarkerHotkey(lambda: self.engine.mark_event("快捷标记"))
        self._hotkey_hint.setText("Ctrl+Alt+F8 全局标记" if self._marker_hotkey.registered
                                 else "全局快捷键不可用，请使用标记按钮")

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(POLL_MS)

    # ==================================================================
    # 背景
    # ==================================================================

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._backdrop_timer.start(BACKDROP_DEBOUNCE_MS)

    def _rebuild_backdrop(self) -> None:
        size = self.size()
        if size.width() < 4 or size.height() < 4:
            return
        self._backdrop_pixmap = T.build_backdrop(size.width(), size.height())
        W.set_backdrop(self._backdrop_pixmap)
        # 新主题里卡片是纯白，不再采样背景，所以不需要通知它们重算
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        if self._backdrop_pixmap is not None and not self._backdrop_pixmap.isNull():
            painter.drawPixmap(0, 0, self._backdrop_pixmap)
        else:
            painter.fillRect(self.rect(), QColor(T.BACKDROP_STOPS[0][1]))

    # ==================================================================
    # 布局
    # ==================================================================

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 12)
        outer.setSpacing(0)

        # ---- 标题栏：直接浮在页面上，不做卡片 ----
        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 2, 0)
        head.setSpacing(12)

        title = QLabel(APP_NAME)
        title.setFont(T.spaced_font("appTitle", -0.15))
        title.setStyleSheet(f"color: {T.INK}; background: transparent;")
        head.addWidget(title)

        subtitle = QLabel("观察游戏与工作时的心率变化")
        subtitle.setFont(T.font("caption"))
        subtitle.setStyleSheet(f"color: {T.INK_3}; background: transparent;")
        head.addWidget(subtitle)
        head.addStretch(1)

        self._state_pill = W.StatusPill("未连接")
        head.addWidget(self._state_pill)

        head_holder = QWidget()
        head_holder.setLayout(head)
        outer.addWidget(head_holder)

        outer.addSpacing(16)
        outer.addWidget(W.Divider())
        outer.addSpacing(14)

        # ---- 分段控件 ----
        tabs = QHBoxLayout()
        tabs.setContentsMargins(0, 0, 0, 0)
        self._tabbar = W.SegmentedTabs(["实时监测", "历史记录"])
        self._tabbar.changed.connect(self._on_tab_changed)
        tabs.addWidget(self._tabbar)
        tabs.addStretch(1)
        tabs_holder = QWidget()
        tabs_holder.setLayout(tabs)
        outer.addWidget(tabs_holder)

        outer.addSpacing(16)

        # ---- 内容 ----
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        self._stack.addWidget(self._build_live_page())
        self._stack.addWidget(self._build_history_page())
        outer.addWidget(self._stack, 1)

        # ---- 页脚 ----
        foot = QHBoxLayout()
        foot.setContentsMargins(2, 0, 2, 0)
        db = QLabel(str(self.db_path))
        db.setFont(T.font("micro"))
        db.setStyleSheet(f"color: {T.INK_4}; background: transparent;")
        foot.addWidget(db)
        foot.addStretch(1)
        version = QLabel(f"v{__version__}")
        version.setFont(T.font("micro"))
        version.setStyleSheet(f"color: {T.INK_4}; background: transparent;")
        foot.addWidget(version)
        foot_holder = QWidget()
        foot_holder.setLayout(foot)
        outer.addWidget(foot_holder)

    def _on_tab_changed(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        if index == 1:
            self._refresh_history()

    # -- 实时页 ----------------------------------------------------------

    def _build_live_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)

        # ---- 连接行：安静的一条，不抢视觉 ----
        bar = QHBoxLayout()
        bar.setContentsMargins(2, 0, 2, 0)
        bar.setSpacing(8)

        cap = QLabel("设备")
        cap.setFont(T.font("caption"))
        cap.setStyleSheet(f"color: {T.INK_3}; background: transparent;")
        bar.addWidget(cap)

        self._device_combo = QComboBox()
        self._device_combo.setEditable(True)
        self._device_combo.setMinimumWidth(220)
        self._device_combo.setFixedHeight(34)
        self._device_combo.setStyleSheet(COMBO_QSS)
        self._device_combo.setFont(T.font("body"))
        self._device_combo.lineEdit().returnPressed.connect(self.on_connect_toggle)
        bar.addWidget(self._device_combo)

        self._scan_btn = W.M3Button("扫描设备", variant="secondary", height=34)
        self._scan_btn.clicked.connect(self.on_scan)
        bar.addWidget(self._scan_btn)

        self._connect_btn = W.M3Button("连接", variant="secondary", height=34, min_width=82)
        self._connect_btn.clicked.connect(self.on_connect_toggle)
        bar.addWidget(self._connect_btn)

        bar.addStretch(1)

        self._sim_chip = W.M3Chip("模拟模式", height=34)
        self._sim_chip.setChecked(False)
        self._sim_chip.clicked.connect(self._on_sim_toggled)
        bar.addWidget(self._sim_chip)

        scene_cap = QLabel("场景")
        scene_cap.setFont(T.font("caption"))
        scene_cap.setStyleSheet(f"color: {T.INK_3}; background: transparent;")
        bar.addWidget(scene_cap)

        self._profile_combo = QComboBox()
        self._profile_combo.addItems(list(PROFILES.values()))
        self._profile_combo.setMinimumWidth(176)
        self._profile_combo.setFixedHeight(34)
        self._profile_combo.setStyleSheet(COMBO_QSS)
        self._profile_combo.setFont(T.font("body"))
        bar.addWidget(self._profile_combo)

        settings = W.M3Button("高级", variant="text", height=34)
        settings.clicked.connect(self.on_settings)
        bar.addWidget(settings)

        bar_holder = QWidget()
        bar_holder.setLayout(bar)
        col.addWidget(bar_holder)

        self._status_label = QLabel("未连接。点「扫描设备」找手表，或打开「模拟模式」先试用。")
        self._status_label.setFont(T.font("caption"))
        self._status_label.setStyleSheet(
            f"color: {T.INK_3}; background: transparent; padding-left: 2px;"
        )
        col.addWidget(self._status_label)

        col.addSpacing(6)

        # ---- 表盘 + 本次记录 ----
        top = QHBoxLayout()
        top.setSpacing(6)

        gauge_card = W.Card(page, radius=T.RADIUS_CARD, padding=6)
        gauge_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.bpm_panel = PCReading()
        gauge_card.layout().addWidget(self.bpm_panel)
        top.addWidget(gauge_card, 5)

        stats_card = W.Card(page, radius=T.RADIUS_CARD, padding=20)
        stats_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sc = QVBoxLayout()
        sc.setContentsMargins(0, 0, 0, 0)
        sc.setSpacing(12)

        sc.addWidget(W.section_label("本次 PC 会话"))

        self._stat_values: Dict[str, QLabel] = {}
        stats_row = QHBoxLayout()
        stats_row.setSpacing(8)
        for label, key in (
            ("时长", "duration"), ("采样", "count"), ("平均", "avg"),
            ("最高", "max"), ("最低", "min"),
        ):
            cell = QVBoxLayout()
            cell.setSpacing(3)
            caption = QLabel(label)
            caption.setFont(T.font("caption"))
            caption.setStyleSheet(f"color: {T.INK_3}; background: transparent;")
            value = QLabel("--")
            value.setFont(T.font_px(22, QFont.Weight.DemiBold))
            value.setStyleSheet(f"color: {T.INK}; background: transparent;")
            self._stat_values[key] = value
            cell.addWidget(caption)
            cell.addWidget(value)
            holder_cell = QWidget()
            holder_cell.setLayout(cell)
            stats_row.addWidget(holder_cell, 1)
        sc.addLayout(stats_row)

        sc.addSpacing(2)
        sc.addWidget(W.Divider())

        self.zone_bar = PCSessionPanel()
        self.zone_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sc.addWidget(self.zone_bar, 1)

        holder_stats = QWidget()
        holder_stats.setLayout(sc)
        stats_card.layout().addWidget(holder_stats)
        top.addWidget(stats_card, 4)

        col.addLayout(top, 3)

        # ---- 趋势图 ----
        chart_card = W.Card(page, radius=T.RADIUS_CARD, padding=18)
        chart_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.live_chart = W.TrendChart(title="实时心率 · 最近 5 分钟")
        chart_card.layout().addWidget(self.live_chart)
        col.addWidget(chart_card, 4)

        context = QHBoxLayout()
        self._activity_combo = QComboBox()
        self._activity_combo.addItems(["游戏", "工作", "其他"])
        self._activity_combo.setStyleSheet(COMBO_QSS)
        context.addWidget(self._activity_combo)
        self._session_title = QLineEdit()
        self._session_title.setPlaceholderText("会话名称，例如：晚间排位 / 编程")
        self._session_title.setMaxLength(120)
        context.addWidget(self._session_title, 1)
        self._event_text = QLineEdit()
        self._event_text.setPlaceholderText("事件，例如：开局 / 团战 / 结束")
        self._event_text.setMaxLength(120)
        input_style = f"QLineEdit {{ background: white; color: {T.INK}; border: 1px solid {T.OUTLINE}; border-radius: 8px; padding: 7px; }} QLineEdit:disabled {{ color: {T.INK_3}; }}"
        self._event_text.setStyleSheet(input_style)
        self._session_title.setStyleSheet(input_style)
        self._event_text.returnPressed.connect(self.on_mark_event)
        context.addWidget(self._event_text, 1)
        mark = W.M3Button("标记事件", variant="secondary", height=34)
        mark.clicked.connect(self.on_mark_event)
        context.addWidget(mark)
        col.addLayout(context)
        self._hotkey_hint = QLabel("Ctrl+Alt+F8 全局标记")
        self._hotkey_hint.setFont(T.font("micro"))
        self._hotkey_hint.setStyleSheet(f"color: {T.INK_3};")
        col.addWidget(self._hotkey_hint)

        # ---- 操作行 ----
        actions = QHBoxLayout()
        actions.setContentsMargins(2, 4, 2, 0)
        actions.setSpacing(10)

        self._record_btn = W.M3Button("开始记录", variant="primary", height=42)
        self._record_btn.setMinimumWidth(126)
        self._record_btn.clicked.connect(self.on_record_toggle)
        actions.addWidget(self._record_btn)

        self._record_label = QLabel("未记录")
        self._record_label.setFont(T.font("caption"))
        self._record_label.setStyleSheet(f"color: {T.INK_3}; background: transparent;")
        actions.addWidget(self._record_label)
        actions.addStretch(1)
        overlay_btn = W.M3Button("心率小窗", variant="secondary", height=38)
        overlay_btn.clicked.connect(self.on_overlay_toggle)
        actions.addWidget(overlay_btn)

        export_btn = self._export_button(variant="text", height=38, current=True)
        actions.addWidget(export_btn)

        open_btn = W.M3Button("打开数据目录", variant="text", height=38)
        open_btn.clicked.connect(self.on_open_data_dir)
        actions.addWidget(open_btn)

        actions_holder = QWidget()
        actions_holder.setLayout(actions)
        col.addWidget(actions_holder)

        return page

    # -- 历史页 ----------------------------------------------------------

    def _build_history_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        left = W.Card(page, radius=T.RADIUS_CARD, padding=16)
        lc = QVBoxLayout()
        lc.setContentsMargins(0, 0, 0, 0)
        lc.setSpacing(12)
        lc.addWidget(W.section_label("记录列表"))

        self.tree = QTableWidget(0, len(TREE_HEADERS))
        self.tree.setHorizontalHeaderLabels(list(TREE_HEADERS))
        self.tree.verticalHeader().setVisible(False)
        self.tree.setShowGrid(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setStyleSheet(TABLE_QSS)
        header = self.tree.horizontalHeader()
        header.setStretchLastSection(False)
        for i, width in enumerate(TREE_WIDTHS):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            self.tree.setColumnWidth(i, width)
        # 只让「设备」列吸收多余宽度，其余列保持紧凑
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.verticalHeader().setDefaultSectionSize(32)
        self.tree.itemSelectionChanged.connect(self.on_history_select)
        lc.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        refresh = W.M3Button("刷新", variant="secondary", height=34)
        refresh.clicked.connect(self._refresh_history)
        buttons.addWidget(refresh)
        export_btn = self._export_button(variant="secondary", height=34, current=False)
        buttons.addWidget(export_btn)
        delete = W.M3Button("删除", variant="text", height=34)
        delete.clicked.connect(self.on_delete_selected)
        buttons.addWidget(delete)
        buttons.addStretch(1)
        buttons_holder = QWidget()
        buttons_holder.setLayout(buttons)
        lc.addWidget(buttons_holder)

        left_holder = QWidget()
        left_holder.setLayout(lc)
        left.layout().addWidget(left_holder)
        row.addWidget(left, 5)

        right = W.Card(page, radius=T.RADIUS_CARD, padding=20)
        rc = QVBoxLayout()
        rc.setContentsMargins(0, 0, 0, 0)
        rc.setSpacing(12)

        self._history_summary = QLabel("选择左侧的一次记录查看详情。")
        self._history_summary.setFont(T.font("body"))
        self._history_summary.setStyleSheet(f"color: {T.INK_2}; background: transparent;")
        self._history_summary.setWordWrap(True)
        rc.addWidget(self._history_summary)

        rc.addWidget(W.Divider())

        self.history_zone_bar = PCSessionPanel()
        self.history_zone_bar.setMinimumHeight(104)
        rc.addWidget(self.history_zone_bar)

        self.history_chart = W.TrendChart()
        self.history_chart.empty_text = "选中左侧一次记录后显示曲线"
        self.history_chart.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        rc.addWidget(self.history_chart, 1)

        right_holder = QWidget()
        right_holder.setLayout(rc)
        right.layout().addWidget(right_holder)
        row.addWidget(right, 6)

        return page

    # ==================================================================
    # 事件循环
    # ==================================================================

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
                self.engine.start_pc_recording(self._session_title.text(), self._activity_combo.currentText())

        elif kind == "sample":
            sample = payload.get("sample")
            if sample is None:
                return
            self._last_sample = sample
            if sample.bpm > 0 and sample.contact is not False:
                self._last_reading_clock = time.monotonic()
                self.zone_bar.sample(sample)
            if sample.bpm and sample.bpm > 0:
                self._live.append((time.monotonic(), int(sample.bpm)))
                if self._session_id is not None:
                    self._rec_sum += sample.bpm
                    self._rec_n += 1
                    self._rec_min = sample.bpm if self._rec_min is None else min(self._rec_min, sample.bpm)
                    self._rec_max = sample.bpm if self._rec_max is None else max(self._rec_max, sample.bpm)
            self.bpm_panel.set_state(
                bpm=sample.bpm if sample.bpm else None,
                max_hr=self._max_hr,
                contact=sample.contact,
            )
            self._chart_dirty = True

        elif kind == "marker":
            self.zone_bar.marker(payload["t_offset"], payload["label"])
            self._pc_markers.append((payload["t_offset"], payload["label"]))
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
            if message:
                _Dialogs.warn(self, APP_NAME, message)

        elif kind == "session_started":
            self._session_id = int(payload.get("session_id"))
            self._session_started = payload.get("started_at") or datetime.now()
            self.zone_bar.reset()
            self._pc_clock = time.monotonic()
            self._pc_markers = []
            self._activity_combo.setEnabled(False)
            self._session_title.setEnabled(False)
            self._sim_chip.setEnabled(False)
            self._profile_combo.setEnabled(False)
            self._device_combo.setEnabled(False)
            self._rec_sum = 0
            self._rec_n = 0
            self._rec_min = None
            self._rec_max = None
            self._record_btn.setText("停止记录")
            self._record_btn.set_variant("danger")
            self._record_label.setText(
                f"正在记录 · {self._session_started:%H:%M:%S} 开始"
            )
            self._update_state_pill()

        elif kind == "session_ended":
            self.zone_bar.active = False
            self._activity_combo.setEnabled(True)
            self._session_title.setEnabled(True)
            self._sim_chip.setEnabled(True)
            self._profile_combo.setEnabled(True)
            self._on_sim_toggled()
            stats = payload.get("stats")
            self._session_id = None
            self._session_started = None
            self._record_btn.setText("开始记录")
            self._record_btn.set_variant("filled")
            self._record_label.setText("未记录")
            self._update_state_pill()
            if stats is not None:
                self._history_summary.setText(self._format_stats(stats))
                _Dialogs.info(self, APP_NAME, "本次记录已保存。\n\n" + self._format_stats(stats))
            self._refresh_history()

    def _update_state_pill(self, state: Optional[str] = None) -> None:
        state = state if state is not None else self.engine.state
        if self.engine.is_recording:
            self._state_pill.set_state("记录中", T.STATE_RECORDING)
        elif state == "connected":
            self._state_pill.set_state("已连接", T.STATE_CONNECTED)
        elif state in ("connecting", "reconnecting", "scanning"):
            self._state_pill.set_state("连接中", T.STATE_CONNECTING)
        elif state == "error":
            self._state_pill.set_state("出错", T.STATE_ERROR)
        else:
            self._state_pill.set_state("未连接", T.STATE_IDLE)

    # ==================================================================
    # 交互
    # ==================================================================

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

    def on_mark_event(self) -> None:
        if not self.engine.is_recording:
            self._status_label.setText("请先开始 PC 会话，再标记事件。")
            return
        self.engine.mark_event(self._event_text.text().strip() or "标记")
        self._event_text.clear()

    def on_overlay_toggle(self) -> None:
        self._overlay.setVisible(not self._overlay.isVisible())

    def _update_pc_reading(self) -> None:
        fresh = (self._last_reading_clock is not None
                 and time.monotonic() - self._last_reading_clock <= 5
                 and self.engine.state == "connected")
        sample = self._last_sample
        bpm = sample.bpm if fresh and sample is not None and sample.contact is not False else None
        prefix = "模拟数据 · " if self.engine.source_kind == "sim" else ""
        baseline = self.zone_bar.baseline
        detail = "等待有效心率 / 读数已过期"
        if bpm:
            detail = (f"相对本次基线 {bpm - baseline:+.0f} bpm" if baseline is not None
                      else "本次基线尚不可用")
        self.zone_bar.change.setText(prefix + detail)
        self.bpm_panel.set_state(bpm=bpm)
        self._overlay.set_reading(bpm, prefix + detail)
        if not fresh and self._live:
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
        if not self.engine.is_running:
            if not _Dialogs.ask(
                self, APP_NAME,
                "还没有连接数据源。\n\n是否用模拟数据开始记录？",
            ):
                return
            self._sim_chip.setChecked(True)
            self._on_sim_toggled()
            self._pending_autorecord = True
            self.engine.connect_simulator(self._selected_profile_key())
            return
        self.engine.start_pc_recording(self._session_title.text(), self._activity_combo.currentText())

    def on_export_current(self, *, directory: Optional[Path] = None) -> None:
        if self._session_id is None:
            _Dialogs.info(self, APP_NAME, "当前没有正在记录或刚记录的会话。")
            return
        self._export_session(self._session_id, directory=directory)

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
            self.store.export_events_csv(session_id, target.with_name(target.stem + "_events.csv"))
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
        if not _Dialogs.ask(self, APP_NAME, "确定删除这条记录吗？此操作不可撤销。"):
            return
        self.store.delete_session(self._selected_session_id)
        self._selected_session_id = None
        self._history_summary.setText("选择左侧的一次记录查看详情。")
        self.history_chart.clear()
        self.history_zone_bar.events.clear()
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
        cutoff = now - LIVE_WINDOW_S
        points = [(t - now, bpm) for t, bpm in self._live if t >= cutoff]
        self.live_chart.markers = [(self._pc_clock + t - now, label) for t, label in self._pc_markers]
        self.live_chart.set_points(points, window=LIVE_WINDOW_S, axis="live", max_hr=None)

    def _update_stat_labels(self) -> None:
        if self._session_started is not None:
            elapsed = max(0.0, time.monotonic() - self._pc_clock)
        else:
            elapsed = 0.0

        if self._rec_n > 0:
            avg = self._rec_sum / self._rec_n
            values = {
                "duration": format_duration(elapsed) if self._session_started else "--",
                "count": str(self._rec_n),
                "avg": f"{avg:.0f}",
                "max": str(self._rec_max) if self._rec_max else "--",
                "min": str(self._rec_min) if self._rec_min else "--",
            }
        elif self._session_started is None and self._live:
            recent = [b for _, b in list(self._live)[-60:]]
            values = {
                "duration": "--",
                "count": str(len(self._live)),
                "avg": f"{sum(recent) / len(recent):.0f}" if recent else "--",
                "max": str(max(recent)) if recent else "--",
                "min": str(min(recent)) if recent else "--",
            }
        else:
            values = {key: "--" for key in self._stat_values}
            if self._session_started is not None:
                values["duration"] = format_duration(elapsed)

        for key, label in self._stat_values.items():
            new = values.get(key, "--")
            if label.text() != new:
                label.setText(new)

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
            duration = format_duration(session.duration_s) if session.ended_at else "进行中"
            context = self.store.pc_context(session.id)
            values = (
                f"{session.started_at:%Y-%m-%d %H:%M:%S}",
                duration,
                (context["title"] if context else session.device_name or "-")[:40],
                SOURCE_LABELS.get(session.source, session.source),
                str(count),
                f"{avg:.0f}" if avg else "--",
                str(high) if high else "--",
            )
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
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

        step = max(1, len(samples) // 1500)
        thinned = samples[::step] if step > 1 else samples
        points = [(s.t_offset, s.bpm) for s in thinned if s.bpm > 0]
        self.history_chart.set_points(
            points, axis="elapsed", max_hr=None,
            title=f"心率曲线（{len(samples)} 个采样点"
                  + (f"，图中抽取 {len(points)} 点" if step > 1 else "") + "）",
        )
        context = self.store.pc_context(session.id)
        if context:
            self._history_summary.setText(f"{context['activity']} · {context['title']}\n" + self._history_summary.text())
        baseline = session_baseline(samples) if session.duration_s >= 30 else None
        self.history_zone_bar.change.setText("事件与心率使用相同的相对时间轴")
        self.history_zone_bar.reference.setText(
            f"本次基线 {baseline:.0f} bpm" if baseline is not None else "本次基线不可用")
        self.history_zone_bar.events.clear()
        events = self.store.session_events(session.id)
        for event in events:
            self.history_zone_bar.marker(event["t_offset"], event["label"])
        self.history_chart.markers = [(e["t_offset"], e["label"]) for e in events]
        self.history_chart.update()

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
        if self.engine.is_recording:
            if not _Dialogs.ask(
                self, APP_NAME, "正在记录中，确定退出吗？\n本次记录会被保存。"
            ):
                event.ignore()
                return
        self._closed = True
        self._marker_hotkey.close()
        self._overlay.close()
        self._poll_timer.stop()
        try:
            self.engine.shutdown()
        except Exception:  # noqa: BLE001
            pass
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
        try:
            engine.shutdown()
        except Exception:  # noqa: BLE001
            pass
        store.close()
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
