@echo off
REM FishFleet 群控 CLI 启动器
REM 用法: fishfleetctl.bat <command> [options]
REM 例:   fishfleetctl.bat tap --all --x 540 --y 960 --verify

setlocal
set "PY=C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set "PROJ=%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%PROJ%"

"%PY%" -m fishfleet.ctl %*
exit /b %ERRORLEVEL%
