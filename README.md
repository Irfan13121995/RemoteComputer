# 🖥️ RemoteDesk Pro — Full-Featured Self-Hosted Remote Desktop Suite

A production-ready, lightweight, low-latency, self-hosted remote desktop application. Designed with the **frosted squircle glassmorphism** aesthetics of `personal-nas` and inspired by the dual-card connection architecture of **TeamViewer / AnyDesk**.

---

## 📦 Standalone Windows Executable (.exe)

You can run RemoteDesk Pro on **any Windows PC** as a standalone portable application with **zero pre-installed dependencies** (no Python, no git required):

### Option 1: Run Pre-Built Executable
Simply double-click `dist\RemoteDeskPro.exe`.
- Automatically initializes the host daemon.
- Automatically pops open your default web browser to the dashboard.
- Displays the **Pairing PIN** in the console.

### Option 2: One-Click Build Script
To re-compile the executable from source at any time:
```bat
.\build_exe.bat
```
*(Automatically generates icon assets, resolves dependencies, and compiles a single-file `dist\RemoteDeskPro.exe`)*

---

## 🌟 Comprehensive Feature Suite

| Feature Category | Description |
|---|---|
| **⚡ Ultra-Low Latency Streaming** | High-speed WebP / TurboJPEG frame encoding with backpressure flow control, frame-diffing, and 10–60 FPS adaptive rate. |
| **🎮 Precision Remote Input** | Sub-pixel mouse mapping, letterbox aspect-fit, **Pointer Lock mode (L)**, touch gestures, and stuck-key safety release. |
| **📁 Remote File Manager** | Two-pane directory explorer, drive switcher (`C:\`, `D:\`, `/`), upload/download, rename, and **Canvas Drag-and-Drop** directly to Host Desktop. |
| **💻 Interactive Web Terminal** | Real-time streaming CLI console (PowerShell / CMD on Windows, Bash / Zsh on Linux/Mac) with ANSI color output. |
| **📊 Task Manager & Diagnostics** | Real-time CPU, RAM, Disk, and Network telemetry graphs, active process table with one-click **Kill Process**, and power controls (Lock, Sleep, Reboot). |
| **💬 In-Session Live Text Chat** | Instant bidirectional messaging drawer between host user and remote partner. |
| **📸 In-Browser Recording & Snapshots** | Capture high-res PNG screenshots or record canvas video stream directly in browser to `.webm` downloads. |
| **⌨ Virtual On-Screen Keyboard** | Touch-friendly key bar for tablets, phones, and quick special keys. |
| **📖 Address Book & Favorites** | Save frequently accessed machines with friendly aliases and one-click connection. |
| **🔒 Enterprise Security** | 6-digit session PIN or permanent password, HMAC-SHA256 tokens, IP rate-limiting lockout, View-Only mode, and auto-generated TLS certificates. |

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
# Set a custom fixed PIN (e.g. 123456)
.\dist\RemoteDeskPro.exe --pin 123456

# Set a custom password instead of a PIN
.\dist\RemoteDeskPro.exe --password MySuperSecretPassword!

# Change port to 9000
.\dist\RemoteDeskPro.exe --port 9000

# Do not automatically launch browser
.\dist\RemoteDeskPro.exe --no-browser

# Low bandwidth mode for slower networks
.\dist\RemoteDeskPro.exe --fps 20 --quality 50 --scale 0.75
```

---

## 🧪 Running Automated Tests

Run the complete test suite:
```bash
.venv\Scripts\pytest tests/ -v
```
*(All 32 tests covering auth, coordinate scaling, file manager, system diagnostics, REST endpoints, and WebSocket streaming will run)*
