# HeartApp · PC 心率监控

用于 Windows 电脑上观察游戏、工作等活动中的心率变化。
从支持标准 BLE 心率服务的智能手表读取数据，保存在本机 SQLite 数据库。

## 使用流程

1. 点击右上角「连接手表」，手表开启心率广播后扫描并连接。
2. 点击「开始记录」，游戏期间自动持续保存心率，无需标记游戏事件。
3. 「显示小窗」打开置顶心率数字与短时曲线，Ctrl+Alt+H 随时隐藏或显示。
4. 「小窗」页可调整大小与透明度、开启鼠标穿透；拖动后自动记住位置。
5. 停止记录后，在「回看」选择整段曲线；滚轮缩放、拖动平移、悬停读数，双击恢复全程。
6. 导出 CSV 保存本次记录，或在历史页导出以往记录。

GUI 根据选定的 D 版数字海报制作：暖白底、大号衬线数字、朱红曲线、顶部文字导航。
超过 5 秒未更新的读数显示过期，缺失数据处曲线留空，不推断情绪或游戏事件。

## 设备与模拟

只支持标准 Heart Rate Service `0x180D` 和测量特征 `0x2A37`，不保证任意品牌或型号兼容。
扫描列表 ♥ 表示广播包包含心率服务；没有标记仍可尝试连接。
没有设备时开启「模拟模式」，选择 PC 游戏或工作演示；所有模拟记录会标注来源。
PC 会话中更换设备或模拟来源需要先结束记录。

置顶小窗适用于普通桌面和窗口化游戏。独占全屏、游戏对快捷键的占用以及真实手表
长时间稳定性仍需实测。快捷键注册失败会在界面提示，可继续使用按钮标记。

## 运行与测试

需要 Python 和项目依赖；在 Windows 上使用项目独立虚拟环境。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m heartapp
```

```powershell
.venv\Scripts\python.exe run_tests.py
.venv\Scripts\python.exe smoke_test.py
.venv\Scripts\python.exe tools\qt_smoke_test.py _test_output\pc.png
```

Qt 是默认界面，Tkinter 保留旧版兼容界面。PC 新流程目前在 Qt 中提供。
命令行仍支持 `list`、`probe`、`record`、`sessions` 和 `export`，使用 `--help` 查看参数。

## 数据与兼容

源码运行时数据在项目 `data/`，打包后在 exe 旁的 `data/`；CSV 使用 UTF-8 BOM。
旧版本数据库会添加 PC 会话和事件表，已有记录继续可读。旧版事件数据保留，
新界面不要求手动标记，不采集键盘内容、游戏进程或前台应用信息。

[产品与架构设计](docs/pc-monitor-design.md) · [开发指南](AGENTS.md) ·
[代码签名](docs/签名说明.md) · [历史版本](https://github.com/soulonlyone/heartapp/releases)

现有 Release 是旧版产物；源码改造不代表已发布新版 EXE。
打包入口：`.venv\Scripts\python.exe tools\build_exe.py`。
