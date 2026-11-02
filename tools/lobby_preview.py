"""真实本机服务下的服务器登录与房间目录截图，使用临时模拟身份。"""
from pathlib import Path
import os
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtWidgets import QApplication
from heartapp.engine import Engine
from heartapp.room_client import RoomClient
from heartapp.store import HeartRateStore
from heartapp.ui.qt.app import HeartAppWindow, _Dialogs

app = QApplication([])
_Dialogs.quiet = True
out = ROOT / "_test_output" / "lobby"
out.mkdir(parents=True, exist_ok=True)
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
process = subprocess.Popen([sys.executable, "-m", "heartapp.relay", "--port", str(port)], cwd=ROOT,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def wait(predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("lobby preview timed out")

peers = []
try:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.1):
                break
        except OSError:
            time.sleep(.05)
    address = f"ws://127.0.0.1:{port}"
    for name, title in (("玩家二", "今晚恐怖游戏"), ("玩家三", "周末四人开黑")):
        peer = RoomClient()
        peer.login(address, name)
        peer.command("create", title=title)
        peers.append(peer)
    with tempfile.TemporaryDirectory(dir=out) as folder:
        store = HeartRateStore(Path(folder) / "preview.db")
        window = HeartAppWindow(store, Engine(store))
        window.show()
        panel = window._room_panel
        panel.address.setText(address)
        panel.name.setText("玩家一")
        window._show_room_dialog()
        app.processEvents()
        window._room_dialog.grab().save(str(out / "logged-out.png"))
        panel.login.click()
        wait(lambda: window._server_logged and panel.rooms.rowCount() == 2)
        window._room_dialog.resize(700, 650)
        app.processEvents()
        window._room_dialog.grab().save(str(out / "logged-in.png"))
        panel.rooms.selectRow(0)
        panel.join.click()
        wait(lambda: bool(window._room_code))
        window.on_room_leave()
        wait(lambda: window._server_logged and not window._room_pending and not window._room_code)
        window._show_room_dialog()
        app.processEvents()
        window._room_dialog.grab().save(str(out / "after-leave.png"))
        window.on_server_logout()
        window.close()
        store.close()
finally:
    for peer in peers:
        peer.leave()
    time.sleep(.3)
    process.terminate()
    process.wait(timeout=3)
print("LOBBY PREVIEW PASS")
