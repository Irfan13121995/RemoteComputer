@echo off
setlocal enabledelayedexpansion

echo =================================================================
echo       RemoteDesk Pro — Windows Executable Builder (.exe)
echo =================================================================
echo.

REM 1. Ensure Python Virtual Environment & PyInstaller
if not exist .venv\Scripts\python.exe (
    echo [*] Setting up environment...
    call setup.bat --no-pause
)

echo [*] Checking PyInstaller...
.venv\Scripts\python.exe -m pip install pyinstaller >nul 2>&1

REM 2. Generate application icon
echo [*] Generating icon assets...
.venv\Scripts\python.exe assets\generate_icon.py

REM 3. Run PyInstaller Build
echo.
echo [*] Compiling RemoteDeskPro.exe with PyInstaller...
echo     (This may take ~30-60 seconds depending on your CPU)
echo.

.venv\Scripts\pyinstaller.exe RemoteDeskPro.spec --clean --noconfirm

if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] Build failed! Check the error log above.
    pause
    exit /b 1
)

echo.
echo =================================================================
echo  [+] BUILD SUCCEEDED!
echo  Standalone Executable Location:
echo      dist\RemoteDeskPro.exe
echo.
echo  You can now copy 'dist\RemoteDeskPro.exe' to any Windows PC
echo  and run it directly with zero dependencies!
echo =================================================================
echo.

if "%1" neq "--no-pause" (
    pause
)
