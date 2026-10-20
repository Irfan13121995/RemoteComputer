"""Rendezvous Server (hbbs) & Relay Broker (hbbr) + Global Cloud Rendezvous Mediator.
Inspired by RustDesk's src/rendezvous_mediator.rs and rustdesk-server.
Allows Partner ID registration, lookup across LAN subnets AND across the global internet
(4G/5G Mobile Data / external Wi-Fi networks), plus WebSocket relay brokering.
"""

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

GLOBAL_RENDEZVOUS_BASE = "https://ntfy.sh"
GLOBAL_TOPIC_PREFIX = "rdpro_v2_id_"


class RendezvousBroker:
    """Built-in self-hosted Rendezvous (hbbs), Global Cloud Directory, and Relay (hbbr) broker."""

    def __init__(self, peer_ttl_seconds: int = 90, enable_cloud_lookup: bool = True):
        self.peer_ttl = peer_ttl_seconds
        self.enable_cloud_lookup = enable_cloud_lookup
        self._lock = threading.RLock()
        # normalized_id -> registration dict
        self._registry: Dict[str, Dict[str, Any]] = {}
        # relay_channel_id -> {"host": Optional[WebSocket], "client": Optional[WebSocket]}
        self._relay_channels: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def normalize_id(machine_id: str) -> str:
        return "".join(ch for ch in str(machine_id) if ch.isdigit())

    @classmethod
    def get_cloud_topic(cls, machine_id: str) -> str:
        norm = cls.normalize_id(machine_id)
        return f"{GLOBAL_TOPIC_PREFIX}{norm}"

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
        public_url: str = "",
    ) -> Dict[str, Any]:
        """Register or heartbeat a host machine ID with the Rendezvous broker."""
        norm_id = self.normalize_id(machine_id)
        if not norm_id:
            raise ValueError("Invalid machine_id")

        proto = "https" if is_ssl else "http"
        lan_ip = next((ip for ip in ips if ip not in ("127.0.0.1", "::1")), public_ip or "127.0.0.1")
        lan_url = f"{proto}://{lan_ip}:{int(port)}"

        with self._lock:
            existing = self._registry.get(norm_id, {})
            effective_public_url = public_url.strip() or existing.get("public_url", "")

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
            "lan_url": lan_url,
            "public_url": effective_public_url,
            "direct_url": effective_public_url or lan_url,
            "last_heartbeat": time.time(),
            "online": True,
        }

        with self._lock:
            self._registry[norm_id] = record
        return record

    def lookup_host(self, machine_id: str, allow_cloud: bool = True) -> Optional[Dict[str, Any]]:
        """Look up connection endpoints for a 9-digit Partner ID (local registry + global cloud directory)."""
        norm_id = self.normalize_id(machine_id)
        if not norm_id:
            return None

        now = time.time()
        with self._lock:
            record = self._registry.get(norm_id)
            if record and (now - record.get("last_heartbeat", 0)) <= self.peer_ttl:
                record["online"] = True
                return dict(record)

        # Query Global Cloud Rendezvous Directory for cross-network / mobile data resolution
        if allow_cloud and self.enable_cloud_lookup:
            cloud_rec = self.lookup_from_global_cloud(norm_id)
            if cloud_rec:
                with self._lock:
                    self._registry[norm_id] = cloud_rec
                return dict(cloud_rec)

        with self._lock:
            if record:
                record["online"] = (now - record.get("last_heartbeat", 0)) <= self.peer_ttl
                return dict(record)
        return None

    @staticmethod
    def _is_url_reachable(base_url: str, timeout: float = 0.65) -> bool:
        """Quickly check if a host base URL responds to /api/host-info."""
        if not base_url:
            return False
        try:
            url = f"{base_url.rstrip('/')}/api/host-info"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def resolve_best_url(self, machine_id: str, allow_cloud: bool = True) -> Optional[Dict[str, Any]]:
        """
        Resolve a 9-digit Partner ID to the best reachable URL:
        1. Try LAN URL (`http://192.168.x.x:8000`) first for low-latency local network streaming.
        2. Automatically fall back to Public HTTPS Tunnel URL (`https://...lhr.life`) when on another network / mobile data.
        """
        rec = self.lookup_host(machine_id, allow_cloud=allow_cloud)
        if not rec:
            return None

        lan_url = rec.get("lan_url") or rec.get("direct_url") or ""
        public_url = rec.get("public_url") or ""

        if lan_url and self._is_url_reachable(lan_url, timeout=0.55):
            rec["resolved_url"] = lan_url
            rec["url"] = lan_url
            rec["route"] = "lan"
            rec["source"] = "lan_direct"
            rec["record"] = dict(rec)
            return rec

        if public_url:
            rec["resolved_url"] = public_url
            rec["url"] = public_url
            rec["route"] = "internet_tunnel"
            rec["source"] = "public_tunnel"
            rec["record"] = dict(rec)
            return rec

        if lan_url:
            rec["resolved_url"] = lan_url
            rec["url"] = lan_url
            rec["route"] = "lan_unverified"
            rec["source"] = "rendezvous_server"
            rec["record"] = dict(rec)
            return rec

        return None

    @classmethod
    def publish_to_global_cloud(cls, record: Dict[str, Any]) -> bool:
        """Publish host connection endpoints (LAN IP + Public HTTPS Tunnel URL) to Global Cloud Rendezvous."""
        norm_id = cls.normalize_id(record.get("machine_id", ""))
        if not norm_id:
            return False
        topic = cls.get_cloud_topic(norm_id)
        url = f"{GLOBAL_RENDEZVOUS_BASE}/{topic}"
        payload = {
            "machine_id": record.get("machine_id"),
            "normalized_id": norm_id,
            "hostname": record.get("hostname", "RemoteDesk-PC"),
            "os": record.get("os", "Windows"),
            "ips": record.get("ips", []),
            "lan_ip": record.get("lan_ip", ""),
            "port": record.get("port", 8000),
            "ssl": record.get("ssl", False),
            "lan_url": record.get("lan_url", ""),
            "public_url": record.get("public_url", ""),
            "direct_url": record.get("public_url") or record.get("lan_url", ""),
            "timestamp": time.time(),
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug(f"Global cloud rendezvous publish error: {e}")
            return False

    @classmethod
    def lookup_from_global_cloud(cls, machine_id: str) -> Optional[Dict[str, Any]]:
        """Query Global Cloud Rendezvous for the latest published endpoints of a 9-digit Partner ID."""
        norm_id = cls.normalize_id(machine_id)
        if not norm_id:
            return None
        topic = cls.get_cloud_topic(norm_id)
        url = f"{GLOBAL_RENDEZVOUS_BASE}/{topic}/json?poll=1&since=12h"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                raw_lines = resp.read().decode("utf-8", errors="replace").strip().splitlines()
            for line in reversed(raw_lines):
                if not line.strip():
                    continue
                envelope = json.loads(line)
                if envelope.get("event") != "message":
                    continue
                msg_str = envelope.get("message", "")
                data = json.loads(msg_str)
                if cls.normalize_id(data.get("machine_id", "")) == norm_id:
                    data["last_heartbeat"] = time.time()
                    data["online"] = True
                    return data
        except Exception as e:
            logger.debug(f"Global cloud rendezvous lookup error for {norm_id}: {e}")
        return None

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
    """Background client mediator that registers this host with local, custom, and Global Cloud Rendezvous."""

    def __init__(
        self,
        local_broker: RendezvousBroker,
        machine_id: str,
        http_port: int = 8000,
        is_ssl: bool = False,
        rendezvous_url: str = "",
        relay_key: str = "",
        public_url_getter: Optional[Any] = None,
    ):
        self.local_broker = local_broker
        self.machine_id = machine_id
        self.http_port = http_port
        self.is_ssl = is_ssl
        self.rendezvous_url = rendezvous_url.strip()
        self.relay_key = relay_key.strip()
        self.public_url_getter = public_url_getter
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_cloud_signature: str = ""
        self._last_cloud_publish_time: float = 0.0

    def register_once(self, force_cloud: bool = False) -> Dict[str, Any]:
        """Perform immediate registration with local broker, global cloud directory, and optional custom server."""
        ips = get_local_ips()
        hostname = platform.node() or "RemoteDesk-Host"
        os_name = f"{platform.system()} {platform.release()}"
        pub_url = ""
        if callable(self.public_url_getter):
            try:
                pub_url = self.public_url_getter() or ""
            except Exception:
                pub_url = ""

        record = self.local_broker.register_host(
            machine_id=self.machine_id,
            hostname=hostname,
            os_name=os_name,
            ips=ips,
            port=self.http_port,
            is_ssl=self.is_ssl,
            relay_key=self.relay_key,
            public_url=pub_url,
        )

        # Publish to Global Cloud Rendezvous when endpoint changes or every 4 minutes
        sig = f"{record.get('lan_url')}|{record.get('public_url')}"
        now = time.time()
        if force_cloud or sig != self._last_cloud_signature or (now - self._last_cloud_publish_time) > 240:
            if self.local_broker.publish_to_global_cloud(record):
                self._last_cloud_signature = sig
                self._last_cloud_publish_time = now

        # If custom rendezvous server URL is configured, push registration via HTTP POST
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
                    "public_url": pub_url,
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
                logger.debug(f"Custom rendezvous registration heartbeat failed ({url}): {e}")

        return record

    def trigger_publish_now(self) -> None:
        """Immediately publish updated tunnel/LAN URLs to local and global rendezvous in a background thread."""
        threading.Thread(target=self.register_once, kwargs={"force_cloud": True}, daemon=True).start()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
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
