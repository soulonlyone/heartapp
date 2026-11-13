#!/usr/bin/env bash
# HeartApp 联机转发服务 · Linux 一键安装（systemd）
#
# 在仓库根目录执行（默认进入交互向导，密码隐藏输入）：
#   sudo bash server/install-linux.sh
#
# 自动部署（无人值守，密码走环境变量，不要写在命令行里）：
#   sudo HEARTAPP_INSTALL_PASSWORD='强密码' bash server/install-linux.sh --non-interactive
#
# 只想改端口等参数、仍要交互确认密码：
#   sudo bash server/install-linux.sh --port 9000 --max-clients 50
#
# 安装内容：
#   代码   /opt/heartapp-relay
#   配置   /etc/heartapp-relay.env（权限 600，含访问密码）
#   服务   systemd 单元 heartapp-relay
#
# 只复制服务端需要的 4 个文件，不需要在服务器上装 Qt / BLE。

set -euo pipefail

APP_DIR=/opt/heartapp-relay
SERVICE=heartapp-relay
ENV_FILE=/etc/heartapp-relay.env
RUN_USER=heartapp
PORT=8765
MAX_CLIENTS=20
ROOM_SIZE=4
BIND_HOST=127.0.0.1
OPEN_PORT=1
PASSWORD=""
PASSWORD_SET=0
CONFIG_ARGS=()

die() { echo "错误：$*" >&2; exit 1; }
log() { echo "[heartapp] $*"; }

usage() {
    cat <<'HELP'
HeartApp Linux 服务端安装器（systemd）
用法：sudo bash server/install-linux.sh [参数]

  --host ADDRESS       监听地址，默认 127.0.0.1；局域网直连才用 0.0.0.0
  --no-open-port       不改本机防火墙（默认会在非环回监听时自动放行端口）
  --port PORT          服务端口，默认 8765
  --max-clients COUNT  总连接上限，默认 20
  --room-size COUNT    每房间人数 1～128，默认 4
  --user NAME          系统运行用户，默认 heartapp
  --password VALUE     仅自动化用：命令行传密码会留在 shell 历史里，交互安装直接回车输入
  --non-interactive    自动部署，跳过交互确认；首次必须显式提供密码
  -h, --help           显示帮助，不安装、不需要 root

示例：sudo bash server/install-linux.sh --host 127.0.0.1 --max-clients 20
安装到 /opt/heartapp-relay，注册 heartapp-relay 服务并开机启动。
默认进入安装向导，隐藏输入并确认密码、选择端口、确认后安装。
重新执行时沿用现有配置。重启服务会清空临时房间。
安装后运行 sudo heartapp-server 修改密码、端口，或查看状态与日志。
运维：systemctl status heartapp-relay；journalctl -u heartapp-relay -n 50
说明：server/Linux说明.md。该帮助不会安装系统依赖，需预先准备 Python 3.9+ 和 venv。
HELP
}

while [ $# -gt 0 ]; do
    case "$1" in
        --port|--max-clients|--room-size|--host|--password|--user)
            [ $# -ge 2 ] || die "$1 缺少参数值（--help 查看用法）" ;;
    esac
    case "$1" in
        --port)         PORT="$2"; CONFIG_ARGS+=("--port" "$2"); shift 2 ;;
        --max-clients)  MAX_CLIENTS="$2"; CONFIG_ARGS+=("--max-clients" "$2"); shift 2 ;;
        --room-size)    ROOM_SIZE="$2"; CONFIG_ARGS+=("--room-size" "$2"); shift 2 ;;
        --host)         BIND_HOST="$2"; CONFIG_ARGS+=("--host" "$2"); shift 2 ;;
        --no-open-port) OPEN_PORT=0; shift ;;
        --password)     PASSWORD="$2"; PASSWORD_SET=1; shift 2 ;;
        --user)         RUN_USER="$2"; CONFIG_ARGS+=("--user" "$2"); shift 2 ;;
        --non-interactive) CONFIG_ARGS+=("--non-interactive"); shift ;;
        -h|--help)
            usage
            exit 0 ;;
        *) die "未知参数 $1（--help 查看用法）" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "需要 root 权限，请用 sudo 运行"

SRC_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
[ -f "$SRC_ROOT/heartapp/relay.py" ] || die "在 $SRC_ROOT 找不到 heartapp/relay.py，请在仓库里运行本脚本"

# 找一个可用的 Python 3.9+
PY=""
for candidate in python3 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
            PY="$candidate"; break
        fi
    fi
done
[ -n "$PY" ] || die "找不到 Python 3.9+，请先安装（Debian/Ubuntu: apt install python3 python3-venv）"

log "使用 $("$PY" --version 2>&1)"

# 向导从 /dev/tty 读取，支持 curl | sudo bash。取消后不复制代码或启动服务。
[ -f "$SRC_ROOT/server/manage-linux.py" ] || die "分发包缺少安装向导，请使用包含新向导的 Release"
if [ "$PASSWORD_SET" -eq 1 ]; then
    HEARTAPP_INSTALL_PASSWORD="$PASSWORD" "$PY" "$SRC_ROOT/server/manage-linux.py" configure "${CONFIG_ARGS[@]}"
else
    "$PY" "$SRC_ROOT/server/manage-linux.py" configure "${CONFIG_ARGS[@]}"
fi
PORT="$("$PY" "$SRC_ROOT/server/manage-linux.py" get --key port)"
RUN_USER="$("$PY" "$SRC_ROOT/server/manage-linux.py" get --key user)"
BIND_HOST="$("$PY" "$SRC_ROOT/server/manage-linux.py" get --key host)"
unset PASSWORD

# 运行用户（仅用于降低权限，本身不需要登录）
if ! id "$RUN_USER" >/dev/null 2>&1; then
    log "创建系统用户 $RUN_USER"
    useradd --system --no-create-home --shell /usr/sbin/nologin "$RUN_USER" 2>/dev/null \
        || die "创建用户失败，可用 --user 指定已有用户"
fi

log "安装到 $APP_DIR"
mkdir -p "$APP_DIR/heartapp"

# 先收紧所有权再执行目录里的任何程序：
# 旧版本把这个目录交给过服务用户，若先跑 $APP_DIR/venv/bin/pip，
# 等于用 root 执行一个服务用户可写（可能已被篡改）的脚本。
chown -R root:root "$APP_DIR"
chmod -R go-w "$APP_DIR"
install -m 644 "$SRC_ROOT/heartapp/__init__.py"      "$APP_DIR/heartapp/__init__.py"
install -m 644 "$SRC_ROOT/heartapp/room_protocol.py" "$APP_DIR/heartapp/room_protocol.py"
install -m 644 "$SRC_ROOT/heartapp/relay.py"         "$APP_DIR/heartapp/relay.py"
install -m 644 "$SRC_ROOT/heartapp/lobby.py"         "$APP_DIR/heartapp/lobby.py"
install -m 644 "$SRC_ROOT/server/requirements.txt"   "$APP_DIR/requirements.txt"

log "重建虚拟环境并安装依赖（只有 websockets，一般 1～2 分钟）"
# 每次安装都重建：升级时复用旧 venv 会执行上一个版本留下的解释器和 pip。
rm -rf "$APP_DIR/venv"
"$PY" -m venv "$APP_DIR/venv"
PIP="$APP_DIR/venv/bin/pip"
PIP_OPTS=(--quiet --disable-pip-version-check --no-input --timeout 20 --retries 1)
MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple

# 升级 pip 只是锦上添花：限时、失败也继续，绝不能让安装卡在这里。
"$PIP" install "${PIP_OPTS[@]}" --upgrade pip \
  || "$PIP" install "${PIP_OPTS[@]}" --upgrade pip -i "$MIRROR" \
  || log "pip 升级失败，继续使用 venv 自带的版本"

# 依赖只有 websockets：先试默认源（限时 20 秒），不行再走国内镜像。
log "安装依赖 websockets（默认源）"
"$PIP" install "${PIP_OPTS[@]}" -r "$APP_DIR/requirements.txt" \
  || {
    log "默认源不可达，改用国内镜像重试"
    "$PIP" install "${PIP_OPTS[@]}" --timeout 30 --retries 2 \
        -r "$APP_DIR/requirements.txt" -i "$MIRROR" \
      || die "依赖安装失败，可手动执行：$PIP install -r $APP_DIR/requirements.txt -i $MIRROR"
  }

# 安装目录归 root 所有（已在复制代码前收紧），服务用户只读、只执行。
# 管理脚本（sudo heartapp-server）会用这里的 Python 解释器执行；
# 如果目录归服务用户所有，服务被攻陷后可以替换解释器、等管理员执行时拿到 root。

# 管理器跟随服务端安装，终端菜单不依赖额外 Python 包。
install -m 644 "$SRC_ROOT/server/manage-linux.py" "$APP_DIR/manage-linux.py"
install -d -m 755 /usr/local/bin
cat > /usr/local/bin/heartapp-server <<'LAUNCHER'
#!/bin/sh
exec /opt/heartapp-relay/venv/bin/python /opt/heartapp-relay/manage-linux.py "$@"
LAUNCHER
chmod 755 /usr/local/bin/heartapp-server

PLAINTEXT_FLAG=""
case "$BIND_HOST" in
    127.0.0.1|localhost|::1|"[::1]") ;;
    *)
        PLAINTEXT_FLAG=" --allow-plaintext"
        log "注意：绑定 $BIND_HOST 且未启用 TLS，访问密码和心率会明文经过网络"
        log "      仅适合可信局域网；公网请配置 HTTPS 反代并改用 --host 127.0.0.1"
        ;;
esac

log "写入 systemd 服务 /etc/systemd/system/${SERVICE}.service"
cat > "/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=HeartApp relay (self-hosted heart-rate rooms)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_USER}
EnvironmentFile=${ENV_FILE}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python -m heartapp.relay${PLAINTEXT_FLAG} --host \${HEARTAPP_RELAY_HOST} --port \${HEARTAPP_RELAY_PORT} --max-clients \${HEARTAPP_RELAY_MAX_CLIENTS} --room-size \${HEARTAPP_RELAY_ROOM_SIZE}
Restart=on-failure
RestartSec=3

# 加固：服务端不需要写文件、不需要额外权限
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6
LockPersonality=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

sleep 1
log "检查服务状态"
if systemctl is-active --quiet "$SERVICE"; then
    log "服务已启动"
else
    echo
    journalctl -u "$SERVICE" -n 30 --no-pager || true
    die "服务没起来，看上面的日志"
fi

# 非环回监听时自动放行本机防火墙。云平台安全组在控制台，脚本无权修改。
if [ -n "$PLAINTEXT_FLAG" ] && [ "$OPEN_PORT" -eq 1 ]; then
    log "放行本机防火墙 TCP ${PORT}"
    FIREWALL_DONE=0
    if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
        firewall-cmd --permanent --add-port="${PORT}/tcp" >/dev/null 2>&1 \
            && firewall-cmd --reload >/dev/null 2>&1 \
            && { log "  已在 firewalld 放行 ${PORT}/tcp"; FIREWALL_DONE=1; }
    elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi "Status: active"; then
        ufw allow "${PORT}/tcp" >/dev/null 2>&1 \
            && { log "  已在 ufw 放行 ${PORT}/tcp"; FIREWALL_DONE=1; }
    fi
    if [ "$FIREWALL_DONE" -eq 0 ]; then
        log "  没检测到启用的 firewalld/ufw，请手动放行 TCP ${PORT}（或用 --no-open-port 跳过）"
    fi
fi

log "健康检查"
HEALTH_URL="http://127.0.0.1:${PORT}/healthz"
if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 3 "$HEALTH_URL"; then
    :
else
    log "健康检查未返回（若服务监听非本机地址属正常），可手动确认：curl ${HEALTH_URL}"
fi

cat <<EOF

============================================================
 安装完成
============================================================
 服务名    ${SERVICE}
 监听      ${BIND_HOST}:${PORT}
 代码      ${APP_DIR}
 配置      ${ENV_FILE}   （推荐通过管理菜单修改）
 运行用户  ${RUN_USER}

 管理菜单
   sudo heartapp-server

 常用命令
   systemctl status ${SERVICE}
   systemctl restart ${SERVICE}
   journalctl -u ${SERVICE} -f

 客户端连接
EOF
if [ -z "$PLAINTEXT_FLAG" ]; then
    printf '   本机使用当前监听地址和端口（默认 127.0.0.1:%s）。
' "$PORT"
    printf '   远程客户端请通过 TLS 反向代理连接，填写 wss://你的域名。
'
else
    printf '   可信局域网客户端填写 ws://服务器局域网IP:%s。
' "$PORT"
    printf '   公网访问还需在【云平台安全组】放行 TCP %s（这一步只能手动做）。
' "$PORT"
    printf '   注意：当前未加密，密码和心率明文传输；正式使用建议仅监听 127.0.0.1 + HTTPS 反代。
'
fi
cat <<EOF

 ⚠ 该服务不保存任何心率历史；房间、会话与令牌都在内存，重启即清空。
EOF
