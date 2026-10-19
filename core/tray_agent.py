"""Windows System Tray Agent for RemoteDesk Pro.
Runs a persistent notification tray icon with quick actions:
- Open RemoteDesk Pro Native Window
- Copy Partner ID & PIN
- Toggle Privacy Mode
- Toggle Start with Windows
- Quit Daemon
"""

import logging
import threading
from typing import Callable, Optional

from PIL import Image, ImageDraw
import pyperclip

from core.windows_integration import is_autostart_enabled, launch_native_app_window, set_autostart_enabled

logger = logging.getLogger(__name__)


def create_tray_icon_image(size: int = 64) -> Image.Image:
    """Create a crisp cyan-blue squircle monitor icon for the Windows System Tray."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Squircle background
    draw.rounded_rectangle([(2, 2), (size - 2, size - 2)], radius=14, fill=(10, 22, 46, 255), outline=(0, 212, 255, 255), width=3)
    # Monitor screen
    draw.rounded_rectangle([(12, 14), (size - 12, size - 24)], radius=4, fill=(0, 212, 255, 255))
    # Monitor stand
    cx = size // 2
    draw.rectangle([(cx - 4, size - 24), (cx + 4, size - 14)], fill=(240, 246, 252, 255))
    draw.rectangle([(cx - 12, size - 15), (cx + 12, size - 11)], fill=(0, 212, 255, 255))
    return img


class SystemTrayAgent:
    """Manages the Windows System Tray icon and menu in a background thread."""

    def __init__(
        self,
        dashboard_url: str,
        machine_id_getter: Callable[[], str],
        pin_getter: Callable[[], str],
        privacy_toggle_fn: Optional[Callable[[], None]] = None,
        on_quit_fn: Optional[Callable[[], None]] = None,
    ):
        self.dashboard_url = dashboard_url
        self.get_machine_id = machine_id_getter
        self.get_pin = pin_getter
        self.privacy_toggle_fn = privacy_toggle_fn
        self.on_quit_fn = on_quit_fn
        self._icon = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the system tray icon on a non-blocking daemon thread."""
        self._thread = threading.Thread(target=self._run_tray, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop and remove the system tray icon."""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass

    def _run_tray(self) -> None:
        try:
            import pystray
            from pystray import Menu, MenuItem

            def on_open(icon, item):
                launch_native_app_window(self.dashboard_url)

            def on_copy_id(icon, item):
                try:
                    pyperclip.copy(self.get_machine_id())
                except Exception:
                    pass

            def on_copy_pin(icon, item):
                try:
                    pyperclip.copy(self.get_pin())
                except Exception:
                    pass

            def on_toggle_privacy(icon, item):
                if self.privacy_toggle_fn:
                    self.privacy_toggle_fn()

            def on_toggle_autostart(icon, item):
                current = is_autostart_enabled()
                set_autostart_enabled(not current)

            def on_quit(icon, item):
                icon.stop()
                if self.on_quit_fn:
                    self.on_quit_fn()

            menu = Menu(
                MenuItem(f"RemoteDesk Pro — ID: {self.get_machine_id()}", on_open, default=True),
                Menu.SEPARATOR,
                MenuItem("Open Dashboard Window", on_open),
                MenuItem("Copy Partner ID", on_copy_id),
                MenuItem("Copy One-Time PIN", on_copy_pin),
                Menu.SEPARATOR,
                MenuItem("Toggle Privacy Mode", on_toggle_privacy),
                MenuItem(
                    "Start Automatically with Windows",
                    on_toggle_autostart,
                    checked=lambda item: is_autostart_enabled(),
                ),
                Menu.SEPARATOR,
                MenuItem("Quit RemoteDesk Pro", on_quit),
            )

            self._icon = pystray.Icon(
                "RemoteDeskPro",
                create_tray_icon_image(),
                f"RemoteDesk Pro (ID: {self.get_machine_id()})",
                menu,
            )
            self._icon.run()
        except Exception as e:
            logger.debug(f"System tray agent unavailable in current environment: {e}")
