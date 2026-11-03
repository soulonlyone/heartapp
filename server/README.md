# HeartApp 联机转发服务

独立、可自部署的 WebSocket 转发服务，用来支持「联机房间」功能。
不需要中央账号或公共服务器——所有参与者连接同一台你自己部署的服务端。

**服务端不包含 GUI、BLE 和数据库**，只需要 Python 3.9+ 与 `websockets`。
它可以只部署 4 个文件运行，不需要在服务器上装 Qt。

## 直接下载现成的包

不想克隆仓库？到 [Server v1.2.0 发布页](https://github.com/soulonlyone/heartapp/releases/tag/server-v1.2.0) 下载：

| 文件 | 用途 |
|---|---|
| `HeartApp-Server-v1.2.0-win64.exe` | **Windows 独立服务端，不需要装 Python**，直接跑 |
| `HeartApp-Server-v1.2.0-linux.tar.gz` | Linux 源码包，解压后 `sudo bash server/install-linux.sh` |
| `HeartApp-Server-v1.2.0-docker.tar.gz` | Docker 构建包，解压后 `sh build_and_run.sh` |
| `SHA256SUMS.txt` | 三个文件的 SHA256 校验和 |

Windows exe 直接在命令行跑：

```bat
HeartApp-Server-v1.2.0-win64.exe --host 0.0.0.0 --port 8765 --password 你的口令
```

首次运行可能弹 SmartScreen，点「更多信息」→「仍要运行」（自签名证书，见[签名说明](../docs/签名说明.md)）。
它会解压到 exe 旁边，请放在**可写目录**，不要放 `C:\Program Files`、不要用管理员运行。

---

| 想用哪种方式 | 跳到 |
|---|---|
| Docker（最省事） | [Docker](#docker) |
| Linux 一键安装（systemd） | [Linux](#linux-systemd) |
| Windows 一键安装（开机自启） | [Windows](#windows) |
| 任意系统手动前台运行 | [手动运行](#手动运行) |

---

## 需要准备什么

- 一台能被参与者访问到的服务器（公网 VPS、公司内网机器、或就是你自己这台电脑）
- 能装 Python 3.9+ 或 Docker
- 一个想用的端口，默认 `8765`

## Docker

```sh
cd server
cp .env.example .env          # 按需改端口 / 密码 / 连接上限
docker compose up -d --build
docker compose ps
docker compose logs --tail=20
```

- 默认只映射**服务器本机**的 `127.0.0.1:8765`，建议前面加 HTTPS 反代（见[公网](#公网与-tls)）
- 最多 20 个连接、0.5 核 / 384 MiB 上限、日志轮转、只读根文件系统、丢弃全部 capabilities
- 容器以普通用户运行，不挂载任何个人数据
- 停止：`docker compose down`

想让局域网其他机器直连，把 `.env` 里的 `HEARTAPP_BIND_IP` 改成 `0.0.0.0`
（这时就没有反向代理兜底了，请确保设置了访问密码）。

镜像目前是**源码构建**，没有发布到镜像仓库。

## Linux (systemd)

一键安装，会在 `/opt/heartapp-relay` 建立独立运行环境并注册服务。只在**仓库里**执行：

```sh
sudo bash server/install-linux.sh
```

常用变体：

```sh
sudo bash server/install-linux.sh --port 9000 --max-clients 50
sudo bash server/install-linux.sh --password '换成你的口令'
sudo bash server/install-linux.sh --user heartapp --room-size 4
```

它会做这些事：

| 项目 | 位置 |
|---|---|
| 运行代码（只复制 4 个文件） | `/opt/heartapp-relay/heartapp/` |
| 虚拟环境 + 依赖 | `/opt/heartapp-relay/venv/` |
| 配置（含访问密码，权限 600） | `/etc/heartapp-relay.env` |
| systemd 单元 | `/etc/systemd/system/heartapp-relay.service` |

安装完成后：

```sh
systemctl status heartapp-relay
systemctl restart heartapp-relay      # 改完密码 env 后重启
journalctl -u heartapp-relay -f
curl http://127.0.0.1:8765/healthz    # HeartApp relay v2 OK
```

服务默认监听 `0.0.0.0:8765`，并以独立系统用户运行（`NoNewPrivileges`、
只读文件系统、私有 /tmp 等加固项已内置）。

卸载：

```sh
sudo systemctl disable --now heartapp-relay
sudo rm -rf /opt/heartapp-relay /etc/heartapp-relay.env \
            /etc/systemd/system/heartapp-relay.service
sudo systemctl daemon-reload
```

## Windows

**第一步：安装**（双击即可）

```
server\install-windows.bat
```

它会创建 `server\.venv`、把服务端 4 个文件复制到 `server\app\`、安装依赖，
并在管理员权限下自动放行防火墙 TCP 8765。

**第二步：选一种运行方式**

| 方式 | 操作 | 适合 |
|---|---|---|
| 前台运行（调试） | `server\run-relay.bat` 或 `server\run-relay.bat 你的密码` | 临时用、排错 |
| 开机自启（推荐） | 见下方 | 长期运行 |

开机自启用**系统计划任务**（不需要 NSSM 之类的外部工具），用管理员 PowerShell：

```powershell
cd server
powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Install
powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Install -Password "口令" -Port 9000

powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Status
powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Remove
```

- 任务以 `SYSTEM` 运行，开机自动启动，失败自动重试 3 次
- 启动脚本里可能含访问密码，已通过 `icacls` 限制为仅 SYSTEM / Administrators 可读
- 启动器为带 UTF-8 BOM 的 `server/app/service-run.ps1`，支持中文密码和中文路径。
  从旧版升级后需重新执行 `service-windows.ps1 -Install`，才能更新已注册任务的启动入口。
- 客户端填写 `ws://这台电脑的IP:8765`

## 手动运行

任意系统，装好 Python 3.9+ 和依赖后：

```sh
pip install -r server/requirements.txt
python -m heartapp.relay --host 0.0.0.0 --port 8765
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` | `127.0.0.1` | 监听地址；局域网/公网要 `0.0.0.0`，IPv6 用 `::` |
| `--port` | `8765` | 监听端口 |
| `--max-clients` | `100` | 总连接上限（1–10000） |
| `--room-size` | `4` | 每个房间人数（1–4） |
| `--cert` / `--key` | 无 | 直接启用 TLS，两个必须一起给 |

访问密码走环境变量：

```sh
HEARTAPP_RELAY_PASSWORD='口令' python -m heartapp.relay --host 0.0.0.0
```

## 客户端怎么填

客户端「实时」页 →「联机房间」：

| 场景 | 服务器地址 |
|---|---|
| 同一台电脑 | `ws://127.0.0.1:8765` |
| 局域网 | `ws://192.168.1.20:8765` |
| IPv6 | `ws://[2001:db8::1]:8765` |
| 公网（有反代） | `https://hr.example.com` 或 `wss://hr.example.com` |

填写昵称、地址、密码（服务端设了才需要）→「登录服务器」。
登录后下方显示同服房间，可以创建房间、从列表加入、或输入 8 位房间码加入。

本机双开测试时给两个客户端用不同数据库：

```sh
python -m heartapp --db data/player1.db
python -m heartapp --db data/player2.db
```

## 公网与 TLS

客户端**只信任系统证书**，没有"忽略证书错误"的开关。`ws://` 只适合本机或可信局域网。

推荐用已有的 Nginx / Caddy 终止 TLS，反代到 `127.0.0.1:8765`，
Nginx 片段见 [`nginx.conf.example`](nginx.conf.example)。要点：

- 必须转发 `Upgrade` / `Connection` 头
- `proxy_read_timeout` 不要太小（客户端每 20 秒 ping 一次）
- 反代后所有来源 IP 会变成同一个，**共用每分钟 30 次的连接尝试限额**

也可以让服务端自己带证书：`--cert fullchain.pem --key privkey.pem`。

## 协议与资源边界

- **协议 v2**：登录服务器与创建 / 加入 / 退出房间分离。客户端与服务端必须一起升级，
  版本不兼容时会明确提示。v1 不再兼容。
- 每房间最多 **4 人**；客户端每秒发一次当前心率，**关闭共享时不发送数值**。
- 断流 5 秒后数值为空，不把旧值当实时；模拟来源保留标识。
- 主动退房 / 退出服务器立即移除成员，无成员房间删除。
  网络断开保留 **30 秒重连席位**，凭仅在内存的随机会话令牌恢复原身份和房间。
- 单条入站消息最多 **2 KiB**，限制消息频率、总连接数和连接尝试；
  发送缓冲有上限，慢连接会被关闭。
- **同一来源 IP 每分钟最多 30 次**连接尝试。经反代时共用该限额，只适合小规模私人部署。
- **服务端不保存心率历史**，日志不输出房间码或心率；健康检查 `GET /healthz`。
- 房间目录最多返回前 100 个房间，每 5 秒自动更新。
- 空闲 / 重连会话只存内存，断开后最多保留 30 秒；总会话数最多为连接上限的两倍。
- 不提供：共享历史、语音视频、房主踢人、端到端加密、多实例房间同步。

**客户端侧的边界**：多人曲线只保留内存最多 30 分钟，退出房间清空，不写入本地历史；
曲线包含设备与网络延迟，不代表逐拍精确同步；个人记录 / 导出始终只含本人数据。

服务端运营者可以接触经过它转发的心率——**请只连接你信任的服务端**。
房间码只是定位符，不是房间密码；同服已登录用户都能浏览并加入未满的房间。

## 容量参考

2C2G 的机器建议先从 **20 个连接**起步（Docker Compose 里已按此设置资源上限）。
以上都是配置上限，**不是已验证的公网容量**——尚未做压测。

家庭宽带能否公网访问取决于是否被 CGNAT；域名不会自动解决这个问题，动态 IP 可以配 DDNS。

## 排障

| 现象 | 排查 |
|---|---|
| 客户端提示连不上 | 服务是否在跑：`curl http://127.0.0.1:8765/healthz` |
| 局域网其他机器连不上 | 监听地址是否 `0.0.0.0`；防火墙是否放行 TCP 端口 |
| 公网连不上 | 反代是否转发 `Upgrade` 头；安全组 / 云防火墙是否放行 |
| 提示协议版本不兼容 | 客户端与服务端版本不一致，一起升级 |
| 登录提示密码错误 | 服务端 `HEARTAPP_RELAY_PASSWORD` 与客户端填写不一致 |
| 房间列表是空的 | 正常：没有房间时为空，或你的服务器还没有人创建过 |

## 合并与验证状态

合并说明与验证记录见 [main 合并交接说明](../docs/merge-handoff.md)。

已验证：Python 本机转发、双客户端联机冒烟（共享开关、退房、断线重连、服务故障不影响本地记录）。
**未验证**：Docker 镜像构建运行、公网 WSS + 证书 + 反代超时、压力测试。
