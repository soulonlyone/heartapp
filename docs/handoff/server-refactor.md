# 服务端重构交接（2026-11-13）

## DSH 检阅与合并（2026-11-20）

检阅分支为 `codex/ip-tls`，目标为 `main`。
上传前核对的基线为 `ecef7f0`，与当时远端 main 一致。

- `heartapp/server_cli.py` 提供服务运行、管理菜单、健康检查和证书管理的统一入口。
- `server/install-standalone.sh` 安装版本目录、写入 systemd 配置，并在失败时恢复旧版本与配置。
- `server/ip-tls.py` 管理 IP 证书签发、权限部署和自动续期。
- `tools/build_linux_standalone.py` 构建包含运行环境和证书工具的 Linux 包。
- `server/install-linux.sh` 识别自包含包并进入对应安装器，保留源码包安装路径。
- `server/deploy.sh` 识别新包结构，并修复下载超时、进度提示及镜像前缀重复问题。

Ubuntu 实装与回退、443 端口 WSS 双客户端、206 项回归、Qt 和联机冒烟已验证。
Windows 跳过的 3 项 Linux 文件权限测试已在 Ubuntu 补测。
公网 CA 签发、跨天续期、Debian 和 OpenCloudOS 实装仍需要公网服务器验收。
证书更新会重启联机服务，临时房间会清空。

合并代码后，发布流程还需要选用自包含构建产物作为 Linux 附件，并生成新的完整校验清单。
现有发布版本为 v1.4.2。请在新版本发布时同步 `server/LATEST`、
`server/checksums/` 和发布说明，使一键安装入口下载到新的自包含包。
本分支交付源码、构建工具和检阅说明。本地测试安装包位于
`_test_output/standalone/`，测试产物与构建环境按仓库忽略规则管理。

本次构建使用 `cpython-3.12.16+20261110-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz`，
来源为 python-build-standalone 官方发布，SHA256 为
`6a9e62dad033790aef722dbd370e0ce76ce17b73627ba3ae679f537049350b39`。
离线 wheel 目录包含 Certbot 5.10.0、websockets 15.0.1 及其 Linux 依赖，
依赖 wheel 采用 manylinux2014 / manylinux_2_28。
在 Linux 构建机上准备好上述文件后运行以下命令。

```sh
python3 tools/build_linux_standalone.py \
  --python-archive /path/to/python.tar.gz \
  --sha256 6a9e62dad033790aef722dbd370e0ce76ce17b73627ba3ae679f537049350b39 \
  --wheelhouse /path/to/wheels
```

## 2026-11-19 IP 证书开发分支补充

`codex/ip-tls` 保留已验证的 Python 联机核心，新增 Linux 自包含分发和自动 IP 证书。
自包含包自带可移植 CPython、websockets、Certbot 和依赖，
目标服务器安装时无需系统 Python、venv 或 pip。
统一入口为 `heartapp-server`，支持管理菜单、run、configure、get、health、
certificate 与 acme 子命令。独立版本目录与 current 指针支持安装失败恢复。

IP 证书部分在现有 Python 服务端上接入了自动 IP 证书，
安装向导新增公网 IP 输入，默认采用 443。Certbot 在独立环境中运行，
通过 TCP 80 的 HTTP-01 验证，申请 Let’s Encrypt shortlived IP 证书。
Linux 分发包包含 `server/ip-tls.py`，续期由
`heartapp-ip-renew.timer` 每 12 小时检查。

部署时检查 IP SAN、有效期和证书私钥配对，证书目录原子切换，
服务用户仅有组读权限。证书发生更新后重启服务，临时房间会清空。
申请失败会停止安装，续期失败保留当前证书，日志保存在 systemd 和独立 ACME 日志目录。
新增依赖仅用于 Linux 安装，现有客户端使用系统证书信任链。

验证覆盖 Windows 全量测试、Qt 冒烟、联机冒烟，以及 Ubuntu 的证书部署和失败回滚。
Ubuntu systemd 实装验证了首次安装、服务健康、真实双客户端登录与加入房间。
人为注入升级失败后，旧版本指针、访问配置和服务运行状态均恢复。
本地 WSS 双客户端测试使用临时测试证书，验证了登录、建房、加入与错误身份拒绝。
自包含测试包名含 `standalone-test`。公网 CA 签发、真实云安全组连通性、
Debian / OpenCloudOS 上完整 systemd 安装和真实续期仍待验收。
发布前需生成包含新安装器和证书管理器的新 Release，现有 v1.4.2 安装包没有此功能。
操作说明见 [服务端 README](../../server/README.md)。
自包含包的具体安装步骤见 [Linux 自包含测试说明](../../server/Linux自包含测试说明.md)。

2026-11-20 收尾检查通过 206 项回归，Windows 跳过的 3 项 Linux 证书文件测试
已在 Ubuntu 补测。最终包还验证了专用账户绑定 443、WSS 双客户端登录入房、
错误证书身份拒绝，以及内置运行环境中的真实证书部署与运行组切换。
部署下载器补上了 8 秒连接超时、10 秒停滞检测和逐来源提示，
正常传输显示进度，单次下载总时限 600 秒以适应低带宽服务器，
修复了手动镜像前缀重复拼接。

Linux 构建入口为 `tools/build_linux_standalone.py`，在 Linux 下传入已核验 SHA256 的
python-build-standalone 安装压缩包与 Linux wheel 目录。使用 Python 3.12 x86_64 gnu，
依赖 wheel 兼容 manylinux2014 / manylinux_2_28，包内 BUILD-INFO.json 记录依赖版本。
正式发布时需要将此构建产物放入统一发布的 Linux 附件位置，再生成完整校验清单。
目前主发布脚本的默认 Linux 构建仍兼容源码分发包，切勿将旧源码附件误当作自包含版。

> **给 Codex 的交接**。这个文档的目标不是"再修一个 bug"，而是**重构服务端**。
> 服务端功能是通的，但部署链路和安全模型是补丁堆出来的：一次真机安装踩出 6 个问题，
> 每个都是"脚本层"的，说明架构选错了地方。

---

## 0. 一句话结论

**问题不在代码逻辑（365 行 Python 很干净），而在两件事：**

1. **运行时依赖太多**：系统 `python3` → `venv` → `pip` → 国内网络 → 镜像/超时/编码，每层都会挂
2. **安全模型缺失**：没有内置 TLS，密码明文过网，全部用户共用一个密码

建议优先去掉第 1 项（**发布自包含二进制**），再内置第 2 项（**TLS + 认证**）。

---

## 1. 现状盘点

### 代码

| 文件 | 行数 | 职责 |
|---|---|---|
| `heartapp/relay.py` | 95 | 入口：argparse、TLS 上下文、`serve()` 参数、healthz |
| `heartapp/lobby.py` | 222 | 登录、房间目录、席位、限速、转发 |
| `heartapp/room_protocol.py` | 48 | 地址解析、房间码、JSON 编解码 |
| **合计** | **365** | 依赖只有 `websockets>=15` |

### 部署链路（742 行脚本 —— 问题集中区）

| 文件 | 行数 | 职责 |
|---|---|---|
| `server/deploy.sh` | 190 | 一行安装：镜像回退、版本解析、下载、校验和、解压 |
| `server/install-linux.sh` | 254 | 安装器：向导调用、复制代码、venv、pip、权限、systemd、防火墙 |
| `server/manage-linux.py` | 298 | 交互向导 + 管理菜单（改密码/端口/日志/容量） |

### 实测环境（用户真机）

- 腾讯云 VM，opencloudos（RHEL 系），Python **3.11.6**
- 云平台安全组需要手动放行端口
- GitHub 直连不稳定，依赖 ghfast.top 等镜像

---

## 2. 协议 v2 要点（重构**不能破坏**，否则客户端要一起改）

### 连接与登录

```
客户端 ──ws/wss──> 服务端
  ① 首帧必须登录：{"type":"login","password":"...","nick":"玩家一","version":2}
  ② 服务端回：   {"type":"login_ok","version":2,"self_id":"...","...":...}
     失败则：     {"type":"reject"} / {"type":"error","message":"..."}
```

### 房间操作（都是 JSON，`type` 区分）

| 类型 | 方向 | 字段 |
|---|---|---|
| `create` | C→S | `title`（可选）→ S 回 `welcome`（含 8 位房间码） |
| `join` | C→S | `room`（8 位码）→ S 回 `welcome` + `snapshot` |
| `leave` | C→S | → S 回 `left` |
| `list` | C→S | → S 回 `rooms`（最多 100 条，含 `total`） |
| `logout` | C→S | 断开会话 |
| `reading` | C→S | `bpm`(1–300 或 null)、`sharing`(bool)、`simulated`(bool) |
| `snapshot` | S→C | `room`、`members[]` |
| `error` / `command_error` | S→C | `message` |

### 约束（客户端已依赖这些行为）

- `max_size=2048` 字节，`max_queue=4`，`write_limit=16384`
- `origins=[None]`：**只接受非浏览器客户端**（拒绝带 Origin 的握手）
- 每 IP 每分钟最多 **30** 次连接尝试
- 每连接 **2 秒内最多 8 条**消息
- 断流 5 秒后读数显示为空（不插值、不补发历史）
- 断线保留 **30 秒**重连席位，凭内存令牌恢复同一 ID
- 房间容量服务端下发（默认 4，1–128），`max_clients` 默认 20–100
- `GET /healthz` → 纯文本 `OK`
- 服务端**不持久化任何心率**，日志不记房间码/心率

### 客户端地址解析规则（`room_protocol.server_url`）

```
不带 scheme            → wss://（默认加密）
不带 scheme 且端口≠443 → ws://（局域网裸端口）
http/https             → ws/wss
```

---

## 3. 本次实测踩出的 10 个坑（已修，但暴露设计问题）

| # | 症状 | 临时修法 | 根因 |
|---|---|---|---|
| 1 | `.sh: $'\r': command not found` | 打包时 `.sh` 强制转 LF | 工作区 CRLF 被原样打进 tar |
| 2 | `curl raw.githubusercontent` 静默挂死 | 加 `--max-time` + 4 源回退 | 单点依赖 GitHub，无超时 |
| 3 | `pip install` 空转 7 分钟 | 全部限时 + 清华源 + 失败不致命 | 运行时依赖 Python 生态 |
| 4 | `UnicodeDecodeError: 'gbk'` | `requirements.txt` 改纯 ASCII | pip 用系统 locale 解码 |
| 5 | 校验和取不到导致安装中止 | 回退仓库 `server/checksums/` | 镜像站不代理 Release 小文件 |
| 6 | `操作未完成：not writable` | 星号写 stderr 而非 stdin | `terminal()` 换的是只读 `/dev/tty` |
| 7 | 装完直接公网裸奔 | 默认 `127.0.0.1` + 拒绝无 TLS 的非环回启动 | 安装脚本默认值与 relay 默认值矛盾 |
| 8 | 升级时以 root 执行旧 pip | 先 `chown root` 再执行 + 每次重建 venv | 权限收紧时序错误 |
| 9 | 云安全组放行不了 | 只能文档说明 | 云控制面权限，VM 无权 |
| 10 | 发布附件被反复替换 | — | 发布流程缺少"一版一包"约束 |

> 这些坑没有一个是业务逻辑问题，**全部来自"把 Python 生态当部署运行时"**。

---

## 4. 仍然存在的问题（按严重度）

### 🔴 阻断级

1. **没有内置 TLS**：只能手传 `--cert/--key`，实际使用必须外挂 nginx/Caddy。自签证书客户端不信任，也没有指纹校验流程。
2. **密码明文传输**：`ws://` 上直接发 `{"type":"login","password":"..."}`，路径上任何节点可截获。
3. **单一共享密码**：所有用户一个密码，无账号、无令牌、无房间权限、无踢人。
4. **部署脆弱**：Python 版本、pip 源、网络可达性、发行版差异（firewalld/ufw/systemd）、云安全组，任一环节都能让安装失败。

### 🟠 一般

5. 无监控/指标（只有 `/healthz`）
6. 升级只能重装脚本，无版本化升级/回滚
7. Docker 镜像从未在 CI 构建验证
8. 无审计日志（为隐私刻意静默，但排障时抓瞎）
9. 协议无正式版本协商（只比对 `version` 字段）
10. 客户端无法知道服务端是否加密

---

## 5. 重构方案（三选一，我推荐 A → 再补 TLS）

### 方案 A：保持 Python，改为自包含二进制（**推荐，成本最低**）

**做法**：用 PyInstaller 在 CI（ubuntu-22.04）打一个 **Linux 单文件二进制**，
和现在的 Windows `HeartApp-Server.exe` 一样。安装器只需：

```
下载二进制 → 写 /etc/heartapp-relay.env → 写 systemd unit → 启动
```

**收益**：

- 彻底去掉 `python3` 版本检查、`venv`、`pip`、镜像源、GBK 解码问题
- 安装脚本从 742 行降到 ~150 行，坑 2/3/4/8 全部消失
- 与 Windows 分发方式统一（已有实现可参考 `tools/build_relay.py`）

**注意**：

- glibc 兼容：用较老的构建基线（ubuntu-22.04 = glibc 2.35），老系统需评估
- 体积 +10MB 左右

### 方案 B：Go 重写（最彻底）

- 单静态二进制（约 8MB），无任何运行时依赖
- **内置 TLS**：`autocert` 可直接申请 Let's Encrypt 证书，一行配置解决最大安全缺口
- 内置限速、指标（`/metrics`）、管理子命令（`heartapp-server passwd/status/logs`）
- 安装脚本可压到 ~50 行

**代价**：协议重写（365 行 Python → 约 800 行 Go）、客户端联机测试重写、Windows 服务端也要重打。

### 方案 C：维持现状，只补 TLS 反代文档

最快，但坑 1–10 全部保留，不推荐。

---

## 6. 无论选哪条路，都该做的 7 件事

1. **内置 TLS**，至少三种入口：
   - `--cert/--key`（已有）
   - `--tls-auto <domain>`（ACME 自动申请，方案 B 最容易）
   - 自签 + 客户端显示指纹让用户确认
2. **密码不再裸传**：要么强制 TLS，要么改用 `HMAC(password, nonce)` 挑战应答
3. **安装器幂等**：`--dry-run`、`--uninstall`、升级保留配置、失败可回滚
4. **安装验收脚本**：干净 VM 上跑 安装 → healthz → 双客户端登录 → 转发 → 卸载
5. **CI 增加**：`shellcheck`、`.sh` 换行检查、Linux 安装冒烟（容器或 VM）、Docker 构建
6. **客户端显示「此服务器未加密」**（服务端在 `login_ok` 里带 `tls: true/false`）
7. **发布不可变**：一版一包，禁止替换已发布附件（这次 v1.4.0 被换了 4 次、v1.4.1 被换了 3 次）

---

## 7. 验收标准（重构完成后必须全过）

```
[ ] 干净 Ubuntu 22.04 / Debian 12 / opencloudos 各装一次，全程无人工干预（除云安全组）
[ ] 安装全程 ≤ 3 分钟，失败时有明确下一步
[ ] healthz 返回 200
[ ] 两个客户端能登录、建房、共享心率、看到彼此
[ ] 断网 10 秒后恢复，客户端回到原房间
[ ] 明文模式下服务端拒绝启动（除非显式 --allow-plaintext）
[ ] wss 模式下抓包看不到密码与心率
[ ] 卸载后无残留：/opt、/etc、systemd unit 全清
[ ] 升级保留配置与密码
```

---

## 8. 关键文件与环境注意事项

### 文件索引

| 文件 | 作用 |
|---|---|
| `heartapp/relay.py` | 服务端入口 |
| `heartapp/lobby.py` | 业务：登录/房间/限速/转发 |
| `heartapp/room_protocol.py` | 协议常量与地址解析 |
| `server/deploy.sh` | 一行安装（镜像回退、版本、校验和） |
| `server/install-linux.sh` | Linux 安装器 |
| `server/manage-linux.py` | 向导与管理菜单 |
| `tools/build_relay.py` | 打包 linux tar / docker tar / win exe |
| `tools/build_release.py` | 一版一包、签名、`server/LATEST`、`server/checksums/` |
| `docs/handoff/server-security-p0.md` | P0 加固的上一份交接（已完成） |

### 必须遵守的工程约束

- **`.sh` 打包时必须转 LF**（`build_relay.py` 的 `_add()` 已处理）
- **`requirements.txt` 必须纯 ASCII**（pip 用系统 locale 解码）
- **PyInstaller 单文件版必须 `--runtime-tmpdir .`**（Windows 上实测）
- **exe 必须有版本资源**（`build_exe._version_file`，否则杀毒误报）
- **`.bat` 必须 CRLF，`.ps1` 必须 UTF-8 with BOM**
- **发布附件名必须 ASCII**
- 测试：`run_tests.py`（189 项）+ 4 个冒烟，改联机必跑 `tools/room_smoke_test.py`

### 现成的可复用资产

- `heartapp/lobby.py` 的限速与席位逻辑可以直接照搬（真机验证过）
- `server/checksums/` 回退机制解决了镜像拿不到小文件的问题，可保留
- `server/LATEST` 解决了 `api.github.com` 不可达时的版本解析

---

## 9. 建议的推进顺序

1. **先做方案 A**（自包含二进制）——把部署从 742 行降到 150 行，坑去掉一半
2. **再补内置 TLS**（方案 B 的话顺手就有）
3. 然后才考虑安全模型重构（账号、令牌、房间权限）

> 如果决定直接走方案 B（Go），建议一次性做：二进制 + 内置 ACME + 内置限速 + 管理子命令，
> 并把客户端协议测试作为验收门槛，避免"重写完发现客户端连不上"。
