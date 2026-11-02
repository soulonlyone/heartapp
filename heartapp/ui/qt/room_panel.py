"""D 版联机页及独立多人浮窗。"""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QCheckBox, QHBoxLayout, QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from .editorial import Button, INK, MUTED, PAPER, RED, RULE, label, serif
from .editorial_layout import EditorialLayout
from ...room_client import RoomClient
from ...room_protocol import room_code, server_url
from ...room_history import RoomHistory, segments
from .comparison import COLORS, MemberLegend


class RoomActions:
    def _init_room(self) -> None:
        self._room_client = RoomClient()
        self._room_members = []
        self._room_self_id = ""
        self._room_code = ""
        self._room_active = False
        self._room_updated = 0.0
        self._room_history = RoomHistory()
        self._room_colors = {"self": COLORS[0]}
        self._room_legends = {}
        self._room_hidden = set()
        self._room_render_clock = 0.0
        self._room_notice = ""
        self._room_link_note = ""

    def _show_room_dialog(self) -> None:
        self._room_dialog.show()
        self._room_dialog.raise_()
        self._room_dialog.activateWindow()

    def _room_display_members(self) -> list:
        sample = self._last_sample
        fresh = (self._last_reading_clock is not None and time.monotonic() - self._last_reading_clock <= 5
                 and self.engine.state == "connected")
        own = dict(id="self", name=self._room_panel.name.text().strip() or "我",
                   bpm=sample.bpm if fresh and sample and sample.contact is not False else None,
                   sharing=self._room_panel.share.isChecked(), simulated=self.engine.source_kind == "sim")
        return [own] + [m for m in self._room_members if m.get("id") != self._room_self_id]

    def _sync_room_home(self) -> None:
        joined = bool(self._room_code)
        for widget in (self._room_record_area, self._room_legend_widget, self._room_stats, self._room_hint):
            widget.setVisible(joined)
        self._live_hero.setVisible(not joined)
        self._live_share.setEnabled(joined)
        self._chart_title.setText("共同心率曲线" if joined else "心率曲线")
        self._live_export_btn.setText("导出我的记录" if joined else "导出曲线")
        range_text = "本次联机（最多30分钟）" if joined else "本次全程"
        if self._live_range.itemText(2) != range_text:
            self._live_range.setItemText(2, range_text)
        if not joined:
            message = "正在连接房间…" if self._room_active else self._room_notice or "个人模式 · 可邀请朋友一起查看心率"
            self._live_room_status.setText(message)
            self._live_room_status.setToolTip(message)
            return
        count = len(self._room_members)
        message = f"房间 {self._room_code} · {count} / 4 人 · " + (self._room_link_note or ("正在共享" if self._room_panel.share.isChecked() else "我的心率仅本机可见"))
        self._live_room_status.setText(message)
        self._live_room_status.setToolTip(message)
        self._room_duration.setText("我的记录 " + self._stat_values["duration"].text())
        self._room_record_btn.setText("停止我的记录" if self.engine.is_recording else "记录我的心率")
        self._room_record_btn.setEnabled(self._record_btn.isEnabled())
        now = time.monotonic()
        if now - self._room_render_clock < .5:
            return
        self._room_render_clock = now
        members = self._room_display_members()
        if members[0]["bpm"] is None:
            self._room_history.append("self", now, None)
        keys = {m["id"] for m in members}
        for key in list(self._room_legends):
            if key not in keys:
                button = self._room_legends.pop(key)
                self._room_legend_layout.removeWidget(button)
                button.deleteLater()
                self._room_colors.pop(key, None)
                self._room_hidden.discard(key)
        for member in members:
            key = member["id"]
            if key not in self._room_colors:
                self._room_colors[key] = next((c for c in COLORS[1:] if c not in self._room_colors.values()), COLORS[1])
            if key not in self._room_legends:
                button = MemberLegend(key)
                button.toggled.connect(lambda checked, k=key: self._room_toggle_series(k, checked))
                self._room_legend_layout.addWidget(button, 1)
                self._room_legends[key] = button
            self._room_legends[key].set_member(member, self._room_colors[key])
        short = {key: [(t, b) for t, b in values if t >= now - 60]
                 for key, values in self._room_history.points.items()}
        self._room_overlay.set_members(members, "self", histories=short, colors=self._room_colors)

    def _room_toggle_series(self, key: str, checked: bool) -> None:
        self._room_hidden.discard(key) if checked else self._room_hidden.add(key)
        self._draw_live_chart()

    def _refresh_room_stats(self) -> None:
        self._room_stats.setText(self._room_chart.summary())

    def _refresh_room_graph(self, now: float) -> None:
        self._room_history.prune(now)
        window = self._live_range.currentData()
        if not window:
            window = min(1800, max(60, now - self._room_history.started))
        series = {}
        for member in self._room_display_members():
            key = member["id"]
            if key in self._room_hidden:
                continue
            if key not in self._room_colors:
                self._room_colors[key] = next((c for c in COLORS[1:] if c not in self._room_colors.values()), COLORS[1])
            series[key] = dict(name=str(member.get("name", "玩家")), color=self._room_colors.get(key, COLORS[0]),
                               points=[(t - now, b) for t, b in self._room_history.points.get(key, [])])
        self._room_chart.set_series(series, window)
        self._refresh_room_stats()

    def on_room_connect(self, *, create: bool) -> None:
        panel = self._room_panel
        try:
            address = server_url(panel.address.text())
            code = "" if create else room_code(panel.code.text())
            self._room_client.connect(address, panel.name.text(), code=code, password=panel.password.text())
            self.store.set_setting("room_server", address)
            self.store.set_setting("room_name", panel.name.text().strip())
        except (ValueError, OSError) as exc:
            panel.status.setText(str(exc))
            return
        self._room_active = True
        self._room_notice = ""
        panel.share.setChecked(False)
        panel.set_active(True)
        panel.status.setText("正在连接服务器…")

    def on_room_leave(self) -> None:
        self._room_client.leave()
        self._room_active = False
        self._room_link_note = ""
        self._room_notice = "已离开房间；本地记录继续"
        self._room_history.clear()
        self._room_chart.set_series({}, 300)
        self._room_chart.reset_view()
        self._room_members, self._room_self_id, self._room_code = [], "", ""
        self._room_panel.share.setChecked(False)
        self._room_panel.set_active(False)
        self._room_panel.set_members([], "")
        self._room_panel.status.setText("已离开房间；本地记录继续")
        self._room_overlay.set_members([], "")
        self._room_overlay.hide()
        for button in self._room_legends.values():
            self._room_legend_layout.removeWidget(button)
            button.deleteLater()
        self._room_legends.clear()
        self._room_colors = {"self": COLORS[0]}
        self._room_hidden.clear()
        self._sync_room_home()
        self._draw_live_chart()

    def on_room_copy(self) -> None:
        if self._room_code:
            QApplication.clipboard().setText(
                f"HeartApp 联机邀请\n服务器：{server_url(self._room_panel.address.text())}\n房间码：{self._room_code}"
            )
            self._room_panel.status.setText("已复制服务器地址和房间码，请发给朋友。访问密码需另行告知。")

    def on_room_overlay_toggle(self) -> None:
        self._room_overlay.setVisible(not self._room_overlay.isVisible())

    def _toggle_active_overlay(self) -> None:
        if self._room_active or self._room_overlay.isVisible():
            self.on_room_overlay_toggle()
        else:
            self.on_overlay_toggle()

    def _publish_room_reading(self) -> None:
        fresh = (self._last_reading_clock is not None and time.monotonic() - self._last_reading_clock <= 5
                 and self.engine.state == "connected")
        sample = self._last_sample
        bpm = sample.bpm if fresh and sample and sample.contact is not False else None
        self._room_client.publish(bpm, sharing=self._room_active and self._room_panel.share.isChecked(),
                                  simulated=self.engine.source_kind == "sim")

    def _poll_room(self) -> None:
        for kind, payload in self._room_client.poll_events():
            if kind == "room_error":
                self.on_room_leave()
                self._room_notice = payload["message"]
                self._room_panel.status.setText(payload["message"])
            elif kind == "room_status":
                self._room_link_note = payload["message"]
                self._room_panel.status.setText(payload["message"])
                if payload.get("clear"):
                    self._room_history.gap(time.monotonic())
                    self._room_members = []
                    self._room_panel.set_members([], self._room_self_id)
                    self._room_overlay.set_members([], self._room_self_id)
            elif kind == "room_welcome":
                if self._room_code != payload["room"]:
                    self._room_history.clear(time.monotonic())
                    self._room_chart.reset_view()
                self._room_code = payload["room"]
                self._room_link_note = ""
                self._room_self_id = payload["self_id"]
                self._room_panel.code.setText(self._room_code)
                self._room_panel.set_active(True, joined=True)
                self._room_dialog.hide()
                self._tabbar.select(0)
                self._room_panel.status.setText(f"房间 {self._room_code} · 已加入，按需开启共享")
            elif kind == "room_snapshot":
                self._room_updated = time.monotonic()
                self._room_link_note = ""
                self._room_members = payload["members"]
                self._room_history.snapshot(self._room_members, self._room_self_id, self._room_updated)
                self._room_panel.set_members(self._room_members, self._room_self_id)
        if self._room_members and time.monotonic() - self._room_updated > 5:
            if any(m.get("bpm") is not None for m in self._room_members):
                self._room_members = [dict(m, bpm=None) for m in self._room_members]
                self._room_history.gap(time.monotonic())
                self._room_panel.set_members(self._room_members, self._room_self_id)
        if self._room_code:
            self._chart_dirty = True


class RoomPanel(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window_owner = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 10)
        layout.setSpacing(12)
        title = label("一起，看见心跳")
        title.setFont(serif(32))
        layout.addWidget(title)
        note = label("连接你或朋友部署的服务器，创建一个最多 4 人的私人房间。", 12, True)
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QHBoxLayout()
        left, right = QVBoxLayout(), QVBoxLayout()
        left.addWidget(label("服务器地址", 12))
        self.address = QLineEdit(window.store.get_setting("room_server", "ws://127.0.0.1:8765"))
        self.address.setPlaceholderText("https://hr.example.com 或 ws://IP:端口")
        left.addWidget(self.address)
        right.addWidget(label("你的昵称", 12))
        self.name = QLineEdit(window.store.get_setting("room_name", "玩家"))
        self.name.setMaxLength(24)
        right.addWidget(self.name)
        form.addLayout(left, 2)
        form.addLayout(right, 1)
        layout.addLayout(form)
        access = QHBoxLayout()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("服务器访问密码（部署者设置了才需要填写）")
        access.addWidget(self.password, 2)
        self.code = QLineEdit()
        self.code.setPlaceholderText("8 位房间码")
        self.code.setMaxLength(12)
        access.addWidget(self.code, 1)
        layout.addLayout(access)
        controls = QHBoxLayout()
        self.create = Button("创建房间", primary=True)
        self.join = Button("加入房间")
        self.leave = Button("离开房间")
        self.copy = Button("复制邀请")
        self.create.clicked.connect(lambda: window.on_room_connect(create=True))
        self.join.clicked.connect(lambda: window.on_room_connect(create=False))
        self.leave.clicked.connect(window.on_room_leave)
        self.copy.clicked.connect(window.on_room_copy)
        for button in (self.create, self.join, self.leave, self.copy):
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        self.share = QCheckBox("共享我的实时心率（含模拟标识）")
        self.share.setChecked(False)
        self.share.toggled.connect(lambda _: window._publish_room_reading())
        layout.addWidget(self.share)
        privacy = label("共享默认关闭。房间成员可临时查看最近30分钟共享曲线，服务器运营者可看到读数。队友曲线不写入历史；公网建议使用 HTTPS / WSS。", 11, True)
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        layout.addWidget(EditorialLayout._rule())
        self.status = label("尚未加入房间", 13, True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["成员", "心率", "状态"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 100)
        self.table.setStyleSheet(f"QTableWidget {{ background: {PAPER}; color: {INK}; border: none; }} "
                                f"QHeaderView::section {{ background: {PAPER}; color: {MUTED}; border: none; padding: 8px; }}")
        self.table.verticalHeader().setDefaultSectionSize(44)
        layout.addWidget(self.table, 1)
        self.table.hide()
        show = Button("显示 / 隐藏联机小窗")
        show.setMaximumWidth(230)
        show.clicked.connect(window.on_room_overlay_toggle)
        layout.addWidget(show)
        show.hide()
        self.set_active(False)

    def set_active(self, active: bool, joined: bool = False) -> None:
        for widget in (self.address, self.name, self.password, self.code, self.create, self.join):
            widget.setEnabled(not active)
        self.leave.setEnabled(active)
        self.copy.setEnabled(joined)
        self.share.setEnabled(joined)

    def set_members(self, members: list, self_id: str) -> None:
        self.table.setRowCount(len(members))
        for row, member in enumerate(members):
            name = str(member.get("name", "玩家"))[:24] + (" · 我" if member.get("id") == self_id else "")
            bpm = member.get("bpm")
            valid = type(bpm) is int and 1 <= bpm <= 300
            status = "未共享" if not member.get("sharing") else "实时" if valid else "等待读数"
            if member.get("simulated"):
                status = "模拟 · " + status
            for col, text in enumerate((name, str(bpm) + " bpm" if valid else "—", status)):
                self.table.setItem(row, col, QTableWidgetItem(text))


class RoomOverlay(QWidget):
    def __init__(self, store) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.store = store
        self.members = []
        self.self_id = ""
        self._drag = None
        self.updated = 0.0
        self.histories = {}
        self.colors = {}
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        screen = QApplication.primaryScreen().availableGeometry()
        x = store.get_int_setting("room_overlay_x", screen.right() - 470)
        y = store.get_int_setting("room_overlay_y", screen.top() + 190)
        if not any(s.availableGeometry().contains(x + 40, y + 20) for s in QApplication.screens()):
            x, y = screen.right() - 470, screen.top() + 190
        self.move(x, y)
        self.resize(430, 104)

    def set_members(self, members: list, self_id: str, *, histories=None, colors=None) -> None:
        self.members, self.self_id, self.updated = list(members), self_id, time.monotonic()
        self.histories, self.colors = histories or {}, colors or {}
        self.resize(430, 44 + max(1, len(members)) * 58)
        self.update()

    def set_passthrough(self, enabled: bool) -> None:
        shown = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
        if shown:
            self.show()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(RULE), 1))
        p.setBrush(QColor(PAPER))
        p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 12, 12)
        p.setPen(QColor(MUTED))
        p.setFont(QFont("Microsoft YaHei UI", 9))
        p.drawText(QPointF(16, 25), "HeartApp · 最近 60 秒")
        p.drawText(QPointF(405, 25), "×")
        if not self.members:
            p.drawText(QPointF(16, 70), "等待房间成员 / 连接中断")
        now = time.monotonic()
        values = [b for points in self.histories.values() for t, b in points if t >= now - 60 and b is not None]
        low = min(values or [60]) - 8
        high = max(low + 40, max(values or [100]) + 8)
        for row, member in enumerate(self.members):
            y = 42 + row * 58
            color = QColor(self.colors.get(member.get("id"), RED))
            name = str(member.get("name", "玩家"))[:24] + (" · 我" if member.get("id") == self.self_id else "")
            p.setFont(QFont("Microsoft YaHei UI", 10))
            name = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 118)
            p.setPen(QColor(INK))
            p.setFont(QFont("Microsoft YaHei UI", 10))
            p.drawText(QPointF(16, y + 19), name)
            bpm = member.get("bpm")
            valid = type(bpm) is int and 1 <= bpm <= 300 and time.monotonic() - self.updated <= 5
            p.setPen(color if valid else QColor(MUTED))
            p.setFont(serif(32))
            p.drawText(QRectF(142, y - 7, 70, 43), Qt.AlignmentFlag.AlignRight, str(bpm) if valid else "—")
            p.setFont(QFont("Microsoft YaHei UI", 8))
            p.setPen(QColor(MUTED))
            p.drawText(QPointF(223, y + 19), "bpm")
            detail = "模拟" if member.get("simulated") else "实时"
            if not member.get("sharing"):
                detail += " · 仅本机" if member.get("id") == self.self_id else " · 未共享"
            elif not valid:
                detail += " · 等待读数"
            p.drawText(QPointF(16, y + 40), detail)
            plot = QRectF(276, y, 137, 38)
            p.setPen(QPen(QColor(RULE), 1))
            p.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
            p.setPen(QPen(color, 1.3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for segment in segments(self.histories.get(member.get("id"), [])):
                path = QPainterPath()
                first = True
                for t, b in segment:
                    if t < now - 60:
                        continue
                    point = QPointF(plot.left() + (t - now + 60) / 60 * plot.width(),
                                    plot.bottom() - (b - low) / (high - low) * plot.height())
                    path.moveTo(point) if first else path.lineTo(point)
                    first = False
                p.drawPath(path)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if event.position().x() > self.width() - 30 and event.position().y() < 35:
                self.hide()
            else:
                self._drag = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None:
            self._drag = None
            self.store.set_setting("room_overlay_x", str(self.x()))
            self.store.set_setting("room_overlay_y", str(self.y()))
