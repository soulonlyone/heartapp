@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title HeartApp 联机转发服务 - 安装

echo ============================================================
echo   HeartApp 联机转发服务 · Windows 安装
echo ============================================================
echo.
echo   只复制服务端需要的 4 个文件，不需要装 Qt / BLE。
echo   安装位置：%~dp0app
echo.

rem ---- 1. 找 Python 3.9+ ----
set "PY="
py -3 -c "import sys; exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python -c "import sys; exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo   [x] 找不到 Python 3.9 或更高版本。
    echo       请先安装：https://www.python.org/downloads/windows/
    echo       安装时记得勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)
echo   Python: %PY%

rem ---- 2. 复制服务端代码 ----
if not exist "app\heartapp" mkdir "app\heartapp"
copy /y "..\heartapp\__init__.py"      "app\heartapp\__init__.py"      >nul || goto copyfail
copy /y "..\heartapp\room_protocol.py" "app\heartapp\room_protocol.py" >nul || goto copyfail
copy /y "..\heartapp\relay.py"         "app\heartapp\relay.py"         >nul || goto copyfail
copy /y "..\heartapp\lobby.py"         "app\heartapp\lobby.py"         >nul || goto copyfail
copy /y "requirements.txt"             "app\requirements.txt"          >nul || goto copyfail
echo   代码已复制到 app\

rem ---- 3. 虚拟环境与依赖 ----
if not exist ".venv\Scripts\python.exe" (
    echo   创建虚拟环境 …
    %PY% -m venv .venv
    if errorlevel 1 (
        echo   [x] 创建虚拟环境失败
        pause
        exit /b 1
    )
)
echo   安装依赖 websockets …
rem 限时 + 镜像回退：默认源不可达时不会无限挂起
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --timeout 20 --retries 1 -r "app\requirements.txt"
if errorlevel 1 (
    echo   默认源不可达，改用国内镜像重试（可能要几十秒）…
    ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --timeout 30 --retries 2 -r "app\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
)
if errorlevel 1 (
    echo.
    echo   [x] 依赖安装失败。可以手动装，指定镜像：
    echo       .venv\Scripts\python.exe -m pip install -r app\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    echo.
    pause
    exit /b 1
)

rem ---- 4. 防火墙（需要管理员）----
net session >nul 2>&1
if not errorlevel 1 (
    netsh advfirewall firewall delete rule name="HeartApp Relay 8765" >nul 2>&1
    netsh advfirewall firewall add rule name="HeartApp Relay 8765" dir=in action=allow protocol=TCP localport=8765 >nul
    if errorlevel 1 (echo   防火墙放行失败，可手动放行 TCP 8765) else (echo   已放行防火墙 TCP 8765 入站)
) else (
    echo   [提示] 非管理员运行，跳过了防火墙放行。
    echo          局域网其他机器连不上时，请用管理员运行本脚本，或手动放行 TCP 8765。
)

echo.
echo ============================================================
echo   安装完成
echo ============================================================
echo.
echo   前台运行（调试用）：
echo       run-relay.bat
echo       run-relay.bat 你的访问密码
echo.
echo   开机自启（管理员 PowerShell）：
echo       powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Install
echo       powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Status
echo       powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Remove
echo.
echo   客户端填写：ws://这台电脑的IP:8765
echo   公网使用请在前面加 HTTPS/WSS 反向代理。
echo.
pause
exit /b 0

:copyfail
echo   [x] 复制服务端代码失败：请确认本脚本在仓库的 server\ 目录里运行。
pause
exit /b 1
