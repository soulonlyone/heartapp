# Linux 服务端部署与运维说明

适合已有 VPS、需要长期后台运行的用户。服务只需 Python 3.9+ 与 websockets，
不安装 Qt、BLE 或数据库。2C2G 且同时运行其他 agent 的机器，先采用 20 连接。

本文从零安装到日常运维均在命令行操作，没有网页管理后台。下面的环境准备针对
使用 systemd 的 Ubuntu/Debian；其他发行版请用其包管理器安装等价软件。

## 一行部署（Linux）

先在服务器准备好 Python 3.9+、venv、curl 和 tar，使用 SSH 终端执行。

```sh
curl -fsSL https://raw.githubusercontent.com/soulonlyone/heartapp/main/server/deploy.sh | sudo bash
```

安装向导会依次提示设置密码（隐藏输入并再次确认）、选择端口（默认 8765）、
确认安装。确认后下载包中的安装器会创建运行用户、虚拟环境和 systemd 服务，
安装到 `/opt/heartapp-relay` 并开机启动。密码保存在权限为 600 的
`/etc/heartapp-relay.env`，屏幕上不会回显密码。

再次安装会读取已有配置。密码与端口处直接回车可保留原值，确认后重启服务。
取消安装会保留原配置。服务重启会清空内存中的临时房间。

安装后运行管理菜单。

```sh
sudo heartapp-server
```

菜单支持查看状态、启动、停止、重启、修改密码、修改端口、修改每房人数和查看最近日志。
修改密码或端口时，确认保存后才重启。修改端口还需同步调整防火墙、云安全组、
反向代理和客户端地址。

默认监听 `127.0.0.1:8765`。本机客户端可用 `127.0.0.1:8765`，公网客户端使用 TLS 反向代理的 `wss://域名`。
位于家庭路由器后的服务器需要端口转发；公网加 TLS 反向代理后使用 `wss://域名`。
可用 `--host 127.0.0.1` 预设仅本机监听。

```sh
# 指定版本并进入向导
curl -fsSL https://raw.githubusercontent.com/soulonlyone/heartapp/main/server/deploy.sh | sudo bash -s -- --version v<版本>

# 只下载解压，不安装
curl -fsSL https://raw.githubusercontent.com/soulonlyone/heartapp/main/server/deploy.sh | sudo bash -s -- --dry-run
```

自动化部署可加 `--non-interactive`。首次必须提供 `--password`，后续默认沿用旧配置。
密码参数可能出现在 shell 历史和进程列表中，手动安装请使用默认向导。

也可以下载后检查脚本，再运行。

```sh
curl -fsSLO https://raw.githubusercontent.com/soulonlyone/heartapp/main/server/deploy.sh
less deploy.sh
sudo bash deploy.sh
```

此交互流程需要包含 `server/manage-linux.py` 的新版 Linux Release。
旧版发布包需更新后才能使用本流程。


## 1. 连接服务器、准备环境

从你自己的电脑终端连接（把账户和 IP 换成自己的）：

```sh
ssh server_user@服务器IPv4
```

下面命令都在服务器执行。已有 agent 的服务器无需重装系统，也无需安装 Docker：

```sh
cat /etc/os-release
sudo apt-get update
sudo apt-get install -y python3 python3-venv git curl nano
python3 --version
systemctl --version
```

Python 应为 3.9 或更高。若系统没有 systemd，请使用 Docker 方案或前台运行；
本安装器不负责适配其他服务管理器。终端以 root 登录时可省略命令前的 sudo。

## 2. 取得程序、先看帮助

克隆仓库后进入项目根目录；私有仓库需要有读取权限，不要把访问令牌写进命令 URL：

```sh
git clone --branch main https://github.com/soulonlyone/heartapp.git heartapp
cd heartapp
bash server/install-linux.sh --help
```

正式部署宜固定经过验收的发布标签或提交。已有仓库不必重复 clone，先确认所在分支与版本。
也可解压 Linux 分发包，进入同时包含 `heartapp/`、`server/` 的根目录。

## 3. 安装服务、配置密码

在源码仓库或 Linux 分发包根目录执行。

```sh
sudo bash server/install-linux.sh
```

跟随向导输入密码、确认密码、选择端口并确认安装。回车使用默认端口，
升级时回车保留现有密码和端口。

使用反向代理时，可以预设仅本机监听。

```sh
sudo bash server/install-linux.sh --host 127.0.0.1
```

安装完成后检查服务。

```sh
sudo systemctl status heartapp-relay --no-pager
curl --fail http://127.0.0.1:8765/healthz
```

若修改过端口或监听地址，请相应调整健康检查地址。

## 4. 配置在哪里、如何修改

| 内容 | 路径 |
|---|---|
| 服务代码 | `/opt/heartapp-relay/heartapp/` |
| Python 环境 | `/opt/heartapp-relay/venv/` |
| 服务配置和密码 | `/etc/heartapp-relay.env`，权限 600 |
| systemd 单元 | `/etc/systemd/system/heartapp-relay.service` |
| 管理入口 | `/usr/local/bin/heartapp-server` |

运行 `sudo heartapp-server`，按菜单修改密码、端口或每房人数。新配置确认保存后生效，
并重启服务。其他参数可以重新运行安装向导并传入 `--host`、`--max-clients`、
`--room-size`，未显式修改的设置会保留。配置文件由管理器引用和转义，
包含空格、引号、反斜杠的密码也会原样保留。

`heartapp-server --help`、`bash server/install-linux.sh --help`、
`bash server/deploy.sh --help` 均可查看帮助。

## 5. 日常运维

```sh
sudo systemctl status heartapp-relay
sudo systemctl restart heartapp-relay
sudo systemctl stop heartapp-relay
sudo systemctl start heartapp-relay
sudo journalctl -u heartapp-relay -n 50 --no-pager
curl --fail http://127.0.0.1:8765/healthz
sudo systemctl enable heartapp-relay
sudo systemctl disable heartapp-relay
sudo journalctl -u heartapp-relay -f
sudo ss -ltnp 'sport = :8765'
```

健康检查应返回 `OK`。它只验证本机服务，不代表公网入口可达。
服务已配置开机启动；修改配置后使用 restart，不会保留现有房间。
`disable` 只取消开机启动，不会立即停止；停止使用 stop。日志跟随用 Ctrl+C 结束查看，
不会停止后台服务。首次排障先看 status 和 journal，再检查端口，不必反复重装。

## 6. 朋友怎么连接、公网入口

建议让服务监听 `127.0.0.1`，由同机 Nginx/Caddy 提供可信证书和 WSS。
Nginx 参考 [nginx.conf.example](nginx.conf.example)，需要转发 WebSocket Upgrade 头，
并保留足够的空闲超时。客户端填 `wss://你的域名`，不要填 `0.0.0.0`。

放行的是代理对外提供的端口，通常为 443；无需再把本机 8765 对公网开放。
没有反代、只做可信局域网测试时，才使用 `ws://局域网IP:8765` 并放行 TCP 8765。
域名不解决 CGNAT，家庭网络还需要实际可达的公网入口或可信私人组网。

云服务器安全组需按云厂商提供的控制台或 CLI 放行实际对外端口。本文的开服命令不会
自动修改安全组；无需更改已经运行的 agent 端口。客户端登录需要地址和服务器访问密码，
登录后在客户端创建房间，房间码不是另一个密码。

## 7. 升级与回退

升级前记录当前版本，备份 env 和 systemd 单元，停止服务，再更新必要源码或重新安装。
客户端、服务端协议必须同为 v2；回退时整套回退协议组件，不混用不同协议版本。
服务不存历史，停止或重启后房间消失，需要重新创建；客户端本地数据库不用迁移。

源码安装的升级示例，逐条执行，任何一步报错先处理再继续。以下在项目根目录执行：

```sh
sudo mkdir -p /root/heartapp-relay-backup
sudo cp -a /etc/heartapp-relay.env /root/heartapp-relay-backup/
sudo cp -a /etc/systemd/system/heartapp-relay.service /root/heartapp-relay-backup/
sudo cp -a /opt/heartapp-relay/heartapp /root/heartapp-relay-backup/
sudo cp -a /opt/heartapp-relay/requirements.txt /root/heartapp-relay-backup/
git rev-parse HEAD | sudo tee /root/heartapp-relay-backup/source-commit.txt
sudo systemctl stop heartapp-relay
git pull --ff-only
sudo install -m 644 heartapp/__init__.py heartapp/room_protocol.py heartapp/relay.py heartapp/lobby.py /opt/heartapp-relay/heartapp/
sudo install -m 644 server/requirements.txt /opt/heartapp-relay/requirements.txt
sudo /opt/heartapp-relay/venv/bin/pip install -r /opt/heartapp-relay/requirements.txt
sudo systemctl start heartapp-relay
curl --fail http://127.0.0.1:8765/healthz
```

已固定标签或 detached 提交时，不使用 `git pull`，改为 fetch 后切到要验收的版本。
压缩包安装则用新包替代上面的源目录。上述升级不会覆盖服务器密码和 systemd 单元。
升级前的备份目录应是本次升级前新建的，重复升级时先更名保留旧备份，避免混淆。

需要回退时恢复刚才备份的运行代码、依赖约束及配置：

```sh
sudo systemctl stop heartapp-relay
sudo install -m 644 /root/heartapp-relay-backup/heartapp/*.py /opt/heartapp-relay/heartapp/
sudo install -m 644 /root/heartapp-relay-backup/requirements.txt /opt/heartapp-relay/requirements.txt
sudo /opt/heartapp-relay/venv/bin/pip install -r /opt/heartapp-relay/requirements.txt
sudo cp -a /root/heartapp-relay-backup/heartapp-relay.env /etc/heartapp-relay.env
sudo cp -a /root/heartapp-relay-backup/heartapp-relay.service /etc/systemd/system/heartapp-relay.service
sudo systemctl daemon-reload
sudo systemctl start heartapp-relay
curl --fail http://127.0.0.1:8765/healthz
```

## 8. 停用与卸载

只是不想继续开服时，停止并取消自启即可，保留文件方便恢复：

```sh
sudo systemctl disable --now heartapp-relay
```

彻底卸载前保管好密码配置，再删除本项目明确创建的路径：

```sh
sudo rm /etc/systemd/system/heartapp-relay.service
sudo systemctl daemon-reload
sudo rm /etc/heartapp-relay.env
sudo rm -r /opt/heartapp-relay
```

不要删除共享 Python、其他 agent 的目录或其他服务用户。备份不会随上述操作删除。

## 9. 命令行帮助与前台排错

```sh
bash server/install-linux.sh --help
cd /opt/heartapp-relay
/opt/heartapp-relay/venv/bin/python -m heartapp.relay --help
/opt/heartapp-relay/venv/bin/python -m heartapp.relay --version
```

安装器帮助在源码根目录执行；后两个命令在 `/opt/heartapp-relay` 目录执行。
帮助显示参数、默认值及示例，不需要启动服务。前台排错可先停止后台服务，然后：

```sh
cd /opt/heartapp-relay
sudo -u heartapp venv/bin/python -m heartapp.relay --host 127.0.0.1 --port 8765 --max-clients 20
```

这条前台命令不会自动加载 systemd 的密码文件，只用于本机排错；Ctrl+C 停止。
排错结束再用 `sudo systemctl start heartapp-relay` 恢复后台运行。

## 10. 边界

反向代理后的所有用户可能共用每 IP 每分钟 30 次连接尝试限额。
初版不提供端到端加密、房间独立权限、多实例房间同步或历史补发。
公网证书、跨网重连、长期资源占用仍需在实际服务器验收；20 连接是初始配置，非压测结论。
