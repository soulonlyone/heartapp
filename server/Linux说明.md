# Linux 服务端部署与运维说明

适合已有 VPS、需要长期后台运行的用户。服务只需 Python 3.9+ 与 websockets，
不安装 Qt、BLE 或数据库。2C2G 且同时运行其他 agent 的机器，先采用 20 连接。

本文从零安装到日常运维均在命令行操作，没有网页管理后台。下面的环境准备针对
使用 systemd 的 Ubuntu/Debian；其他发行版请用其包管理器安装等价软件。

## 一行部署（Linux）

```sh
curl -fsSL https://raw.githubusercontent.com/soulonlyone/heartapp/main/server/deploy.sh | sudo bash -s -- --password '你的口令'
```

等价于「下载对应 Release 的服务端压缩包 → 校验 SHA256 → 解压 → 运行 `install-linux.sh`」，
最终把代码装到 `/opt/heartapp-relay` 并注册 systemd 服务 `heartapp-relay`。
参数会原样转发给安装脚本：

```sh
# 指定端口与连接上限
curl -fsSL .../server/deploy.sh | sudo bash -s -- --password '口令' --port 9000 --max-clients 50

# 固定版本
... | sudo bash -s -- --password '口令' --version v1.2.0

# 只看它会做什么，不真装
... | sudo bash -s -- --dry-run --password '口令'
```

不放心管道安装，可以先下载检查：

```sh
curl -fsSLO https://raw.githubusercontent.com/soulonlyone/heartapp/main/server/deploy.sh
less deploy.sh
sudo bash deploy.sh --password '你的口令'
```

服务默认只监听本机 `127.0.0.1:8765`，公网请在前面加 HTTPS / WSS 反向代理。

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
git clone --branch codex/server-control-panel https://github.com/soulonlyone/heartapp.git heartapp
cd heartapp
bash server/install-linux.sh --help
```

上例使用当前开发分支，含新面板文档和完整帮助；合并后可替换为 main，正式部署宜固定
经过验收的发布标签或提交。已有仓库不必重复 clone，先确认所在分支与版本。
也可解压 Linux 分发包，进入同时包含 `heartapp/`、`server/` 的根目录。

## 3. 安装服务、配置密码

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

可以先在终端生成随机密码，再粘贴进配置文件：

```sh
python3 -c 'import secrets; print(secrets.token_hex(16))'
sudo systemctl restart heartapp-relay
sudo systemctl status heartapp-relay --no-pager
curl --fail http://127.0.0.1:8765/healthz
```

注意先完成密码文件编辑，再 restart。健康检查返回 `HeartApp relay v2 OK` 后，
再配置朋友的访问入口。本人在服务器上运行的客户端可用 `ws://127.0.0.1:8765`；
朋友不能拿这个回环地址访问你的服务器。

## 4. 配置在哪里、如何修改

| 内容 | 路径 |
|---|---|
| 服务代码 | `/opt/heartapp-relay/heartapp/` |
| Python 环境 | `/opt/heartapp-relay/venv/` |
| 密码配置 | `/etc/heartapp-relay.env`，权限 600 |
| systemd 单元 | `/etc/systemd/system/heartapp-relay.service` |

密码更新只需修改 env 并重启。调整端口、监听地址、人数上限可以修改单元中的
`ExecStart` 参数，随后执行 `systemctl daemon-reload` 和服务重启。
重新执行安装脚本会覆盖配置和单元文件；升级前先备份并准备原来的参数及密码。

修改监听地址、端口、人数的具体步骤：

```sh
sudoedit /etc/systemd/system/heartapp-relay.service
sudo systemctl daemon-reload
sudo systemctl restart heartapp-relay
sudo systemctl status heartapp-relay --no-pager
```

编辑 `ExecStart` 中的 `--host`、`--port`、`--max-clients`、`--room-size`；
不要删除其他加固配置。监听和端口改变后，同步修改客户端地址、代理或防火墙设置。

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

健康检查应返回 `HeartApp relay v2 OK`。它只验证本机服务，不代表公网入口可达。
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
