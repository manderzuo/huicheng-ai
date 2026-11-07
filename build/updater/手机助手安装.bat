@echo off
cd /d "%~dp0"
if not exist "%~dp0install-agents.ps1" (
  echo [X] 找不到 install-agents.ps1，请确认它和本文件在同一个目录。
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-agents.ps1" 2>&1
echo.
echo [完成] 上面是安装结果。按任意键关闭窗口。
pause >nul
