# Linux 服务端部署与运维说明

适合已有 VPS、需要长期后台运行的用户。服务只需 Python 3.9+ 与 websockets，
不安装 Qt、BLE 或数据库。2C2G 且同时运行其他 agent 的机器，先采用 20 连接。

## 安装

使用仓库源码，或解压 Linux 分发包，进入包含 `heartapp/` 和 `server/` 的根目录：

```sh
sudo bash server/install-linux.sh --port 8765 --max-clients 20 --room-size 4 --host 127.0.0.1
```

上例只监听本机，适合在前面放 Nginx/Caddy 做 WSS。局域网直连时改成 `--host 0.0.0.0`。
脚本会创建独立系统用户、虚拟环境、systemd 服务。它不是发行版包管理器安装包，
需要系统已安装 Python 及 venv 支持；Debian/Ubuntu 可先安装 `python3`、`python3-venv`。

安装后立即设置服务器密码：

```sh
sudoedit /etc/heartapp-relay.env
```

将密码写入下面的字段，再重启服务。值中如有空格、引号或反斜杠，应按 systemd
EnvironmentFile 的语法正确引用；初次部署可使用随机英文字母、数字组成的口令。

```ini
HEARTAPP_RELAY_PASSWORD=替换为你的随机口令
```

安装脚本默认未设置密码。在设置完成前不要对公网放行，建议沿用上面的仅本机监听。
也可通过安装脚本的 `--password` 参数设置，但口令可能短暂出现在进程命令行中。

## 配置在哪里

| 内容 | 路径 |
|---|---|
| 服务代码 | `/opt/heartapp-relay/heartapp/` |
| Python 环境 | `/opt/heartapp-relay/venv/` |
| 密码配置 | `/etc/heartapp-relay.env`，权限 600 |
| systemd 单元 | `/etc/systemd/system/heartapp-relay.service` |

密码更新只需修改 env 并重启。调整端口、监听地址、人数上限可以修改单元中的
`ExecStart` 参数，随后执行 `systemctl daemon-reload` 和服务重启。
重新执行安装脚本会覆盖配置和单元文件；升级前先备份并准备原来的参数及密码。

## 常用操作

```sh
sudo systemctl status heartapp-relay
sudo systemctl restart heartapp-relay
sudo systemctl stop heartapp-relay
sudo systemctl start heartapp-relay
sudo journalctl -u heartapp-relay -n 50 --no-pager
curl --fail http://127.0.0.1:8765/healthz
```

健康检查应返回 `HeartApp relay v2 OK`。它只验证本机服务，不代表公网入口可达。
服务已配置开机启动；修改配置后使用 restart，不会保留现有房间。

## 公网入口

建议让服务监听 `127.0.0.1`，由同机 Nginx/Caddy 提供可信证书和 WSS。
Nginx 参考 [nginx.conf.example](nginx.conf.example)，需要转发 WebSocket Upgrade 头，
并保留足够的空闲超时。客户端填 `wss://你的域名`，不要填 `0.0.0.0`。

放行的是代理对外提供的端口，通常为 443；无需再把本机 8765 对公网开放。
没有反代、只做可信局域网测试时，才使用 `ws://局域网IP:8765` 并放行 TCP 8765。
域名不解决 CGNAT，家庭网络还需要实际可达的公网入口或可信私人组网。

## 升级、回退与边界

升级前记录当前版本，备份 env 和 systemd 单元，停止服务，再更新必要源码或重新安装。
客户端、服务端协议必须同为 v2；回退时整套回退协议组件，不混用不同协议版本。
服务不存历史，停止或重启后房间消失，需要重新创建；客户端本地数据库不用迁移。

反向代理后的所有用户可能共用每 IP 每分钟 30 次连接尝试限额。
初版不提供端到端加密、房间独立权限、多实例房间同步或历史补发。
公网证书、跨网重连、长期资源占用仍需在实际服务器验收；20 连接是初始配置，非压测结论。
