"""服务器登录与房间目录分区。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QHBoxLayout,
    QHeaderView, QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from .editorial import Button, INK, MUTED, PAPER, RULE, label, serif


class RoomPanel(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.owner = window
        self._state = dict(logged=False, connecting=False, joined=False, pending=False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        heading = label("服务器")
        heading.setFont(serif(24))
        layout.addWidget(heading)
        address_row = QHBoxLayout()
        self.address = QLineEdit(window.store.get_setting("room_server", "ws://127.0.0.1:8765"))
        self.address.setPlaceholderText("IP / 域名，例如 https://hr.example.com")
        self.name = QLineEdit(window.store.get_setting("room_name", "玩家"))
        self.name.setPlaceholderText("你的昵称")
        self.name.setMaxLength(24)
        address_row.addWidget(self.address, 3)
        address_row.addWidget(self.name, 1)
        layout.addLayout(address_row)
        auth_row = QHBoxLayout()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("服务器访问密码（未设置可留空）")
        self.login = Button("登录服务器", primary=True)
        self.logout = Button("退出登录")
        self.login.clicked.connect(window.on_server_login)
        self.logout.clicked.connect(window.on_server_logout)
        self.password.returnPressed.connect(window.on_server_login)
        auth_row.addWidget(self.password, 1)
        auth_row.addWidget(self.login)
        auth_row.addWidget(self.logout)
        layout.addLayout(auth_row)
        self.server_status = label("未登录 · 密码不会保存到磁盘", 11, True)
        self.server_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.server_status)
        layout.addSpacing(8)
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {RULE};")
        layout.addWidget(rule)
        layout.addSpacing(8)
        heading = label("房间")
        heading.setFont(serif(24))
        room_heading = QHBoxLayout()
        room_heading.addWidget(heading)
        room_heading.addStretch()
        self.refresh = Button("刷新列表")
        self.refresh.clicked.connect(window.on_room_refresh)
        room_heading.addWidget(self.refresh)
        layout.addLayout(room_heading)
        self.rooms = QTableWidget(0, 3)
        self.rooms.setHorizontalHeaderLabels(["房间名称", "人数", "状态"])
        self.rooms.verticalHeader().hide()
        self.rooms.setShowGrid(False)
        self.rooms.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rooms.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rooms.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rooms.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.rooms.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.rooms.setColumnWidth(1, 75)
        self.rooms.setColumnWidth(2, 110)
        self.rooms.verticalHeader().setDefaultSectionSize(34)
        self.rooms.setMinimumHeight(110)
        self.rooms.setMaximumHeight(180)
        self.rooms.setStyleSheet(f"QTableWidget {{ background: {PAPER}; color: {INK}; border: 1px solid {RULE}; selection-background-color: #F8EAE4; selection-color: {INK}; }} "
                                f"QHeaderView::section {{ background: {PAPER}; color: {MUTED}; border: none; padding: 7px; }}")
        self.rooms.itemSelectionChanged.connect(self._select_room)
        self.rooms.cellDoubleClicked.connect(lambda *_: window.on_room_connect(create=False) if self.join.isEnabled() else None)
        layout.addWidget(self.rooms, 1)
        create_row = QHBoxLayout()
        self.title = QLineEdit()
        self.title.setPlaceholderText("新房间名称（可留空）")
        self.title.setMaxLength(32)
        self.create = Button("创建房间", primary=True)
        self.create.clicked.connect(lambda: window.on_room_connect(create=True))
        create_row.addWidget(self.title, 1)
        create_row.addWidget(self.create)
        layout.addLayout(create_row)
        room_row = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("选择列表中的房间，或填写 8 位房间码")
        self.code.setMaxLength(12)
        self.code.textChanged.connect(lambda _: self._controls())
        self.join = Button("加入房间")
        self.join.clicked.connect(lambda: window.on_room_connect(create=False))
        room_row.addWidget(self.code, 1)
        room_row.addWidget(self.join)
        layout.addLayout(room_row)
        actions = QHBoxLayout()
        self.leave = Button("退出房间")
        self.copy = Button("复制邀请")
        self.leave.clicked.connect(window.on_room_leave)
        self.copy.clicked.connect(window.on_room_copy)
        actions.addWidget(self.leave)
        actions.addWidget(self.copy)
        actions.addStretch()
        self.share = QCheckBox("共享我的实时心率（含模拟标识）")
        self.share.toggled.connect(lambda _: window._publish_room_reading())
        actions.addWidget(self.share)
        layout.addLayout(actions)
        self.status = label("请先登录服务器，再选择或创建房间。", 11, True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        privacy = label("共享默认关闭；同服已登录用户可查看房间列表。队友曲线临时保留最多30分钟，退出即清空。公网使用 HTTPS / WSS。", 10, True)
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        self._controls()

    def _select_room(self) -> None:
        item = self.rooms.item(self.rooms.currentRow(), 0)
        if item is not None and self.code.isEnabled():
            self.code.setText(str(item.data(Qt.ItemDataRole.UserRole)))

    def set_rooms(self, rooms: list) -> None:
        selected = self.code.text().strip()
        self.rooms.blockSignals(True)
        self.rooms.setRowCount(len(rooms))
        for row, room in enumerate(rooms):
            count, capacity = room.get("count", 0), room.get("capacity", 4)
            texts = (str(room.get("name", "未命名房间")), f"{count} / {capacity}",
                     "已满" if count >= capacity else "可加入")
            for col, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, str(room.get("room", "")))
                self.rooms.setItem(row, col, item)
            if room.get("room") == selected:
                self.rooms.selectRow(row)
        self.rooms.blockSignals(False)

    def set_state(self, *, logged: bool, connecting: bool, joined: bool, pending: bool) -> None:
        self._state = dict(logged=logged, connecting=connecting, joined=joined, pending=pending)
        self._controls()

    def set_active(self, active: bool, joined: bool = False) -> None:
        self.set_state(logged=active, connecting=False, joined=joined, pending=False)

    def set_members(self, members: list, self_id: str) -> None:
        pass  # 成员及曲线统一呈现在主页；这里仅展示服务器房间目录。

    def _controls(self) -> None:
        if not hasattr(self, "share"):
            return
        logged, connecting, joined, pending = (self._state[k] for k in ("logged", "connecting", "joined", "pending"))
        for widget in (self.address, self.name, self.password):
            widget.setEnabled(not logged and not connecting)
        self.login.setEnabled(not logged and not connecting)
        self.logout.setEnabled(logged or connecting)
        for widget in (self.title, self.code, self.rooms):
            widget.setEnabled(logged and not joined and not pending)
        self.create.setEnabled(logged and not joined and not pending)
        self.join.setEnabled(logged and not joined and not pending and bool(self.code.text().strip()))
        self.refresh.setEnabled(logged)
        self.leave.setEnabled(joined and not pending)
        self.copy.setEnabled(joined)
        self.share.setEnabled(joined)
