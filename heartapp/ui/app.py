"""Tkinter 主界面。

线程安全约定
------------
界面只通过两条通道和引擎打交道：

1. 调用 ``engine.xxx()``（引擎自己负责把活丢到它的循环线程）；
2. 每 120ms 用 ``engine.poll_events()`` 取事件。

界面的任何代码都不直接创建 asyncio 对象，也不在非主线程里碰 Tk 控件。
"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Deque, Dict, List, Optional, Tuple

from .. import APP_NAME, __version__
from ..ble import DiscoveredDevice
from ..engine import Engine
from ..metrics import format_duration
from ..simulator import DEFAULT_PROFILE, PROFILES
from ..store import HeartRateStore, SessionRow
from . import widgets as W

#: 实时趋势图显示最近多少秒
LIVE_WINDOW_S = 300.0
#: 实时曲线最多保留多少点（5 分钟 × 1Hz 绰绰有余，留点余量给高频广播）
LIVE_MAX_POINTS = 4000
#: 历史曲线最多画多少点
HISTORY_MAX_POINTS = 1500
#: 轮询间隔
POLL_MS = 120
#: 趋势图重绘间隔下限，太快会白烧 CPU
CHART_REDRAW_S = 0.5

TREE_COLUMNS = (
    ("started", "开始时间", 140),
    ("duration", "时长", 70),
    ("device", "设备", 150),
    ("source", "来源", 60),
    ("count", "样本", 60),
    ("avg", "平均", 60),
    ("max", "最高", 60),
    ("min", "最低", 60),
)

SOURCE_LABELS = {"ble": "手表", "sim": "模拟", "none": "-"}


class HeartAppFrame(ttk.Frame):
    """主界面。放进一个 ``tk.Tk`` 里即可运行。"""

    def __init__(
        self,
        master: tk.Misc,
        store: HeartRateStore,
        engine: Engine,
        *,
        db_path: Optional[Path] = None,
    ) -> None:
        super().__init__(master)
        self.store = store
        self.engine = engine
        self.db_path = Path(db_path) if db_path else Path(store.db_path)

        # 运行态
        self._devices: List[DiscoveredDevice] = []
        self._device_by_label: Dict[str, DiscoveredDevice] = {}
        self._live: Deque[Tuple[float, int]] = deque(maxlen=LIVE_MAX_POINTS)
        self._last_sample: Optional[Any] = None
        self._chart_dirty = False
        self._last_chart_draw = 0.0
        self._last_zone_update = 0.0

        # 当前记录
        self._session_id: Optional[int] = None
        self._session_started: Optional[datetime] = None
        self._rec_sum = 0
        self._rec_n = 0
        self._rec_min: Optional[int] = None
        self._rec_max: Optional[int] = None
        #: 记录过程中的区间时长累加（与最终统计同一套算法）
        self._rec_zones: Dict[str, float] = {}
        self._rec_last_point: Optional[Tuple[float, int]] = None

        # 历史
        self._sessions: List[SessionRow] = []
        self._selected_session_id: Optional[int] = None

        self._pending_autorecord = False
        self._scan_running = False
        self._last_error_shown = ""
        self._closed = False
        self._max_hr = self.engine.max_hr()

        self._stat_vars: Dict[str, tk.StringVar] = {}
        self._device_var = tk.StringVar()
        self._sim_var = tk.BooleanVar(value=False)
        self._profile_var = tk.StringVar(value=PROFILES[DEFAULT_PROFILE])
        self._status_var = tk.StringVar(value="未连接。点「扫描设备」找手表，或勾选「模拟模式」先试用。")
        self._record_var = tk.StringVar(value="未记录")
        self._connect_btn_text = tk.StringVar(value="连接")
        self._history_summary_var = tk.StringVar(value="选择左侧的一次记录查看详情。")
        self._status_fg = tk.StringVar(value=W.TEXT)
        self._state_pill: Optional[W.Pill] = None
        self._sim_chip: Optional[W.M3Chip] = None
        self._shell_size: Tuple[int, int] = (0, 0)
        self._surfaces_size: Tuple[int, int] = (-1, -1)

        self._build_style()
        self._build_ui()
        self._refresh_history()
        self._update_stat_labels()
        self.after(POLL_MS, self._poll)

    # ==================================================================
    # 样式与布局
    # ==================================================================

    def _build_style(self) -> None:
        """只剩少数原生 ttk 控件需要设样式。

        按钮、选项卡、芯片、卡片、图表都是自绘控件，不走 ttk —— 这样才有
        胶囊形状和液态玻璃质感。
        """
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        base = W.pick_family(self, W.FONT_CANDIDATES, "TkDefaultFont")
        self._base_family = base

        style.configure(".", font=(base, 10))
        style.configure("TFrame", background="#FFFFFF")
        style.configure("TLabel", background="#FFFFFF", foreground=W.TEXT, font=(base, 10))

        # 下拉框：Material 3 outlined 风格
        style.configure(
            "TCombobox", padding=(10, 6), font=(base, 10),
            fieldbackground="#FFFFFF", background="#FFFFFF",
            bordercolor=W.OUTLINE, lightcolor="#FFFFFF", darkcolor="#FFFFFF",
            arrowcolor=W.MUTED, relief="flat", borderwidth=1,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#FFFFFF"), ("disabled", "#F2EEF1")],
            bordercolor=[("focus", W.PRIMARY)],
            arrowcolor=[("disabled", W.FAINT)],
        )
        self.option_add("*TCombobox*Listbox.font", (base, 10))
        self.option_add("*TCombobox*Listbox.selectBackground", W.PRIMARY_CONTAINER)
        self.option_add("*TCombobox*Listbox.selectForeground", W.ON_PRIMARY_CONTAINER)

        # 历史表格
        style.configure(
            "Treeview", background="#FFFFFF", fieldbackground="#FFFFFF",
            foreground=W.TEXT, rowheight=30, font=(base, 10), borderwidth=0,
            relief="flat", bordercolor="#FFFFFF", lightcolor="#FFFFFF", darkcolor="#FFFFFF",
        )
        style.configure(
            "Treeview.Heading", font=(base, 10, "bold"), background="#FFFFFF",
            foreground=W.MUTED, relief="flat", padding=(8, 8), borderwidth=0,
        )
        style.map("Treeview.Heading", background=[("active", "#F6F1F4")])
        style.map(
            "Treeview",
            background=[("selected", W.PRIMARY_CONTAINER)],
            foreground=[("selected", W.ON_PRIMARY_CONTAINER)],
        )

        style.configure(
            "Vertical.TScrollbar", background="#DCCFD6", troughcolor="#FFFFFF",
            bordercolor="#FFFFFF", arrowcolor=W.MUTED, relief="flat", arrowsize=12,
        )
        style.configure(
            "TEntry", padding=(10, 6), fieldbackground="#FFFFFF",
            bordercolor=W.OUTLINE, lightcolor="#FFFFFF", darkcolor="#FFFFFF",
        )

    def _build_ui(self) -> None:
        self.pack(fill="both", expand=True)

        # ---- 背景壁纸（液态玻璃要透出的那层）----
        self._backdrop = W.BackdropCanvas(self)
        self._backdrop.place(x=0, y=0, relwidth=1, relheight=1)

        # ---- 三块玻璃面板。面板之间的缝隙透出背景，这是「液态玻璃」的关键 ----
        self._header_card = W.GlassCard(self, radius=22, padding=12)
        self._body_card = W.GlassCard(self, radius=28, padding=12)
        self._footer_card = W.GlassCard(self, radius=16, padding=6)

        self._build_header(self._header_card.body)
        self._build_footer(self._footer_card.body)

        notebook = W.M3Tabs(self._body_card.body)
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook

        self._build_live_tab(notebook)
        self._build_history_tab(notebook)

        self.bind("<Configure>", self._layout_shell)

    def _build_header(self, parent: tk.Misc) -> None:
        left = W.SurfaceFrame(parent)
        left.pack(side="left")
        W.SurfaceLabel(
            left, text=APP_NAME, fg=W.TEXT, font=W.type_font(self, "headlineSmall"),
        ).pack(side="left")
        W.SurfaceLabel(
            left, text="从智能手表实时读取并记录", fg=W.MUTED,
            font=W.type_font(self, "bodyMedium"),
        ).pack(side="left", padx=(12, 0), pady=(7, 0))

        self._state_pill = W.Pill(parent, text="未连接")
        self._state_pill.pack(side="right")

    def _build_footer(self, parent: tk.Misc) -> None:
        W.SurfaceLabel(
            parent, text=str(self.db_path), fg=W.FAINT,
            font=W.type_font(self, "labelSmall"), anchor="w",
        ).pack(side="left", padx=(4, 0))
        W.SurfaceLabel(
            parent, text=f"v{__version__}", fg=W.FAINT,
            font=W.type_font(self, "labelSmall"), anchor="e",
        ).pack(side="right", padx=(0, 4))

    def _layout_shell(self, event: Optional[tk.Event] = None) -> None:
        """三块玻璃面板的定位。窗口变化时重排。"""
        w = int(event.width) if event is not None else self.winfo_width()
        h = int(event.height) if event is not None else self.winfo_height()
        if w < 120 or h < 160:
            return
        margin, gap = 18, 14
        header_h, footer_h = 62, 30
        inner_w = max(80, w - margin * 2)
        body_h = max(160, h - margin * 2 - header_h - footer_h - gap * 2)
        self._header_card.place(x=margin, y=margin, width=inner_w, height=header_h)
        self._body_card.place(
            x=margin, y=margin + header_h + gap, width=inner_w, height=body_h
        )
        self._footer_card.place(
            x=margin, y=margin + header_h + gap + body_h + gap,
            width=inner_w, height=footer_h,
        )
        self._shell_size = (w, h)

    # -- 实时监测页 ------------------------------------------------------

    def _build_live_tab(self, notebook: W.M3Tabs) -> None:
        page = notebook.new_page()
        notebook.add(page, text="实时监测")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=3, minsize=220)
        page.rowconfigure(3, weight=4, minsize=170)

        # --- 连接条 ---
        connect = W.TonalCard(page, radius=22, padding=12, tone=0.34, height=66)
        connect.grid(row=0, column=0, sticky="ew", pady=(2, 10))
        bar = connect.body

        W.SurfaceLabel(
            bar, text="手表", fg=W.MUTED, font=W.type_font(self, "labelMedium")
        ).pack(side="left")
        self._device_combo = ttk.Combobox(
            bar, textvariable=self._device_var, width=30, font=(self._base_family, 10)
        )
        self._device_combo.pack(side="left", padx=(8, 10))
        self._device_combo.bind("<Return>", lambda _e: self.on_connect_toggle())

        self._scan_btn = W.M3Button(bar, "扫描设备", self.on_scan,
                                    variant="outlined", height=36)
        self._scan_btn.pack(side="left", padx=(0, 8))

        self._connect_btn = W.M3Button(
            bar, "连接", self.on_connect_toggle, variant="filled", height=36, min_width=88
        )
        self._connect_btn.pack(side="left")

        W.M3Button(bar, "设置", self.on_settings, variant="text", height=36).pack(side="right")

        self._profile_combo = ttk.Combobox(
            bar, textvariable=self._profile_var, width=20, state="readonly",
            values=list(PROFILES.values()), font=(self._base_family, 10),
        )
        self._profile_combo.pack(side="right", padx=(8, 14))
        W.SurfaceLabel(
            bar, text="场景", fg=W.MUTED, font=W.type_font(self, "labelMedium")
        ).pack(side="right")
        self._sim_chip = W.M3Chip(
            bar, "模拟模式", variable=self._sim_var,
            command=self._on_sim_toggle, height=36,
        )
        self._sim_chip.pack(side="right", padx=(16, 14))

        # --- 状态行 ---
        W.SurfaceLabel(
            page, textvariable=self._status_var, fg=W.MUTED,
            font=W.type_font(self, "bodyMedium"), anchor="w", justify="left",
            wraplength=1020,
        ).grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 10))

        # --- 心率表盘 + 统计/区间 ---
        top = W.SurfaceFrame(page)
        top.grid(row=2, column=0, sticky="nsew")
        top.columnconfigure(0, weight=5, minsize=290)
        top.columnconfigure(1, weight=4, minsize=330)
        top.rowconfigure(0, weight=1)

        gauge_card = W.TonalCard(top, radius=26, padding=8, tone=0.62)
        gauge_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        gauge_card.body.rowconfigure(0, weight=1)
        gauge_card.body.columnconfigure(0, weight=1)
        self.bpm_panel = W.BpmGauge(gauge_card.body)
        self.bpm_panel.grid(row=0, column=0, sticky="nsew")

        right_card = W.TonalCard(top, radius=26, padding=16, tone=0.62)
        right_card.grid(row=0, column=1, sticky="nsew")
        rb = right_card.body
        for c in range(5):
            rb.columnconfigure(c, weight=1)
        # 区间条所在行给权重，让它吃掉剩余高度；否则多余空间跑到别处，
        # 区间条只剩几十像素，图例会被裁掉（踩过这个坑）。
        rb.rowconfigure(2, weight=1)

        W.SurfaceLabel(
            rb, text="本次记录", fg=W.TEXT, font=W.type_font(self, "titleMedium")
        ).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 8))

        # 5 个统计排成一行：两行会吃掉 60px，区间条就放不下了
        stats = (
            ("时长", "duration"), ("采样", "count"), ("平均", "avg"),
            ("最高", "max"), ("最低", "min"),
        )
        for idx, (label, key) in enumerate(stats):
            cell = W.SurfaceFrame(rb)
            cell.grid(row=1, column=idx, sticky="nw", padx=(0, 6))
            W.SurfaceLabel(
                cell, text=label, fg=W.MUTED, font=W.type_font(self, "labelMedium")
            ).pack(anchor="w")
            var = tk.StringVar(value="--")
            self._stat_vars[key] = var
            W.SurfaceLabel(
                cell, textvariable=var, fg=W.TEXT,
                font=(self._base_family, 16, "bold"),
            ).pack(anchor="w")

        self.zone_bar = W.ZoneBar(rb, height=124)
        self.zone_bar.grid(row=2, column=0, columnspan=5, sticky="nsew", pady=(12, 0))

        # --- 趋势图 ---
        chart_card = W.TonalCard(page, radius=26, padding=12, tone=0.62)
        chart_card.grid(row=3, column=0, sticky="nsew", pady=(10, 12))
        chart_card.body.rowconfigure(0, weight=1)
        chart_card.body.columnconfigure(0, weight=1)
        self.live_chart = W.TrendChart(
            chart_card.body, height=200,
            title=f"实时心率趋势 · 最近 {int(LIVE_WINDOW_S // 60)} 分钟",
        )
        self.live_chart.grid(row=0, column=0, sticky="nsew")

        # --- 操作条 ---
        actions = W.SurfaceFrame(page)
        actions.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        self._record_btn = W.M3Button(
            actions, "开始记录", self.on_record_toggle,
            variant="filled", height=44, padx=26,
        )
        self._record_btn.pack(side="left")
        W.SurfaceLabel(
            actions, textvariable=self._record_var, fg=W.MUTED,
            font=W.type_font(self, "bodyMedium"),
        ).pack(side="left", padx=16)
        W.M3Button(
            actions, "打开数据目录", self.on_open_data_dir, variant="outlined", height=40
        ).pack(side="right")
        W.M3Button(
            actions, "导出本次 CSV", self.on_export_current, variant="outlined", height=40
        ).pack(side="right", padx=10)

    # -- 历史记录页 ------------------------------------------------------

    def _build_history_tab(self, notebook: W.M3Tabs) -> None:
        page = notebook.new_page()
        notebook.add(page, text="历史记录")
        page.columnconfigure(0, weight=5, minsize=430)
        page.columnconfigure(1, weight=6, minsize=390)
        page.rowconfigure(0, weight=1)

        # 左：列表。tone 取高一点接近白色，好和里面的白色表格衔接
        left_card = W.TonalCard(page, radius=26, padding=14, tone=0.88)
        left_card.grid(row=0, column=0, sticky="nsew", padx=(2, 12), pady=(2, 2))
        lb = left_card.body
        lb.rowconfigure(1, weight=1)
        lb.columnconfigure(0, weight=1)

        W.SurfaceLabel(
            lb, text="记录列表", fg=W.TEXT, font=W.type_font(self, "titleMedium")
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        columns = [c[0] for c in TREE_COLUMNS]
        self.tree = ttk.Treeview(lb, columns=columns, show="headings", selectmode="browse")
        for key, title, width in TREE_COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="center", stretch=False)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(lb, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns", padx=(4, 0))
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        buttons = W.SurfaceFrame(lb)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        W.M3Button(buttons, "刷新", self._refresh_history,
                   variant="outlined", height=36).pack(side="left")
        W.M3Button(buttons, "导出 CSV", self.on_export_selected,
                   variant="outlined", height=36).pack(side="left", padx=8)
        W.M3Button(buttons, "删除", self.on_delete_selected,
                   variant="text", height=36).pack(side="left")

        # 右：详情
        right_card = W.TonalCard(page, radius=26, padding=16, tone=0.72)
        right_card.grid(row=0, column=1, sticky="nsew", pady=(2, 2))
        rb = right_card.body
        rb.columnconfigure(0, weight=1)
        rb.rowconfigure(2, weight=1)

        W.SurfaceLabel(
            rb, textvariable=self._history_summary_var, fg=W.TEXT,
            font=W.type_font(self, "bodyMedium"), anchor="w", justify="left",
            wraplength=470,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.history_zone_bar = W.ZoneBar(rb, height=130)
        self.history_zone_bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        self.history_chart = W.TrendChart(rb, height=200, title="")
        self.history_chart.empty_text = "选中左侧一次记录后显示曲线"
        self.history_chart.grid(row=2, column=0, sticky="nsew")

    # ==================================================================
    # 交互
    # ==================================================================

    def _selected_profile_key(self) -> str:
        label = self._profile_var.get()
        for key, text in PROFILES.items():
            if text == label:
                return key
        return DEFAULT_PROFILE

    def _on_sim_toggle(self) -> None:
        simulated = bool(self._sim_var.get())
        self._device_combo.configure(state="disabled" if simulated else "normal")
        self._scan_btn.set_enabled(not simulated)
        if self._sim_chip is not None:
            self._sim_chip.refresh()

    def on_scan(self) -> None:
        if self._scan_running:
            return
        if self._sim_var.get():
            messagebox.showinfo(APP_NAME, "当前是模拟模式，不需要扫描设备。\n点一下「模拟模式」取消勾选即可扫描真实手表。")
            return
        self._scan_running = True
        self._scan_btn.set_enabled(False)
        self.engine.scan(timeout=8.0)

    def on_connect_toggle(self) -> None:
        if self.engine.is_running:
            self.engine.stop_source()
            return
        if self._sim_var.get():
            self.engine.connect_simulator(self._selected_profile_key())
            return
        text = self._device_var.get().strip()
        if not text:
            messagebox.showinfo(
                APP_NAME,
                "请先点「扫描设备」选一个设备，或直接输入手表的蓝牙名称。\n\n"
                "⚠ 手表端请先打开「心率广播 / 心率分享」，否则连上也没用。",
            )
            return
        device = self._device_by_label.get(text)
        self.engine.connect_device(device if device is not None else text)

    def on_record_toggle(self) -> None:
        if self.engine.is_recording:
            self.engine.stop_recording()
            return

        if not self.engine.is_running:
            if not messagebox.askyesno(
                APP_NAME,
                "还没有连接数据源。\n\n是否用模拟数据开始记录？（之后可以随时接上真手表）",
            ):
                return
            self._sim_var.set(True)
            self._on_sim_toggle()
            self._pending_autorecord = True
            self._status_var.set("正在启动模拟数据源，随后自动开始记录…")
            self.engine.connect_simulator(self._selected_profile_key())
            return

        self.engine.start_recording()

    def on_export_current(self) -> None:
        if self._session_id is None:
            messagebox.showinfo(APP_NAME, "当前没有正在记录或刚记录的会话。\n可在「历史记录」页导出以往的记录。")
            return
        self._export_session(self._session_id)

    def on_export_selected(self) -> None:
        if self._selected_session_id is None:
            messagebox.showinfo(APP_NAME, "请先在左侧选择一次记录。")
            return
        self._export_session(self._selected_session_id)

    def _export_session(self, session_id: int) -> None:
        session = self.store.get_session(session_id)
        if session is None:
            messagebox.showwarning(APP_NAME, "这条记录已经不存在了。")
            return
        default_name = f"心率记录_{session.started_at:%Y%m%d_%H%M%S}.csv"
        path = filedialog.asksaveasfilename(
            title="导出为 CSV",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            initialdir=str(self.db_path.parent),
        )
        if not path:
            return
        try:
            target = self.store.export_csv(session_id, path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"导出失败：{exc}")
            return
        messagebox.showinfo(APP_NAME, f"已导出：\n{target}")

    def on_delete_selected(self) -> None:
        if self._selected_session_id is None:
            messagebox.showinfo(APP_NAME, "请先在左侧选择一次记录。")
            return
        if not messagebox.askyesno(APP_NAME, "确定删除这条记录吗？此操作不可撤销。"):
            return
        self.store.delete_session(self._selected_session_id)
        self._selected_session_id = None
        self._history_summary_var.set("选择左侧的一次记录查看详情。")
        self.history_chart.clear()
        self.history_zone_bar.set_zones({}, max_hr=self._max_hr)
        self._refresh_history()

    def on_open_data_dir(self) -> None:
        folder = self.db_path.parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{folder}"')
            else:
                os.system(f'xdg-open "{folder}"')
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning(APP_NAME, f"打不开目录：{exc}\n\n路径：{folder}")

    def on_settings(self) -> None:
        SettingsDialog(self, self.store, on_saved=self._on_settings_saved)

    def _on_settings_saved(self) -> None:
        self._max_hr = self.engine.max_hr()
        self._chart_dirty = True
        self._update_stat_labels()

    # ==================================================================
    # 事件循环
    # ==================================================================

    def _poll(self) -> None:
        """每 120ms 跑一次：收事件、更新界面。

        每一步都单独兜底。曾经踩过的坑：趋势图抛一次「bitmap not defined」，
        异常冒到外面，后面的统计刷新就再也不执行了，界面看起来「卡死」但又不报错。
        宁可某一块这一帧没画出来，也不能让整条轮询链断掉。
        """
        if self._closed:
            return
        try:
            for kind, payload in self.engine.poll_events():
                try:
                    self._handle_event(kind, payload)
                except Exception as exc:  # noqa: BLE001
                    self._note_ui_error(f"处理事件 {kind} 失败：{exc}")

            # 窗口尺寸变了 → 玻璃面板的磨砂色跟着变 → 所有「跟随底色」的控件重新取色，
            # 否则面板上会残留上一尺寸的色块
            if self._shell_size != self._surfaces_size:
                self._surfaces_size = self._shell_size
                W.refresh_surfaces(self)

            now = time.monotonic()
            if self._chart_dirty and (now - self._last_chart_draw) >= CHART_REDRAW_S:
                try:
                    self._draw_live_chart()
                except Exception as exc:  # noqa: BLE001
                    self._note_ui_error(f"绘制实时曲线失败：{exc}")
                finally:
                    # 无论成功失败都要推进计时，否则会每帧重试、疯狂刷屏
                    self._chart_dirty = False
                    self._last_chart_draw = now

            try:
                self._update_stat_labels()
            except Exception as exc:  # noqa: BLE001
                self._note_ui_error(f"刷新统计失败：{exc}")

            # 区间条在记录过程中要持续刷新，否则会一直停在「暂无数据」
            if self.engine.is_recording and (now - self._last_zone_update) >= 2.0:
                self._last_zone_update = now
                try:
                    self._zone_live_update()
                except Exception as exc:  # noqa: BLE001
                    self._note_ui_error(f"刷新区间失败：{exc}")
        finally:
            self.after(POLL_MS, self._poll)

    def _note_ui_error(self, message: str) -> None:
        if message != self._status_var.get():
            self._status_var.set("⚠ " + message)

    def _handle_event(self, kind: str, payload: Dict[str, Any]) -> None:
        if kind == "status":
            state = payload.get("state", "")
            detail = payload.get("detail", "")
            self._status_var.set(detail)
            self._update_state_pill(state)
            self.bpm_panel.set_state(
                bpm=self._last_sample.bpm if self._last_sample else None,
                max_hr=self._max_hr,
                contact=getattr(self._last_sample, "contact", None),
                # 顶部状态行已经写了完整细节，这里只放设备名，避免同一句话出现两遍
                subtitle=self.engine.device_name or detail,
                device_text="",
            )
            if state in ("connected", "connecting", "reconnecting"):
                self._connect_btn.set_text("断开")
            elif state in ("stopped", "idle", "error"):
                self._connect_btn.set_text("连接")
            if state == "connected" and self._pending_autorecord:
                self._pending_autorecord = False
                self.engine.start_recording()

        elif kind == "sample":
            sample = payload.get("sample")
            if sample is None:
                return
            self._last_sample = sample
            if sample.bpm and sample.bpm > 0:
                self._live.append((time.monotonic(), int(sample.bpm)))
                if self._session_id is not None:
                    self._rec_sum += sample.bpm
                    self._rec_n += 1
                    self._rec_min = sample.bpm if self._rec_min is None else min(self._rec_min, sample.bpm)
                    self._rec_max = sample.bpm if self._rec_max is None else max(self._rec_max, sample.bpm)
                    self._accumulate_zone(int(sample.bpm))
            self.bpm_panel.set_state(
                bpm=sample.bpm if sample.bpm else None,
                max_hr=self._max_hr,
                contact=sample.contact,
                subtitle=self.engine.device_name or self.engine.state_detail,
                device_text="",
            )
            self._chart_dirty = True

        elif kind == "devices":
            devices = payload.get("devices") or []
            self._scan_running = False
            if not self._sim_var.get():
                self._scan_btn.set_enabled(True)
            self._devices = list(devices)
            self._device_by_label = {d.label: d for d in devices}
            labels = [d.label for d in devices]
            self._device_combo.configure(values=labels)
            if labels and not self._device_var.get():
                heart = [d for d in devices if d.has_heart_rate]
                self._device_var.set((heart or devices)[0].label)

        elif kind == "error":
            message = payload.get("message", "")
            self._status_var.set(message)
            if message and message != self._last_error_shown:
                self._last_error_shown = message
                messagebox.showwarning(APP_NAME, message)

        elif kind == "session_started":
            self._session_id = int(payload.get("session_id"))
            self._session_started = payload.get("started_at") or datetime.now()
            self._rec_sum = 0
            self._rec_n = 0
            self._rec_min = None
            self._rec_max = None
            self._rec_zones = {}
            self._rec_last_point = None
            self._record_btn.configure(text="停止记录", style="Stop.TButton")
            self._record_var.set(
                f"正在记录 · {self._session_started:%H:%M:%S} 开始 · "
                f"来源 {SOURCE_LABELS.get(payload.get('source', ''), '-')}"
            )
            self._update_state_pill()
            self._zone_live_update()

        elif kind == "session_ended":
            stats = payload.get("stats")
            self._session_id = None
            self._session_started = None
            self._rec_zones = {}
            self._rec_last_point = None
            self._record_btn.configure(text="开始记录", style="Primary.TButton")
            self._record_var.set("未记录")
            self._update_state_pill()
            if stats is not None:
                self.zone_bar.set_zones(stats.zone_seconds, max_hr=stats.max_hr)
                self._history_summary_var.set(self._format_stats(stats))
                messagebox.showinfo(
                    APP_NAME,
                    "本次记录已保存。\n\n" + self._format_stats(stats),
                )
            self._refresh_history()

    def _update_state_pill(self, state: Optional[str] = None) -> None:
        """刷新顶部那个状态胶囊。"""
        if self._state_pill is None:
            return
        state = state if state is not None else self.engine.state
        if self.engine.is_recording:
            self._state_pill.set("记录中", fg="#FFFFFF", bg=W.STATE_RECORDING)
        elif state == "connected":
            self._state_pill.set("已连接", fg="#FFFFFF", bg=W.STATE_CONNECTED)
        elif state in ("connecting", "reconnecting", "scanning"):
            self._state_pill.set("连接中", fg="#FFFFFF", bg=W.STATE_CONNECTING)
        elif state == "error":
            self._state_pill.set("出错", fg="#FFFFFF", bg=W.STATE_ERROR)
        else:
            self._state_pill.set("未连接", fg="#FFFFFF", bg=W.STATE_IDLE)

    def _accumulate_zone(self, bpm: int) -> None:
        """把「上一个采样点 → 当前采样点」这段时间记到上一个点所在的区间。

        与 :func:`heartapp.metrics.time_in_zones` 保持同一套规则（含最长间隔封顶），
        这样进行中的显示和结束后的统计不会互相打架。
        """
        from ..metrics import MAX_GAP_S, zone_for_bpm

        now = time.monotonic()
        previous = self._rec_last_point
        if previous is not None:
            dt = now - previous[0]
            if 0 < dt <= MAX_GAP_S:
                zone = zone_for_bpm(previous[1], self._max_hr)
                self._rec_zones[zone] = self._rec_zones.get(zone, 0.0) + dt
        self._rec_last_point = (now, bpm)

    def _zone_live_update(self) -> None:
        """记录过程中刷新区间条。

        这里累加的是**真实的分段时长**（每个采样点到下一个采样点归入前一个点所在的
        区间），和会话结束后 ``metrics.time_in_zones`` 的算法一致。早期版本偷懒按
        平均心率归到单一区间，结果会把整段记录显示成「Z4 100%」，是错的。
        """
        if not self._rec_zones:
            self.zone_bar.set_zones({}, max_hr=self._max_hr, title="心率区间时长")
            return
        self.zone_bar.set_zones(
            dict(self._rec_zones), max_hr=self._max_hr, title="心率区间时长（进行中）"
        )

    def _draw_live_chart(self) -> None:
        now = time.monotonic()
        cutoff = now - LIVE_WINDOW_S
        points = [(t - now, bpm) for t, bpm in self._live if t >= cutoff]
        self.live_chart.set_points(
            points, window=LIVE_WINDOW_S, axis="live", max_hr=self._max_hr
        )

    def _update_stat_labels(self) -> None:
        if self._session_started is not None:
            elapsed = (datetime.now() - self._session_started).total_seconds()
        elif self._live:
            elapsed = 0.0
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
            values = {k: "--" for k in ("duration", "count", "avg", "max", "min")}
            if self._session_started is not None:
                values["duration"] = format_duration(elapsed)

        for key, var in self._stat_vars.items():
            new = values.get(key, "--")
            if var.get() != new:
                var.set(new)

    # ==================================================================
    # 历史记录
    # ==================================================================

    def _refresh_history(self) -> None:
        sessions = self.store.list_sessions(limit=500)
        aggregates = self.store.session_bpm_aggregate([s.id for s in sessions])
        self._sessions = sessions

        selected = self._selected_session_id
        for item in self.tree.get_children():
            self.tree.delete(item)

        for s in sessions:
            avg, _low, high, count = aggregates.get(s.id, (None, None, None, 0))
            duration = s.duration_s
            values = (
                f"{s.started_at:%Y-%m-%d %H:%M:%S}",
                format_duration(duration) if s.ended_at else "进行中",
                (s.device_name or "-")[:22],
                SOURCE_LABELS.get(s.source, s.source),
                str(count),
                f"{avg:.0f}" if avg else "--",
                str(high) if high else "--",
                str(_low) if _low else "--",
            )
            self.tree.insert("", "end", iid=str(s.id), values=values)

        if selected is not None and self.tree.exists(str(selected)):
            self.tree.selection_set(str(selected))

    def _on_tree_select(self, _event: object = None) -> None:
        try:
            self.on_history_select()
        except Exception as exc:  # noqa: BLE001
            self._note_ui_error(f"加载历史记录失败：{exc}")

    def on_history_select(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        session_id = int(selection[0])
        self._selected_session_id = session_id
        session = self.store.get_session(session_id)
        if session is None:
            return

        samples = self.store.session_samples(session_id)
        stats = self.engine.session_stats(session_id)
        self._history_summary_var.set(
            f"记录 #{session_id} · {session.started_at:%Y-%m-%d %H:%M:%S}"
            f"{'' if session.ended_at else '（进行中）'}\n" + self._format_stats(stats)
        )

        step = max(1, len(samples) // HISTORY_MAX_POINTS)
        thinned = samples[::step] if step > 1 else samples
        points = [(s.t_offset, s.bpm) for s in thinned if s.bpm > 0]
        self.history_chart.set_points(
            points,
            axis="elapsed",
            max_hr=stats.max_hr,
            title=f"心率曲线（{len(samples)} 个采样点"
                  + (f"，图中抽取 {len(points)} 点" if step > 1 else "")
                  + "）",
        )
        self.history_zone_bar.set_zones(
            stats.zone_seconds, max_hr=stats.max_hr, title="心率区间时长"
        )

    @staticmethod
    def _format_stats(stats: Any) -> str:
        if stats is None or not stats.sample_count:
            return "没有采样数据。"

        def num(value: Optional[float], digits: int = 0) -> str:
            if value is None:
                return "--"
            return f"{value:.{digits}f}"

        lines = [
            f"时长 {stats.duration_text} · 采样 {stats.sample_count} 条"
            + (f"（无读数 {stats.dropped_sample_count} 条）" if stats.dropped_sample_count else ""),
            f"平均 {num(stats.avg_bpm, 1)} bpm · 最高 {num(stats.max_bpm)} · "
            f"最低 {num(stats.min_bpm)} · 静息 {num(stats.resting_bpm, 1)}",
        ]
        if stats.rmssd_ms is not None:
            lines.append(f"HRV(RMSSD) {stats.rmssd_ms:.1f} ms（基于广播 RR 间期，仅供参考）")
        else:
            lines.append("HRV：本次未收到足够的 RR 间期，无法计算")
        return "\n".join(lines)

    # ==================================================================
    # 关闭
    # ==================================================================

    def close(self) -> None:
        """收尾：停数据源、结束记录、关数据库。"""
        if self._closed:
            return
        self.engine.shutdown()
        self._closed = True


# ---------------------------------------------------------------------------
# 设置对话框
# ---------------------------------------------------------------------------


class SettingsDialog(tk.Toplevel):
    """心率区间设置：年龄 → 最大心率，或者直接指定最大心率。"""

    def __init__(self, master: tk.Misc, store: HeartRateStore, *, on_saved=None) -> None:
        super().__init__(master)
        self.store = store
        self.on_saved = on_saved
        self.title("设置")
        dialog_bg = "#FBF8FA"
        self.configure(bg=dialog_bg)
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())

        from ..metrics import DEFAULT_AGE, estimate_max_hr

        age = store.get_int_setting("age", DEFAULT_AGE)
        max_hr = store.get_int_setting("max_hr", 0)

        self._age_var = tk.StringVar(value=str(age))
        self._max_hr_var = tk.StringVar(value=str(max_hr) if max_hr > 0 else "")
        self._hint_var = tk.StringVar()

        body = tk.Frame(self, bg=dialog_bg, padx=20, pady=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        label_font = W.type_font(self, "bodyMedium")
        tk.Label(body, text="年龄（用于推算最大心率）", bg=dialog_bg, fg=W.TEXT,
                 font=label_font).grid(row=0, column=0, sticky="w", pady=5)
        age_entry = ttk.Entry(body, textvariable=self._age_var, width=10)
        age_entry.grid(row=0, column=1, sticky="w", padx=(12, 0), pady=5)
        age_entry.bind("<KeyRelease>", lambda _e: self._update_hint())

        tk.Label(body, text="最大心率（留空 = 用 220-年龄）", bg=dialog_bg, fg=W.TEXT,
                 font=label_font).grid(row=1, column=0, sticky="w", pady=5)
        max_entry = ttk.Entry(body, textvariable=self._max_hr_var, width=10)
        max_entry.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=5)
        max_entry.bind("<KeyRelease>", lambda _e: self._update_hint())

        tk.Label(
            body, textvariable=self._hint_var, bg=dialog_bg, fg=W.MUTED,
            font=W.type_font(self, "labelMedium"), justify="left", wraplength=340,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 4))

        tk.Label(
            body, text=f"数据库：{self.store.db_path}", bg=dialog_bg, fg=W.FAINT,
            font=W.type_font(self, "labelSmall"), justify="left", wraplength=340,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 12))

        buttons = tk.Frame(body, bg=dialog_bg)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e")
        W.M3Button(buttons, "取消", self.destroy,
                   variant="text", height=38).pack(side="right", padx=(8, 0))
        W.M3Button(buttons, "保存", self._save,
                   variant="filled", height=38).pack(side="right")

        self._update_hint()
        self.grab_set()
        self.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self.destroy())

    def _update_hint(self) -> None:
        from ..metrics import DEFAULT_AGE, estimate_max_hr

        try:
            age = int(float(self._age_var.get() or DEFAULT_AGE))
        except (TypeError, ValueError):
            age = DEFAULT_AGE
        raw = self._max_hr_var.get().strip()
        if raw:
            try:
                value = int(float(raw))
                self._hint_var.set(f"当前使用最大心率 {value} bpm，各区间按它的百分比划分。")
                return
            except (TypeError, ValueError):
                self._hint_var.set("最大心率填的不是数字，将回退到按年龄推算。")
        self._hint_var.set(
            f"按年龄 {age} 推算最大心率 ≈ {estimate_max_hr(age)} bpm。\n"
            "区间：Z1 50-60% / Z2 60-70% / Z3 70-80% / Z4 80-90% / Z5 90%+"
        )

    def _save(self) -> None:
        from ..metrics import DEFAULT_AGE

        try:
            age = int(float(self._age_var.get() or DEFAULT_AGE))
        except (TypeError, ValueError):
            messagebox.showwarning("设置", "年龄必须是数字。", parent=self)
            return
        if not (5 <= age <= 110):
            messagebox.showwarning("设置", "年龄请填 5–110 之间的数字。", parent=self)
            return

        raw = self._max_hr_var.get().strip()
        max_hr = 0
        if raw:
            try:
                max_hr = int(float(raw))
            except (TypeError, ValueError):
                messagebox.showwarning("设置", "最大心率必须是数字，或留空。", parent=self)
                return
            if not (100 <= max_hr <= 230):
                messagebox.showwarning("设置", "最大心率请填 100–230 之间的数字。", parent=self)
                return

        self.store.set_setting("age", str(age))
        self.store.set_setting("max_hr", str(max_hr))
        if self.on_saved is not None:
            self.on_saved()
        self.destroy()


def main(argv: Optional[List[str]] = None) -> int:
    """界面入口。"""
    import argparse

    parser = argparse.ArgumentParser(prog="heartapp-gui", description=f"{APP_NAME} 图形界面")
    parser.add_argument("--db", default=None, help="数据库文件路径")
    parser.add_argument(
        "--simulate", action="store_true", help="启动后立刻用模拟数据源"
    )
    args = parser.parse_args(argv)

    from ..paths import default_db_path

    _enable_dpi_awareness()
    _set_app_user_model_id()

    db_path = Path(args.db) if args.db else default_db_path()
    store = HeartRateStore(db_path)
    store.close_stale_sessions()

    engine = Engine(store)
    engine.start()

    root = tk.Tk()
    root.title(f"{APP_NAME} — 从智能手表读取并记录心率")
    root.geometry("1160x880")
    root.minsize(980, 700)
    root.configure(bg=W.BACKDROP_STOPS[0][1])
    _apply_window_icon(root)

    frame = HeartAppFrame(root, store, engine, db_path=db_path)

    def _on_close() -> None:
        if engine.is_recording:
            if not messagebox.askyesno(APP_NAME, "正在记录中，确定退出吗？\n本次记录会被保存。"):
                return
        try:
            frame.close()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"记录保存失败，已取消退出。请排除问题后重试。\n\n{exc}")
            return
        store.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)

    if args.simulate:
        root.after(300, lambda: (frame._sim_var.set(True), frame._on_sim_toggle(),
                                 engine.connect_simulator(DEFAULT_PROFILE)))

    try:
        root.mainloop()
    finally:
        frame.close()
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass
    return 0


def _enable_dpi_awareness() -> None:
    """让界面在高分屏上不发虚。失败无所谓。"""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def _set_app_user_model_id() -> None:
    """告诉 Windows 这是独立应用。

    不做这一步的话，任务栏会把窗口归到 python.exe 下面，图标和名字都不对。
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            "HeartApp.HeartRateRecorder.1"
        )
    except Exception:  # noqa: BLE001
        pass


def _apply_window_icon(root: tk.Tk) -> None:
    """设置窗口图标（打包后从 exe 内解出的资源目录里取）。找不到就算了。"""
    try:
        from ..paths import icon_path

        path = icon_path()
        if path.exists():
            root.iconbitmap(default=str(path))
    except Exception:  # noqa: BLE001
        pass
