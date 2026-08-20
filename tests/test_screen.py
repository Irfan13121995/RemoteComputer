"""Unit tests for ScreenCapture monitor enumeration and frame encoding."""

import pytest
from core.screen import ScreenCapture


def test_monitor_enumeration():
    """Verify monitor discovery produces valid rects."""
    screen = ScreenCapture()
    try:
        monitors = screen.get_monitors()
        assert len(monitors) > 0

        # Check fields in first monitor
        mon0 = monitors[0]
        assert "index" in mon0
        assert "width" in mon0
        assert "height" in mon0
        assert mon0["width"] > 0
        assert mon0["height"] > 0
    finally:
        screen.close()


def test_frame_capture_and_encoding_webp():
    """Verify frame capture and WebP encoding."""
    screen = ScreenCapture(format="webp", quality=60, scale=0.5)
    try:
        frame_bytes, has_changed, meta = screen.capture_frame()
        assert isinstance(frame_bytes, bytes)
        assert len(frame_bytes) > 0
        assert meta["format"] == "webp"
        assert meta["scaled_width"] > 0
        assert meta["scaled_height"] > 0
    finally:
        screen.close()


def test_frame_capture_and_encoding_jpeg():
    """Verify frame capture and JPEG encoding."""
    screen = ScreenCapture(format="jpeg", quality=60, scale=0.5)
    try:
        frame_bytes, has_changed, meta = screen.capture_frame()
        assert isinstance(frame_bytes, bytes)
        assert len(frame_bytes) > 0
        assert meta["format"] == "jpeg"
    finally:
        screen.close()


def test_set_monitor():
    """Verify switching monitor index."""
    screen = ScreenCapture()
    try:
        monitors = screen.get_monitors()
        if len(monitors) > 1:
            assert screen.set_monitor(1) is True
            assert screen.monitor_index == 1

        # Invalid monitor index
        assert screen.set_monitor(9999) is False
    finally:
        screen.close()
