@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 心率记录仪

set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo.
    echo   首次运行，正在准备环境（只需这一次）...
    echo.
    where python >nul 2>nul
    if errorlevel 1 (
        echo   [x] 没找到 Python。请先安装 Python 3.9 或更高版本：
        echo       https://www.python.org/downloads/
        echo       安装时记得勾选 "Add Python to PATH"。
        echo.
        pause
        exit /b 1
    )
    python -m venv .venv
    if errorlevel 1 (
        echo   [x] 创建虚拟环境失败。
        pause
        exit /b 1
    )
    echo   正在安装依赖（需要联网）...
    "%PY%" -m pip install --upgrade pip
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   [x] 依赖安装失败。请检查网络后重试。
        pause
        exit /b 1
    )
    echo.
    echo   环境准备完成。
    echo.
)

echo   正在启动心率记录仪...
echo.
"%PY%" -m heartapp %*

if errorlevel 1 (
    echo.
    echo   [!] 程序异常退出，错误信息见上方。
    echo.
    pause
)

endlocal
