"""命令行入口。

    python -m heartapp                     # 打开图形界面
    python -m heartapp --simulate          # 图形界面 + 模拟数据
    python -m heartapp list                # 扫描附近蓝牙设备，找心率广播
    python -m heartapp probe "OPPO Watch"  # 连接并检查有没有心率服务
    python -m heartapp record --simulate --duration 30
    python -m heartapp record --device "OPPO Watch X2" --duration 120 --csv
    python -m heartapp sessions            # 列出历史记录
    python -m heartapp export 3 --out out.csv
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from . import APP_NAME, __version__
from .metrics import format_duration
from .paths import cli_hint, default_db_path, default_export_path
from .simulator import DEFAULT_PROFILE, PROFILES
from .store import HeartRateStore

BLE_TIPS = """
如果找不到手表，按顺序检查：
  1. 电脑蓝牙是否打开：设置 → 蓝牙和其他设备。
  2. 手表是否开了「心率广播 / 心率分享」。
     例如 OPPO Watch 一般在 设置 → 运动与健康，或运动 App 里的心率分享；
     部分机型要先开始一个运动，广播开关才会出现。
  3. 手表是不是还被手机连着。有些手表同一时间只允许一个连接，
     可以在手机上临时关掉蓝牙，或断开手表与手机的连接。
  4. 把手表靠近电脑（1 米内），再重新扫描。
"""


def _setup_console() -> None:
    """Windows 控制台默认不是 UTF-8，中文会乱码或直接抛异常。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    import asyncio

    from . import ble

    print(f"正在扫描蓝牙设备（{args.timeout:.0f} 秒）…")
    try:
        devices = asyncio.run(
            ble.scan_devices(timeout=args.timeout, include_all=not args.heart_only)
        )
    except Exception as exc:  # noqa: BLE001
        print(f"扫描失败：{exc}")
        print(BLE_TIPS)
        return 1

    if not devices:
        print("没有扫描到任何设备。")
        print(BLE_TIPS)
        return 1

    print(f"\n找到 {len(devices)} 个设备：\n")
    print(f"  {'心率广播':<10}{'名称':<30}{'地址':<22}{'信号':>8}")
    print("  " + "-" * 68)
    for d in devices:
        mark = "♥ 有" if d.has_heart_rate else "—"
        print(f"  {mark:<10}{(d.name or '(未知)'):<30}{d.address:<22}{d.rssi:>5} dBm")

    heart = [d for d in devices if d.has_heart_rate]
    print()
    if heart:
        best = heart[0]
        print(f"✓ 有 {len(heart)} 个设备正在广播标准心率服务（0x180D）。")
        print(f"  推荐连接：{best.name or best.address}")
        print(f'  开始记录：{cli_hint()} record --device "{best.name or best.address}"')
    else:
        print("⚠ 没有设备广播标准心率服务（0x180D）。")
        print(BLE_TIPS)
        return 2
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    import asyncio

    from . import ble

    async def _run() -> int:
        print(f"正在查找 “{args.device}” …")
        try:
            device = await ble.find_device(args.device, timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"找不到设备：{exc}")
            print(BLE_TIPS)
            return 1
        print(f"找到 {device.name or device.address}（{device.address}），正在连接…")
        try:
            report = await ble.probe_device(device)
        except Exception as exc:  # noqa: BLE001
            print(f"连接失败：{exc}")
            print(BLE_TIPS)
            return 1
        print()
        print(report)
        return 0

    return asyncio.run(_run())


def cmd_record(args: argparse.Namespace) -> int:
    from .engine import Engine

    db_path = Path(args.db) if args.db else default_db_path()
    store = HeartRateStore(db_path)
    store.close_stale_sessions()
    engine = Engine(store)
    engine.start()

    stats: Optional[Any] = None
    session_id: Optional[int] = None
    try:
        print(f"{APP_NAME} v{__version__}")
        print(f"数据库：{db_path}")

        if args.simulate:
            profile = args.profile if args.profile in PROFILES else DEFAULT_PROFILE
            print(f"数据源：模拟（{PROFILES[profile]}）")
            engine.connect_simulator(profile)
            connect_timeout = 10.0
        else:
            if not args.device:
                print("错误：没有指定 --device。用 `list` 子命令先找设备。")
                return 2
            print(f"数据源：蓝牙手表 “{args.device}”")
            engine.connect_device(args.device)
            connect_timeout = args.connect_timeout

        print("正在连接…")
        if not engine.wait_for_state(("connected",), timeout=connect_timeout):
            print(f"连接失败：{engine.state_detail}")
            if not args.simulate:
                print(BLE_TIPS)
            return 1

        session_id = engine.start_recording(note=args.note, wait=True)
        print(f"已连接，开始记录（session #{session_id}）。")
        if args.duration:
            print(f"将记录 {format_duration(args.duration)}，按 Ctrl+C 可提前结束。")
        else:
            print("按 Ctrl+C 结束记录。")
        print()

        deadline = time.monotonic() + args.duration if args.duration else None
        last_print = 0.0
        last_bpm: Optional[int] = None
        count = 0
        started = time.monotonic()

        while True:
            for kind, payload in engine.poll_events():
                if kind == "sample":
                    sample = payload.get("sample")
                    if sample and sample.bpm > 0:
                        last_bpm = int(sample.bpm)
                        count += 1
                elif kind == "error":
                    print(f"\n⚠ {payload.get('message')}")

            if deadline is not None and time.monotonic() >= deadline:
                break

            now = time.monotonic()
            if now - last_print >= 1.0:
                last_print = now
                elapsed = now - started
                bar = "♥" if last_bpm else "…"
                line = f"  {bar} {format_duration(elapsed)}  当前 {last_bpm if last_bpm else '--':>3} bpm  已记录 {count} 条"
                print(line, end="\r", flush=True)

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n\n收到 Ctrl+C，正在保存…")
    finally:
        stats = engine.stop_recording(wait=True)
        engine.shutdown()
        print()

    if stats is not None:
        print_report(stats)

    if args.csv and session_id is not None:
        target = Path(args.out) if args.out else default_export_path(session_id)
        path = store.export_csv(session_id, target)
        print(f"\nCSV 已导出：{path}")

    store.close()
    return 0


def print_report(stats: Any) -> None:
    if stats is None or not stats.sample_count:
        print("本次没有采到任何数据。")
        print(BLE_TIPS)
        return

    def num(value: Optional[float], digits: int = 0) -> str:
        return "--" if value is None else f"{value:.{digits}f}"

    print("=" * 52)
    print("本次记录统计")
    print("=" * 52)
    print(f"  时长      {stats.duration_text}")
    print(f"  采样      {stats.sample_count} 条（有效 {stats.valid_sample_count}）")
    print(f"  平均      {num(stats.avg_bpm, 1)} bpm")
    print(f"  最高/最低 {num(stats.max_bpm)} / {num(stats.min_bpm)} bpm")
    print(f"  静息      {num(stats.resting_bpm, 1)} bpm")
    if stats.rmssd_ms is not None:
        print(f"  HRV       RMSSD {stats.rmssd_ms:.1f} ms（基于广播 RR，仅供参考）")
    else:
        print("  HRV       未收到足够的 RR 间期")
    print(f"  最大心率设定 {stats.max_hr} bpm")
    print("  区间时长：")
    total = sum(stats.zone_seconds.values()) or 1.0
    for name, seconds in stats.zone_seconds.items():
        if seconds <= 0:
            continue
        print(f"    {name:<8} {format_duration(seconds):>8}  {seconds / total * 100:5.1f}%")
    print("=" * 52)


def cmd_sessions(args: argparse.Namespace) -> int:
    db_path = Path(args.db) if args.db else default_db_path()
    store = HeartRateStore(db_path)
    rows = store.list_sessions(limit=args.limit)
    if not rows:
        print(f"还没有任何记录。（数据库：{db_path}）")
        store.close()
        return 0
    agg = store.session_bpm_aggregate([r.id for r in rows])
    print(f"数据库：{db_path}\n")
    print(f"  {'ID':>4}  {'开始时间':<20}{'时长':>9}{'来源':>6}{'样本':>7}{'平均':>7}{'最高':>6}{'最低':>6}  设备")
    print("  " + "-" * 92)
    for r in rows:
        avg, low, high, count = agg.get(r.id, (None, None, None, 0))
        duration = format_duration(r.duration_s) if r.ended_at else "进行中"
        src = {"ble": "手表", "sim": "模拟"}.get(r.source, r.source)
        print(
            f"  {r.id:>4}  {r.started_at:%Y-%m-%d %H:%M:%S}  {duration:>8}"
            f"{src:>6}{count:>7}"
            f"{(f'{avg:.0f}' if avg else '--'):>7}"
            f"{(str(high) if high else '--'):>6}"
            f"{(str(low) if low else '--'):>6}  {r.device_name or '-'}"
        )
    print(f"\n共 {len(rows)} 条。导出：{cli_hint()} export <ID>")
    store.close()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    db_path = Path(args.db) if args.db else default_db_path()
    store = HeartRateStore(db_path)
    session = store.get_session(args.session_id)
    if session is None:
        print(f"没有 ID 为 {args.session_id} 的记录。用 `sessions` 子命令查看。")
        store.close()
        return 1
    target = Path(args.out) if args.out else default_export_path(args.session_id)
    path = store.export_csv(args.session_id, target)
    count = store.sample_count(args.session_id)
    print(f"已导出 {count} 条数据：{path}")
    store.close()
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heartapp",
        description=f"{APP_NAME} —— 从智能手表（蓝牙标准心率服务）实时读取并记录心率",
        epilog="不带子命令直接运行会打开图形界面。",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--simulate", action="store_true", help="图形界面启动后直接用模拟数据")

    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="扫描附近蓝牙设备，标出哪些在广播心率")
    p_list.add_argument("--timeout", type=float, default=8.0, help="扫描秒数（默认 8）")
    p_list.add_argument("--heart-only", action="store_true", help="只显示广播心率的设备")
    p_list.set_defaults(func=cmd_list)

    p_probe = sub.add_parser("probe", help="连接一个设备并检查它有没有标准心率服务")
    p_probe.add_argument("device", help="设备名称或 MAC 地址")
    p_probe.add_argument("--timeout", type=float, default=12.0, help="查找超时秒数")
    p_probe.set_defaults(func=cmd_probe)

    p_rec = sub.add_parser("record", help="连接并记录心率（命令行版）")
    p_rec.add_argument("--device", help="手表名称或 MAC 地址")
    p_rec.add_argument("--simulate", action="store_true", help="用模拟数据源，不需要手表")
    p_rec.add_argument("--profile", default=DEFAULT_PROFILE, choices=list(PROFILES), help="模拟场景")
    p_rec.add_argument("--duration", type=float, default=30.0, help="记录秒数，0 表示直到 Ctrl+C")
    p_rec.add_argument("--connect-timeout", type=float, default=30.0, help="连接超时秒数")
    p_rec.add_argument("--note", default=None, help="给这次记录加备注")
    p_rec.add_argument("--csv", action="store_true", help="结束后导出 CSV")
    p_rec.add_argument("--out", default=None, help="CSV 输出路径")
    p_rec.add_argument("--db", default=None, help="数据库路径")
    p_rec.set_defaults(func=cmd_record)

    p_sessions = sub.add_parser("sessions", help="列出历史记录")
    p_sessions.add_argument("--limit", type=int, default=50)
    p_sessions.add_argument("--db", default=None)
    p_sessions.set_defaults(func=cmd_sessions)

    p_export = sub.add_parser("export", help="把某次记录导出为 CSV")
    p_export.add_argument("session_id", type=int)
    p_export.add_argument("--out", default=None, help="输出路径")
    p_export.add_argument("--db", default=None)
    p_export.set_defaults(func=cmd_export)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    _setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        # 图形界面优先用 Qt（真模糊/真透明/抗锯齿）；没装 PySide6 就退回 Tkinter。
        # 命令行版 exe 两个界面都不打进去，所以还要能优雅降级成帮助信息。
        gui_main = None
        try:
            from .ui.qt.app import main as gui_main  # type: ignore[assignment]
        except ImportError:
            try:
                from .ui.app import main as gui_main  # type: ignore[assignment]
            except ImportError:
                gui_main = None

        if gui_main is None:
            print("这是命令行版，不含图形界面。")
            print("  • 想看图形界面：双击「心率记录仪.exe」")
            print("  • 想用命令行：加上子命令，例如")
            print("      heartapp-cli list          # 扫描附近设备")
            print("      heartapp-cli record --simulate --duration 30")
            print()
            parser.print_help()
            return 0

        gui_argv: List[str] = []
        if args.simulate:
            gui_argv.append("--simulate")
        return gui_main(gui_argv)

    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
