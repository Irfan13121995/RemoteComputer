# 🖥️ RemoteDesk Pro v2.0 — RustDesk-Powered Self-Hosted Remote Desktop Suite

A production-ready, lightweight, low-latency, self-hosted remote desktop application combining the **frosted squircle glassmorphism** UI of `personal-nas` with the core architecture and feature set of **[RustDesk](https://github.com/rustdesk/rustdesk)** and **TeamViewer**.

---

## 💿 Windows Installer & Standalone Executable (Run on Any PC)

RemoteDesk Pro v2.0 is packaged in **two ready-to-use Windows formats** in the `dist\` directory—requiring **zero Python or external dependencies** on the target computer:

### Option 1: One-Click Windows Setup Installer (`dist\RemoteDeskPro-Setup.exe`)
Simply copy and run **`dist\RemoteDeskPro-Setup.exe`** on **any Windows PC**:
- Launches a native **Windows Setup Wizard** (or run silently via `RemoteDeskPro-Setup.exe --silent`).
- Installs the application to `%LOCALAPPDATA%\Programs\RemoteDeskPro`.
- Creates **Desktop** (`RemoteDesk Pro.lnk`) and **Start Menu** shortcuts.
- Registers in **Windows Installed Apps (Add or Remove Programs)** with a dedicated `Uninstall.exe`.
- Configures **Unattended Auto-Start with Windows** (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`).
- Launches the **Windows System Tray Agent** and **Native Desktop App Window**.

### Option 2: Portable Single-File Executable (`dist\RemoteDeskPro.exe`)
Double-click **`dist\RemoteDeskPro.exe`** to run immediately without installation.

### Option 3: Rebuild Executables & Installer from Source
```bat
.\build_exe.bat
```

---

## 🌟 Merged RustDesk + Enterprise Feature Suite

| Feature Category | Description |
|---|---|
| **📡 Rendezvous (`hbbs`), Relay (`hbbr`) & LAN Discovery** | Automatic **UDP Port 21116** LAN peer discovery + built-in Rendezvous/Relay broker so you can connect directly using a **9-digit Partner ID (`360 523 800`)**. |
| **🔊 Remote System Audio Streaming** | Real-time PCM/WAV audio streaming over WebSockets with Web Audio API playback and in-session Mute/Unmute toggle. |
| **🕵️ RustDesk Privacy Mode** | Blanks the remote physical screen (`WDA_EXCLUDEFROMCAPTURE` dark curtain) and blocks local physical mouse/keyboard input while the remote administrator works. |
| **🔌 TCP Port Tunneling / Forwarding** | Forward local TCP ports (`127.0.0.1:listen_port`) to any remote service (`target_host:target_port`) such as SSH, RDP, databases, or internal web servers. |
| **🛡️ Granular Session Permissions & Security** | Independently toggle remote permissions (*Keyboard/Mouse*, *Clipboard*, *File Transfer*, *Audio*, *TCP Tunnel*, *Terminal*, *Privacy Mode*), configure **Permanent Unattended Passwords**, IP Whitelists, and **Kick** active sessions. |
| **⚡ Adaptive Video Engine & Static Frame Skipping** | High-speed WebP / JPEG encoding, RTT-based **Auto Adaptive Quality**, **0 KB/s idle static frame skipping**, and live remote cursor tracking. |
| **📁 File Transfer + Folder `.ZIP` Download** | Full directory browser, drive switcher (`C:\`, `D:\`, `/`), **One-Click Folder `.zip` Download**, live transfer speed queue, and **Canvas Drag-and-Drop** to Host Desktop. |
| **💻 Interactive Web Terminal & Task Manager** | Real-time streaming PowerShell / Bash console, live CPU/RAM/Network gauges, process killer, and remote power controls (Lock, Sleep, Reboot). |
| **🖥️ Native Windows App + System Tray Agent** | Persistent Windows System Tray icon (`pystray`) with quick ID/PIN copy, Privacy Mode toggle, Auto-Start toggle, and frameless native desktop window mode. |

---

## 🚀 Quickstart: Running from Source

### 🪟 On Windows:
```bat
.\run.bat
```

### 🍎 On macOS or 🐧 Linux:
```bash
chmod +x run.sh setup.sh
./run.sh
```

---

## 🛠️ CLI Options & Customization

```bash
# Start normally (System Tray + Native Desktop App Window)
.\dist\RemoteDeskPro.exe

# Start silently minimized in the Windows System Tray (used on Windows boot)
.\dist\RemoteDeskPro.exe --tray

# Run Silent Unattended Installation on any Windows PC
.\dist\RemoteDeskPro-Setup.exe --silent

# Uninstall silently
.\dist\RemoteDeskPro-Setup.exe --uninstall
```

---

## 🧪 Running Automated Tests

Run the complete 38-test suite:
```bash
.\.venv\Scripts\pytest.exe tests/ -v
```
