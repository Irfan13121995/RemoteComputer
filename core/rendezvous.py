"""Rendezvous Server (hbbs) & Relay Broker (hbbr) + Client Mediator.
Inspired by RustDesk's src/rendezvous_mediator.rs and rustdesk-server.
Allows Partner ID registration, lookup across subnets, and WebSocket relay brokering.
"""

import asyncio
import json
import logging
import platform
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from core.tls import get_local_ips

logger = logging.getLogger(__name__)


class RendezvousBroker:
    """Built-in self-hosted Rendezvous (hbbs) and Relay (hbbr) broker."""

    def __init__(self, peer_ttl_seconds: int = 90):
        self.peer_ttl = peer_ttl_seconds
        self._lock = threading.RLock()
        # normalized_id -> registration dict
        self._registry: Dict[str, Dict[str, Any]] = {}
        # relay_channel_id -> {"host_ws": Optional[WebSocket], "client_ws": Optional[WebSocket]}
        self._relay_channels: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def normalize_id(machine_id: str) -> str:
        return "".join(ch for ch in str(machine_id) if ch.isdigit())

    def register_host(
        self,
        machine_id: str,
        hostname: str,
        os_name: str,
        ips: List[str],
        port: int,
        is_ssl: bool = False,
        relay_key: str = "",
        public_ip: str = "127.0.0.1",
    ) -> Dict[str, Any]:
        """Register or heartbeat a host machine ID with the Rendezvous broker."""
        norm_id = self.normalize_id(machine_id)
        if not norm_id:
            raise ValueError("Invalid machine_id")

        proto = "https" if is_ssl else "http"
        lan_ip = next((ip for ip in ips if ip not in ("127.0.0.1", "::1")), public_ip or "127.0.0.1")

        record = {
            "machine_id": machine_id,
            "normalized_id": norm_id,
            "hostname": hostname or "RemoteDesk-Host",
            "os": os_name or platform.system(),
            "ips": ips,
            "lan_ip": lan_ip,
            "public_ip": public_ip,
            "port": int(port),
            "ssl": bool(is_ssl),
            "direct_url": f"{proto}://{lan_ip}:{port}",
            "last_heartbeat": time.time(),
            "online": True,
        }

        with self._lock:
            self._registry[norm_id] = record
        return record

    def lookup_host(self, machine_id: str) -> Optional[Dict[str, Any]]:
        """Look up connection endpoints for a 9-digit Partner ID."""
        norm_id = self.normalize_id(machine_id)
        now = time.time()
        with self._lock:
            record = self._registry.get(norm_id)
            if not record:
                return None
            if (now - record.get("last_heartbeat", 0)) > self.peer_ttl:
                record["online"] = False
            else:
                record["online"] = True
            return dict(record)

    def list_registered_hosts(self) -> List[Dict[str, Any]]:
        """Return all registered hosts in the Rendezvous server."""
        now = time.time()
        results = []
        with self._lock:
            for norm_id, rec in list(self._registry.items()):
                rec["online"] = (now - rec.get("last_heartbeat", 0)) <= self.peer_ttl
                results.append(dict(rec))
        return results

    async def bridge_relay_websocket(self, channel_id: str, role: str, ws: WebSocket) -> None:
        """
        Bridge two WebSocket peers (role='host' and role='client') over hbbr-style relay channel.
        Forwards both text and binary frames seamlessly between the two sockets.
        """
        channel = self._relay_channels.setdefault(channel_id, {"host": None, "client": None})
        channel[role] = ws
        other_role = "client" if role == "host" else "host"

        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                peer_ws: Optional[WebSocket] = channel.get(other_role)
                if peer_ws is not None:
                    if "text" in msg and msg["text"] is not None:
                        await peer_ws.send_text(msg["text"])
                    elif "bytes" in msg and msg["bytes"] is not None:
                        await peer_ws.send_bytes(msg["bytes"])
        except Exception as e:
            logger.debug(f"Relay channel {channel_id} ({role}) closed: {e}")
        finally:
            if channel.get(role) is ws:
                channel[role] = None
            if channel.get("host") is None and channel.get("client") is None:
                self._relay_channels.pop(channel_id, None)


class RendezvousClientMediator:
    """Background client mediator that registers this host with a remote or local Rendezvous server."""

    def __init__(
        self,
        local_broker: RendezvousBroker,
        machine_id: str,
        http_port: int = 8000,
        is_ssl: bool = False,
        rendezvous_url: str = "",
        relay_key: str = "",
    ):
        self.local_broker = local_broker
        self.machine_id = machine_id
        self.http_port = http_port
        self.is_ssl = is_ssl
        self.rendezvous_url = rendezvous_url.strip()
        self.relay_key = relay_key.strip()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def register_once(self) -> Dict[str, Any]:
        """Perform immediate registration with local broker and optional remote Rendezvous server."""
        ips = get_local_ips()
        hostname = platform.node() or "RemoteDesk-Host"
        os_name = f"{platform.system()} {platform.release()}"

        # Always register in local broker
        record = self.local_broker.register_host(
            machine_id=self.machine_id,
            hostname=hostname,
            os_name=os_name,
            ips=ips,
            port=self.http_port,
            is_ssl=self.is_ssl,
            relay_key=self.relay_key,
        )

        # If remote rendezvous server URL is configured, push registration via HTTP POST
        if self.rendezvous_url:
            target = self.rendezvous_url.rstrip("/")
            if not target.startswith("http://") and not target.startswith("https://"):
                target = f"http://{target}"
            url = f"{target}/api/rendezvous/register"
            payload = json.dumps(
                {
                    "machine_id": self.machine_id,
                    "hostname": hostname,
                    "os": os_name,
                    "ips": ips,
                    "port": self.http_port,
                    "ssl": self.is_ssl,
                    "relay_key": self.relay_key,
                }
            ).encode("utf-8")
            try:
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    resp.read()
            except Exception as e:
                logger.debug(f"Remote rendezvous registration heartbeat failed ({url}): {e}")

        return record

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.register_once()
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                self.register_once()
            except Exception as e:
                logger.debug(f"Rendezvous heartbeat error: {e}")
            time.sleep(20.0)
