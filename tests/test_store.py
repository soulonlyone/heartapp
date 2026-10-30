"""SQLite 存储层测试。"""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from heartapp.store import HeartRateStore, isoformat  # noqa: E402


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.store = HeartRateStore(self.db_path)
        self.t0 = datetime(2026, 1, 2, 10, 0, 0)

    def tearDown(self) -> None:
        try:
            self.store.close()
        finally:
            self._tmp.cleanup()

    def _fill(self, session_id: int, count: int = 5, bpm_start: int = 70) -> None:
        rows = []
        for i in range(count):
            rows.append(
                (
                    self.t0 + timedelta(seconds=i),
                    float(i),
                    bpm_start + i * 5,
                    True,
                    800.0 + i,
                )
            )
        self.store.add_samples(session_id, rows)


class TestSessionLifecycle(StoreTestCase):
    def test_start_and_get(self):
        sid = self.store.start_session(
            started_at=self.t0, device_name="OPPO Watch X2", source="ble"
        )
        session = self.store.get_session(sid)
        self.assertIsNotNone(session)
        self.assertEqual(session.id, sid)
        self.assertEqual(session.device_name, "OPPO Watch X2")
        self.assertEqual(session.source, "ble")
        self.assertIsNone(session.ended_at)
        self.assertTrue(session.is_open)

    def test_end_session_sets_ended_at_and_count(self):
        sid = self.store.start_session(started_at=self.t0)
        self._fill(sid, 5)
        self.store.end_session(sid, ended_at=self.t0 + timedelta(seconds=4))
        session = self.store.get_session(sid)
        self.assertIsNotNone(session)
        self.assertFalse(session.is_open)
        self.assertEqual(session.sample_count, 5)
        self.assertAlmostEqual(session.duration_s, 4.0, places=6)

    def test_end_session_is_idempotent(self):
        sid = self.store.start_session(started_at=self.t0)
        self.store.end_session(sid, ended_at=self.t0 + timedelta(seconds=10))
        first = self.store.get_session(sid).ended_at
        self.store.end_session(sid, ended_at=self.t0 + timedelta(seconds=99))
        second = self.store.get_session(sid).ended_at
        self.assertEqual(first, second)  # 第二次不应覆盖

    def test_delete_session_cascades_to_samples(self):
        sid = self.store.start_session(started_at=self.t0)
        self._fill(sid, 4)
        self.assertEqual(self.store.sample_count(sid), 4)
        self.store.delete_session(sid)
        self.assertIsNone(self.store.get_session(sid))
        self.assertEqual(self.store.session_samples(sid), [])

    def test_close_stale_sessions(self):
        open_id = self.store.start_session(started_at=self.t0)
        closed_id = self.store.start_session(started_at=self.t0)
        self.store.end_session(closed_id)
        changed = self.store.close_stale_sessions()
        self.assertEqual(changed, 1)
        self.assertFalse(self.store.get_session(open_id).is_open)
        self.assertIn("未正常退出", self.store.get_session(open_id).note or "")

    def test_update_note(self):
        sid = self.store.start_session(started_at=self.t0)
        self.store.update_session_note(sid, "睡前静息")
        self.assertEqual(self.store.get_session(sid).note, "睡前静息")


class TestSamples(StoreTestCase):
    def test_add_sample_and_count(self):
        sid = self.store.start_session(started_at=self.t0)
        self.store.add_sample(sid, ts=self.t0, t_offset=0.0, bpm=72, contact=True, rr_ms=830.0)
        self.assertEqual(self.store.sample_count(sid), 1)
        row = self.store.session_samples(sid)[0]
        self.assertEqual(row.bpm, 72)
        self.assertTrue(row.contact)
        self.assertAlmostEqual(row.rr_ms, 830.0)

    def test_contact_none_round_trips(self):
        sid = self.store.start_session(started_at=self.t0)
        self.store.add_sample(sid, ts=self.t0, t_offset=0.0, bpm=72, contact=None)
        self.assertIsNone(self.store.session_samples(sid)[0].contact)

    def test_contact_false_round_trips(self):
        sid = self.store.start_session(started_at=self.t0)
        self.store.add_sample(sid, ts=self.t0, t_offset=0.0, bpm=72, contact=False)
        self.assertIs(self.store.session_samples(sid)[0].contact, False)

    def test_batch_insert_and_ordering(self):
        sid = self.store.start_session(started_at=self.t0)
        rows = [
            (self.t0 + timedelta(seconds=offset), float(offset), 70 + offset, None, None)
            for offset in (3, 1, 2, 0)
        ]
        written = self.store.add_samples(sid, rows)
        self.assertEqual(written, 4)
        offsets = [s.t_offset for s in self.store.session_samples(sid)]
        self.assertEqual(offsets, [0.0, 1.0, 2.0, 3.0])

    def test_add_samples_empty_is_noop(self):
        sid = self.store.start_session(started_at=self.t0)
        self.assertEqual(self.store.add_samples(sid, []), 0)
        self.assertEqual(self.store.sample_count(sid), 0)

    def test_samples_isolated_per_session(self):
        a = self.store.start_session(started_at=self.t0)
        b = self.store.start_session(started_at=self.t0)
        self._fill(a, 3)
        self._fill(b, 7)
        self.assertEqual(self.store.sample_count(a), 3)
        self.assertEqual(self.store.sample_count(b), 7)

    def test_session_samples_limit(self):
        sid = self.store.start_session(started_at=self.t0)
        self._fill(sid, 10)
        self.assertEqual(len(self.store.session_samples(sid, limit=4)), 4)

    def test_time_bounds(self):
        sid = self.store.start_session(started_at=self.t0)
        self._fill(sid, 5)
        first, last = self.store.time_bounds(sid)
        self.assertEqual(first, self.t0)
        self.assertEqual(last, self.t0 + timedelta(seconds=4))

    def test_time_bounds_empty(self):
        sid = self.store.start_session(started_at=self.t0)
        self.assertEqual(self.store.time_bounds(sid), (None, None))


class TestListingAndAggregates(StoreTestCase):
    def test_list_sessions_newest_first(self):
        a = self.store.start_session(started_at=self.t0)
        b = self.store.start_session(started_at=self.t0 + timedelta(hours=1))
        ids = [s.id for s in self.store.list_sessions()]
        self.assertEqual(ids[0], b)
        self.assertIn(a, ids)

    def test_list_sessions_limit(self):
        for i in range(5):
            self.store.start_session(started_at=self.t0 + timedelta(minutes=i))
        self.assertEqual(len(self.store.list_sessions(limit=3)), 3)

    def test_aggregate(self):
        a = self.store.start_session(started_at=self.t0)
        b = self.store.start_session(started_at=self.t0)
        # 会话 A：70, 80, 90
        self.store.add_samples(
            a,
            [
                (self.t0, 0.0, 70, None, None),
                (self.t0, 1.0, 80, None, None),
                (self.t0, 2.0, 90, None, None),
            ],
        )
        self.store.add_samples(b, [(self.t0, 0.0, 120, None, None)])
        agg = self.store.session_bpm_aggregate([a, b])
        avg, low, high, count = agg[a]
        self.assertAlmostEqual(avg, 80.0, places=6)
        self.assertEqual(low, 70)
        self.assertEqual(high, 90)
        self.assertEqual(count, 3)
        self.assertEqual(agg[b][3], 1)

    def test_aggregate_ignores_zero_bpm(self):
        a = self.store.start_session(started_at=self.t0)
        self.store.add_samples(
            a,
            [
                (self.t0, 0.0, 0, None, None),
                (self.t0, 1.0, 80, None, None),
            ],
        )
        avg, low, high, count = self.store.session_bpm_aggregate([a])[a]
        self.assertEqual(count, 1)
        self.assertEqual(low, 80)

    def test_aggregate_empty(self):
        self.assertEqual(self.store.session_bpm_aggregate([]), {})


class TestCsvExport(StoreTestCase):
    def test_export_content(self):
        sid = self.store.start_session(
            started_at=self.t0, device_name="OPPO Watch X2", source="ble"
        )
        self._fill(sid, 3)
        out = Path(self._tmp.name) / "out.csv"
        path = self.store.export_csv(sid, out)
        self.assertTrue(path.exists())

        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 4)  # 表头 + 3 行
        self.assertEqual(rows[0][0], "序号")
        self.assertEqual(rows[0][3], "心率bpm")
        self.assertEqual(rows[1][3], "70")
        self.assertEqual(rows[3][3], "80")

    def test_export_has_bom_for_excel(self):
        sid = self.store.start_session(started_at=self.t0)
        self._fill(sid, 1)
        out = Path(self._tmp.name) / "bom.csv"
        self.store.export_csv(sid, out)
        raw = out.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "缺少 BOM，Excel 打开会中文乱码")

    def test_export_empty_session(self):
        sid = self.store.start_session(started_at=self.t0)
        out = Path(self._tmp.name) / "empty.csv"
        self.store.export_csv(sid, out)
        lines = out.read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(len(lines), 1)  # 只有表头

    def test_export_creates_parent_dir(self):
        sid = self.store.start_session(started_at=self.t0)
        self._fill(sid, 1)
        out = Path(self._tmp.name) / "nested" / "deep" / "out.csv"
        self.store.export_csv(sid, out)
        self.assertTrue(out.exists())

    def test_export_contact_column(self):
        sid = self.store.start_session(started_at=self.t0)
        self.store.add_samples(
            sid,
            [
                (self.t0, 0.0, 70, True, None),
                (self.t0, 1.0, 71, False, None),
                (self.t0, 2.0, 72, None, None),
            ],
        )
        out = Path(self._tmp.name) / "contact.csv"
        self.store.export_csv(sid, out)
        rows = list(csv.reader(out.open("r", encoding="utf-8-sig", newline="")))
        self.assertEqual(rows[1][4], "是")
        self.assertEqual(rows[2][4], "否")
        self.assertEqual(rows[3][4], "")


class TestSettings(StoreTestCase):
    def test_default_when_missing(self):
        self.assertEqual(self.store.get_int_setting("age", 30), 30)
        self.assertIsNone(self.store.get_setting("nope"))

    def test_set_and_get(self):
        self.store.set_setting("age", "41")
        self.assertEqual(self.store.get_int_setting("age", 30), 41)

    def test_bad_value_falls_back(self):
        self.store.set_setting("age", "abc")
        self.assertEqual(self.store.get_int_setting("age", 30), 30)

    def test_float_string_accepted(self):
        self.store.set_setting("max_hr", "185.0")
        self.assertEqual(self.store.get_int_setting("max_hr", 0), 185)


class TestPersistence(StoreTestCase):
    def test_data_survives_reopen(self):
        sid = self.store.start_session(started_at=self.t0, device_name="测试手表")
        self._fill(sid, 6)
        self.store.end_session(sid)
        self.store.close()

        reopened = HeartRateStore(self.db_path)
        try:
            self.assertEqual(reopened.sample_count(sid), 6)
            self.assertEqual(reopened.get_session(sid).device_name, "测试手表")
        finally:
            reopened.close()
        # tearDown 还会再 close 一次 self.store —— 正好顺带验证 close() 幂等

    def test_close_is_idempotent(self):
        """退出路径上 close() 会被调用两次（窗口回调 + finally），不能因此抛异常。"""
        store = HeartRateStore(Path(self._tmp.name) / "twice.db")
        store.start_session(started_at=self.t0)
        store.close()
        store.close()  # 第二次必须是安全的空操作
        self.assertTrue(store.is_closed)

    def test_creates_parent_directory(self):
        nested = Path(self._tmp.name) / "a" / "b" / "hr.db"
        store = HeartRateStore(nested)
        try:
            self.assertTrue(nested.exists())
        finally:
            store.close()

    def test_context_manager(self):
        path = Path(self._tmp.name) / "ctx.db"
        with HeartRateStore(path) as store:
            store.start_session(started_at=self.t0)
            self.assertEqual(len(store.list_sessions()), 1)

    def test_isoformat_round_trip(self):
        text = isoformat(self.t0)
        self.assertTrue(text.startswith("2026-01-02T10:00:00"))
        self.assertEqual(datetime.fromisoformat(text), self.t0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
