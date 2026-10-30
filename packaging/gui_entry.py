"""图形界面版 exe 的入口（PyInstaller 用）。

GUI 版打包时用的是 ``--windowed``（无控制台），这意味着**任何启动期异常都会
静默消失，用户只看到窗口闪一下就没了**。所以这里必须兜底：把完整堆栈写进
日志文件，并弹一个对话框告诉用户日志在哪。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _write_crash_log(exc: BaseException) -> Path:
    try:
        from heartapp.paths import app_dir

        target = app_dir() / "启动错误.log"
    except Exception:  # noqa: BLE001
        target = Path("heartapp_crash.log")

    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    text = (
        "心率记录仪 启动失败\n"
        f"Python: {sys.version}\n"
        f"可执行文件: {sys.executable}\n"
        f"frozen: {getattr(sys, 'frozen', False)}\n"
        + "-" * 60
        + "\n"
        + text
    )
    try:
        target.write_text(text, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return target


def _report(exc: BaseException) -> None:
    log = _write_crash_log(exc)
    try:
        import tkinter.messagebox as messagebox

        messagebox.showerror(
            "心率记录仪 启动失败",
            f"{type(exc).__name__}: {exc}\n\n完整信息已写入：\n{log}",
        )
    except Exception:  # noqa: BLE001
        pass


SUBCOMMANDS = {"list", "probe", "record", "sessions", "export"}


def _redirect_subcommand() -> bool:
    """图形版没有控制台，跑命令行子命令只会一闪而过。给出明确指引。"""
    args = sys.argv[1:]
    if not args or args[0] not in SUBCOMMANDS:
        return False
    try:
        import tkinter.messagebox as messagebox

        messagebox.showinfo(
            "心率记录仪",
            "这是图形界面版，没有命令行输出。\n\n"
            "请改用旁边的「心率记录仪-命令行.exe」，例如：\n\n"
            f"  心率记录仪-命令行.exe {' '.join(args)}",
        )
    except Exception:  # noqa: BLE001
        pass
    return True


def main() -> int:
    if _redirect_subcommand():
        return 0
    try:
        from heartapp.ui.app import main as gui_main

        return gui_main(sys.argv[1:])
    except SystemExit as exc:  # argparse 的正常退出（例如 --help）
        return int(exc.code or 0)
    except BaseException as exc:  # noqa: BLE001
        _report(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
