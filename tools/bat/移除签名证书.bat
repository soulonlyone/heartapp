@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."
title 移除自签名证书

echo ============================================================
echo   移除 HeartApp 自签名证书
echo ============================================================
echo.
echo   会删除：
echo     - 受信任的根证书颁发机构里的 HeartApp 证书
echo     - 个人存储里的 HeartApp 私钥证书
echo.
echo   移除后 exe 会恢复显示「未知发布者」，并且不再自动签名。
echo   想重新签名，双击「给exe签名.bat」即可。
echo.
pause

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\remove_cert.ps1"

echo.
echo ============================================================
pause
endlocal
