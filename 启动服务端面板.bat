@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 请先按 server\Windows面板说明.md 创建虚拟环境并安装面板依赖。
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m heartapp.ui.qt.server_panel
if errorlevel 1 pause
endlocal
