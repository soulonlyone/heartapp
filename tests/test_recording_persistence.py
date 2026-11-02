"""定时保存、退出失败重试、重启回看及异常进程退出后的数据恢复。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from heartapp.engine import Engine
from heartapp.hr import HeartRateSample
from heartapp.store import HeartRateStore
from heartapp.ui.qt.app import HeartAppWindow, _Dialogs


def wait_until(predicate, app=None, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if app:
            app.processEvents()
        if predicate():
            return True
        time.sleep(.01)
    return bool(predicate())


class TestRecordingPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "recording.db"
        self.store = HeartRateStore(self.path)
        self.engine = Engine(self.store).start()

    def tearDown(self):
        self.engine.shutdown()
        self.store.close()
        self.tmp.cleanup()

    def sample(self):
        self.engine.submit(self.engine._invoke(
            self.engine._on_sample, HeartRateSample(ts=datetime.now(), bpm=73)
        )).result(timeout=2)

    def test_timer_saves_after_stream_stalls(self):
        with patch("heartapp.engine.FLUSH_MAX_SECONDS", .1):
            sid = self.engine.start_recording(wait=True)
            self.sample()
            self.assertTrue(wait_until(lambda: self.store.sample_count(sid) == 1))
            self.assertTrue(self.engine.is_recording)

    def test_timer_retries_failed_write_without_new_samples(self):
        with patch("heartapp.engine.FLUSH_MAX_SECONDS", .1), patch("heartapp.engine.FLUSH_RETRY_SECONDS", .1):
            sid = self.engine.start_recording(wait=True)
            with patch.object(self.store, "add_samples", side_effect=OSError("disk full")) as failed:
                self.sample()
                self.assertTrue(wait_until(lambda: failed.call_count > 0))
                self.assertTrue(self.engine.is_started)
                self.assertEqual(len(self.engine._pending), 1)
                self.assertEqual(self.store.sample_count(sid), 0)
            self.assertTrue(wait_until(lambda: self.store.sample_count(sid) == 1))

    def test_shutdown_failure_preserves_engine_then_retry_persists_after_reopen(self):
        sid = self.engine.start_recording(wait=True)
        self.sample()
        with patch.object(self.store, "add_samples", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.engine.shutdown()
        self.assertTrue(self.engine.is_started)
        self.assertTrue(self.engine.is_recording)
        self.assertEqual(len(self.engine._pending), 1)
        self.engine.shutdown()
        self.store.close()
        with HeartRateStore(self.path) as reopened:
            self.assertEqual(reopened.sample_count(sid), 1)
            self.assertFalse(reopened.get_session(sid).is_open)
            self.assertEqual(reopened.session_samples(sid)[0].bpm, 73)

    def test_end_session_failure_retries_without_duplicate_samples(self):
        sid = self.engine.start_recording(wait=True)
        self.sample()
        with patch.object(self.store, "end_session", side_effect=OSError("database locked")):
            with self.assertRaises(OSError):
                self.engine.shutdown()
        self.assertTrue(self.engine.is_started)
        self.assertTrue(self.engine.is_recording)
        self.assertEqual(self.store.sample_count(sid), 1)
        self.engine.shutdown()
        self.assertEqual(self.store.sample_count(sid), 1)

    def test_abrupt_process_exit_preserves_committed_record(self):
        path = Path(self.tmp.name) / "interrupted.db"
        code = """
import os, sys, time
from datetime import datetime
from heartapp import engine as module
from heartapp.store import HeartRateStore
from heartapp.hr import HeartRateSample
module.FLUSH_MAX_SECONDS = .1
store = HeartRateStore(sys.argv[1])
engine = module.Engine(store).start()
sid = engine.start_recording(wait=True)
engine.submit(engine._invoke(engine._on_sample, HeartRateSample(ts=datetime.now(), bpm=81))).result()
deadline = time.monotonic() + 3
while store.sample_count(sid) != 1 and time.monotonic() < deadline:
    time.sleep(.01)
os._exit(0 if store.sample_count(sid) == 1 else 2)
"""
        result = subprocess.run([sys.executable, "-c", code, str(path)],
                                cwd=Path(__file__).resolve().parents[1], timeout=10, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        with HeartRateStore(path) as reopened:
            self.assertEqual(reopened.close_stale_sessions(), 1)
            sid = reopened.list_sessions()[0].id
            sample = reopened.session_samples(sid)[0]
            self.assertEqual(sample.bpm, 81)
            self.assertEqual(reopened.get_session(sid).ended_at, sample.ts)


class TestWindowPersistence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_close_failure_keeps_window_then_restart_restores_history(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(_Dialogs, "quiet", True):
            path = Path(folder) / "gui.db"
            store = HeartRateStore(path)
            engine = Engine(store).start()
            window = HeartAppWindow(store, engine)
            window.show()
            try:
                sid = engine.start_recording(wait=True)
                engine.submit(engine._invoke(
                    engine._on_sample, HeartRateSample(ts=datetime.now(), bpm=79)
                )).result(timeout=2)
                self.assertTrue(wait_until(lambda: window._session_id == sid, self.app))
                with patch.object(store, "add_samples", side_effect=OSError("disk full")), \
                     patch.object(_Dialogs, "error") as error:
                    window.close()
                    self.assertTrue(wait_until(lambda: not window._closing, self.app))
                    self.assertTrue(window.isVisible())
                    self.assertTrue(window.isEnabled())
                    self.assertTrue(engine.is_recording)
                    self.assertEqual(len(engine._pending), 1)
                    error.assert_called_once()
                window.close()
                self.assertTrue(wait_until(lambda: window._closed, self.app))
                self.assertFalse(window.isVisible())
                self.assertFalse(window._overlay.isVisible())
            finally:
                engine.shutdown()
                window.close()
                store.close()
            reopened = HeartRateStore(path)
            new_engine = Engine(reopened)
            new_window = HeartAppWindow(reopened, new_engine)
            try:
                self.assertEqual(new_window.tree.rowCount(), 1)
                new_window.tree.selectRow(0)
                self.app.processEvents()
                self.assertEqual(len(new_window.history_chart.points), 1)
                self.assertEqual(new_window.history_chart.points[0][1], 79)
            finally:
                new_window.close()
                reopened.close()
