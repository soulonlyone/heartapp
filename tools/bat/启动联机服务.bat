@echo off
rem Keep CRLF line endings for Windows cmd.exe.
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."
if not exist ".venv\Scripts\python.exe" (
    echo 请先运行「启动心率记录仪.bat」准备环境。
    pause
    exit /b 1
)
echo 本机地址：ws://127.0.0.1:8765
echo 局域网朋友请使用这台电脑的局域网 IP，端口 8765。
echo 按 Ctrl+C 停止服务。公网部署请先阅读 server\README.md。
".venv\Scripts\python.exe" -m heartapp.relay --host 0.0.0.0 --port 8765 --max-clients 20
pause
endlocal
