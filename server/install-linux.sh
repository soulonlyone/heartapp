#!/usr/bin/env bash
# HeartApp 联机转发服务 · Linux 一键安装（systemd）
#
# 在仓库根目录执行：
#   sudo bash server/install-linux.sh
#   sudo bash server/install-linux.sh --port 9000 --max-clients 50
#   sudo bash server/install-linux.sh --password '换成你的口令'
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
BIND_HOST=0.0.0.0
PASSWORD=""

die() { echo "错误：$*" >&2; exit 1; }
log() { echo "[heartapp] $*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --port)         PORT="$2"; shift 2 ;;
        --max-clients)  MAX_CLIENTS="$2"; shift 2 ;;
        --room-size)    ROOM_SIZE="$2"; shift 2 ;;
        --host)         BIND_HOST="$2"; shift 2 ;;
        --password)     PASSWORD="$2"; shift 2 ;;
        --user)         RUN_USER="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
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

# 运行用户（仅用于降低权限，本身不需要登录）
if ! id "$RUN_USER" >/dev/null 2>&1; then
    log "创建系统用户 $RUN_USER"
    useradd --system --no-create-home --shell /usr/sbin/nologin "$RUN_USER" 2>/dev/null \
        || die "创建用户失败，可用 --user 指定已有用户"
fi

log "安装到 $APP_DIR"
mkdir -p "$APP_DIR/heartapp"
install -m 644 "$SRC_ROOT/heartapp/__init__.py"      "$APP_DIR/heartapp/__init__.py"
install -m 644 "$SRC_ROOT/heartapp/room_protocol.py" "$APP_DIR/heartapp/room_protocol.py"
install -m 644 "$SRC_ROOT/heartapp/relay.py"         "$APP_DIR/heartapp/relay.py"
install -m 644 "$SRC_ROOT/heartapp/lobby.py"         "$APP_DIR/heartapp/lobby.py"
install -m 644 "$SRC_ROOT/server/requirements.txt"   "$APP_DIR/requirements.txt"

log "创建虚拟环境并安装依赖（只有 websockets）"
[ -d "$APP_DIR/venv" ] || "$PY" -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
# 先试默认源（限时，避免无限挂起），失败再走国内镜像
"$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check \
    --timeout 20 --retries 1 -r "$APP_DIR/requirements.txt" \
  || {
    log "默认源不可达，改用国内镜像重试"
    "$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check \
        --timeout 30 --retries 2 -r "$APP_DIR/requirements.txt" \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
      || die "依赖安装失败，可手动执行：$APP_DIR/venv/bin/pip install -r $APP_DIR/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
  }

chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"

log "写入配置 $ENV_FILE"
umask 077
cat > "$ENV_FILE" <<EOF
# HeartApp 联机转发服务配置
# 访问密码：留空表示任何人都能登录（仅建议在纯局域网使用）
HEARTAPP_RELAY_PASSWORD=${PASSWORD}
EOF
chmod 600 "$ENV_FILE"

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
EnvironmentFile=-${ENV_FILE}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python -m heartapp.relay --host ${BIND_HOST} --port ${PORT} --max-clients ${MAX_CLIENTS} --room-size ${ROOM_SIZE}
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
systemctl enable --now "$SERVICE"

sleep 1
log "检查服务状态"
if systemctl is-active --quiet "$SERVICE"; then
    log "服务已启动"
else
    echo
    journalctl -u "$SERVICE" -n 30 --no-pager || true
    die "服务没起来，看上面的日志"
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
 配置      ${ENV_FILE}   （改密码后执行 systemctl restart ${SERVICE}）
 运行用户  ${RUN_USER}

 常用命令
   systemctl status ${SERVICE}
   systemctl restart ${SERVICE}
   journalctl -u ${SERVICE} -f

 客户端填写
   ws://服务器IP:${PORT}
   公网请在前面加 TLS 反向代理，客户端填 wss://你的域名

 ⚠ 该服务不保存任何心率历史；房间、会话与令牌都在内存，重启即清空。
EOF
