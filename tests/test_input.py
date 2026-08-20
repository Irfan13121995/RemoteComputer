"""Unit tests for InputHandler key resolution, mappings, and special actions."""

import pytest
from pynput.keyboard import Key, KeyCode
from core.input_handler import InputHandler


def test_key_resolution_special_keys():
    """Verify mapping of browser key strings to pynput Key objects."""
    handler = InputHandler()

    assert handler._resolve_key("Enter") == Key.enter
    assert handler._resolve_key("Escape") == Key.esc
    assert handler._resolve_key("Tab") == Key.tab
    assert handler._resolve_key("ArrowUp") == Key.up
    assert handler._resolve_key("F5") == Key.f5
    assert handler._resolve_key("Control") == Key.ctrl
    assert handler._resolve_key("Shift") == Key.shift


def test_key_resolution_characters():
    """Verify single printable characters resolution."""
    handler = InputHandler()

    key_a = handler._resolve_key("a")
    assert isinstance(key_a, KeyCode)
    assert key_a.char == "a"

    key_z = handler._resolve_key("Z")
    assert isinstance(key_z, KeyCode)
    assert key_z.char == "Z"


def test_mac_cmd_to_ctrl_translation():
    """Verify Mac Meta key translation to Windows Ctrl when enabled."""
    handler_win = InputHandler(map_cmd_to_ctrl=True)
    handler_win.is_windows = True
    assert handler_win._resolve_key("Meta") == Key.ctrl

    handler_no_map = InputHandler(map_cmd_to_ctrl=False)
    handler_no_map.is_windows = True
    assert handler_no_map._resolve_key("Meta") == Key.cmd


def test_release_all_clears_state():
    """Verify release_all() cleans up tracked buttons and keys."""
    handler = InputHandler()
    handler._pressed_keys.add(Key.ctrl)
    handler._pressed_buttons.add(handler.BUTTON_MAP[0])

    handler.release_all()
    assert len(handler._pressed_keys) == 0
    assert len(handler._pressed_buttons) == 0
