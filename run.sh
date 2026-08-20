#!/usr/bin/env bash
set -e

echo "========================================================="
echo "       Starting Remote Desktop Host Daemon...            "
echo "========================================================="
echo ""

# Auto-setup on first launch if .venv is missing
if [ ! -f ".venv/bin/python" ]; then
    echo "[*] First-time launch: Installing dependencies..."
    chmod +x setup.sh
    ./setup.sh
fi

.venv/bin/python -m server.cli "$@"
