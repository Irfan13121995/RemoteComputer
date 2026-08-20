import json
import time
import urllib.request


def test_exe():
    print("[*] Probing compiled RemoteDeskPro.exe on port 8765...")

    # 1. Fetch HTML
    with urllib.request.urlopen("http://127.0.0.1:8765/") as res:
        html = res.read().decode("utf-8")
        assert "RemoteDesk Pro" in html
        assert "remote-canvas" in html
        print(f"[+] HTML UI successfully served from inside compiled .exe ({len(html)} bytes)!")

    # 2. Fetch Host Info
    with urllib.request.urlopen("http://127.0.0.1:8765/api/host-info") as res:
        info = json.loads(res.read().decode("utf-8"))
        print(f"[+] Host Info API: Machine ID={info['machine_id']}, PIN={info['pin']}, OS={info['os']}")

    # 3. Test Login
    req = urllib.request.Request(
        "http://127.0.0.1:8765/api/auth/login",
        data=json.dumps({"pin": info["pin"]}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as res:
        login_data = json.loads(res.read().decode("utf-8"))
        assert login_data["success"] is True
        print(f"[+] Authentication verified! Token: {login_data['token'][:20]}...")

    print("\n[SUCCESS] Standalone Windows executable verified and working 100%!")


if __name__ == "__main__":
    test_exe()
