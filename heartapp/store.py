"""SQLite 持久化：心率会话与采样点。

设计要点
--------
* 一条 *session* 代表一次记录（按「开始记录 / 停止记录」划分）。
* *samples* 以 ``t_offset``（相对会话开始的秒数）为主时间轴，``ts`` 保留墙钟时间。
* 采集在后台线程写入、界面在主线程读取，因此连接使用
  ``check_same_thread=False`` 并由一把 :class:`threading.RLock` 串行化所有操作。
"""

from __future__ import annotations

import csv
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT    NOT NULL,
    ended_at       TEXT,
    device_name    TEXT,
    device_address TEXT,
    source         TEXT    NOT NULL DEFAULT 'ble',
    note           TEXT,
    sample_count   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts         TEXT    NOT NULL,
    t_offset   REAL    NOT NULL,
    bpm        INTEGER NOT NULL,
    contact    INTEGER,
    rr_ms      REAL
);

CREATE TABLE IF NOT EXISTS pc_sessions (
    session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    activity TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    t_offset REAL NOT NULL,
    label TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON session_events(session_id, t_offset);

CREATE INDEX IF NOT EXISTS idx_samples_session ON samples(session_id, t_offset);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
"""

CSV_HEADER = ["序号", "时间", "相对秒", "心率bpm", "贴合", "RR间期ms"]


def isoformat(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


@dataclass(frozen=True)
class SessionRow:
    id: int
    started_at: datetime
    ended_at: Optional[datetime]
    device_name: Optional[str]
    device_address: Optional[str]
    source: str
    note: Optional[str]
    sample_count: int

    @property
    def duration_s(self) -> float:
        if self.ended_at is None:
            return 0.0
        return max(0.0, (self.ended_at - self.started_at).total_seconds())

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


@dataclass(frozen=True)
class SampleRow:
    ts: datetime
    t_offset: float
    bpm: int
    contact: Optional[bool]
    rr_ms: Optional[float]


class HeartRateStore:
    """心率数据的本地存储。线程安全。"""

    def __init__(self, db_path: Union[str, Path] = "heartrate.db") -> None:
        self.db_path = Path(db_path)
        if self.db_path.parent and str(self.db_path.parent) not in ("", "."):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    # -- 生命周期 ---------------------------------------------------------

    @property
    def is_closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """关闭连接。**可重复调用**。

        退出路径上很容易被关两次（窗口关闭回调 + ``finally``），所以这里必须幂等，
        否则用户一关窗口就吃一个异常。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._conn.commit()
            except sqlite3.Error:
                pass
            finally:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass

    def __enter__(self) -> "HeartRateStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- 会话 -------------------------------------------------------------

    def start_session(
        self,
        *,
        started_at: Optional[datetime] = None,
        device_name: Optional[str] = None,
        device_address: Optional[str] = None,
        source: str = "ble",
        note: Optional[str] = None,
    ) -> int:
        started_at = started_at or datetime.now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sessions(started_at, device_name, device_address, source, note)"
                " VALUES(?, ?, ?, ?, ?)",
                (
                    isoformat(started_at),
                    device_name,
                    device_address,
                    source,
                    note,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def end_session(self, session_id: int, *, ended_at: Optional[datetime] = None) -> None:
        ended_at = ended_at or datetime.now()
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (isoformat(ended_at), session_id),
            )
            self._conn.execute(
                "UPDATE sessions SET sample_count ="
                " (SELECT COUNT(*) FROM samples WHERE session_id = sessions.id)"
                " WHERE id = ?",
                (session_id,),
            )
            self._conn.commit()

    def close_stale_sessions(self, *, note: str = "程序上次未正常退出") -> int:
        """把上次崩溃/强杀留下的未结束会话收尾，避免出现永远「进行中」的记录。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET ended_at = COALESCE("
                " (SELECT MAX(ts) FROM samples WHERE session_id = sessions.id), started_at),"
                " note = COALESCE(note || ' | ', '') || ?"
                " WHERE ended_at IS NULL",
                (note,),
            )
            self._conn.commit()
            return cur.rowcount

    def get_session(self, session_id: int) -> Optional[SessionRow]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return _row_to_session(row) if row else None

    def list_sessions(self, *, limit: int = 200) -> List[SessionRow]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_session(r) for r in rows]

    def delete_session(self, session_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM samples WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()

    def update_session_note(self, session_id: int, note: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET note = ? WHERE id = ?", (note, session_id)
            )
            self._conn.commit()

    # -- 采样 -------------------------------------------------------------

    def add_sample(
        self,
        session_id: int,
        *,
        ts: datetime,
        t_offset: float,
        bpm: int,
        contact: Optional[bool] = None,
        rr_ms: Optional[float] = None,
    ) -> None:
        self.add_samples(
            session_id,
            [(ts, t_offset, bpm, contact, rr_ms)],
        )

    def add_samples(
        self,
        session_id: int,
        rows: Iterable[Tuple[datetime, float, int, Optional[bool], Optional[float]]],
    ) -> int:
        """批量写入。返回写入条数。"""
        payload = [
            (
                session_id,
                isoformat(ts),
                float(t_offset),
                int(bpm),
                None if contact is None else (1 if contact else 0),
                None if rr_ms is None else float(rr_ms),
            )
            for (ts, t_offset, bpm, contact, rr_ms) in rows
        ]
        if not payload:
            return 0
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO samples(session_id, ts, t_offset, bpm, contact, rr_ms)"
                " VALUES(?, ?, ?, ?, ?, ?)",
                payload,
            )
            self._conn.execute(
                "UPDATE sessions SET sample_count ="
                " (SELECT COUNT(*) FROM samples WHERE session_id = sessions.id)"
                " WHERE id = ?",
                (session_id,),
            )
        return len(payload)

    def session_samples(
        self, session_id: int, *, limit: Optional[int] = None
    ) -> List[SampleRow]:
        sql = "SELECT ts, t_offset, bpm, contact, rr_ms FROM samples WHERE session_id = ?"
        params: List[object] = [session_id]
        sql += " ORDER BY t_offset ASC, id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_sample(r) for r in rows]

    def session_bpm_aggregate(
        self, session_ids: Optional[Sequence[int]] = None
    ) -> Dict[int, Tuple[Optional[float], Optional[int], Optional[int], int]]:
        """批量取每个会话的 (平均, 最低, 最高, 条数)。

        历史列表要显示这些数字；逐会话去 ``session_samples`` 会把整张表读进内存，
        长会话一多界面就卡住了，所以这里用一条 SQL 聚合。
        """
        sql = (
            "SELECT session_id, AVG(bpm) AS avg_bpm, MIN(bpm) AS min_bpm,"
            " MAX(bpm) AS max_bpm, COUNT(*) AS n FROM samples WHERE bpm > 0"
        )
        params: List[object] = []
        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            sql += f" AND session_id IN ({placeholders})"
            params.extend(int(s) for s in session_ids)
        sql += " GROUP BY session_id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        result: Dict[int, Tuple[Optional[float], Optional[int], Optional[int], int]] = {}
        for row in rows:
            result[int(row["session_id"])] = (
                float(row["avg_bpm"]) if row["avg_bpm"] is not None else None,
                int(row["min_bpm"]) if row["min_bpm"] is not None else None,
                int(row["max_bpm"]) if row["max_bpm"] is not None else None,
                int(row["n"]),
            )
        return result

    def sample_count(self, session_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM samples WHERE session_id = ?", (session_id,)
            ).fetchone()
        return int(row["n"])

    def time_bounds(self, session_id: int) -> Tuple[Optional[datetime], Optional[datetime]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(ts) AS a, MAX(ts) AS b FROM samples WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row or row["a"] is None:
            return None, None
        return parse_iso(row["a"]), parse_iso(row["b"])

    # -- 导出 -------------------------------------------------------------

    def export_csv(self, session_id: int, path: Union[str, Path]) -> Path:
        """把一次会话导出为 CSV。

        ``utf-8-sig`` 是为了 Excel 直接双击打开不乱码。
        """
        target = Path(path)
        if target.parent and str(target.parent) not in ("", "."):
            target.parent.mkdir(parents=True, exist_ok=True)
        samples = self.session_samples(session_id)
        with target.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(CSV_HEADER)
            for idx, s in enumerate(samples, start=1):
                writer.writerow(
                    [
                        idx,
                        isoformat(s.ts),
                        f"{s.t_offset:.3f}",
                        s.bpm,
                        "" if s.contact is None else ("是" if s.contact else "否"),
                        "" if s.rr_ms is None else f"{s.rr_ms:.1f}",
                    ]
                )
        return target

    def set_pc_context(self, session_id: int, title: str, activity: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO pc_sessions VALUES (?, ?, ?)",
                               (session_id, title[:120], activity))

    def pc_context(self, session_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT title, activity FROM pc_sessions WHERE session_id=?",
                                     (session_id,)).fetchone()
        return dict(row) if row else None

    def add_event(self, session_id: int, t_offset: float, label: str) -> None:
        label = label.strip()[:120]
        if not label or t_offset < 0:
            raise ValueError("事件需要名称和非负时间")
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO session_events(session_id,t_offset,label) VALUES(?,?,?)",
                               (session_id, t_offset, label))

    def session_events(self, session_id: int) -> List[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT t_offset,label FROM session_events WHERE session_id=? ORDER BY t_offset,id",
                                      (session_id,)).fetchall()
        return [dict(row) for row in rows]

    def export_events_csv(self, session_id: int, path: Union[str, Path]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["相对秒", "事件"])
            for event in self.session_events(session_id):
                label = event["label"]
                if label.startswith(("=", "+", "-", "@")):
                    label = "'" + label
                writer.writerow([f"{event['t_offset']:.3f}", label])
        return target

    # -- 设置 -------------------------------------------------------------

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value))
            )
            self._conn.commit()

    def get_int_setting(self, key: str, default: int) -> int:
        raw = self.get_setting(key)
        if raw is None:
            return default
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return default


def _row_to_session(row: sqlite3.Row) -> SessionRow:
    return SessionRow(
        id=int(row["id"]),
        started_at=parse_iso(row["started_at"]),
        ended_at=parse_iso(row["ended_at"]) if row["ended_at"] else None,
        device_name=row["device_name"],
        device_address=row["device_address"],
        source=row["source"],
        note=row["note"],
        sample_count=int(row["sample_count"] or 0),
    )


def _row_to_sample(row: sqlite3.Row) -> SampleRow:
    contact_raw = row["contact"]
    return SampleRow(
        ts=parse_iso(row["ts"]),
        t_offset=float(row["t_offset"]),
        bpm=int(row["bpm"]),
        contact=None if contact_raw is None else bool(contact_raw),
        rr_ms=None if row["rr_ms"] is None else float(row["rr_ms"]),
    )
