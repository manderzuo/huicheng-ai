@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"

title 汇城群控AI版 - USB 驱动修复

rem ============================================================
rem  自提权：需要管理员才能操作驱动
rem ============================================================
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 正在请求管理员权限...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

if not exist "fix-usb-driver.ps1" (
    echo [错误] 找不到 fix-usb-driver.ps1
    echo 请确保两个文件在同一个文件夹里。
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix-usb-driver.ps1"

endlocal
