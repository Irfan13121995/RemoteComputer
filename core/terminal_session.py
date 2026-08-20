"""Interactive Terminal / Remote Shell runner with async streaming over WebSockets."""

import asyncio
import os
import platform
import subprocess
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect


class TerminalSession:
    """Manages an interactive shell subprocess session streamed to a WebSocket client."""

    def __init__(self, websocket: WebSocket):
        self.ws = websocket
        self.process: Optional[asyncio.subprocess.Process] = None
        self.is_running = False
        self.is_windows = platform.system() == "Windows"

    async def start(self) -> None:
        """Start the shell subprocess and read/write loops."""
        self.is_running = True

        # Choose shell
        if self.is_windows:
            shell_cmd = "powershell.exe -NoLogo -NoExit -Command -"
        else:
            shell_cmd = os.environ.get("SHELL", "/bin/bash")

        try:
            self.process = await asyncio.create_subprocess_shell(
                shell_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            # Send banner
            banner = f"--- Connected to {platform.system()} Remote Shell [{platform.node()}] ---\r\n\r\n"
            await self.ws.send_text(banner)

            # Run reader and writer concurrently
            reader_task = asyncio.create_task(self._stdout_reader())
            writer_task = asyncio.create_task(self._stdin_writer())

            done, pending = await asyncio.wait(
                [reader_task, writer_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            for t in pending:
                t.cancel()

        except Exception as e:
            try:
                await self.ws.send_text(f"\r\n[Terminal Error: {e}]\r\n")
            except Exception:
                pass
        finally:
            await self.close()

    async def _stdout_reader(self) -> None:
        """Read stdout from subprocess and stream to client."""
        while self.is_running and self.process and self.process.stdout:
            try:
                data = await self.process.stdout.read(1024)
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                await self.ws.send_text(text)
            except (asyncio.CancelledError, WebSocketDisconnect):
                break
            except Exception:
                break

    async def _stdin_writer(self) -> None:
        """Read client input from WebSocket and write to subprocess stdin."""
        while self.is_running and self.process and self.process.stdin:
            try:
                msg = await self.ws.receive_text()
                # If command string
                command_bytes = msg.encode("utf-8")
                if not msg.endswith("\n"):
                    command_bytes += b"\n"
                self.process.stdin.write(command_bytes)
                await self.process.stdin.drain()
            except (asyncio.CancelledError, WebSocketDisconnect):
                break
            except Exception:
                break

    async def close(self) -> None:
        """Terminate the shell subprocess."""
        self.is_running = False
        if self.process:
            try:
                self.process.terminate()
                await self.process.wait()
            except Exception:
                pass
            self.process = None
