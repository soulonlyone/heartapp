"""合成四人曲线的实际 GUI 预览，不连接真实手表或读取个人历史。"""
from pathlib import Path
from datetime import datetime
import math
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication
from heartapp.engine import Engine
from heartapp.hr import HeartRateSample
from heartapp.store import HeartRateStore
from heartapp.ui.qt.app import HeartAppWindow, _Dialogs

app = QApplication([])
_Dialogs.quiet = True
out = ROOT / "_test_output" / "room-comparison"
out.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=out) as folder:
    store = HeartRateStore(Path(folder) / "preview.db")
    engine = Engine(store)
    window = HeartAppWindow(store, engine)
    window._poll_timer.stop()
    window.show()
    engine.state, engine._source_kind = "connected", "sim"
    now = time.monotonic()
    window._room_active = True
    window._room_code, window._room_self_id = "DEMX2345", "local"
    window._room_panel.name.setText("玩家一")
    window._room_panel.share.setChecked(True)
    window._room_history.clear(now - 300)
    names = ("玩家一", "阿青", "小林", "阿澈")
    keys = ("self", "peer1", "peer2", "peer3")
    members = []
    for index, (key, name) in enumerate(zip(keys, names)):
        last = 0
        for t in range(301):
            if index == 2 and 160 <= t <= 178:
                window._room_history.append(key, now - 300 + t, None)
                continue
            bpm = round((78, 88, 69, 83)[index] + 4 * math.sin(t / (13 + index * 2) + index)
                        + (24 - index * 3) * math.exp(-((t - 142 - index * 8) / 24) ** 2)
                        + (16 + index * 2) * math.exp(-((t - 262 + index * 4) / 16) ** 2))
            window._room_history.append(key, now - 300 + t, bpm)
            last = bpm
        members.append(dict(id="local" if index == 0 else key, name=name, bpm=last,
                            sharing=True, simulated=True))
    window._room_members, window._room_updated = members, now
    window._last_sample = HeartRateSample(ts=datetime.now(), bpm=members[0]["bpm"], contact=True, source="sim")
    window._last_reading_clock = now
    window._room_panel.set_active(True, joined=True)
    window._handle_event("status", dict(state="connected", detail="四人曲线模拟预览 · 非真实心率"))
    window._sync_room_home()
    app.processEvents()
    window._draw_live_chart()
    window._room_overlay.show()
    app.processEvents()
    window.grab().save(str(out / "main.png"))
    window._room_overlay.grab().save(str(out / "overlay.png"))
    plot = window._room_chart._plot()
    window._room_chart._hover = QPointF(plot.left() + plot.width() * .64, plot.top() + 60)
    window._room_chart.update()
    app.processEvents()
    window.grab().save(str(out / "hover.png"))
    window._room_chart._hover = None
    window.resize(920, 680)
    app.processEvents()
    window.grab().save(str(out / "main-small.png"))
    window.close()
    store.close()
print("FOUR PLAYER COMPARISON PREVIEW PASS")
