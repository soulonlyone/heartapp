# AGENTS.md — 给 AI 协作者的开发指南

这个文件写给**接手本项目的 AI 助手**。README.md 面向最终用户，这里讲工程细节、
约定和坑。开始改代码前请完整读一遍。

---

## 1. 这个项目是什么

产品方向：PC 游戏期间的实时心率小窗、本地持续记录和整段曲线回看，
不推断情绪。设计依据见 [docs/pc-monitor-design.md](docs/pc-monitor-design.md)。
界面只有 Qt 一种实现（D 版数字海报）。

Windows 桌面工具：通过**蓝牙 BLE** 从智能手表实时读取心率并记录到 SQLite，
支持历史查询、区间统计、CSV 导出。成品是免安装 exe（PyInstaller 打包）。

- 手表侧要求：支持标准 **Heart Rate Service `0x180D`**，特征 `0x2A37`（心率广播）
- **不绑定品牌**：OPPO / 华为 / 小米 / 三星 / Garmin 都可以，只看是否广播标准心率
- 开发对照设备：支持标准 BLE 心率广播的手表（不记录个人设备地址）

## 2. 技术栈

| 层 | 选择 | 说明 |
|---|---|---|
| 语言 | Python 3.9 | venv 在 `.venv/`（使用项目独立虚拟环境；已在 Python 3.12 验证 bleak 1.1.1） |
| BLE | `bleak` 1.1.x + `winrt-*` 3.2.1 | 异步 API |
| 存储 | 标准库 `sqlite3` | 无 ORM |
| GUI | **PySide6-Essentials 6.10.x**（Qt 6） | 主界面 |
| 打包 | PyInstaller 6.22 | 4 个产物 |

## 3. 目录结构与职责

```
heartapp/
├── hr.py          心率帧解析（0x2A37）—— 纯函数，最好测
├── metrics.py     区间划分(Z1-Z5)、统计、HRV、格式化
├── store.py       SQLite 持久化（会话/样本/设置），线程安全
├── source.py      数据源抽象基类
├── simulator.py   模拟数据源（无需真机即可开发，有 3 种场景）
├── ble.py         BLE 扫描/连接/订阅
├── engine.py      采集引擎：后台线程 + asyncio 事件循环 + queue 事件队列
├── paths.py       路径解析（区分源码运行 / exe 运行）
├── cli.py         命令行（probe / list / record / export / gui）
└── ui/
    └── qt/              ★ 主界面（Qt，D 版数字海报）
        ├── editorial.py        设计令牌 + 自绘控件（曲线 / 小窗 / 全局热键 / 调色板）
        ├── editorial_layout.py 实时 / 回看 / 小窗三页布局 + 连接对话框
        ├── chrome.py           无边框窗口按钮与缩放
        ├── room_panel.py       联机弹窗与多人小窗
        ├── lobby_panel.py      服务器登录 / 房间列表
        ├── comparison.py       多人同坐标曲线
        └── app.py              主窗口、事件泵、业务接线
```

**分层纪律**：业务逻辑（hr/metrics/store/engine）**不得** import 任何 UI 模块。
界面只有 Qt 一种实现，业务逻辑不得反向依赖它。

联机初版：`room_protocol.py` 为纯协议，`relay.py` 为独立 asyncio 入口，`lobby.py` 管理登录与房间，`room_client.py`
为独立网络线程；Qt 的 `room_panel.py` 提供主页联机弹窗与多人小窗，`comparison.py` 提供共同坐标轴。
`lobby_panel.py` 将服务器登录与房间列表分区；协议 v2 中退出房间不能断开服务器登录。
会话令牌仅存内存，断线后保留 30 秒重连席位与稳定身份；密码不落盘，重启软件重新登录。
`room_history.py` 仅缓存房间内最多 30 分钟曲线，退出清空；不把队友数据写入数据库。
服务端无需 Qt / BLE / 数据库。联机不依赖公共服务器；共享默认关闭，密码不落盘，日志不含房间码或心率。
协议变更需要版本检查；队友心率过期必须清空。联机改动还需跑 `tools/room_smoke_test.py`，
覆盖真实双客户端、共享开关、掉线重连、服务故障及本地保存独立性。

## 4. 并发模型（改代码前必须理解）

```
GUI 线程 ──poll_events()──> queue.Queue ──<─ engine 后台线程（asyncio loop）
   │                                          │
   └── 调用 engine.start_recording() 等 ──────> 投递到 loop（run_coroutine_threadsafe）
```

- 引擎在**独立线程**跑 asyncio，GUI 用 `QTimer` 每 100ms 调 `engine.poll_events()`
- GUI **绝不能**直接 await 或阻塞等待 BLE
- 事件是 `(kind, payload)` 元组：`sample` / `state` / `error` / `device` ...
- 状态属性（`state`、`last_error`）可随时读，**不要**从事件队列里捞状态（会消费掉别人要用的）

## 5. 常用命令

```powershell
# 跑全部单元测试（当前 171 个）
.venv\Scripts\python.exe run_tests.py

# Qt 界面端到端冒烟（自动操作，不需要人看；必须看到 QT SMOKE PASS）
.venv\Scripts\python.exe tools\qt_smoke_test.py

# 后端全链路冒烟
.venv\Scripts\python.exe smoke_test.py

# 启动界面（模拟数据，不需要真表）
.venv\Scripts\python.exe -m heartapp --simulate

# 命令行探针
.venv\Scripts\python.exe -m heartapp list
.venv\Scripts\python.exe -m heartapp probe "设备名"

# 重新打包 4 个 exe
.venv\Scripts\python.exe tools\build_exe.py

# 界面截图（只用合成数据，改动界面后用来肉眼确认；生成物在 _test_output/）
.venv\Scripts\python.exe tools\d_gui_preview.py
.venv\Scripts\python.exe tools\room_comparison_preview.py
.venv\Scripts\python.exe tools\lobby_preview.py
```

**提交前必须**：

1. `run_tests.py` 全绿
2. `qt_smoke_test.py` 输出 `QT SMOKE PASS`
3. **改过错误处理的话**：人为制造一次失败，确认调用方不会被异常掀翻（见第 10 节）

只改文档时可以跳过。

## 6. UI 设计约定（很重要，别随手改配色）

当前 GUI 按用户选定的 **D 版数字海报**实现，参考 `docs/design/reference-d.png`。

- 暖白画布 `#FBF9F4`、墨色衬线数字、朱红 `#F13D2B` 数据曲线，细线分隔。
- 顶部文字导航：实时 / 回看 / 小窗；单一连续画布，不恢复卡片仪表盘布局。
- 操作按钮统一为 36px 高的胶囊形，非编辑下拉使用 CapsuleCombo，保持箭头与弹出菜单风格一致。
- 主窗口无系统边框，`chrome.py` 提供内置窗口按钮、顶部拖动/双击、系统边缘缩放。
  关闭按钮必须调用主窗口 `close()` 走保存保护；不要直接退出 QApplication。
- Qt 新界面绘制在 `editorial.py`，页面构建在 `editorial_layout.py`，事件与业务在 `app.py`。
- 游戏小窗必须保留，手动事件标记与运动区间不出现在默认流程中。
- 配色和字体集中在 `editorial.py`，新增控件直接用它导出的令牌，不要再引入第二套主题。
- 曲线必须先在原始采样上识别缺失区间，再按屏幕像素压缩，避免长记录被画成断线。

### Qt 特有的坑

- 游戏小窗必须是无父窗口的独立顶层工具窗；Windows 会在主窗口最小化时隐藏它拥有的
  工具窗。主窗口保存 `_overlay` 引用并在退出时显式关闭，禁止重新添加父窗口。
- 圆角下拉弹窗：透明属性和无边框标志必须在首次显示前设置；QSS 的圆角不会裁剪
  原生窗口及子控件，需在 Show / Resize 时同步圆角 mask。菜单底色由 QListView 绘制，
  viewport 禁用自动填底；不要只在 `showPopup()` 之后加透明属性（Windows 暗色模式会露黑角）。
- **调色板必须固定成浅色**：D 版是刻意的暖白纸面设计，`HeartAppWindow.__init__` 会调用
  `editorial.ensure_light_palette()` 把 Qt 调色板钉死。系统处于**深色模式**时 Qt 默认
  文字色是白色，而 QSS 把背景写死成 PAPER —— 任何「没显式设色」的控件都会变成
  白字白底。历史上 `QTableWidget` 就因为漏写 `color` 导致**整个历史列表不可见**。
  新增控件时：要么在 QSS 里写 `color`，要么依赖这套调色板（默认已覆盖）。
- 子控件**默认透明**，内容会自然叠在父控件绘制结果上 —— 这是特性，别去设背景色
- `QMessageBox` 会开**嵌套事件循环**并卡住自动化。测试/截图脚本必须先设
  `_Dialogs.quiet = True`
- 表格列宽要用 `header.setSectionResizeMode(i, Fixed)` 逐列设，光 `setColumnWidth` 不生效
- 分数缩放（150% DPI）下 `window.grab()` 出的图是 1.5 倍尺寸，属正常

## 7. 数据与隐私（红线）

- **心率是个人健康数据**，`data/heartrate.db` 已在 `.gitignore` 里，**绝不能提交**
- 数据目录在 exe / 源码旁边（`paths.app_dir()`），**不是** `_MEIPASS`
- 不要在任何日志里打印完整心率时间序列
- 签名私钥（`.pfx` / `*.cer` 私钥证书）绝不入库

## 8. 打包相关的坑

- 单文件版必须 `--runtime-tmpdir .`：用户 `%TEMP%` 的 ACL 被改坏了，
  PyInstaller 解压到系统临时目录会失败（`Could not create temporary directory`）
  → 改成解压到 exe 旁边
- 打包后要签名（`tools/sign_exe.ps1`，`build_exe.py` 会自动调）
- **`.ps1` 脚本必须存成 UTF-8 with BOM**：Windows PowerShell 5.1 读无 BOM 文件会按
  GBK 解释，中文全乱并破坏字符串引号。用编辑器保存时注意
- **`.bat` 工作区文件必须使用 CRLF 换行**：UTF-8 中文提示在 LF 文件中可能被 cmd.exe
  截断并当作命令执行。`.gitattributes` 已配置规则，新建或改写后仍需检查实际字节。
- **发布用 `tools/build_release.py`**：一个版本一个 Release，客户端（4 个）+ 服务端（3 个）
  + 服务端面板放在一起；签名必须在压缩之前
- PyInstaller `--exclude-module`：GUI 版排除 `tkinter`（已无代码引用，只为瘦身）；
  CLI 版还要排除 `PySide6` + `shiboken6`（否则 CLI 也有 37MB）

## 9. 代码风格

- 中文注释、中文 UI 文案、中文文档
- 类型注解齐全（`from __future__ import annotations`）
- 纯函数优先（`hr.py`、`metrics.py` 应该是可单测的纯函数）
- 异常处理要具体，不要 `except Exception: pass`
- 不要引入新依赖，除非确实必要（每加一个都会让 exe 变大）

## 10. 错误处理的铁律（血泪教训）

**改了任何 `try/except`，或者新增了 `raise`，必须人为制造失败跑一遍，
看清这个异常一路冒到哪、那一层挂掉之后系统处于什么状态。**

只看「这个异常在哪儿被捕获」是不够的。

### 真实案例：一次"改对了一半"的提交

有人给 `Engine._flush` 加了 `raise`，意图非常好——写库失败时让用户看到错误、
会话保持进行中、可以重试停止补写数据。停止记录这条路径上确实完全正确。

但 `_flush` 有**两个**调用方：

```
_stop_recording_impl()  → 抛错 ✅ 用户看到弹窗，重试即可
_on_sample()            → 抛错 ❌ 冒到 source.run() → 被 _runner 捕获
                             → 数据源任务结束 → 采集中断
                             → 但 _session_id 还在，界面仍显示「记录中」
```

用户的感受是：一条平稳的心率曲线突然变成直线，以为手表掉线了。

**正确做法**：让「要不要抛」成为参数，由调用方决定语义，而不是全局一个 `raise`。

```python
def _flush(self, *, raise_on_error: bool = False) -> None:
    ...
    except Exception as exc:
        self._flush_backoff_until = now + FLUSH_RETRY_SECONDS
        if raise_on_error:
            raise          # 用户主动操作：必须让他知道
        return             # 采集循环：只报错，绝不中断采集
```

### 三条规则

1. **区分「服务循环」和「用户主动操作」**
   循环里的异常绝不能逃出去（会杀死循环、留下"界面说在跑其实已停"的僵尸状态）；
   用户操作的异常应该逃出去（让用户知道失败了）。

2. **数据完整性优先于错误上报**
   写失败时先保住内存里的数据，再考虑怎么报错。
   顺序永远是「`try` 成功之后才清空缓存」，不是「先清空再 `try`」。

3. **测试必须覆盖失败路径，而不只是成功路径**
   冒烟测试永远造不出磁盘满、数据库锁这类错误，跑绿了也说明不了什么。

```python
from unittest.mock import patch
with patch.object(store, "add_samples", side_effect=OSError("disk full")):
    for _ in range(10):
        engine._on_sample(sample)      # 断言：不抛、缓存保留
```

退出约定：`Engine.shutdown()` 保存失败必须抛出并保留线程和缓存；Qt 使用
`request_shutdown()` 异步等待 `shutdown_ready` 后才关闭，`shutdown_failed` 则恢复窗口。
不要在 GUI 关闭回调里吞异常继续关库。独立定时保存不依赖新样本到达；数据提交使用 FULL 同步。

### 改错误处理时的自查清单

- [ ] 这个异常会冒到哪一层？那一层挂掉后，界面和内部状态还一致吗？
- [ ] 失败瞬间有没有提前丢数据？（先 `try` 后清空，不是先清空后 `try`）
- [ ] 重试会不会产生重复数据？（事务 + 回滚，见 `store.add_samples`）
- [ ] 重试会不会把错误日志刷屏？（需要退避，见 `FLUSH_RETRY_SECONDS`）
- [ ] 有没有一个测试能让它**真的失败一次**？

## 11. 已知待办 / 可以改进的地方

- [ ] 深色主题（令牌结构已支持，只需加一套色值 + 切换开关）
- [ ] 多设备同时记录（engine 目前单源）
- [x] 趋势图支持缩放/拖动
- [ ] 「本次记录」信息层级可再降密度（均值大字 + 小字详情）
- [ ] 打包体积优化（PySide6 裁掉不用的 Qt 模块，可省 10MB+）
- [ ] 真机长期稳定性验证（目前只在 OPPO Watch X2 上探针验证过服务存在）

合并 main 前另读 [docs/merge-handoff.md](docs/merge-handoff.md)，其中列出当前协议 v2、
验证结果、数据边界和发布前未完成项。仅补文档可沿用最近一次代码验证；合并后需重新验证。

## 12. 不要做的事

- 不要为了"好看"加高饱和渐变、彩色毛玻璃、大面积投影
- 不要在 GUI 线程里做 BLE 或数据库长操作
- **不要在采集循环里抛出未捕获的异常**（会杀死循环，留下僵尸状态）
- **不要只测成功路径就宣称改好了**（失败路径才是 bug 藏身处）
- 不要把心跳数据/用户路径写进示例或测试外的文件
- 演示、测试及截图只使用「玩家一 / 玩家二」等通用身份；不得从本机账户名、路径或对话推断真实姓名或昵称。
- 界面只剩 Qt 一种实现（Tk 兜底已于 2026-11 移除）；PySide6 装不上时程序只能退化为命令行
- 不要在没有跑通两个冒烟测试的情况下声称"已完成"

## 13. Windows 服务端面板

面板在 `ui/qt/server_panel.py`，纯逻辑控制器在 `server_control.py`。独立线程复用 Relay，
不导入采集引擎、BLE 或数据库。配置不保存密码；退出先停止本实例，不接管其他服务。
改动后还需运行 `tools/server_panel_smoke_test.py`。Linux/Docker 仍只运行无界面核心，无需 Qt。
