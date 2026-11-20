#!/usr/bin/env bash
# 安装自包含 Linux 服务端。配置与证书沿用旧版固定位置。
set -euo pipefail
APP=/opt/heartapp-relay
SERVICE=heartapp-relay
ENV_FILE=/etc/heartapp-relay.env
UNIT=/etc/systemd/system/heartapp-relay.service
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPEN_PORT=1
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            cat <<'HELP'
HeartApp 自包含 Linux 服务端安装
用法：sudo bash server/install-linux.sh
向导设置访问密码、公网 IP 和端口；公网 IP 模式自动申请证书并续期。
自动化可传 --tls-ip IP --port 443 --non-interactive，
并通过 HEARTAPP_INSTALL_PASSWORD 设置密码。
支持 --host、--user、--max-clients、--room-size、--no-open-port。
安装后 sudo heartapp-server 打开菜单。
要求 systemd、x86_64、glibc Linux；公网证书要求 TCP 80 和服务端口可达。
HELP
            exit 0 ;;
        --no-open-port) OPEN_PORT=0; shift ;;
        --password)
            [ $# -ge 2 ] || { echo '缺少密码参数' >&2; exit 1; }
            export HEARTAPP_INSTALL_PASSWORD="$2"; shift 2 ;;
        *) ARGS+=("$1"); shift ;;
    esac
done
[ "$(id -u)" -eq 0 ] || { echo '请使用 sudo 运行' >&2; exit 1; }
command -v systemctl >/dev/null || { echo '需要 systemd' >&2; exit 1; }
"$SRC/runtime/heartapp-server" --version
if [ -e "$APP/current" ] && [ ! -L "$APP/current" ]; then
    echo "$APP/current 必须是版本符号链接，请先备份现有目录" >&2
    exit 1
fi
if [ -d "$UNIT.d" ] && grep -Rq '^ExecStart=' "$UNIT.d"; then
    echo '检测到旧 ExecStart 覆盖配置，请先更新覆盖配置再安装' >&2
    exit 1
fi
BACKUP="$(mktemp -d /tmp/heartapp-install-XXXXXXXX)"
chmod 700 "$BACKUP"
FILES=("$ENV_FILE" "$UNIT" /etc/systemd/system/heartapp-ip-renew.service
       /etc/systemd/system/heartapp-ip-renew.timer /usr/local/bin/heartapp-server)
for i in "${!FILES[@]}"; do
    if [ -e "${FILES[$i]}" ] || [ -L "${FILES[$i]}" ]; then
        cp -a "${FILES[$i]}" "$BACKUP/$i"
    fi
done
PREVIOUS="$(readlink "$APP/current" || true)"
PREVIOUS_TLS="$(readlink /etc/heartapp-tls/current || true)"
PREVIOUS_TLS_GROUP="$(stat -c %g /etc/heartapp-tls 2>/dev/null || true)"
TIMER_WAS_ENABLED=0
systemctl is-enabled --quiet heartapp-ip-renew.timer 2>/dev/null && TIMER_WAS_ENABLED=1
SERVICE_WAS_ENABLED=0
systemctl is-enabled --quiet "$SERVICE" 2>/dev/null && SERVICE_WAS_ENABLED=1
SERVICE_WAS_ACTIVE=0
systemctl is-active --quiet "$SERVICE" 2>/dev/null && SERVICE_WAS_ACTIVE=1
COMMITTED=0
MUTATED=0
cleanup() {
    result=$?
    trap - EXIT
    if [ "$COMMITTED" -eq 0 ] && [ "$MUTATED" -eq 1 ]; then
        echo '[heartapp] 安装失败，恢复原配置和服务版本' >&2
        for i in "${!FILES[@]}"; do
            if [ -e "$BACKUP/$i" ] || [ -L "$BACKUP/$i" ]; then
                rm -f "${FILES[$i]}"
                cp -a "$BACKUP/$i" "${FILES[$i]}" || true
            else
                rm -f "${FILES[$i]}"
            fi
        done
        if [ -n "$PREVIOUS" ]; then
            ln -sfn "$PREVIOUS" "$APP/.current-rollback"
            mv -Tf "$APP/.current-rollback" "$APP/current"
        else
            rm -f "$APP/current"
        fi
        if [ -n "$PREVIOUS_TLS" ]; then
            ln -sfn "$PREVIOUS_TLS" /etc/heartapp-tls/.current-rollback
            mv -Tf /etc/heartapp-tls/.current-rollback /etc/heartapp-tls/current
            [ -z "$PREVIOUS_TLS_GROUP" ] || chgrp "$PREVIOUS_TLS_GROUP" /etc/heartapp-tls
        fi
        systemctl daemon-reload || true
        if [ "$TIMER_WAS_ENABLED" -eq 0 ]; then
            systemctl disable --now heartapp-ip-renew.timer >/dev/null 2>&1 || true
        fi
        if [ -e "$BACKUP/1" ]; then
            if [ "$SERVICE_WAS_ACTIVE" -eq 1 ]; then
                systemctl restart "$SERVICE" || echo '原服务恢复失败，请查看服务日志' >&2
            else
                systemctl stop "$SERVICE" >/dev/null 2>&1 || true
            fi
            [ "$SERVICE_WAS_ENABLED" -eq 1 ] || systemctl disable "$SERVICE" >/dev/null 2>&1 || true
        else
            systemctl stop "$SERVICE" >/dev/null 2>&1 || true
            systemctl disable "$SERVICE" >/dev/null 2>&1 || true
        fi
    fi
    rm -rf "$BACKUP"
    exit "$result"
}
trap cleanup EXIT
"$SRC/runtime/heartapp-server" configure "${ARGS[@]}"
MUTATED=1
install -d -m 755 "$APP" "$APP/releases"
RELEASE="$(mktemp -d "$APP/releases/release-XXXXXXXX")"
cp -a "$SRC/runtime/." "$RELEASE/"
chown -R root:root "$RELEASE"
chmod 755 "$RELEASE"
chmod -R go-w "$RELEASE"
RUN_USER="$("$RELEASE/heartapp-server" get --key user)"
PORT="$("$RELEASE/heartapp-server" get --key port)"
HOST="$("$RELEASE/heartapp-server" get --key host)"
TLS_IP="$("$RELEASE/heartapp-server" get --key tls_ip)"
if ! id "$RUN_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$RUN_USER"
fi
ln -sfn "$RELEASE" "$APP/.current-new"
mv -Tf "$APP/.current-new" "$APP/current"
TLS_ARGS=""
CAPABILITIES=""
if [ -n "$TLS_IP" ]; then
    EXTRA=()
    [ "$OPEN_PORT" -eq 1 ] || EXTRA+=(--no-open-port)
    "$APP/current/heartapp-server" certificate setup "${EXTRA[@]}"
    TLS_ARGS=' --cert /etc/heartapp-tls/current/fullchain.pem --key /etc/heartapp-tls/current/privkey.pem'
    CAPABILITIES=CAP_NET_BIND_SERVICE
else
    case "$HOST" in
        127.0.0.1|localhost|::1) ;;
        *)
            TLS_ARGS=' --allow-plaintext'
            if [ "$OPEN_PORT" -eq 1 ]; then
                "$APP/current/heartapp-server" firewall || echo '请手动放行服务端口'
            fi
            ;;
    esac
fi
[ "$PORT" -ge 1024 ] || CAPABILITIES=CAP_NET_BIND_SERVICE
cat > "$UNIT" <<EOF
[Unit]
Description=HeartApp relay (standalone)
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
EnvironmentFile=$ENV_FILE
WorkingDirectory=$APP/current
ExecStart=$APP/current/heartapp-server run$TLS_ARGS --host \${HEARTAPP_RELAY_HOST} --port \${HEARTAPP_RELAY_PORT} --max-clients \${HEARTAPP_RELAY_MAX_CLIENTS} --room-size \${HEARTAPP_RELAY_ROOM_SIZE}
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
AmbientCapabilities=$CAPABILITIES
CapabilityBoundingSet=$CAPABILITIES
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
RestrictAddressFamilies=AF_INET AF_INET6
[Install]
WantedBy=multi-user.target
EOF
install -d /usr/local/bin
ln -sfn "$APP/current/heartapp-server" /usr/local/bin/heartapp-server
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"
READY=0
for attempt in 1 2 3 4 5; do
    if "$APP/current/heartapp-server" health; then READY=1; break; fi
    sleep 1
done
[ "$READY" -eq 1 ] || { journalctl -u "$SERVICE" -n 20 --no-pager; exit 1; }
COMMITTED=1
echo '[heartapp] 安装完成，管理菜单 sudo heartapp-server'
if [ -n "$TLS_IP" ]; then
    [[ "$TLS_IP" != *:* ]] || TLS_IP="[$TLS_IP]"
    echo "客户端连接 wss://$TLS_IP:$PORT"
    echo '已启用证书自动续期，证书更新会重启服务并清空临时房间。'
else
    echo "监听 $HOST:$PORT，公网加密使用请在向导填写公网 IP。"
fi
