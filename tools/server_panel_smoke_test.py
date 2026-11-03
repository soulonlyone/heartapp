"""真实本机服务与 Qt 面板；不安装系统任务、不操作防火墙。"""
from pathlib import Path
import json
import socket
import sys
import tempfile
import time
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
from heartapp.room_client import RoomClient
from heartapp.ui.qt.server_panel import ServerPanel


class Clipboard:
    # 部分自动化桌面无法读写系统剪贴板，使用替身验证发送内容和拒绝路径。
    value = ""
    reject = False

    def setText(self, text):
        self.value = "" if self.reject else text

    def text(self):
        return self.value


def wait(predicate, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("面板状态等待超时")


app = QApplication([])
out = ROOT / "_test_output/server-panel"
out.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=out) as temporary:
    config = Path(temporary) / "panel.json"
    panel = ServerPanel(config)
    clients = [RoomClient(), RoomClient()]
    events = [[], []]

    def client_event(index, kind):
        events[index].extend(clients[index].poll_events())
        return any(event == kind for event, _ in events[index])

    panel.show()
    try:
        panel.scope.setCurrentIndex(1)
        panel.password.setText("演示口令 % '$")
        panel.public_url.setText("https://hr.example.com")
        wait(lambda: panel.isVisible())
        panel.grab().save(str(out / "idle.png"))

        original_port = panel.port.value()
        panel.port.setText("")
        panel.start_service()
        assert not panel.controller.active and "端口" in panel.notice.text()
        panel.port.setValue(original_port)

        # 配置保存失败必须阻止启动，并允许恢复后重试。
        with patch("heartapp.ui.qt.server_panel.save_settings", side_effect=PermissionError("read only")):
            panel.start_service()
            assert not panel.controller.active
            assert "写入权限" in panel.notice.text()

        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            panel.port.setValue(port)
            panel.start_service()
            wait(lambda: panel.status.text() == "启动失败")
            assert "端口已被占用" in panel.notice.text()
            assert panel.start_button.isEnabled()
            panel.grab().save(str(out / "port-error.png"))

        panel.start_service()
        wait(lambda: panel._running)
        assert not panel.port.isEnabled()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as response:
            assert b"relay v2 OK" in response.read()
        assert "password" not in json.loads(config.read_text(encoding="utf-8"))
        assert panel.password.text() not in config.read_text(encoding="utf-8")
        address = f"ws://127.0.0.1:{port}"
        clients[0].login(address, "玩家一", password=panel.password.text())
        wait(lambda: client_event(0, "server_login"))
        clients[0].command("create", title="朋友的房间")
        wait(lambda: client_event(0, "room_welcome"))
        code = next(payload["room"] for kind, payload in events[0] if kind == "room_welcome")
        clients[1].connect(address, "玩家二", code=code, password=panel.password.text())
        wait(lambda: client_event(1, "room_welcome"))
        wait(lambda: panel._online == 2 and panel._rooms == 1)
        clipboard = Clipboard()
        with patch.object(QApplication, "clipboard", return_value=clipboard):
            panel.copy_address(0)
            assert clipboard.text() == address
            panel.copy_address(2)
            assert clipboard.text() == "wss://hr.example.com/"
            panel.copy_diagnostics()
            assert panel.password.text() not in clipboard.text()
            clipboard.reject = True
            panel.copy_address(0)
            assert "无法访问系统剪贴板" in panel.notice.text()
        panel.notice.setText("服务已开始监听。朋友可登录后创建房间；局域网访问请确认防火墙已放行。")
        panel.grab().save(str(out / "running.png"))

        panel.showMinimized()
        wait(lambda: panel.isMinimized())
        assert panel.controller.active
        panel.showNormal()
        if panel._tray is not None:
            with patch("heartapp.ui.qt.server_panel.QSystemTrayIcon.showMessage"):
                panel.close()
            wait(lambda: not panel.isVisible())
            assert panel.controller.active
            panel.show_panel()
            wait(lambda: panel.isVisible())
        panel.stop_service()
        wait(lambda: not panel.controller.active and panel.status.text() == "已停止")
        assert panel.start_button.isEnabled()
        assert not panel.copy_local.isEnabled()
        panel.start_service()
        wait(lambda: panel._running)
        panel.request_quit()
        wait(lambda: not panel.controller.active)
        app.processEvents()
        print("SERVER PANEL SMOKE PASS")
    finally:
        for client in clients:
            client.leave()
        panel.controller.stop()
        wait(lambda: not panel.controller.active)
