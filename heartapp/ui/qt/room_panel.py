"""D 版联机页及独立多人浮窗。"""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QCheckBox, QHBoxLayout, QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from .editorial import Button, INK, MUTED, PAPER, RED, RULE, label, serif
from .editorial_layout import EditorialLayout
from ...room_client import RoomClient
from ...room_protocol import room_code, server_url


class RoomActions:
    def _init_room(self) -> None:
        self._room_client = RoomClient()
        self._room_members = []
        self._room_self_id = ""
        self._room_code = ""
        self._room_active = False
        self._room_updated = 0.0

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
        panel.share.setChecked(False)
        panel.set_active(True)
        panel.status.setText("正在连接服务器…")

    def on_room_leave(self) -> None:
        self._room_client.leave()
        self._room_active = False
        self._room_members, self._room_self_id, self._room_code = [], "", ""
        self._room_panel.share.setChecked(False)
        self._room_panel.set_active(False)
        self._room_panel.set_members([], "")
        self._room_panel.status.setText("已离开房间；本地记录继续")
        self._room_overlay.set_members([], "")
        self._room_overlay.hide()

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
                self._room_panel.status.setText(payload["message"])
            elif kind == "room_status":
                self._room_panel.status.setText(payload["message"])
                if payload.get("clear"):
                    self._room_members = []
                    self._room_panel.set_members([], self._room_self_id)
                    self._room_overlay.set_members([], self._room_self_id)
            elif kind == "room_welcome":
                self._room_code = payload["room"]
                self._room_self_id = payload["self_id"]
                self._room_panel.code.setText(self._room_code)
                self._room_panel.set_active(True, joined=True)
                self._room_panel.status.setText(f"房间 {self._room_code} · 已加入，按需开启共享")
            elif kind == "room_snapshot":
                self._room_updated = time.monotonic()
                self._room_members = payload["members"]
                self._room_panel.set_members(self._room_members, self._room_self_id)
                self._room_overlay.set_members(self._room_members, self._room_self_id)
        if self._room_members and time.monotonic() - self._room_updated > 5:
            if any(m.get("bpm") is not None for m in self._room_members):
                self._room_members = [dict(m, bpm=None) for m in self._room_members]
                self._room_panel.set_members(self._room_members, self._room_self_id)
                self._room_overlay.set_members(self._room_members, self._room_self_id)


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
        privacy = label("共享默认关闭。房间成员和服务器运营者能看到共享读数；历史记录保留在本机。公网建议使用 HTTPS / WSS。", 11, True)
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
        show = Button("显示 / 隐藏联机小窗")
        show.setMaximumWidth(230)
        show.clicked.connect(window.on_room_overlay_toggle)
        layout.addWidget(show)
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
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        screen = QApplication.primaryScreen().availableGeometry()
        x = store.get_int_setting("room_overlay_x", screen.right() - 380)
        y = store.get_int_setting("room_overlay_y", screen.top() + 190)
        if not any(s.availableGeometry().contains(x + 40, y + 20) for s in QApplication.screens()):
            x, y = screen.right() - 380, screen.top() + 190
        self.move(x, y)
        self.resize(340, 100)

    def set_members(self, members: list, self_id: str) -> None:
        self.members, self.self_id, self.updated = list(members), self_id, time.monotonic()
        self.resize(340, 44 + max(1, len(members)) * 46)
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
        p.drawText(QPointF(16, 25), "HeartApp · 联机")
        p.drawText(QPointF(315, 25), "×")
        if not self.members:
            p.drawText(QPointF(16, 70), "等待房间成员 / 连接中断")
        for row, member in enumerate(self.members):
            y = 42 + row * 46
            name = str(member.get("name", "玩家"))[:24] + (" · 我" if member.get("id") == self.self_id else "")
            name = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 140)
            p.setPen(QColor(INK))
            p.setFont(QFont("Microsoft YaHei UI", 10))
            p.drawText(QPointF(16, y + 21), name)
            bpm = member.get("bpm")
            valid = type(bpm) is int and 1 <= bpm <= 300 and time.monotonic() - self.updated <= 5
            p.setPen(QColor(RED if valid else MUTED))
            p.setFont(serif(30))
            p.drawText(QRectF(176, y - 4, 68, 37), Qt.AlignmentFlag.AlignRight, str(bpm) if valid else "—")
            p.setFont(QFont("Microsoft YaHei UI", 8))
            p.setPen(QColor(MUTED))
            p.drawText(QPointF(258, y + 21), "模拟" if member.get("simulated") else "bpm")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if event.position().x() > 305 and event.position().y() < 35:
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
