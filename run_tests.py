"""一次跑完所有测试：python run_tests.py"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(ROOT / "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print()
    print("=" * 60)
    print(f"共 {result.testsRun} 个测试："
          f"失败 {len(result.failures)}，错误 {len(result.errors)}，"
          f"跳过 {len(result.skipped)}")
    if result.wasSuccessful():
        print("全部通过 ✓")
        return 0
    print("存在失败 ✗")
    return 1


if __name__ == "__main__":
    sys.exit(main())
