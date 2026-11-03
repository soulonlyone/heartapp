"""采集引擎与模拟数据源测试（含线程/生命周期行为）。

这些测试覆盖的是最容易被忽视、出问题也最难看出来的部分：会话会不会丢数据、
退出时会不会留下一条永远「进行中」的记录、跨线程调用会不会静默失效。
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from heartapp.engine import Engine  # noqa: E402
from heartapp.simulator import PROFILES, SimulatedHeartRateSource  # noqa: E402
from heartapp.store import HeartRateStore  # noqa: E402


def wait_until(predicate: Callable[[], bool], timeout: float = 8.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class EngineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "engine.db"
        self.store = HeartRateStore(self.db_path)
        self.engine = Engine(self.store)
        self.engine.start()

    def tearDown(self) -> None:
        try:
            self.engine.shutdown()
        finally:
            try:
                self.store.close()
            finally:
                self._tmp.cleanup()

    def _collect(self) -> List[Tuple[str, dict]]:
        return self.engine.poll_events(max_items=100000)


class TestSimulatorSource(EngineTestCase):
    def test_simulator_produces_samples(self):
        self.engine.connect_simulator("interval")
        self.assertTrue(self.engine.wait_for_state(("connected",), timeout=5.0))
        self.assertTrue(
            wait_until(lambda: self.engine.is_running, timeout=3.0),
            "模拟源没有运行",
        )

        seen: List[object] = []

        def drain_samples() -> bool:
            for kind, payload in self.engine.poll_events():
                if kind == "sample":
                    seen.append(payload["sample"])
            return len(seen) >= 3

        self.assertTrue(wait_until(drain_samples, timeout=8.0), "模拟源没有产出采样")
        for sample in seen:
            self.assertGreaterEqual(sample.bpm, 0)
            self.assertLessEqual(sample.bpm, 200)

    def test_wait_for_state_does_not_swallow_events(self):
        """回归测试：wait_for_state 曾经把事件队列抽干，界面因此收不到「已连接」。"""
        self.engine.connect_simulator("rest")
        self.assertTrue(self.engine.wait_for_state(("connected",), timeout=5.0))
        kinds = [k for k, _ in self._collect()]
        self.assertIn(
            "status", kinds,
            "wait_for_state 消费掉了状态事件；界面会因此看不到连接状态",
        )

    def test_wait_for_state_fails_fast_on_error(self):
        # 引擎一旦报错，wait_for_state 应立刻返回 False，而不是干等到超时
        self.engine.last_error = "模拟的连接失败"
        started = time.monotonic()
        ok = self.engine.wait_for_state(("connected",), timeout=5.0)
        self.assertFalse(ok)
        self.assertLess(time.monotonic() - started, 2.0, "报错后没有快速返回")

    def test_simulator_profiles_are_plausible(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                source = SimulatedHeartRateSource(profile=profile, interval=0.01, seed=1)
                import asyncio

                samples = []

                async def _run() -> None:
                    stop = asyncio.Event()

                    def emit(sample):
                        samples.append(sample)
                        if len(samples) >= 30:
                            stop.set()

                    await source.run(emit, stop)

                asyncio.run(_run())
                self.assertGreaterEqual(len(samples), 30)
                bpms = [s.bpm for s in samples if s.bpm > 0]
                self.assertTrue(bpms, f"{profile} 没有任何有效心率")
                self.assertGreaterEqual(min(bpms), 35)
                self.assertLessEqual(max(bpms), 200)

    def test_unknown_profile_falls_back(self):
        source = SimulatedHeartRateSource(profile="不存在的场景")
        self.assertIn(source.profile, PROFILES)


class TestRecording(EngineTestCase):
    def test_full_record_cycle(self):
        # 正常生命周期使用确定性输入；随机丢帧会令首批不足 5 条，第二批晚于测试截止。
        # 丢帧及断流保存由独立回归测试覆盖。
        # 采样间隔调小：落库要攒够条数或等定时 flush，慢机器上 1 秒一帧会顶到超时。
        source = SimulatedHeartRateSource("workout", interval=0.05, seed=0, dropout_rate=0)
        with patch("heartapp.simulator.SimulatedHeartRateSource", return_value=source):
            self.engine.connect_simulator("workout")
        self.assertTrue(self.engine.wait_for_state(("connected",), timeout=5.0))
        session_id = self.engine.start_recording(note="单元测试", wait=True)
        self.assertIsNotNone(session_id)

        self.assertTrue(
            wait_until(lambda: self.store.sample_count(session_id) >= 5, timeout=20.0),
            "记录期间没有数据落库",
        )

        stats = self.engine.stop_recording(wait=True)
        self.assertIsNotNone(stats)
        self.assertEqual(stats.session_id, session_id)
        self.assertGreaterEqual(stats.sample_count, 5)
        self.assertIsNotNone(stats.avg_bpm)
        self.assertIsNotNone(stats.max_bpm)
        self.assertIsNotNone(stats.min_bpm)
        self.assertIsNotNone(stats.duration_s)

        session = self.store.get_session(session_id)
        self.assertFalse(session.is_open, "停止记录后会话仍是「进行中」")
        self.assertEqual(session.note, "单元测试")
        self.assertEqual(session.source, "sim")
        self.assertGreater(session.sample_count, 0)

    def test_events_are_emitted(self):
        self.engine.drain_events()
        self.engine.connect_simulator("rest")
        self.assertTrue(self.engine.wait_for_state(("connected",), timeout=5.0))
        session_id = self.engine.start_recording(wait=True)
        wait_until(lambda: self.store.sample_count(session_id) >= 3, timeout=8.0)
        self.engine.stop_recording(wait=True)

        kinds = [k for k, _ in self._collect()]
        self.assertIn("status", kinds)
        self.assertIn("sample", kinds)
        self.assertIn("session_started", kinds)
        self.assertIn("session_ended", kinds)

    def test_start_recording_is_idempotent(self):
        self.engine.connect_simulator("rest")
        self.engine.wait_for_state(("connected",), timeout=5.0)
        first = self.engine.start_recording(wait=True)
        second = self.engine.start_recording(wait=True)
        self.assertEqual(first, second)
        self.engine.stop_recording(wait=True)

    def test_stop_recording_without_start_returns_none(self):
        self.assertIsNone(self.engine.stop_recording(wait=True))

    def test_zero_bpm_samples_not_persisted(self):
        """手表测不到脉搏时会发 0 bpm，这种「坏读数」不该写进数据库。"""
        from datetime import datetime

        from heartapp.hr import HeartRateSample

        self.engine.connect_simulator("rest")
        self.engine.wait_for_state(("connected",), timeout=5.0)
        session_id = self.engine.start_recording(wait=True)

        # 先停掉数据源，免得模拟器和我们注入的采样抢同一个缓冲区
        self.engine.stop_source(wait=True)

        now = datetime.now()
        self.engine._dispatch(
            self.engine._on_sample, HeartRateSample(now, 0, source="sim"), wait=True
        )
        self.engine._dispatch(
            self.engine._on_sample, HeartRateSample(now, 75, source="sim"), wait=True
        )
        self.engine.stop_recording(wait=True)

        bpms = [s.bpm for s in self.store.session_samples(session_id)]
        self.assertNotIn(0, bpms)
        self.assertIn(75, bpms)

    def test_sample_count_matches_db_after_stop(self):
        self.engine.connect_simulator("interval")
        self.engine.wait_for_state(("connected",), timeout=5.0)
        session_id = self.engine.start_recording(wait=True)
        wait_until(lambda: self.store.sample_count(session_id) >= 6, timeout=10.0)
        stats = self.engine.stop_recording(wait=True)
        self.assertEqual(stats.valid_sample_count, self.store.sample_count(session_id))


class TestShutdownSafety(EngineTestCase):
    def test_shutdown_finalises_open_session(self):
        """直接退出程序时，正在记录的会话必须被收尾，不能留下永远「进行中」的记录。"""
        self.engine.connect_simulator("rest")
        self.engine.wait_for_state(("connected",), timeout=5.0)
        session_id = self.engine.start_recording(wait=True)
        wait_until(lambda: self.store.sample_count(session_id) >= 3, timeout=8.0)

        self.engine.shutdown()
        session = self.store.get_session(session_id)
        self.assertIsNotNone(session)
        self.assertFalse(session.is_open, "退出后会话没有收尾")

        # 重新创建一个引擎，模拟下次启动
        reopened = HeartRateStore(self.db_path)
        try:
            self.assertIsNotNone(reopened.get_session(session_id).ended_at)
        finally:
            reopened.close()

    def test_pending_samples_flushed_on_stop(self):
        """最后几条还在内存缓冲里的采样，停止记录时必须落库。"""
        self.engine.connect_simulator("rest")
        self.engine.wait_for_state(("connected",), timeout=5.0)
        session_id = self.engine.start_recording(wait=True)
        wait_until(lambda: self.store.sample_count(session_id) >= 2, timeout=8.0)
        stats = self.engine.stop_recording(wait=True)
        self.assertGreater(stats.sample_count, 0)
        self.assertEqual(stats.sample_count, self.store.sample_count(session_id))

    def test_engine_can_restart(self):
        engine = Engine(self.store)
        engine.start()
        engine.connect_simulator("rest")
        self.assertTrue(engine.wait_for_state(("connected",), timeout=5.0))
        engine.shutdown()
        self.assertFalse(engine.is_running)


class TestSourceSwitching(EngineTestCase):
    def test_switching_source_keeps_recording(self):
        self.engine.connect_simulator("rest")
        self.engine.wait_for_state(("connected",), timeout=5.0)
        session_id = self.engine.start_recording(wait=True)
        wait_until(lambda: self.store.sample_count(session_id) >= 3, timeout=8.0)

        # 记录中途切到另一个数据源（真实使用场景：模拟 → 真手表）
        self.engine.connect_simulator("workout")
        self.assertTrue(self.engine.wait_for_state(("connected",), timeout=5.0))
        self.assertTrue(self.engine.is_recording, "切换数据源把记录弄丢了")

        wait_until(lambda: self.store.sample_count(session_id) >= 5, timeout=10.0)
        stats = self.engine.stop_recording(wait=True)
        self.assertGreaterEqual(stats.sample_count, 5)

    def test_stop_source_leaves_session_open(self):
        self.engine.connect_simulator("rest")
        self.engine.wait_for_state(("connected",), timeout=5.0)
        session_id = self.engine.start_recording(wait=True)
        wait_until(lambda: self.store.sample_count(session_id) >= 2, timeout=8.0)

        self.engine.stop_source(wait=True)
        self.assertFalse(self.engine.is_running)
        # 断连不等于停止记录：会话应仍在进行中，等用户决定
        self.assertTrue(self.engine.is_recording)
        self.assertTrue(self.store.get_session(session_id).is_open)
        self.engine.stop_recording(wait=True)


class TestStats(EngineTestCase):
    def test_max_hr_from_settings(self):
        self.assertEqual(self.engine.max_hr(), 190)  # 默认 30 岁
        self.store.set_setting("age", "40")
        self.assertEqual(self.engine.max_hr(), 180)
        self.store.set_setting("max_hr", "175")
        self.assertEqual(self.engine.max_hr(), 175)  # 手动指定优先
        self.store.set_setting("max_hr", "0")
        self.assertEqual(self.engine.max_hr(), 180)  # 0 = 回退到按年龄

    def test_session_stats_reads_from_db(self):
        self.engine.connect_simulator("workout")
        self.engine.wait_for_state(("connected",), timeout=5.0)
        session_id = self.engine.start_recording(wait=True)
        wait_until(lambda: self.store.sample_count(session_id) >= 5, timeout=10.0)
        self.engine.stop_recording(wait=True)

        stats = self.engine.session_stats(session_id)
        self.assertEqual(stats.session_id, session_id)
        self.assertGreater(stats.sample_count, 0)
        self.assertEqual(sum(1 for v in stats.zone_seconds.values() if v >= 0), 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
