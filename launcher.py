"""Desktop executable launcher for RemoteDesk Pro.
Spawns background server daemon and auto-launches default web browser.
"""

import argparse
import io
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add root directory to sys.path for PyInstaller frozen mode
if getattr(sys, "frozen", False):
    app_dir = Path(sys._MEIPASS)
    sys.path.insert(0, str(app_dir))
else:
    app_dir = Path(__file__).resolve().parent

import uvicorn

from core.auth import AuthManager
from core.clipboard import ClipboardManager
from core.file_manager import FileManager
from core.input_handler import InputHandler
from core.screen import ScreenCapture
from core.system_diagnostics import SystemDiagnostics
from core.tls import generate_self_signed_cert, get_local_ips
from server.app import create_app


def print_banner(host: str, port: int, is_ssl: bool, pin: str, is_password: bool):
    """Print clean terminal banner with connection URLs and pairing code."""
    proto = "https" if is_ssl else "http"
    local_ips = get_local_ips()

    print("\n" + "=" * 65)
    print("        [+] REMOTEDESK PRO — STANDALONE DESKTOP HOST")
    print("=" * 65)
    print(f"  * Status         : Online & Running")
    print(f"  * Security (TLS) : {'Enabled (HTTPS/WSS)' if is_ssl else 'Disabled (HTTP/WS)'}")
    print(f"  * Web Access URL :")
    print(f"      - Localhost  : {proto}://localhost:{port}")
    for ip in local_ips:
        if ip not in ("127.0.0.1", "::1"):
            print(f"      - LAN        : {proto}://{ip}:{port}")

    print("-" * 65)
    if is_password:
        print(f"  [KEY] ACCESS PASSWORD: [Set via CLI]")
    else:
        print(f"  [PIN] PAIRING PIN    :  >>>  {pin}  <<<")
    print("-" * 65)
    print("  * Features:")
    print("     - Remote Desktop Control (Low Latency Video & Mouse/Keyboard)")
    print("     - Remote File Manager & Canvas Drag-and-Drop")
    print("     - Interactive Remote Terminal (PowerShell / Bash)")
    print("     - System Diagnostics, Task Manager & Live Chat")
    print("     - Auto-opening web client in your default browser...")
    print("=" * 65 + "\n")


def auto_open_browser(url: str, delay: float = 1.2):
    """Wait for server to bind port then open user's default browser."""
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"[!] Unable to launch browser automatically: {e}")


def main():
    """Main desktop launcher entrypoint."""
    parser = argparse.ArgumentParser(description="RemoteDesk Pro — Self-Hosted Remote Desktop")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host address to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument("--pin", type=str, default=None, help="Custom 6-digit pairing PIN")
    parser.add_argument("--password", type=str, default=None, help="Custom access password")
    parser.add_argument("--ssl", dest="ssl", action="store_true", default=False, help="Enable HTTPS/WSS (self-signed cert)")
    parser.add_argument("--no-ssl", dest="ssl", action="store_false", help="Disable HTTPS/WSS (default: HTTP)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically launch web browser")
    parser.add_argument("--fps", type=int, default=30, help="Target max FPS (default: 30)")
    parser.add_argument("--quality", type=int, default=70, help="Image compression quality (10-95, default: 70)")
    parser.add_argument("--format", type=str, default="webp", choices=["webp", "jpeg"], help="Frame format (webp/jpeg)")
    parser.add_argument("--scale", type=float, default=1.0, help="Resolution scale factor (0.25 - 1.0, default: 1.0)")
    parser.add_argument("--cert-dir", type=str, default="certs", help="Directory for TLS certificates")

    args = parser.parse_args()

    # Initialize Core Components
    auth_manager = AuthManager(pin=args.pin, password=args.password)
    screen_capture = ScreenCapture(format=args.format, quality=args.quality, scale=args.scale)
    input_handler = InputHandler()
    clipboard_manager = ClipboardManager()
    file_manager = FileManager()
    system_diagnostics = SystemDiagnostics()

    # Setup TLS if enabled
    ssl_certfile = None
    ssl_keyfile = None
    if args.ssl:
        try:
            ssl_certfile, ssl_keyfile = generate_self_signed_cert(cert_dir=args.cert_dir)
        except Exception as e:
            print(f"[Warning] Failed to generate TLS certificate: {e}. Falling back to plain HTTP.")
            args.ssl = False

    app = create_app(
        auth_manager=auth_manager,
        screen_capture=screen_capture,
        input_handler=input_handler,
        clipboard_manager=clipboard_manager,
        file_manager=file_manager,
        system_diagnostics=system_diagnostics,
        default_fps=args.fps,
    )

    pin_display = auth_manager.password if auth_manager.is_custom_password else auth_manager.pin
    print_banner(
        host=args.host,
        port=args.port,
        is_ssl=args.ssl,
        pin=pin_display or "******",
        is_password=auth_manager.is_custom_password,
    )

    # Spawn auto-open browser thread
    proto = "https" if args.ssl else "http"
    browser_url = f"{proto}://localhost:{args.port}"
    if not args.no_browser:
        t = threading.Thread(target=auto_open_browser, args=(browser_url,), daemon=True)
        t.start()

    # Run Uvicorn Server
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        ssl_certfile=ssl_certfile if args.ssl else None,
        ssl_keyfile=ssl_keyfile if args.ssl else None,
        log_level="info",
    )


if __name__ == "__main__":
    main()
