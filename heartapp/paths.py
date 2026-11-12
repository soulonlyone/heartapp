"""解析程序位置、只读资源和用户数据路径。"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Optional


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """程序所在目录。

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


PORTABLE_MARKER = "heartapp-portable"


def uses_profile_data() -> bool:
    return is_frozen() and not (app_dir() / PORTABLE_MARKER).is_file()


def data_dir() -> Path:
    """普通 EXE 使用用户目录，便携版和源码使用程序旁的 data。"""
    if uses_profile_data():
        local = os.environ.get("LOCALAPPDATA")
        target = Path(local) / "HeartApp" if local else Path.home() / "HeartApp"
    else:
        target = app_dir() / "data"
    target.mkdir(parents=True, exist_ok=True)
    return target


def legacy_db_path(target: Path) -> Optional[Path]:
    if uses_profile_data():
        for source in (app_dir() / "data/heartrate.db", Path.home() / "HeartApp/heartrate.db"):
            if source.is_file() and source.resolve() != target.resolve():
                return source
    return None


def migrate_database(source: Path, target: Path) -> None:
    """复制并校验 SQLite 快照，包含 WAL 中已提交的记录。"""
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".migration-") as tmp:
        snapshot = Path(tmp) / target.name
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as old:
            with closing(sqlite3.connect(snapshot)) as new:
                old.backup(new)
                if new.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise sqlite3.DatabaseError("旧数据库完整性校验失败")
        # 目标可能已被另一实例创建。只在目标不存在时发布快照。
        if os.name == "nt":
            snapshot.rename(target)
        else:
            os.link(snapshot, target)


def default_db_path() -> Path:
    return data_dir() / "heartrate.db"


def cli_db_path() -> Path:
    target = default_db_path()
    if not target.exists():
        source = legacy_db_path(target)
        if source is not None:
            try:
                migrate_database(source, target)
            except FileExistsError:
                if not target.is_file():
                    raise
    return target


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
