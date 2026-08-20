"""WebSocket streaming session manager, backpressure controller, and chat/input router."""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from core.clipboard import ClipboardManager
from core.input_handler import InputHandler
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
    ):
        self.ws = websocket
        self.screen = screen_capture
        self.input = input_handler
        self.clipboard = clipboard_manager

        self.fps = max(5, min(60, default_fps))
        self.is_running = False
        self.frame_seq = 0
        self.last_client_ack_seq = 0
        self.max_inflight_frames = 1  # Low latency backpressure: 1 in-flight frame max

        self._stream_task: Optional[asyncio.Task] = None
        self._clipboard_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start streaming and input listener loops."""
        self.is_running = True
        self._stream_task = asyncio.create_task(self._streaming_loop())
        self._clipboard_task = asyncio.create_task(self._clipboard_monitor_loop())

        try:
            await self._incoming_message_loop()
        except WebSocketDisconnect:
            logger.info("Client disconnected.")
        except Exception as e:
            logger.error(f"Session error: {e}")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Clean up session tasks and release inputs."""
        self.is_running = False

        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
        if self._clipboard_task and not self._clipboard_task.done():
            self._clipboard_task.cancel()

        # Release any stuck keys or mouse buttons
        self.input.release_all()

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

            # Maintain target frame rate
            if elapsed < target_interval:
                await asyncio.sleep(target_interval - elapsed)

            # Flow control: Backpressure check
            if (self.frame_seq - self.last_client_ack_seq) > self.max_inflight_frames:
                await asyncio.sleep(0.005)
                continue

            last_frame_time = time.time()

            try:
                # Capture in worker thread to prevent event loop blocking
                frame_bytes, has_changed, meta = await asyncio.to_thread(self.screen.capture_frame)

                self.frame_seq += 1
                meta["seq"] = self.frame_seq

                # 1. Send frame metadata JSON
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

    async def _incoming_message_loop(self) -> None:
        """Handles incoming control, input, ping, and chat packets from client."""
        while self.is_running:
            raw_msg = await self.ws.receive_text()
            try:
                msg = json.loads(raw_msg)
                await self._process_client_message(msg)
            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.debug(f"Error handling client message: {e}")

    async def _process_client_message(self, msg: Dict[str, Any]) -> None:
        """Dispatch client commands to core modules."""
        msg_type = msg.get("type")

        # 1. Frame acknowledgment for backpressure
        if msg_type == "ack":
            seq = msg.get("seq", 0)
            if seq > self.last_client_ack_seq:
                self.last_client_ack_seq = seq

        # 2. Ping / Pong for latency measurement
        elif msg_type == "ping":
            client_time = msg.get("t", 0)
            await self.ws.send_text(
                json.dumps(
                    {
                        "type": "pong",
                        "t": client_time,
                        "server_time": int(time.time() * 1000),
                    }
                )
            )

        # 3. Mouse Movement (Normalized 0.0 - 1.0)
        elif msg_type == "mousemove":
            x = msg.get("x", 0.0)
            y = msg.get("y", 0.0)
            mon_rect = self.screen.get_current_monitor_rect()
            self.input.handle_mouse_move(x, y, mon_rect)

        # 4. Mouse Movement Relative (Pointer Lock)
        elif msg_type == "mousemove_rel":
            dx = msg.get("dx", 0.0)
            dy = msg.get("dy", 0.0)
            self.input.handle_mouse_move_relative(dx, dy)

        # 5. Mouse Button Press/Release
        elif msg_type == "mousedown":
            btn = msg.get("button", 0)
            self.input.handle_mouse_button(btn, is_down=True)

        elif msg_type == "mouseup":
            btn = msg.get("button", 0)
            self.input.handle_mouse_button(btn, is_down=False)

        # 6. Mouse Click
        elif msg_type == "click":
            btn = msg.get("button", 0)
            count = msg.get("count", 1)
            self.input.handle_mouse_click(btn, count)

        # 7. Mouse Wheel
        elif msg_type == "wheel":
            dx = msg.get("dx", 0.0)
            dy = msg.get("dy", 0.0)
            self.input.handle_mouse_scroll(dx, dy)

        # 8. Keyboard
        elif msg_type == "keydown":
            key = msg.get("key", "")
            code = msg.get("code")
            self.input.handle_keyboard(key, code, is_down=True)

        elif msg_type == "keyup":
            key = msg.get("key", "")
            code = msg.get("code")
            self.input.handle_keyboard(key, code, is_down=False)

        # 9. Type Full Text
        elif msg_type == "type_text":
            text = msg.get("text", "")
            if text:
                self.input.type_text(text)

        # 10. Special Macro Action
        elif msg_type == "special":
            action = msg.get("action", "")
            self.input.trigger_special_action(action)

        # 11. View-Only Toggle
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

        # 12. In-Session Live Text Chat
        elif msg_type == "chat":
            text = msg.get("text", "").strip()
            sender = msg.get("sender", "Client")
            if text:
                # Echo chat message back to participants with timestamp
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

        # 13. Dynamic Stream Quality & FPS Configuration
        elif msg_type == "config":
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

            # Echo updated config back
            await self.ws.send_text(
                json.dumps(
                    {
                        "type": "config_updated",
                        "fps": self.fps,
                        "quality": self.screen.quality,
                        "scale": self.screen.scale,
                        "format": self.screen.format,
                        "monitor": self.screen.monitor_index,
                    }
                )
            )

        # 14. Clipboard sync from client
        elif msg_type == "clipboard_set":
            text = msg.get("text", "")
            if text:
                self.clipboard.set_text(text)

    async def _clipboard_monitor_loop(self) -> None:
        """Polls host clipboard for external changes and notifies client."""
        while self.is_running:
            try:
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
