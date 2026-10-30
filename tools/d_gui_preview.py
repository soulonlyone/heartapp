"""仅使用合成数据生成 D 版界面截图，不读取用户健康记录。"""
from pathlib import Path
import math
import sys
import tempfile
import time
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PySide6.QtWidgets import QApplication
from heartapp.engine import Engine
from heartapp.hr import HeartRateSample
from heartapp.store import HeartRateStore
from heartapp.ui.qt.app import HeartAppWindow, _Dialogs

_Dialogs.quiet = True
out = ROOT / "_test_output" / "design-d"
out.mkdir(parents=True, exist_ok=True)
app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory(dir=out) as folder:
    store = HeartRateStore(Path(folder) / "preview.db")
    engine = Engine(store)
    window = HeartAppWindow(store, engine)
    window.show()
    window._poll_timer.stop()
    engine._source_kind = "sim"
    engine.state = "connected"
    start = datetime.now() - timedelta(seconds=5076)
    clock = time.monotonic() - 5076
    sid = store.start_session(started_at=start, source="sim", device_name="模拟手表")
    store.set_pc_context(sid, "游戏心率示例", "游戏")
    engine._session_id = sid
    window._handle_event("session_started", {"session_id": sid, "started_at": start, "clock": clock})
    rows = []
    for t in range(0, 5076, 5):
        if 3290 < t < 3520:
            continue
        bpm = round(76 + 5 * math.sin(t / 180) + 3 * math.sin(t / 43)
                    + 31 * math.exp(-((t - 2840) / 190) ** 2))
        sample = HeartRateSample(start + timedelta(seconds=t), bpm, True, source="sim", t_offset=t)
        window._handle_event("sample", {"sample": sample, "received_at": clock + t})
        rows.append((sample.ts, t, bpm, True, None))
    store.add_samples(sid, rows)
    window._handle_event("sample", {"sample": HeartRateSample(datetime.now(), 82, True, source="sim", t_offset=5076),
                                    "received_at": time.monotonic()})
    window._update_stat_labels()
    window._update_pc_reading()
    window._draw_live_chart()
    app.processEvents()
    window.grab().save(str(out / "main.png"))
    window._live_range.showPopup()
    app.processEvents()
    window._live_range.view().window().grab().save(str(out / "range-menu.png"))
    window._live_range.hidePopup()
    window.resize(920, 680)
    app.processEvents()
    window.grab().save(str(out / "main-small.png"))
    window.resize(1120, 780)
    app.processEvents()
    window._overlay.show()
    app.processEvents()
    window._overlay.grab().save(str(out / "overlay.png"))
    store.end_session(sid)
    engine._session_id = None
    window._session_id = None
    window._tabbar.select(1)
    window.tree.selectRow(0)
    app.processEvents()
    window.grab().save(str(out / "history.png"))
    window._tabbar.select(2)
    app.processEvents()
    window.grab().save(str(out / "settings.png"))
    window.close()
    store.close()
print("D GUI PREVIEW PASS")
