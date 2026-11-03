@echo off
chcp 65001 >nul
setlocal

rem ---------------------------------------------------------------
rem  GitHub CLI 启动器（带代理）
rem
rem  用法：
rem    GitHub命令.bat auth login
rem    GitHub命令.bat repo create heartapp --private --source=. --push
rem    GitHub命令.bat repo view
rem ---------------------------------------------------------------

rem gh 依赖 Go 的 TLS，不走 Windows 系统代理，所以要显式给环境变量
set "HTTP_PROXY=http://127.0.0.1:7897"
set "HTTPS_PROXY=http://127.0.0.1:7897"

set "GH=%~dp0..\..\.tools\gh\bin\gh.exe"

if not exist "%GH%" (
    echo [x] 找不到 gh：%GH%
    echo     重新下载：用 python 拉取 cli/cli 的 windows_amd64.zip 到 .tools\gh
    pause
    exit /b 1
)

"%GH%" %*
exit /b %errorlevel%
