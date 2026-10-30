"""记录缓存和异常会话恢复的回归测试。"""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from heartapp.engine import Engine
from heartapp.hr import HeartRateSample
from heartapp.store import HeartRateStore


class TestRecordingReliability(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = HeartRateStore(Path(self.tmp.name) / "test.db")
        self.engine = Engine(self.store)

    def tearDown(self):
        self.engine.shutdown()
        self.store.close()
        self.tmp.cleanup()

    def test_first_batch_flushes_after_five_seconds(self):
        with patch("heartapp.engine.time.monotonic", return_value=100):
            sid = self.engine._start_recording_impl(None)
        with patch("heartapp.engine.time.monotonic", return_value=106):
            self.engine._on_sample(HeartRateSample(ts=datetime.now(), bpm=70))
        self.assertEqual(self.store.sample_count(sid), 1)

    def test_failed_write_keeps_pending_samples(self):
        sid = self.engine._start_recording_impl(None)
        self.engine._on_sample(HeartRateSample(ts=datetime.now(), bpm=70))
        with patch.object(self.store, "add_samples", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.engine._stop_recording_impl()
        self.assertTrue(self.engine.is_recording)
        self.assertEqual(len(self.engine._pending), 1)
        self.engine._stop_recording_impl()
        self.assertEqual(self.store.sample_count(sid), 1)

    def test_stale_session_ends_at_last_sample(self):
        start = datetime(2026, 1, 1)
        sid = self.store.start_session(started_at=start)
        end = start + timedelta(seconds=10)
        self.store.add_sample(sid, ts=end, t_offset=10, bpm=70)
        self.store.close_stale_sessions()
        self.assertEqual(self.store.get_session(sid).ended_at, end)

    def test_empty_stale_session_has_zero_duration(self):
        sid = self.store.start_session(started_at=datetime(2026, 1, 1))
        self.store.close_stale_sessions()
        self.assertEqual(self.store.get_session(sid).duration_s, 0)

    def test_failed_batch_rolls_back_before_retry(self):
        sid = self.store.start_session()
        self.store._conn.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON samples "
            "WHEN NEW.bpm = 71 BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
        rows = [(datetime.now(), 0, 70, None, None),
                (datetime.now(), 1, 71, None, None)]
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_samples(sid, rows)
        self.assertEqual(self.store.sample_count(sid), 0)
        self.store._conn.execute("DROP TRIGGER fail_second")
        self.store.add_samples(sid, rows)
        self.assertEqual(self.store.sample_count(sid), 2)
