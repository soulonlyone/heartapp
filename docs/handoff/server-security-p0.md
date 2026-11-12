# 服务端安全加固 P0 · 交接说明（2026-11-12）

> **接管对象**：在 Codex 里继续修复服务端。
> **一句话状态**：P0 的代码改动已写好并手工验证过行为，**但还没提交、没跑测试、没重建发布包**；
> 配套的测试断言和文档文案还没改，照现在的状态跑 CI 会失败。

---

## 1. 为什么做这件事

在上海 VPS 上实测 Linux 一行安装，暴露两个问题：

1. **发布包里 `install-linux.sh` 是 CRLF**，bash 报 `$'\r': command not found` —— 已修复并重传（见 §2）。
2. **装完默认就是公网裸奔**：安装脚本默认 `--host 0.0.0.0`，而 `relay.py` 默认是 `127.0.0.1`，
   两个默认值互相矛盾。结果访问密码和所有人的心率都以明文经过公网。

安全评估结论（P0 要解决）：

| 风险 | 级别 | P0 对策 |
|---|---|---|
| 明文传输密码与心率 | 高 | 非本机监听时**拒绝启动**，除非显式 `--allow-plaintext` |
| 默认绑定 0.0.0.0 | 高 | 安装脚本默认改为 `127.0.0.1` |
| 密码可设为 `123456` | 中 | 最少 8 位 + 拒绝全同字符 + 弱密码黑名单 |
| `/healthz` 返回版本号 | 低 | 只回 `OK` |

---

## 2. 已完成并已提交（main）

| 提交 | 内容 |
|---|---|
| `44e93f5` | 一行安装**自动回退镜像**（ghfast.top / gh-proxy.com / ghproxy.net），`api.github.com` 不通时改读 `server/LATEST` |
| `9ac959d` | **打包装 .sh 强制转 LF**（`build_relay.py`），`.gitattributes` 加 `*.sh eol=lf`；v1.4.0 的 linux/docker 附件已重建重传 |
| `d8878e7` | 向导把「访问密码」解释清楚（客户端登录服务器用，不是房间密码） |
| `c0dc6bf` | 输入密码时显示 `*`（`_masked_input`），支持退格、中文按字符计 |

**发布包当前状态（重要）**

v1.4.0 的附件已经被替换过 4 次（CRLF 修复 → 向导文案 → 星号回显 → 每次同步重新生成 `SHA256SUMS.txt`）。
版本号没变但内容变了，不符合「发布不可变」的原则。

**建议**：P0 完成后再改一次包时，**直接发 v1.4.1**，别再替换 v1.4.0 的附件。

---

## 3. 工作区里未提交的 P0 改动

改动**没有提交**，仍在工作区。已导出为 `docs/handoff/server-security-p0.patch`（8.1 KB），
即使工作区被覆盖也能用 `git apply` 恢复。

三个文件、共 52 行新增 / 8 行删除：

### 3.1 `heartapp/relay.py`

```python
# /healthz 不再暴露版本号
if request.path == "/healthz":
    return connection.respond(200, "OK\n")

# 新增参数
parser.add_argument("--allow-plaintext", action="store_true", ...)

# 启动前拦截
loopback = {"127.0.0.1", "localhost", "::1", "[::1]"}
if not args.cert and args.host not in loopback and not args.allow_plaintext:
    parser.error("拒绝在 ... 上以明文启动：访问密码和心率都会明文经过网络。...")
```

**已手工验证**（改完后当场跑的）：

```
python -m heartapp.relay --host 0.0.0.0 --port 18799
→ error: 拒绝在 0.0.0.0 上以明文启动 ...

python -m heartapp.relay --host 0.0.0.0 --port 18799 --allow-plaintext
→ HeartApp relay v2 · 0.0.0.0:18799 · max 100 · room 4   （正常启动）
```

### 3.2 `server/install-linux.sh`

- `BIND_HOST=0.0.0.0` → **`127.0.0.1`**
- `--host` 帮助文案跟着改（局域网直连才用 0.0.0.0）
- 写 unit 前计算 `PLAINTEXT_FLAG`：

```sh
case "$BIND_HOST" in
    127.0.0.1|localhost|::1|"[::1]") ;;
    *) PLAINTEXT_FLAG=" --allow-plaintext"
       log "注意：绑定 $BIND_HOST 且未启用 TLS，访问密码和心率会明文经过网络" ;;
esac

ExecStart=.../python -m heartapp.relay${PLAINTEXT_FLAG} --host ${HEARTAPP_RELAY_HOST} ...
```

`bash -n` 通过。

### 3.3 `server/manage-linux.py`

- `DEFAULTS` 的 `host` 改为 `127.0.0.1`
- 新增常量：

```python
MIN_PASSWORD_LENGTH = 8
WEAK_PASSWORDS = {'password', 'passw0rd', '12345678', '123456789', '87654321',
                  'qwertyui', 'abcdefgh', '11111111', '88888888', 'heartapp'}
PASSWORD_PLACEHOLDER = 'validate-only'   # 校验其它字段时的占位密码
```

- `validate()` 增加三条密码规则：长度 ≥ 8、不能全同字符、不在弱密码表
- `read_number()` 改用 `PASSWORD_PLACEHOLDER`（原来用 `'check'`，5 位，会被新规则拒绝）
- 向导在非环回监听时打印明文风险提示

`py_compile` 通过。

---

## 4. 必须补但还没改（现在的状态跑 CI 会失败）

### 4.1 测试断言

| 文件 | 位置 | 现状 | 应改为 |
|---|---|---|---|
| `tests/test_rooms.py` | 第 168 行 | `assertIn(b"relay v2 OK", body)` | `assertEqual(body.strip(), b"OK")` |
| `tools/server_panel_smoke_test.py` | 第 89 行 | `assert b"relay v2 OK" in response.read()` | `assert response.read().strip() == b"OK"` |

### 4.2 `tests/test_linux_manager.py` 的短密码

新密码策略要求 ≥ 8 位，而这个测试里全是短密码，会全部失败：

| 行 | 现值 | 建议改为 |
|---|---|---|
| 51 | `side_effect=['new', 'new']` | `['new-pass-1', 'new-pass-1']` |
| 59 | `['one', 'two', 'three', 'three']` | `['one-pass-1', 'two-pass-1', 'three-pass', 'three-pass']` |
| 62 | `assertEqual(..., 'three')` | `'three-pass'` |
| 66 | `password='preserved'` | `'preserved-pw'` |
| 90 | `password='before'` | `'before-pw'` |
| 91 | `(('n', 'before'), ('y', 'after'))` | `(('n', 'before-pw'), ('y', 'after-pw'))` |
| 93 | `side_effect=['after', 'after']` | `['after-pw', 'after-pw']` |
| 101 | `password='test'` | `'test-pass'` |
| 83 | `password='after'`（atomic 写失败测试） | `'after-pw'` |

另外建议**新增一条测试**：`validate()` 对 `'1234567'`（7 位）、`'aaaaaaaa'`、`'password'` 抛 `ValueError`。

### 4.3 文档里的 healthz 期望输出

```
README.md:89              # HeartApp relay v2 OK   →  # OK
server/Linux说明.md:155   健康检查应返回 `HeartApp relay v2 OK` → 返回 `OK`
server/Docker说明.md:130  健康检查返回 `HeartApp relay v2 OK` 后 → 返回 `OK` 后
```

Dockerfile 里的 `HEALTHCHECK` 用的是 `curl --fail`（只看状态码），不受影响。

---

## 5. 验证步骤（接手后按顺序跑）

**先修环境**（见 §7）：`.venv` 现在是空的 Python 3.12，依赖没装。

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.venv\Scripts\python.exe run_tests.py                # 目前 187 项
.venv\Scripts\python.exe smoke_test.py
.venv\Scripts\python.exe tools\qt_smoke_test.py
.venv\Scripts\python.exe tools\room_smoke_test.py     # 改了 relay，必须跑
.venv\Scripts\python.exe tools\server_panel_smoke_test.py
```

服务端行为可单独手工验证：

```powershell
$env:HEARTAPP_RELAY_PASSWORD = 'test-password'
.venv\Scripts\python.exe -m heartapp.relay --host 0.0.0.0 --port 18799        # 应报错退出
.venv\Scripts\python.exe -m heartapp.relay --host 0.0.0.0 --port 18799 --allow-plaintext  # 应正常
.venv\Scripts\python.exe -m heartapp.relay --host 127.0.0.1 --port 18799      # 应正常
```

安装脚本语法：`bash -n server/install-linux.sh`。
Linux 侧完整验证只能在真机上做（见 §6）。

---

## 6. 发布影响

`install-linux.sh` 和 `manage-linux.py` 都改了，**必须重建 Linux / Docker 分发包**，
否则用户装到的还是旧默认值。

```powershell
# 重建两个 tar 包（不需要重新跑 PyInstaller）
@'
import sys; sys.path.insert(0, "tools")
import build_relay
target = "dist/release"
for build in (build_relay.build_linux_tarball, build_relay.build_docker_tarball):
    src = build()
    print("更新", src.name)
'@ | .\.venv\Scripts\python.exe -
```

然后：重新生成 `dist/release/SHA256SUMS.txt` → 上传附件。

**建议直接发 v1.4.1**，理由见 §2 末尾。

---

## 7. 环境事故记录（2026-11-12）

本机 `.venv` 的原基座是 **Microsoft Store 版 Python 3.9**：

```
C:\Users\<用户名>\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.9_qbz5n2kfra8p0
```

这个目录**已经不存在**了（Store 应用被移除或更新），于是所有 `.venv\Scripts\python.exe` 调用都报：

```
No Python at 'C:\Users\??\AppData\Local\Microsoft\WindowsApps\...\python.exe'
```

注意错误信息里的 `??`：非 ASCII 用户名在解析失败时被替换，容易误判成编码问题，实际是**基座不见了**。

处理：已用 `py -3.12 -m venv .venv` 重建（Python 3.12.0），但依赖安装被中断，
**PySide6 / bleak / websockets / pyinstaller 都还没装**。接手第一件事就是装依赖。

---

## 8. 后续设计建议（P1 / P2，尚未实现）

### P1：向导引导部署模式

安装向导第一问改成三选一，避免用户自己判断：

```
1) 仅局域网 / 本机      → 绑定 0.0.0.0，允许明文（需确认）
2) 公网 + 已有 HTTPS 反代 → 绑定 127.0.0.1，自动生成 nginx/Caddy 片段
3) 公网 + 本机直接 TLS  → 输入证书路径（或域名 + ACME）
```

选项 2 直接把配置写到 `/opt/heartapp-relay/nginx-snippet.conf` 并打印下一步。

### P2：让客户端知情

服务端在 welcome 里带 `tls: true/false`，客户端联机面板显示
**「⚠ 此服务器未加密」**。这是最诚实的做法——无法保证别人的服务端配好了 TLS。

### P3：协议层加固（要改协议版本）

- **challenge-response 替代明文密码**：服务端发 nonce，客户端回 `HMAC(password, nonce)`。
  密码不再明文过网，但心率仍是明文，治标不治本，要配合 TLS 才有意义。
- **全局失败限速**：现在是「每 IP 每分钟 30 次」，分布式 IP 可绕过；可加全局面冷却。

---

## 9. 服务端现有防护（供评估时参考，别重复造）

| 防护 | 位置 |
|---|---|
| 单条消息 ≤ 2 KiB，队列 4，写限 16 KiB | `relay.py` `serve(...)` |
| 每连接 2 秒内最多 8 条消息 | `lobby.py` |
| 每 IP 每分钟最多 30 次连接尝试 | `lobby.py` `attempts` |
| 密码 `hmac.compare_digest` 比对 | `lobby.py` |
| `origins=[None]`：只接受非浏览器客户端 | `relay.py` |
| websockets 日志压到 CRITICAL，不记房间码/心率 | `relay.py` |
| systemd：`NoNewPrivileges`、`ProtectSystem=strict`、`ProtectHome`、`RestrictAddressFamilies` | `install-linux.sh` |
| `/opt/heartapp-relay` 归 root 所有，服务用户只读执行 | `install-linux.sh`（`9ac959d` 前一提交修的） |
| 不保存任何心率历史 | `lobby.py` |

---

## 10. 文件索引

| 文件 | 作用 |
|---|---|
| `docs/handoff/server-security-p0.patch` | 未提交改动的完整 diff（8.1 KB） |
| `heartapp/relay.py` | 服务端入口：healthz、TLS、启动校验 |
| `server/install-linux.sh` | Linux 安装器：默认绑定、systemd 单元生成 |
| `server/manage-linux.py` | 安装向导与管理菜单：密码策略、配置读写 |
| `server/deploy.sh` | 一行安装：镜像回退、版本解析、下载校验 |
| `server/README.md` | 部署文档（含「连不上 GitHub 时」） |
| `tools/build_relay.py` | 打包 Linux/Docker 分发包（`.sh` 转 LF） |
| `tools/build_release.py` | 一键构建全部发布件、签名、写 `server/LATEST` |
