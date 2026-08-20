@echo off
setlocal enabledelayedexpansion

echo =========================================================
echo       Starting Remote Desktop Host Daemon...
echo =========================================================
echo.

if not exist .venv\Scripts\python.exe (
    echo [*] First-time setup detected. Installing dependencies automatically...
    call setup.bat --no-pause
)

REM Launch host with any custom arguments passed (e.g. run.bat --port 9000 --no-ssl)
.venv\Scripts\python.exe -m server.cli %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] Server exited with code %ERRORLEVEL%
    pause
)
