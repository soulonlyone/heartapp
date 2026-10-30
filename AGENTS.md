# AGENTS.md — 给 AI 协作者的开发指南

这个文件写给**接手本项目的 AI 助手**。README.md 面向最终用户，这里讲工程细节、
约定和坑。开始改代码前请完整读一遍。

---

## 1. 这个项目是什么

产品方向：PC 游戏 / 工作期间的心率监控，以会话、基线和手动事件时间轴为核心，
不推断情绪。设计依据见 [docs/pc-monitor-design.md](docs/pc-monitor-design.md)。
Qt 为新交互入口，Tk 保留旧版兼容界面。

Windows 桌面工具：通过**蓝牙 BLE** 从智能手表实时读取心率并记录到 SQLite，
支持历史查询、区间统计、CSV 导出。成品是免安装 exe（PyInstaller 打包）。

- 手表侧要求：支持标准 **Heart Rate Service `0x180D`**，特征 `0x2A37`（心率广播）
- **不绑定品牌**：OPPO / 华为 / 小米 / 三星 / Garmin 都可以，只看是否广播标准心率
- 开发对照设备：OPPO Watch X2（`AA:BB:CC:DD:EE:FF`）

## 2. 技术栈

| 层 | 选择 | 说明 |
|---|---|---|
| 语言 | Python 3.9 | venv 在 `.venv/`（使用项目独立虚拟环境；已在 Python 3.12 验证 bleak 1.1.1） |
| BLE | `bleak` 1.1.x + `winrt-*` 3.2.1 | 异步 API |
| 存储 | 标准库 `sqlite3` | 无 ORM |
| GUI | **PySide6-Essentials 6.10.x**（Qt 6） | 主界面 |
| GUI 兜底 | Tkinter | 仅当 PySide6 导入失败时降级，功能等价但视觉差 |
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
    ├── qt/        ★ 主界面（Qt）
    │   ├── theme.py    设计令牌：色板/字阶/间距/圆角/背景，无业务逻辑
    │   ├── widgets.py  自绘控件：卡片、分段控件、按钮、表盘、图表、区间条
    │   └── app.py      主窗口、设置对话框、事件泵
    ├── theme.py   Tk 版令牌（兜底）
    ├── widgets.py Tk 版控件
    └── app.py     Tk 版主窗口
```

**分层纪律**：业务逻辑（hr/metrics/store/engine）**不得** import 任何 UI 模块。
两个 UI 共享同一套逻辑层，改逻辑时两边都要能跑。

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
# 跑全部单元测试（当前 124 个）
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

# 截图（改动界面后用来肉眼确认）
.venv\Scripts\python.exe tools\qt_screenshot_demo.py screenshot.png 30 live interval
```

**提交前必须**：

1. `run_tests.py` 全绿
2. `qt_smoke_test.py` 输出 `QT SMOKE PASS`
3. **改过错误处理的话**：人为制造一次失败，确认调用方不会被异常掀翻（见第 10 节）

只改文档时可以跳过。

## 6. UI 设计约定（很重要，别随手改配色）

> **⚠️ 界面视觉正在重新设计**，由 Codex 基于 GPT Image 的设计产物实现。
> 下面这一节描述的是**当前实现的既有约定**，用来读懂现有代码；
> 新的视觉方案以设计产物为准，**不要用这一节去否决新设计**。
> 重新设计时保持：`theme.py` 作为唯一令牌来源、控件自绘走 `QPainter`、
> 业务逻辑不依赖 UI。

当前风格是**克制的精密感**（Apple Health / Linear 那一类），不是彩色玻璃。

核心原则：**一张页面上只允许一个亮点**。

- 页面：近中性冷灰渐变（几乎无色相），只有极淡的光晕
- 卡片：纯白 + 1px 发丝描边 + 几乎看不见的投影（`theme.paint_card`）
- 强调色 `ACCENT #C9184A`：**只**用于心率曲线、当前区间标签、记录状态、停止按钮
- 心率大字用**墨色** `INK`，不用彩色
- 文字四级灰阶：`INK` / `INK_2` / `INK_3` / `INK_4`
- 间距统一取 `theme.SPACE` 里的 4 的倍数
- 所有绘制定制走 `QPainter` + 抗锯齿

**换主题**：只改 `heartapp/ui/qt/theme.py` 顶部的常量，不要改控件代码。
（曾经尝试过高饱和渐变 + 重磨砂玻璃，用户明确反馈"土"，别走回头路。）

### Qt 特有的坑

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
- PyInstaller `--exclude-module`：GUI 版排除 `tkinter`，CLI 版排除 `tkinter`
  + `PySide6` + `shiboken6`（否则 CLI 也有 37MB）

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

### 改错误处理时的自查清单

- [ ] 这个异常会冒到哪一层？那一层挂掉后，界面和内部状态还一致吗？
- [ ] 失败瞬间有没有提前丢数据？（先 `try` 后清空，不是先清空后 `try`）
- [ ] 重试会不会产生重复数据？（事务 + 回滚，见 `store.add_samples`）
- [ ] 重试会不会把错误日志刷屏？（需要退避，见 `FLUSH_RETRY_SECONDS`）
- [ ] 有没有一个测试能让它**真的失败一次**？

## 11. 已知待办 / 可以改进的地方

- [ ] 深色主题（令牌结构已支持，只需加一套色值 + 切换开关）
- [ ] 多设备同时记录（engine 目前单源）
- [ ] 趋势图支持缩放/拖动
- [ ] 「本次记录」信息层级可再降密度（均值大字 + 小字详情）
- [ ] 打包体积优化（PySide6 裁掉不用的 Qt 模块，可省 10MB+）
- [ ] 真机长期稳定性验证（目前只在 OPPO Watch X2 上探针验证过服务存在）

## 12. 不要做的事

- 不要为了"好看"加高饱和渐变、彩色毛玻璃、大面积投影
- 不要在 GUI 线程里做 BLE 或数据库长操作
- **不要在采集循环里抛出未捕获的异常**（会杀死循环，留下僵尸状态）
- **不要只测成功路径就宣称改好了**（失败路径才是 bug 藏身处）
- 不要把心跳数据/用户路径写进示例或测试外的文件
- 不要删掉 Tk 兜底界面（有些环境装不上 PySide6）
- 不要在没有跑通两个冒烟测试的情况下声称"已完成"
