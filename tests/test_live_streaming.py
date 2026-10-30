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
from server.streamer import RDP2_HEADER


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


@pytest.mark.anyio
async def test_websocket_rdp2_binary_delta_stream():
    """Verify single-packet 30-byte RDP2 binary header streaming and live scale/4:4:4 config updates."""
    auth = AuthManager(pin="445566")
    screen = ScreenCapture(format="jpeg", quality=80, scale=1.0, subsampling=0)
    input_h = InputHandler()
    clip = ClipboardManager()

    app = create_app(
        auth_manager=auth,
        screen_capture=screen,
        input_handler=input_h,
        clipboard_manager=clip,
        default_fps=60,
    )

    client = TestClient(app)
    login_res = client.post("/api/auth/login", json={"pin": "445566"})
    assert login_res.status_code == 200
    token = login_res.json()["token"]

    with client.websocket_connect(f"/ws/stream?token={token}&proto=rdp2") as ws:
        got_first_rdp2 = False
        got_config_ack = False
        got_scaled_rdp2 = False
        orig_full_w = 0

        for _ in range(15):
            msg = ws.receive()
            if "bytes" in msg and len(msg["bytes"]) >= 30:
                pkt = msg["bytes"]
                (
                    magic,
                    frame_type,
                    fmt_code,
                    seq,
                    full_w,
                    full_h,
                    rect_x,
                    rect_y,
                    rect_w,
                    rect_h,
                    cx_u16,
                    cy_u16,
                    orig_w,
                    orig_h,
                ) = RDP2_HEADER.unpack(pkt[:30])
                assert magic == b"RDP2"
                if seq > 0:
                    ws.send_text(json.dumps({"type": "ack", "seq": seq}))
                if not got_first_rdp2 and frame_type == 0:
                    got_first_rdp2 = True
                    orig_full_w = full_w
                    assert pkt[30:32] == b"\xff\xd8"
                    # Request 50% scale keyframe dynamically
                    ws.send_text(
                        json.dumps(
                            {
                                "type": "config",
                                "scale": 0.5,
                                "quality": 86,
                                "subsampling": 0,
                                "force_keyframe": True,
                            }
                        )
                    )
                elif got_first_rdp2 and full_w < orig_full_w and frame_type == 0:
                    got_scaled_rdp2 = True
            elif "text" in msg:
                data = json.loads(msg["text"])
                if data.get("type") == "config_updated":
                    assert data["scale"] == 0.5
                    assert data["subsampling"] == 0
                    got_config_ack = True

            if got_first_rdp2 and got_config_ack and got_scaled_rdp2:
                break

        assert got_first_rdp2 is True
        assert got_config_ack is True
        assert got_scaled_rdp2 is True
