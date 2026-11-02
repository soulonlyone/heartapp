@echo off
rem Keep CRLF line endings for Windows cmd.exe.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 请先运行「启动心率记录仪.bat」准备环境。
    pause
    exit /b 1
)
echo 即将打开两个模拟玩家窗口和本机转发服务，不读取真实手表数据。
echo 点击主页「联机房间」，两边先登录服务器（演示无需密码）。
echo 玩家一创建房间，玩家二刷新列表选择房间加入，也可输入房间码。
echo 两边勾选「共享心率」，即可在主页对比；点击「显示小窗」查看浮窗。
echo 关闭两个窗口后，本机演示服务会自动停止。
".venv\Scripts\python.exe" tools\room_demo.py
if errorlevel 1 pause
endlocal
