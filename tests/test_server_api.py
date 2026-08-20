"""Integration tests for FastAPI REST endpoints and authentication."""

import tempfile
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from core.auth import AuthManager
from core.clipboard import ClipboardManager
from core.file_manager import FileManager
from core.input_handler import InputHandler
from core.screen import ScreenCapture
from core.system_diagnostics import SystemDiagnostics
from server.app import create_app


@pytest.fixture
def test_app():
    auth = AuthManager(pin="123456")
    screen = ScreenCapture()
    input_h = InputHandler()
    clip = ClipboardManager()
    files = FileManager()
    diag = SystemDiagnostics()

    app = create_app(
        auth_manager=auth,
        screen_capture=screen,
        input_handler=input_h,
        clipboard_manager=clip,
        file_manager=files,
        system_diagnostics=diag,
    )
    return TestClient(app), auth


def test_login_success(test_app):
    client, auth = test_app
    res = client.post("/api/auth/login", json={"pin": "123456"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "token" in data
    assert auth.validate_session_token(data["token"]) is True


def test_login_failure(test_app):
    client, auth = test_app
    res = client.post("/api/auth/login", json={"pin": "wrong_pin"})
    assert res.status_code == 401


def test_host_info_public(test_app):
    client, auth = test_app
    res = client.get("/api/host-info")
    assert res.status_code == 200
    data = res.json()
    assert "machine_id" in data
    assert "pin" in data
    assert "monitors" in data


def test_status_endpoint_auth(test_app):
    client, auth = test_app
    token = auth.create_session_token()
    res = client.get("/api/status", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert "os" in data


def test_file_manager_api(test_app):
    client, auth = test_app
    token = auth.create_session_token()
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Get drives
    res = client.get("/api/files/drives", headers=headers)
    assert res.status_code == 200
    assert "shortcuts" in res.json()

    # 2. List temp directory
    with tempfile.TemporaryDirectory() as temp_dir:
        res = client.get(f"/api/files/list?path={temp_dir}", headers=headers)
        assert res.status_code == 200
        assert res.json()["total_items"] == 0

        # 3. Mkdir
        res = client.post("/api/files/mkdir", json={"parent": temp_dir, "name": "ApiFolder"}, headers=headers)
        assert res.status_code == 200

        # 4. Upload file
        file_content = b"Integration test uploaded file content"
        res = client.post(
            "/api/files/upload",
            data={"target_dir": temp_dir},
            files={"file": ("uploaded.txt", file_content, "text/plain")},
            headers=headers,
        )
        assert res.status_code == 200
        assert res.json()["success"] is True

        # 5. List again
        res = client.get(f"/api/files/list?path={temp_dir}", headers=headers)
        assert res.status_code == 200
        assert res.json()["total_items"] == 2


def test_system_diagnostics_api(test_app):
    client, auth = test_app
    token = auth.create_session_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Telemetry
    res = client.get("/api/system/telemetry", headers=headers)
    assert res.status_code == 200
    assert "cpu" in res.json()

    # Processes
    res = client.get("/api/system/processes?limit=10", headers=headers)
    assert res.status_code == 200
    assert len(res.json()["processes"]) > 0


def test_addressbook_api(test_app):
    client, auth = test_app
    token = auth.create_session_token()
    headers = {"Authorization": f"Bearer {token}"}

    test_entries = [{"alias": "Server 1", "id": "123 456 789"}]
    res = client.post("/api/addressbook", json={"entries": test_entries}, headers=headers)
    assert res.status_code == 200

    res = client.get("/api/addressbook", headers=headers)
    assert res.status_code == 200
    assert len(res.json()["entries"]) == 1
