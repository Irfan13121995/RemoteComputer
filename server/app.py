"""FastAPI application providing REST endpoints, static assets, WebSocket streaming, and remote terminal."""

import os
import platform
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.auth import AuthManager
from core.clipboard import ClipboardManager
from core.file_manager import FileManager
from core.input_handler import InputHandler
from core.screen import ScreenCapture
from core.system_diagnostics import SystemDiagnostics
from core.terminal_session import TerminalSession
from core.tls import get_local_ips
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


def create_app(
    auth_manager: AuthManager,
    screen_capture: Optional[ScreenCapture] = None,
    input_handler: Optional[InputHandler] = None,
    clipboard_manager: Optional[ClipboardManager] = None,
    file_manager: Optional[FileManager] = None,
    system_diagnostics: Optional[SystemDiagnostics] = None,
    default_fps: int = 30,
) -> FastAPI:
    """Create and configure FastAPI application instance."""
    app = FastAPI(title="RemoteDesk Pro Host", version="1.0.0")

    # Shared instances
    screen = screen_capture or ScreenCapture()
    input_h = input_handler or InputHandler()
    clip = clipboard_manager or ClipboardManager()
    files = file_manager or FileManager()
    diagnostics = system_diagnostics or SystemDiagnostics()

    # Address book memory cache
    _address_book_cache: List[Dict[str, Any]] = []

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def verify_auth_header(authorization: Optional[str] = Header(None)) -> bool:
        """Dependency to authenticate REST API requests."""
        if not authorization:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header")
        token = authorization.replace("Bearer ", "").strip()
        if not auth_manager.validate_session_token(token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session token")
        return True

    # ════════════════════════════════════════════════════════════════════════
    # AUTHENTICATION & HOST INFO
    # ════════════════════════════════════════════════════════════════════════

    @app.get("/api/host-info")
    async def get_host_info():
        """Public host information (Machine ID, LAN IPs, OS, monitors count) for connection cards."""
        monitors = screen.get_monitors()
        active_rect = screen.get_current_monitor_rect()
        return {
            "machine_id": auth_manager.get_machine_id(),
            "pin": auth_manager.pin if not auth_manager.is_custom_password else "••••••",
            "has_password": auth_manager.is_custom_password,
            "hostname": platform.node(),
            "os": platform.system(),
            "os_release": platform.release(),
            "monitors": monitors,
            "active_monitor": screen.monitor_index,
            "monitor_rect": active_rect,
            "local_ips": get_local_ips(),
            "status": "ready",
        }

    @app.post("/api/auth/login")
    async def login(req: LoginRequest, request: Request):
        """Authenticate with PIN or password and receive session token."""
        client_ip = request.client.host if request.client else "127.0.0.1"
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
        }

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
        return {"text": clip.get_text()}

    @app.post("/api/clipboard")
    async def set_clipboard(req: ClipboardRequest, authorized: bool = Depends(verify_auth_header)):
        """Set host clipboard contents."""
        success = clip.set_text(req.text)
        return {"success": success}

    @app.post("/api/action/special")
    async def special_action(req: SpecialActionRequest, authorized: bool = Depends(verify_auth_header)):
        """Trigger keyboard/system macros."""
        success = input_h.trigger_special_action(req.action)
        return {"success": success, "action": req.action}

    # ════════════════════════════════════════════════════════════════════════
    # REMOTE FILE MANAGER
    # ════════════════════════════════════════════════════════════════════════

    @app.get("/api/files/drives")
    async def get_drives(authorized: bool = Depends(verify_auth_header)):
        """Get storage partitions and user shortcut folders."""
        return files.get_drives_and_shortcuts()

    @app.get("/api/files/list")
    async def list_files(path: Optional[str] = Query(None), authorized: bool = Depends(verify_auth_header)):
        """List files in directory."""
        try:
            return files.list_dir(path)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @app.get("/api/files/desktop-path")
    async def get_desktop_path(authorized: bool = Depends(verify_auth_header)):
        """Get path to Host Desktop for dropzone upload."""
        return {"desktop_path": files.get_desktop_dir()}

    @app.post("/api/files/upload")
    async def upload_file(
        target_dir: str = Form(...),
        file: UploadFile = File(...),
        authorized: bool = Depends(verify_auth_header),
    ):
        """Upload file to host folder."""
        try:
            content = await file.read()
            saved_path = files.save_uploaded_file(target_dir, file.filename, content)
            return {"success": True, "saved_path": saved_path, "filename": file.filename, "size": len(content)}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    @app.get("/api/files/download")
    async def download_file(path: str = Query(...), token: Optional[str] = Query(None), authorization: Optional[str] = Header(None)):
        """Download file from host."""
        auth_token = token or (authorization.replace("Bearer ", "").strip() if authorization else None)
        if not auth_manager.validate_session_token(auth_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        target = Path(path).resolve()
        if not target.exists() or target.is_dir():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

        return FileResponse(
            str(target),
            media_type="application/octet-stream",
            filename=target.name,
        )

    @app.post("/api/files/delete")
    async def delete_file(req: FileDeleteRequest, authorized: bool = Depends(verify_auth_header)):
        """Delete file or directory."""
        success = files.delete_item(req.path)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
        return {"success": True, "path": req.path}

    @app.post("/api/files/mkdir")
    async def mkdir(req: FileMkdirRequest, authorized: bool = Depends(verify_auth_header)):
        """Create new directory."""
        try:
            created = files.create_dir(req.parent, req.name)
            return {"success": True, "path": created}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    @app.post("/api/files/rename")
    async def rename_file(req: FileRenameRequest, authorized: bool = Depends(verify_auth_header)):
        """Rename file or directory."""
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
        success = diagnostics.kill_process(pid)
        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to kill process {pid}")
        return {"success": True, "pid": pid}

    @app.post("/api/system/power")
    async def power_control(req: PowerActionRequest, authorized: bool = Depends(verify_auth_header)):
        """Execute power action (lock, sleep, restart, shutdown)."""
        success = diagnostics.execute_power_action(req.action)
        return {"success": success, "action": req.action}

    # ════════════════════════════════════════════════════════════════════════
    # ADDRESS BOOK
    # ════════════════════════════════════════════════════════════════════════

    @app.get("/api/addressbook")
    async def get_addressbook(authorized: bool = Depends(verify_auth_header)):
        """Get saved computers."""
        return {"entries": _address_book_cache}

    @app.post("/api/addressbook")
    async def save_addressbook(req: AddressBookSaveRequest, authorized: bool = Depends(verify_auth_header)):
        """Save address book entries."""
        nonlocal _address_book_cache
        _address_book_cache = req.entries
        return {"success": True, "count": len(_address_book_cache)}

    # ════════════════════════════════════════════════════════════════════════
    # WEBSOCKET STREAMING & INTERACTIVE TERMINAL
    # ════════════════════════════════════════════════════════════════════════

    @app.websocket("/ws/stream")
    async def websocket_stream(websocket: WebSocket, token: Optional[str] = Query(None)):
        """WebSocket endpoint for remote desktop video frames, chat, and input sync."""
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

        term_session = TerminalSession(websocket)
        await term_session.start()

    # Mount static web client (supports standard Python and PyInstaller frozen bundle)
    import sys
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
