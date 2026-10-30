"""D 版曲线的长记录、缺失区间和缩放边界回归测试。"""
from __future__ import annotations
import unittest
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtWidgets import QApplication
from heartapp.ui.qt.editorial import Curve


class Wheel:
    def position(self):
        return QPointF(300, 120)
    def angleDelta(self):
        return QPoint(0, 120)
    def accept(self):
        pass


class TestEditorialCurve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.chart = Curve()
        self.chart.resize(900, 360)

    def tearDown(self):
        self.chart.close()

    def test_four_hour_continuous_record_remains_one_segment(self):
        points = [(float(t), 112.0 if t == 7111 else 75.0) for t in range(14400)]
        self.chart.set_points(points)
        segments = self.chart.segments()
        self.assertEqual(len(segments), 1)
        self.assertIn((7111.0, 112.0), segments[0])
        self.assertEqual(len(segments[0]), len(points))

    def test_missing_data_is_a_true_gap(self):
        self.chart.set_points([(0., 70.), (1., 72.), (20., 80.), (21., 78.)])
        self.assertEqual([len(s) for s in self.chart.segments()], [2, 2])

    def test_zoom_pan_and_reset_stay_inside_record(self):
        self.chart.set_points([(float(t), 75.) for t in range(3601)])
        self.chart.wheelEvent(Wheel())
        left, right = self.chart._range()
        self.assertGreater(left, 0)
        self.assertLess(right - left, 3600)
        self.assertEqual(self.chart._clamp(-50, 50), (0, 100))
        self.assertEqual(self.chart._clamp(3550, 3650), (3500, 3600))
        self.chart.reset_view()
        self.assertEqual(self.chart._range(), (0, 3600))
