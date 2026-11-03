"""文本编码相关的防回归测试。

背景：Windows 上 `strftime` 会把整个格式串按系统 ANSI 代码页编码。
把中文写进格式串（例如 strftime("游戏记录 %m-%d"))，在英文区域（cp1252）
的机器上会抛 UnicodeEncodeError，直接让界面崩溃。CI 抓过一次。
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = [ROOT / "heartapp", ROOT / "tools", ROOT / "packaging"]


class TestNoNonAsciiStrftime(unittest.TestCase):
    def test_format_strings_are_ascii(self):
        """strftime 的格式串里不允许出现非 ASCII 字符。"""
        offenders = []
        for base in SOURCE_DIRS:
            if not base.exists():
                continue
            for path in base.rglob("*.py"):
                for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if "strftime(" not in line:
                        continue
                    # 只看同一行里 strftime 之后的部分
                    tail = line.split("strftime(", 1)[1]
                    if any(ord(ch) > 127 for ch in tail.split(")")[0]):
                        offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "strftime 格式串含非 ASCII 字符：\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
