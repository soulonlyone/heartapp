"""给界面截图用的一次性脚本：打开窗口 → 连模拟源 → 自动开始记录 → 保持窗口在前。

    用法：.venv\\Scripts\\python.exe tools\\screenshot_demo.py [秒数] [live|history]
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from heartapp.engine import Engine  # noqa: E402
from heartapp.store import HeartRateStore  # noqa: E402
from heartapp.ui.app import HeartAppFrame  # noqa: E402

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
TAB = sys.argv[2] if len(sys.argv) > 2 else "live"
DEMO_DB = ROOT / "data" / "_screenshot_demo.db"
DEMO_DB.parent.mkdir(parents=True, exist_ok=True)

store = HeartRateStore(DEMO_DB)
store.close_stale_sessions()
engine = Engine(store)
engine.start()

root = tk.Tk()
root.title("心率记录仪 — 从智能手表读取并记录心率")
root.geometry("1160x880+30+10")
root.minsize(980, 700)

frame = HeartAppFrame(root, store, engine, db_path=ROOT / "data" / "heartrate.db")
root.attributes("-topmost", True)
root.lift()

frame._sim_var.set(True)
frame._on_sim_toggle()
engine.connect_simulator("interval")


def _begin_recording() -> None:
    frame._record_btn.invoke()


def _show_history() -> None:
    """切到历史页并选中第一条记录。"""
    try:
        frame.notebook.select(1)
        frame._refresh_history()
        children = frame.tree.get_children()
        if children:
            frame.tree.selection_set(children[0])
            frame.on_history_select()
    except Exception:
        pass


root.after(1200, _begin_recording)
if TAB == "history":
    # 先记录一段时间攒出曲线，再切过去
    root.after(int(max(3.0, SECONDS - 18.0) * 1000), _show_history)

try:
    end = time.monotonic() + SECONDS
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)
finally:
    try:
        frame.close()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass
    engine.shutdown()
    store.close()
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DEMO_DB) + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
