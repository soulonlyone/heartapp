"""启动两个模拟玩家和一个本机转发服务，供交互体验。"""
from __future__ import annotations

from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from heartapp.engine import Engine
from heartapp.store import HeartRateStore
from heartapp.ui.qt.app import HeartAppWindow


def main() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = QApplication([])
    app.setApplicationName("HeartApp 联机本机演示")
    process = subprocess.Popen(
        [sys.executable, "-m", "heartapp.relay", "--port", str(port)], cwd=ROOT,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    windows, engines, stores = [], [], []
    folder = ROOT / "_test_output" / "room-demo"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        for index, name in enumerate(("玩家一", "玩家二")):
            store = HeartRateStore(folder / f"player{index + 1}.db")
            store.close_stale_sessions()
            engine = Engine(store).start()
            window = HeartAppWindow(store, engine)
            stores.append(store)
            engines.append(engine)
            windows.append(window)
            window.setWindowTitle(f"HeartApp · {name} · 模拟演示")
            window._room_panel.address.setText(f"ws://127.0.0.1:{port}")
            window._room_panel.name.setText(name)
            window._tabbar.select(3)
            window._sim_chip.setChecked(True)
            window._on_sim_toggled()
            engine.connect_simulator("gaming")
            window.move(60 + index * 100, 60 + index * 70)
            window.show()
        # 创建和加入仍由用户操作，可真实体验房间码；共享也默认关闭。
        timer = QTimer()
        def check_server():
            if process.poll() is not None:
                for window in windows:
                    if not window._closed:
                        window._room_panel.status.setText("本机演示服务未能启动，请检查依赖或端口，关闭演示后重试。")
                timer.stop()
        timer.timeout.connect(check_server)
        timer.start(1000)
        return app.exec()
    finally:
        for engine in engines:
            engine.shutdown()
        for store in stores:
            store.close()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


if __name__ == "__main__":
    sys.exit(main())
