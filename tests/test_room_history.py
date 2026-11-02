"""多人曲线的时序去重、共享缺口、缓存边界和同刻比较。"""
import unittest

from PySide6.QtWidgets import QApplication
from heartapp.room_history import RoomHistory, segments
from heartapp.ui.qt.comparison import ComparisonCurve, COLORS


class TestRoomHistory(unittest.TestCase):
    def setUp(self):
        self.history = RoomHistory()
        self.history.clear(100)

    def test_repeated_snapshot_does_not_create_samples(self):
        member = dict(id="peer", bpm=80, sharing=True, sample_seq=1, age_ms=200)
        self.history.snapshot([member], "me", 101)
        self.history.snapshot([dict(member, age_ms=1200)], "me", 102)
        self.assertEqual(list(self.history.points["peer"]), [(100.8, 80)])

    def test_stop_sharing_is_an_explicit_gap_even_within_five_seconds(self):
        for stamp, bpm in ((100, 80), (101, None), (102, 90)):
            self.history.snapshot([dict(id="peer", bpm=bpm, sharing=bpm is not None,
                                        sample_seq=stamp, age_ms=0)], "me", stamp)
        self.assertEqual(segments(list(self.history.points["peer"])), [[(100, 80)], [(102, 90)]])

    def test_expiry_inserts_gap_without_new_sequence(self):
        self.history.snapshot([dict(id="peer", bpm=80, sharing=True, sample_seq=1)], "me", 100)
        self.history.snapshot([dict(id="peer", bpm=None, sharing=True, sample_seq=1)], "me", 106)
        self.assertEqual(list(self.history.points["peer"])[-1], (106, None))

    def test_missing_peer_is_removed_while_local_data_remains(self):
        self.history.append("self", 100, 72)
        self.history.snapshot([dict(id="peer", bpm=80, sharing=True)], "me", 100)
        self.history.snapshot([], "me", 101)
        self.assertEqual(set(self.history.points), {"self"})

    def test_thirty_minute_retention_and_clear(self):
        for t in range(2000):
            self.history.append("peer", t, 80)
        self.assertGreaterEqual(self.history.points["peer"][0][0], 199)
        self.assertLessEqual(len(self.history.points["peer"]), 1801)
        self.history.clear()
        self.assertEqual(self.history.points, {})

    def test_real_time_gaps_are_not_joined(self):
        self.assertEqual(segments([(0, 70), (1, 71), (10, 80)]), [[(0, 70), (1, 71)], [(10, 80)]])


class TestComparisonCurve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.chart = ComparisonCurve()
        self.chart.resize(900, 360)
        self.chart.set_series({
            "self": dict(name="我", color=COLORS[0], points=[(-10, 70), (-9, 90), (-8, None), (-1, 80)]),
            "peer": dict(name="队友", color=COLORS[1], points=[(-10, 100), (-9, 110), (-1, 120)]),
        }, 60)

    def tearDown(self):
        self.chart.close()

    def test_same_cursor_reads_both_series_without_interpolating_gaps(self):
        self.assertEqual([b for _, b in self.chart.values_at(-9)], [90, 110])
        self.assertEqual(self.chart.values_at(-5), [])
        self.assertEqual([item["name"] for item, _ in self.chart.values_at(-8)], ["队友"])

    def test_summary_uses_visible_time_range_and_series(self):
        self.chart.view = (-10, -9)
        summary = self.chart.summary()
        self.assertIn("我  80 / 90", summary)
        self.assertIn("队友  105 / 110", summary)
        self.chart.set_series({"self": self.chart.series["self"]}, 60)
        self.assertNotIn("队友", self.chart.summary())
