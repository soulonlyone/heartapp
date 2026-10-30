"""Heart Rate Measurement (0x2A37) 帧解析测试。

心率帧的字节布局是整套程序的地基：解析错了，后面统计、图表、导出全是错的，
而且错得很隐蔽（数字看起来还挺像回事），所以这里测得比较细。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from heartapp.hr import (  # noqa: E402
    FLAG_ENERGY_EXPENDED,
    FLAG_RR_INTERVAL,
    FLAG_SENSOR_CONTACT_DETECTED,
    FLAG_SENSOR_CONTACT_SUPPORTED,
    FLAG_VALUE_16BIT,
    HEART_RATE_MEASUREMENT_UUID,
    HEART_RATE_SERVICE_UUID,
    HeartRateParseError,
    build_heart_rate_measurement,
    describe_flags,
    is_heart_rate_service,
    normalise_uuid,
    parse_heart_rate_measurement,
)


class TestParseBasics(unittest.TestCase):
    def test_8bit_value(self):
        m = parse_heart_rate_measurement(bytes([0x00, 72]))
        self.assertEqual(m.bpm, 72)
        self.assertIsNone(m.contact)  # 未声明支持贴合检测
        self.assertEqual(m.rr_intervals_ms, ())
        self.assertTrue(m.is_valid)

    def test_16bit_value(self):
        # flags bit0 = 1 → 心率是 uint16 LE
        m = parse_heart_rate_measurement(bytes([0x01, 0x2C, 0x01]))  # 300
        self.assertEqual(m.bpm, 300)
        self.assertEqual(m.flags & FLAG_VALUE_16BIT, FLAG_VALUE_16BIT)

    def test_16bit_small_value_still_parsed(self):
        # 有些设备明明值很小也坚持用 16 位，不能只按 8 位读
        m = parse_heart_rate_measurement(bytes([0x01, 65, 0x00]))
        self.assertEqual(m.bpm, 65)

    def test_zero_bpm_is_parsed_but_not_valid(self):
        m = parse_heart_rate_measurement(bytes([0x00, 0x00]))
        self.assertEqual(m.bpm, 0)
        self.assertFalse(m.is_valid)


class TestSensorContact(unittest.TestCase):
    def test_contact_supported_and_detected(self):
        flags = FLAG_SENSOR_CONTACT_SUPPORTED | FLAG_SENSOR_CONTACT_DETECTED
        m = parse_heart_rate_measurement(bytes([flags, 80]))
        self.assertIs(m.contact, True)
        self.assertEqual(m.contact_text, "已贴合")

    def test_contact_supported_not_detected(self):
        flags = FLAG_SENSOR_CONTACT_SUPPORTED
        m = parse_heart_rate_measurement(bytes([flags, 80]))
        self.assertIs(m.contact, False)
        self.assertEqual(m.contact_text, "未贴合")

    def test_contact_unsupported_gives_none(self):
        m = parse_heart_rate_measurement(bytes([0x00, 80]))
        self.assertIsNone(m.contact)
        self.assertEqual(m.contact_text, "未知")


class TestEnergyAndRR(unittest.TestCase):
    def test_energy_expended(self):
        flags = FLAG_SENSOR_CONTACT_SUPPORTED | FLAG_ENERGY_EXPENDED
        data = bytes([flags, 90, 0x10, 0x00])  # 能量 16 kJ
        m = parse_heart_rate_measurement(data)
        self.assertEqual(m.bpm, 90)
        self.assertEqual(m.energy_expended_kj, 16)

    def test_rr_intervals(self):
        flags = FLAG_RR_INTERVAL
        # 1024/1024 秒 = 1000 ms，512/1024 = 500 ms
        data = bytes([flags, 70, 0x00, 0x04, 0x00, 0x02])
        m = parse_heart_rate_measurement(data)
        self.assertEqual(len(m.rr_intervals_ms), 2)
        self.assertAlmostEqual(m.rr_intervals_ms[0], 1000.0, places=3)
        self.assertAlmostEqual(m.rr_intervals_ms[1], 500.0, places=3)
        self.assertAlmostEqual(m.rr_mean_ms, 750.0, places=3)

    def test_energy_and_rr_combined(self):
        flags = FLAG_ENERGY_EXPENDED | FLAG_RR_INTERVAL
        data = bytes([flags, 75, 0x05, 0x00, 0x00, 0x04])
        m = parse_heart_rate_measurement(data)
        self.assertEqual(m.bpm, 75)
        self.assertEqual(m.energy_expended_kj, 5)
        self.assertEqual(len(m.rr_intervals_ms), 1)
        self.assertAlmostEqual(m.rr_intervals_ms[0], 1000.0, places=3)

    def test_bpm_from_rr(self):
        flags = FLAG_RR_INTERVAL
        data = bytes([flags, 60, 0x00, 0x04])  # RR = 1000 ms → 60 bpm
        m = parse_heart_rate_measurement(data)
        self.assertAlmostEqual(m.to_bpm_from_rr(), 60.0, places=3)

    def test_energy_flag_without_bytes_is_tolerated(self):
        # 声明了 Energy Expended 却没给字节：宽容处理，不抛异常
        flags = FLAG_ENERGY_EXPENDED
        m = parse_heart_rate_measurement(bytes([flags, 88]))
        self.assertEqual(m.bpm, 88)
        self.assertIsNone(m.energy_expended_kj)


class TestMalformedFrames(unittest.TestCase):
    def test_empty_frame(self):
        with self.assertRaises(HeartRateParseError):
            parse_heart_rate_measurement(b"")

    def test_single_byte_frame(self):
        with self.assertRaises(HeartRateParseError):
            parse_heart_rate_measurement(b"\x00")

    def test_truncated_16bit_value(self):
        with self.assertRaises(HeartRateParseError):
            parse_heart_rate_measurement(bytes([FLAG_VALUE_16BIT, 0x10]))

    def test_implausible_bpm_rejected(self):
        # 16 位帧里放 301 bpm，超出 MAX_PLAUSIBLE_BPM(300)，应判为坏帧
        with self.assertRaises(HeartRateParseError):
            parse_heart_rate_measurement(bytes([FLAG_VALUE_16BIT, 0x2D, 0x01]))

    def test_boundary_values_accepted(self):
        # 8 位能表示的 255 在生理上不合理但没超上限，仍按规范接受（不能悄悄丢数据）
        self.assertEqual(parse_heart_rate_measurement(bytes([0x00, 0xFF])).bpm, 255)
        self.assertEqual(
            parse_heart_rate_measurement(bytes([FLAG_VALUE_16BIT, 0x2C, 0x01])).bpm, 300
        )

    def test_none_input(self):
        with self.assertRaises(HeartRateParseError):
            parse_heart_rate_measurement(None)  # type: ignore[arg-type]

    def test_odd_trailing_byte_ignored(self):
        # RR 段多出一个孤立字节：丢掉尾巴，不报错
        flags = FLAG_RR_INTERVAL
        m = parse_heart_rate_measurement(bytes([flags, 70, 0x00, 0x04, 0xAA]))
        self.assertEqual(len(m.rr_intervals_ms), 1)


class TestRoundTrip(unittest.TestCase):
    def test_build_then_parse(self):
        for bpm in (45, 60, 72, 128, 190, 255, 300):
            with self.subTest(bpm=bpm):
                frame = build_heart_rate_measurement(bpm, contact=True)
                m = parse_heart_rate_measurement(frame)
                self.assertEqual(m.bpm, bpm)
                self.assertIs(m.contact, True)

    def test_round_trip_with_rr(self):
        rr = [1000.0, 980.0, 1010.0]
        frame = build_heart_rate_measurement(60, contact=False, rr_intervals_ms=rr)
        m = parse_heart_rate_measurement(frame)
        self.assertEqual(m.bpm, 60)
        self.assertIs(m.contact, False)
        self.assertEqual(len(m.rr_intervals_ms), 3)
        for expected, actual in zip(rr, m.rr_intervals_ms):
            self.assertAlmostEqual(expected, actual, delta=1.0)  # 1/1024 秒量化误差

    def test_round_trip_with_energy(self):
        frame = build_heart_rate_measurement(70, energy_expended_kj=123)
        m = parse_heart_rate_measurement(frame)
        self.assertEqual(m.energy_expended_kj, 123)

    def test_force_16bit_round_trip(self):
        frame = build_heart_rate_measurement(70, force_16bit=True)
        self.assertTrue(frame[0] & FLAG_VALUE_16BIT)
        self.assertEqual(parse_heart_rate_measurement(frame).bpm, 70)


class TestUuidHandling(unittest.TestCase):
    def test_short_uuid_normalised(self):
        self.assertEqual(normalise_uuid("180D"), HEART_RATE_SERVICE_UUID)
        self.assertEqual(normalise_uuid("0x180D"), HEART_RATE_SERVICE_UUID)  # 容错

    def test_long_uuid_normalised(self):
        self.assertEqual(normalise_uuid("0000180D-0000-1000-8000-00805F9B34FB"),
                         HEART_RATE_SERVICE_UUID)

    def test_uuid_object_normalised(self):
        import uuid

        self.assertEqual(normalise_uuid(uuid.UUID(HEART_RATE_MEASUREMENT_UUID)),
                         HEART_RATE_MEASUREMENT_UUID)

    def test_is_heart_rate_service(self):
        self.assertTrue(is_heart_rate_service("180D"))
        self.assertTrue(is_heart_rate_service(HEART_RATE_SERVICE_UUID.upper()))
        self.assertFalse(is_heart_rate_service("180F"))  # 电池服务

    def test_non_uuid_text_passes_through(self):
        self.assertEqual(normalise_uuid("not-a-uuid"), "not-a-uuid")

    def test_none(self):
        self.assertEqual(normalise_uuid(None), "")


class TestDescribeFlags(unittest.TestCase):
    def test_describe(self):
        flags = FLAG_VALUE_16BIT | FLAG_SENSOR_CONTACT_SUPPORTED | FLAG_RR_INTERVAL
        text = describe_flags(flags)
        self.assertIn("16bit", text)
        self.assertIn("contact", text)
        self.assertIn("rr", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
