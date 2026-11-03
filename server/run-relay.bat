@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo   [x] 还没安装，请先双击 install-windows.bat
    pause
    exit /b 1
)

rem 第一个参数作为访问密码；不传则沿用环境变量里的值（可为空）
if not "%~1"=="" set "HEARTAPP_RELAY_PASSWORD=%~1"

if defined HEARTAPP_RELAY_PASSWORD (
    echo   访问密码：已设置
) else (
    echo   访问密码：未设置（本机或纯局域网测试可以；公网请务必设置）
)

echo.
echo   监听 0.0.0.0:8765  关闭窗口或按 Ctrl+C 停止
echo.
cd /d "app"
"..\.venv\Scripts\python.exe" -m heartapp.relay --host 0.0.0.0 --port 8765 --max-clients 20 --room-size 4
if errorlevel 1 (
    echo.
    echo   [x] 服务退出，返回码 %errorlevel%
    pause
)
exit /b 0
