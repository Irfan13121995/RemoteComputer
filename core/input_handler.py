"""Cross-platform mouse and keyboard input dispatcher using pynput with View-Only support."""

import logging
import platform
import subprocess
import time
from typing import Any, Dict, Optional, Set, Tuple

import pynput
from pynput.keyboard import Controller as KeyController
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button, Controller as MouseController

logger = logging.getLogger(__name__)


class InputHandler:
    """Dispatches mouse and keyboard events safely to the host operating system."""

    # Map standard browser Key strings to pynput Key objects
    KEY_MAP = {
        "Alt": Key.alt,
        "AltGraph": Key.alt_gr,
        "Backspace": Key.backspace,
        "CapsLock": Key.caps_lock,
        "Control": Key.ctrl,
        "Delete": Key.delete,
        "ArrowDown": Key.down,
        "End": Key.end,
        "Enter": Key.enter,
        "Escape": Key.esc,
        "F1": Key.f1,
        "F2": Key.f2,
        "F3": Key.f3,
        "F4": Key.f4,
        "F5": Key.f5,
        "F6": Key.f6,
        "F7": Key.f7,
        "F8": Key.f8,
        "F9": Key.f9,
        "F10": Key.f10,
        "F11": Key.f11,
        "F12": Key.f12,
        "Home": Key.home,
        "Insert": Key.insert,
        "ArrowLeft": Key.left,
        "Meta": Key.cmd,
        "PageDown": Key.page_down,
        "PageUp": Key.page_up,
        "ArrowRight": Key.right,
        "Shift": Key.shift,
        " ": Key.space,
        "Tab": Key.tab,
        "ArrowUp": Key.up,
        "NumLock": Key.num_lock,
        "ScrollLock": Key.scroll_lock,
        "PrintScreen": Key.print_screen,
        "Pause": Key.pause,
        "ContextMenu": Key.menu,
    }

    # Map browser mouse button numbers
    BUTTON_MAP = {
        0: Button.left,
        1: Button.middle,
        2: Button.right,
    }

    def __init__(self, map_cmd_to_ctrl: bool = True, view_only: bool = False):
        self.mouse = MouseController()
        self.keyboard = KeyController()
        self.map_cmd_to_ctrl = map_cmd_to_ctrl
        self.view_only = view_only
        self.is_windows = platform.system() == "Windows"
        self.is_mac = platform.system() == "Darwin"

        # Track currently held keys/buttons to allow full release on disconnect
        self._pressed_keys: Set[Any] = set()
        self._pressed_buttons: Set[Button] = set()

    def scale_coordinates(
        self,
        norm_x: float,
        norm_y: float,
        monitor_rect: Dict[str, int],
    ) -> Tuple[int, int]:
        """Convert normalized 0.0-1.0 client coordinates to host screen pixel coordinates."""
        clamped_x = max(0.0, min(1.0, float(norm_x)))
        clamped_y = max(0.0, min(1.0, float(norm_y)))

        left = monitor_rect.get("left", 0)
        top = monitor_rect.get("top", 0)
        width = monitor_rect.get("width", 1920)
        height = monitor_rect.get("height", 1080)

        target_x = int(left + clamped_x * width)
        target_y = int(top + clamped_y * height)

        # Clamp to bounds
        target_x = max(left, min(left + width - 1, target_x))
        target_y = max(top, min(top + height - 1, target_y))

        return target_x, target_y

    def handle_mouse_move(self, norm_x: float, norm_y: float, monitor_rect: Dict[str, int]) -> None:
        """Move cursor to normalized position."""
        if self.view_only:
            return
        target_x, target_y = self.scale_coordinates(norm_x, norm_y, monitor_rect)
        try:
            self.mouse.position = (target_x, target_y)
        except Exception as e:
            logger.debug(f"Failed to move mouse: {e}")

    def handle_mouse_move_relative(self, delta_x: float, delta_y: float) -> None:
        """Move cursor by relative delta (Pointer Lock mode)."""
        if self.view_only:
            return
        try:
            self.mouse.move(int(delta_x), int(delta_y))
        except Exception as e:
            logger.debug(f"Failed to move mouse relatively: {e}")

    def handle_mouse_button(self, button_num: int, is_down: bool) -> None:
        """Handle mouse button press or release."""
        if self.view_only:
            return
        btn = self.BUTTON_MAP.get(button_num, Button.left)
        try:
            if is_down:
                self.mouse.press(btn)
                self._pressed_buttons.add(btn)
            else:
                self.mouse.release(btn)
                self._pressed_buttons.discard(btn)
        except Exception as e:
            logger.debug(f"Failed to handle mouse button {btn}: {e}")

    def handle_mouse_click(self, button_num: int, count: int = 1) -> None:
        """Perform instant mouse click."""
        if self.view_only:
            return
        btn = self.BUTTON_MAP.get(button_num, Button.left)
        try:
            self.mouse.click(btn, count)
        except Exception as e:
            logger.debug(f"Failed to click mouse button {btn}: {e}")

    def handle_mouse_scroll(self, delta_x: float, delta_y: float) -> None:
        """Handle mouse wheel scrolling."""
        if self.view_only:
            return
        try:
            scroll_x = int(-delta_x / 100) if abs(delta_x) >= 50 else (1 if delta_x < 0 else -1 if delta_x > 0 else 0)
            scroll_y = int(-delta_y / 100) if abs(delta_y) >= 50 else (1 if delta_y < 0 else -1 if delta_y > 0 else 0)
            self.mouse.scroll(scroll_x, scroll_y)
        except Exception as e:
            logger.debug(f"Failed to scroll mouse: {e}")

    def _resolve_key(self, key_str: str, code_str: Optional[str] = None) -> Optional[Any]:
        """Convert browser key / code strings into pynput key identifier."""
        # Handle Command / Meta translation for Mac clients connecting to Windows hosts
        if key_str in ("Meta", "OS") and self.map_cmd_to_ctrl and self.is_windows:
            return Key.ctrl

        # Check special key mapping
        if key_str in self.KEY_MAP:
            return self.KEY_MAP[key_str]

        # Handle specific Key codes if key_str was generic
        if code_str:
            if code_str.startswith("Numpad") and len(code_str) > 6:
                digit = code_str[6:]
                if digit.isdigit():
                    return KeyCode.from_char(digit)
            if code_str.startswith("Key") and len(code_str) == 4:
                return KeyCode.from_char(code_str[3].lower())
            if code_str.startswith("Digit") and len(code_str) == 6:
                return KeyCode.from_char(code_str[5])

        # Single printable character
        if len(key_str) == 1:
            return KeyCode.from_char(key_str)

        return None

    def handle_keyboard(self, key_str: str, code_str: Optional[str], is_down: bool) -> None:
        """Handle key down or key up."""
        if self.view_only:
            return
        resolved = self._resolve_key(key_str, code_str)
        if resolved is None:
            return

        try:
            if is_down:
                self.keyboard.press(resolved)
                self._pressed_keys.add(resolved)
            else:
                self.keyboard.release(resolved)
                self._pressed_keys.discard(resolved)
        except Exception as e:
            logger.debug(f"Failed to handle keyboard event {resolved}: {e}")

    def type_text(self, text: str) -> None:
        """Type a string of text sequentially."""
        if self.view_only:
            return
        try:
            self.keyboard.type(text)
        except Exception as e:
            logger.debug(f"Failed to type text: {e}")

    def trigger_special_action(self, action_name: str) -> bool:
        """Execute special system macros (Ctrl+Alt+Del, Alt+Tab, Task Manager, Win+E, etc.)."""
        if self.view_only:
            return False
        action = action_name.lower().strip()
        try:
            if action in ("ctrl_alt_del", "taskmgr", "task_manager", "ctrl_shift_esc"):
                if self.is_windows:
                    subprocess.Popen(["taskmgr.exe"])
                    return True
                else:
                    subprocess.Popen(["open", "-a", "Activity Monitor"])
                    return True

            elif action == "alt_tab":
                with self.keyboard.pressed(Key.alt):
                    self.keyboard.press(Key.tab)
                    self.keyboard.release(Key.tab)
                return True

            elif action in ("win_d", "show_desktop"):
                if self.is_windows:
                    with self.keyboard.pressed(Key.cmd):
                        self.keyboard.press("d")
                        self.keyboard.release("d")
                return True

            elif action == "win_e":
                if self.is_windows:
                    with self.keyboard.pressed(Key.cmd):
                        self.keyboard.press("e")
                        self.keyboard.release("e")
                return True

            elif action == "win_r":
                if self.is_windows:
                    with self.keyboard.pressed(Key.cmd):
                        self.keyboard.press("r")
                        self.keyboard.release("r")
                return True

            elif action in ("win_l", "lock_screen"):
                if self.is_windows:
                    subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
                elif self.is_mac:
                    subprocess.Popen(["pmset", "displaysleepnow"])
                return True

            elif action == "alt_f4":
                with self.keyboard.pressed(Key.alt):
                    self.keyboard.press(Key.f4)
                    self.keyboard.release(Key.f4)
                return True

            elif action == "copy":
                modifier = Key.cmd if self.is_mac else Key.ctrl
                with self.keyboard.pressed(modifier):
                    self.keyboard.press("c")
                    self.keyboard.release("c")
                return True

            elif action == "paste":
                modifier = Key.cmd if self.is_mac else Key.ctrl
                with self.keyboard.pressed(modifier):
                    self.keyboard.press("v")
                    self.keyboard.release("v")
                return True

            elif action == "escape":
                self.keyboard.press(Key.esc)
                self.keyboard.release(Key.esc)
                return True

        except Exception as e:
            logger.error(f"Failed to execute special action {action_name}: {e}")
            return False

        return False

    def release_all(self) -> None:
        """Release all currently held mouse buttons and keys."""
        for btn in list(self._pressed_buttons):
            try:
                self.mouse.release(btn)
            except Exception:
                pass
        self._pressed_buttons.clear()

        for k in list(self._pressed_keys):
            try:
                self.keyboard.release(k)
            except Exception:
                pass
        self._pressed_keys.clear()
