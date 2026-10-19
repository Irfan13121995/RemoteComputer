"""WebSocket streaming session manager, adaptive bitrate controller, audio streamer, privacy mode, and input router."""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from core.audio_service import AudioService
from core.clipboard import ClipboardManager
from core.config_store import ConfigStore
from core.input_handler import InputHandler
from core.privacy_mode import PrivacyModeManager
from core.screen import ScreenCapture

logger = logging.getLogger(__name__)


class ClientSession:
    """Manages an individual remote desktop streaming session over WebSocket."""

    def __init__(
        self,
        websocket: WebSocket,
        screen_capture: ScreenCapture,
        input_handler: InputHandler,
        clipboard_manager: ClipboardManager,
        default_fps: int = 30,
        config_store: Optional[ConfigStore] = None,
        privacy_manager: Optional[PrivacyModeManager] = None,
        audio_service: Optional[AudioService] = None,
    ):
        self.ws = websocket
        self.screen = screen_capture
        self.input = input_handler
        self.clipboard = clipboard_manager
        self.config_store = config_store
        self.privacy = privacy_manager or PrivacyModeManager()
        self.audio = audio_service or AudioService()

        self.fps = max(5, min(60, default_fps))
        self.adaptive_quality = False
        self.skip_static_frames = False
        self.last_rtt_ms = 20

        self.is_running = False
        self.frame_seq = 0
        self.last_client_ack_seq = 0
        self.max_inflight_frames = 1  # Low latency backpressure: 1 in-flight frame max
        self._last_sent_time = 0.0

        self._stream_task: Optional[asyncio.Task] = None
        self._clipboard_task: Optional[asyncio.Task] = None
        self._audio_task: Optional[asyncio.Task] = None

    def _perm_allowed(self, perm_key: str) -> bool:
        """Check if host security permissions allow the requested capability."""
        if self.config_store is None:
            return True
        perms = self.config_store.get_permissions()
        return bool(perms.get(perm_key, True))

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
        """Continuously captures, encodes, and transmits screen frames."""
        last_frame_time = 0.0

        while self.is_running:
            target_interval = 1.0 / self.fps
            now = time.time()
            elapsed = now - last_frame_time

            if elapsed < target_interval:
                await asyncio.sleep(target_interval - elapsed)

            # Flow control: Backpressure check
            if (self.frame_seq - self.last_client_ack_seq) > self.max_inflight_frames:
                await asyncio.sleep(0.005)
                continue

            last_frame_time = time.time()

            try:
                frame_bytes, has_changed, meta = await asyncio.to_thread(self.screen.capture_frame)

                # RustDesk-style static frame skipping when enabled (always send first frame or keyframe every 1.5s)
                if (
                    self.skip_static_frames
                    and self.frame_seq > 1
                    and not has_changed
                    and (time.time() - self._last_sent_time) < 1.5
                ):
                    continue

                self.frame_seq += 1
                self._last_sent_time = time.time()
                meta["seq"] = self.frame_seq

                # 1. Send frame metadata JSON (includes remote cursor position)
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

                # 2. Send binary frame bytes
                await self.ws.send_bytes(frame_bytes)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in streaming loop: {e}")
                await asyncio.sleep(0.1)

    async def _audio_streaming_loop(self) -> None:
        """Streams remote audio chunks when audio is unmuted and permitted."""
        while self.is_running:
            try:
                if not self.audio.muted and self._perm_allowed("allow_audio"):
                    chunk = await asyncio.to_thread(self.audio.capture_audio_chunk, 180)
                    await self.ws.send_text(json.dumps(chunk))
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
        """Dynamically adjust quality and FPS based on network RTT latency (RustDesk Auto Quality)."""
        if not self.adaptive_quality:
            return
        if rtt_ms < 25:
            self.screen.quality = 80
            self.fps = 45
        elif rtt_ms < 75:
            self.screen.quality = 65
            self.fps = 30
        elif rtt_ms < 150:
            self.screen.quality = 50
            self.fps = 20
        else:
            self.screen.quality = 35
            self.fps = 15

    async def _process_client_message(self, msg: Dict[str, Any]) -> None:
        """Dispatch client commands to core modules while enforcing host permissions."""
        msg_type = msg.get("type")

        # 1. Frame acknowledgment for backpressure
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
            await self.ws.send_text(
                json.dumps(
                    {
                        "type": "pong",
                        "t": client_time,
                        "server_time": int(time.time() * 1000),
                    }
                )
            )

        # 3. Mouse & Keyboard Input (requires allow_keyboard_mouse permission)
        elif msg_type in ("mousemove", "mousemove_rel", "mousedown", "mouseup", "click", "wheel", "keydown", "keyup", "type_text", "special"):
            if not self._perm_allowed("allow_keyboard_mouse"):
                return

            if msg_type == "mousemove":
                mon_rect = self.screen.get_current_monitor_rect()
                self.input.handle_mouse_move(msg.get("x", 0.0), msg.get("y", 0.0), mon_rect)
            elif msg_type == "mousemove_rel":
                self.input.handle_mouse_move_relative(msg.get("dx", 0.0), msg.get("dy", 0.0))
            elif msg_type == "mousedown":
                self.input.handle_mouse_button(msg.get("button", 0), is_down=True)
            elif msg_type == "mouseup":
                self.input.handle_mouse_button(msg.get("button", 0), is_down=False)
            elif msg_type == "click":
                self.input.handle_mouse_click(msg.get("button", 0), msg.get("count", 1))
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
            await self.ws.send_text(
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
                await self.ws.send_text(
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
            await self.ws.send_text(
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
                await self.ws.send_text(
                    json.dumps(
                        {
                            "type": "chat_message",
                            "sender": sender,
                            "text": text,
                            "time": time.strftime("%H:%M:%S"),
                        }
                    )
                )

        # 8. Dynamic Stream Quality, Adaptive Bitrate & FPS Configuration
        elif msg_type == "config":
            if "adaptive" in msg:
                self.adaptive_quality = bool(msg["adaptive"])
            if "skip_static" in msg:
                self.skip_static_frames = bool(msg["skip_static"])
            if "fps" in msg:
                self.fps = max(5, min(60, int(msg["fps"])))
            if "quality" in msg:
                self.screen.quality = max(10, min(100, int(msg["quality"])))
            if "scale" in msg:
                self.screen.scale = max(0.25, min(1.0, float(msg["scale"])))
            if "format" in msg:
                fmt = str(msg["format"]).lower()
                if fmt in ("webp", "jpeg"):
                    self.screen.format = fmt
            if "monitor" in msg:
                mon_idx = int(msg["monitor"])
                self.screen.set_monitor(mon_idx)

            await self.ws.send_text(
                json.dumps(
                    {
                        "type": "config_updated",
                        "fps": self.fps,
                        "quality": self.screen.quality,
                        "scale": self.screen.scale,
                        "format": self.screen.format,
                        "monitor": self.screen.monitor_index,
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
                        await self.ws.send_text(
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
