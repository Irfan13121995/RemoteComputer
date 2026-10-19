"""Native Windows Setup Installer & Uninstaller Wizard for RemoteDesk Pro v2.0.
Compiles into `dist/RemoteDeskPro-Setup.exe` (bundling `RemoteDeskPro.exe`).
Can be run on any Windows computer by double-clicking to install:
- Extracts `RemoteDeskPro.exe` to `%LOCALAPPDATA%\\Programs\\RemoteDeskPro`
- Creates Desktop & Start Menu `.lnk` shortcuts
- Registers in Windows "Add or Remove Programs" (Installed Apps) with Uninstaller
- Optionally configures Auto-Start with Windows
- Supports both interactive Setup Wizard GUI and `--silent` (`/S`) unattended installation
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

APP_NAME = "RemoteDesk Pro"
APP_VERSION = "2.0.0"
APP_PUBLISHER = "RemoteDesk Open Source Project"
EXE_FILENAME = "RemoteDeskPro.exe"
UNINSTALL_FILENAME = "Uninstall.exe"

REGISTRY_RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REGISTRY_UNINSTALL_PATH = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\RemoteDeskPro"
REGISTRY_KEY_NAME = "RemoteDeskPro"


def get_default_install_dir() -> Path:
    """Return standard per-user Windows installation directory (requires no UAC prompt)."""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "Programs" / "RemoteDeskPro"
    return Path.home() / "AppData" / "Local" / "Programs" / "RemoteDeskPro"


def get_bundled_payload_exe() -> Optional[Path]:
    """Locate bundled RemoteDeskPro.exe inside PyInstaller _MEIPASS or dist/ folder."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate = Path(sys._MEIPASS) / "payload" / EXE_FILENAME
        if candidate.exists():
            return candidate
    # Fallback when run from source tree
    dist_candidate = Path(__file__).resolve().parent.parent / "dist" / EXE_FILENAME
    if dist_candidate.exists():
        return dist_candidate
    return None


def stop_running_instances() -> None:
    """Stop any running RemoteDeskPro.exe processes before overwriting."""
    if platform.system() == "Windows":
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Stop-Process -Name RemoteDeskPro -Force -ErrorAction SilentlyContinue"],
                capture_output=True,
                timeout=5,
            )
            time.sleep(0.5)
        except Exception:
            pass


def create_shortcut(shortcut_path: Path, target_path: Path, arguments: str = "", description: str = "") -> bool:
    """Create a Windows .lnk shortcut via PowerShell WScript.Shell COM."""
    if platform.system() != "Windows":
        return False
    try:
        shortcut_path.parent.mkdir(parents=True, exist_ok=True)
        working_dir = str(target_path.parent)
        ps_cmd = (
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"$s = $ws.CreateShortcut('{shortcut_path}'); "
            f"$s.TargetPath = '{target_path}'; "
            f"$s.Arguments = '{arguments}'; "
            f"$s.WorkingDirectory = '{working_dir}'; "
            f"$s.Description = '{description}'; "
            f"$s.IconLocation = '{target_path}'; "
            f"$s.Save()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return shortcut_path.exists()
    except Exception:
        return False


def register_uninstall_entry(install_dir: Path, exe_path: Path, uninstaller_path: Path) -> bool:
    """Register RemoteDesk Pro in Windows Installed Apps (Add or Remove Programs)."""
    if platform.system() != "Windows":
        return False
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_UNINSTALL_PATH) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, APP_PUBLISHER)
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(exe_path))
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{uninstaller_path}" --uninstall')
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        return True
    except Exception:
        return False


def set_autostart_registry(enable: bool, exe_path: Path) -> bool:
    """Register or remove RemoteDesk Pro in Windows Startup."""
    if platform.system() != "Windows":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, REGISTRY_KEY_NAME, 0, winreg.REG_SZ, f'"{exe_path}" --tray')
            else:
                try:
                    winreg.DeleteValue(key, REGISTRY_KEY_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


def perform_installation(
    install_dir: Path,
    desktop_shortcut: bool = True,
    start_menu_shortcut: bool = True,
    autostart: bool = True,
    launch_after: bool = True,
) -> dict:
    """Execute the complete installation workflow."""
    payload_exe = get_bundled_payload_exe()
    if not payload_exe or not payload_exe.exists():
        raise FileNotFoundError(f"Bundled {EXE_FILENAME} payload not found.")

    stop_running_instances()

    install_dir = Path(install_dir).resolve()
    install_dir.mkdir(parents=True, exist_ok=True)

    target_exe = install_dir / EXE_FILENAME
    shutil.copy2(payload_exe, target_exe)

    # Copy installer itself as Uninstall.exe if running as frozen setup executable
    uninstaller_exe = install_dir / UNINSTALL_FILENAME
    if getattr(sys, "frozen", False):
        try:
            shutil.copy2(sys.executable, uninstaller_exe)
        except Exception:
            pass

    created_shortcuts = []
    if desktop_shortcut:
        desktop_lnk = Path.home() / "Desktop" / f"{APP_NAME}.lnk"
        if create_shortcut(desktop_lnk, target_exe, "", "Launch RemoteDesk Pro"):
            created_shortcuts.append(str(desktop_lnk))

    if start_menu_shortcut:
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        start_menu_lnk = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"
        if create_shortcut(start_menu_lnk, target_exe, "", "Launch RemoteDesk Pro"):
            created_shortcuts.append(str(start_menu_lnk))

    set_autostart_registry(autostart, target_exe)
    register_uninstall_entry(install_dir, target_exe, uninstaller_exe)

    if launch_after and target_exe.exists():
        try:
            subprocess.Popen([str(target_exe)], cwd=str(install_dir))
        except Exception:
            pass

    return {
        "success": True,
        "install_dir": str(install_dir),
        "executable": str(target_exe),
        "shortcuts": created_shortcuts,
        "autostart": autostart,
    }


def perform_uninstall(install_dir: Optional[Path] = None) -> dict:
    """Cleanly uninstall RemoteDesk Pro, remove shortcuts and registry entries."""
    stop_running_instances()
    target_dir = Path(install_dir) if install_dir else get_default_install_dir()

    # Remove shortcuts
    desktop_lnk = Path.home() / "Desktop" / f"{APP_NAME}.lnk"
    if desktop_lnk.exists():
        try:
            desktop_lnk.unlink()
        except Exception:
            pass

    appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    start_menu_lnk = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"
    if start_menu_lnk.exists():
        try:
            start_menu_lnk.unlink()
        except Exception:
            pass

    # Remove registry entries
    if platform.system() == "Windows":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_PATH, 0, winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, REGISTRY_KEY_NAME)
                except FileNotFoundError:
                    pass
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REGISTRY_UNINSTALL_PATH)
        except Exception:
            pass

    # Remove target executable
    exe_file = target_dir / EXE_FILENAME
    if exe_file.exists():
        try:
            exe_file.unlink()
        except Exception:
            pass

    return {"success": True, "uninstalled_dir": str(target_dir)}


def run_gui_wizard() -> None:
    """Launch sleek modern Windows Setup Wizard GUI."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(f"{APP_NAME} v{APP_VERSION} Setup")
    root.geometry("540x430")
    root.configure(bg="#0A0E17")
    root.resizable(False, False)

    # Header Banner
    header = tk.Frame(root, bg="#11192B", height=80)
    header.pack(fill="x")
    tk.Label(
        header,
        text=f"🖥️  {APP_NAME} v{APP_VERSION} Setup Wizard",
        fg="#F8FAFC",
        bg="#11192B",
        font=("Segoe UI", 15, "bold"),
    ).pack(anchor="w", padx=24, pady=(16, 2))
    tk.Label(
        header,
        text="Install RustDesk-Powered Self-Hosted Remote Desktop Suite on this computer",
        fg="#94A3B8",
        bg="#11192B",
        font=("Segoe UI", 9),
    ).pack(anchor="w", padx=24, pady=(0, 14))

    body = tk.Frame(root, bg="#0A0E17", padx=26, pady=18)
    body.pack(fill="both", expand=True)

    tk.Label(body, text="Installation Folder:", fg="#00D4FF", bg="#0A0E17", font=("Segoe UI", 10, "bold")).pack(anchor="w")

    dir_frame = tk.Frame(body, bg="#0A0E17")
    dir_frame.pack(fill="x", pady=(6, 16))
    install_dir_var = tk.StringVar(value=str(get_default_install_dir()))
    dir_entry = tk.Entry(
        dir_frame,
        textvariable=install_dir_var,
        bg="#141E32",
        fg="#F8FAFC",
        insertbackground="#F8FAFC",
        relief="flat",
        font=("Consolas", 10),
    )
    dir_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))

    def browse_dir():
        chosen = filedialog.askdirectory(initialdir=install_dir_var.get())
        if chosen:
            install_dir_var.set(chosen)

    tk.Button(
        dir_frame,
        text="Browse...",
        command=browse_dir,
        bg="#1E293B",
        fg="#F8FAFC",
        relief="flat",
        padx=10,
        pady=4,
    ).pack(side="right")

    desktop_var = tk.BooleanVar(value=True)
    startmenu_var = tk.BooleanVar(value=True)
    autostart_var = tk.BooleanVar(value=True)
    launch_var = tk.BooleanVar(value=True)

    for text, var in [
        ("Create Desktop Shortcut (RemoteDesk Pro.lnk)", desktop_var),
        ("Create Start Menu Shortcut & Register in Installed Apps", startmenu_var),
        ("Start RemoteDesk Pro automatically when Windows starts (Unattended Access)", autostart_var),
        ("Launch RemoteDesk Pro immediately after installation", launch_var),
    ]:
        cb = tk.Checkbutton(
            body,
            text=text,
            variable=var,
            bg="#0A0E17",
            fg="#E2E8F0",
            selectcolor="#141E32",
            activebackground="#0A0E17",
            activeforeground="#00D4FF",
            font=("Segoe UI", 9),
        )
        cb.pack(anchor="w", pady=3)

    status_lbl = tk.Label(body, text="Ready to install.", fg="#94A3B8", bg="#0A0E17", font=("Segoe UI", 9))
    status_lbl.pack(anchor="w", pady=(14, 4))

    footer = tk.Frame(root, bg="#11192B", height=60)
    footer.pack(fill="x", side="bottom")

    def on_install():
        status_lbl.config(text="Installing RemoteDesk Pro files & shortcuts...", fg="#00D4FF")
        root.update()
        try:
            res = perform_installation(
                install_dir=Path(install_dir_var.get()),
                desktop_shortcut=desktop_var.get(),
                start_menu_shortcut=startmenu_var.get(),
                autostart=autostart_var.get(),
                launch_after=launch_var.get(),
            )
            status_lbl.config(text=f"✔ Installed successfully to {res['install_dir']}", fg="#10B981")
            messagebox.showinfo(
                "Installation Complete",
                f"{APP_NAME} v{APP_VERSION} has been installed successfully!\n\n"
                f"Location: {res['install_dir']}\n"
                f"Shortcuts created: {len(res['shortcuts'])}",
            )
            root.destroy()
        except Exception as e:
            status_lbl.config(text=f"Error: {e}", fg="#EF4444")
            messagebox.showerror("Installation Failed", str(e))

    tk.Button(
        footer,
        text="Cancel",
        command=root.destroy,
        bg="#1E293B",
        fg="#F8FAFC",
        relief="flat",
        padx=18,
        pady=6,
        font=("Segoe UI", 10),
    ).pack(side="right", padx=(8, 24), pady=12)

    tk.Button(
        footer,
        text="🚀 Install Now",
        command=on_install,
        bg="#00A6FF",
        fg="#FFFFFF",
        relief="flat",
        padx=22,
        pady=6,
        font=("Segoe UI", 10, "bold"),
    ).pack(side="right", pady=12)

    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=f"{APP_NAME} Windows Installer & Uninstaller")
    parser.add_argument("--silent", "-s", dest="silent", action="store_true", help="Run silent unattended installation")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall RemoteDesk Pro")
    parser.add_argument("--install-dir", type=str, default=None, help="Custom installation directory")
    parser.add_argument("--no-launch", action="store_true", help="Do not launch app after silent install")
    parser.add_argument("--no-shortcuts", action="store_true", help="Do not create Desktop/Start Menu shortcuts")
    parser.add_argument("--no-autostart", action="store_true", help="Do not enable Windows startup registry entry")

    args, unknown = parser.parse_known_args()
    if "/S" in unknown or "/s" in unknown:
        args.silent = True
    target_dir = Path(args.install_dir) if args.install_dir else get_default_install_dir()

    if args.uninstall:
        res = perform_uninstall(target_dir)
        print(f"[+] Uninstalled {APP_NAME} from {res['uninstalled_dir']}")
        return

    if args.silent:
        res = perform_installation(
            install_dir=target_dir,
            desktop_shortcut=not args.no_shortcuts,
            start_menu_shortcut=not args.no_shortcuts,
            autostart=not args.no_autostart,
            launch_after=not args.no_launch,
        )
        print(f"[+] Silent install complete: {res['executable']}")
        return

    run_gui_wizard()


if __name__ == "__main__":
    main()
