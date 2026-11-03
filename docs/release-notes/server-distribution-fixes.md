# 服务端分发修复（待发布）

基于 main 的 `13d1b51`，修复服务端分发入口；协议仍为 v2，数据库结构不变。

- Docker 包内启动脚本明确使用 `-f server/Dockerfile`，构建上下文仍为解压后的根目录。
- 生成的 `build_and_run.sh` 固定为 UTF-8、LF，不随 Windows 打包环境变成 CRLF。
- Windows 计划任务改用带 UTF-8 BOM 的 PowerShell 启动器，保留中文路径和密码。
  密码按单引号字面量转义，避免 `%`、`$`、引号及反引号被重新解释；启动器保留服务退出码。
  卸载兼容清理旧版 `service-run.cmd`。密码文件的管理员 / SYSTEM 读取限制保持不变。

已有 Windows 自启部署更新源码后，需以管理员重新执行：

```powershell
powershell -ExecutionPolicy Bypass -File server/service-windows.ps1 -Install -Password "你的口令"
```

如原来使用自定义端口或连接上限，重新安装时也需传入原参数。
此修复不自动替换 GitHub 已发布的附件；Docker 分发包需重新构建后发布。

新增 `tests/test_server_distribution.py`：执行生成的 shell 脚本（Docker 使用命令桩），
检查 Dockerfile 路径、LF 和构建失败后停止；执行生成的 Windows 启动器，检查中文及特殊
字符密码、中文路径、参数、环境变量继承和子进程失败退出码。测试不注册任务、不改防火墙。
这些检查不能替代真实 Docker 镜像构建或 Windows SYSTEM 计划任务安装验收。

2026-11-03 本机验证：159 项测试全部通过（无失败、错误或跳过），后端、Qt 界面及
双客户端联机冒烟均通过；PowerShell 语法、BOM/CRLF 和文档链接检查通过。
