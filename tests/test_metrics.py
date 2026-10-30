"""统计与心率区间测试。"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from heartapp import metrics  # noqa: E402
from heartapp.metrics import (  # noqa: E402
    REST_LABEL,
    compute_stats,
    downsample,
    estimate_max_hr,
    format_duration,
    rmssd,
    rolling_average,
    time_in_zones,
    zone_for_bpm,
)


@dataclass
class FakeSample:
    """模拟 store.SampleRow。"""

    t_offset: float
    bpm: int
    rr_ms: Optional[float] = None


class TestEstimateMaxHr(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(estimate_max_hr(30), 190)
        self.assertEqual(estimate_max_hr(20), 200)

    def test_clamped(self):
        self.assertEqual(estimate_max_hr(5), 210)  # 夹到 10 岁
        self.assertEqual(estimate_max_hr(200), 120)  # 夹到 100 岁


class TestZones(unittest.TestCase):
    def test_boundaries(self):
        max_hr = 200
        # 每个区间的下边界应归入该区间
        self.assertEqual(zone_for_bpm(99, max_hr), REST_LABEL)
        self.assertEqual(zone_for_bpm(100, max_hr), "Z1 恢复")
        self.assertEqual(zone_for_bpm(119, max_hr), "Z1 恢复")
        self.assertEqual(zone_for_bpm(120, max_hr), "Z2 燃脂")
        self.assertEqual(zone_for_bpm(140, max_hr), "Z3 有氧")
        self.assertEqual(zone_for_bpm(160, max_hr), "Z4 无氧")
        self.assertEqual(zone_for_bpm(180, max_hr), "Z5 极限")
        self.assertEqual(zone_for_bpm(230, max_hr), "Z5 极限")  # 超出上限仍归 Z5

    def test_zero_max_hr_does_not_crash(self):
        self.assertEqual(zone_for_bpm(100, 0), REST_LABEL)

    def test_zone_names(self):
        self.assertEqual(len(metrics.zone_names()), 6)


class TestTimeInZones(unittest.TestCase):
    def test_weighted_by_interval(self):
        # 100 bpm 持续 10 秒（Z1，max=200），160 bpm 持续 10 秒（Z4）
        samples = [(0.0, 100), (10.0, 160), (20.0, 160)]
        zones = time_in_zones(samples, max_hr=200)
        self.assertAlmostEqual(zones["Z1 恢复"], 10.0, places=3)
        self.assertAlmostEqual(zones["Z4 无氧"], 10.0, places=3)

    def test_long_gap_is_capped(self):
        # 中间断了 10 分钟，不能把这段时间全算进某个区间
        samples = [(0.0, 100), (600.0, 100)]
        zones = time_in_zones(samples, max_hr=200)
        self.assertLessEqual(zones["Z1 恢复"], metrics.MAX_GAP_S + 1.5)

    def test_single_sample_has_no_zone_time(self):
        # 只有一个采样点，没有「到下一个点」的区间，所以区间时长为 0（诚实，不编造）
        zones = time_in_zones([(0.0, 170)], max_hr=200)
        self.assertEqual(sum(zones.values()), 0.0)

    def test_zone_sum_equals_duration(self):
        # 干净的不变量：无断连时，各区间时长之和 == 采样总时长
        samples = [(0.0, 100), (1.0, 130), (2.0, 170), (3.0, 90)]
        zones = time_in_zones(samples, max_hr=200)
        self.assertAlmostEqual(sum(zones.values()), 3.0, places=6)

    def test_empty(self):
        zones = time_in_zones([], max_hr=200)
        self.assertEqual(sum(zones.values()), 0.0)

    def test_all_zones_present_as_keys(self):
        zones = time_in_zones([(0.0, 100), (5.0, 100)], max_hr=200)
        for name in metrics.zone_names():
            self.assertIn(name, zones)


class TestRmssd(unittest.TestCase):
    def test_known_value(self):
        # 差值恒为 10 → RMSSD = 10
        rr = [800, 810, 800, 810, 800, 810]
        self.assertAlmostEqual(rmssd(rr), 10.0, places=6)

    def test_too_few_samples_returns_none(self):
        self.assertIsNone(rmssd([800, 810]))

    def test_filters_implausible_values(self):
        # 10 ms / 10000 ms 这种明显是坏值，应被丢掉
        rr = [800, 810, 10, 10000, 800, 810, 800, 810]
        value = rmssd(rr)
        self.assertIsNotNone(value)
        self.assertLess(value, 50.0)

    def test_constant_rr_gives_zero(self):
        self.assertAlmostEqual(rmssd([800] * 6), 0.0, places=6)


class TestComputeStats(unittest.TestCase):
    def test_basic_stats(self):
        samples = [
            FakeSample(0.0, 70, rr_ms=800),
            FakeSample(1.0, 80, rr_ms=810),
            FakeSample(2.0, 90, rr_ms=800),
            FakeSample(3.0, 100, rr_ms=810),
            FakeSample(4.0, 110, rr_ms=800),
            FakeSample(5.0, 120, rr_ms=810),
        ]
        stats = compute_stats(samples, max_hr=200)
        self.assertEqual(stats.sample_count, 6)
        self.assertEqual(stats.min_bpm, 70)
        self.assertEqual(stats.max_bpm, 120)
        self.assertAlmostEqual(stats.avg_bpm, 95.0, places=6)
        self.assertAlmostEqual(stats.duration_s, 5.0, places=6)
        self.assertEqual(stats.first_bpm, 70)
        self.assertEqual(stats.last_bpm, 120)
        self.assertIsNotNone(stats.rmssd_ms)

    def test_zero_bpm_counted_as_dropout(self):
        samples = [FakeSample(0.0, 0), FakeSample(1.0, 80), FakeSample(2.0, 90)]
        stats = compute_stats(samples, max_hr=200)
        self.assertEqual(stats.valid_sample_count, 2)
        self.assertEqual(stats.dropped_sample_count, 1)
        self.assertEqual(stats.sample_count, 3)
        # 统计只基于有效读数
        self.assertEqual(stats.min_bpm, 80)
        self.assertAlmostEqual(stats.avg_bpm, 85.0, places=6)

    def test_empty_samples(self):
        stats = compute_stats([], max_hr=190)
        self.assertFalse(stats.has_data)
        self.assertIsNone(stats.avg_bpm)
        self.assertEqual(stats.zone_seconds["静息"], 0.0)

    def test_unsorted_input_is_sorted(self):
        samples = [FakeSample(2.0, 90), FakeSample(0.0, 70), FakeSample(1.0, 80)]
        stats = compute_stats(samples, max_hr=200)
        self.assertEqual(stats.first_bpm, 70)
        self.assertEqual(stats.last_bpm, 90)
        self.assertAlmostEqual(stats.duration_s, 2.0, places=6)

    def test_zone_share_sums_to_one(self):
        samples = [FakeSample(float(i), 100 + i * 10) for i in range(10)]
        stats = compute_stats(samples, max_hr=200)
        share = stats.zone_share()
        self.assertAlmostEqual(sum(share.values()), 1.0, places=6)

    def test_rr_intervals_tuple_supported(self):
        from heartapp.hr import HeartRateSample
        from datetime import datetime

        samples = [
            HeartRateSample(datetime.now(), 80, t_offset=float(i),
                            rr_intervals_ms=(800.0, 810.0))
            for i in range(6)
        ]
        stats = compute_stats(samples, max_hr=200)
        self.assertEqual(stats.rr_count, 6)
        self.assertIsNotNone(stats.rmssd_ms)

    def test_resting_uses_min_of_rolling_window_for_long_sessions(self):
        # 前 2 分钟 100 bpm，之后 60 秒 60 bpm
        samples = [FakeSample(float(i), 100) for i in range(120)]
        samples += [FakeSample(float(120 + i), 60) for i in range(60)]
        stats = compute_stats(samples, max_hr=200)
        self.assertIsNotNone(stats.resting_bpm)
        self.assertLessEqual(stats.resting_bpm, 70.0)

    def test_short_session_resting_is_min(self):
        samples = [FakeSample(0.0, 90), FakeSample(1.0, 70), FakeSample(2.0, 110)]
        stats = compute_stats(samples, max_hr=200)
        self.assertAlmostEqual(stats.resting_bpm, 70.0, places=6)


class TestHelpers(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(format_duration(0), "00:00")
        self.assertEqual(format_duration(59), "00:59")
        self.assertEqual(format_duration(60), "01:00")
        self.assertEqual(format_duration(3661), "1:01:01")
        self.assertEqual(format_duration(-5), "00:00")

    def test_rolling_average(self):
        self.assertEqual(rolling_average([10, 20, 30], 1), [10.0, 20.0, 30.0])
        self.assertEqual(rolling_average([10, 20, 30], 2), [10.0, 15.0, 25.0])
        self.assertEqual(rolling_average([], 3), [])

    def test_downsample_keeps_bounds(self):
        samples = [FakeSample(float(i), 70) for i in range(1000)]
        picked = downsample(samples, 100)
        self.assertLessEqual(len(picked), 102)
        self.assertIs(picked[0], samples[0])
        self.assertIs(picked[-1], samples[-1])

    def test_downsample_noop_when_small(self):
        samples = [FakeSample(float(i), 70) for i in range(10)]
        self.assertEqual(len(downsample(samples, 100)), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
