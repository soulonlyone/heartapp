# Docker 服务端部署与运维说明

适合已有 Docker Compose 的服务器。推荐使用仓库中的 Compose 配置，
它包含连接人数、资源限制、健康检查及日志轮转；不需要安装本机 Python 或 Qt。

## 从仓库部署

在仓库根目录进入 `server`，创建本机配置：

```sh
cd server
cp .env.example .env
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
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8765/healthz
```

健康检查返回 `HeartApp relay v2 OK` 后，本机服务正常。公网访问仍需要证书、
反向代理和安全组；推荐代理到 `127.0.0.1:8765`，客户端填写 `wss://你的域名`。
局域网直连将绑定地址改成 `0.0.0.0`，客户端填写服务器局域网 IP，放行相应 TCP 端口。

## 配置含义

| 字段 | 默认 | 作用 |
|---|---|---|
| `HEARTAPP_BIND_IP` | 127.0.0.1 | 宿主机监听地址；不是发给朋友的地址 |
| `HEARTAPP_PORT` | 8765 | 宿主机端口，容器内部仍为 8765 |
| `HEARTAPP_MAX_CLIENTS` | 20 | 总连接上限，默认每房间仍为 4 人 |
| `HEARTAPP_RELAY_PASSWORD` | 空 | 整台服务器的访问密码，部署前设置 |

Compose 默认限制 0.5 CPU、384MiB，容器为非 root 用户、只读根文件系统，
去掉额外 capabilities，日志每份 5MiB、最多 3 份。不挂载数据库或个人记录。
这些是配置限制，不代表已验证的公网容量。

## 修改配置与日常维护

改 `.env` 后需要重新创建容器，仅执行 restart 不会应用新的环境变量：

```sh
docker compose up -d --force-recreate
docker compose logs --tail=50
docker compose restart
docker compose stop
docker compose start
docker compose down
```

restart、stop 或重新创建都会使内存房间清空；客户端本地历史不受影响。
升级源码后用 `docker compose up -d --build`，需要回退时先切回原版本代码并重建。
请单独保管 `.env`，不要提交真实密码，也不要把含环境变量的完整诊断输出公开。

## 使用 Docker 分发压缩包

解压修复版 `HeartApp-Server-*-docker.tar.gz`，进入包含 `build_and_run.sh` 的根目录：

```sh
export HEARTAPP_RELAY_PASSWORD='替换为你的随机口令'
sh build_and_run.sh
```

辅助脚本会使用 `server/Dockerfile` 构建镜像、替换名为 `heartapp-server` 的容器，
仅映射本机 `127.0.0.1:8765`；替换前请确认这个容器就是你要更新的实例。
该独立包不是 Compose 项目，使用以下命令管理：

```sh
docker logs --tail=50 heartapp-server
docker restart heartapp-server
docker stop heartapp-server
docker start heartapp-server
```

辅助脚本与 Compose 配置不完全相同：当前独立包沿用服务端默认的 100 连接上限，
没有 Compose 的日志轮转配置。2C2G 共用其他任务的场景优先采用 Compose 的 20 连接配置。
需要修改独立包的连接上限时，在辅助脚本镜像名后添加 `python -m heartapp.relay
--host 0.0.0.0 --port 8765 --max-clients 20`；修改后重新运行脚本。

## 排查顺序

1. `docker compose ps` 或 `docker ps` 确认容器正在运行。
2. 在宿主机访问 `/healthz`，确认本机转发核心正常。
3. 检查监听范围、云安全组、防火墙及代理配置。
4. 确认客户端协议 v2、地址和密码一致，再由客户端创建房间。

房间码不是独立密码，同服登录用户可查看并加入未满房间。
本项目尚未完成真实 Docker 引擎构建、公网 WSS 或压力测试；分发脚本测试使用 Docker 命令桩，
请在目标服务器完成实际部署验收后再对外使用。
