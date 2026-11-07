@echo off
cd /d "%~dp0"
if not exist "%~dp0update.ps1" (
  echo [X] 找不到 update.ps1。
  echo     请把更新压缩包里的“一键更新.bat”和“update.ps1”一起解压到这个目录，
  echo     再把压缩包改名成 update.zip，然后双击本文件。
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1" 2>&1
echo.
echo [完成] 上面是更新结果。按任意键关闭窗口。
pause >nul
