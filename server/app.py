"""FastAPI application providing REST endpoints, static assets, WebSocket streaming,
Rendezvous (hbbs) & Relay (hbbr) services, LAN discovery, TCP tunneling, Privacy Mode, and remote terminal."""

import os
import platform
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.audio_service import AudioService
from core.auth import AuthManager
from core.clipboard import ClipboardManager
from core.config_store import ConfigStore
from core.discovery import LanDiscoveryService
from core.file_manager import FileManager
from core.input_handler import InputHandler
from core.internet_tunnel import InternetTunnelManager
from core.privacy_mode import PrivacyModeManager
from core.rendezvous import RendezvousBroker, RendezvousClientMediator
from core.screen import ScreenCapture
from core.system_diagnostics import SystemDiagnostics
from core.tcp_tunnel import TcpTunnelManager
from core.terminal_session import TerminalSession
from core.tls import get_local_ips
from core.windows_integration import get_windows_integration_status, set_autostart_enabled
from server.streamer import ClientSession


# Request Models
class LoginRequest(BaseModel):
    pin: Optional[str] = None
    password: Optional[str] = None


class SpecialActionRequest(BaseModel):
    action: str


class ClipboardRequest(BaseModel):
    text: str


class MonitorSelectRequest(BaseModel):
    index: int


class FileDeleteRequest(BaseModel):
    path: str


class FileMkdirRequest(BaseModel):
    parent: str
    name: str


class FileRenameRequest(BaseModel):
    path: str
    new_name: str


class PowerActionRequest(BaseModel):
    action: str


class AddressBookSaveRequest(BaseModel):
    entries: List[Dict[str, Any]]


class PermissionsUpdateRequest(BaseModel):
    allow_keyboard_mouse: Optional[bool] = None
    allow_clipboard: Optional[bool] = None
    allow_file_transfer: Optional[bool] = None
    allow_audio: Optional[bool] = None
    allow_tcp_tunnel: Optional[bool] = None
    allow_power_control: Optional[bool] = None
    allow_terminal: Optional[bool] = None
    privacy_mode_allowed: Optional[bool] = None
    unattended_enabled: Optional[bool] = None


class PermanentPasswordRequest(BaseModel):
    password: Optional[str] = None


class NetworkConfigRequest(BaseModel):
    rendezvous_server: Optional[str] = None
    relay_server: Optional[str] = None
    relay_key: Optional[str] = None
    lan_discovery: Optional[bool] = None
    direct_ip_access: Optional[bool] = None
    ip_whitelist: Optional[List[str]] = None


class RendezvousRegisterRequest(BaseModel):
    machine_id: str
    hostname: Optional[str] = "RemoteDesk-Host"
    os: Optional[str] = "Windows"
    ips: List[str] = []
    port: int = 8000
    ssl: bool = False
    relay_key: Optional[str] = ""
    public_url: Optional[str] = ""


class PrivacyToggleRequest(BaseModel):
    enabled: bool


class TunnelCreateRequest(BaseModel):
    name: Optional[str] = Field(default="", max_length=64)
    listen_port: int = Field(..., ge=0, le=65535)
    target_host: str = Field(default="127.0.0.1", pattern=r"^(127\.0\.0\.1|localhost)$")
    target_port: int = Field(..., ge=1, le=65535)
    listen_host: str = Field(default="127.0.0.1", pattern=r"^(127\.0\.0\.1|localhost)$")


class AutoStartRequest(BaseModel):
    enabled: bool


def create_app(
    auth_manager: AuthManager,
    screen_capture: Optional[ScreenCapture] = None,
    input_handler: Optional[InputHandler] = None,
    clipboard_manager: Optional[ClipboardManager] = None,
    file_manager: Optional[FileManager] = None,
    system_diagnostics: Optional[SystemDiagnostics] = None,
    config_store: Optional[ConfigStore] = None,
    discovery_service: Optional[LanDiscoveryService] = None,
    rendezvous_broker: Optional[RendezvousBroker] = None,
    privacy_manager: Optional[PrivacyModeManager] = None,
    audio_service: Optional[AudioService] = None,
    tunnel_manager: Optional[TcpTunnelManager] = None,
    internet_tunnel: Optional[InternetTunnelManager] = None,
    default_fps: int = 30,
) -> FastAPI:
    """Create and configure FastAPI application instance with full RustDesk feature suite."""
    app = FastAPI(title="RemoteDesk Pro Host", version="2.0.0")

    # Shared instances
    cfg = config_store or getattr(auth_manager, "config_store", None) or ConfigStore()
    if auth_manager.config_store is None:
        auth_manager.config_store = cfg

    screen = screen_capture or ScreenCapture()
    input_h = input_handler or InputHandler()
    clip = clipboard_manager or ClipboardManager()
    files = file_manager or FileManager()
    diagnostics = system_diagnostics or SystemDiagnostics()
    privacy = privacy_manager or PrivacyModeManager()
    audio = audio_service or AudioService()
    tunnels = tunnel_manager or TcpTunnelManager()
    rv_broker = rendezvous_broker or RendezvousBroker()
    discovery = discovery_service or LanDiscoveryService(machine_id=auth_manager.get_machine_id())

    # Register self in local rendezvous broker immediately
    rv_broker.register_host(
        machine_id=auth_manager.get_machine_id(),
        hostname=platform.node(),
        os_name=platform.system(),
        ips=get_local_ips(),
        port=discovery.http_port,
        is_ssl=discovery.is_ssl,
    )

    # CORS configuration (disable credentials on wildcard origins to prevent cross-site credentialed reads)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self' 'unsafe-inline'; "
            "connect-src 'self' ws: wss: http: https:;"
        )
        return response

    def is_proxied_request(request: Request) -> bool:
        """Return True if the request arrived via a reverse proxy / public SSH tunnel."""
        return any(
            h in request.headers
            for h in ("x-forwarded-for", "x-real-ip", "forwarded", "cf-connecting-ip")
        )

    def get_effective_client_ip(request: Request) -> str:
        """Resolve real client IP, honoring X-Forwarded-For when traffic arrives via local reverse tunnel."""
        raw_ip = request.client.host if request.client else "127.0.0.1"
        if raw_ip in ("127.0.0.1", "::1", "testclient"):
            xff = request.headers.get("x-forwarded-for") or request.headers.get("cf-connecting-ip") or request.headers.get("x-real-ip")
            if xff:
                return xff.split(",")[0].strip()
        return raw_ip

    def is_local_console_request(request: Request) -> bool:
        """Return True only if request originates directly from local machine (never over tunnel/proxy)."""
        raw_ip = request.client.host if request.client else ""
        return raw_ip in ("127.0.0.1", "::1", "testclient") and not is_proxied_request(request)

    def verify_auth_header(authorization: Optional[str] = Header(None)) -> bool:
        """Dependency to authenticate REST API requests."""
        if not authorization:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header")
        token = authorization.replace("Bearer ", "").strip()
        if not auth_manager.validate_session_token(token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session token")
        return True

    def check_permission(perm_key: str) -> None:
        """Raise 403 if the requested capability is disabled in host security permissions."""
        perms = cfg.get_permissions()
        if not perms.get(perm_key, True):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Host security policy has disabled '{perm_key}'.",
            )

    # ════════════════════════════════════════════════════════════════════════
    # AUTHENTICATION & HOST INFO
    # ════════════════════════════════════════════════════════════════════════

    @app.get("/api/host-info")
    async def get_host_info(request: Request, authorization: Optional[str] = Header(None)):
        """Public host information (Redacts plaintext PIN for non-local / reverse-proxied requests)."""
        monitors = screen.get_monitors()
        active_rect = screen.get_current_monitor_rect()
        pub_url = internet_tunnel.public_url if internet_tunnel else ""
        token = authorization.replace("Bearer ", "").strip() if authorization else ""
        can_view_pin = is_local_console_request(request) or auth_manager.validate_session_token(token)
        return {
            "machine_id": auth_manager.get_machine_id(),
            "pin": (auth_manager.pin if (can_view_pin and not auth_manager.is_custom_password) else "••••••"),
            "has_password": auth_manager.is_custom_password,
            "has_permanent_password": cfg.has_permanent_password(),
            "hostname": platform.node(),
            "os": platform.system(),
            "os_release": platform.release(),
            "port": discovery.http_port,
            "ssl": discovery.is_ssl,
            "monitors": monitors,
            "active_monitor": screen.monitor_index,
            "monitor_rect": active_rect,
            "local_ips": get_local_ips() if can_view_pin else [],
            "public_url": pub_url if can_view_pin else "",
            "internet_tunnel": (
                internet_tunnel.get_status()
                if (internet_tunnel and can_view_pin)
                else {"status": "disabled", "public_url": ""}
            ),
            "permissions": cfg.get_permissions(),
            "privacy_mode": privacy.get_status(),
            "windows_integration": get_windows_integration_status() if can_view_pin else {"is_windows": platform.system() == "Windows"},
            "version": "2.0.0",
            "status": "ready",
        }

    @app.post("/api/auth/login")
    async def login(req: LoginRequest, request: Request):
        """Authenticate with One-Time PIN or Unattended Permanent Password and receive session token."""
        client_ip = get_effective_client_ip(request)
        code = req.password or req.pin or ""

        success, token, message = auth_manager.verify_credentials(code, client_ip=client_ip)
        if not success:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=message)

        return {
            "success": True,
            "token": token,
            "expires_in": auth_manager.token_ttl,
            "message": message,
            "machine_id": auth_manager.get_machine_id(),
            "permissions": cfg.get_permissions(),
        }

    @app.post("/api/auth/regenerate-pin")
    async def regenerate_pin(authorized: bool = Depends(verify_auth_header)):
        """Regenerate session PIN."""
        new_pin = auth_manager.regenerate_pin()
        return {"success": True, "pin": new_pin}

    @app.get("/api/status")
    async def get_status(token: Optional[str] = Query(None), authorization: Optional[str] = Header(None)):
        """Check host status, OS info, and active display bounds."""
        auth_token = token or (authorization.replace("Bearer ", "").strip() if authorization else None)
        if not auth_manager.validate_session_token(auth_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        monitors = screen.get_monitors()
        active_rect = screen.get_current_monitor_rect()

        return {
            "status": "online",
            "os": platform.system(),
            "os_release": platform.release(),
            "hostname": platform.node(),
            "machine_id": auth_manager.get_machine_id(),
            "active_monitor": screen.monitor_index,
            "monitors": monitors,
            "monitor_rect": active_rect,
            "default_fps": default_fps,
            "format": screen.format,
            "quality": screen.quality,
            "permissions": cfg.get_permissions(),
            "privacy_mode": privacy.get_status(),
        }

    # ════════════════════════════════════════════════════════════════════════
    # RUSTDESK LAN PEER DISCOVERY & RENDEZVOUS (HBBS / HBBR)
    # ════════════════════════════════════════════════════════════════════════

    @app.get("/api/discovery/peers")
    async def get_discovered_peers():
        """List all RemoteDesk Pro peers discovered on the local network."""
        peers = discovery.get_discovered_peers(include_self=True)
        return {"peers": peers, "count": len(peers)}

    @app.get("/api/discovery/resolve")
    async def resolve_partner_id(partner_id: str = Query(...)):
        """
        Resolve a 9-digit Partner ID (e.g. '360 523 800') via:
        1. Local LAN UDP Discovery Table + Concurrent /24 HTTP Subnet Sweep
        2. Built-in Rendezvous (hbbs) Registry + Global Cloud Rendezvous (Cross-Network Tunnel)
        """
        is_self = discovery.normalize_id(partner_id) == discovery.normalize_id(auth_manager.get_machine_id())
        # 1. Check LAN Discovery (UDP cache + fast /24 HTTP subnet sweep)
        lan_url = discovery.resolve_partner_id(partner_id)
        if lan_url:
            return {
                "found": True,
                "is_self": is_self,
                "source": "lan_discovery",
                "url": lan_url,
                "partner_id": partner_id,
            }

        # 2. Check Rendezvous Broker (Local + Global Cloud Rendezvous with LAN vs. Public Tunnel probe)
        resolved = rv_broker.resolve_best_url(partner_id)
        if resolved and resolved.get("url"):
            return {
                "found": True,
                "is_self": is_self,
                "source": resolved.get("source", "rendezvous_server"),
                "url": resolved["url"],
                "partner_id": partner_id,
                "host": resolved.get("record"),
            }

        return {"found": False, "is_self": is_self, "partner_id": partner_id, "url": None}

    @app.post("/api/rendezvous/register")
    async def rendezvous_register(req: RendezvousRegisterRequest, request: Request):
        """Rendezvous (hbbs) endpoint: Register or heartbeat a host Machine ID."""
        client_ip = request.client.host if request.client else "127.0.0.1"
        record = rv_broker.register_host(
            machine_id=req.machine_id,
            hostname=req.hostname or "RemoteDesk-Host",
            os_name=req.os or "Windows",
            ips=req.ips,
            port=req.port,
            is_ssl=req.ssl,
            relay_key=req.relay_key or "",
            public_ip=client_ip,
            public_url=req.public_url or "",
        )
        return {"success": True, "registration": record}

    @app.get("/api/rendezvous/lookup/{machine_id}")
    async def rendezvous_lookup(machine_id: str):
        """Rendezvous (hbbs) endpoint: Lookup connection details for a 9-digit Partner ID."""
        record = rv_broker.lookup_host(machine_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Partner ID not registered")
        return record

    @app.get("/api/rendezvous/peers")
    async def rendezvous_list_peers():
        """List all hosts registered with this Rendezvous server."""
        peers = rv_broker.list_registered_hosts()
        return {"peers": peers, "count": len(peers)}

    @app.websocket("/ws/relay/{channel_id}")
    async def websocket_relay(
        websocket: WebSocket,
        channel_id: str,
        role: str = Query("client"),
        token: Optional[str] = Query(None),
        relay_key: Optional[str] = Query(None),
    ):
        """Relay (hbbr) endpoint: Bridge host and client WebSockets when behind symmetric NAT (requires auth token or relay_key)."""
        await websocket.accept()
        expected_key = cfg.get_network_config().get("relay_key", "")
        valid_token = auth_manager.validate_session_token(token)
        valid_key = bool(expected_key and relay_key and relay_key == expected_key)
        if not valid_token and not valid_key:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await rv_broker.bridge_relay_websocket(channel_id=channel_id, role=role, ws=websocket)

    # ════════════════════════════════════════════════════════════════════════
    # RUSTDESK GRANULAR SECURITY, PERMANENT PASSWORD, AUDIT LOG & PRIVACY MODE
    # ════════════════════════════════════════════════════════════════════════

    @app.get("/api/security/permissions")
    async def get_permissions():
        """Return granular session permissions and unattended status."""
        return {
            "permissions": cfg.get_permissions(),
            "has_permanent_password": cfg.has_permanent_password(),
            "network": cfg.get_network_config(),
        }

    @app.post("/api/security/permissions")
    async def update_permissions(req: PermissionsUpdateRequest, authorized: bool = Depends(verify_auth_header)):
        """Update granular session permissions (Keyboard/Mouse, Clipboard, File Transfer, Audio, Tunneling, Power)."""
        updates = {k: v for k, v in req.model_dump().items() if v is not None}
        updated = cfg.update_permissions(updates)
        cfg.add_audit_event("PERMISSIONS_UPDATED", details=str(updates))
        return {"success": True, "permissions": updated}

    @app.post("/api/security/permanent-password")
    async def set_permanent_password(req: PermanentPasswordRequest, authorized: bool = Depends(verify_auth_header)):
        """Set or clear the persistent Unattended Access Permanent Password."""
        has_pwd = cfg.set_permanent_password(req.password)
        cfg.add_audit_event("PERMANENT_PASSWORD_CHANGED", details="Set" if has_pwd else "Cleared")
        return {"success": True, "has_permanent_password": has_pwd}

    @app.post("/api/security/network")
    async def update_network_settings(req: NetworkConfigRequest, authorized: bool = Depends(verify_auth_header)):
        """Configure Rendezvous/Relay server URL, key, LAN discovery, and IP whitelist."""
        updates = {k: v for k, v in req.model_dump().items() if v is not None}
        net = cfg.update_network_config(updates)
        if "lan_discovery" in updates:
            discovery.enabled = bool(updates["lan_discovery"])
        return {"success": True, "network": net}

    @app.get("/api/security/sessions")
    async def list_active_sessions(authorized: bool = Depends(verify_auth_header)):
        """List currently connected remote sessions."""
        return {"sessions": auth_manager.list_active_sessions()}

    @app.post("/api/security/sessions/{session_id}/kick")
    async def kick_session(session_id: str, authorized: bool = Depends(verify_auth_header)):
        """Disconnect / revoke an active remote session."""
        revoked = auth_manager.revoke_session(session_id)
        if not revoked:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return {"success": True, "session_id": session_id}

    @app.get("/api/security/audit-log")
    async def get_audit_log(limit: int = Query(50), authorized: bool = Depends(verify_auth_header)):
        """Fetch recent security and connection audit events."""
        return {"events": cfg.get_audit_log(limit=limit)}

    @app.get("/api/privacy")
    async def get_privacy_status(authorized: bool = Depends(verify_auth_header)):
        """Get current Privacy Mode status."""
        return privacy.get_status()

    @app.post("/api/privacy")
    async def toggle_privacy_mode(req: PrivacyToggleRequest, authorized: bool = Depends(verify_auth_header)):
        """Enable or disable RustDesk-style Privacy Mode (screen curtain + physical input block)."""
        check_permission("privacy_mode_allowed")
        status_dict = privacy.set_privacy_mode(req.enabled)
        cfg.add_audit_event("PRIVACY_MODE", details=f"Privacy mode {'enabled' if req.enabled else 'disabled'}")
        return {"success": True, "privacy": status_dict}

    # ════════════════════════════════════════════════════════════════════════
    # RUSTDESK TCP TUNNELING / PORT FORWARDING
    # ════════════════════════════════════════════════════════════════════════

    @app.get("/api/tunnels")
    async def list_tunnels(authorized: bool = Depends(verify_auth_header)):
        """List active TCP port-forwarding tunnels."""
        return {"tunnels": tunnels.list_tunnels()}

    @app.post("/api/tunnels")
    async def create_tunnel(req: TunnelCreateRequest, authorized: bool = Depends(verify_auth_header)):
        """Create a new local-to-remote TCP port-forwarding tunnel."""
        check_permission("allow_tcp_tunnel")
        try:
            tunnel = tunnels.create_tunnel(
                listen_port=req.listen_port,
                target_host=req.target_host,
                target_port=req.target_port,
                listen_host=req.listen_host,
                name=req.name or "",
            )
            cfg.add_audit_event("TCP_TUNNEL_CREATED", details=f"{tunnel['listen_port']} -> {tunnel['target_host']}:{tunnel['target_port']}")
            return {"success": True, "tunnel": tunnel}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to bind tunnel port: {e}")

    @app.delete("/api/tunnels/{tunnel_id}")
    async def delete_tunnel(tunnel_id: str, authorized: bool = Depends(verify_auth_header)):
        """Stop and remove a TCP port-forwarding tunnel."""
        stopped = tunnels.stop_tunnel(tunnel_id)
        if not stopped:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tunnel not found")
        return {"success": True, "id": tunnel_id}

    # ════════════════════════════════════════════════════════════════════════
    # WINDOWS OS INTEGRATION (AUTO-START & STATUS)
    # ════════════════════════════════════════════════════════════════════════

    @app.get("/api/windows/integration")
    async def windows_status():
        """Get Windows desktop integration status (Auto-Start, Executable path)."""
        return get_windows_integration_status()

    @app.post("/api/windows/autostart")
    async def windows_autostart(req: AutoStartRequest, authorized: bool = Depends(verify_auth_header)):
        """Enable or disable starting RemoteDesk Pro automatically with Windows."""
        ok = set_autostart_enabled(req.enabled)
        return {"success": ok, "autostart_enabled": req.enabled}

    # ════════════════════════════════════════════════════════════════════════
    # MONITORS, INPUT & CLIPBOARD
    # ════════════════════════════════════════════════════════════════════════

    @app.get("/api/monitors")
    async def list_monitors(token: Optional[str] = Query(None), authorization: Optional[str] = Header(None)):
        """List attached monitors."""
        auth_token = token or (authorization.replace("Bearer ", "").strip() if authorization else None)
        if not auth_manager.validate_session_token(auth_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
        return {"monitors": screen.get_monitors()}

    @app.post("/api/monitors/select")
    async def select_monitor(req: MonitorSelectRequest, authorized: bool = Depends(verify_auth_header)):
        """Switch current display stream."""
        success = screen.set_monitor(req.index)
        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid monitor index")
        return {"success": True, "active_monitor": screen.monitor_index}

    @app.get("/api/clipboard")
    async def get_clipboard(authorized: bool = Depends(verify_auth_header)):
        """Fetch host clipboard contents."""
        check_permission("allow_clipboard")
        return {"text": clip.get_text()}

    @app.post("/api/clipboard")
    async def set_clipboard(req: ClipboardRequest, authorized: bool = Depends(verify_auth_header)):
        """Set host clipboard contents."""
        check_permission("allow_clipboard")
        success = clip.set_text(req.text)
        return {"success": success}

    @app.post("/api/action/special")
    async def special_action(req: SpecialActionRequest, authorized: bool = Depends(verify_auth_header)):
        """Trigger keyboard/system macros."""
        check_permission("allow_keyboard_mouse")
        success = input_h.trigger_special_action(req.action)
        return {"success": success, "action": req.action}

    # ════════════════════════════════════════════════════════════════════════
    # REMOTE FILE MANAGER (WITH FOLDER ZIP ARCHIVE DOWNLOAD)
    # ════════════════════════════════════════════════════════════════════════

    @app.get("/api/files/drives")
    async def get_drives(authorized: bool = Depends(verify_auth_header)):
        """Get storage partitions and user shortcut folders."""
        check_permission("allow_file_transfer")
        return files.get_drives_and_shortcuts()

    @app.get("/api/files/list")
    async def list_files(path: Optional[str] = Query(None), authorized: bool = Depends(verify_auth_header)):
        """List files in directory."""
        check_permission("allow_file_transfer")
        try:
            return files.list_dir(path)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @app.get("/api/files/desktop-path")
    async def get_desktop_path(authorized: bool = Depends(verify_auth_header)):
        """Get path to Host Desktop for dropzone upload."""
        return {"desktop_path": files.get_desktop_dir()}

    MAX_UPLOAD_BYTES = 250 * 1024 * 1024  # 250 MB limit to prevent memory exhaustion DoS

    @app.post("/api/files/upload")
    async def upload_file(
        target_dir: str = Form(...),
        file: UploadFile = File(...),
        authorized: bool = Depends(verify_auth_header),
    ):
        """Upload file to host folder with size cap and protected directory enforcement."""
        check_permission("allow_file_transfer")
        try:
            chunks = []
            total_read = 0
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total_read += len(chunk)
                if total_read > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Uploaded file exceeds 250 MB maximum limit.",
                    )
                chunks.append(chunk)
            content = b"".join(chunks)
            saved_path = files.save_uploaded_file(target_dir, file.filename or "upload.bin", content)
            cfg.add_audit_event("FILE_UPLOAD", details=f"Uploaded {file.filename} ({len(content)} bytes)")
            return {"success": True, "saved_path": saved_path, "filename": file.filename, "size": len(content)}
        except HTTPException:
            raise
        except PermissionError as e:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @app.get("/api/files/download")
    async def download_file(path: str = Query(...), token: Optional[str] = Query(None), authorization: Optional[str] = Header(None)):
        """Download file from host."""
        auth_token = token or (authorization.replace("Bearer ", "").strip() if authorization else None)
        if not auth_manager.validate_session_token(auth_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
        check_permission("allow_file_transfer")

        try:
            target = files.validate_path_access(path)
        except PermissionError as e:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
        if not target.exists() or target.is_dir():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

        return FileResponse(
            str(target),
            media_type="application/octet-stream",
            filename=target.name,
        )

    @app.get("/api/files/download-zip")
    async def download_folder_zip(path: str = Query(...), token: Optional[str] = Query(None), authorization: Optional[str] = Header(None)):
        """Download an entire folder compressed on-the-fly as a .zip archive."""
        auth_token = token or (authorization.replace("Bearer ", "").strip() if authorization else None)
        if not auth_manager.validate_session_token(auth_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
        check_permission("allow_file_transfer")

        try:
            zip_bytes, zip_name = files.create_folder_zip_bytes(path)
            return Response(
                content=zip_bytes,
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
            )
        except FileNotFoundError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @app.post("/api/files/delete")
    async def delete_file(req: FileDeleteRequest, authorized: bool = Depends(verify_auth_header)):
        """Delete file or directory."""
        check_permission("allow_file_transfer")
        success = files.delete_item(req.path)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
        return {"success": True, "path": req.path}

    @app.post("/api/files/mkdir")
    async def mkdir(req: FileMkdirRequest, authorized: bool = Depends(verify_auth_header)):
        """Create new directory."""
        check_permission("allow_file_transfer")
        try:
            created = files.create_dir(req.parent, req.name)
            return {"success": True, "path": created}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @app.post("/api/files/rename")
    async def rename_file(req: FileRenameRequest, authorized: bool = Depends(verify_auth_header)):
        """Rename file or directory."""
        check_permission("allow_file_transfer")
        try:
            renamed = files.rename_item(req.path, req.new_name)
            return {"success": True, "path": renamed}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # ════════════════════════════════════════════════════════════════════════
    # SYSTEM DIAGNOSTICS & TASK MANAGER
    # ════════════════════════════════════════════════════════════════════════

    @app.get("/api/system/telemetry")
    async def get_telemetry(authorized: bool = Depends(verify_auth_header)):
        """Fetch real-time CPU, RAM, Disk, Network metrics."""
        return diagnostics.get_telemetry()

    @app.get("/api/system/processes")
    async def get_processes(
        limit: int = Query(60),
        sort: str = Query("cpu"),
        authorized: bool = Depends(verify_auth_header),
    ):
        """Fetch list of running processes."""
        return {"processes": diagnostics.get_processes(limit=limit, sort_by=sort)}

    @app.post("/api/system/processes/{pid}/kill")
    async def kill_proc(pid: int, authorized: bool = Depends(verify_auth_header)):
        """Terminate a running process."""
        check_permission("allow_power_control")
        success = diagnostics.kill_process(pid)
        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to kill process {pid}")
        return {"success": True, "pid": pid}

    @app.post("/api/system/power")
    async def power_control(req: PowerActionRequest, authorized: bool = Depends(verify_auth_header)):
        """Execute power action (lock, sleep, restart, shutdown)."""
        check_permission("allow_power_control")
        success = diagnostics.execute_power_action(req.action)
        return {"success": success, "action": req.action}

    # ════════════════════════════════════════════════════════════════════════
    # ADDRESS BOOK (PERSISTED IN CONFIG STORE)
    # ════════════════════════════════════════════════════════════════════════

    @app.get("/api/addressbook")
    async def get_addressbook(authorized: bool = Depends(verify_auth_header)):
        """Get saved computers."""
        return {"entries": cfg.get_address_book()}

    @app.post("/api/addressbook")
    async def save_addressbook(req: AddressBookSaveRequest, authorized: bool = Depends(verify_auth_header)):
        """Save address book entries."""
        saved = cfg.save_address_book(req.entries)
        return {"success": True, "count": len(saved)}

    # ════════════════════════════════════════════════════════════════════════
    # ANDROID MOBILE APP DOWNLOAD & QR PAIRING
    # ════════════════════════════════════════════════════════════════════════

    def _find_android_apk() -> Optional[Path]:
        """Locate RemoteDeskPro-Android.apk in assets/ or dist/ (supports PyInstaller _MEIPASS)."""
        candidates: List[Path] = []
        if hasattr(sys, "_MEIPASS"):
            base = Path(sys._MEIPASS)
            candidates.append(base / "assets" / "RemoteDeskPro-Android.apk")
            candidates.append(base / "RemoteDeskPro-Android.apk")
        root = Path(__file__).resolve().parent.parent
        candidates.append(root / "dist" / "RemoteDeskPro-Android.apk")
        candidates.append(root / "assets" / "RemoteDeskPro-Android.apk")
        for c in candidates:
            if c.exists():
                return c
        return None

    @app.get("/api/mobile/qr-info")
    async def get_mobile_qr_info(request: Request, authorization: Optional[str] = Header(None)):
        """Return LAN & Public Internet Tunnel URLs for downloading the Android APK and instant QR pairing."""
        token = authorization.replace("Bearer ", "").strip() if authorization else ""
        can_view_pin = is_local_console_request(request) or auth_manager.validate_session_token(token)
        ips = get_local_ips()
        lan_ips = [ip for ip in ips if ip not in ("127.0.0.1", "::1")]
        primary_ip = lan_ips[0] if lan_ips else "127.0.0.1"
        scheme = "https" if discovery.is_ssl else "http"
        port = discovery.http_port
        lan_base_url = f"{scheme}://{primary_ip}:{port}"
        pub_url = (internet_tunnel.public_url.rstrip("/") if (internet_tunnel and internet_tunnel.public_url) else "")
        preferred_base = pub_url if pub_url else lan_base_url
        apk_path = _find_android_apk()
        pin_val = (auth_manager.pin if (can_view_pin and not auth_manager.is_custom_password) else "")
        connect_url = f"{preferred_base}/?autoconnect=1&mobile=1&show_desktop=1&pin={pin_val}" if can_view_pin else preferred_base
        lan_connect_url = f"{lan_base_url}/?autoconnect=1&mobile=1&show_desktop=1&pin={pin_val}" if can_view_pin else lan_base_url
        apk_url = f"{preferred_base}/api/mobile/apk"
        lan_apk_url = f"{lan_base_url}/api/mobile/apk"
        return {
            "hostname": platform.node(),
            "machine_id": auth_manager.get_machine_id(),
            "primary_ip": primary_ip if can_view_pin else "",
            "port": port,
            "base_url": preferred_base,
            "lan_base_url": lan_base_url if can_view_pin else "",
            "public_url": pub_url if can_view_pin else "",
            "apk_url": apk_url,
            "lan_apk_url": lan_apk_url,
            "connect_url": connect_url,
            "lan_connect_url": lan_connect_url,
            "apk_available": apk_path is not None,
            "apk_size_kb": round(apk_path.stat().st_size / 1024, 1) if apk_path else 0,
        }

    @app.get("/api/mobile/apk")
    async def download_android_apk():
        """Download the signed native Android app (RemoteDeskPro-Android.apk)."""
        apk_path = _find_android_apk()
        if not apk_path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="RemoteDeskPro-Android.apk not found on host.",
            )
        return FileResponse(
            path=str(apk_path),
            filename="RemoteDeskPro-Android.apk",
            media_type="application/vnd.android.package-archive",
        )

    @app.get("/api/mobile/qr.png")
    async def generate_mobile_qr(data: str = Query(..., description="URL or payload to encode in QR code")):
        """Generate a high-contrast PNG QR code image for Android phone scanning."""
        import io
        try:
            import qrcode
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8,
                border=2,
            )
            qr.add_data(data)
            qr.make(fit=True)
            img = qr.make_image(fill_color="#0A162E", back_color="#FFFFFF")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")
        except Exception:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (240, 240), (255, 255, 255))
            draw = ImageDraw.Draw(img)
            draw.rectangle([(10, 10), (230, 230)], outline=(10, 22, 46), width=4)
            draw.text((24, 105), data[:28], fill=(10, 22, 46))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")

    # ════════════════════════════════════════════════════════════════════════
    # WEBSOCKET STREAMING & INTERACTIVE TERMINAL
    # ════════════════════════════════════════════════════════════════════════

    @app.websocket("/ws/stream")
    async def websocket_stream(websocket: WebSocket, token: Optional[str] = Query(None)):
        """WebSocket endpoint for remote desktop video frames, audio, privacy mode, chat, and input sync."""
        await websocket.accept()

        if not auth_manager.validate_session_token(token):
            await websocket.send_text('{"type": "error", "message": "Unauthorized token"}')
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        session = ClientSession(
            websocket=websocket,
            screen_capture=screen,
            input_handler=input_h,
            clipboard_manager=clip,
            default_fps=default_fps,
            config_store=cfg,
            privacy_manager=privacy,
            audio_service=audio,
            auth_manager=auth_manager,
            session_token=token,
        )

        await session.start()

    @app.websocket("/ws/terminal")
    async def websocket_terminal(websocket: WebSocket, token: Optional[str] = Query(None)):
        """WebSocket endpoint for interactive streaming terminal console."""
        await websocket.accept()

        if not auth_manager.validate_session_token(token):
            await websocket.send_text("\r\n[Error: Unauthorized session token]\r\n")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        if not cfg.get_permissions().get("allow_terminal", True):
            await websocket.send_text("\r\n[Error: Terminal access is disabled by Host Security Policy]\r\n")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        term_session = TerminalSession(websocket)
        await term_session.start()

    # Mount static web client (supports standard Python and PyInstaller frozen bundle)
    if hasattr(sys, "_MEIPASS"):
        client_dir = Path(sys._MEIPASS) / "client"
    else:
        client_dir = Path(__file__).resolve().parent.parent / "client"

    if client_dir.exists():
        app.mount("/static", StaticFiles(directory=str(client_dir)), name="static")

        @app.get("/")
        async def root():
            index_file = client_dir / "index.html"
            if index_file.exists():
                return FileResponse(str(index_file))
            return JSONResponse({"message": "RemoteDesk Pro Host is running."})

    return app
