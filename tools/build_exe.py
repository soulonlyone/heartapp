"""把 heartapp 打包成免安装 exe。

    .venv\\Scripts\\python.exe tools\\build_exe.py

产物（都在 dist\\ 下）：

    心率记录仪.exe                 单文件版，图形界面，双击即用（方便拷贝）
    心率记录仪-命令行.exe          单文件版，命令行
    心率记录仪-便携版\\心率记录仪.exe   文件夹版，启动更快、更不容易被杀软误报

为什么还要有个「文件夹版」
--------------------------
单文件版每次启动都要把自己解压到临时目录。PyInstaller 出于安全考虑，会给这个
临时目录设置**独立权限（不继承父目录）**。在受限的沙箱环境里这会丢掉沙箱授予的
写权限，导致解压失败：

    [PYI-xxx:ERROR] Failed to extract VCRUNTIME140.dll: fopen: Permission denied

普通用户双击运行时不存在这个问题（解压目录只授权给当前用户，而当前用户正是启动者）。
但文件夹版压根不需要运行时解压，所以更稳、启动也快得多（实测 ~0.3s vs ~1.5s）。

刻意采用「先用 ASCII 名字打包、再用 Python 改名」：
把中文参数从 .bat 传给 PyInstaller 会受控制台代码页影响，容易变乱码；
Python 的路径 API 处理 Unicode 是可靠的。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYINSTALLER = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
ICON = ROOT / "heartapp.ico"

DIST = ROOT / "dist"
BUILD = ROOT / "build"

#: 单文件版的解压目录：`.` = exe 自己所在的目录（双击时工作目录就是它）。
#:
#: 默认行为是解压到系统临时目录（%TEMP%）。这在很多机器上会翻车：
#: 临时目录被安全软件/优化工具改坏、被沙箱工具阻断继承、被清理工具删掉……
#: 一旦建不了目录，exe 就报「Could not create temporary directory」直接退出，
#: 而且发生在 Python 代码运行之前，连日志都留不下。
#: 改成解压到 exe 旁边，就完全不依赖系统临时目录了。
RUNTIME_TMPDIR = "."

EXCLUDES = [
    "matplotlib", "numpy", "pandas", "PIL", "pytest",
    "scipy", "IPython", "notebook", "setuptools", "pip",
]

GUI_EXE = "心率记录仪.exe"
CLI_EXE = "心率记录仪-命令行.exe"
PORTABLE_DIR = "心率记录仪-便携版"


def _base() -> list:
    return [
        "--noconfirm",
        "--clean",
        "--icon", str(ICON),
        "--collect-all", "winrt",
        "--collect-all", "bleak",
        "--paths", str(ROOT),
        "--distpath", str(DIST),
        "--specpath", str(BUILD),
    ]


def _excludes() -> list:
    out: list = []
    for name in EXCLUDES:
        out += ["--exclude-module", name]
    return out


def run(args: list) -> int:
    printable = " ".join(str(a) for a in args)
    print("  $ " + printable, flush=True)
    # stdout/stderr 直接继承，不用管道（沙箱里管道会 EPERM，这里也想看实时日志）
    return subprocess.run(
        [str(a) for a in args], cwd=str(ROOT), stdout=None, stderr=None
    ).returncode


def _remove_tree(path: Path) -> None:
    """删目录。失败时给出人话提示，而不是丢一个 WinError 183 给用户。"""
    if not path.exists():
        return
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        raise SystemExit(
            f"\n删不掉：{path}\n原因：{exc}\n\n"
            "多半是程序还在运行（图形界面版会占住 data\\heartrate.db）。\n"
            "请先关掉「心率记录仪」窗口，再重新打包。"
        )


def _prepare_dist(groups: set) -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    if "onefile" in groups:
        for name in (GUI_EXE, CLI_EXE, "_internal", "HeartApp.exe", "HeartAppCLI.exe"):
            _remove_tree(DIST / name)
    if "onedir" in groups:
        for name in (PORTABLE_DIR, "HeartAppPortable", "HeartAppCLIPortable"):
            _remove_tree(DIST / name)


def _clean_runtime_data() -> None:
    """清掉打包/测试过程中产生的 data 目录，别把测试数据当成交付物发出去。"""
    for path in DIST.rglob("data"):
        if path.is_dir():
            _remove_tree(path)
            print(f"  清掉运行期数据目录 {path.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# 单文件版
# ---------------------------------------------------------------------------


def build_gui_onefile() -> int:
    print("\n[1/4] 单文件 图形界面版 …")
    return run(
        [PYINSTALLER] + _base() + _excludes() + [
            "--onefile",
            "--windowed",
            "--exclude-module", "tkinter",
            "--runtime-tmpdir", RUNTIME_TMPDIR,
            "--name", "HeartApp",
            "--add-data", f"{ICON};.",
            "--workpath", str(BUILD / "onefile_gui"),
            str(ROOT / "packaging" / "qt_entry.py"),
        ]
    )


def build_cli_onefile() -> int:
    print("\n[2/4] 单文件 命令行版 …")
    return run(
        [PYINSTALLER] + _base() + _excludes() + [
            "--onefile",
            "--console",
            "--runtime-tmpdir", RUNTIME_TMPDIR,
            "--name", "HeartAppCLI",
            # 命令行版不含任何图形界面
            "--exclude-module", "tkinter",
            "--exclude-module", "PySide6",
            "--exclude-module", "shiboken6",
            "--workpath", str(BUILD / "onefile_cli"),
            str(ROOT / "packaging" / "cli_entry.py"),
        ]
    )


# ---------------------------------------------------------------------------
# 文件夹版（不解压，最稳）
# ---------------------------------------------------------------------------


def build_gui_onedir() -> int:
    print("\n[3/4] 文件夹 图形界面版 …")
    return run(
        [PYINSTALLER] + _base() + _excludes() + [
            "--onedir",
            "--windowed",
            "--exclude-module", "tkinter",
            "--name", "HeartAppPortable",
            "--add-data", f"{ICON};.",
            "--workpath", str(BUILD / "onedir_gui"),
            str(ROOT / "packaging" / "qt_entry.py"),
        ]
    )


def build_cli_onedir() -> int:
    print("\n[4/4] 文件夹 命令行版 …")
    return run(
        [PYINSTALLER] + _base() + _excludes() + [
            "--onedir",
            "--console",
            "--name", "HeartAppCLIPortable",
            "--exclude-module", "tkinter",
            "--exclude-module", "PySide6",
            "--exclude-module", "shiboken6",
            "--workpath", str(BUILD / "onedir_cli"),
            str(ROOT / "packaging" / "cli_entry.py"),
        ]
    )


# ---------------------------------------------------------------------------
# 重命名
# ---------------------------------------------------------------------------


def _rename_file(src: Path, dst: Path) -> bool:
    if not src.exists():
        print(f"  [x] 没找到 {src}")
        return False
    if dst.exists():
        dst.unlink() if dst.is_file() else shutil.rmtree(dst, ignore_errors=True)
    src.rename(dst)
    print(f"  {src.name}  ->  {dst.name}")
    return True


def rename_onefile() -> bool:
    print("\n重命名单文件版 …")
    return _rename_file(DIST / "HeartApp.exe", DIST / GUI_EXE) and _rename_file(
        DIST / "HeartAppCLI.exe", DIST / CLI_EXE
    )


def rename_onedir() -> bool:
    print("\n重命名文件夹版 …")
    gui_src = DIST / "HeartAppPortable"
    cli_src = DIST / "HeartAppCLIPortable"
    if not gui_src.is_dir() or not cli_src.is_dir():
        print("  [x] 没有找到 onedir 产物目录")
        return False

    # 先把两个 exe 各自改成中文名，再把目录合并成一个便携版文件夹
    _rename_file(gui_src / "HeartAppPortable.exe", gui_src / GUI_EXE)
    _rename_file(cli_src / "HeartAppCLIPortable.exe", cli_src / CLI_EXE)

    target = DIST / PORTABLE_DIR
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)

    # 两个 exe 共享同一份 _internal 是不行的（打包内容不同），所以各自一个子目录
    shutil.move(str(gui_src), str(target / "图形界面版"))
    shutil.move(str(cli_src), str(target / "命令行版"))
    print(f"  合并为 {target.name}\\图形界面版\\{GUI_EXE}")
    print(f"          {target.name}\\命令行版\\{CLI_EXE}")
    return True


def report() -> None:
    print("\n" + "=" * 64)
    print("产物：")
    for path in sorted(DIST.rglob("*")):
        if path.is_file() and path.suffix.lower() in (".exe",):
            print(f"  {path.relative_to(ROOT)}   {path.stat().st_size / 1048576:.1f} MB")
    print("=" * 64)


def sign_outputs() -> None:
    """给打包好的 exe 打自签名代码签名。

    签名必须放在**重命名之后**，因为签名会改动文件内容；
    失败不阻断构建（没装证书的机器上也应该能正常打包）。
    """
    script = ROOT / "tools" / "sign_exe.ps1"
    if not script.exists():
        return
    print("\n给 exe 签名 …")
    shell = "powershell"
    try:
        code = subprocess.run(
            [shell, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script), "-Quiet"],
            cwd=str(ROOT),
        ).returncode
    except Exception as exc:  # noqa: BLE001
        print(f"  跳过：{exc}")
        return
    if code != 0:
        print("  未签名（或证书未生效）。需要时双击「给exe签名.bat」处理。")


def main() -> int:
    if not PYINSTALLER.exists():
        print("找不到 PyInstaller。先运行：")
        print(r"  .venv\Scripts\python.exe -m pip install pyinstaller")
        return 1

    # 可以不传参数打全部，也可以只打其中一类：
    #   tools\build_exe.py onefile     只打单文件版
    #   tools\build_exe.py onedir      只打文件夹版
    wanted = {a.lower() for a in sys.argv[1:]} or {"onefile", "onedir"}
    unknown = wanted - {"onefile", "onedir"}
    if unknown:
        print(f"未知参数：{', '.join(sorted(unknown))}（只支持 onefile / onedir）")
        return 2

    _prepare_dist(wanted)

    print("生成图标 …")
    if run([VENV_PY, ROOT / "tools" / "make_icon.py"]) != 0:
        return 1

    steps = [
        ("单文件 图形界面版", "onefile", build_gui_onefile),
        ("单文件 命令行版", "onefile", build_cli_onefile),
        ("文件夹 图形界面版", "onedir", build_gui_onedir),
        ("文件夹 命令行版", "onedir", build_cli_onedir),
    ]
    for label, group, fn in steps:
        if group not in wanted:
            print(f"\n跳过 {label}（未选中 {group}）")
            continue
        if fn() != 0:
            print(f"\n{label} 打包失败。")
            return 1

    if "onefile" in wanted and not rename_onefile():
        return 1
    if "onedir" in wanted and not rename_onedir():
        return 1

    print("\n清理运行期数据 …")
    _clean_runtime_data()

    sign_outputs()

    report()
    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
