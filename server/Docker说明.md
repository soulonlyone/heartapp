# Docker 服务端部署与运维说明

适合已有 Docker Compose 的服务器。推荐使用仓库中的 Compose 配置，
它包含连接人数、资源限制、健康检查及日志轮转；不需要安装本机 Python 或 Qt。

部署、配置和维护均在 SSH/终端操作，无需 Docker Desktop 或网页面板。
下面以 Ubuntu/Debian VPS 为例；已有 Docker 和 agent 容器的机器跳过引擎安装，
不要为了本项目重装 Docker 或删除其他容器。

## 1. 连接服务器、检查 Docker

从自己的电脑连接服务器，之后命令均在服务器执行：

```sh
ssh server_user@服务器IPv4
```

先检查环境：

```sh
cat /etc/os-release
docker --version
docker compose version
sudo docker info
```

后三条都正常时直接到第 3 节。本文使用带空格的 `docker compose` 插件命令，
不是旧的 `docker-compose`。默认使用 sudo，无需为本项目给普通用户增加 docker 组权限。

## 2. 新机器安装 Docker Engine 和 Compose

以下仅用于尚未安装 Docker 的 Ubuntu/Debian。已有旧引擎或冲突包时先按官方说明
检查兼容性，不盲目卸载已有服务依赖。安装方式核对自 Docker 官方
[Ubuntu 文档](https://docs.docker.com/engine/install/ubuntu/)与
[Debian 文档](https://docs.docker.com/engine/install/debian/)。

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl git nano
. /etc/os-release
case "$ID" in
  ubuntu) HEARTAPP_DOCKER_DISTRO=ubuntu; HEARTAPP_DOCKER_CODENAME="${UBUNTU_CODENAME:-$VERSION_CODENAME}" ;;
  debian) HEARTAPP_DOCKER_DISTRO=debian; HEARTAPP_DOCKER_CODENAME="$VERSION_CODENAME" ;;
  *) echo '请按 Docker 官方对应发行版文档安装'; exit 1 ;;
esac
sudo install -d -m 0755 /etc/apt/keyrings
sudo curl --fail --location "https://download.docker.com/linux/${HEARTAPP_DOCKER_DISTRO}/gpg" --output /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' "$HEARTAPP_DOCKER_DISTRO" "$HEARTAPP_DOCKER_CODENAME" "$(dpkg --print-architecture)" | sudo tee /etc/apt/sources.list.d/docker.sources
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run --rm hello-world
sudo docker compose version
```

看到 hello-world 成功信息且 Compose 可用，再继续。系统版本或架构不在官方支持范围时，
按对应发行版文档处理，不套用别的发行版代号。无法下载镜像时先排查服务器外网。

## 3. 下载程序、配置并首次启动

已有 Docker 但缺少 git、curl、nano 时，先用发行版包管理器安装这几个工具。
仓库目前的新帮助功能在开发分支；正式部署可换成已合并的 main 或已验收标签：

```sh
git clone --branch codex/server-control-panel https://github.com/soulonlyone/heartapp.git heartapp
cd heartapp
```

私有仓库需具备读取权限，不把访问令牌写进 clone 地址。以下新部署固定 Compose 项目名为
`heartapp`；已用其他项目名部署时沿用原项目名，不要直接改名创建第二个实例。

在仓库根目录进入 `server`，创建本机配置：

```sh
cd server
cp .env.example .env
chmod 600 .env
nano .env
```

编辑 `.env`：

```ini
HEARTAPP_BIND_IP=127.0.0.1
HEARTAPP_PORT=8765
HEARTAPP_MAX_CLIENTS=20
HEARTAPP_RELAY_PASSWORD=替换为你的随机口令
```

随后运行：

```sh
sudo docker compose -p heartapp up -d --build
sudo docker compose -p heartapp ps
curl --fail http://127.0.0.1:8765/healthz
```

健康检查返回 `HeartApp relay v2 OK` 后，本机服务正常。公网访问仍需要证书、
反向代理和安全组；推荐代理到 `127.0.0.1:8765`，客户端填写 `wss://你的域名`。
局域网直连将绑定地址改成 `0.0.0.0`，客户端填写服务器局域网 IP，放行相应 TCP 端口。

朋友在心率客户端填写地址和访问密码，登录后创建房间。`127.0.0.1`、`0.0.0.0`
都不能直接作为朋友连接你服务器的地址。云安全组需要用云厂商的控制台或 CLI 配置；
Docker 发布端口可能绕过部分 ufw 规则，不能只凭 ufw 的状态判断公网是否封闭。
本机绑定模式下通常只对外提供代理的 443 端口，不再暴露 8765。

## 4. 配置含义

| 字段 | 默认 | 作用 |
|---|---|---|
| `HEARTAPP_BIND_IP` | 127.0.0.1 | 宿主机监听地址；不是发给朋友的地址 |
| `HEARTAPP_PORT` | 8765 | 宿主机端口，容器内部仍为 8765 |
| `HEARTAPP_MAX_CLIENTS` | 20 | 总连接上限，默认每房间仍为 4 人 |
| `HEARTAPP_RELAY_PASSWORD` | 空 | 整台服务器的访问密码，部署前设置 |

Compose 默认限制 0.5 CPU、384MiB，容器为非 root 用户、只读根文件系统，
去掉额外 capabilities，日志每份 5MiB、最多 3 份。不挂载数据库或个人记录。
这些是配置限制，不代表已验证的公网容量。

## 5. 修改配置与日常运维

改 `.env` 后需要重新创建容器，仅执行 restart 不会应用新的环境变量：

```sh
sudo docker compose -p heartapp up -d --force-recreate
sudo docker compose -p heartapp logs --tail=50
sudo docker compose -p heartapp logs -f --tail=50
sudo docker compose -p heartapp restart
sudo docker compose -p heartapp stop
sudo docker compose -p heartapp start
sudo docker stats --no-stream $(sudo docker compose -p heartapp ps -q)
```

restart、stop 或重新创建都会使内存房间清空；客户端本地历史不受影响。
升级源码后用 `docker compose up -d --build`，需要回退时先切回原版本代码并重建。
请单独保管 `.env`，不要提交真实密码，也不要把含环境变量的完整诊断输出公开。
以上命令在仓库 `server` 目录执行。跟随日志时 Ctrl+C 只结束查看，不停止容器。
资源检查前先确认 `compose ps` 中该服务正在运行；无人运行时不要执行空的 stats 命令。
`restart: unless-stopped` 会在 Docker/服务器重启后恢复容器，但手动 stop 后保持停止，
需再 start 或 up。更新 env 用重新创建，更新源码用重建镜像，两者不同。

## 6. 升级和回退

逐条执行，出错先处理再继续。先在当前项目的 `server` 目录记录版本、备份 env，并保留旧镜像：

```sh
mkdir -p ../_deploy-backup
cp -p .env ../_deploy-backup/server.env
git rev-parse HEAD > ../_deploy-backup/source-commit.txt
HEARTAPP_IMAGE_ID=$(sudo docker compose -p heartapp images -q heartapp-relay)
sudo docker image tag "$HEARTAPP_IMAGE_ID" heartapp-relay:rollback
cd ..
git pull --ff-only
cd server
sudo docker compose -p heartapp up -d --build
curl --fail http://127.0.0.1:8765/healthz
sudo docker compose -p heartapp logs --tail=30
```

上面从当前 Compose 实例读取镜像 ID，不需要猜测生成的镜像名字；读取结果为空时停止升级，
先确认原实例确实已部署。若自己修改过项目名，所有命令沿用实际项目名。
备份目录包含密码，不提交到 Git；新一轮升级前保留旧备份，避免覆盖唯一可用回退点。
固定到标签/提交的源码使用 fetch 后切换指定版本，不使用 pull。

故障时用保存的旧镜像恢复，避免回退过程再次联网下载旧依赖。在 `server` 目录创建临时覆盖文件：

```sh
cp -p ../_deploy-backup/server.env .env
printf 'services:\n  heartapp-relay:\n    image: heartapp-relay:rollback\n' > compose.rollback.yaml
sudo docker compose -p heartapp -f compose.yaml -f compose.rollback.yaml up -d --no-build
curl --fail http://127.0.0.1:8765/healthz
```

若新版修改过 compose 配置，回退前还需切回备份记录的源码提交；上例适用于配置结构未变。
当前运行旧镜像期间，维护命令沿用相同的 `-f` 文件组合。确认恢复后，再安排正式升级。
不要执行全局 `docker system prune` 清理回退镜像或其他 agent 的数据。

## 7. 停用与卸载

停用并移除本项目容器、网络，在当前 `server` 目录执行：

```sh
sudo docker compose -p heartapp down
```

若正在使用回退覆盖文件，添加上一节同样的 `-f` 参数。down 不会移除共享 Docker 引擎或
其他项目，源码、env 和备份也保留。确认不需要后再按实际路径归档或删除本项目目录。
不要为卸载 HeartApp 而卸载整台机器的 Docker，尤其服务器还有 agent 容器时。

## 8. 使用 Docker 分发压缩包

解压修复版 `HeartApp-Server-*-docker.tar.gz`，进入包含 `build_and_run.sh` 的根目录：

```sh
export HEARTAPP_RELAY_PASSWORD='替换为你的随机口令'
sudo --preserve-env=HEARTAPP_RELAY_PASSWORD sh build_and_run.sh
```

辅助脚本会使用 `server/Dockerfile` 构建镜像、替换名为 `heartapp-server` 的容器，
仅映射本机 `127.0.0.1:8765`；替换前请确认这个容器就是你要更新的实例。
该独立包不是 Compose 项目，使用以下命令管理：

```sh
sudo docker logs --tail=50 heartapp-server
sudo docker restart heartapp-server
sudo docker stop heartapp-server
sudo docker start heartapp-server
```

辅助脚本与 Compose 配置不完全相同：当前独立包沿用服务端默认的 100 连接上限，
没有 Compose 的日志轮转配置。2C2G 共用其他任务的场景优先采用 Compose 的 20 连接配置。
需要修改独立包的连接上限时，在辅助脚本镜像名后添加 `python -m heartapp.relay
--host 0.0.0.0 --port 8765 --max-clients 20`；修改后重新运行脚本。

## 9. 命令行帮助

```sh
sh build_and_run.sh --help
sudo docker compose --help
sudo docker compose up --help
```

第一条在独立分发包根目录执行，查看帮助不需要 Docker 或 sudo；它会列出默认绑定、资源
限制、密码设置方法及维护命令，不构建镜像、不替换容器。
Compose 项目没有 `build_and_run.sh`，直接使用后两条查看 Docker 的帮助。
转发核心的参数帮助也能在已运行的 Compose 容器中查看，以下命令在仓库 `server` 目录执行：

```sh
sudo docker compose -p heartapp exec heartapp-relay python -m heartapp.relay --help
sudo docker compose -p heartapp exec heartapp-relay python -m heartapp.relay --version
```

帮助进程会退出，不会另开一个监听实例。

## 10. 排查顺序

1. `docker compose ps` 或 `docker ps` 确认容器正在运行。
2. 在宿主机访问 `/healthz`，确认本机转发核心正常。
3. 检查监听范围、云安全组、防火墙及代理配置。
4. 确认客户端协议 v2、地址和密码一致，再由客户端创建房间。

房间码不是独立密码，同服登录用户可查看并加入未满房间。
本项目尚未完成真实 Docker 引擎构建、公网 WSS 或压力测试；分发脚本测试使用 Docker 命令桩，
请在目标服务器完成实际部署验收后再对外使用。
