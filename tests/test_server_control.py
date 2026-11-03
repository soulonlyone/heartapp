"""面板配置、线程失败恢复与真实转发服务生命周期。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from websockets.asyncio.client import connect

from heartapp.room_protocol import VERSION, decode, encode
from heartapp.server_control import ServerController, ServerSettings, load_settings, save_settings


class TestServerSettings(unittest.TestCase):
    def test_roundtrip_has_no_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings = ServerSettings("local", 9000, 12, 3, "https://hr.example.com")
            save_settings(path, settings)
            self.assertEqual(load_settings(path), (settings, ""))
            self.assertNotIn("password", json.loads(path.read_text(encoding="utf-8")))
            self.assertEqual(settings.public_address, "wss://hr.example.com/")

    def test_missing_or_corrupt_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            self.assertEqual(load_settings(path), (ServerSettings(), ""))
            for value in ('bad json', '[]', '{"port":0}', '{"public_url":123}'):
                path.write_text(value, encoding="utf-8")
                settings, warning = load_settings(path)
                self.assertEqual(settings, ServerSettings())
                self.assertTrue(warning)

    def test_validation_rejects_invalid_and_credential_urls(self):
        for settings in (ServerSettings(port=0), ServerSettings(port=True), ServerSettings(max_clients=0),
                         ServerSettings(room_size=5), ServerSettings(scope="invalid"),
                         ServerSettings(public_url="ws://example.com"),
                         ServerSettings(public_url="https://user:secret@example.com")):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                settings.validate()

    def test_save_failure_retains_previous_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            save_settings(path, ServerSettings())
            with patch.object(Path, "replace", side_effect=PermissionError("read only")):
                with self.assertRaises(PermissionError):
                    save_settings(path, ServerSettings(port=9000))
            self.assertEqual(load_settings(path)[0], ServerSettings())


class TestServerController(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller = ServerController()
        self.events = []
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.settings = ServerSettings("local", self.port)

    async def asyncTearDown(self):
        self.controller.stop()
        await self.until(lambda: not self.controller.active)

    async def until(self, predicate, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.events.extend(self.controller.poll())
            if predicate():
                return
            await asyncio.sleep(.02)
        self.fail("后台服务状态未按预期恢复")

    async def running(self):
        self.assertTrue(self.controller.start(self.settings, "演示口令"))
        await self.until(lambda: any(kind == "running" for kind, _ in self.events))

    async def test_real_login_counts_stop_and_restart(self):
        await self.running()
        self.assertFalse(self.controller.start(self.settings, "演示口令"))
        async with connect(f"ws://127.0.0.1:{self.port}") as socket_client:
            await socket_client.send(encode(dict(type="login", version=VERSION, name="玩家一", password="演示口令")))
            self.assertEqual(decode(await socket_client.recv())["type"], "login_ok")
            await socket_client.send(encode(dict(type="create", name="测试房间")))
            await self.until(lambda: any(kind == "stats" and p["online"] == 1 and p["rooms"] == 1
                                         for kind, p in self.events))
            self.controller.stop()
            await self.until(lambda: not self.controller.active)
            await asyncio.wait_for(socket_client.wait_closed(), timeout=3)
        self.events.clear()
        await self.running()

    async def test_occupied_port_then_retry(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", self.port))
            occupied.listen()
            self.controller.start(self.settings, "演示口令")
            await self.until(lambda: not self.controller.active)
            self.assertTrue(any(kind == "error" and "端口已被占用" in p["message"] for kind, p in self.events))
        self.events.clear()
        await self.running()

    async def test_background_exception_restores_retry_state(self):
        with patch("heartapp.server_control.start_server", new=AsyncMock(side_effect=RuntimeError("private detail"))):
            self.controller.start(self.settings, "演示口令")
            await self.until(lambda: not self.controller.active)
            message = next(p["message"] for kind, p in self.events if kind == "error")
            self.assertNotIn("private detail", message)
        self.events.clear()
        await self.running()

    async def test_immediate_stop_and_empty_password(self):
        with self.assertRaises(ValueError):
            self.controller.start(self.settings, "")
        self.assertFalse(self.controller.active)
        self.controller.start(self.settings, "演示口令")
        self.controller.stop()
        await self.until(lambda: not self.controller.active)
