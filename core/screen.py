"""High-performance multi-monitor screen capture and encoding engine with cursor tracking and headless/locked fallback."""

import ctypes
import io
import logging
import platform
import time
from typing import Any, Dict, List, Optional, Tuple

import mss
import mss.exception
import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


class ScreenCapture:
    """Handles monitor enumeration, desktop frame grabbing, diffing, cursor tracking, encoding, and headless fallback."""

    def __init__(
        self,
        monitor_index: int = 1,
        format: str = "webp",
        quality: int = 70,
        scale: float = 1.0,
    ):
        self._sct: Optional[Any] = None
        self.monitor_index = monitor_index
        self.format = format.lower() if format.lower() in ("webp", "jpeg", "jpg") else "jpeg"
        if self.format == "jpg":
            self.format = "jpeg"
        self.quality = max(10, min(100, quality))
        self.scale = max(0.25, min(1.0, scale))
        self.is_windows = platform.system() == "Windows"

        self._last_frame_hash: Optional[int] = None
        self._last_thumbnail: Optional[np.ndarray] = None
        self._cached_monitors: List[Dict[str, Any]] = []

    def _ensure_sct(self) -> Any:
        """Initialize mss instance lazily."""
        if self._sct is None:
            if hasattr(mss, "MSS"):
                self._sct = mss.MSS()
            else:
                self._sct = mss.mss()
        return self._sct

    def get_monitors(self) -> List[Dict[str, Any]]:
        """
        Return list of available monitors.
        mss index 0 = Virtual Desktop (all displays), 1 = Primary, 2+ = Secondary
        """
        try:
            sct = self._ensure_sct()
            monitors = []
            for idx, mon in enumerate(sct.monitors):
                label = "All Displays (Virtual Desktop)" if idx == 0 else f"Display {idx}"
                if idx == 1:
                    label += " (Primary)"
                monitors.append(
                    {
                        "index": idx,
                        "label": label,
                        "left": mon["left"],
                        "top": mon["top"],
                        "width": mon["width"],
                        "height": mon["height"],
                        "is_all": (idx == 0),
                    }
                )
            self._cached_monitors = monitors
            return monitors
        except Exception as e:
            logger.warning(f"Error enumerating monitors: {e}. Using virtual fallback.")
            return [
                {
                    "index": 0,
                    "label": "Virtual Display (Default)",
                    "left": 0,
                    "top": 0,
                    "width": 1920,
                    "height": 1080,
                    "is_all": True,
                }
            ]

    def set_monitor(self, index: int) -> bool:
        """Switch active capture monitor."""
        monitors = self.get_monitors()
        if 0 <= index < len(monitors):
            self.monitor_index = index
            self._last_frame_hash = None
            self._last_thumbnail = None
            return True
        return False

    def get_current_monitor_rect(self) -> Dict[str, int]:
        """Get bounds of current active monitor."""
        try:
            sct = self._ensure_sct()
            if 0 <= self.monitor_index < len(sct.monitors):
                return sct.monitors[self.monitor_index]
            return sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        except Exception:
            return {"left": 0, "top": 0, "width": 1920, "height": 1080}

    def get_normalized_cursor_pos(self, mon_rect: Dict[str, int]) -> Tuple[float, float]:
        """Return normalized (0.0 - 1.0) cursor coordinates relative to the active monitor."""
        try:
            if self.is_windows:
                class POINT(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

                pt = POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                cx, cy = pt.x, pt.y
            else:
                from pynput.mouse import Controller

                cx, cy = Controller().position

            left = mon_rect.get("left", 0)
            top = mon_rect.get("top", 0)
            width = max(1, mon_rect.get("width", 1920))
            height = max(1, mon_rect.get("height", 1080))
            nx = max(0.0, min(1.0, (cx - left) / width))
            ny = max(0.0, min(1.0, (cy - top) / height))
            return round(nx, 4), round(ny, 4)
        except Exception:
            return 0.5, 0.5

    def _generate_fallback_frame(self, width: int = 1920, height: int = 1080, message: str = "Host Screen Protected / Headless Mode") -> Image.Image:
        """Generate an informative placeholder frame when display DC is inaccessible (e.g. headless/UAC)."""
        img = Image.new("RGB", (width, height), color=(18, 22, 31))
        draw = ImageDraw.Draw(img)

        for x in range(0, width, 120):
            draw.line([(x, 0), (x, height)], fill=(28, 33, 45), width=1)
        for y in range(0, height, 120):
            draw.line([(0, y), (width, y)], fill=(28, 33, 45), width=1)

        card_w, card_h = 600, 240
        cx, cy = width // 2, height // 2
        draw.rectangle(
            [cx - card_w // 2, cy - card_h // 2, cx + card_w // 2, cy + card_h // 2],
            fill=(30, 36, 50),
            outline=(88, 166, 255),
            width=2,
        )

        title = "🖥️ REMOTE HOST DESKTOP"
        subtitle = message
        time_str = f"Live Clock: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        info = f"Display: {self.monitor_index} | Res: {width}x{height} | Format: {self.format.upper()}"

        draw.text((cx - 140, cy - 70), title, fill=(240, 246, 252))
        draw.text((cx - 180, cy - 30), subtitle, fill=(210, 153, 34))
        draw.text((cx - 120, cy + 10), time_str, fill=(139, 148, 158))
        draw.text((cx - 150, cy + 40), info, fill=(88, 166, 255))

        return img

    def capture_frame(self) -> Tuple[bytes, bool, Dict[str, Any]]:
        """
        Capture current frame, encode to target format, and determine if frame changed.
        Returns: (encoded_bytes, has_changed, metadata)
        """
        mon_rect = self.get_current_monitor_rect()
        width = mon_rect.get("width", 1920)
        height = mon_rect.get("height", 1080)

        img = None
        try:
            sct = self._ensure_sct()
            raw = sct.grab(mon_rect)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except (mss.exception.ScreenShotError, OSError, Exception) as e:
            logger.debug(f"Direct screen capture unavailable ({e}). Using headless fallback.")
            img = self._generate_fallback_frame(width=width, height=height)

        orig_w, orig_h = img.size

        # Apply scaling if requested
        if self.scale < 0.99:
            target_w = int(orig_w * self.scale)
            target_h = int(orig_h * self.scale)
            img = img.resize((target_w, target_h), Image.Resampling.BILINEAR)

        # Fast change detection using downsampled thumbnail
        small_thumb = img.resize((64, 36), Image.Resampling.NEAREST)
        thumb_np = np.asarray(small_thumb)

        has_changed = True
        if self._last_thumbnail is not None:
            diff = np.mean(np.abs(thumb_np.astype(np.int16) - self._last_thumbnail.astype(np.int16)))
            if diff < 0.8:
                has_changed = False

        self._last_thumbnail = thumb_np

        # Encode to WebP or JPEG bytes
        buf = io.BytesIO()
        if self.format == "webp":
            img.save(buf, format="WEBP", quality=self.quality, method=0)
        else:
            img.save(buf, format="JPEG", quality=self.quality, optimize=False)

        encoded_bytes = buf.getvalue()
        cursor_x, cursor_y = self.get_normalized_cursor_pos(mon_rect)

        meta = {
            "width": orig_w,
            "height": orig_h,
            "scaled_width": img.width,
            "scaled_height": img.height,
            "monitor_index": self.monitor_index,
            "format": self.format,
            "bytes_size": len(encoded_bytes),
            "cursor_x": cursor_x,
            "cursor_y": cursor_y,
            "has_changed": has_changed,
            "timestamp": time.time(),
        }

        return encoded_bytes, has_changed, meta

    def close(self) -> None:
        """Release capture resources."""
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
