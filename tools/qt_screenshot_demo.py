"""Qt 版截图脚本：跑到指定状态直接 grab() 窗口存图。

Qt 可以直接把窗口渲染成图片，不需要像 Tkinter 那样用 Win32 抓屏。

    .venv\\Scripts\\python.exe tools\\qt_screenshot_demo.py <输出文件> [秒数] [live|history] [profile]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from heartapp.engine import Engine  # noqa: E402
from heartapp.store import HeartRateStore  # noqa: E402
from heartapp.ui.qt.app import HeartAppWindow, _Dialogs  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_qt_shot.png"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 32.0
TAB = sys.argv[3] if len(sys.argv) > 3 else "live"
PROFILE = sys.argv[4] if len(sys.argv) > 4 else "interval"

_Dialogs.quiet = True

DB_PATH = ROOT / "_test_output" / "qt_demo.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
for suffix in ("", "-wal", "-shm"):
    p = Path(str(DB_PATH) + suffix)
    if p.exists():
        p.unlink()

app = QApplication.instance() or QApplication(sys.argv[:1])
store = HeartRateStore(DB_PATH)
engine = Engine(store)
engine.start()

window = HeartAppWindow(store, engine, db_path=ROOT / "data" / "heartrate.db")
window.show()


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


try:
    pump(1.0)

    # 选模拟模式 + 指定场景
    window._sim_chip.setChecked(True)
    window._on_sim_toggled()
    from heartapp.simulator import PROFILES

    if PROFILE in PROFILES:
        window._profile_combo.setCurrentText(PROFILES[PROFILE])
    window.on_connect_toggle()
    pump(2.0)

    window.on_record_toggle()
    pump(max(4.0, SECONDS - 6.0))

    if TAB == "history":
        # 先停一次记录，历史里才有已完成的会话
        window.on_record_toggle()
        pump(1.2)
        window._tabbar.select(1)
        pump(1.0)
        if window.tree.rowCount() > 0:
            window.tree.selectRow(0)
            pump(1.0)

    window.grab().save(str(OUT))
    print(f"已保存 {OUT}")
finally:
    try:
        engine.shutdown()
    except Exception:
        pass
    try:
        store.close()
    except Exception:
        pass
    window.close()
