@echo off
chcp 65001 >nul
title FishFleet 群控

set PYTHON=C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
set ROOT=%~dp0

cd /d "%ROOT%"

echo ============================================================
echo   FishFleet 智能群控
echo ============================================================
echo.

echo [1/2] 检查环境...
"%PYTHON%" -c "import fastapi, uvicorn, uiautomator2" 2>nul
if errorlevel 1 (
    echo   缺少依赖，正在安装...
    "%PYTHON%" -m pip install fastapi "uvicorn[standard]" websockets --quiet
)

echo [2/2] 启动服务...
echo.
echo   访问地址: http://127.0.0.1:8765
echo   按 Ctrl+C 停止
echo.

start "" "http://127.0.0.1:8765"

"%PYTHON%" -m fishfleet.web.server

pause
