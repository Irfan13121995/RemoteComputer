#!/usr/bin/env bash
set -e

echo "========================================================="
echo "   Remote Desktop Host - Environment Setup (Unix/macOS)  "
echo "========================================================="
echo ""

if command -v uv >/dev/null 2>&1; then
    echo "[*] Found 'uv' package manager. Setting up environment..."
    if [ ! -d ".venv" ]; then
        uv venv .venv --python 3.12 || uv venv .venv
    fi
    uv pip install --python .venv/bin/python -r requirements.txt
else
    echo "[*] 'uv' not detected. Checking for python3..."
    if command -v python3 >/dev/null 2>&1; then
        PY_CMD=python3
    else
        echo "[!] Error: python3 is not installed or not in PATH."
        exit 1
    fi

    if [ ! -d ".venv" ]; then
        echo "[*] Creating virtual environment (.venv)..."
        $PY_CMD -m venv .venv
    fi

    echo "[*] Installing requirements..."
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi

echo ""
echo "========================================================="
echo " [+] Setup completed successfully!"
echo " To start the host, run: ./run.sh"
echo "========================================================="
