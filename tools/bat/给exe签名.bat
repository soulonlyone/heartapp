@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."
title 给 exe 签名

echo ============================================================
echo   给 dist\ 下的 exe 打代码签名
echo ============================================================
echo.
echo   首次运行会生成一张自签名证书，并装进本机的
echo   「受信任的根证书颁发机构」——这样 exe 就不再显示
echo   「未知发布者」。
echo.
echo   注意：自签名只在本机有效。换台电脑仍然会提示未知发布者，
echo         想要全球信任必须购买商业代码签名证书。
echo.
pause

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\sign_exe.ps1"

echo.
echo ============================================================
pause
endlocal
