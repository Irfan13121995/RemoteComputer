"""WebSocket streaming session manager, RDP2 binary delta protocol, adaptive quality controller, audio streamer, privacy mode, and input router."""

import asyncio
import json
import logging
import struct
import time
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from core.audio_service import AudioService
from core.clipboard import ClipboardManager
from core.config_store import ConfigStore
from core.input_handler import InputHandler
from core.privacy_mode import PrivacyModeManager
from core.screen import (
    DeltaCaptureState,
    FORMAT_CODE_JPEG,
    FORMAT_CODE_NONE,
    FORMAT_CODE_WEBP,
    FRAME_CURSOR_ONLY,
    FRAME_DELTA,
    FRAME_KEYFRAME,
    FRAME_SETTLING,
    ScreenCapture,
)

logger = logging.getLogger(__name__)

# 30-byte binary header for RDP2 single-packet video frames:
# magic(4s) | frame_type(B) | format_code(B) | seq(I) | full_w(H) | full_h(H) |
# rect_x(H) | rect_y(H) | rect_w(H) | rect_h(H) | cursor_x(H) | cursor_y(H) | orig_w(H) | orig_h(H)
RDP2_HEADER = struct.Struct("!4sBBIHHHHHHHHHH")


class ClientSession:
    """Manages an individual remote desktop streaming session over WebSocket."""

    def __init__(
        self,
        websocket: WebSocket,
        screen_capture: ScreenCapture,
        input_handler: InputHandler,
        clipboard_manager: ClipboardManager,
        default_fps: int = 60,
        config_store: Optional[ConfigStore] = None,
        privacy_manager: Optional[PrivacyModeManager] = None,
        audio_service: Optional[AudioService] = None,
        auth_manager: Optional[Any] = None,
        session_token: Optional[str] = None,
        use_rdp2: bool = False,
    ):
        self.ws = websocket
        self.screen = screen_capture
        self.input = input_handler
        self.clipboard = clipboard_manager
        self.config_store = config_store
        self.privacy = privacy_manager or PrivacyModeManager()
        self.audio = audio_service or AudioService()
        self.auth_manager = auth_manager
        self.session_token = session_token
        self.use_rdp2 = bool(use_rdp2)

        # Per-session isolated streaming configuration (never clobbers other sessions)
        self.fps = max(5, min(60, default_fps))
        self.quality = max(10, min(100, screen_capture.quality))
        self.user_scale = max(0.25, min(1.0, screen_capture.scale))
        self.active_scale = self.user_scale
        self.format = screen_capture.format
        self.subsampling = screen_capture.subsampling
        self.monitor_index = screen_capture.monitor_index

        self.adaptive_quality = False
        self.skip_static_frames = True
        self.force_keyframe = True
        self.delta_state = DeltaCaptureState()
        self.last_rtt_ms = 15.0

        self.is_running = False
        self.frame_seq = 0
        self.last_client_ack_seq = 0
        self.max_inflight_frames = 12 if self.use_rdp2 else 4
        self._last_sent_time = 0.0
        self._ws_send_lock = asyncio.Lock()

        self._stream_task: Optional[asyncio.Task] = None
        self._clipboard_task: Optional[asyncio.Task] = None
        self._audio_task: Optional[asyncio.Task] = None

    def _perm_allowed(self, perm_key: str) -> bool:
        """Check if host security permissions allow the requested capability."""
        if self.config_store is None:
            return True
        perms = self.config_store.get_permissions()
        return bool(perms.get(perm_key, True))

    async def _send_text(self, payload: str) -> None:
        async with self._ws_send_lock:
            await self.ws.send_text(payload)

    async def _send_bytes(self, payload: bytes) -> None:
        async with self._ws_send_lock:
            await self.ws.send_bytes(payload)

    async def start(self) -> None:
        """Start streaming, audio, and input listener loops."""
        self.is_running = True
        self._stream_task = asyncio.create_task(self._streaming_loop())
        self._clipboard_task = asyncio.create_task(self._clipboard_monitor_loop())
        self._audio_task = asyncio.create_task(self._audio_streaming_loop())

        try:
            await self._incoming_message_loop()
        except WebSocketDisconnect:
            logger.info("Client disconnected.")
        except Exception as e:
            logger.error(f"Session error: {e}")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Clean up session tasks, release inputs, and disable privacy curtain."""
        self.is_running = False

        for task in (self._stream_task, self._clipboard_task, self._audio_task):
            if task and not task.done():
                task.cancel()

        # Release any stuck keys or mouse buttons
        self.input.release_all()

        # Automatically turn off Privacy Mode if it was left on during the session
        if self.privacy.enabled:
            self.privacy.set_privacy_mode(False)

        try:
            await self.ws.close()
        except Exception:
            pass

    async def _streaming_loop(self) -> None:
        """Continuously captures, encodes, and transmits screen frames (RDP2 binary delta or legacy mode)."""
        last_frame_time = 0.0

        while self.is_running:
            if (
                self.auth_manager
                and self.session_token
                and not self.auth_manager.validate_session_token(self.session_token)
            ):
                logger.info("Session token revoked or expired; terminating active stream.")
                self.is_running = False
                break

            target_interval = 1.0 / max(5, self.fps)
            now = time.perf_counter()
            elapsed = now - last_frame_time

            if elapsed < target_interval:
                await asyncio.sleep(target_interval - elapsed)

            # Sliding-window backpressure check with fast stall auto-recovery
            stall_timeout = 0.25 if self.use_rdp2 else 0.35
            if (self.frame_seq - self.last_client_ack_seq) > self.max_inflight_frames:
                if (time.perf_counter() - self._last_sent_time) < stall_timeout:
                    await asyncio.sleep(0.003)
                    continue
                self.last_client_ack_seq = self.frame_seq

            last_frame_time = time.perf_counter()

            try:
                if self.use_rdp2:
                    if self.adaptive_quality:
                        self._adjust_adaptive_quality(self.last_rtt_ms)
                    eff_scale = self.active_scale if self.adaptive_quality else self.user_scale
                    need_kf = self.force_keyframe

                    frame_bytes, frame_type, meta = await asyncio.to_thread(
                        self.screen.capture_delta_frame,
                        self.delta_state,
                        self.monitor_index,
                        self.format,
                        self.quality,
                        eff_scale,
                        self.subsampling,
                        need_kf,
                    )

                    if frame_type in (FRAME_KEYFRAME, FRAME_SETTLING):
                        self.force_keyframe = False

                    cx_u16 = max(0, min(65535, int(round(meta.get("cursor_x", 0.5) * 65535.0))))
                    cy_u16 = max(0, min(65535, int(round(meta.get("cursor_y", 0.5) * 65535.0))))

                    if frame_type == FRAME_CURSOR_ONLY:
                        if not meta.get("cursor_moved", False):
                            continue
                        header = RDP2_HEADER.pack(
                            b"RDP2",
                            FRAME_CURSOR_ONLY,
                            FORMAT_CODE_NONE,
                            0,  # seq=0 does not consume inflight ack window
                            min(65535, meta["full_w"]),
                            min(65535, meta["full_h"]),
                            0,
                            0,
                            0,
                            0,
                            cx_u16,
                            cy_u16,
                            min(65535, meta["orig_w"]),
                            min(65535, meta["orig_h"]),
                        )
                        await self._send_bytes(header)
                        continue

                    self.frame_seq += 1
                    self._last_sent_time = time.perf_counter()
                    fmt_code = FORMAT_CODE_WEBP if meta["format"] == "webp" else FORMAT_CODE_JPEG
                    header = RDP2_HEADER.pack(
                        b"RDP2",
                        frame_type,
                        fmt_code,
                        self.frame_seq,
                        min(65535, meta["full_w"]),
                        min(65535, meta["full_h"]),
                        min(65535, meta["rect_x"]),
                        min(65535, meta["rect_y"]),
                        min(65535, meta["rect_w"]),
                        min(65535, meta["rect_h"]),
                        cx_u16,
                        cy_u16,
                        min(65535, meta["orig_w"]),
                        min(65535, meta["orig_h"]),
                    )
                    await self._send_bytes(header + frame_bytes)
                else:
                    # Legacy 2-message protocol (JSON metadata + binary frame) for backward compatibility
                    frame_bytes, has_changed, meta = await asyncio.to_thread(self.screen.capture_frame)

                    if (
                        self.skip_static_frames
                        and self.frame_seq > 1
                        and not has_changed
                        and (time.perf_counter() - self._last_sent_time) < 1.5
                    ):
                        continue

                    self.frame_seq += 1
                    self._last_sent_time = time.perf_counter()
                    meta["seq"] = self.frame_seq

                    async with self._ws_send_lock:
                        await self.ws.send_text(
                            json.dumps(
                                {
                                    "type": "frame_meta",
                                    "seq": self.frame_seq,
                                    "width": meta["width"],
                                    "height": meta["height"],
                                    "scaled_width": meta["scaled_width"],
                                    "scaled_height": meta["scaled_height"],
                                    "format": meta["format"],
                                    "size": meta["bytes_size"],
                                    "cursor_x": meta.get("cursor_x", 0.5),
                                    "cursor_y": meta.get("cursor_y", 0.5),
                                    "timestamp": meta["timestamp"],
                                }
                            )
                        )
                        await self.ws.send_bytes(frame_bytes)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in streaming loop: {e}")
                await asyncio.sleep(0.05)

    async def _audio_streaming_loop(self) -> None:
        """Streams remote audio chunks when audio is unmuted and permitted."""
        while self.is_running:
            try:
                if not self.audio.muted and self._perm_allowed("allow_audio"):
                    chunk = await asyncio.to_thread(self.audio.capture_audio_chunk, 180)
                    await self._send_text(json.dumps(chunk))
                    await asyncio.sleep(0.18)
                else:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Audio stream loop error: {e}")
                await asyncio.sleep(0.5)

    async def _incoming_message_loop(self) -> None:
        """Handles incoming control, input, ping, audio, privacy, and chat packets from client."""
        while self.is_running:
            raw_msg = await self.ws.receive_text()
            try:
                msg = json.loads(raw_msg)
                await self._process_client_message(msg)
            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.debug(f"Error handling client message: {e}")

    def _adjust_adaptive_quality(self, rtt_ms: float) -> None:
        """
        Motion-aware adaptive quality controller.
        Preserves native 1:1 resolution during normal RTT spikes and relies on automatic 220ms
        4:4:4 q=92 settling frames so static desktop text is always razor-sharp.
        """
        if not self.adaptive_quality:
            self.active_scale = self.user_scale
            return

        inflight = max(0, self.frame_seq - self.last_client_ack_seq)
        if inflight <= 3 and rtt_ms < 60:
            self.fps = 60
            self.quality = 82
            self.subsampling = 0
            self.active_scale = self.user_scale
        elif inflight <= 8:
            self.fps = 55 if rtt_ms < 150 else 45
            self.quality = 76
            self.subsampling = 2  # Fast motion encode; auto-refines to 4:4:4 q=92 220ms after motion stops
            self.active_scale = self.user_scale
        else:
            # Sustained bandwidth congestion -> slight scale step-down while keeping readable text
            self.fps = 35
            self.quality = 70
            self.subsampling = 2
            self.active_scale = min(self.user_scale, 0.85)

    async def _process_client_message(self, msg: Dict[str, Any]) -> None:
        """Dispatch client commands to core modules while enforcing host permissions."""
        msg_type = msg.get("type")

        # 1. Frame acknowledgment for sliding-window flow control
        if msg_type == "ack":
            seq = msg.get("seq", 0)
            if seq > self.last_client_ack_seq:
                self.last_client_ack_seq = seq

        # 2. Ping / Pong for latency measurement & adaptive bitrate
        elif msg_type == "ping":
            client_time = msg.get("t", 0)
            if "last_rtt" in msg:
                self.last_rtt_ms = float(msg["last_rtt"])
                self._adjust_adaptive_quality(self.last_rtt_ms)
            await self._send_text(
                json.dumps(
                    {
                        "type": "pong",
                        "t": client_time,
                        "server_time": int(time.time() * 1000),
                    }
                )
            )

        # 3. Mouse & Keyboard Input (requires allow_keyboard_mouse permission)
        elif msg_type in (
            "mousemove",
            "mousemove_rel",
            "mousedown",
            "mouseup",
            "click",
            "wheel",
            "keydown",
            "keyup",
            "type_text",
            "special",
        ):
            if not self._perm_allowed("allow_keyboard_mouse"):
                return

            mon_rect = self.screen.get_monitor_rect_by_index(self.monitor_index)
            nx = msg.get("x") if isinstance(msg.get("x"), (int, float)) else None
            ny = msg.get("y") if isinstance(msg.get("y"), (int, float)) else None

            if msg_type == "mousemove":
                self.input.handle_mouse_move(msg.get("x", 0.0), msg.get("y", 0.0), mon_rect)
            elif msg_type == "mousemove_rel":
                self.input.handle_mouse_move_relative(msg.get("dx", 0.0), msg.get("dy", 0.0))
            elif msg_type == "mousedown":
                self.input.handle_mouse_button(msg.get("button", 0), is_down=True, norm_x=nx, norm_y=ny, monitor_rect=mon_rect)
            elif msg_type == "mouseup":
                self.input.handle_mouse_button(msg.get("button", 0), is_down=False, norm_x=nx, norm_y=ny, monitor_rect=mon_rect)
            elif msg_type == "click":
                self.input.handle_mouse_click(msg.get("button", 0), msg.get("count", 1), norm_x=nx, norm_y=ny, monitor_rect=mon_rect)
            elif msg_type == "wheel":
                self.input.handle_mouse_scroll(msg.get("dx", 0.0), msg.get("dy", 0.0))
            elif msg_type == "keydown":
                self.input.handle_keyboard(msg.get("key", ""), msg.get("code"), is_down=True)
            elif msg_type == "keyup":
                self.input.handle_keyboard(msg.get("key", ""), msg.get("code"), is_down=False)
            elif msg_type == "type_text":
                text = msg.get("text", "")
                if text:
                    self.input.type_text(text)
            elif msg_type == "special":
                self.input.trigger_special_action(msg.get("action", ""))

        # 4. View-Only Toggle
        elif msg_type == "set_view_only":
            self.input.view_only = bool(msg.get("view_only", False))
            await self._send_text(
                json.dumps(
                    {
                        "type": "view_only_status",
                        "view_only": self.input.view_only,
                    }
                )
            )

        # 5. RustDesk Privacy Mode Toggle
        elif msg_type == "privacy_mode":
            if self._perm_allowed("privacy_mode_allowed"):
                enable = bool(msg.get("enabled", False))
                status = self.privacy.set_privacy_mode(enable)
                await self._send_text(
                    json.dumps(
                        {
                            "type": "privacy_mode_status",
                            "enabled": status["enabled"],
                            "mode": status["mode"],
                        }
                    )
                )

        # 6. RustDesk Remote Audio Mute/Unmute Toggle
        elif msg_type == "audio_toggle":
            muted = bool(msg.get("muted", True))
            self.audio.set_muted(muted)
            await self._send_text(
                json.dumps(
                    {
                        "type": "audio_status",
                        "muted": self.audio.muted,
                    }
                )
            )

        # 7. In-Session Live Text Chat
        elif msg_type == "chat":
            text = msg.get("text", "").strip()
            sender = msg.get("sender", "Client")
            if text:
                await self._send_text(
                    json.dumps(
                        {
                            "type": "chat_message",
                            "sender": sender,
                            "text": text,
                            "time": time.strftime("%H:%M:%S"),
                        }
                    )
                )

        # 8. Dynamic Stream Quality, Resolution Scale, Chroma Subsampling & FPS Configuration
        elif msg_type == "config":
            if msg.get("proto") == "rdp2":
                self.use_rdp2 = True
                self.max_inflight_frames = 12
                self.force_keyframe = True
            if "adaptive" in msg:
                self.adaptive_quality = bool(msg["adaptive"])
                if not self.adaptive_quality:
                    self.active_scale = self.user_scale
            if "skip_static" in msg:
                self.skip_static_frames = bool(msg["skip_static"])
            if "fps" in msg:
                self.fps = max(5, min(60, int(msg["fps"])))
            if "quality" in msg:
                self.quality = max(10, min(100, int(msg["quality"])))
                if not self.use_rdp2:
                    self.screen.quality = self.quality
                self.force_keyframe = True
            if "scale" in msg:
                self.user_scale = max(0.25, min(1.0, float(msg["scale"])))
                self.active_scale = self.user_scale
                if not self.use_rdp2:
                    self.screen.scale = self.user_scale
                self.force_keyframe = True
            if "subsampling" in msg:
                sub = int(msg["subsampling"])
                if sub in (0, 1, 2):
                    self.subsampling = sub
                    self.force_keyframe = True
            if "format" in msg:
                fmt = str(msg["format"]).lower()
                if fmt == "jpg":
                    fmt = "jpeg"
                if fmt in ("webp", "jpeg"):
                    self.format = fmt
                    if not self.use_rdp2:
                        self.screen.format = fmt
                    self.force_keyframe = True
            if "monitor" in msg:
                mon_idx = int(msg["monitor"])
                if self.screen.set_monitor(mon_idx):
                    self.monitor_index = mon_idx
                    self.delta_state.reset()
                    self.force_keyframe = True
            if msg.get("force_keyframe"):
                self.force_keyframe = True

            await self._send_text(
                json.dumps(
                    {
                        "type": "config_updated",
                        "fps": self.fps,
                        "quality": self.quality,
                        "scale": self.user_scale,
                        "subsampling": self.subsampling,
                        "format": self.format,
                        "monitor": self.monitor_index,
                        "adaptive": self.adaptive_quality,
                    }
                )
            )

        # 9. Clipboard sync from client
        elif msg_type == "clipboard_set":
            if self._perm_allowed("allow_clipboard"):
                text = msg.get("text", "")
                if text:
                    self.clipboard.set_text(text)

    async def _clipboard_monitor_loop(self) -> None:
        """Polls host clipboard for external changes and notifies client."""
        while self.is_running:
            try:
                if self._perm_allowed("allow_clipboard"):
                    new_text = self.clipboard.has_changed()
                    if new_text is not None:
                        await self._send_text(
                            json.dumps(
                                {
                                    "type": "clipboard_sync",
                                    "text": new_text,
                                }
                            )
                        )
            except Exception as e:
                logger.debug(f"Clipboard poll error: {e}")
            await asyncio.sleep(1.0)
