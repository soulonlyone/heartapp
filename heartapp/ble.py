"""真实手表数据源：BLE 标准心率服务（0x180D / 0x2A37）。

为什么用标准心率服务
--------------------
OPPO Watch X2 的「蓝牙心率广播 / 心率分享」就是按 Bluetooth SIG 的 Heart Rate
Service 广播心率，健身器材、码表都是这么读的。好处是**不需要**厂商云 API、不需要
手机 App 中转、不需要审核。

常见坑
------
1. 手表端必须先打开「心率广播」，否则设备上根本没有 0x180D 服务，连接会失败并
   报 :class:`NoHeartRateServiceError`。
2. 广播开关有时在手表的运动模式里（开始一个运动后才有「心率分享」）。
3. Windows 上必须在「设置 → 蓝牙和其他设备」里确认蓝牙已打开；部分机器还需允许
   应用访问蓝牙。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional, Sequence

from bleak import BleakClient, BleakScanner

from .hr import (
    BATTERY_LEVEL_UUID,
    BODY_SENSOR_LOCATION_UUID,
    HEART_RATE_MEASUREMENT_UUID,
    HEART_RATE_SERVICE_UUID,
    HeartRateParseError,
    HeartRateSample,
    body_sensor_location_name,
    is_heart_rate_service,
    normalise_uuid,
    parse_heart_rate_measurement,
)
from .source import (
    STATE_CONNECTED,
    STATE_CONNECTING,
    STATE_RECONNECTING,
    DeviceNotFoundError,
    EmitFn,
    HeartRateSourceError,
    NoHeartRateServiceError,
    StatusFn,
    make_status_callback,
    sleep_or_stop,
)
from .paths import cli_hint

#: 重连最大退避秒数
MAX_BACKOFF_S = 20.0

#: 心跳检查间隔：用于发现底层连接已经断开
CONNECTION_POLL_S = 0.25


@dataclass
class DiscoveredDevice:
    """扫描结果。

    ``handle`` 保存 bleak 的 ``BLEDevice`` 对象（不透明）。Windows(WinRT) 后端
    连接时必须带回这个对象，只用地址字符串会重新解析、更容易出错。
    """

    name: str
    address: str
    rssi: int
    service_uuids: Sequence[str] = field(default_factory=tuple)
    has_heart_rate: bool = False
    handle: object = None

    @property
    def label(self) -> str:
        name = self.name or "(未知设备)"
        mark = " ♥" if self.has_heart_rate else ""
        return f"{name}  [{self.address}]  {self.rssi}dBm{mark}"


def _to_discovered(device: object, adv: object) -> DiscoveredDevice:
    name = getattr(adv, "local_name", None) or getattr(device, "name", None) or ""
    uuids = [normalise_uuid(u) for u in (getattr(adv, "service_uuids", None) or [])]
    address = str(getattr(device, "address", "") or "")
    return DiscoveredDevice(
        name=str(name),
        address=address,
        rssi=int(getattr(adv, "rssi", 0) or 0),
        service_uuids=tuple(uuids),
        has_heart_rate=any(is_heart_rate_service(u) for u in uuids),
        handle=device,
    )


async def scan_devices(
    timeout: float = 8.0,
    *,
    include_all: bool = True,
    name_filter: Optional[str] = None,
) -> List[DiscoveredDevice]:
    """扫描附近的 BLE 设备。

    返回按「是否带心率服务 → 信号强度」排序的列表。
    """
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    devices: List[DiscoveredDevice] = []
    for _address, pair in found.items():
        try:
            device, adv = pair
        except (TypeError, ValueError):
            continue
        item = _to_discovered(device, adv)
        if name_filter:
            needle = name_filter.lower()
            if needle not in item.name.lower() and needle not in item.address.lower():
                continue
        if not include_all and not item.has_heart_rate:
            continue
        devices.append(item)

    devices.sort(key=lambda d: (not d.has_heart_rate, -d.rssi, d.name.lower()))
    return devices


async def find_device(
    query: str, *, timeout: float = 12.0
) -> DiscoveredDevice:
    """按名称子串或 MAC 地址找设备。"""
    needle = (query or "").strip().lower()
    if not needle:
        raise DeviceNotFoundError("未指定设备名称或地址")
    devices = await scan_devices(timeout=timeout, include_all=True)
    # 先精确匹配地址，再模糊匹配名称
    for d in devices:
        if d.address.lower() == needle:
            return d
    for d in devices:
        if needle in d.name.lower():
            return d
    for d in devices:
        if needle in d.address.lower():
            return d
    names = "、".join(d.name or d.address for d in devices[:8]) or "（无）"
    raise DeviceNotFoundError(
        f"没有扫描到匹配 “{query}” 的设备。本次扫到的设备：{names}"
    )


class BleHeartRateSource:
    """连接 BLE 心率设备并持续产出心率，自带断线重连。"""

    kind = "ble"

    def __init__(
        self,
        device: object,
        *,
        connect_timeout: float = 20.0,
        display_name: Optional[str] = None,
    ) -> None:
        self._device = device
        self._connect_timeout = connect_timeout
        self._display_name = display_name or self._guess_name(device)
        self.address = str(getattr(device, "address", "") or "")
        self.body_sensor_location: Optional[int] = None
        self.battery_percent: Optional[int] = None
        self.parse_errors = 0
        self.dropouts = 0
        self.received_frames = 0

    @property
    def label(self) -> str:
        return self._display_name

    @staticmethod
    def _guess_name(device: object) -> str:
        if isinstance(device, DiscoveredDevice):
            return device.name or device.address or "BLE 设备"
        return str(getattr(device, "name", None) or getattr(device, "address", "") or "BLE 设备")

    def _resolve_handle(self) -> object:
        if isinstance(self._device, DiscoveredDevice):
            return self._device.handle if self._device.handle is not None else self._device.address
        return self._device

    async def run(
        self,
        emit: EmitFn,
        stop: asyncio.Event,
        on_status: Optional[StatusFn] = None,
    ) -> None:
        status = make_status_callback(on_status)
        loop = asyncio.get_running_loop()

        disconnected = asyncio.Event()

        def _on_disconnected(_client: object) -> None:
            # bleak 可能从别的线程回调，必须回到事件循环里操作 asyncio.Event
            try:
                loop.call_soon_threadsafe(disconnected.set)
            except RuntimeError:
                pass

        pending_rr: List[float] = []

        def _on_measurement(_sender: object, data: bytearray) -> None:
            self.received_frames += 1
            try:
                measurement = parse_heart_rate_measurement(data)
            except HeartRateParseError:
                self.parse_errors += 1
                return
            if not measurement.is_valid:
                # 手表暂时测不到脉搏（0 bpm），属于正常现象
                self.dropouts += 1
                return

            intervals = tuple(measurement.rr_intervals_ms)
            emit(
                HeartRateSample(
                    ts=datetime.now(),
                    bpm=measurement.bpm,
                    contact=measurement.contact,
                    rr_intervals_ms=intervals,
                    source=self.kind,
                )
            )

        attempt = 0
        client: Optional[BleakClient] = None

        while not stop.is_set():
            associated = True
            disconnected.clear()
            attempt += 1
            status(
                STATE_CONNECTING if attempt == 1 else STATE_RECONNECTING,
                f"正在连接 {self._display_name}（第 {attempt} 次）…",
            )

            try:
                client = BleakClient(
                    self._resolve_handle(),
                    disconnected_callback=_on_disconnected,
                    timeout=self._connect_timeout,
                )
                await client.connect()
            except Exception as exc:  # noqa: BLE001 - 任何连接失败都走重连
                associated = False
                status(
                    STATE_RECONNECTING,
                    f"连接失败：{exc}",
                )
            else:
                try:
                    characteristic = client.services.get_characteristic(
                        HEART_RATE_MEASUREMENT_UUID
                    )
                    if characteristic is None:
                        raise NoHeartRateServiceError(
                            "该设备没有心率测量特征（0x2A37）。"
                            "请先在手表上打开「心率广播 / 心率分享」，"
                            "例如 OPPO Watch 通常在 设置 → 运动与健康（或运动 App 内的分享）里。"
                        )
                    await self._read_metadata(client)
                    await client.start_notify(characteristic, _on_measurement)
                except NoHeartRateServiceError as exc:
                    associated = False
                    status(STATE_RECONNECTING, str(exc))
                except Exception as exc:  # noqa: BLE001
                    associated = False
                    status(STATE_RECONNECTING, f"订阅心率通知失败：{exc}")
                else:
                    attempt = 0
                    detail = f"已连接 {self._display_name}"
                    if self.body_sensor_location is not None:
                        detail += f"（佩戴位置：{body_sensor_location_name(self.body_sensor_location)}"
                        if self.battery_percent is not None:
                            detail += f"，电量 {self.battery_percent}%"
                        detail += "）"
                    elif self.battery_percent is not None:
                        detail += f"（电量 {self.battery_percent}%）"
                    status(STATE_CONNECTED, detail)

                    # 保持连接，直到收到停止信号或底层断开
                    while not stop.is_set() and not disconnected.is_set():
                        if not client.is_connected:
                            break
                        try:
                            await asyncio.wait_for(
                                disconnected.wait(), timeout=CONNECTION_POLL_S
                            )
                        except asyncio.TimeoutError:
                            pass

                    if stop.is_set():
                        break
                    associated = False
                    status(STATE_RECONNECTING, "连接已断开，准备重连…")

            finally:
                if client is not None:
                    try:
                        if client.is_connected:
                            await client.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
                    client = None

            if stop.is_set():
                break

            backoff = min(MAX_BACKOFF_S, 2.0 ** min(attempt, 5))
            if await sleep_or_stop(backoff, stop):
                break

        status("stopped", "已停止")

    async def _read_metadata(self, client: BleakClient) -> None:
        """读取佩戴位置和电量。失败无所谓，属于锦上添花。"""
        try:
            raw = await client.read_gatt_char(BODY_SENSOR_LOCATION_UUID)
            if raw:
                self.body_sensor_location = int(raw[0])
        except Exception:  # noqa: BLE001
            self.body_sensor_location = None
        try:
            raw = await client.read_gatt_char(BATTERY_LEVEL_UUID)
            if raw:
                self.battery_percent = int(raw[0])
        except Exception:  # noqa: BLE001
            self.battery_percent = None


async def probe_device(device: object) -> str:
    """诊断用：连接一个设备并报告它是否提供标准心率服务。

    ``device`` 应当是扫描得到的 :class:`DiscoveredDevice`（或 bleak 的 ``BLEDevice``）。
    Windows(WinRT) 后端只认扫描产物，直接传地址字符串往往连不上，所以这里不做
    「按地址重连」这种取巧的事。
    """
    if isinstance(device, DiscoveredDevice):
        handle = device.handle if device.handle is not None else device.address
        title = device.name or device.address
    else:
        handle = device
        title = str(getattr(device, "name", None) or getattr(device, "address", "") or device)

    client = BleakClient(handle, timeout=20.0)
    await client.connect()
    try:
        services = list(client.services)
        lines = [f"已连接 {title}，共 {len(services)} 个服务："]
        for svc in services:
            lines.append(f"  - {normalise_uuid(svc.uuid)}")
        has = client.services.get_characteristic(HEART_RATE_MEASUREMENT_UUID) is not None
        lines.append("")
        if has:
            lines.append("✓ 找到心率测量特征 0x2A37，可以用本程序读数。")
            lines.append(
                f'  试一下：{cli_hint()} record --device "{title}" --duration 20'
            )
        else:
            lines.append("✗ 没找到心率测量特征 0x2A37 —— 手表端的「心率广播」没开。")
            lines.append("  例如 OPPO Watch 一般在 设置 → 运动与健康，或运动 App 里的「心率分享 / 心率广播」。")
        return "\n".join(lines)
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
