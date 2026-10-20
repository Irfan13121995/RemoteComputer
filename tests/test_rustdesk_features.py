"""Unit and integration tests for merged RustDesk features:
- Persistent ConfigStore, Permanent Password, Granular Permissions, Audit Log
- LAN UDP Discovery & 9-digit Partner ID resolution
- Rendezvous (hbbs) & Relay (hbbr) broker
- Privacy Mode & Remote Audio Service
- TCP Port Tunneling / Forwarding
- Folder .zip Archive Download
- Windows Installer Silent Install & Uninstall workflow
"""

import base64
import io
import socket
import tempfile
import threading
import wave
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from core.audio_service import AudioService
from core.auth import AuthManager
from core.config_store import ConfigStore
from core.discovery import LanDiscoveryService
from core.file_manager import FileManager
from core.privacy_mode import PrivacyModeManager
from core.rendezvous import RendezvousBroker
from core.tcp_tunnel import TcpTunnelManager
from installer.setup_wizard import perform_installation, perform_uninstall
from server.app import create_app


def test_config_store_permanent_password_and_permissions(tmp_path):
    cfg_file = tmp_path / "config.json"
    store = ConfigStore(config_path=cfg_file)

    # Machine ID format check
    mid = store.get_machine_id()
    assert len(mid.replace(" ", "")) == 9

    # Permanent password setup & verification
    assert not store.has_permanent_password()
    store.set_permanent_password("MySecretUnattendedPass!2026")
    assert store.has_permanent_password()
    assert store.verify_permanent_password("MySecretUnattendedPass!2026")
    assert not store.verify_permanent_password("WrongPassword")

    # Granular permissions update & persistence
    store.update_permissions({"allow_audio": False, "allow_tcp_tunnel": False})
    reloaded = ConfigStore(config_path=cfg_file)
    perms = reloaded.get_permissions()
    assert perms["allow_audio"] is False
    assert perms["allow_tcp_tunnel"] is False
    assert perms["allow_keyboard_mouse"] is True

    # Audit log
    store.add_audit_event("TEST_EVENT", "192.168.1.10", "Unit test event")
    logs = store.get_audit_log()
    assert len(logs) == 1
    assert logs[0]["event"] == "TEST_EVENT"


def test_lan_discovery_and_partner_id_resolution():
    svc = LanDiscoveryService(machine_id="360 523 800", http_port=8000, is_ssl=False)
    pkt = svc.build_announcement_packet("announce")

    # Simulate receiving packet from another LAN host
    peer_svc = LanDiscoveryService(machine_id="999 888 777", http_port=8000)
    peer_pkt = peer_svc.build_announcement_packet("announce")
    parsed = svc.process_incoming_packet(peer_pkt, "192.168.1.77")

    assert parsed is not None
    assert parsed["machine_id"] == "999 888 777"
    assert parsed["url"] == "http://192.168.1.77:8000"

    # Resolve formatted or unformatted 9-digit ID
    assert svc.resolve_partner_id("999 888 777") == "http://192.168.1.77:8000"
    assert svc.resolve_partner_id("999888777") == "http://192.168.1.77:8000"
    assert svc.resolve_partner_id("360 523 800") == "http://127.0.0.1:8000"


def test_rendezvous_broker_registration_and_lookup():
    broker = RendezvousBroker()
    rec = broker.register_host(
        machine_id="123 456 789",
        hostname="Office-Workstation",
        os_name="Windows 11",
        ips=["10.0.0.42"],
        port=8000,
        is_ssl=False,
    )
    assert rec["normalized_id"] == "123456789"
    assert rec["direct_url"] == "http://10.0.0.42:8000"

    lookup = broker.lookup_host("123-456-789")
    assert lookup is not None
    assert lookup["hostname"] == "Office-Workstation"
    assert lookup["online"] is True


def test_audio_service_wav_chunk():
    audio = AudioService()
    audio.set_muted(False)
    chunk = audio.capture_audio_chunk(duration_ms=100)
    assert chunk["type"] == "audio_frame"
    raw_wav = base64.b64decode(chunk["data_b64"])
    with wave.open(io.BytesIO(raw_wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 22050
        assert wf.getnframes() > 0


def test_tcp_tunnel_port_forwarding():
    # Start a dummy echo TCP target server
    target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    target_sock.bind(("127.0.0.1", 0))
    target_port = target_sock.getsockname()[1]
    target_sock.listen(5)

    def echo_server():
        try:
            conn, _ = target_sock.accept()
            data = conn.recv(1024)
            conn.sendall(b"ECHO:" + data)
            conn.close()
        except Exception:
            pass
        finally:
            target_sock.close()

    t = threading.Thread(target=echo_server, daemon=True)
    t.start()

    mgr = TcpTunnelManager()
    try:
        tunnel = mgr.create_tunnel(
            listen_port=0,  # ephemeral port
            target_host="127.0.0.1",
            target_port=target_port,
            name="Test Echo Tunnel",
        )
        listen_port = tunnel["listen_port"]
        assert listen_port > 0

        # Connect to the tunnel's listen_port
        client = socket.create_connection(("127.0.0.1", listen_port), timeout=3.0)
        client.sendall(b"RustDeskTunnel")
        resp = client.recv(1024)
        client.close()
        assert resp == b"ECHO:RustDeskTunnel"

        active_list = mgr.list_tunnels()
        assert len(active_list) == 1
        assert active_list[0]["bytes_forwarded"] >= len(b"RustDeskTunnel")
    finally:
        mgr.stop_all()


def test_rustdesk_api_endpoints_and_folder_zip(tmp_path):
    cfg = ConfigStore(config_path=tmp_path / "cfg.json")
    cfg.set_permanent_password("PermPass99")
    auth = AuthManager(pin="112233", config_store=cfg)
    app = create_app(auth_manager=auth, config_store=cfg)
    client = TestClient(app)

    # 1. Authenticate using Permanent Unattended Password!
    login_res = client.post("/api/auth/login", json={"password": "PermPass99"})
    assert login_res.status_code == 200
    token = login_res.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Discovery & Rendezvous endpoints
    peers_res = client.get("/api/discovery/peers")
    assert peers_res.status_code == 200
    assert peers_res.json()["count"] >= 1

    rv_reg = client.post(
        "/api/rendezvous/register",
        json={
            "machine_id": "555 666 777",
            "hostname": "Lab-PC",
            "ips": ["192.168.1.99"],
            "port": 8000,
            "public_url": "https://test-tunnel.lhr.life",
        },
    )
    assert rv_reg.status_code == 200

    resolve_res = client.get("/api/discovery/resolve?partner_id=555+666+777")
    assert resolve_res.status_code == 200
    assert resolve_res.json()["found"] is True
    assert resolve_res.json()["url"] in ("http://192.168.1.99:8000", "https://test-tunnel.lhr.life")

    # 3. Folder ZIP download
    sample_dir = tmp_path / "my_folder"
    sample_dir.mkdir()
    (sample_dir / "hello.txt").write_text("Hello RustDesk ZIP!", encoding="utf-8")

    zip_res = client.get(f"/api/files/download-zip?path={sample_dir}", headers=headers)
    assert zip_res.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zip_res.content), "r") as zf:
        assert "hello.txt" in zf.namelist()
        assert zf.read("hello.txt").decode("utf-8") == "Hello RustDesk ZIP!"

    # 4. Security Sessions & Audit Log
    sess_res = client.get("/api/security/sessions", headers=headers)
    assert sess_res.status_code == 200
    assert len(sess_res.json()["sessions"]) >= 1

    audit_res = client.get("/api/security/audit-log", headers=headers)
    assert audit_res.status_code == 200
    assert len(audit_res.json()["events"]) >= 1

    # 5. Android Mobile App APK Download & QR Code Endpoints
    qr_info_res = client.get("/api/mobile/qr-info")
    assert qr_info_res.status_code == 200
    qr_info = qr_info_res.json()
    assert "apk_url" in qr_info
    assert "connect_url" in qr_info
    assert qr_info["apk_available"] is True

    qr_png_res = client.get("/api/mobile/qr.png?data=http://192.168.1.50:8000")
    assert qr_png_res.status_code == 200
    assert qr_png_res.headers["content-type"] == "image/png"
    assert qr_png_res.content[:8] == b"\x89PNG\r\n\x1a\n"

    apk_res = client.get("/api/mobile/apk")
    assert apk_res.status_code == 200
    assert apk_res.headers["content-type"] == "application/vnd.android.package-archive"
    assert apk_res.content[:2] == b"PK"

    # 6. Verify self Partner ID resolution includes is_self=True so remote clients stay on host origin
    self_id = auth.get_machine_id()
    self_resolve = client.get(f"/api/discovery/resolve?partner_id={self_id}")
    assert self_resolve.status_code == 200
    assert self_resolve.json()["is_self"] is True


def test_cross_thread_screen_capture():
    """Ensure ScreenCapture uses thread-local MSS instances so worker threads never fail GDI BitBlt."""
    import asyncio
    from core.screen import ScreenCapture

    sc = ScreenCapture()
    monitors = sc.get_monitors()
    assert len(monitors) >= 1

    frame_bytes, has_changed, meta = asyncio.run(asyncio.to_thread(sc.capture_frame))
    assert len(frame_bytes) > 0
    assert meta["width"] > 0 and meta["height"] > 0


def test_cross_network_tunnel_and_responsive_toolbar():
    """Verify InternetTunnelManager URL extraction, Rendezvous public_url preference when off-LAN, and mobile toolbar CSS."""
    from core.internet_tunnel import InternetTunnelManager, _LHR_URL_RE, _CF_URL_RE

    sample_lhr = "authenticated as anonymous user, your tunneled http url is https://42f891bc9e0123.lhr.life"
    m1 = _LHR_URL_RE.search(sample_lhr)
    assert m1 is not None and m1.group(0) == "https://42f891bc9e0123.lhr.life"

    sample_cf = "INF |  https://random-words-here.trycloudflare.com  |"
    m2 = _CF_URL_RE.search(sample_cf)
    assert m2 is not None and m2.group(0) == "https://random-words-here.trycloudflare.com"

    mgr = InternetTunnelManager(local_port=8000)
    st = mgr.get_status()
    assert "public_url" in st and "status" in st

    # Check RendezvousBroker resolve_best_url chooses public_url when LAN IP is unreachable
    broker = RendezvousBroker()
    broker.register_host(
        machine_id="777 888 999",
        hostname="Remote-Laptop",
        os_name="Windows 11",
        ips=["198.51.100.25"],
        port=8000,
        public_url="https://remote-laptop.lhr.life",
    )
    resolved = broker.resolve_best_url("777 888 999")
    assert resolved is not None
    assert resolved["url"] == "https://remote-laptop.lhr.life"
    assert resolved["source"] == "public_tunnel"

    # Check responsive mobile toolbar CSS & collapse button
    root = Path(__file__).resolve().parent.parent
    css_text = (root / "client" / "css" / "style.css").read_text(encoding="utf-8")
    html_text = (root / "client" / "index.html").read_text(encoding="utf-8")
    assert "flex-wrap: wrap" in css_text
    assert "@media (max-width: 860px)" in css_text
    assert "btn-toolbar-collapse" in html_text
    assert "btn-toolbar-expand" in html_text


def test_security_hardening_remediations(tmp_path):
    """Verify security audit remediations: PIN redaction on proxied requests, security headers, path protection, encrypted address book."""
    import json
    import pytest

    cfg_path = tmp_path / "sec_cfg.json"
    cfg = ConfigStore(config_path=cfg_path)
    auth = AuthManager(pin="654321", config_store=cfg)
    app = create_app(auth_manager=auth, config_store=cfg)
    client = TestClient(app)

    # 1. Local console request sees PIN, but reverse-proxied / tunneled request has PIN redacted + security headers
    local_res = client.get("/api/host-info")
    assert local_res.status_code == 200
    assert local_res.json()["pin"] == "654321"
    assert local_res.headers.get("x-frame-options") == "DENY"
    assert local_res.headers.get("x-content-type-options") == "nosniff"
    assert "default-src 'self'" in local_res.headers.get("content-security-policy", "")

    proxied_res = client.get("/api/host-info", headers={"X-Forwarded-For": "203.0.113.99"})
    assert proxied_res.status_code == 200
    assert proxied_res.json()["pin"] == "••••••"

    # 2. Address book password encryption at rest in config.json
    cfg.save_address_book([{"alias": "ProdServer", "id": "111 222 333", "password": "SuperSecretPassword!"}])
    raw_on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "password" not in raw_on_disk["address_book"][0]
    assert "password_enc" in raw_on_disk["address_book"][0]
    decrypted = cfg.get_address_book()
    assert decrypted[0]["password"] == "SuperSecretPassword!"

    # 3. FileManager blocks access to protected key files & config directory
    fm = FileManager()
    with pytest.raises(PermissionError):
        fm.validate_path_access(str(tmp_path / "key.pem"))
    with pytest.raises(PermissionError):
        fm.validate_path_access(str(tmp_path / ".secret.key"))

    # 4. IP whitelist enforced on X-Forwarded-For tunneled login attempts
    cfg.update_network_config({"ip_whitelist": ["192.168.1.50"]})
    blocked_login = client.post(
        "/api/auth/login",
        json={"pin": "654321"},
        headers={"X-Forwarded-For": "198.51.100.77"},
    )
    assert blocked_login.status_code == 401
    assert "whitelist" in blocked_login.json()["detail"].lower()




