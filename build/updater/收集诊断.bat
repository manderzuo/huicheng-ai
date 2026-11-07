@echo off
cd /d "%~dp0"
if not exist "%~dp0collect_report.ps1" (
  echo [X] 找不到 collect_report.ps1，请确认它和本文件在同一个目录。
  echo.
  pause
  exit /b 1
)
echo 正在采集诊断信息，约需 1-2 分钟（40 台设备时更久）……
echo 本操作只读取，不修改任何东西。
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0collect_report.ps1" 2>&1
echo.
echo 采集完成，报告在桌面「汇城诊断报告」文件夹里。
echo 按任意键关闭窗口。
pause >nul
