# HeartApp 联机服务端

可自部署的 WebSocket 转发服务，支撑「联机房间」。**不含 GUI / BLE / 数据库**，
只需要 Python 3.9+ 与 `websockets`，也可以直接用免 Python 的独立 exe。

> **English quick start**
> - One-line Linux: `curl -fsSL https://raw.githubusercontent.com/soulonlyone/heartapp/main/server/deploy.sh | sudo bash`
>   (the wizard asks for a password with hidden input; use `--non-interactive` + `HEARTAPP_INSTALL_PASSWORD` for automation)
> - Docker: `docker run -d --name heartapp-server -p 127.0.0.1:8765:8765 -e HEARTAPP_RELAY_PASSWORD=pw <image>`
> - Windows: run `HeartApp-Server-v<ver>-win64.exe --allow-plaintext --host 0.0.0.0 --port 8765 --password pw`,
>   or use the GUI panel (`HeartApp-Server-Panel`).
> - Clients connect with `ws://<host>:8765` (LAN) or `wss://<domain>` (public, behind TLS).
> - The server stores **no heart-rate history**; rooms/tokens live in memory only.
> - Full details below are in Chinese.

## 三种部署方式

| 方式 | 适合 | 入口 |
|---|---|---|
| **Linux 一行** | VPS、长期后台 | 本页「一行部署」 |
| **Docker** | 已有 Compose 的机器 | `compose.yaml` + `Dockerfile` |
| **Windows** | 本机 / 局域网 | 独立 exe 或服务端面板 |

## 一行部署（Linux）

```sh
curl -fsSL https://raw.githubusercontent.com/soulonlyone/heartapp/main/server/deploy.sh \
  | sudo bash
```

运行后进入向导：**设置服务器访问密码（输入时显示 \* ，不回显明文，输两次）** → 确认端口 → 确认安装。

> 这个密码是**客户端在「联机房间」登录服务器时填的那个**，不是房间密码；
> 所有联机的人共用同一个，安装时必须设置（不能为空）。
> 密码不会出现在命令行里，也不会留在 shell 历史。

流程：查最新 Release → 下载服务端包 → **校验 SHA256** → 解压 → 安装。
代码装到 `/opt/heartapp-relay`（**root 所有，服务用户只读执行**），注册 systemd
服务 `heartapp-relay`，配置写到 `/etc/heartapp-relay.env`（权限 600）。

参数原样转发：`--port` / `--max-clients` / `--room-size` / `--version` / `--dry-run`。

```sh
# 换端口和容量
... | sudo bash -s -- --password '口令' --port 9000 --max-clients 50 --room-size 8
# 只看会做什么，不真装
... | sudo bash -s -- --dry-run --password '口令'
```

不放心管道安装，先下载检查：`curl -fsSLO .../deploy.sh && less deploy.sh && sudo bash deploy.sh`。

### 连不上 GitHub 时

国内直连 `github.com` 常超时（`Failed to connect to github.com port 443`）。**脚本已经自动处理**：

- 镜像站常常拿不到 Release 里的 `SHA256SUMS.txt`，此时自动改用仓库里的
  `server/checksums/v<版本>.txt`（与脚本同源，不经过镜像）

- 直连失败会依次尝试 `ghfast.top`、`gh-proxy.com`、`ghproxy.net` 三个镜像
- `api.github.com` 不可达时，改为从仓库的 `server/LATEST` 读取版本号

所以通常什么都不用加。想强制指定某个镜像再执行：

```sh
curl -fsSLO https://raw.githubusercontent.com/soulonlyone/heartapp/main/server/deploy.sh
sudo bash deploy.sh --version v1.4.0 --mirror https://ghfast.top/
```

如果连脚本本身都下载不下来（`raw.githubusercontent.com` 也不通），用下面的办法 2。

**2. 在能上网的电脑下载，再传到服务器**（最稳）

```sh
# 浏览器打开 releases/latest，下载 HeartApp-Server-v<版本>-linux.tar.gz
scp HeartApp-Server-v1.4.0-linux.tar.gz you@server:/tmp/
```

```sh
# 服务器上
cd /tmp && tar -xzf HeartApp-Server-v1.4.0-linux.tar.gz
cd heartapp-server-1.4.0 && sudo bash server/install-linux.sh
```

**3. 直接放到有一台能访问 GitHub 的机器上编译/运行**（服务端只需要 Python + websockets）

装好之后用管理菜单：

```sh
sudo heartapp-server          # 启停服务、看日志、改密码、改端口、改每房容量
systemctl status heartapp-relay
journalctl -u heartapp-relay -f
curl http://127.0.0.1:8765/healthz     # OK
```

卸载：

```sh
sudo systemctl disable --now heartapp-relay
sudo rm -rf /opt/heartapp-relay /etc/heartapp-relay.env /etc/systemd/system/heartapp-relay.service
sudo rm -f /usr/local/bin/heartapp-server && sudo systemctl daemon-reload
```

## Docker

```sh
cd server
cp .env.example .env          # 改端口 / 密码 / 连接上限 / 每房容量
docker compose up -d --build
docker compose logs --tail=20
```

默认只映射 `127.0.0.1:8765`，限制 0.5 核 / 384 MiB、日志轮转、只读根文件系统、丢弃全部
capabilities。想让局域网直连就把 `HEARTAPP_BIND_IP` 改成 `0.0.0.0`（记得设密码）。

## Windows

- **独立 exe**（不需要 Python）：`HeartApp-Server-v<版本>-win64.exe --allow-plaintext --host 0.0.0.0 --port 8765 --password pw`
- **可视化面板**：解压 `HeartApp-Server-Panel-v<版本>-win64.zip`，双击运行，可启停、设密码、
  复制地址、显示在线人数、最小化到托盘。退出时只停自己启动的实例。
- **开机自启**：管理员 PowerShell 运行 `server\service-windows.ps1 -Install`（计划任务，SYSTEM 运行）。
- 需要把 exe 放在可写目录，不要放 `C:\Program Files`，不要用管理员运行。

## 手动运行（任意系统）

```sh
pip install -r server/requirements.txt
python -m heartapp.relay --allow-plaintext --host 0.0.0.0 --port 8765 --password '口令'
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` | `127.0.0.1` | 局域网/公网要 `0.0.0.0`；IPv6 用 `::` |
| `--port` | `8765` | |
| `--max-clients` | `100` | 总连接上限（1–10000） |
| `--room-size` | `4` | 每房人数（1–128，受总上限约束） |
| `--cert` / `--key` | 无 | 直接启用 TLS，必须一起给 |

密码也可用环境变量 `HEARTAPP_RELAY_PASSWORD`（长期运行的服务建议用环境变量，
避免密码出现在进程命令行里）。

## 客户端怎么填

| 场景 | 地址 |
|---|---|
| 同一台电脑 | `ws://127.0.0.1:8765` |
| 局域网 | `ws://192.168.1.20:8765` |
| IPv6 | `ws://[2001:db8::1]:8765` |
| 公网（有反代） | `https://hr.example.com` 或 `wss://hr.example.com` |

客户端支持 `IP:端口` / `域名:端口` 简写：带非 443 端口按 `ws://` 连，要 TLS 就写明 `wss://`。

## 公网与 TLS

客户端**只信任系统证书**，没有「忽略证书错误」的开关；`ws://` 只适合本机或可信局域网。

建议用 Nginx / Caddy 终止 TLS 再反代到 `127.0.0.1:8765`，片段见 `nginx.conf.example`。要点：

- 必须转发 `Upgrade` / `Connection` 头
- `proxy_read_timeout` 别太小（客户端每 20 秒 ping 一次）
- 反代后所有来源 IP 会变成同一个，**共用每分钟 30 次的连接尝试限额**

也可以让服务端自带证书：`--cert fullchain.pem --key privkey.pem`。

## 协议与资源边界

- **协议 v2**：登录服务器与创建/加入/退出房间分离；客户端与服务端需一起升级，
  版本不兼容会明确提示。新客户端可连旧服务端。
- 每房容量默认 4，服务端可设 1–128；客户端每秒发一次心率，**关闭共享时不发送数值**。
- 断流 5 秒后数值为空，不把旧值当实时；模拟来源保留标识。
- 主动退房立即移除成员，无成员房间删除；网络断开保留 **30 秒重连席位**，
  凭仅在内存的随机会话令牌恢复原身份。
- 单条入站消息最多 2 KiB，限制消息频率、连接数与连接尝试；慢连接会被关闭。
- **同一 IP 每分钟最多 30 次**连接尝试；经反代时共用该限额。
- **服务端不保存心率历史**，日志不输出房间码或心率；健康检查 `GET /healthz`。
- 房间目录最多返回前 100 个房间，每 5 秒更新。
- 不提供：共享历史、语音视频、房主踢人、端到端加密、多实例同步。

**客户端侧**：多人曲线只在内存保留最多 30 分钟，退出清空，不写入本地历史；
曲线含设备与网络延迟，不代表逐拍同步；个人记录/导出只含本人数据。

服务端运营者可以接触经过它转发的心率——**只连接你信任的服务端**。
房间码只是定位符，不是房间密码。

## 容量参考

2C2G 机器建议先从 **20 个连接**起步。以上都是配置上限，**不是已验证的公网容量**（未压测）。
家庭宽带能否公网访问取决于是否被 CGNAT，域名不会自动解决。

## 排障

| 现象 | 排查 |
|---|---|
| 客户端连不上 | `curl http://127.0.0.1:8765/healthz` 看服务是否在跑 |
| 局域网其他机器连不上 | 是否 `--host 0.0.0.0`；防火墙是否放行端口 |
| 公网连不上 | 反代是否转发 `Upgrade`；安全组/云防火墙 |
| 版本不兼容 | 两端一起升级 |
| 密码错误 | `HEARTAPP_RELAY_PASSWORD` 与客户端填写不一致 |
| 房间列表为空 | 正常：还没人创建房间 |

## 验证状态

已验证：Python 本机转发、双客户端联机冒烟（共享开关、退房、断线重连、服务故障不影响本地记录）、
Windows 独立 exe 启动与密码校验、GitHub Actions CI 全绿。

**未验证**：真实 systemd 环境的首次安装/升级、Docker 镜像构建运行、公网 WSS + 证书 + 反代超时、压测。
