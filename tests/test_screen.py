"""Unit tests for ScreenCapture monitor enumeration, 4:4:4 JPEG encoding, and dirty-region delta engine."""

import time
import numpy as np
import pytest
from core.screen import (
    DeltaCaptureState,
    FRAME_CURSOR_ONLY,
    FRAME_DELTA,
    FRAME_KEYFRAME,
    FRAME_SETTLING,
    ScreenCapture,
)


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


def test_delta_capture_keyframe_dirty_rect_settling_and_cursor_only(monkeypatch):
    """Verify keyframe, MCU-aligned dirty-rect delta, cursor-only zero-byte idle, and 4:4:4 crisp settling frame."""
    screen = ScreenCapture(format="jpeg", quality=75, scale=1.0, subsampling=2)
    try:
        desktop_buf = np.zeros((1080, 1920, 4), dtype=np.uint8)
        desktop_buf[:, :, 0] = 35
        desktop_buf[:, :, 1] = 28
        desktop_buf[:, :, 2] = 22
        cursor_pos = [0.25, 0.50]

        monkeypatch.setattr(screen, "_grab_raw_array", lambda rect: (desktop_buf, "BGRX"))
        monkeypatch.setattr(screen, "get_normalized_cursor_pos", lambda rect: (cursor_pos[0], cursor_pos[1]))

        state = DeltaCaptureState()

        # 1. First capture must emit a full FRAME_KEYFRAME
        kf_bytes, ftype1, meta1 = screen.capture_delta_frame(
            state, quality=75, subsampling=2, settle_delay_sec=0.06
        )
        assert ftype1 == FRAME_KEYFRAME
        assert meta1["rect_w"] == 1920 and meta1["rect_h"] == 1080
        assert kf_bytes[:2] == b"\xff\xd8"

        # 2. Mutate a small 160x100 window region -> must emit a compact FRAME_DELTA
        desktop_buf[300:400, 500:660, :3] = 220
        delta_bytes, ftype2, meta2 = screen.capture_delta_frame(
            state, quality=75, subsampling=2, settle_delay_sec=0.06
        )
        assert ftype2 == FRAME_DELTA
        assert 160 <= meta2["rect_w"] <= 224
        assert 100 <= meta2["rect_h"] <= 160
        assert meta2["rect_x"] % 16 == 0
        assert meta2["rect_y"] % 16 == 0
        assert len(delta_bytes) > 0

        # 3. Immediate static frame with cursor movement -> FRAME_CURSOR_ONLY (0 image bytes)
        cursor_pos[0] = 0.80
        idle_bytes, ftype3, meta3 = screen.capture_delta_frame(
            state, quality=75, subsampling=2, settle_delay_sec=0.50
        )
        assert ftype3 == FRAME_CURSOR_ONLY
        assert idle_bytes == b""
        assert meta3["cursor_moved"] is True

        # 4. After settle_delay_sec elapses -> automatic 4:4:4 q=92 FRAME_SETTLING refinement
        time.sleep(0.07)
        settle_bytes, ftype4, meta4 = screen.capture_delta_frame(
            state, quality=75, subsampling=2, settle_delay_sec=0.05
        )
        assert ftype4 == FRAME_SETTLING
        assert meta4["rect_w"] == 1920 and meta4["rect_h"] == 1080
        assert len(settle_bytes) > 0

        # 5. Subsequent static frame after settling with static cursor -> 0 bytes, cursor_moved=False
        idle2_bytes, ftype5, meta5 = screen.capture_delta_frame(
            state, quality=75, subsampling=2, settle_delay_sec=0.05
        )
        assert ftype5 == FRAME_CURSOR_ONLY
        assert idle2_bytes == b""
        assert meta5["cursor_moved"] is False
    finally:
        screen.close()
