@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 请先运行「启动心率记录仪.bat」准备环境。
    pause
    exit /b 1
)
echo 即将打开两个模拟玩家窗口和本机转发服务，不读取真实手表数据。
echo 玩家一点击「创建房间」，玩家二填写房间码后点击「加入房间」。
echo 两边勾选「共享我的实时心率」，即可看到对方；点击「显示联机小窗」查看浮窗。
echo 关闭两个窗口后，本机演示服务会自动停止。
".venv\Scripts\python.exe" tools\room_demo.py
if errorlevel 1 pause
endlocal
