"""D 版联机页及独立多人浮窗。"""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .editorial import INK, RED, RULE, OverlayAppearance, serif
from .lobby_panel import RoomPanel
from ...room_client import RoomClient
from ...room_protocol import DEFAULT_ROOM_SIZE, room_code, server_url
from ...room_history import RoomHistory, segments
from .comparison import COLORS, MemberLegend


class RoomActions:
    def _init_room(self) -> None:
        self._room_client = RoomClient()
        self._room_members = []
        self._room_capacity = DEFAULT_ROOM_SIZE
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
        self._server_logged = False
        self._server_connecting = False
        self._room_pending = False

    def _sync_server_controls(self) -> None:
        self._room_panel.set_state(logged=self._server_logged, connecting=self._server_connecting,
                                   joined=bool(self._room_code), pending=self._room_pending,
                                   capacity=self._room_capacity)

    def on_server_login(self) -> None:
        panel = self._room_panel
        try:
            address = server_url(panel.address.text())
            name = panel.name.text().strip()
            if not 1 <= len(name) <= 24:
                raise ValueError("请输入 1～24 个字符作为昵称")
            self.store.set_setting("room_server", address)
            self.store.set_setting("room_name", name)
            self._room_client.login(address, name, password=panel.password.text())
        except Exception as exc:  # noqa: BLE001
            panel.server_status.setText(f"未能登录：{exc}")
            return
        self._server_connecting = True
        self._room_notice = ""
        panel.server_status.setText("正在登录服务器…")
        self._sync_server_controls()

    def on_server_logout(self) -> None:
        self._room_client.leave()
        self._server_logged = self._server_connecting = False
        self._clear_room_view()
        self._room_panel.set_rooms([])
        self._room_panel.password.clear()
        self._room_panel.server_status.setText("未登录 · 密码不会保存到磁盘")
        self._sync_server_controls()

    def on_room_refresh(self) -> None:
        if self._server_logged:
            self._room_client.command("list")

    def _show_room_dialog(self) -> None:
        self._room_dialog.adjustSize()
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
            message = "正在连接房间…" if self._room_active else self._room_notice or ("服务器已登录 · 选择或创建房间" if self._server_logged else "个人模式 · 可邀请朋友一起查看心率")
            self._live_room_status.setText(message)
            self._live_room_status.setToolTip(message)
            return
        count = len(self._room_members)
        message = f"房间 {self._room_code} · {count} / {self._room_capacity} 人 · " + (self._room_link_note or ("正在共享" if self._room_panel.share.isChecked() else "我的心率仅本机可见"))
        self._live_room_status.setText(message)
        self._live_room_status.setToolTip(message)
        self._room_duration.setText("我的记录 " + self._stat_values["duration"].text())
        self._room_record_btn.setText(self._record_btn.text())
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
                used = set(self._room_colors.values())
                candidates = (*COLORS[1:], *(QColor.fromHsv(i * 137 % 360, 155, 162).name() for i in range(len(used) + 1)))
                self._room_colors[key] = next(color for color in candidates if color not in used)
            if key not in self._room_legends:
                button = MemberLegend(key)
                button.toggled.connect(lambda checked, k=key: self._room_toggle_series(k, checked))
                self._room_legend_layout.addWidget(button, 1)
                self._room_legends[key] = button
            self._room_legends[key].set_member(member, self._room_colors[key])
        short = {key: [(t, b) for t, b in values if t >= now - 60]
                 for key, values in self._room_history.points.items()}
        # 小窗保留少量成员，全部成员仍可在主界面查看或用图例切换。
        visible = [members[0]] + [m for m in members[1:] if m["id"] not in self._room_hidden]
        self._room_overlay.set_members(visible[:4], "self", histories=short,
                                       colors=self._room_colors, total_members=len(members))

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
            series[key] = dict(name=str(member.get("name", "玩家")), color=self._room_colors.get(key, COLORS[0]),
                               points=[(t - now, b) for t, b in self._room_history.points.get(key, [])])
        self._room_chart.set_series(series, window)
        self._refresh_room_stats()

    def on_room_connect(self, *, create: bool) -> None:
        panel = self._room_panel
        if not self._server_logged:
            panel.status.setText("请先登录服务器")
            return
        try:
            code = "" if create else room_code(panel.code.text())
            self._room_client.command("create" if create else "join", code=code, title=panel.title.text())
        except (ValueError, OSError) as exc:
            panel.status.setText(str(exc))
            return
        self._room_active = True
        self._room_pending = True
        self._room_notice = ""
        panel.share.setChecked(False)
        self._sync_server_controls()
        panel.status.setText("正在进入房间…")

    def on_room_leave(self) -> None:
        self._room_client.leave_room()
        self._clear_room_view()
        self._room_pending = True
        self._room_panel.status.setText("正在退出房间，服务器登录保持不变…")
        self._sync_server_controls()

    def _clear_room_view(self) -> None:
        self._room_active = False
        self._room_pending = False
        self._room_link_note = ""
        self._room_notice = "已离开房间；本地记录继续"
        self._room_history.clear()
        self._room_chart.set_series({}, 300)
        self._room_chart.reset_view()
        self._room_members, self._room_self_id, self._room_code = [], "", ""
        self._room_panel.share.setChecked(False)
        self._sync_server_controls()
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
        if self._room_code or self._room_overlay.isVisible():
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
            if kind == "server_login":
                if not payload.get("resumed") and self._room_code:
                    self._clear_room_view()
                    self._room_notice = "服务器会话已更新，请重新选择房间"
                self._server_logged, self._server_connecting = True, False
                self._room_pending = bool(payload.get("room")) or bool(payload.get("leaving"))
                self._room_panel.password.clear()
                self._room_capacity = payload.get("capacity", DEFAULT_ROOM_SIZE)
                self._room_panel.server_status.setText("已登录 · 退房保持登录")
                if not self._room_code:
                    self._room_panel.status.setText("双击列表中的房间可直接加入。")
                self._sync_server_controls()
            elif kind == "server_rooms":
                self._room_panel.set_rooms(payload["rooms"])
            elif kind == "room_left":
                self._clear_room_view()
                self._room_panel.status.setText("已退出房间，服务器仍保持登录。可直接选择另一个房间。")
                self._sync_server_controls()
            elif kind == "room_command_error":
                self._room_pending = False
                self._room_active = bool(self._room_code)
                self._room_panel.status.setText(payload["message"])
                self._room_notice = payload["message"]
                self._sync_server_controls()
            elif kind == "room_error":
                self.on_server_logout()
                self._room_notice = payload["message"]
                self._room_panel.status.setText(payload["message"])
                self._room_panel.server_status.setText(payload["message"])
            elif kind == "room_status":
                self._room_link_note = payload["message"]
                self._room_panel.status.setText(payload["message"])
                if payload.get("clear"):
                    self._server_logged, self._server_connecting = False, True
                    self._room_pending = False
                    self._room_panel.server_status.setText("连接已断开，正在恢复登录…")
                    self._sync_server_controls()
                    self._room_history.gap(time.monotonic())
                    self._room_members = [dict(m, bpm=None, connected=False) for m in self._room_members]
                    self._room_panel.set_members([], self._room_self_id)
                    self._room_overlay.set_members([], self._room_self_id)
            elif kind == "room_welcome":
                if self._room_code != payload["room"]:
                    self._room_history.clear(time.monotonic())
                    for stamp, bpm in self._live:
                        self._room_history.append("self", stamp, bpm)
                    self._room_chart.reset_view()
                self._room_code = payload["room"]
                self._room_capacity = payload.get("capacity", self._room_capacity)
                self._room_link_note = ""
                self._room_self_id = payload["self_id"]
                self._room_panel.code.setText(self._room_code)
                self._room_active, self._room_pending = True, False
                self._sync_server_controls()
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


class RoomOverlay(QWidget):
    def __init__(self, store, *, appearance: OverlayAppearance | None = None) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.store = store
        self.appearance = appearance if appearance is not None else OverlayAppearance(store)
        self.members = []
        self.total_members = 0
        self.curve_seconds = 60
        self._scale = 100
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
        self._resize_for_members()

    def _layout_width(self) -> int:
        return 430 if self.curve_seconds else 270

    def _resize_for_members(self) -> None:
        height = 44 + max(1, len(self.members)) * 58
        self.resize(round(self._layout_width() * self._scale / 100), round(height * self._scale / 100))
        self.update()

    def set_scale(self, percent: int) -> None:
        self._scale = max(75, min(150, percent))
        self._resize_for_members()

    def set_curve_seconds(self, seconds: int) -> None:
        self.curve_seconds = seconds
        self._resize_for_members()

    def set_members(self, members: list, self_id: str, *, histories=None, colors=None, total_members=None) -> None:
        self.members, self.self_id, self.updated = list(members), self_id, time.monotonic()
        self.histories, self.colors = histories or {}, colors or {}
        self.total_members = len(members) if total_members is None else total_members
        self._resize_for_members()

    def set_passthrough(self, enabled: bool) -> None:
        shown = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
        if shown:
            self.show()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.scale(self._scale / 100, self._scale / 100)
        width = self._layout_width()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.appearance.paint_background(
            p, QRectF(1, 1, width - 2, 42 + max(1, len(self.members)) * 58), 12
        )
        p.setPen(self.appearance.colors["text"])
        p.setFont(QFont("Microsoft YaHei UI", 9))
        title = f"HeartApp · 最近 {self.curve_seconds} 秒" if self.curve_seconds else "HeartApp · 联机心率"
        if self.total_members > len(self.members):
            title += f" · {len(self.members)}/{self.total_members} 人"
        p.drawText(QPointF(16, 25), title)
        p.drawText(QPointF(width - 25, 25), "×")
        if not self.members:
            p.drawText(QPointF(16, 70), "等待房间成员 / 连接中断")
        now = time.monotonic()
        values = [b for points in self.histories.values() for t, b in points if t >= now - self.curve_seconds and b is not None]
        low = min(values or [60]) - 8
        high = max(low + 40, max(values or [100]) + 8)
        for row, member in enumerate(self.members):
            y = 42 + row * 58
            color = QColor(self.colors.get(member.get("id"), RED))
            name = str(member.get("name", "玩家"))[:24] + (" · 我" if member.get("id") == self.self_id else "")
            p.setFont(QFont("Microsoft YaHei UI", 10))
            name = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 118)
            p.setPen(self.appearance.colors["text"])
            p.setFont(QFont("Microsoft YaHei UI", 10))
            p.drawText(QPointF(16, y + 19), name)
            bpm = member.get("bpm")
            valid = type(bpm) is int and 1 <= bpm <= 300 and time.monotonic() - self.updated <= 5
            text_color = self.appearance.colors["text"]
            p.setPen(color if valid and text_color == QColor(INK) else text_color)
            p.setFont(serif(32))
            p.drawText(QRectF(142, y - 7, 70, 43), Qt.AlignmentFlag.AlignRight, str(bpm) if valid else "—")
            p.setFont(QFont("Microsoft YaHei UI", 8))
            p.setPen(self.appearance.colors["text"])
            p.drawText(QPointF(223, y + 19), "bpm")
            detail = "模拟" if member.get("simulated") else "实时"
            if member.get("connected") is False:
                detail = "重连中"
            if not member.get("sharing"):
                detail += " · 仅本机" if member.get("id") == self.self_id else " · 未共享"
            elif not valid:
                detail += " · 等待读数"
            p.drawText(QPointF(16, y + 40), detail)
            if not self.curve_seconds:
                continue
            plot = QRectF(276, y, 137, 38)
            p.setPen(QPen(QColor(RULE), 1))
            p.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
            p.setPen(QPen(color, self.appearance.line_width))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for segment in segments(self.histories.get(member.get("id"), [])):
                path = QPainterPath()
                first = True
                for t, b in segment:
                    if t < now - self.curve_seconds:
                        continue
                    point = QPointF(plot.left() + (t - now + self.curve_seconds) / self.curve_seconds * plot.width(),
                                    plot.bottom() - (b - low) / (high - low) * plot.height())
                    path.moveTo(point) if first else path.lineTo(point)
                    first = False
                p.drawPath(path)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if event.position().x() > self.width() - 30 * self._scale / 100 and event.position().y() < 35 * self._scale / 100:
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
