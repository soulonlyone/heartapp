# Linux 自包含服务端测试版

此测试版自带 Python、联机库和 Certbot，目标服务器安装时无需安装 Python 或 pip，
也无需访问 Python 软件源。支持 Linux x86_64、glibc 2.28+、systemd。
安装与证书管理命令通过同一个 `heartapp-server` 入口执行。

## 安装

将压缩包和 SHA256SUMS.txt 上传到服务器，在同一目录执行。

```sh
sha256sum -c SHA256SUMS.txt
tar -xzf HeartApp-Server-v1.4.2-standalone-test-linux.tar.gz
cd heartapp-server-1.4.2-standalone-test
sudo bash server/install-linux.sh
```

向导依次设置服务器访问密码、公网 IP、服务端口并确认安装。
公网 IP 模式默认端口 443，自动申请受信任的 IP 证书并启用续期任务。
首次申请和续期都需要访问 Let’s Encrypt，云安全组需放行 TCP 80 和服务端口。
TCP 80 保持空闲供证书验证使用。已有网站占用该端口时安装器会提示并停止。

旧版本的访问密码、容量等设置会被读取，直接回车可以保留。
已有服务的 ExecStart 覆盖配置可能覆盖新启动命令，安装器会提示先处理该冲突。
初次安装会自动创建专用系统用户。配置文件权限为 600。

安装成功后，现有客户端可使用 `wss://公网IP`。
自定义端口填写 `wss://公网IP:端口`，IPv6 地址加方括号。

## 检查与日常管理

```sh
sudo heartapp-server
sudo heartapp-server health
sudo systemctl status heartapp-relay --no-pager
sudo systemctl status heartapp-ip-renew.timer --no-pager
sudo journalctl -u heartapp-ip-renew.service -n 50 --no-pager
# 主动运行一次续期检查
sudo systemctl start heartapp-ip-renew.service
```

证书每 12 小时检查一次。实际更新后重启联机服务，临时房间会清空。
续期失败会记录错误并保留当前证书，需在证书到期前处理失败原因。
证书身份不匹配或过期时客户端会拒绝连接。

## 升级和失败恢复

每次安装写入 `/opt/heartapp-relay/releases/` 下的独立版本目录，
`/opt/heartapp-relay/current` 指向当前版本。
安装失败时自动恢复之前的配置、服务文件和版本指针，并恢复原服务运行状态。
旧版本目录保留供排障，原先源码版的目录也保留。
证书机构账户和已申请的证书会保留，便于重试安装。

主配置位于 `/etc/heartapp-relay.env`，私钥部署到 `/etc/heartapp-tls`。
证书申请数据位于 `/etc/heartapp-acme`，日志位于 `/var/log/heartapp-acme`。

## 测试边界

本地可验证独立运行、systemd 安装、登录和房间协议、失败恢复及证书部署逻辑。
真实公网 IP 签发、云安全组连通性和跨天自动续期需要在公网服务器验收。
此包用于实测，正式发布前还需在 Debian 与 OpenCloudOS 上复核。
