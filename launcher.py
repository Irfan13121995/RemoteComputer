"""Desktop executable launcher for RemoteDesk Pro v2.0.
Spawns background server daemon, LAN UDP Discovery (21116), Rendezvous Mediator,
Windows System Tray Agent, and Native Desktop App Window.
"""

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
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

from core.audio_service import AudioService
from core.auth import AuthManager
from core.clipboard import ClipboardManager
from core.config_store import ConfigStore
from core.discovery import LanDiscoveryService
from core.file_manager import FileManager
from core.input_handler import InputHandler
from core.privacy_mode import PrivacyModeManager
from core.rendezvous import RendezvousBroker, RendezvousClientMediator
from core.screen import ScreenCapture
from core.system_diagnostics import SystemDiagnostics
from core.tcp_tunnel import TcpTunnelManager
from core.tls import generate_self_signed_cert, get_local_ips
from core.tray_agent import SystemTrayAgent
from core.windows_integration import launch_native_app_window
from server.app import create_app


def print_banner(host: str, port: int, is_ssl: bool, machine_id: str, pin: str, is_password: bool, has_perm_pwd: bool):
    """Print clean terminal banner with connection URLs, Partner ID, and pairing code."""
    proto = "https" if is_ssl else "http"
    local_ips = get_local_ips()

    print("\n" + "=" * 68)
    print("        [+] REMOTEDESK PRO v2.0 — RUSTDESK-POWERED DESKTOP HOST")
    print("=" * 68)
    print(f"  * Status           : Online & Running (System Tray Active)")
    print(f"  * Partner ID       : >>>  {machine_id}  <<<")
    if is_password:
        print(f"  * One-Time PIN     : [Custom Password Set via CLI]")
    else:
        print(f"  * One-Time PIN     : >>>  {pin}  <<<")
    print(f"  * Unattended Pass  : {'Configured (Active)' if has_perm_pwd else 'Not Set (Configure in UI)'}")
    print(f"  * LAN Discovery    : Active (UDP Port 21116 + Rendezvous Broker)")
    print(f"  * Web / App URL    :")
    print(f"      - Localhost    : {proto}://localhost:{port}")
    for ip in local_ips:
        if ip not in ("127.0.0.1", "::1"):
            print(f"      - LAN          : {proto}://{ip}:{port}")
    print("=" * 68 + "\n")


def auto_open_window(url: str, use_native_window: bool = True, delay: float = 1.2):
    """Wait for server to bind port then open native desktop app window or browser."""
    time.sleep(delay)
    if use_native_window:
        launch_native_app_window(url)
    else:
        try:
            webbrowser.open(url)
        except Exception as e:
            print(f"[!] Unable to launch browser automatically: {e}")


def main():
    """Main desktop launcher entrypoint."""
    parser = argparse.ArgumentParser(description="RemoteDesk Pro v2.0 — Self-Hosted Remote Desktop Suite")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host address to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument("--pin", type=str, default=None, help="Custom 6-digit pairing PIN")
    parser.add_argument("--password", type=str, default=None, help="Custom access password")
    parser.add_argument("--ssl", dest="ssl", action="store_true", default=False, help="Enable HTTPS/WSS (self-signed cert)")
    parser.add_argument("--no-ssl", dest="ssl", action="store_false", help="Disable HTTPS/WSS (default: HTTP)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically launch desktop/browser window")
    parser.add_argument("--tray", action="store_true", help="Start minimized in Windows System Tray (used for Windows Startup)")
    parser.add_argument("--browser-mode", action="store_true", help="Open in standard browser tab instead of frameless native app window")
    parser.add_argument("--fps", type=int, default=30, help="Target max FPS (default: 30)")
    parser.add_argument("--quality", type=int, default=70, help="Image compression quality (10-95, default: 70)")
    parser.add_argument("--format", type=str, default="webp", choices=["webp", "jpeg"], help="Frame format (webp/jpeg)")
    parser.add_argument("--scale", type=float, default=1.0, help="Resolution scale factor (0.25 - 1.0, default: 1.0)")
    parser.add_argument("--cert-dir", type=str, default="certs", help="Directory for TLS certificates")

    args = parser.parse_args()

    # Initialize Core & RustDesk Subsystems
    config_store = ConfigStore()
    auth_manager = AuthManager(pin=args.pin, password=args.password, config_store=config_store)
    screen_capture = ScreenCapture(format=args.format, quality=args.quality, scale=args.scale)
    input_handler = InputHandler()
    clipboard_manager = ClipboardManager()
    file_manager = FileManager()
    system_diagnostics = SystemDiagnostics()
    privacy_manager = PrivacyModeManager()
    audio_service = AudioService()
    tunnel_manager = TcpTunnelManager()
    rendezvous_broker = RendezvousBroker()

    machine_id = auth_manager.get_machine_id()
    discovery_service = LanDiscoveryService(
        machine_id=machine_id,
        http_port=args.port,
        is_ssl=args.ssl,
    )
    net_cfg = config_store.get_network_config()
    discovery_service.enabled = bool(net_cfg.get("lan_discovery", True))
    discovery_service.start()

    rv_mediator = RendezvousClientMediator(
        local_broker=rendezvous_broker,
        machine_id=machine_id,
        http_port=args.port,
        is_ssl=args.ssl,
        rendezvous_url=net_cfg.get("rendezvous_server", ""),
        relay_key=net_cfg.get("relay_key", ""),
    )
    rv_mediator.start()

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
        config_store=config_store,
        discovery_service=discovery_service,
        rendezvous_broker=rendezvous_broker,
        privacy_manager=privacy_manager,
        audio_service=audio_service,
        tunnel_manager=tunnel_manager,
        default_fps=args.fps,
    )

    pin_display = auth_manager.password if auth_manager.is_custom_password else auth_manager.pin
    print_banner(
        host=args.host,
        port=args.port,
        is_ssl=args.ssl,
        machine_id=machine_id,
        pin=pin_display or "******",
        is_password=auth_manager.is_custom_password,
        has_perm_pwd=config_store.has_permanent_password(),
    )

    proto = "https" if args.ssl else "http"
    dashboard_url = f"{proto}://localhost:{args.port}"

    # Start Windows System Tray Agent (unless running headless test with --no-browser)
    if not args.no_browser or args.tray:
        tray = SystemTrayAgent(
            dashboard_url=dashboard_url,
            machine_id_getter=auth_manager.get_machine_id,
            pin_getter=lambda: auth_manager.pin or "",
            privacy_toggle_fn=lambda: privacy_manager.toggle(),
            on_quit_fn=lambda: os._exit(0),
        )
        tray.start()

    # Spawn auto-open window thread (unless --no-browser or --tray was passed)
    if not args.no_browser and not args.tray:
        use_native = not args.browser_mode
        t = threading.Thread(target=auto_open_window, args=(dashboard_url, use_native), daemon=True)
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
