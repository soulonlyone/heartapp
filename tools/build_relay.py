"""打包服务端分发包。

产出三个可直接分发的文件（dist/relay/）：

    HeartApp-Server-<版本>-win64.exe            Windows 独立服务端，不需要装 Python
    HeartApp-Server-<版本>-linux.tar.gz         Linux 源码包（含 install-linux.sh）
    HeartApp-Server-<版本>-docker.tar.gz        Docker 构建上下文（不用克隆仓库）
    SHA256SUMS.txt                            校验和

用法：
    .venv\\Scripts\\python.exe tools\\build_relay.py
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from heartapp import __version__ as VERSION  # noqa: E402

PYINSTALLER = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
OUT = DIST / "relay"

EXE_NAME = "HeartApp-Server.exe"

#: 服务端只需要这些文件；不要牵进 Qt / BLE / 数据库。
SERVER_FILES = ["__init__.py", "room_protocol.py", "relay.py", "lobby.py"]
SERVER_GUIDES = ["Windows面板说明.md", "Linux说明.md", "Docker说明.md", "nginx.conf.example"]

#: 服务端 exe 用不到的重量级模块，显式排掉以减小体积。
EXCLUDES = [
    "tkinter", "PySide6", "shiboken6", "bleak", "winrt",
    "matplotlib", "numpy", "pandas", "PIL", "pytest", "scipy", "IPython", "setuptools", "pip",
]


def run(args: list) -> int:
    print("  $ " + " ".join(str(a) for a in args), flush=True)
    return subprocess.run([str(a) for a in args], cwd=str(ROOT)).returncode


def build_windows_exe() -> Path:
    """用 PyInstaller 打一个不需要 Python 环境的服务端 exe。"""
    if not PYINSTALLER.exists():
        raise SystemExit("找不到 PyInstaller。先运行： .venv\\Scripts\\python.exe -m pip install pyinstaller")

    args = [
        PYINSTALLER,
        "--noconfirm", "--clean", "--onefile", "--console",
        "--name", "HeartApp-Server",
        "--runtime-tmpdir", ".",          # 与客户端一致：不在 %TEMP% 里解压
        "--paths", str(ROOT),
        "--distpath", str(DIST),
        "--specpath", str(BUILD),
    ]
    for name in EXCLUDES:
        args += ["--exclude-module", name]
    args.append(str(ROOT / "packaging" / "relay_entry.py"))

    print("\n[1/4] 打包 Windows 独立服务端 …")
    if run(args) != 0:
        raise SystemExit("PyInstaller 失败")
    exe = DIST / EXE_NAME
    if not exe.exists():
        raise SystemExit(f"没找到产物 {exe}")
    return exe


def sign_exe() -> None:
    """给 dist/ 下的 exe 打自签名（失败不阻断构建）。"""
    script = ROOT / "tools" / "sign_exe.ps1"
    if not script.exists():
        return
    print("\n[2/4] 给 exe 签名 …")
    shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    try:
        code = subprocess.run(
            [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=str(ROOT),
        ).returncode
    except Exception as exc:  # noqa: BLE001
        print(f"  跳过：{exc}")
        return
    if code != 0:
        print("  未签名（或证书未生效）。需要时双击 tools/bat/给exe签名.bat。")


def _add(tar: tarfile.TarFile, source: Path, arcname: str, mode: int = 0o644) -> None:
    info = tar.gettarinfo(str(source), arcname=arcname)
    info.mode = mode
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    with source.open("rb") as handle:
        tar.addfile(info, handle)
    print(f"    + {arcname}")


def build_linux_tarball() -> Path:
    """Linux 源码包：解压后 sudo bash server/install-linux.sh 即可。"""
    print("\n[3/4] 打 Linux 源码包 …")
    prefix = f"heartapp-server-{VERSION}"
    target = OUT / f"HeartApp-Server-v{VERSION}-linux.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        for name in SERVER_FILES:
            _add(tar, ROOT / "heartapp" / name, f"{prefix}/heartapp/{name}")
        _add(tar, ROOT / "server" / "requirements.txt", f"{prefix}/server/requirements.txt")
        _add(tar, ROOT / "server" / "install-linux.sh", f"{prefix}/server/install-linux.sh", 0o755)
        _add(tar, ROOT / "server" / "manage-linux.py", f"{prefix}/server/manage-linux.py")
        _add(tar, ROOT / "server" / "README.md", f"{prefix}/server/README.md")
        _add(tar, ROOT / "server" / ".env.example", f"{prefix}/server/.env.example")
        for name in SERVER_GUIDES:
            _add(tar, ROOT / "server" / name, f"{prefix}/server/{name}")
    return target


def build_docker_tarball() -> Path:
    """Docker 构建上下文：不用克隆仓库，docker build 就能用。"""
    print("\n[4/4] 打 Docker 构建包 …")
    prefix = f"heartapp-server-{VERSION}"
    target = OUT / f"HeartApp-Server-v{VERSION}-docker.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        for name in SERVER_FILES:
            _add(tar, ROOT / "heartapp" / name, f"{prefix}/heartapp/{name}")
        _add(tar, ROOT / "server" / "Dockerfile", f"{prefix}/server/Dockerfile")
        _add(tar, ROOT / "server" / "requirements.txt", f"{prefix}/server/requirements.txt")
        _add(tar, ROOT / "server" / "README.md", f"{prefix}/server/README.md")

        for name in SERVER_GUIDES:
            _add(tar, ROOT / "server" / name, f"{prefix}/server/{name}")

        dockerignore = ROOT / ".dockerignore"
        if dockerignore.exists():
            _add(tar, dockerignore, f"{prefix}/.dockerignore")

        helper = OUT / "build_and_run.sh"
        helper.write_bytes((
            "#!/usr/bin/env sh\n"
            "# 在本目录执行：sh build_and_run.sh [访问密码]\n"
            "set -e\n"
            'case "${1:-}" in\n'
            "  -h|--help)\n"
            "    cat <<'HELP'\n"
            "HeartApp Docker 分发包启动器\n"
            "用法：sh build_and_run.sh [访问密码]\n"
            "      sh build_and_run.sh --help\n"
            "建议通过环境变量 HEARTAPP_RELAY_PASSWORD 设置密码。\n"
            "构建后替换同名容器 heartapp-server；绑定 127.0.0.1:8765。\n"
            "资源限制：0.5 CPU / 384MiB；连接上限为核心默认 100，每房间 4 人。\n"
            "查看日志：docker logs --tail=50 heartapp-server\n"
            "停止服务：docker stop heartapp-server\n"
            "恢复服务：docker start heartapp-server\n"
            "完整配置与升级步骤见 server/Docker说明.md。\n"
            "显示帮助不需要 Docker，也不会构建或替换容器。\n"
            "HELP\n"
            "    exit 0 ;;\n"
            '  -*) echo "未知参数，请使用 --help；以 - 开头的密码请改用环境变量。" >&2; exit 2 ;;\n'
            "esac\n"
            '[ "$#" -le 1 ] || { echo "参数过多，请使用 --help。" >&2; exit 2; }\n'
            'PASSWORD="${1:-${HEARTAPP_RELAY_PASSWORD:-}}"\n'
            "docker build -f server/Dockerfile -t heartapp-server .\n"
            "docker rm -f heartapp-server >/dev/null 2>&1 || true\n"
            "docker run -d --name heartapp-server \\\n"
            "    -p 127.0.0.1:8765:8765 \\\n"
            '    -e "HEARTAPP_RELAY_PASSWORD=${PASSWORD}" \\\n'
            "    --restart unless-stopped \\\n"
            "    --read-only --cap-drop ALL --security-opt no-new-privileges:true \\\n"
            '    --memory 384m --cpus 0.5 \\\n'
            "    heartapp-server\n"
            'echo "已启动：curl http://127.0.0.1:8765/healthz"\n'
        ).encode("utf-8"))
        _add(tar, helper, f"{prefix}/build_and_run.sh", 0o755)
        helper.unlink()
    return target


def write_checksums(files: list[Path]) -> Path:
    target = OUT / "SHA256SUMS.txt"
    lines = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*"):
        if stale.is_file():
            stale.unlink()

    exe = build_windows_exe()
    sign_exe()

    exe_target = OUT / f"HeartApp-Server-v{VERSION}-win64.exe"
    shutil.move(str(exe), str(exe_target))

    linux = build_linux_tarball()
    docker = build_docker_tarball()
    sums = write_checksums([exe_target, linux, docker])

    print("\n" + "=" * 60)
    print(f"服务端分发包 v{VERSION}（{OUT.relative_to(ROOT)}）")
    print("=" * 60)
    for path in (exe_target, linux, docker, sums):
        print(f"  {path.name:<42} {path.stat().st_size / 1024 / 1024:6.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
