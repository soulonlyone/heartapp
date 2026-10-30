"""命令行版 exe 的入口（PyInstaller 用）。

保持控制台模式，这样 ``list`` / ``probe`` / ``record`` / ``sessions`` / ``export``
的输出（包括故障排查提示）都能正常打印。
"""

from __future__ import annotations

import sys


def main() -> int:
    from heartapp.cli import main as cli_main

    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
