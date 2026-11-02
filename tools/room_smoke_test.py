"""真实本机转发服务 + 两个 GUI 客户端的完整联机冒烟。"""
from __future__ import annotations

import asyncio
import ctypes
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtWidgets import QApplication
from heartapp.engine import Engine
from heartapp.relay import Relay, start_server
from heartapp.store import HeartRateStore
from heartapp.ui.qt.app import HeartAppWindow, _Dialogs


class LocalRelay:
    def __init__(self):
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.port = 0
        self.error = None
        self.loop = None
        self.relay = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        async def serve():
            relay = Relay()
            self.loop, self.relay = asyncio.get_running_loop(), relay
            async with await start_server(relay, port=0) as server:
                self.port = server.sockets[0].getsockname()[1]
                ticker = asyncio.create_task(relay.ticker())
                self.ready.set()
                try:
                    while not self.stop.is_set():
                        await asyncio.sleep(.05)
                finally:
                    ticker.cancel()
                    await asyncio.gather(ticker, return_exceptions=True)
        try:
            asyncio.run(serve())
        except Exception as exc:
            self.error = exc
            self.ready.set()


def wait(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("等待联机状态超时")


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(.02)


app = QApplication([])
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass
_Dialogs.quiet = True
relay = LocalRelay()
relay.thread.start()
assert relay.ready.wait(5) and relay.error is None, relay.error
out = ROOT / "_test_output" / "rooms"
out.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=out) as folder:
    stores, engines, windows = [], [], []
    try:
        for index in range(2):
            store = HeartRateStore(Path(folder) / f"player{index}.db")
            stores.append(store)
            engine = Engine(store).start()
            engines.append(engine)
            window = HeartAppWindow(store, engine)
            windows.append(window)
            window.show()
            window._tabbar.select(0)
            panel = window._room_panel
            panel.address.setText(f"ws://127.0.0.1:{relay.port}")
            panel.name.setText(("玩家一", "玩家二")[index])
        first, second = windows
        first._room_manage_btn.click()
        assert first._room_dialog.isVisible()
        with patch.object(first.store, "set_setting", side_effect=OSError("settings read only")):
            first._room_panel.login.click()
            assert not first._server_logged and first._room_panel.login.isEnabled()
            assert "未能登录" in first._room_panel.server_status.text()
        for window in windows:
            window._room_panel.login.click()
            wait(lambda: window._server_logged)
            assert not window._room_code
        first._room_panel.create.click()
        wait(lambda: bool(first._room_code))
        wait(lambda: second._room_panel.rooms.rowCount() == 1)
        second._room_panel.rooms.selectRow(0)
        assert second._room_panel.code.text() == first._room_code
        second._room_panel.join.click()
        wait(lambda: len(first._room_members) == len(second._room_members) == 2)
        wait(lambda: first._live_chart_stack.currentIndex() == 1)
        assert not first._room_dialog.isVisible()
        assert not first._room_panel.share.isChecked() and not second._room_panel.share.isChecked()
        print("[1] 创建 / 加入私人房间，默认不共享 PASS")
        for engine, window in zip(engines, windows):
            engine.connect_simulator("gaming")
            wait(lambda: engine.state == "connected")
            window.on_record_toggle()
            wait(lambda: window._session_id is not None)
        first._room_panel.share.setChecked(True)
        wait(lambda: any(m.get("bpm") for m in second._room_members))
        own = next(m for m in first._room_members if m["id"] == second._room_self_id)
        assert own["bpm"] is None
        second._room_panel.share.setChecked(True)
        wait(lambda: all(m.get("bpm") and m.get("simulated") for m in first._room_members))
        wait(lambda: len(first._room_chart.series) == 2 and all(s["points"] for s in first._room_chart.series.values()))
        key = next(k for k in first._room_legends if k != "self")
        first._room_legends[key].click()
        assert key not in first._room_chart.series
        first._room_legends[key].click()
        assert key in first._room_chart.series
        assert first._live_share.isChecked()
        first._room_chart.view = (-30, 0)
        first._live_range.setCurrentIndex(1)
        assert first._room_chart.view is None and first._room_chart.full_range() == (-1800, 0)
        first._live_range.setCurrentIndex(0)
        print("[2] 两端读数与模拟标识转发 PASS")
        identity = first._room_self_id
        first.on_room_leave()
        wait(lambda: first._server_logged and not first._room_pending and not first._room_code)
        assert first._room_panel.create.isEnabled()
        first._room_panel.code.setText(second._room_code)
        first._room_panel.join.click()
        wait(lambda: bool(first._room_code))
        assert first._room_self_id == identity and not first._live_share.isChecked()
        first._live_share.setChecked(True)
        wait(lambda: all(m.get("bpm") for m in first._room_members))
        print("    退房保持登录，并可直接重新加入 PASS")
        old_id = first._room_self_id
        old_code = first._room_code
        old_socket = relay.relay.rooms[old_code][old_id].socket
        async def disconnect_one():
            await relay.relay.rooms[old_code][old_id].socket.close(code=1012, reason="测试重连")
        asyncio.run_coroutine_threadsafe(disconnect_one(), relay.loop).result(timeout=3)
        wait(lambda: first._server_logged and relay.relay.rooms[old_code][old_id].socket is not None
             and relay.relay.rooms[old_code][old_id].socket is not old_socket)
        assert first._room_self_id == old_id
        wait(lambda: len(first._room_members) == 2 and all(m.get("bpm") for m in first._room_members))
        assert first._room_code == old_code
        print("    单人掉线后自动重新加入原房间 PASS")
        first.on_room_overlay_toggle()
        first.showMinimized()
        pump(.3)
        assert first._room_overlay.isVisible()
        if sys.platform == "win32":
            assert ctypes.windll.user32.IsWindowVisible(ctypes.c_void_p(int(first._room_overlay.winId())))
        first.showNormal()
        first.on_room_copy()
        assert first._room_code in QApplication.clipboard().text()
        first.grab().save(str(out / "room-page.png"))
        first._room_overlay.grab().save(str(out / "room-overlay.png"))
        print("[3] 复制邀请 / 最小化保持多人小窗 / 截图 PASS")
        first._room_panel.share.setChecked(False)
        wait(lambda: next(m for m in second._room_members if m["id"] == first._room_self_id)["bpm"] is None)
        print("[4] 关闭共享立即停止转发心率 PASS")
        assert not first._live_share.isChecked()
        assert first._room_display_members()[0]["bpm"] is not None
        counts = [w._rec_n for w in windows]
        relay.stop.set()
        pump(2.5)
        assert all(e.is_recording for e in engines)
        assert all(w._rec_n > n for w, n in zip(windows, counts))
        assert all(not any(m.get("bpm") for m in w._room_members) for w in windows)
        print("[5] 服务器停止，清空远端读数，本地采集与记录继续 PASS")
        ids = [w._session_id for w in windows]
        for window in windows:
            window.on_room_leave()
            assert not window._room_history.points and window._live_chart_stack.currentIndex() == 0
            window._titlebar.close_button.click()
            wait(lambda: window._closed)
        for engine, store, sid in zip(engines, stores, ids):
            engine.shutdown()
            path = store.db_path
            store.close()
            with HeartRateStore(path) as reopened:
                assert reopened.sample_count(sid) >= 2
                assert not reopened.get_session(sid).is_open
        print("[6] 两端退出后各自历史记录保留 PASS")
    finally:
        relay.stop.set()
        for window in windows:
            window.close()
        pump(.3)
        for engine in engines:
            engine.shutdown()
        for store in stores:
            store.close()
        relay.thread.join(timeout=3)
print("ROOM SMOKE PASS")
