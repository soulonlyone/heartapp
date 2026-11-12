"""Qt 版图形界面 exe 的入口（PyInstaller 用）。

``--windowed`` 打包后没有控制台，启动期异常会静默消失，
所以这里必须把堆栈写进日志并弹窗告诉用户日志在哪。
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path
from typing import Optional


def _write_crash_log(exc: BaseException) -> Optional[Path]:
    from heartapp.paths import data_dir

    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    text = (
        "心率记录仪 启动失败（Qt 界面）\n"
        f"Python: {sys.version}\n"
        f"可执行文件: {sys.executable}\n"
        f"frozen: {getattr(sys, 'frozen', False)}\n"
        + "-" * 60
        + "\n"
        + text
    )
    try:
        target = data_dir() / "启动错误.log"
        target.write_text(text, encoding="utf-8")
        return target
    except OSError:
        try:
            with tempfile.NamedTemporaryFile(
                prefix="HeartApp-", suffix=".log", mode="w", encoding="utf-8", delete=False
            ) as log:
                log.write(text)
                return Path(log.name)
        except OSError:
            return None


def _report(exc: BaseException) -> None:
    log = _write_crash_log(exc)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication([])
        detail = f"完整信息已写入：\n{log}" if log else "无法写入启动日志。"
        QMessageBox.critical(
            None,
            "心率记录仪 启动失败",
            f"{type(exc).__name__}: {exc}\n\n{detail}",
        )
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    try:
        from heartapp.ui.qt.app import main as gui_main

        return gui_main(sys.argv[1:])
    except SystemExit as exc:
        return int(exc.code or 0)
    except BaseException as exc:  # noqa: BLE001
        _report(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
