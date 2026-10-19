"""Privacy Mode & Local Physical Input Blocker.
Inspired by RustDesk's privacy mode (src/platform/windows.rs).
Blanks the physical display with an excluded-from-capture curtain and blocks physical input
while allowing the remote administrator to view and control the desktop normally.
"""

import ctypes
import logging
import platform
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Windows 10 2004+: Invisible to screen capture (mss), black/opaque on physical monitor


class PrivacyModeManager:
    """Manages RustDesk-style Privacy Mode (physical screen curtain + physical input lock)."""

    def __init__(self):
        self.is_windows = platform.system() == "Windows"
        self.enabled = False
        self.input_blocked = False
        self._lock = threading.RLock()
        self._curtain_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def set_privacy_mode(self, enable: bool) -> Dict[str, Any]:
        """Enable or disable Privacy Mode on the host machine."""
        with self._lock:
            if enable == self.enabled:
                return self.get_status()

            if enable:
                self.enabled = True
                self._stop_event.clear()
                if self.is_windows:
                    # 1. Attempt Win32 BlockInput (works when running with administrative privileges)
                    try:
                        res = ctypes.windll.user32.BlockInput(True)
                        self.input_blocked = bool(res)
                    except Exception as e:
                        logger.debug(f"Win32 BlockInput unavailable: {e}")
                        self.input_blocked = False

                    # 2. Spawn physical privacy curtain overlay thread
                    self._curtain_thread = threading.Thread(target=self._run_windows_curtain, daemon=True)
                    self._curtain_thread.start()
            else:
                self.enabled = False
                self._stop_event.set()
                if self.is_windows:
                    try:
                        ctypes.windll.user32.BlockInput(False)
                    except Exception:
                        pass
                self.input_blocked = False

            return self.get_status()

    def toggle(self) -> Dict[str, Any]:
        """Toggle Privacy Mode state."""
        return self.set_privacy_mode(not self.enabled)

    def get_status(self) -> Dict[str, Any]:
        """Return current privacy mode status."""
        return {
            "enabled": self.enabled,
            "input_blocked": self.input_blocked,
            "platform": platform.system(),
            "mode": "WDA_EXCLUDEFROMCAPTURE_CURTAIN" if self.is_windows else "VIRTUAL_PRIVACY_SHIELD",
        }

    def _run_windows_curtain(self) -> None:
        """
        Create a topmost fullscreen dark curtain window with SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE).
        Because WDA_EXCLUDEFROMCAPTURE excludes the window from desktop duplication/BitBlt capture,
        the remote viewer continues to see the real desktop while the physical monitor shows the privacy screen!
        """
        try:
            import tkinter as tk

            root = tk.Tk()
            root.title("RemoteDesk Pro Privacy Curtain")
            root.configure(bg="#06090F")
            root.attributes("-fullscreen", True)
            root.attributes("-topmost", True)
            root.overrideredirect(True)

            # Get HWND and apply WDA_EXCLUDEFROMCAPTURE so mss captures underneath it
            root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
            affinity_ok = False
            try:
                res = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
                affinity_ok = bool(res)
            except Exception:
                affinity_ok = False

            # If OS doesn't support WDA_EXCLUDEFROMCAPTURE, make curtain non-blocking to capture
            if not affinity_ok:
                root.withdraw()
            else:
                label = tk.Label(
                    root,
                    text="🔒 RemoteDesk Pro — Privacy Mode Active\n\nRemote administrator is currently working on this computer.",
                    fg="#00D4FF",
                    bg="#06090F",
                    font=("Segoe UI", 20, "bold"),
                )
                label.place(relx=0.5, rely=0.5, anchor="center")

            def check_stop():
                if self._stop_event.is_set() or not self.enabled:
                    try:
                        root.destroy()
                    except Exception:
                        pass
                else:
                    root.after(200, check_stop)

            root.after(200, check_stop)
            root.mainloop()
        except Exception as e:
            logger.debug(f"Privacy curtain window skipped (headless or no Tk): {e}")
