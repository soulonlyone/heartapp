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

from .room_protocol import DEFAULT_ROOM_SIZE, MAX_ROOM_SIZE, VERSION
from . import __version__
from .lobby import Relay


async def start_server(relay: Relay, host: str = "127.0.0.1", port: int = 8765,
                       *, tls: Optional[ssl.SSLContext] = None) -> Server:
    def health(connection: ServerConnection, request: Request) -> Optional[Response]:
        if request.path == "/healthz":
            # 只回 OK：不暴露版本与协议号，避免给扫描器提供指纹。
            return connection.respond(200, "OK\n")
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
                  password=args.password or os.environ.get("HEARTAPP_RELAY_PASSWORD", ""))
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
    parser = argparse.ArgumentParser(
        description="HeartApp 自部署心率转发服务（不保存心率历史）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("示例：\n"
                "  python -m heartapp.relay --host 127.0.0.1 --port 8765 --max-clients 20\n"
                "  python -m heartapp.relay --allow-plaintext --host 0.0.0.0 --room-size 4\n\n"
                "密码：优先使用环境变量 HEARTAPP_RELAY_PASSWORD，避免写入进程命令行。\n"
                "验收：访问 http://127.0.0.1:8765/healthz 返回 OK；前台运行按 Ctrl+C 停止。\n"
                "帮助不会启动服务。完整操作见 server/Linux说明.md、server/Docker说明.md。"),
    )
    parser.add_argument("--version", action="version", version=f"HeartApp relay {__version__} · protocol {VERSION}")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址；局域网直连用 0.0.0.0（默认：%(default)s）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认：%(default)s）")
    parser.add_argument("--max-clients", type=int, default=100, help="总连接上限 1～10000；小型部署建议 20（默认：%(default)s）")
    parser.add_argument("--room-size", type=int, default=os.getenv("HEARTAPP_ROOM_SIZE", str(DEFAULT_ROOM_SIZE)),
                        help=f"每房间人数 1～{MAX_ROOM_SIZE}（默认：%(default)s）")
    parser.add_argument("--cert", help="TLS 证书文件")
    parser.add_argument("--key", help="TLS 私钥文件")
    parser.add_argument(
        "--allow-plaintext",
        action="store_true",
        help="允许在非本机地址上以明文运行；仅适合可信局域网（公网请用 HTTPS 反代）",
    )
    parser.add_argument(
        "--password",
        help="访问密码；也可用环境变量 HEARTAPP_RELAY_PASSWORD。"
             "长期运行的服务建议用环境变量，避免密码出现在进程命令行里",
    )
    args = parser.parse_args()
    if not 1 <= args.room_size <= MAX_ROOM_SIZE or not 1 <= args.max_clients <= 10000:
        parser.error(f"房间人数应为 1～{MAX_ROOM_SIZE}，总连接上限应为 1～10000")
    if bool(args.cert) != bool(args.key):
        parser.error("--cert 与 --key 必须同时提供")
    loopback = {"127.0.0.1", "localhost", "::1", "[::1]"}
    if not args.cert and args.host not in loopback and not args.allow_plaintext:
        parser.error(
            f"拒绝在 {args.host} 上以明文启动：访问密码和心率都会明文经过网络。\n"
            "  仅局域网使用：加 --allow-plaintext；\n"
            "  公网使用：先配置 HTTPS 反向代理，本机监听 127.0.0.1（见 server/README.md）。"
        )
    # 不记录 WebSocket 消息、昵称、房间码或心率。
    logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
