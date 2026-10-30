"""gui_smoke_test.py —— 自动把界面跑一遍：建窗口、连模拟源、记录、画图、读历史。

不需要人工操作，也不需要有手表。用来确认界面代码不会在真实使用中第一秒就崩，
并且「事件 → 控件 → 图表」这条链路是通的。
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from heartapp.engine import Engine
from heartapp.store import HeartRateStore
from heartapp.ui.app import HeartAppFrame

DB = Path("_test_output/gui_smoke.db")
DB.parent.mkdir(parents=True, exist_ok=True)
for suffix in ("", "-wal", "-shm"):
    p = Path(str(DB) + suffix)
    if p.exists():
        p.unlink()

problems = []
store = HeartRateStore(DB)
engine = Engine(store)
engine.start()

root = tk.Tk()
root.geometry("1080x780")
frame = HeartAppFrame(root, store, engine, db_path=DB)


def pump(seconds: float) -> None:
    """代替 mainloop 跑事件循环。"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)


try:
    print("[1] 窗口构建完成")
    pump(0.5)

    print("[2] 勾选模拟模式并连接")
    frame._sim_var.set(True)
    frame._on_sim_toggle()
    frame.on_connect_toggle()
    pump(2.0)
    print("    状态栏:", frame._status_var.get()[:70])
    if not engine.is_running:
        problems.append("模拟源没有启动")

    print("[3] 开始记录")
    frame.on_record_toggle()
    pump(9.0)
    session_id = frame._session_id
    print("    当前会话:", session_id, " 记录状态:", frame._record_var.get())
    if session_id is None:
        problems.append("点击开始记录后没有创建会话")
    if frame._rec_n < 5:
        problems.append(f"界面累计采样过少: {frame._rec_n}")
    print("    界面累计采样:", frame._rec_n, " 统计:", {k: v.get() for k, v in frame._stat_vars.items()})
    if not frame.live_chart.points:
        problems.append("实时趋势图没有数据点")
    else:
        print("    实时图点数:", len(frame.live_chart.points))

    # 区间时长必须真的在累加：曾经这里只按平均值归到单一区间，
    # 结果整段记录被显示成「Z4 100%」，是错的。
    zone_total = sum(frame._rec_zones.values())
    print("    区间累计秒数:", round(zone_total, 1), " 分布:",
          {k: round(v, 1) for k, v in frame._rec_zones.items()})
    if zone_total <= 0:
        problems.append("记录过程中区间时长没有累加")
    elif frame._rec_n >= 6 and zone_total < frame._rec_n - 3:
        problems.append(f"区间累计 {zone_total:.1f}s 远少于采样数 {frame._rec_n}")
    if not frame.zone_bar.zone_seconds:
        problems.append("区间条控件始终是空的（界面上会显示「暂无数据」）")

    print("[4] 停止记录")
    frame.on_record_toggle()
    pump(1.5)
    if engine.is_recording:
        problems.append("停止记录后引擎仍处于记录状态")
    db_count = store.sample_count(session_id) if session_id else 0
    print("    落库条数:", db_count)
    if db_count < 5:
        problems.append(f"落库条数过少: {db_count}")

    print("[5] 历史页刷新与选中")
    frame._refresh_history()
    pump(0.5)
    children = frame.tree.get_children()
    print("    历史行数:", len(children))
    if not children:
        problems.append("历史列表为空")
    else:
        frame.tree.selection_set(children[0])
        pump(1.0)
        if not frame.history_chart.points:
            problems.append("历史曲线没有数据点")
        else:
            print("    历史图点数:", len(frame.history_chart.points))
        print("    摘要:", frame._history_summary_var.get().replace("\n", " | ")[:110])

    print("[6] 导出 CSV")
    out = Path("_test_output/gui_smoke_export.csv")
    store.export_csv(session_id, out)
    rows = out.read_text(encoding="utf-8-sig").splitlines()
    print("    CSV 行数:", len(rows))
    if len(rows) != db_count + 1:
        problems.append(f"CSV 行数 {len(rows)} != 落库 {db_count} + 1")

    print("[7] 打开设置对话框（检查自绘控件在弹窗里也能取对底色）")
    from heartapp.ui.app import SettingsDialog

    dialog = SettingsDialog(frame, store)
    pump(0.8)
    dialog._age_var.set("35")
    dialog._update_hint()
    pump(0.3)
    dialog.destroy()
    pump(0.4)

    print("[8] 检查界面有没有内部错误")
    status = frame._status_var.get()
    if status.startswith("⚠"):
        problems.append("界面报内部错误: " + status)

    print("[9] 关闭窗口")
    frame.close()
    pump(0.3)

finally:
    try:
        frame.close()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass
    try:
        store.close()
    except Exception:
        pass

print()
if problems:
    print("GUI SMOKE FAIL:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("GUI SMOKE PASS  — 界面全流程正常")
