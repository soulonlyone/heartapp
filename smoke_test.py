"""smoke_test.py —— 不依赖界面，验证「模拟源 → 引擎 → 数据库 → 统计 → CSV」全链路。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from heartapp.engine import Engine
from heartapp.store import HeartRateStore

DB = Path("_test_output/smoke.db")
CSV = Path("_test_output/smoke_export.csv")
DB.parent.mkdir(parents=True, exist_ok=True)
if DB.exists():
    DB.unlink()
for suffix in ("-wal", "-shm"):
    p = Path(str(DB) + suffix)
    if p.exists():
        p.unlink()

store = HeartRateStore(DB)
engine = Engine(store)
engine.start()

samples_seen = 0
statuses = []

try:
    print("[1] 启动模拟数据源 profile=interval ...")
    engine.connect_simulator("interval")
    ok = engine.wait_for_state(("connected",), timeout=10)
    print("    进入 connected 状态:", ok)

    print("[2] 开始记录 ...")
    session_id = engine.start_recording(note="smoke test", wait=True)
    print("    session_id =", session_id)

    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        for kind, payload in engine.poll_events():
            if kind == "sample":
                samples_seen += 1
                if samples_seen <= 3 or samples_seen % 20 == 0:
                    s = payload["sample"]
                    print(f"    #{samples_seen:3d}  {s.bpm:3d} bpm  contact={s.contact}  rr={s.rr_intervals_ms}")
            elif kind == "status":
                statuses.append((payload["state"], payload["detail"]))
            elif kind == "error":
                print("    ERROR:", payload["message"])
        time.sleep(0.2)

    print("[3] 停止记录 ...")
    stats = engine.stop_recording(wait=True)
    print("    统计:", f"样本={stats.sample_count} 有效={stats.valid_sample_count} "
          f"均值={(stats.avg_bpm or 0):.1f} 最低={stats.min_bpm} 最高={stats.max_bpm} "
          f"时长={stats.duration_text} RMSSD={stats.rmssd_ms}")
    print("    区间:", {k: round(v, 1) for k, v in stats.zone_seconds.items() if v > 0})

    db_count = store.sample_count(session_id)
    print("[4] 数据库实际落库条数:", db_count)

    path = store.export_csv(session_id, CSV)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    print("[5] CSV:", path, "行数:", len(lines))
    print("    表头:", lines[0])
    print("    首行:", lines[1] if len(lines) > 1 else "(空)")
    print("    末行:", lines[-1] if len(lines) > 1 else "(空)")

    sessions = store.list_sessions()
    print("[6] 会话列表:", [(s.id, s.source, s.sample_count, s.duration_s) for s in sessions])

    print("[7] 最近状态:", statuses[-3:])

    problems = []
    if samples_seen < 8:
        problems.append(f"收到的采样太少: {samples_seen}")
    # 接口事件是异步投递的，可能有 1-2 条在轮询循环结束后、停止记录前到达，
    # 因此只要求「落库不少于观察到的」，且不能多出一个不合理的量。
    if db_count < samples_seen:
        problems.append(f"落库条数 {db_count} 少于收到采样数 {samples_seen}（丢数据）")
    if db_count > samples_seen + 3:
        problems.append(f"落库条数 {db_count} 明显多于收到采样数 {samples_seen}")
    if not stats.avg_bpm or not (50 < stats.avg_bpm < 200):
        problems.append(f"平均心率异常: {stats.avg_bpm}")
    if len(lines) != db_count + 1:
        problems.append(f"CSV 行数 {len(lines)} 与落库 {db_count} 不匹配")

finally:
    engine.shutdown()
    store.close()

print()
if problems:
    print("SMOKE FAIL:", "; ".join(problems))
    sys.exit(1)
print("SMOKE PASS  — 全链路正常")
