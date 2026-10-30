"""High-performance multi-monitor screen capture, zero-copy Win32 GDI grabber,
dirty-region delta bounding-box engine, 4:4:4 Turbo JPEG / WebP encoder, and cursor tracker."""

import ctypes
from ctypes import wintypes
import io
import logging
import platform
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import mss
import mss.exception
import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


def _enable_windows_dpi_awareness() -> None:
    """Enable Per-Monitor DPI Awareness on Windows so screen capture and mouse coordinates match physical pixels."""
    if platform.system() != "Windows":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_enable_windows_dpi_awareness()

# Frame types for RDP2 binary streaming protocol
FRAME_KEYFRAME = 0
FRAME_DELTA = 1
FRAME_SETTLING = 2
FRAME_CURSOR_ONLY = 3

FORMAT_CODE_JPEG = 0
FORMAT_CODE_WEBP = 1
FORMAT_CODE_NONE = 2


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class DirectWin32Grabber:
    """
    Zero-copy Win32 GDI CreateDIBSection screen grabber using pure SRCCOPY (without DWM-stalling CAPTUREBLT).
    Maps the persistent 32-bit BGRX DIB memory directly into a NumPy ndarray view with 0-byte frame allocation.
    """

    SRCCOPY = 0x00CC0020
    DIB_RGB_COLORS = 0
    BI_RGB = 0

    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32
        self._gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(_BITMAPINFO),
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self._gdi32.CreateDIBSection.restype = wintypes.HBITMAP

        self._srcdc: Optional[int] = None
        self._memdc: Optional[int] = None
        self._dib: Optional[int] = None
        self._dib_bits = ctypes.c_void_p()
        self._np_view: Optional[np.ndarray] = None
        self._dims: Tuple[int, int] = (0, 0)

    def _ensure_buffers(self, width: int, height: int) -> None:
        if self._srcdc is None:
            self._srcdc = self._user32.GetWindowDC(0)
            if not self._srcdc:
                raise OSError("GetWindowDC(0) returned NULL")
            self._memdc = self._gdi32.CreateCompatibleDC(self._srcdc)
            if not self._memdc:
                self.close()
                raise OSError("CreateCompatibleDC failed")

        if self._dims != (width, height) or self._dib is None or self._np_view is None:
            if self._dib:
                self._gdi32.DeleteObject(self._dib)
                self._dib = None

            bmi = _BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = width
            bmi.bmiHeader.biHeight = -height  # Negative for top-down DIB
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = self.BI_RGB
            bmi.bmiHeader.biSizeImage = width * height * 4

            self._dib_bits = ctypes.c_void_p()
            self._dib = self._gdi32.CreateDIBSection(
                self._memdc,
                ctypes.byref(bmi),
                self.DIB_RGB_COLORS,
                ctypes.byref(self._dib_bits),
                None,
                0,
            )
            if not self._dib or not self._dib_bits.value:
                self.close()
                raise OSError("CreateDIBSection failed")

            self._gdi32.SelectObject(self._memdc, self._dib)
            byte_len = width * height * 4
            buf_type = ctypes.c_uint8 * byte_len
            raw_buf = ctypes.cast(self._dib_bits, ctypes.POINTER(buf_type)).contents
            self._np_view = np.frombuffer(raw_buf, dtype=np.uint8).reshape((height, width, 4))
            self._dims = (width, height)

    def grab_bgra_view(self, left: int, top: int, width: int, height: int) -> np.ndarray:
        """Capture monitor rect directly into persistent DIB and return zero-copy (H, W, 4) BGRX NumPy view."""
        self._ensure_buffers(width, height)
        ok = self._gdi32.BitBlt(
            self._memdc,
            0,
            0,
            width,
            height,
            self._srcdc,
            left,
            top,
            self.SRCCOPY,
        )
        if not ok:
            self.close()
            raise OSError("Win32 GDI BitBlt(SRCCOPY) failed")
        self._gdi32.GdiFlush()
        assert self._np_view is not None
        return self._np_view

    def close(self) -> None:
        try:
            if self._dib:
                self._gdi32.DeleteObject(self._dib)
        except Exception:
            pass
        try:
            if self._memdc:
                self._gdi32.DeleteDC(self._memdc)
        except Exception:
            pass
        try:
            if self._srcdc:
                self._user32.ReleaseDC(0, self._srcdc)
        except Exception:
            pass
        self._dib = None
        self._memdc = None
        self._srcdc = None
        self._np_view = None
        self._dims = (0, 0)


@dataclass
class DeltaCaptureState:
    """Per-session delta tracking state so concurrent sessions never corrupt each other's reference buffer."""

    prev_arr: Optional[np.ndarray] = None
    prev_raw_mode: str = "BGRX"
    frames_since_keyframe: int = 0
    last_change_time: float = field(default_factory=time.perf_counter)
    settled_sent: bool = False
    last_cursor: Tuple[float, float] = (-1.0, -1.0)
    monitor_index: int = -1

    def reset(self) -> None:
        self.prev_arr = None
        self.frames_since_keyframe = 0
        self.settled_sent = False


class ScreenCapture:
    """Handles monitor enumeration, zero-copy Win32 desktop grabbing, dirty-rect delta encoding, and cursor tracking."""

    def __init__(
        self,
        monitor_index: int = 1,
        format: str = "jpeg",
        quality: int = 80,
        scale: float = 1.0,
        subsampling: int = 0,
    ):
        self._thread_local = threading.local()
        self.monitor_index = monitor_index
        self.format = format.lower() if format.lower() in ("webp", "jpeg", "jpg") else "jpeg"
        if self.format == "jpg":
            self.format = "jpeg"
        self.quality = max(10, min(100, quality))
        self.scale = max(0.25, min(1.0, scale))
        self.subsampling = subsampling if subsampling in (0, 1, 2) else 0
        self.is_windows = platform.system() == "Windows"

        self._last_thumbnail: Optional[np.ndarray] = None
        self._cached_monitors: List[Dict[str, Any]] = []

    def _ensure_sct(self, force_recreate: bool = False) -> Any:
        """Initialize or retrieve a thread-local mss instance."""
        sct = getattr(self._thread_local, "sct", None)
        if sct is not None and force_recreate:
            try:
                sct.close()
            except Exception:
                pass
            sct = None

        if sct is None:
            sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
            self._thread_local.sct = sct
        return sct

    def _ensure_win32_grabber(self) -> Optional[DirectWin32Grabber]:
        """Retrieve or create thread-local zero-copy DirectWin32Grabber on Windows."""
        if not self.is_windows:
            return None
        grabber = getattr(self._thread_local, "win32_grabber", None)
        if grabber is None:
            grabber = DirectWin32Grabber()
            self._thread_local.win32_grabber = grabber
        return grabber

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
            if self.is_windows:
                try:
                    w = int(ctypes.windll.user32.GetSystemMetrics(0)) or 1920
                    h = int(ctypes.windll.user32.GetSystemMetrics(1)) or 1080
                    return [
                        {
                            "index": 0,
                            "label": "All Displays (Virtual Desktop)",
                            "left": 0,
                            "top": 0,
                            "width": w,
                            "height": h,
                            "is_all": True,
                        },
                        {
                            "index": 1,
                            "label": "Display 1 (Primary)",
                            "left": 0,
                            "top": 0,
                            "width": w,
                            "height": h,
                            "is_all": False,
                        },
                    ]
                except Exception:
                    pass
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
            self._last_thumbnail = None
            return True
        return False

    def get_monitor_rect_by_index(self, monitor_index: int) -> Dict[str, int]:
        """Get bounds of specified monitor index."""
        try:
            sct = self._ensure_sct()
            if 0 <= monitor_index < len(sct.monitors):
                return dict(sct.monitors[monitor_index])
            return dict(sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0])
        except Exception:
            if self._cached_monitors:
                for m in self._cached_monitors:
                    if m["index"] == monitor_index:
                        return {
                            "left": m["left"],
                            "top": m["top"],
                            "width": m["width"],
                            "height": m["height"],
                        }
            return {"left": 0, "top": 0, "width": 1920, "height": 1080}

    def get_current_monitor_rect(self) -> Dict[str, int]:
        """Get bounds of current active monitor."""
        return self.get_monitor_rect_by_index(self.monitor_index)

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

    def _generate_fallback_frame(self, width: int = 1920, height: int = 1080, message: str = "Host Screen Protected / UAC Prompt Active") -> Image.Image:
        """Generate an informative placeholder frame when display DC is temporarily inaccessible."""
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

        title = "REMOTE HOST DESKTOP"
        subtitle = message
        time_str = f"Live Clock: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        info = f"Display: {self.monitor_index} | Res: {width}x{height} | Format: {self.format.upper()}"

        draw.text((cx - 140, cy - 70), title, fill=(240, 246, 252))
        draw.text((cx - 180, cy - 30), subtitle, fill=(210, 153, 34))
        draw.text((cx - 120, cy + 10), time_str, fill=(139, 148, 158))
        draw.text((cx - 150, cy + 40), info, fill=(88, 166, 255))

        return img

    def _grab_raw_array(self, mon_rect: Dict[str, int]) -> Tuple[np.ndarray, str]:
        """
        Capture desktop pixels as a (H, W, C) NumPy array + raw Pillow mode ('BGRX' or 'RGB').
        Tier 0: Zero-copy DirectWin32Grabber (SRCCOPY without CAPTUREBLT DWM stall)
        Tier 1: Thread-local mss GDI capture
        Tier 2: PIL.ImageGrab fallback
        Tier 3: Synthetic fallback frame (headless / UAC lock screen)
        """
        left = mon_rect.get("left", 0)
        top = mon_rect.get("top", 0)
        width = mon_rect.get("width", 1920)
        height = mon_rect.get("height", 1080)

        # Tier 0: Fast Zero-Copy Win32 SRCCOPY Grabber
        if self.is_windows:
            grabber = self._ensure_win32_grabber()
            if grabber is not None:
                try:
                    bgra_view = grabber.grab_bgra_view(left, top, width, height)
                    return bgra_view, "BGRX"
                except Exception as e:
                    logger.debug(f"DirectWin32Grabber fallback: {e}")

        # Tier 1: Thread-local mss grab
        for attempt in range(2):
            try:
                sct = self._ensure_sct(force_recreate=(attempt == 1))
                raw = sct.grab(mon_rect)
                arr = np.frombuffer(raw.raw, dtype=np.uint8).reshape((raw.height, raw.width, 4))
                return arr, "BGRX"
            except Exception as e:
                if attempt == 1:
                    logger.debug(f"Thread-local MSS grab failed after retry: {e}")

        # Tier 2: PIL ImageGrab fallback
        try:
            from PIL import ImageGrab

            bbox = (left, top, left + width, top + height)
            grabbed = ImageGrab.grab(bbox=bbox, all_screens=True)
            if grabbed and grabbed.size[0] > 0 and grabbed.size[1] > 0:
                rgb = grabbed.convert("RGB")
                return np.asarray(rgb), "RGB"
        except Exception as e:
            logger.debug(f"PIL ImageGrab fallback unavailable: {e}")

        # Tier 3: Headless / Lock Screen fallback
        fallback_img = self._generate_fallback_frame(width=width, height=height)
        return np.asarray(fallback_img), "RGB"

    @staticmethod
    def _encode_array_region(
        arr: np.ndarray,
        raw_mode: str,
        fmt: str,
        quality: int,
        subsampling: int = 0,
    ) -> bytes:
        """Encode a contiguous (H, W, C) array into JPEG (libjpeg-turbo) or WebP bytes."""
        h, w = arr.shape[0], arr.shape[1]
        contig = np.ascontiguousarray(arr)
        if raw_mode == "BGRX":
            img = Image.frombuffer("RGB", (w, h), contig.data, "raw", "BGRX", 0, 1)
        else:
            img = Image.frombuffer("RGB", (w, h), contig.data, "raw", "RGB", 0, 1)

        buf = io.BytesIO()
        if fmt == "webp":
            img.save(buf, format="WEBP", quality=quality, method=0)
        else:
            img.save(buf, format="JPEG", quality=quality, subsampling=subsampling, optimize=False)
        return buf.getvalue()

    def capture_delta_frame(
        self,
        state: DeltaCaptureState,
        monitor_index: Optional[int] = None,
        fmt: Optional[str] = None,
        quality: Optional[int] = None,
        scale: Optional[float] = None,
        subsampling: Optional[int] = None,
        force_keyframe: bool = False,
        keyframe_interval: int = 180,
        settle_delay_sec: float = 0.22,
    ) -> Tuple[bytes, int, Dict[str, Any]]:
        """
        High-FPS Dirty-Region & Crisp-Settling Frame Capture Engine.
        Returns: (encoded_bytes, frame_type, metadata_dict)
        frame_type:
          0 = FRAME_KEYFRAME (full desktop)
          1 = FRAME_DELTA (dirty bounding rectangle x, y, w, h)
          2 = FRAME_SETTLING (crisp 4:4:4 q=92 refinement keyframe 220ms after motion stops)
          3 = FRAME_CURSOR_ONLY (0 image bytes when screen is static & settled)
        """
        mon_idx = self.monitor_index if monitor_index is None else monitor_index
        use_fmt = (fmt or self.format).lower()
        if use_fmt == "jpg":
            use_fmt = "jpeg"
        use_quality = max(10, min(100, self.quality if quality is None else quality))
        use_scale = max(0.25, min(1.0, self.scale if scale is None else scale))
        use_subsampling = self.subsampling if subsampling is None else subsampling

        if state.monitor_index != mon_idx:
            state.reset()
            state.monitor_index = mon_idx

        mon_rect = self.get_monitor_rect_by_index(mon_idx)
        raw_arr, raw_mode = self._grab_raw_array(mon_rect)
        orig_h, orig_w = raw_arr.shape[0], raw_arr.shape[1]

        # Scale if requested (< 0.99); at 1.0 (Native 1:1) operate directly on zero-copy raw_arr!
        if use_scale < 0.99:
            scaled_w = max(16, int(orig_w * use_scale) & ~1)
            scaled_h = max(16, int(orig_h * use_scale) & ~1)
            contig = np.ascontiguousarray(raw_arr)
            pil_full = Image.frombuffer("RGB", (orig_w, orig_h), contig.data, "raw", raw_mode, 0, 1)
            pil_scaled = pil_full.resize((scaled_w, scaled_h), Image.Resampling.BILINEAR)
            work_arr = np.asarray(pil_scaled)
            work_mode = "RGB"
        else:
            scaled_w, scaled_h = orig_w, orig_h
            work_arr = raw_arr
            work_mode = raw_mode

        cursor_x, cursor_y = self.get_normalized_cursor_pos(mon_rect)
        now = time.perf_counter()

        need_full = (
            force_keyframe
            or state.prev_arr is None
            or state.prev_arr.shape != work_arr.shape
            or state.prev_raw_mode != work_mode
            or state.frames_since_keyframe >= keyframe_interval
        )

        if need_full:
            encoded = self._encode_array_region(work_arr, work_mode, use_fmt, use_quality, use_subsampling)
            state.prev_arr = work_arr.copy()
            state.prev_raw_mode = work_mode
            state.frames_since_keyframe = 0
            state.last_change_time = now
            # If already encoded at high 4:4:4 clarity, mark settled
            state.settled_sent = (use_quality >= 90 and use_subsampling == 0)
            state.last_cursor = (cursor_x, cursor_y)
            return encoded, FRAME_KEYFRAME, {
                "orig_w": orig_w,
                "orig_h": orig_h,
                "full_w": scaled_w,
                "full_h": scaled_h,
                "rect_x": 0,
                "rect_y": 0,
                "rect_w": scaled_w,
                "rect_h": scaled_h,
                "format": use_fmt,
                "bytes_size": len(encoded),
                "cursor_x": cursor_x,
                "cursor_y": cursor_y,
                "timestamp": time.time(),
            }

        # Vectorized 4x4 strided uint8 SIMD pixel difference (< 0.5ms for 1080p, zero int16 allocation)
        assert state.prev_arr is not None
        curr_sample = work_arr[::4, ::4, :3]
        prev_sample = state.prev_arr[::4, ::4, :3]
        if np.array_equal(curr_sample, prev_sample):
            has_pixel_diff = False
            diff_mask = None
        else:
            channel_diff = np.maximum(curr_sample, prev_sample) - np.minimum(curr_sample, prev_sample)
            diff_mask = (channel_diff[:, :, 0] > 2) | (channel_diff[:, :, 1] > 2) | (channel_diff[:, :, 2] > 2)
            has_pixel_diff = bool(np.any(diff_mask))

        if not has_pixel_diff:
            # Screen pixels are static! Check if we should emit a 220ms Crisp 4:4:4 Settling Keyframe
            if not state.settled_sent and (now - state.last_change_time) >= settle_delay_sec:
                settle_quality = max(use_quality, 92)
                encoded = self._encode_array_region(work_arr, work_mode, use_fmt, settle_quality, subsampling=0)
                state.settled_sent = True
                state.frames_since_keyframe = 0
                state.last_cursor = (cursor_x, cursor_y)
                return encoded, FRAME_SETTLING, {
                    "orig_w": orig_w,
                    "orig_h": orig_h,
                    "full_w": scaled_w,
                    "full_h": scaled_h,
                    "rect_x": 0,
                    "rect_y": 0,
                    "rect_w": scaled_w,
                    "rect_h": scaled_h,
                    "format": use_fmt,
                    "bytes_size": len(encoded),
                    "cursor_x": cursor_x,
                    "cursor_y": cursor_y,
                    "timestamp": time.time(),
                }

            cursor_moved = (cursor_x, cursor_y) != state.last_cursor
            state.last_cursor = (cursor_x, cursor_y)
            return b"", FRAME_CURSOR_ONLY, {
                "orig_w": orig_w,
                "orig_h": orig_h,
                "full_w": scaled_w,
                "full_h": scaled_h,
                "rect_x": 0,
                "rect_y": 0,
                "rect_w": 0,
                "rect_h": 0,
                "format": use_fmt,
                "bytes_size": 0,
                "cursor_x": cursor_x,
                "cursor_y": cursor_y,
                "cursor_moved": cursor_moved,
                "timestamp": time.time(),
            }

        # Pixels changed -> compute 16px MCU-aligned dirty bounding box
        state.last_change_time = now
        state.settled_sent = False
        state.last_cursor = (cursor_x, cursor_y)

        ys, xs = np.where(diff_mask)
        x0 = max(0, (int(xs.min()) * 4 - 8) & ~15)
        y0 = max(0, (int(ys.min()) * 4 - 8) & ~15)
        x1 = min(scaled_w, ((int(xs.max()) + 1) * 4 + 23) & ~15)
        y1 = min(scaled_h, ((int(ys.max()) + 1) * 4 + 23) & ~15)
        rect_w = max(16, min(scaled_w - x0, x1 - x0))
        rect_h = max(16, min(scaled_h - y0, y1 - y0))

        # If >= 65% of the screen changed (e.g. switching apps / full-screen video), send a full keyframe
        if (rect_w * rect_h) >= int(0.65 * scaled_w * scaled_h):
            encoded = self._encode_array_region(work_arr, work_mode, use_fmt, use_quality, use_subsampling)
            state.prev_arr = work_arr.copy()
            state.frames_since_keyframe = 0
            return encoded, FRAME_KEYFRAME, {
                "orig_w": orig_w,
                "orig_h": orig_h,
                "full_w": scaled_w,
                "full_h": scaled_h,
                "rect_x": 0,
                "rect_y": 0,
                "rect_w": scaled_w,
                "rect_h": scaled_h,
                "format": use_fmt,
                "bytes_size": len(encoded),
                "cursor_x": cursor_x,
                "cursor_y": cursor_y,
                "timestamp": time.time(),
            }

        # Encode ONLY the dirty region!
        crop_arr = work_arr[y0 : y0 + rect_h, x0 : x0 + rect_w]
        encoded = self._encode_array_region(crop_arr, work_mode, use_fmt, use_quality, use_subsampling)
        state.prev_arr[y0 : y0 + rect_h, x0 : x0 + rect_w] = crop_arr
        state.frames_since_keyframe += 1

        return encoded, FRAME_DELTA, {
            "orig_w": orig_w,
            "orig_h": orig_h,
            "full_w": scaled_w,
            "full_h": scaled_h,
            "rect_x": x0,
            "rect_y": y0,
            "rect_w": rect_w,
            "rect_h": rect_h,
            "format": use_fmt,
            "bytes_size": len(encoded),
            "cursor_x": cursor_x,
            "cursor_y": cursor_y,
            "timestamp": time.time(),
        }

    def capture_frame(self) -> Tuple[bytes, bool, Dict[str, Any]]:
        """
        Full-frame capture method (used by screenshots and legacy callers).
        Returns: (encoded_bytes, has_changed, metadata)
        """
        mon_rect = self.get_current_monitor_rect()
        raw_arr, raw_mode = self._grab_raw_array(mon_rect)
        orig_h, orig_w = raw_arr.shape[0], raw_arr.shape[1]

        if self.scale < 0.99:
            target_w = max(1, int(orig_w * self.scale))
            target_h = max(1, int(orig_h * self.scale))
            contig = np.ascontiguousarray(raw_arr)
            pil_full = Image.frombuffer("RGB", (orig_w, orig_h), contig.data, "raw", raw_mode, 0, 1)
            pil_scaled = pil_full.resize((target_w, target_h), Image.Resampling.BILINEAR)
            work_arr = np.asarray(pil_scaled)
            work_mode = "RGB"
        else:
            target_w, target_h = orig_w, orig_h
            work_arr = raw_arr
            work_mode = raw_mode

        # Fast strided thumbnail diff (no PIL resize needed)
        step_y = max(1, target_h // 36)
        step_x = max(1, target_w // 64)
        thumb_np = work_arr[::step_y, ::step_x, :3].copy()

        has_changed = True
        if self._last_thumbnail is not None and self._last_thumbnail.shape == thumb_np.shape:
            diff = np.mean(np.abs(thumb_np.astype(np.int16) - self._last_thumbnail.astype(np.int16)))
            if diff < 0.8:
                has_changed = False
        self._last_thumbnail = thumb_np

        encoded_bytes = self._encode_array_region(
            work_arr,
            work_mode,
            self.format,
            self.quality,
            self.subsampling,
        )
        cursor_x, cursor_y = self.get_normalized_cursor_pos(mon_rect)

        meta = {
            "width": orig_w,
            "height": orig_h,
            "scaled_width": target_w,
            "scaled_height": target_h,
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
        """Release thread-local GDI / MSS capture resources."""
        grabber = getattr(self._thread_local, "win32_grabber", None)
        if grabber is not None:
            grabber.close()
            self._thread_local.win32_grabber = None

        sct = getattr(self._thread_local, "sct", None)
        if sct is not None:
            try:
                sct.close()
            except Exception:
                pass
            self._thread_local.sct = None
