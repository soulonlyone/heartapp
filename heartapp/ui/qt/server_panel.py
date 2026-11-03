"""Windows 轻量服务端面板；与无界面转发服务共用核心。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import secrets
import time

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QIntValidator, QPainter, QPixmap
from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QHBoxLayout, QLineEdit, QMenu, QPlainTextEdit,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)

from ... import __version__
from ...server_control import ServerController, ServerSettings, load_settings, save_settings
from .chrome import FramelessWindow, TitleBar
from .editorial import Button, CapsuleCombo, INK, MUTED, PAPER, RED, RULE, ensure_light_palette, label, serif


def panel_icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(RED))
    painter.drawEllipse(2, 2, 28, 28)
    painter.setPen(QColor("white"))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "S")
    painter.end()
    return QIcon(pixmap)


class NumberField(QLineEdit):
    valueChanged = Signal(int)

    def __init__(self, low: int, high: int, value: int) -> None:
        super().__init__(str(value))
        self.setValidator(QIntValidator(low, high, self))
        self.textChanged.connect(lambda: self.valueChanged.emit(self.value()))

    def value(self) -> int:
        try:
            return int(self.text())
        except ValueError:
            return 0  # 编辑中的空值或非法输入交由启动时统一验证。

    def setValue(self, value: int) -> None:
        self.setText(str(value))


class ServerPanel(FramelessWindow):
    def __init__(self, config_path: Path, *, tray_enabled: bool = True, exit_on_close: bool = False) -> None:
        super().__init__()
        ensure_light_palette()
        self.config_path = config_path
        self.controller = ServerController()
        self.settings, warning = load_settings(config_path)
        self._running = False
        self._started = 0.0
        self._quitting = False
        self._failed = False
        self._state = "未启动"
        self._online = self._rooms = 0
        self._tray = None
        self._exit_on_close = exit_on_close
        self._addresses_updated = 0.0
        self.setWindowTitle("HeartApp · 服务端")
        self.setWindowIcon(panel_icon())
        self.resize(850, 800)
        self.setMinimumSize(770, 770)
        self.setStyleSheet(f"""
            QWidget {{ background: {PAPER}; color: {INK}; font-family: 'Microsoft YaHei UI'; }}
            QLineEdit {{ border: 1px solid {RULE}; border-radius: 12px;
                padding: 6px 12px; min-height: 24px; background: {PAPER}; }}
            QLineEdit:focus {{ border-color: {MUTED}; }}
            QLineEdit:disabled {{ color: {MUTED}; }}
            QPlainTextEdit {{ border: 1px solid {RULE}; border-radius: 10px; padding: 8px; }}
            QMenu {{ background: {PAPER}; border: 1px solid {RULE}; padding: 6px; }}
            QMenu::item {{ padding: 8px 20px; }}
            QMenu::item:selected {{ background: #F0ECE4; }}
        """)
        self._build()
        self._setup_tray(tray_enabled)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(150)
        self._refresh_addresses()
        if warning:
            self._message(warning)

    def _rule(self, layout) -> None:
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {RULE};")
        layout.addWidget(line)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 20, 32, 20)
        layout.setSpacing(14)
        self._titlebar = TitleBar(self)
        self._titlebar.row.addWidget(label("HEARTAPP  /  SERVER", 13))
        self._titlebar.row.addStretch()
        self._titlebar.add_controls()
        layout.addWidget(self._titlebar)
        self._rule(layout)
        hero = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(label("把朋友连接到一起", 13, True))
        self.status = label("未启动", 40)
        left.addWidget(self.status)
        self.counts = label("0 人在线  ·  0 个房间", 13, True)
        left.addWidget(self.counts)
        hero.addLayout(left)
        hero.addStretch()
        right = QVBoxLayout()
        right.addWidget(label("运行时长", 12, True), alignment=Qt.AlignmentFlag.AlignRight)
        self.uptime = label("00:00", 54)
        self.uptime.setFont(serif(54))
        right.addWidget(self.uptime)
        hero.addLayout(right)
        layout.addLayout(hero)
        actions = QHBoxLayout()
        self.start_button = Button("启动服务", primary=True)
        self.start_button.clicked.connect(self.start_service)
        self.stop_button = Button("停止服务")
        self.stop_button.clicked.connect(self.stop_service)
        self.stop_button.setEnabled(False)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addStretch()
        self.background_button = Button("收起到托盘")
        self.background_button.clicked.connect(self.hide)
        actions.addWidget(self.background_button)
        layout.addLayout(actions)
        self._rule(layout)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(9)
        self.scope = CapsuleCombo()
        self.scope.addItem("局域网联机", "lan")
        self.scope.addItem("仅本机 / 反向代理", "local")
        self.scope.setCurrentIndex(0 if self.settings.scope == "lan" else 1)
        self.port = self._spin(1, 65535, self.settings.port)
        self.max_clients = self._spin(1, 10000, self.settings.max_clients)
        self.room_size = self._spin(1, 4, self.settings.room_size)
        for column, (title, widget) in enumerate((("监听范围", self.scope), ("服务端口", self.port),
                                                 ("连接人数上限", self.max_clients), ("每房间人数", self.room_size))):
            grid.addWidget(label(title, 12, True), 0, column)
            grid.addWidget(widget, 1, column)
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)
        password_row = QHBoxLayout()
        self.password = QLineEdit(secrets.token_urlsafe(12))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMaxLength(256)
        self.password.setAccessibleName("服务器访问密码")
        self.show_password = Button("显示密码")
        self.show_password.setCheckable(True)
        self.show_password.toggled.connect(self._toggle_password)
        self.new_password = Button("重新生成")
        self.new_password.clicked.connect(lambda: self.password.setText(secrets.token_urlsafe(12)))
        password_row.addWidget(self.password, 1)
        password_row.addWidget(self.show_password)
        password_row.addWidget(self.new_password)
        layout.addWidget(label("访问密码", 12, True))
        layout.addLayout(password_row)
        layout.addWidget(label("首次已生成密码，可自行修改；密码不保存，下次打开会重新生成。", 12, True))
        self._rule(layout)
        address_grid = QGridLayout()
        address_grid.setHorizontalSpacing(12)
        self.local_address = QLineEdit()
        self.local_address.setReadOnly(True)
        self.lan_address = CapsuleCombo()
        for row, (title, widget) in enumerate((("本机测试", self.local_address), ("局域网地址", self.lan_address))):
            address_grid.addWidget(label(title, 12, True), row, 0)
            address_grid.addWidget(widget, row, 1)
            button = Button("复制地址")
            button.clicked.connect(lambda checked=False, kind=row: self.copy_address(kind))
            address_grid.addWidget(button, row, 2)
            if row == 0:
                self.copy_local = button
            else:
                self.copy_lan = button
        self.public_url = QLineEdit(self.settings.public_url)
        self.public_url.setPlaceholderText("可选：填已配置反代的 wss://域名")
        address_grid.addWidget(label("公网地址", 12, True), 2, 0)
        address_grid.addWidget(self.public_url, 2, 1)
        self.copy_public = Button("复制地址")
        self.copy_public.clicked.connect(lambda: self.copy_address(2))
        address_grid.addWidget(self.copy_public, 2, 2)
        layout.addLayout(address_grid)
        self.reachability = label("启动仅代表本机开始监听；公网地址需自行配置证书、反向代理和网络放行。", 12, True)
        self.reachability.setWordWrap(True)
        layout.addWidget(self.reachability)
        self.notice = label("点击启动后，把地址和密码分别发给朋友。房间在客户端创建。", 12)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        layout.addStretch(1)
        self._rule(layout)
        footer = QHBoxLayout()
        footer.addWidget(label("只转发实时心率  /  不保存历史", 12, True))
        footer.addStretch()
        details = Button("运行信息")
        details.setCheckable(True)
        footer.addWidget(details)
        diagnostics = Button("复制诊断")
        diagnostics.clicked.connect(self.copy_diagnostics)
        footer.addWidget(diagnostics)
        self.quit_button = Button("退出面板")
        self.quit_button.clicked.connect(self.request_quit)
        footer.addWidget(self.quit_button)
        layout.addLayout(footer)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(100)
        self.logs.setFixedHeight(96)
        self.logs.hide()
        details.toggled.connect(self.logs.setVisible)
        layout.addWidget(self.logs)
        self._inputs = [self.scope, self.port, self.max_clients, self.room_size,
                        self.password, self.new_password, self.public_url]
        self.scope.currentIndexChanged.connect(self._refresh_addresses)
        self.port.valueChanged.connect(self._refresh_addresses)

    @staticmethod
    def _spin(low: int, high: int, value: int) -> NumberField:
        return NumberField(low, high, value)

    def _setup_tray(self, enabled: bool) -> None:
        if enabled and QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = QSystemTrayIcon(self.windowIcon(), self)
            menu = QMenu()
            for title, callback in (("打开控制面板", self.show_panel), ("停止服务", self.stop_service),
                                    ("退出面板并停止服务", self.request_quit)):
                action = QAction(title, self)
                action.triggered.connect(callback)
                menu.addAction(action)
            self._tray.setContextMenu(menu)
            self._tray_menu = menu
            self._tray.activated.connect(lambda reason: self.show_panel()
                                        if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
            self._tray.show()
        self.background_button.setEnabled(self._tray is not None)
        if self._tray is None:
            self.background_button.setToolTip("系统托盘不可用，关闭面板将停止服务。")

    def show_panel(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _toggle_password(self, visible: bool) -> None:
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.show_password.setText("隐藏密码" if visible else "显示密码")

    def _current_settings(self) -> ServerSettings:
        return ServerSettings(self.scope.currentData(), self.port.value(), self.max_clients.value(),
                              self.room_size.value(), self.public_url.text().strip())

    def _refresh_addresses(self, *args) -> None:
        self._addresses_updated = time.monotonic()
        port = self.port.value()
        self.local_address.setText(f"ws://127.0.0.1:{port}")
        previous = self.lan_address.currentText()
        self.lan_address.clear()
        addresses = sorted({entry.ip().toString()
                            for interface in QNetworkInterface.allInterfaces()
                            if interface.flags() & QNetworkInterface.InterfaceFlag.IsUp
                            for entry in interface.addressEntries()
                            if entry.ip().protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol
                            and not entry.ip().isLoopback() and not entry.ip().toString().startswith("169.254.")})
        if self.scope.currentData() == "lan":
            self.lan_address.addItems([f"ws://{ip}:{port}" for ip in addresses])
        if self.lan_address.findText(previous) >= 0:
            self.lan_address.setCurrentText(previous)
        self.lan_address.setEnabled(self.lan_address.count() > 0)
        self.copy_local.setEnabled(self._running)
        self.copy_lan.setEnabled(self._running and self.lan_address.count() > 0)
        self.copy_public.setEnabled(self._running and bool(self.public_url.text().strip()))

    def _message(self, text: str) -> None:
        self.notice.setText(text)
        self.logs.appendPlainText(datetime.now().strftime("%H:%M:%S") + "  " + text)

    def start_service(self) -> None:
        if self.controller.active or self._quitting:
            return
        try:
            settings = self._current_settings()
            if not self.password.text():
                raise ValueError("请先填写访问密码，也可以点击「重新生成」。")
            save_settings(self.config_path, settings)
            self.controller.start(settings, self.password.text())
        except (OSError, ValueError, RuntimeError) as exc:
            self._message(str(exc) if isinstance(exc, ValueError) else "无法保存配置或启动线程，请检查配置目录写入权限后重试。")
            return
        self.settings = settings
        self._failed = False
        self._state = "正在启动"
        self.status.setText(self._state)
        self._set_busy(True)
        self._message("正在启动服务…")

    def _set_busy(self, busy: bool) -> None:
        for widget in self._inputs:
            widget.setEnabled(not busy)
        self.start_button.setEnabled(not busy and not self._quitting)
        self.stop_button.setEnabled(busy and not self._quitting)

    def stop_service(self) -> None:
        if not self.controller.active:
            return
        self._state = "正在停止"
        self.status.setText(self._state)
        self.stop_button.setEnabled(False)
        self.controller.stop()

    def _poll(self) -> None:
        for kind, payload in self.controller.poll():
            if kind == "running":
                self._running = True
                self._started = time.monotonic()
                self._state = "运行中"
                self.status.setText(self._state)
                self.status.setStyleSheet(f"color: {RED};")
                self._message("服务已开始监听。朋友可登录后创建房间；局域网访问请确认防火墙已放行。")
            elif kind == "stats":
                self._online, self._rooms = payload["online"], payload["rooms"]
                self.counts.setText(f"{self._online} 人在线  ·  {self._rooms} 个房间")
            elif kind == "error":
                self._failed = True
                self._message(payload["message"])
            elif kind == "stopped":
                self._running = False
                self._online = self._rooms = 0
                self.counts.setText("0 人在线  ·  0 个房间")
                self._state = "启动失败" if self._failed else "已停止"
                self.status.setText(self._state)
                self.status.setStyleSheet(f"color: {INK};")
                self._set_busy(False)
                if not self._failed:
                    self._message("服务已停止。房间已清空，客户端的本地记录不受影响。")
        if self._running:
            elapsed = int(time.monotonic() - self._started)
            self.uptime.setText(f"{elapsed // 3600:02}:{elapsed // 60 % 60:02}:{elapsed % 60:02}")
        if time.monotonic() - self._addresses_updated > 5 and not self.lan_address.view().isVisible():
            self._refresh_addresses()
        self.copy_local.setEnabled(self._running)
        self.copy_lan.setEnabled(self._running and self.lan_address.count() > 0)
        self.copy_public.setEnabled(self._running and bool(self.public_url.text().strip()))
        if self._tray:
            self._tray.setToolTip(f"HeartApp 服务端 · {self._state}")
        if self._quitting and not self.controller.active:
            self.close()

    def copy_address(self, kind: int) -> None:
        if not self._running:
            return
        addresses = [self.local_address.text(), self.lan_address.currentText(), self.settings.public_address]
        if addresses[kind]:
            self._copy_text(addresses[kind], "地址已复制，不包含密码。请将访问密码另行告知朋友。")

    def _copy_text(self, text: str, success: str) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        if clipboard.text() == text:
            self._message(success)
        else:
            self._message("无法访问系统剪贴板，请选中地址手动复制，或稍后重试。")

    def copy_diagnostics(self) -> None:
        text = (f"HeartApp Server {__version__}\n状态：{self._state}\n"
                f"监听范围：{self.settings.scope}\n端口：{self.settings.port}\n"
                f"连接上限：{self.settings.max_clients}\n每房间：{self.settings.room_size}\n"
                f"在线：{self._online}\n房间：{self._rooms}\n公网可达性：未检测")
        self._copy_text(text, "诊断信息已复制，不包含密码、昵称或心率。")

    def request_quit(self) -> None:
        self._quitting = True
        self._set_busy(True)
        self.quit_button.setEnabled(False)
        self.controller.stop()
        if self.controller.active:
            self.status.setText("正在退出")
        else:
            self.close()

    def closeEvent(self, event) -> None:
        if not self._quitting and self._tray is not None:
            self.hide()
            self._tray.showMessage("HeartApp 服务端", "面板已收起，服务状态保持不变。右键托盘可停止或退出。")
            event.ignore()
            return
        if self.controller.active:
            self.request_quit()
            event.ignore()
            return
        self.timer.stop()
        if self._tray:
            self._tray.hide()
        event.accept()
        self.deleteLater()
        if self._exit_on_close:
            QApplication.instance().quit()


def main() -> int:
    import argparse
    import sys
    from ...paths import app_dir
    parser = argparse.ArgumentParser(description="HeartApp 服务端控制面板")
    parser.add_argument("--config", type=Path, default=app_dir() / "data/server-panel.json")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("HeartApp Server")
    window = ServerPanel(args.config, exit_on_close=True)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
