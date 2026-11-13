"""真实本机网络下的房间隔离、共享授权、输入错误及断线清理。"""
from __future__ import annotations

import asyncio
import unittest
import urllib.request

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from heartapp.relay import Relay, start_server
from heartapp.room_protocol import VERSION, decode, encode, room_code, server_url


class TestRoomAddress(unittest.TestCase):
    def test_domain_ipv4_ipv6_and_path(self):
        self.assertEqual(server_url("https://hr.example.com"), "wss://hr.example.com/")
        self.assertEqual(server_url("ws://127.0.0.1:8765"), "ws://127.0.0.1:8765/")
        self.assertEqual(server_url("http://[::1]:8765"), "ws://[::1]:8765/")
        self.assertEqual(server_url("hr.example.com/relay/"), "wss://hr.example.com/relay")

    def test_short_addresses_infer_scheme_from_port(self):
        cases = {
            "192.168.1.20:8765": "ws://192.168.1.20:8765/",
            "relay.example.com:9000": "ws://relay.example.com:9000/",
            "[::1]:8765": "ws://[::1]:8765/",
            "relay.example.com:443/relay": "wss://relay.example.com:443/relay",
            "relay.example.com": "wss://relay.example.com/",
            "wss://relay.example.com:9443": "wss://relay.example.com:9443/",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(server_url(value), expected)

    def test_bad_address_and_room_code(self):
        for value in ("file:///tmp/test", "ws://", "https://user:secret@example.com", "ws://example.com:bad"):
            with self.assertRaises(ValueError):
                server_url(value)
        self.assertEqual(room_code("abcd-2345"), "ABCD2345")
        with self.assertRaises(ValueError):
            room_code("123")


class TestRelay(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.relay = Relay(room_size=2)
        self.server = await start_server(self.relay, port=0)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        self.ticker = asyncio.create_task(self.relay.ticker())
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients:
            try:
                await client.send(encode(dict(type="logout")))
            except ConnectionClosed:
                pass
            await client.close()
        self.server.close()
        await self.server.wait_closed()
        self.ticker.cancel()
        await asyncio.gather(self.ticker, return_exceptions=True)

    async def open(self, name="玩家", code="", **fields):
        client = await connect(self.url, proxy=None)
        self.clients.append(client)
        hello = dict(type="login", version=VERSION, name=name)
        hello.update(fields)
        await client.send(encode(hello))
        response = decode(await client.recv())
        if response.get("type") == "error":
            return client, response
        await client.send(encode(dict(type="join" if code else "create", room=code)))
        response = await self.receive(client, lambda p: p.get("type") in ("welcome", "command_error", "error"))
        return client, response

    async def receive(self, client, predicate):
        async def wait():
            while True:
                packet = decode(await client.recv())
                if predicate(packet):
                    return packet
        return await asyncio.wait_for(wait(), 4)

    async def reading(self, client, bpm, sharing=True):
        await client.send(encode(dict(type="reading", bpm=bpm, sharing=sharing, simulated=True)))

    async def test_room_isolation_and_server_owned_identity(self):
        first, welcome = await self.open("一号")
        second, _ = await self.open("二号", welcome["room"])
        third, separate = await self.open("另一房间")
        await self.reading(first, 82)
        shared = await self.receive(second, lambda p: any(m.get("bpm") == 82 for m in p.get("members", [])))
        self.assertEqual(len(shared["members"]), 2)
        self.assertEqual(shared["members"][0]["id"], welcome["self_id"])
        other = await self.receive(third, lambda p: p.get("type") == "snapshot")
        self.assertEqual(len(other["members"]), 1)
        self.assertEqual(other["room"], separate["room"])

    async def test_sharing_off_and_expired_data_never_expose_bpm(self):
        client, welcome = await self.open()
        await self.reading(client, 88, sharing=False)
        snapshot = await self.receive(client, lambda p: p.get("type") == "snapshot")
        self.assertIsNone(snapshot["members"][0]["bpm"])
        await self.reading(client, 88)
        await self.receive(client, lambda p: any(m.get("bpm") == 88 for m in p.get("members", [])))
        member = self.relay.rooms[welcome["room"]][welcome["self_id"]]
        member.updated -= 6
        snapshot = await self.receive(client, lambda p: p.get("type") == "snapshot" and p["members"][0]["bpm"] is None)
        self.assertIsNone(snapshot["members"][0]["bpm"])

    async def test_capacity_missing_room_and_version(self):
        first, welcome = await self.open()
        await self.open("二号", welcome["room"])
        _, full = await self.open("三号", welcome["room"])
        self.assertEqual(full["type"], "command_error")
        self.assertIn("已满", full["message"])
        _, missing = await self.open(code="ABCDEFGH")
        self.assertEqual(missing["type"], "command_error")
        _, incompatible = await self.open(version=99)
        self.assertIn("版本", incompatible["message"])

    async def test_access_password(self):
        self.relay.password = "私人的密码"
        _, denied = await self.open(password="wrong")
        self.assertIn("密码", denied["message"])
        _, accepted = await self.open(password="私人的密码")
        self.assertEqual(accepted["type"], "welcome")

    async def test_invalid_data_does_not_crash_server(self):
        client, _ = await self.open()
        await client.send("{broken json")
        response = await self.receive(client, lambda p: p.get("type") == "error")
        self.assertIn("格式", response["message"])
        _, accepted = await self.open("仍可连接")
        self.assertEqual(accepted["type"], "welcome")

    async def test_disconnect_removes_member_and_empty_room(self):
        client, welcome = await self.open()
        await client.close()
        for _ in range(100):
            member = self.relay.rooms[welcome["room"]][welcome["self_id"]]
            if member.socket is None:
                break
            await asyncio.sleep(.01)
        self.assertFalse(self.relay.snapshot(welcome["room"])["members"][0]["connected"])
        member.offline_since -= 31
        for _ in range(150):
            if welcome["room"] not in self.relay.rooms:
                break
            await asyncio.sleep(.01)
        self.assertNotIn(welcome["room"], self.relay.rooms)

    async def test_flood_is_rejected(self):
        client, _ = await self.open()
        for _ in range(9):
            await self.reading(client, 80)
        error = await self.receive(client, lambda p: p.get("type") == "error")
        self.assertIn("频繁", error["message"])

    async def test_health_endpoint(self):
        def fetch():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(self.url.replace("ws://", "http://") + "/healthz", timeout=2) as response:
                return response.status, response.read()
        status, body = await asyncio.to_thread(fetch)
        self.assertEqual(status, 200)
        self.assertEqual(body.strip(), b"OK")

    async def login(self, **fields):
        client = await connect(self.url, proxy=None)
        self.clients.append(client)
        hello = dict(type="login", version=VERSION, name="大厅玩家")
        hello.update(fields)
        await client.send(encode(hello))
        return client, decode(await client.recv())

    async def test_login_lists_rooms_without_joining_and_leave_keeps_login(self):
        creator, welcome = await self.open("房主")
        lobby, login = await self.login()
        self.assertEqual(login["type"], "login_ok")
        self.assertEqual(login["room"], "")
        directory = await self.receive(lobby, lambda p: p.get("type") == "rooms")
        self.assertEqual(directory["rooms"][0]["room"], welcome["room"])
        self.assertNotIn("bpm", directory["rooms"][0])
        await lobby.send(encode(dict(type="join", room=welcome["room"])))
        await self.receive(lobby, lambda p: p.get("type") == "welcome")
        await lobby.send(encode(dict(type="leave")))
        await self.receive(lobby, lambda p: p.get("type") == "left")
        self.assertIn(login["resume_token"], self.relay.sessions)
        await lobby.send(encode(dict(type="create", title="换一个房间")))
        new_room = await self.receive(lobby, lambda p: p.get("type") == "welcome")
        self.assertEqual(new_room["room_name"], "换一个房间")

    async def test_failed_join_stays_logged_in(self):
        client, login = await self.login()
        await client.send(encode(dict(type="join", room="ABCDEFGH")))
        await self.receive(client, lambda p: p.get("type") == "command_error")
        await client.send(encode(dict(type="list")))
        directory = await self.receive(client, lambda p: p.get("type") == "rooms")
        self.assertEqual(directory["rooms"], [])
        self.assertIn(login["resume_token"], self.relay.sessions)

    async def test_resume_preserves_identity_and_room(self):
        client, login = await self.login()
        await client.send(encode(dict(type="create")))
        room = await self.receive(client, lambda p: p.get("type") == "welcome")
        await client.close()
        for _ in range(100):
            if self.relay.sessions[login["resume_token"]].socket is None:
                break
            await asyncio.sleep(.01)
        resumed, response = await self.login(resume_token=login["resume_token"])
        self.assertEqual(response["self_id"], login["self_id"])
        self.assertEqual(response["room"], room["room"])
        self.assertEqual((await self.receive(resumed, lambda p: p.get("type") == "welcome"))["room"], room["room"])

    async def test_reading_before_login_is_rejected(self):
        client = await connect(self.url, proxy=None)
        self.clients.append(client)
        await client.send(encode(dict(type="list", version=VERSION)))
        response = decode(await client.recv())
        self.assertEqual(response["type"], "error")
        self.assertIn("登录", response["message"])
