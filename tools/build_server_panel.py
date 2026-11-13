"""打包独立 Windows 服务端面板文件夹版，不需要同目录另放转发 exe。"""
from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

# 版本资源：没有它的 exe 在杀毒软件眼里是匿名文件，容易误报（实现见 build_exe.py）
sys.path.insert(0, str(ROOT / "tools"))
from build_exe import _version_file  # noqa: E402


def main() -> int:
    version_file = _version_file(
        "HeartApp-Server-Panel", "HeartApp 服务端面板", "HeartApp-Server-Panel.exe"
    )
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--noupx", "--onedir", "--windowed",
            "--version-file", str(version_file),
            "--name", "HeartApp-Server-Panel", "--paths", str(ROOT),
            "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build/server-panel"),
            "--specpath", str(ROOT / "build")]
    for name in ("tkinter", "bleak", "winrt", "numpy", "matplotlib", "PIL",
                 "heartapp.engine", "heartapp.store", "heartapp.ble", "heartapp.ui.qt.app"):
        args.extend(["--exclude-module", name])
    args.append(str(ROOT / "packaging/server_panel_entry.py"))
    # 其他应用放进 PATH 的 ICU 等 DLL 可能与 Qt 的 Windows 系统依赖同名。
    # 只收窄构建子进程的 PATH，不修改用户环境，避免打入不兼容的第三方 DLL。
    environment = dict(os.environ)
    windows = Path(environment.get("SystemRoot", "C:/Windows"))
    environment["PATH"] = os.pathsep.join(str(path) for path in (
        Path(sys.executable).parent, Path(sys.base_prefix), windows / "System32", windows,
    ))
    result = subprocess.run(args, cwd=ROOT, env=environment)
    if result.returncode:
        return result.returncode
    target = ROOT / "dist/HeartApp-Server-Panel"
    (target / "README.md").write_text(
        "# HeartApp Windows 服务端面板\n\n"
        "双击 HeartApp-Server-Panel.exe，不需要安装 Python。请保留同目录的 _internal 文件夹。\n\n"
        "设置监听范围、端口和密码，点击启动，再把局域网地址和密码分别告知朋友。\n"
        "本机运行成功不代表公网可达，公网仍需配置证书、反代和网络放行。\n\n"
        "关闭窗口会收起到托盘；右键托盘或点击退出面板可停止服务并退出。\n"
        "设置保存在 data/server-panel.json；密码不保存，每次重开会重新生成。\n\n"
        "这是本地开发测试构建，发布签名前请完成目标机器验收。\n"
        "完整源码及三端说明：https://github.com/soulonlyone/heartapp\n",
        encoding="utf-8",
    )
    print(f"面板文件夹版：{target}")
    print("分发时保留整个目录；签名可使用 tools/sign_exe.ps1。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
