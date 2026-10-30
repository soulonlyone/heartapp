"""临时诊断：打印改版后各控件的实际尺寸，确认没有控件被挤出可视区。"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from heartapp.engine import Engine  # noqa: E402
from heartapp.store import HeartRateStore  # noqa: E402
from heartapp.ui.app import HeartAppFrame  # noqa: E402

out = Path("_test_output")
out.mkdir(parents=True, exist_ok=True)
store = HeartRateStore(out / "measure.db")
engine = Engine(store)
engine.start()

root = tk.Tk()
root.geometry("1160x880")
frame = HeartAppFrame(root, store, engine)
for _ in range(90):
    root.update()
    time.sleep(0.02)

print(f"窗口            {root.winfo_width()} x {root.winfo_height()}")
print(f"顶栏卡          {frame._header_card.winfo_width()} x {frame._header_card.winfo_height()}")
print(f"主体卡          {frame._body_card.winfo_width()} x {frame._body_card.winfo_height()}")
print(f"底栏卡          {frame._footer_card.winfo_width()} x {frame._footer_card.winfo_height()}")
print(f"表盘            {frame.bpm_panel.winfo_width()} x {frame.bpm_panel.winfo_height()}")
print(f"趋势图          {frame.live_chart.winfo_width()} x {frame.live_chart.winfo_height()}")
print(f"区间条          {frame.zone_bar.winfo_width()} x {frame.zone_bar.winfo_height()}  (需要 ~118)")

page = frame.notebook._pages[0]
print(f"\n实时页          {page.winfo_width()} x {page.winfo_height()}")
rows: dict = {}
for w in page.grid_slaves():
    rows.setdefault(int(w.grid_info().get("row", -1)), []).append(w)
for r in sorted(rows):
    for w in rows[r]:
        print(f"  row {r}: y={w.winfo_y():>4} h={w.winfo_height():>4}  {type(w).__name__}")

btn = frame._record_btn
print(f"\n开始记录按钮    y={btn.winfo_y()} h={btn.winfo_height()} "
      f"ismapped={btn.winfo_ismapped()} 在可视区内={btn.winfo_y() + btn.winfo_height() <= page.winfo_height()}")

root.destroy()
engine.shutdown()
store.close()
