@echo off
setlocal enabledelayedexpansion

echo =========================================================
echo    Remote Desktop Host - Environment Setup (Windows)
echo =========================================================
echo.

where uv >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [*] Found 'uv' package manager. Setting up environment...
    if not exist .venv (
        uv venv .venv --python 3.12
    )
    uv pip install --python .venv\Scripts\python.exe -r requirements.txt
) else (
    echo [*] 'uv' not detected. Checking for python/py...
    where python >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set PY_CMD=python
    ) else (
        where py >nul 2>nul
        if %ERRORLEVEL% equ 0 (
            set PY_CMD=py -3
        ) else (
            echo [!] Error: Python is not installed or not in PATH.
            echo Please install Python 3.10+ or 'uv' and try again.
            pause
            exit /b 1
        )
    )

    if not exist .venv (
        echo [*] Creating virtual environment (.venv)...
        %PY_CMD% -m venv .venv
    )
    echo [*] Installing requirements...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

echo.
echo =========================================================
echo  [+] Setup completed successfully!
echo  To start the host, run: run.bat
echo =========================================================
echo.

if "%1" neq "--no-pause" (
    pause
)
