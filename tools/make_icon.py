"""生成 heartapp.ico —— 纯标准库实现，不依赖 Pillow。

图标是一个红色心形。心形用隐函数 ``(x²+y²-1)³ - x²y³ <= 0`` 画出来，
再套 4×4 超采样做抗锯齿。PNG 用 zlib 手工编码（ICO 从 Vista 起允许直接内嵌 PNG）。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4

# 颜色：上浅下深，看起来有点立体感
TOP_COLOR = (229, 72, 77)
BOTTOM_COLOR = (170, 22, 28)


def _heart_inside(x: float, y: float) -> bool:
    """隐函数心形。y 轴向上。"""
    a = x * x + y * y - 1.0
    return a * a * a - x * x * y * y * y <= 0.0


def _coverage(px: int, py: int, size: int) -> float:
    """一个像素被心形覆盖的比例（0..1），靠超采样得到。"""
    hits = 0
    for sy in range(SUPERSAMPLE):
        for sx in range(SUPERSAMPLE):
            fx = (px + (sx + 0.5) / SUPERSAMPLE) / size
            fy = (py + (sy + 0.5) / SUPERSAMPLE) / size
            # 映射到 [-1.3, 1.3]，心形横向约 ±1.13、纵向约 -1.26..1.0
            x = (fx * 2.0 - 1.0) * 1.3
            y = (1.0 - fy * 2.0) * 1.15 + 0.05
            if _heart_inside(x, y):
                hits += 1
    return hits / float(SUPERSAMPLE * SUPERSAMPLE)


def _pixels(size: int):
    rows = []
    for py in range(size):
        row = bytearray()
        t = py / max(1, size - 1)
        for px in range(size):
            cover = _coverage(px, py, size)
            if cover <= 0.0:
                row += bytes((0, 0, 0, 0))
                continue
            # 垂直渐变
            r = int(TOP_COLOR[0] + (BOTTOM_COLOR[0] - TOP_COLOR[0]) * t)
            g = int(TOP_COLOR[1] + (BOTTOM_COLOR[1] - TOP_COLOR[1]) * t)
            b = int(TOP_COLOR[2] + (BOTTOM_COLOR[2] - TOP_COLOR[2]) * t)
            alpha = int(round(cover * 255))
            row += bytes((r, g, b, alpha))
        rows.append(bytes(row))
    return rows


def _png(size: int) -> bytes:
    rows = _pixels(size)
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def build_ico(target: Path) -> Path:
    images = [_png(size) for size in SIZES]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = bytearray()
    for size, blob in zip(SIZES, images):
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,  # 256 用 0 表示
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(blob),
            offset,
        )
        offset += len(blob)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(header + bytes(entries) + b"".join(images))
    return target


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    out = build_ico(root / "heartapp.ico")
    print(f"已生成 {out}（{out.stat().st_size:,} 字节，含 {len(SIZES)} 种尺寸）")
