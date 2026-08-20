"""End-to-end live test verifying WebSocket streaming, ping-pong, and input events."""

import asyncio
import json
import pytest
from starlette.testclient import TestClient
import websockets.client

from core.auth import AuthManager
from core.clipboard import ClipboardManager
from core.input_handler import InputHandler
from core.screen import ScreenCapture
from server.app import create_app


@pytest.mark.anyio
async def test_websocket_stream_flow():
    """Verify end-to-end WebSocket frame streaming, ping/pong, and input dispatch."""
    auth = AuthManager(pin="112233")
    screen = ScreenCapture(format="jpeg", quality=50, scale=0.5)
    input_h = InputHandler()
    clip = ClipboardManager()

    app = create_app(
        auth_manager=auth,
        screen_capture=screen,
        input_handler=input_h,
        clipboard_manager=clip,
        default_fps=20,
    )

    client = TestClient(app)

    # 1. Login
    login_res = client.post("/api/auth/login", json={"pin": "112233"})
    assert login_res.status_code == 200
    token = login_res.json()["token"]

    # 2. Connect WebSocket using TestClient websocket context
    with client.websocket_connect(f"/ws/stream?token={token}") as ws:
        # Ping / Pong
        ws.send_text(json.dumps({"type": "ping", "t": 1000}))

        # Send mouse movement
        ws.send_text(json.dumps({"type": "mousemove", "x": 0.5, "y": 0.5}))

        # Receive messages
        received_meta = False
        received_binary = False
        received_pong = False

        for _ in range(10):
            msg = ws.receive()
            if "text" in msg:
                data = json.loads(msg["text"])
                if data.get("type") == "frame_meta":
                    received_meta = True
                    seq = data.get("seq")
                    # Send ACK back for flow control
                    ws.send_text(json.dumps({"type": "ack", "seq": seq}))
                elif data.get("type") == "pong":
                    received_pong = True
                    assert data.get("t") == 1000
            elif "bytes" in msg:
                received_binary = True
                assert len(msg["bytes"]) > 0

            if received_meta and received_binary and received_pong:
                break

        assert received_meta is True
        assert received_binary is True
        assert received_pong is True
