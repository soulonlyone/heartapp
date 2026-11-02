"""可自部署的心率房间转发服务：python -m heartapp.relay。"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import ssl
from typing import Optional

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.http11 import Request, Response

from .room_protocol import VERSION
from .lobby import Relay


async def start_server(relay: Relay, host: str = "127.0.0.1", port: int = 8765,
                       *, tls: Optional[ssl.SSLContext] = None) -> Server:
    def health(connection: ServerConnection, request: Request) -> Optional[Response]:
        if request.path == "/healthz":
            return connection.respond(200, f"HeartApp relay v{VERSION} OK\n")
        if request.path != "/":
            return connection.respond(404, "Not found\n")
        return None

    return await serve(relay.handler, host, port, ssl=tls, compression=None, max_size=2048,
                       max_queue=4, write_limit=16384, open_timeout=10, close_timeout=2,
                       ping_interval=20, ping_timeout=20, origins=[None], process_request=health)


async def run(args: argparse.Namespace) -> None:
    tls = None
    if args.cert and args.key:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(args.cert, args.key)
    relay = Relay(max_clients=args.max_clients, room_size=args.room_size,
                  password=os.environ.get("HEARTAPP_RELAY_PASSWORD", ""))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    if os.name != "nt":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
    async with await start_server(relay, args.host, args.port, tls=tls):
        ticker = asyncio.create_task(relay.ticker())
        print(f"HeartApp relay v{VERSION} · {args.host}:{args.port} · max {args.max_clients} · room {args.room_size}", flush=True)
        try:
            await stop.wait()
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="HeartApp 自部署心率转发服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--max-clients", type=int, default=100)
    parser.add_argument("--room-size", type=int, default=4)
    parser.add_argument("--cert", help="TLS 证书文件")
    parser.add_argument("--key", help="TLS 私钥文件")
    args = parser.parse_args()
    if not 1 <= args.room_size <= 4 or not 1 <= args.max_clients <= 10000:
        parser.error("房间人数应为 1～4，总连接上限应为 1～10000")
    if bool(args.cert) != bool(args.key):
        parser.error("--cert 与 --key 必须同时提供")
    # 不记录 WebSocket 消息、昵称、房间码或心率。
    logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
