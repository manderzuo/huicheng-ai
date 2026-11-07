@echo off
cd /d "%~dp0"
if not exist "%~dp0彻底关闭.ps1" (
  echo [X] 找不到 彻底关闭.ps1，请确认它和本文件在同一个目录。
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0彻底关闭.ps1" 2>&1
echo.
echo [完成] 按任意键关闭窗口。
pause >nul
