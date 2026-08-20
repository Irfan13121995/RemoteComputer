"""Unit tests for normalized coordinate scaling and multi-monitor coordinate mappings."""

import pytest
from core.input_handler import InputHandler


def test_standard_monitor_coordinates():
    """Test scaling on primary 1920x1080 monitor."""
    handler = InputHandler()
    rect = {"left": 0, "top": 0, "width": 1920, "height": 1080}

    # Top-Left (0, 0)
    x, y = handler.scale_coordinates(0.0, 0.0, rect)
    assert x == 0
    assert y == 0

    # Center (0.5, 0.5)
    x, y = handler.scale_coordinates(0.5, 0.5, rect)
    assert x == 960
    assert y == 540

    # Bottom-Right (1.0, 1.0) -> clamped to width-1, height-1
    x, y = handler.scale_coordinates(1.0, 1.0, rect)
    assert x == 1919
    assert y == 1079


def test_multi_monitor_secondary_offset():
    """Test scaling on secondary monitor placed to the right (left=1920, top=0)."""
    handler = InputHandler()
    rect = {"left": 1920, "top": 0, "width": 1920, "height": 1080}

    # Top-Left of monitor 2
    x, y = handler.scale_coordinates(0.0, 0.0, rect)
    assert x == 1920
    assert y == 0

    # Center of monitor 2
    x, y = handler.scale_coordinates(0.5, 0.5, rect)
    assert x == 1920 + 960
    assert y == 540


def test_multi_monitor_negative_offset():
    """Test scaling on secondary monitor placed to the left (left=-1920, top=0)."""
    handler = InputHandler()
    rect = {"left": -1920, "top": 0, "width": 1920, "height": 1080}

    # Top-Left
    x, y = handler.scale_coordinates(0.0, 0.0, rect)
    assert x == -1920
    assert y == 0

    # Center
    x, y = handler.scale_coordinates(0.5, 0.5, rect)
    assert x == -1920 + 960
    assert y == 540


def test_coordinate_clamping():
    """Verify out-of-bounds coordinates (<0.0 or >1.0) are safely clamped."""
    handler = InputHandler()
    rect = {"left": 0, "top": 0, "width": 1000, "height": 500}

    # Negative coordinates
    x, y = handler.scale_coordinates(-0.5, -0.2, rect)
    assert x == 0
    assert y == 0

    # Oversized coordinates
    x, y = handler.scale_coordinates(1.5, 2.0, rect)
    assert x == 999
    assert y == 499
