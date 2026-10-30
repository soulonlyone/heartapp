@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 心率记录仪 - 模拟演示

set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo.
    echo   还没准备好环境，先运行一次「启动心率记录仪.bat」。
    echo.
    pause
    exit /b 1
)

echo   以「模拟模式」启动：不需要手表，直接可以看到心率曲线和记录效果。
echo   想连真手表时，取消勾选界面上的「模拟模式」再点「扫描设备」。
echo.
"%PY%" -m heartapp --simulate

if errorlevel 1 (
    echo.
    echo   [!] 程序异常退出，错误信息见上方。
    echo.
    pause
)

endlocal
