@echo off
cd /d "%~dp0"
if not exist "%~dp0配置视频网关.ps1" (
  echo [X] 找不到 配置视频网关.ps1，请确认它和本文件在同一个目录。
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0配置视频网关.ps1"
echo.
echo [完成] 按任意键关闭窗口。
pause >nul
