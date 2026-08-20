import asyncio
import json
import urllib.request
import websockets


async def probe_live_server():
    print("[*] Probing live server on port 8888...")

    # 1. Fetch Web Client HTML
    with urllib.request.urlopen("http://127.0.0.1:8888/") as res:
        html = res.read().decode("utf-8")
        assert "remote-canvas" in html
        print(f"[+] Web Client HTML successfully served ({len(html)} bytes)")

    # 2. Authenticate
    login_data = json.dumps({"pin": "123456"}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8888/api/auth/login",
        data=login_data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as res:
        data = json.loads(res.read().decode("utf-8"))
        token = data["token"]
        print(f"[+] Authenticated successfully! Token: {token[:24]}...")

    # 3. Fetch Status
    status_req = urllib.request.Request(
        f"http://127.0.0.1:8888/api/status?token={token}",
    )
    with urllib.request.urlopen(status_req) as res:
        status_data = json.loads(res.read().decode("utf-8"))
        print(f"[+] Status API: OS={status_data['os']}, Monitors={len(status_data['monitors'])}, Active={status_data['active_monitor']}")

    # 4. Connect WebSocket stream
    ws_url = f"ws://127.0.0.1:8888/ws/stream?token={token}"
    async with websockets.connect(ws_url) as ws:
        print(f"[+] WebSocket connection established to {ws_url}")

        # Send ping and mouse move
        await ws.send(json.dumps({"type": "ping", "t": 1000}))
        await ws.send(json.dumps({"type": "mousemove", "x": 0.5, "y": 0.5}))

        got_pong = False
        got_meta = False
        got_frame = False

        for _ in range(10):
            msg = await ws.recv()
            if isinstance(msg, str):
                parsed = json.loads(msg)
                if parsed.get("type") == "pong":
                    got_pong = True
                    print(f"[+] Received Pong: RTT ping roundtrip verified")
                elif parsed.get("type") == "frame_meta":
                    got_meta = True
                    seq = parsed["seq"]
                    print(f"[+] Received Frame Metadata: seq={seq}, res={parsed['width']}x{parsed['height']}, format={parsed['format']}")
                    # Send acknowledgment
                    await ws.send(json.dumps({"type": "ack", "seq": seq}))
            elif isinstance(msg, bytes):
                got_frame = True
                print(f"[+] Received Binary Frame: {len(msg)} bytes")

            if got_pong and got_meta and got_frame:
                break

    assert got_pong and got_meta and got_frame
    print("\n[SUCCESS] Live server probe passed all checks!")


if __name__ == "__main__":
    asyncio.run(probe_live_server())
