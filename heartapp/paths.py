"""路径工具：数据库、导出文件、只读资源分别放在哪里。

打包成 exe 之后有两个完全不同的目录，混用会导致**用户数据丢失**：

* ``app_dir()``      —— 用户的**数据**目录。打包后必须落在 exe 所在目录，
  因为 ``__file__`` 指向的是 PyInstaller 的临时解包目录（``_MEIPASS``），
  程序一退出就被删掉，心率记录会跟着一起没。
* ``resource_dir()`` —— 只读**资源**目录（图标等）。打包后就是临时解包目录。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """程序所在目录（数据就放它旁边）。

    * 源码运行：``heartapp`` 包的上一级
    * exe 运行：**exe 文件所在目录**
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """只读资源目录（图标等）。exe 下是临时解包目录。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """数据目录。默认放在程序目录下的 ``data``，方便直接找到、直接备份。"""
    target = app_dir() / "data"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 程序目录不可写（例如装在 Program Files、或 U 盘只读）时退回用户目录
        fallback = Path.home() / "HeartApp"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    return target


def default_db_path() -> Path:
    return data_dir() / "heartrate.db"


def default_export_path(session_id: int) -> Path:
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return data_dir() / f"heartrate_session{session_id}_{stamp}.csv"


def icon_path() -> Path:
    """窗口/程序图标。找不到就返回一个不存在的路径，由调用方兜底。"""
    return resource_dir() / "heartapp.ico"


def cli_hint() -> str:
    """该怎么写命令行。

    exe 运行时提示用户「python -m heartapp」是没有意义的 —— 他机器上没装 Python。
    """
    return "心率记录仪-命令行.exe" if is_frozen() else "python -m heartapp"
