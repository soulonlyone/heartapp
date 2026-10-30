"""PC 会话基线、迁移和事件时间轴测试。"""
from __future__ import annotations
import csv
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from heartapp.engine import Engine
from heartapp.hr import HeartRateSample
from heartapp.pc import session_baseline
from heartapp.store import HeartRateStore


class TestBaseline(unittest.TestCase):
    def samples(self):
        return [HeartRateSample(datetime.now(), 70, t_offset=float(t)) for t in range(31)]

    def test_reference_is_median_and_ignores_later_peaks(self):
        points = self.samples()
        points[8].bpm = 150
        points.append(HeartRateSample(datetime.now(), 160, t_offset=60))
        self.assertEqual(session_baseline(points), 70)

    def test_insufficient_or_gapped_data_has_no_reference(self):
        self.assertIsNone(session_baseline(self.samples()[:10]))
        self.assertIsNone(session_baseline([s for s in self.samples() if not 10 < s.t_offset < 20]))

    def test_bad_contact_does_not_establish_reference(self):
        points = self.samples()
        for point in points:
            point.contact = False
        self.assertIsNone(session_baseline(points))


class TestPCSession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"
        self.store = HeartRateStore(self.path)
        self.engine = Engine(self.store)

    def tearDown(self):
        self.engine.shutdown()
        self.store.close()
        self.tmp.cleanup()

    def test_events_and_samples_use_same_monotonic_clock(self):
        with patch("heartapp.engine.time.monotonic", return_value=100):
            sid = self.engine._start_recording_impl(None)
        self.store.set_pc_context(sid, "排位", "游戏")
        with patch("heartapp.engine.time.monotonic", return_value=110):
            self.engine._on_sample(HeartRateSample(datetime(2020, 1, 1), 80))
        self.engine.mark_event("团战")
        self.engine.submit(self.engine._invoke(lambda: None)).result(5)
        events = self.store.session_events(sid)
        self.assertEqual(events[0]["label"], "团战")
        self.assertGreaterEqual(events[0]["t_offset"], 10)
        self.engine._stop_recording_impl()
        self.assertEqual(self.store.session_samples(sid)[0].t_offset, 10)

    def test_migration_preserves_old_session_and_cascades_events(self):
        sid = self.store.start_session()
        self.store.set_pc_context(sid, "工作", "工作")
        self.store.add_event(sid, 2, "开始")
        self.store.close()
        conn = sqlite3.connect(self.path)
        conn.execute("DROP TABLE session_events")
        conn.execute("DROP TABLE pc_sessions")
        conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
        conn.commit()
        conn.close()
        self.store = HeartRateStore(self.path)
        self.engine.store = self.store
        self.assertIsNotNone(self.store.get_session(sid))
        self.assertIsNone(self.store.pc_context(sid))
        self.store.set_pc_context(sid, "新会话", "游戏")
        self.store.add_event(sid, 3, "标记")
        self.store.delete_session(sid)
        self.assertEqual(self.store.session_events(sid), [])
        self.assertIsNone(self.store.pc_context(sid))

    def test_event_export_preserves_unicode_and_escapes_formulas(self):
        sid = self.store.start_session()
        self.store.add_event(sid, 1.25, "=1+1")
        target = self.store.export_events_csv(sid, Path(self.tmp.name) / "events.csv")
        with target.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[1], ["1.250", "'=1+1"])
