"""Windows OS Integration Helper for RemoteDesk Pro.
Provides:
- Native Desktop App Mode Window Launcher (Edge WebView / Chrome --app mode with standard browser fallback)
- Windows Auto-Start Registry management (HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run)
- Desktop & Start Menu Shortcut (.lnk) creation
- Windows Installed Apps / Uninstall Registry management
- Windows Firewall rule helper
"""

import logging
import os
import platform
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

APP_NAME = "RemoteDesk Pro"
REGISTRY_RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REGISTRY_UNINSTALL_PATH = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\RemoteDeskPro"
REGISTRY_KEY_NAME = "RemoteDeskPro"


def get_executable_path() -> Path:
    """Return the path to the currently running executable or launcher script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return (Path(__file__).resolve().parent.parent / "launcher.py").resolve()


def is_autostart_enabled() -> bool:
    """Check if RemoteDesk Pro is registered to start automatically with Windows."""
    if platform.system() != "Windows":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, REGISTRY_KEY_NAME)
            return bool(val)
    except Exception:
        return False


def set_autostart_enabled(enable: bool, exe_path: Optional[Path] = None) -> bool:
    """Enable or disable starting RemoteDesk Pro automatically when Windows boots."""
    if platform.system() != "Windows":
        return False
    try:
        import winreg

        target = exe_path or get_executable_path()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                if str(target).lower().endswith(".exe"):
                    cmd = f'"{target}" --tray'
                else:
                    cmd = f'"{sys.executable}" "{target}" --tray'
                winreg.SetValueEx(key, REGISTRY_KEY_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, REGISTRY_KEY_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        logger.warning(f"Failed to update Windows startup registry: {e}")
        return False


def create_windows_shortcut(
    shortcut_path: Path,
    target_path: Path,
    arguments: str = "",
    description: str = "RemoteDesk Pro — Self-Hosted Remote Desktop Suite",
    icon_path: Optional[Path] = None,
) -> bool:
    """Create a Windows .lnk shortcut using WScript.Shell COM via PowerShell."""
    if platform.system() != "Windows":
        return False
    try:
        shortcut_path.parent.mkdir(parents=True, exist_ok=True)
        icon_loc = str(icon_path or target_path)
        working_dir = str(target_path.parent)
        ps_script = (
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"$s = $ws.CreateShortcut('{shortcut_path}'); "
            f"$s.TargetPath = '{target_path}'; "
            f"$s.Arguments = '{arguments}'; "
            f"$s.WorkingDirectory = '{working_dir}'; "
            f"$s.Description = '{description}'; "
            f"$s.IconLocation = '{icon_loc}'; "
            f"$s.Save()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps_script],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return shortcut_path.exists()
    except Exception as e:
        logger.warning(f"Failed to create shortcut {shortcut_path}: {e}")
        return False


def launch_native_app_window(url: str) -> None:
    """
    Open the RemoteDesk Pro interface as a native-feeling standalone desktop window
    using Edge/Chrome --app mode (frameless window without browser address bar/tabs),
    or fall back to default browser if neither is found.
    """
    if platform.system() == "Windows":
        candidates = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        for browser_exe in candidates:
            if os.path.exists(browser_exe):
                try:
                    subprocess.Popen(
                        [
                            browser_exe,
                            f"--app={url}",
                            "--window-size=1280,820",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return
                except Exception:
                    continue

    # Fallback to system default browser
    try:
        webbrowser.open(url)
    except Exception as e:
        logger.warning(f"Failed to open browser window: {e}")


def get_windows_integration_status() -> Dict[str, Any]:
    """Return current Windows integration state (Auto-Start, Frozen EXE mode, Install dir)."""
    exe_path = get_executable_path()
    return {
        "is_windows": platform.system() == "Windows",
        "is_frozen_exe": bool(getattr(sys, "frozen", False)),
        "executable_path": str(exe_path),
        "autostart_enabled": is_autostart_enabled(),
    }
