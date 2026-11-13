"""一次性打出完整发布包：客户端 + 服务端 + 服务端面板。

一个版本只发**一个 Release**，所有产物放在一起，不再按客户端/服务端拆开。

用法：
    .venv\\Scripts\\python.exe tools\\build_release.py

产物在 dist\\release\\，全部 ASCII 文件名，可直接：

    gh release create v<版本> --title "HeartApp v<版本> · ..." \\
        --notes-file docs/release-notes/v<版本>.md dist\\release\\*

流程：三个构建脚本各自打包 → 统一签名 → 逐个复制/压缩成发布件 → 生成校验和。
**签名必须在压缩之前**，否则压缩包里是未签名的 exe。
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from heartapp import __version__ as VERSION  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import build_exe  # noqa: E402
import build_relay  # noqa: E402
import build_server_panel  # noqa: E402

DIST = ROOT / "dist"
OUT = DIST / "release"

V = VERSION

#: 发布件的 ASCII 名称。中文文件名经 gh 上传会被截断。
ARTIFACTS = {
    "client_exe":     f"HeartApp-v{V}-win64.exe",
    "client_cli":     f"HeartApp-v{V}-win64-cli.exe",
    "client_zip":     f"HeartApp-v{V}-win64-portable.zip",
    "server_exe":     f"HeartApp-Server-v{V}-win64.exe",
    "server_linux":   f"HeartApp-Server-v{V}-linux.tar.gz",
    "server_docker":  f"HeartApp-Server-v{V}-docker.tar.gz",
    "panel_zip":      f"HeartApp-Server-Panel-v{V}-win64.zip",
}


def step(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}", flush=True)


def sign_all() -> None:
    """统一签名。必须在压缩成 zip / tar 之前执行。"""
    script = ROOT / "tools" / "sign_exe.ps1"
    if not script.exists():
        print("  跳过：没有 tools/sign_exe.ps1")
        return
    # 优先 pwsh：Windows PowerShell 5.1 解析该脚本会 ParserError。
    shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    print(f"  签名工具：{shell}", flush=True)
    code = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=str(ROOT),
    ).returncode
    if code != 0:
        print("  ⚠ 签名脚本返回非 0，产物可能未签名")


def _zip_dir(source: Path, target: Path) -> None:
    if target.exists():
        target.unlink()
    shutil.make_archive(str(target.with_suffix("")), "zip", root_dir=str(source))


def assemble() -> list:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    produced: list = []

    print("  客户端单文件版 / 命令行版 …")
    for src, key in ((DIST / "心率记录仪.exe", "client_exe"),
                     (DIST / "心率记录仪-命令行.exe", "client_cli")):
        dst = OUT / ARTIFACTS[key]
        shutil.copy2(src, dst)
        produced.append(dst)

    print("  客户端便携版（压缩）…")
    _zip_dir(DIST / "心率记录仪-便携版", OUT / ARTIFACTS["client_zip"])
    produced.append(OUT / ARTIFACTS["client_zip"])

    print("  服务端三个包 …")
    for key, name in (("server_exe", f"HeartApp-Server-v{V}-win64.exe"),
                      ("server_linux", f"HeartApp-Server-v{V}-linux.tar.gz"),
                      ("server_docker", f"HeartApp-Server-v{V}-docker.tar.gz")):
        src = DIST / "relay" / name
        dst = OUT / ARTIFACTS[key]
        shutil.copy2(src, dst)
        produced.append(dst)

    print("  服务端面板（压缩）…")
    _zip_dir(DIST / "HeartApp-Server-Panel", OUT / ARTIFACTS["panel_zip"])
    produced.append(OUT / ARTIFACTS["panel_zip"])

    return produced


def checksums(files: list) -> Path:
    lines = []
    for path in sorted(files, key=lambda p: p.name):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    target = OUT / "SHA256SUMS.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    step(f"[1/5] 客户端 exe（版本 {V}）")
    if build_exe.main() != 0:
        return 1

    step("[2/5] 服务端分发包")
    if build_relay.main() != 0:
        return 1

    step("[3/5] 服务端面板")
    if build_server_panel.main() != 0:
        return 1

    step("[4/5] 统一签名")
    sign_all()

    step("[5/5] 组装发布件")
    files = assemble()
    sums = checksums(files)

    # deploy.sh 在 api.github.com 不可达时用它解析版本号，随发布一起提交。
    latest = ROOT / "server" / "LATEST"
    latest.write_text(f"v{V}\n", encoding="utf-8")
    print(f"  已更新 {latest.relative_to(ROOT)} -> v{V}")

    # 校验和也进仓库：镜像站常常拿不到 Release 里的 SHA256SUMS.txt，
    # deploy.sh 会退回这里取（与脚本同源，走 raw.githubusercontent）。
    checksums = ROOT / "server" / "checksums" / f"v{V}.txt"
    checksums.parent.mkdir(parents=True, exist_ok=True)
    checksums.write_text(sums.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"  已更新 {checksums.relative_to(ROOT)}")

    print("\n" + "=" * 60)
    print(f"发布包 v{V} —— 一个 Release 装全部（{OUT.relative_to(ROOT)}）")
    print("=" * 60)
    for path in sorted(files, key=lambda p: p.name):
        print(f"  {path.name:<46} {path.stat().st_size / 1024 / 1024:6.1f} MB")
    print(f"  {'SHA256SUMS.txt':<46} {sums.stat().st_size / 1024:6.1f} KB")

    print("\n发布命令：")
    print(f'  gh release create v{V} --title "HeartApp v{V} · ..." \\')
    print(f'      --notes-file "docs/release-notes/v{V}.md" dist\\release\\*')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
