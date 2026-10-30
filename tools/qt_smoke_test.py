"""Qt 界面冒烟测试：自动建窗、连模拟源、记录、画图、截图。

    .venv\\Scripts\\python.exe tools\\qt_smoke_test.py [输出图片路径]
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

# 模态弹窗会开嵌套事件循环，让自动化脚本卡住；测试期间静音
_Dialogs.quiet = True

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_qt_shot.png"
DB_PATH = ROOT / "_test_output" / "qt_smoke.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
for suffix in ("", "-wal", "-shm"):
    p = Path(str(DB_PATH) + suffix)
    if p.exists():
        p.unlink()

problems = []

app = QApplication.instance() or QApplication(sys.argv[:1])
store = HeartRateStore(DB_PATH)
engine = Engine(store)
engine.start()

window = HeartAppWindow(store, engine, db_path=DB_PATH)
window.show()


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def overlay_visible() -> bool:
    """同时检查原生窗口；Qt 的 isVisible 可能看不到 Windows 隐藏 owner 工具窗。"""
    overlay = window._overlay
    if sys.platform == "win32":
        import ctypes
        return overlay.isVisible() and bool(
            ctypes.windll.user32.IsWindowVisible(ctypes.c_void_p(int(overlay.winId())))
        )
    return overlay.isVisible()


try:
    print("[1] 窗口构建")
    pump(1.2)
    print(f"    尺寸 {window.width()}x{window.height()}")

    print("[2] 模拟模式 + 连接")
    window._sim_chip.setChecked(True)
    window._on_sim_toggled()
    window.on_connect_toggle()
    pump(2.5)
    print("    状态:", window._status_label.text()[:60])
    if not engine.is_running:
        problems.append("模拟源没有启动")

    print("[3] 开始记录")
    window.on_record_toggle()
    pump(2.0)
    window.on_overlay_toggle()
    if not window._overlay.isVisible():
        problems.append("心率小窗没有显示")
    window._last_reading_clock = time.monotonic() - 10
    window._update_pc_reading()
    if window._overlay.bpm is not None:
        problems.append("过期读数仍显示为实时心率")
    print("    最小化主窗口，检查小窗与持续采样")
    samples_before = window._rec_n
    window.showMinimized()
    pump(3.0)
    if not window.isMinimized() or not overlay_visible():
        problems.append("主窗口最小化后小窗消失")
    if window._rec_n <= samples_before or window._overlay.bpm is None:
        problems.append("主窗口最小化后小窗读数或记录未更新")
    window.on_overlay_toggle()
    window.on_overlay_toggle()
    window._passthrough.setChecked(True)
    pump(0.2)
    window._passthrough.setChecked(False)
    pump(0.2)
    if not overlay_visible() or not window.isMinimized():
        problems.append("主窗口最小化时切换小窗或穿透设置异常")
    window.showNormal()
    pump(0.3)
    if not overlay_visible():
        problems.append("还原主窗口后小窗消失")
    window.on_overlay_toggle()
    window.showMinimized()
    pump(0.2)
    window.showNormal()
    pump(0.2)
    if overlay_visible():
        problems.append("主窗口最小化或还原擅自打开已隐藏的小窗")
    window.on_overlay_toggle()
    pump(4.0)
    if window._overlay_hotkey.registered:
        import ctypes
        ctypes.windll.user32.PostThreadMessageW(ctypes.windll.kernel32.GetCurrentThreadId(),
                                               0x0312, 0x4842, 0)
        pump(0.3)
        if window._overlay.isVisible():
            problems.append("全局快捷键未隐藏小窗")
        window.on_overlay_toggle()
    window._opacity.setValue(80)
    if store.get_int_setting("overlay_opacity", 0) != 80:
        problems.append("小窗透明度未保存")
    window._overlay_scale.setValue(125)
    if window._overlay.width() != 400 or store.get_int_setting("overlay_scale", 0) != 125:
        problems.append("小窗大小或记忆设置无效")
    window._overlay_scale.setValue(100)
    window._passthrough.setChecked(True)
    pump(0.2)
    window._passthrough.setChecked(False)
    print("    会话:", window._session_id, "| 累计采样:", window._rec_n)
    if window._session_id is None:
        problems.append("没有创建会话")
    if window._rec_n < 5:
        problems.append(f"界面累计采样过少: {window._rec_n}")
    if not window.live_chart.points:
        problems.append("实时趋势图没有数据点")
    print("    统计:", {k: v.text() for k, v in window._stat_values.items()})
    print("[4] 截图")
    window.grab().save(str(OUT))
    print("    已保存", OUT)

    print("[5] 停止记录")
    # 会话 id 在停止后会被清空，先抓下来
    sid = window._session_id
    window.on_record_toggle()
    pump(1.5)
    if engine.is_recording:
        problems.append("停止记录后引擎仍在记录")
    count = store.sample_count(sid) if sid else 0
    print("    落库条数:", count)
    if window._last_session_id != sid:
        problems.append("停止后丢失刚记录的导出入口")
    if count < 5:
        problems.append(f"落库条数过少: {count}")

    print("[6] 历史页")
    window._tabbar.select(1)
    pump(1.0)
    print("    行数:", window.tree.rowCount())
    if window.tree.rowCount() == 0:
        problems.append("历史列表为空")
    else:
        window.tree.selectRow(0)
        pump(1.0)
        if not window.history_chart.points:
            problems.append("历史曲线没有数据点")
        print("    历史图点数:", len(window.history_chart.points))
        if not store.pc_context(sid):
            problems.append("PC 会话元数据未保存")
        window.history_chart.view = (0, 10)
        window.history_chart.reset_view()
        if window.history_chart.view is not None:
            problems.append("历史曲线全程按钮未复位")

    print("[7] 小窗设置")
    window._tabbar.select(2)
    pump(0.3)
    if window._stack.currentIndex() != 2:
        problems.append("小窗设置导航失效")
    window._tabbar.select(0)

    print("[8] 导出 CSV（正常路径 + 无权限兜底）")
    # 别在测试里真的弹资源管理器
    window._open_folder = lambda _p: None
    export_dir = DB_PATH.parent
    for stale in export_dir.glob("心率记录_*.csv"):
        stale.unlink()

    window._export_session(sid, directory=export_dir)
    pump(0.3)
    made = sorted(p for p in export_dir.glob("心率记录_*.csv") if not p.stem.endswith("_events"))
    print("    正常导出产物:", len(made))
    if not made:
        problems.append("导出到数据目录后没有生成 CSV")
    else:
        rows = made[-1].read_text(encoding="utf-8-sig").splitlines()
        print("    CSV 行数:", len(rows))
        if len(rows) < 2:
            problems.append("导出的 CSV 没有数据行")

    # 故意挑一个必然写不进去的目录，验证会自动兜底到数据目录
    for stale in export_dir.glob("心率记录_*.csv"):
        stale.unlink()
    blocked = Path(r"C:\Windows\System32\__heartapp_no_permission__")
    window._export_session(sid, directory=blocked)
    pump(0.3)
    fallback = sorted(export_dir.glob("心率记录_*.csv"))
    print("    兜底导出产物:", len(fallback))
    if not fallback:
        problems.append("无权限时没有自动兜底到数据目录")

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
    if overlay_visible():
        problems.append("退出主窗口后小窗未关闭")

print()
if problems:
    print("QT SMOKE FAIL:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("QT SMOKE PASS")
