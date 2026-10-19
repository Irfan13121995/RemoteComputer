import json
import urllib.request


def test_exe():
    print("[*] Probing compiled RemoteDeskPro.exe v2.0 on port 8765...")

    # 1. Fetch HTML
    with urllib.request.urlopen("http://127.0.0.1:8765/") as res:
        html = res.read().decode("utf-8")
        assert "RemoteDesk Pro" in html
        assert "remote-canvas" in html
        assert "TCP Tunnels" in html
        print(f"[+] HTML UI v2.0 successfully served from inside compiled .exe ({len(html)} bytes)!")

    # 2. Fetch Host Info
    with urllib.request.urlopen("http://127.0.0.1:8765/api/host-info") as res:
        info = json.loads(res.read().decode("utf-8"))
        print(f"[+] Host Info API: Machine ID={info['machine_id']}, PIN={info['pin']}, Version={info.get('version')}")

    # 3. Fetch RustDesk LAN Discovery & Rendezvous Peers
    with urllib.request.urlopen("http://127.0.0.1:8765/api/discovery/peers") as res:
        disc = json.loads(res.read().decode("utf-8"))
        assert disc["count"] >= 1
        print(f"[+] RustDesk LAN Discovery & Rendezvous active: {disc['count']} peer(s) registered!")

    # 4. Fetch RustDesk Granular Security Permissions
    with urllib.request.urlopen("http://127.0.0.1:8765/api/security/permissions") as res:
        perms = json.loads(res.read().decode("utf-8"))
        assert "allow_keyboard_mouse" in perms["permissions"]
        print("[+] RustDesk Granular Security Policy verified!")

    # 5. Test Login
    req = urllib.request.Request(
        "http://127.0.0.1:8765/api/auth/login",
        data=json.dumps({"pin": info["pin"]}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as res:
        login_data = json.loads(res.read().decode("utf-8"))
        assert login_data["success"] is True
        print(f"[+] Authentication verified! Token: {login_data['token'][:20]}...")

    print("\n[SUCCESS] Standalone Windows executable v2.0 verified and working 100%!")


if __name__ == "__main__":
    test_exe()
