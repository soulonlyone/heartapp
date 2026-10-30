"""Heart Rate Service 数据模型与 Heart Rate Measurement (0x2A37) 帧解析。

解析规则依据 Bluetooth SIG *Heart Rate Service* 规范 3.1 节（Heart Rate
Measurement characteristic）。手表实际发出的帧偶尔会有不规范之处，因此这里对
*长度* 严格校验，对 *可选尾随字段* 尽量宽容：能解析多少就解析多少，缺字段不报错，
缺字节才报错。
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 标准 UUID
# ---------------------------------------------------------------------------

HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
BODY_SENSOR_LOCATION_UUID = "00002a38-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

#: RR 间期在帧里的单位是 1/1024 秒
RR_UNIT_MS = 1000.0 / 1024.0

# flags 位定义
FLAG_VALUE_16BIT = 0x01
FLAG_SENSOR_CONTACT_DETECTED = 0x02
FLAG_SENSOR_CONTACT_SUPPORTED = 0x04
FLAG_ENERGY_EXPENDED = 0x08
FLAG_RR_INTERVAL = 0x10

BODY_SENSOR_LOCATIONS = {
    0: "其他",
    1: "胸部",
    2: "手腕",
    3: "手指",
    4: "手部",
    5: "耳垂",
    6: "脚部",
}

#: 生理上可能的心率上限，超出即视为坏帧
MAX_PLAUSIBLE_BPM = 300


def normalise_uuid(value: object) -> str:
    """把 16 位短 UUID / UUID 对象 / 字符串统一成长 UUID 小写字符串。"""
    if value is None:
        return ""
    text = str(value).strip().strip("{}").lower()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) == 4:
        text = f"0000{text}-0000-1000-8000-00805f9b34fb"
    try:
        return str(_uuid.UUID(text))
    except (ValueError, AttributeError, TypeError):
        return text


def is_heart_rate_service(value: object) -> bool:
    return normalise_uuid(value) == HEART_RATE_SERVICE_UUID


def body_sensor_location_name(raw: Optional[int]) -> str:
    if raw is None:
        return "未知"
    return BODY_SENSOR_LOCATIONS.get(raw, f"未知({raw})")


# ---------------------------------------------------------------------------
# 解析结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeartRateMeasurement:
    """一帧心率测量值的解析结果。"""

    bpm: int
    contact: Optional[bool] = None
    rr_intervals_ms: Tuple[float, ...] = ()
    energy_expended_kj: Optional[int] = None
    flags: int = 0
    raw: bytes = b""

    @property
    def is_valid(self) -> bool:
        """0 bpm 通常表示手表暂时测不到脉搏，属于有效帧但无有效读数。"""
        return 1 <= self.bpm <= MAX_PLAUSIBLE_BPM

    @property
    def contact_text(self) -> str:
        if self.contact is None:
            return "未知"
        return "已贴合" if self.contact else "未贴合"

    @property
    def rr_mean_ms(self) -> Optional[float]:
        if not self.rr_intervals_ms:
            return None
        return sum(self.rr_intervals_ms) / len(self.rr_intervals_ms)

    def to_bpm_from_rr(self) -> Optional[float]:
        """用 RR 间期反算瞬时心率（比整数量化的 bpm 精细）。"""
        mean = self.rr_mean_ms
        if not mean or mean <= 0:
            return None
        return 60000.0 / mean


@dataclass
class HeartRateSample:
    """写入数据库的一条采样记录。"""

    ts: datetime
    bpm: int
    contact: Optional[bool] = None
    rr_intervals_ms: Tuple[float, ...] = ()
    source: str = "ble"
    t_offset: float = 0.0


class HeartRateParseError(ValueError):
    """帧不合法（长度不足或数值超出合理范围）。"""


def describe_flags(flags: int) -> str:
    parts = []
    parts.append("16bit" if flags & FLAG_VALUE_16BIT else "8bit")
    if flags & FLAG_SENSOR_CONTACT_SUPPORTED:
        parts.append(
            "contact=detected"
            if flags & FLAG_SENSOR_CONTACT_DETECTED
            else "contact=none"
        )
    else:
        parts.append("contact=unsupported")
    if flags & FLAG_ENERGY_EXPENDED:
        parts.append("energy")
    if flags & FLAG_RR_INTERVAL:
        parts.append("rr")
    return "|".join(parts)


def parse_heart_rate_measurement(data: Sequence[int]) -> HeartRateMeasurement:
    """解析 Heart Rate Measurement 特征值。

    帧结构::

        byte 0        flags
        byte 1..n     Heart Rate Value (uint8 或 uint16 LE, 由 flags bit0 决定)
        [uint16 LE]   Energy Expended (kJ)      当 flags bit3 = 1
        [uint16 LE]*  RR-Interval (1/1024 s)    当 flags bit4 = 1

    :raises HeartRateParseError: 帧长度不足或心率数值不可能。
    """
    if data is None:
        raise HeartRateParseError("心率帧为空")
    buf = bytes(data)
    if len(buf) < 2:
        raise HeartRateParseError(f"心率帧过短：{len(buf)} 字节")

    flags = buf[0]
    offset = 1

    if flags & FLAG_VALUE_16BIT:
        if len(buf) < offset + 2:
            raise HeartRateParseError("flags 声明 16 位心率，但帧长度不足")
        bpm = int.from_bytes(buf[offset : offset + 2], "little")
        offset += 2
    else:
        bpm = buf[offset]
        offset += 1

    if bpm > MAX_PLAUSIBLE_BPM:
        raise HeartRateParseError(f"心率数值不合理：{bpm}")

    contact: Optional[bool] = None
    if flags & FLAG_SENSOR_CONTACT_SUPPORTED:
        contact = bool(flags & FLAG_SENSOR_CONTACT_DETECTED)

    energy: Optional[int] = None
    if flags & FLAG_ENERGY_EXPENDED:
        if len(buf) >= offset + 2:
            energy = int.from_bytes(buf[offset : offset + 2], "little")
            offset += 2
        # 声明了却没有字节：宽容处理，不报错

    rr_intervals = []
    if flags & FLAG_RR_INTERVAL:
        while len(buf) >= offset + 2:
            raw = int.from_bytes(buf[offset : offset + 2], "little")
            offset += 2
            rr_intervals.append(raw * RR_UNIT_MS)

    return HeartRateMeasurement(
        bpm=bpm,
        contact=contact,
        rr_intervals_ms=tuple(rr_intervals),
        energy_expended_kj=energy,
        flags=flags,
        raw=buf,
    )


def build_heart_rate_measurement(
    bpm: int,
    *,
    contact: Optional[bool] = None,
    rr_intervals_ms: Sequence[float] = (),
    energy_expended_kj: Optional[int] = None,
    force_16bit: bool = False,
) -> bytes:
    """构造一帧 Heart Rate Measurement 字节串。

    用于单元测试，以及 ``tools/fake_watch.py`` 里的虚拟手表。
    """
    flags = 0
    if force_16bit or bpm > 0xFF:
        flags |= FLAG_VALUE_16BIT
    if contact is not None:
        flags |= FLAG_SENSOR_CONTACT_SUPPORTED
        if contact:
            flags |= FLAG_SENSOR_CONTACT_DETECTED
    if energy_expended_kj is not None:
        flags |= FLAG_ENERGY_EXPENDED
    if rr_intervals_ms:
        flags |= FLAG_RR_INTERVAL

    out = bytearray([flags])
    out += int(bpm).to_bytes(2 if flags & FLAG_VALUE_16BIT else 1, "little")
    if energy_expended_kj is not None:
        out += int(energy_expended_kj).to_bytes(2, "little")
    for rr in rr_intervals_ms:
        raw = int(round(float(rr) / RR_UNIT_MS))
        raw = max(0, min(raw, 0xFFFF))
        out += raw.to_bytes(2, "little")
    return bytes(out)
