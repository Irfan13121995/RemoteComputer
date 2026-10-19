@echo off
title RemoteDesk Pro v2.0 — Executable & Windows Setup Installer Builder
echo =====================================================================
echo   REMOTEDESK PRO v2.0 — WINDOWS APP ^& SETUP INSTALLER BUILDER
echo =====================================================================
echo.

echo [1/4] Stopping any running RemoteDeskPro instances...
powershell -NoProfile -Command "Stop-Process -Name RemoteDeskPro -Force -ErrorAction SilentlyContinue; Stop-Process -Name RemoteDeskPro-Setup -Force -ErrorAction SilentlyContinue"

echo [2/5] Generating high-resolution Windows application icons...
.\.venv\Scripts\python.exe assets\generate_icon.py

echo [3/5] Compiling signed Native Android App (dist\RemoteDeskPro-Android.apk)...
.\.venv\Scripts\python.exe android\build_apk.py

echo [4/5] Compiling standalone desktop application (dist\RemoteDeskPro.exe)...
.\.venv\Scripts\pyinstaller.exe RemoteDeskPro.spec --clean --noconfirm
if errorlevel 1 (
    echo [ERROR] Failed to build RemoteDeskPro.exe
    exit /b 1
)

echo [5/5] Compiling One-Click Windows Setup Installer (dist\RemoteDeskPro-Setup.exe)...
.\.venv\Scripts\pyinstaller.exe RemoteDeskProSetup.spec --clean --noconfirm
if errorlevel 1 (
    echo [ERROR] Failed to build RemoteDeskPro-Setup.exe
    exit /b 1
)

echo.
echo =====================================================================
echo   [SUCCESS] Build Complete!
echo   - Android Mobile App     : dist\RemoteDeskPro-Android.apk
echo   - Portable / Desktop App : dist\RemoteDeskPro.exe
echo   - Windows Setup Installer: dist\RemoteDeskPro-Setup.exe
echo =====================================================================
