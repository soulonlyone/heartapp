#!/usr/bin/env bash
# HeartApp 服务端 · 一行安装（Linux / systemd）
#
#   curl -fsSL https://raw.githubusercontent.com/soulonlyone/heartapp/main/server/deploy.sh | sudo bash
#
# 建议先看清楚再执行（推荐）：
#
#   curl -fsSLO https://raw.githubusercontent.com/soulonlyone/heartapp/main/server/deploy.sh
#   less deploy.sh
#   sudo bash deploy.sh
#
# 国内直连 GitHub 超时时，加镜像前缀（需同时指定版本）：
#
#   bash deploy.sh --version v1.4.0 --mirror https://ghfast.top/
#
# 参数（全部可选）：
#   默认进入交互向导：隐藏输入密码 → 选择端口 → 确认安装
#   --password <口令>    自动部署时预设访问密码
#   --non-interactive    自动部署，首次必须显式提供密码
#   --port <端口>        默认 8765
#   --max-clients <N>    默认 20
#   --room-size <N>      默认 4
#   --version <标签>     默认取最新 Release，例如 v1.2.0
#   --dry-run            只下载 / 校验 / 解压并打印下一步命令，不真正安装
#
# 环境变量：
#   HEARTAPP_BASE_URL    覆盖下载地址前缀（测试或镜像站用）
#
# 流程：下载 Release 里的服务端压缩包 → 校验 SHA256 → 解压 → 运行其中的 install-linux.sh

set -euo pipefail

REPO="soulonlyone/heartapp"
VERSION=""
MIRROR=""
DRY_RUN=0
NO_VERIFY=0
ARGS=()

say() { printf '[heartapp] %s\n' "$*"; }
die() { printf '错误：%s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --version) [ $# -ge 2 ] || die "--version 需要一个值"; VERSION="$2"; shift 2 ;;
        --mirror)  [ $# -ge 2 ] || die "--mirror 需要一个前缀"; MIRROR="$2"; shift 2 ;;
        --no-verify) NO_VERIFY=1; shift ;;
        --non-interactive) ARGS+=("$1"); shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --password|--port|--max-clients|--room-size|--host|--user)
            [ $# -ge 2 ] || die "$1 需要一个值"
            ARGS+=("$1" "$2"); shift 2 ;;
        -h|--help)
            cat <<'HELP'
HeartApp Linux 一行部署
用法：curl -fsSL https://raw.githubusercontent.com/soulonlyone/heartapp/main/server/deploy.sh | sudo bash
默认在控制终端进入安装向导，按提示设置密码和端口并确认安装。
--version TAG 指定 Release；--dry-run 只下载解压；--non-interactive 用于自动部署。
--mirror 前缀 强制只用某个镜像，如 https://ghfast.top/（默认已自动回退，一般不用给）。
--no-verify 取不到 SHA256SUMS 时仍然继续安装（默认会中止）。
可传 --password、--port、--host、--max-clients、--room-size、--user。
安装后运行 sudo heartapp-server 打开管理菜单。
HELP
            exit 0 ;;
        *) die "未知参数 $1（--help 查看用法）" ;;
    esac
done

# ---- 依赖检查 ----
for cmd in curl tar; do
    command -v "$cmd" >/dev/null 2>&1 || die "缺少 $cmd，请先安装（Debian/Ubuntu: apt install curl tar）"
done
SHA_TOOL=""
for candidate in sha256sum shasum; do
    command -v "$candidate" >/dev/null 2>&1 && { SHA_TOOL="$candidate"; break; }
done

# 直连不通时自动尝试的 GitHub 镜像；用 --mirror 可指定单一前缀。
MIRRORS=("https://ghfast.top" "https://gh-proxy.com" "https://ghproxy.net")

fetch() {
    # fetch <url> <输出文件> [quiet]
    # 直连失败按 MIRRORS 顺序重试；给了 --mirror 就只用它。
    local url="$1" out="$2" quiet="${3:-}" prefix target
    local -a prefixes
    if [ -n "$MIRROR" ]; then
        prefixes=("${MIRROR%/}")
    else
        prefixes=("" "${MIRRORS[@]}")
    fi
    for prefix in "${prefixes[@]}"; do
        if [ -z "$prefix" ]; then
            target="$url"
        else
            target="${prefix%/}/$url"
        fi
        if curl -fsSL --retry 1 --connect-timeout 10 --max-time 300 -o "$out" "$target" 2>/dev/null; then
            [ -z "$quiet" ] && [ -n "$prefix" ] && say "  经镜像 $prefix 下载成功"
            return 0
        fi
        [ -z "$quiet" ] && [ -z "$prefix" ] && say "  直连失败，尝试镜像 …"
    done
    return 1
}

# ---- 确定版本 ----

if [ -z "$VERSION" ]; then
    say "查询最新 Release …"
    TAG="$(curl -fsSL --connect-timeout 10 --max-time 20 "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null \
        | grep -o '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 \
        | sed 's/.*"\([^"]*\)"$/\1/')" || true
    if [ -z "${TAG:-}" ]; then
        say "  api.github.com 不可达，改从仓库读取版本号"
        TAG="$(curl -fsSL --connect-timeout 10 --max-time 20 \
            "https://raw.githubusercontent.com/${REPO}/main/server/LATEST" 2>/dev/null \
            | tr -d '[:space:]')" || true
    fi
    [ -n "${TAG:-}" ] || die "查不到最新版本，请用 --version 指定，例如 --version v1.4.0"
else
    TAG="$VERSION"
fi
say "目标版本：$TAG"

ASSET="HeartApp-Server-${TAG}-linux.tar.gz"
BASE="${HEARTAPP_BASE_URL:-https://github.com/${REPO}/releases/download/${TAG}}"
[ -n "$MIRROR" ] && BASE="${MIRROR%/}/$BASE"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

say "下载 $ASSET"
fetch "$BASE/$ASSET" "$TMP/$ASSET" || die "下载失败：直连和镜像都没通（不是版本问题）。可选：
  1) 手动指定镜像：--mirror https://ghfast.top/
  2) 在能上 GitHub 的电脑下载 ${ASSET}，上传到服务器后按 server/README.md 手动安装
  3) 换一台能访问 GitHub 的机器运行服务端（只需 Python 3.9+ 和 websockets）"

# ---- 校验和 ----
EXPECTED=""
if fetch "$BASE/SHA256SUMS.txt" "$TMP/SHA256SUMS.txt" quiet; then
    # sha256sum 在不同平台输出 "hash  file" 或 "hash *file"（二进制标记），两种都要认。
    # 用 `|| true` 兜住：匹配不到时不能让 set -e 把整个脚本杀掉。
    EXPECTED="$(awk -v f="$ASSET" '$2 == f || $2 == "*" f { print $1; exit }' \
        "$TMP/SHA256SUMS.txt" 2>/dev/null || true)"
fi
if [ -n "$EXPECTED" ] && [ -n "$SHA_TOOL" ]; then
    if [ "$SHA_TOOL" = "sha256sum" ]; then
        ACTUAL="$(sha256sum "$TMP/$ASSET" | awk '{print $1}')"
    else
        ACTUAL="$(shasum -a 256 "$TMP/$ASSET" | awk '{print $1}')"
    fi
    [ "$EXPECTED" = "$ACTUAL" ] || die "SHA256 校验失败，文件可能损坏或被篡改"
    say "SHA256 校验通过"
elif [ -z "${EXPECTED:-}" ]; then
    # 校验和是唯一能确认「下载到的包没被镜像篡改」的依据，缺了就默认不装。
    if [ "$NO_VERIFY" -eq 1 ]; then
        say "警告：没取到 ${ASSET} 的校验和，按 --no-verify 继续安装"
    else
        die "没取到 ${ASSET} 的校验和，无法确认安装包完整。可任选其一：
  1) 换镜像重试：--mirror https://gh-proxy.com/
  2) 确认来源可信后跳过校验：--no-verify
  3) 在能上 GitHub 的电脑下载压缩包和 SHA256SUMS.txt，自行核对后手动安装"
    fi
elif [ -z "$SHA_TOOL" ]; then
    say "跳过 SHA256 校验：系统没有 sha256sum / shasum"
else
    say "跳过 SHA256 校验"
fi

# ---- 解压 ----
say "解压"
tar -xzf "$TMP/$ASSET" -C "$TMP" || die "解压失败"
SRC="$(find "$TMP" -maxdepth 2 -type d -name 'heartapp-server-*' | head -1)"
[ -n "$SRC" ] || die "压缩包结构异常：找不到 heartapp-server-* 目录"
[ -f "$SRC/server/install-linux.sh" ] || die "压缩包里没有 server/install-linux.sh"
[ -f "$SRC/server/manage-linux.py" ] || die "该 Release 尚未包含交互向导，请指定新版 Linux 服务端 Release"

if [ "$DRY_RUN" -eq 1 ]; then
    say "dry-run：下载与校验通过，未执行安装"
    say "实际会运行："
    printf '  cd %q\n  bash server/install-linux.sh' "$SRC"
    for a in ${ARGS[@]+"${ARGS[@]}"}; do printf ' %q' "$a"; done
    printf '\n'
    # 保留解压内容方便检查
    trap - EXIT
    say "解压内容保留在：$SRC"
    exit 0
fi

# ---- 安装 ----
say "开始安装（复制代码到 /opt/heartapp-relay 并注册 systemd 服务）"
cd "$SRC"
# shellcheck disable=SC2068
bash server/install-linux.sh ${ARGS[@]+"${ARGS[@]}"}
