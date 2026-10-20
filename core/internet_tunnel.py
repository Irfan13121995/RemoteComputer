"""Automatic Zero-Config Public HTTPS / WSS Internet Tunnel Manager.
Enables accessing RemoteDesk Pro from 4G/5G Mobile Data or any external Wi-Fi network
without router port-forwarding or third-party account setup.

Uses:
1. Built-in Windows 10/11 OpenSSH (`ssh -R 80:127.0.0.1:<port> nokey@localhost.run`)
2. Optional `cloudflared tunnel --url http://127.0.0.1:<port>` if installed on PATH
"""

import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000 if platform.system() == "Windows" else 0
_LHR_URL_RE = re.compile(r"https://[a-zA-Z0-9.-]+\.lhr\.life")
_CF_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


class InternetTunnelManager:
    """Manages a persistent background public HTTPS/WSS tunnel to the local FastAPI server."""

    def __init__(
        self,
        local_port: int = 8000,
        enabled: bool = True,
        on_url_change: Optional[Callable[[str], None]] = None,
    ):
        self.local_port = int(local_port)
        self.enabled = bool(enabled)
        self.on_url_change = on_url_change

        self._public_url: str = ""
        self._provider: str = "none"
        self._status: str = "stopped"
        self._proc: Optional[subprocess.Popen] = None
        self._running = False
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None

    @property
    def public_url(self) -> str:
        with self._lock:
            return self._public_url

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "status": self._status,
                "provider": self._provider,
                "public_url": self._public_url,
                "local_port": self.local_port,
            }

    def start(self) -> None:
        """Start background tunnel supervisor thread."""
        if self._running or not self.enabled:
            return
        self._running = True
        self._status = "starting"
        self._thread = threading.Thread(target=self._supervisor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop background tunnel and terminate child process."""
        self._running = False
        self._kill_proc()
        with self._lock:
            self._status = "stopped"
            self._public_url = ""

    def restart(self) -> None:
        """Force reconnect to obtain a fresh public tunnel URL."""
        self._kill_proc()
        if not self._running and self.enabled:
            self.start()

    def _kill_proc(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _set_public_url(self, url: str, provider: str) -> None:
        changed = False
        with self._lock:
            if url != self._public_url:
                self._public_url = url
                self._provider = provider
                self._status = "online" if url else "connecting"
                changed = True
        if changed and url:
            logger.info(f"[InternetTunnel] Public HTTPS/WSS Tunnel Active ({provider}): {url}")
            if self.on_url_change:
                try:
                    self.on_url_change(url)
                except Exception as e:
                    logger.debug(f"Tunnel callback error: {e}")

    def _supervisor_loop(self) -> None:
        """Keep tunnel process alive and automatically reconnect if dropped."""
        while self._running:
            if not self.enabled:
                time.sleep(2.0)
                continue

            # Try cloudflared first if installed, otherwise built-in Windows OpenSSH (localhost.run)
            cloudflared_bin = shutil.which("cloudflared")
            ssh_bin = shutil.which("ssh") or (
                r"C:\Windows\System32\OpenSSH\ssh.exe"
                if os.path.exists(r"C:\Windows\System32\OpenSSH\ssh.exe")
                else None
            )

            if cloudflared_bin:
                self._run_cloudflared(cloudflared_bin)
            elif ssh_bin:
                self._run_localhost_run_ssh(ssh_bin)
            else:
                with self._lock:
                    self._status = "unavailable"
                time.sleep(15.0)
                continue

            if self._running:
                time.sleep(3.0)

    def _run_localhost_run_ssh(self, ssh_bin: str) -> None:
        """Establish reverse HTTPS tunnel using built-in OpenSSH and localhost.run."""
        null_hosts = "NUL" if platform.system() == "Windows" else "/dev/null"
        cmd = [
            ssh_bin,
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            f"UserKnownHostsFile={null_hosts}",
            "-o",
            "ServerAliveInterval=25",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "ConnectTimeout=10",
            "-R",
            f"80:127.0.0.1:{self.local_port}",
            "nokey@localhost.run",
        ]
        try:
            with self._lock:
                self._status = "connecting"
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            with self._lock:
                self._proc = proc

            url_pattern = re.compile(r"https://[a-zA-Z0-9.-]+\.lhr\.life")
            assert proc.stdout is not None
            for line in proc.stdout:
                if not self._running:
                    break
                match = url_pattern.search(line)
                if match:
                    self._set_public_url(match.group(0), "localhost.run (SSH)")
        except Exception as e:
            logger.debug(f"SSH tunnel error: {e}")
        finally:
            self._kill_proc()

    def _run_cloudflared(self, cloudflared_bin: str) -> None:
        """Establish reverse HTTPS tunnel using Cloudflare Quick Tunnel if installed."""
        cmd = [
            cloudflared_bin,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{self.local_port}",
            "--no-autoupdate",
        ]
        try:
            with self._lock:
                self._status = "connecting"
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            with self._lock:
                self._proc = proc

            url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
            assert proc.stdout is not None
            for line in proc.stdout:
                if not self._running:
                    break
                match = url_pattern.search(line)
                if match:
                    self._set_public_url(match.group(0), "Cloudflare Quick Tunnel")
        except Exception as e:
            logger.debug(f"Cloudflared tunnel error: {e}")
        finally:
            self._kill_proc()
