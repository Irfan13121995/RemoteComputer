"""LAN Peer Discovery Service via UDP broadcast & Partner ID resolution.
Inspired by RustDesk's LAN discovery (UDP port 21116) and peer resolution.
"""

import concurrent.futures
import json
import logging
import platform
import socket
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

from core.tls import get_local_ips

logger = logging.getLogger(__name__)

DISCOVERY_UDP_PORT = 21116
DISCOVERY_MAGIC = "REMOTEDESK_PRO_V2"


class LanDiscoveryService:
    """Broadcasts host identity on LAN and discovers other RemoteDesk Pro peers."""

    def __init__(
        self,
        machine_id: str,
        http_port: int = 8000,
        is_ssl: bool = False,
        udp_port: int = DISCOVERY_UDP_PORT,
        peer_ttl_seconds: int = 45,
    ):
        self.machine_id = machine_id
        self.hostname = platform.node() or "RemoteDesk-PC"
        self.os_name = f"{platform.system()} {platform.release()}"
        self.http_port = http_port
        self.is_ssl = is_ssl
        self.udp_port = udp_port
        self.peer_ttl = peer_ttl_seconds

        self.enabled = True
        self._running = False
        self._lock = threading.RLock()
        # peer_id (normalized digits) -> peer dict
        self._peers: Dict[str, Dict[str, Any]] = {}

        self._listener_thread: Optional[threading.Thread] = None
        self._broadcaster_thread: Optional[threading.Thread] = None
        self._last_http_sweep_ts: float = 0.0
        self._sweep_lock = threading.Lock()

    @staticmethod
    def normalize_id(partner_id: str) -> str:
        """Normalize '360 523 800' -> '360523800'."""
        return "".join(ch for ch in str(partner_id) if ch.isdigit())

    def build_announcement_packet(self, msg_type: str = "announce") -> bytes:
        """Serialize host announcement packet for UDP broadcast."""
        local_ips = [ip for ip in get_local_ips() if ip not in ("127.0.0.1", "::1")]
        primary_ip = local_ips[0] if local_ips else "127.0.0.1"
        payload = {
            "magic": DISCOVERY_MAGIC,
            "type": msg_type,
            "machine_id": self.machine_id,
            "hostname": self.hostname,
            "os": self.os_name,
            "ip": primary_ip,
            "ips": local_ips,
            "port": self.http_port,
            "ssl": self.is_ssl,
            "timestamp": time.time(),
        }
        return json.dumps(payload).encode("utf-8")

    def process_incoming_packet(self, raw_bytes: bytes, sender_ip: str) -> Optional[Dict[str, Any]]:
        """Parse and register an incoming discovery UDP packet."""
        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except Exception:
            return None

        if data.get("magic") != DISCOVERY_MAGIC:
            return None

        peer_machine_id = data.get("machine_id", "")
        if not peer_machine_id:
            return None

        norm_id = self.normalize_id(peer_machine_id)
        if not norm_id:
            return None

        peer_ip = sender_ip if sender_ip and sender_ip not in ("0.0.0.0", "127.0.0.1") else data.get("ip", "127.0.0.1")
        peer_port = int(data.get("port", 8000))
        is_ssl = bool(data.get("ssl", False))
        proto = "https" if is_ssl else "http"

        peer_entry = {
            "machine_id": peer_machine_id,
            "normalized_id": norm_id,
            "hostname": data.get("hostname", "Unknown-PC"),
            "os": data.get("os", "Windows"),
            "ip": peer_ip,
            "port": peer_port,
            "ssl": is_ssl,
            "url": f"{proto}://{peer_ip}:{peer_port}",
            "last_seen": time.time(),
            "is_self": norm_id == self.normalize_id(self.machine_id),
        }

        with self._lock:
            self._peers[norm_id] = peer_entry

        return peer_entry

    def register_peer(
        self,
        machine_id: str,
        ip: str,
        port: int = 8000,
        hostname: str = "Remote-PC",
        os_name: str = "Windows",
        is_ssl: bool = False,
    ) -> Dict[str, Any]:
        """Manually or programmatically register a discovered/known peer."""
        norm_id = self.normalize_id(machine_id)
        proto = "https" if is_ssl else "http"
        entry = {
            "machine_id": machine_id,
            "normalized_id": norm_id,
            "hostname": hostname,
            "os": os_name,
            "ip": ip,
            "port": int(port),
            "ssl": is_ssl,
            "url": f"{proto}://{ip}:{port}",
            "last_seen": time.time(),
            "is_self": norm_id == self.normalize_id(self.machine_id),
        }
        with self._lock:
            self._peers[norm_id] = entry
        return entry

    def get_discovered_peers(self, include_self: bool = True) -> List[Dict[str, Any]]:
        """Return active LAN peers seen within the TTL window."""
        now = time.time()
        # Always ensure self entry is up to date
        local_ips = [ip for ip in get_local_ips() if ip not in ("127.0.0.1", "::1")]
        self_ip = local_ips[0] if local_ips else "127.0.0.1"
        self.register_peer(
            machine_id=self.machine_id,
            ip=self_ip,
            port=self.http_port,
            hostname=self.hostname,
            os_name=self.os_name,
            is_ssl=self.is_ssl,
        )

        active: List[Dict[str, Any]] = []
        with self._lock:
            expired_keys = [
                k for k, v in self._peers.items() if (now - v.get("last_seen", 0)) > self.peer_ttl and not v.get("is_self")
            ]
            for k in expired_keys:
                del self._peers[k]

            for peer in self._peers.values():
                if not include_self and peer.get("is_self"):
                    continue
                active.append(dict(peer))

        active.sort(key=lambda x: (not x.get("is_self", False), x.get("hostname", "")))
        return active

    def resolve_partner_id(self, partner_id_or_host: str, allow_subnet_sweep: bool = True) -> Optional[str]:
        """
        Resolve a 9-digit Partner ID (e.g. '360 523 800') to a full base URL (e.g. 'http://192.168.1.25:8000').
        Returns None if not found in LAN discovery table.
        """
        norm = self.normalize_id(partner_id_or_host)
        if not norm:
            return None

        # Check self first
        if norm == self.normalize_id(self.machine_id):
            proto = "https" if self.is_ssl else "http"
            return f"{proto}://127.0.0.1:{self.http_port}"

        with self._lock:
            peer = self._peers.get(norm)
            if peer:
                return peer.get("url")

        # If not yet in UDP cache and subnet sweep is allowed, perform a fast concurrent /24 HTTP probe
        if allow_subnet_sweep and (time.time() - self._last_http_sweep_ts) > 5.0:
            found_url = self.sweep_subnet_http(target_norm_id=norm)
            if found_url:
                return found_url

        return None

    def _probe_single_http_host(self, ip: str, port: int, timeout: float = 0.28) -> Optional[Dict[str, Any]]:
        """Probe http://ip:port/api/host-info to detect a RemoteDesk Pro host on the LAN."""
        url = f"http://{ip}:{port}/api/host-info"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    machine_id = data.get("machine_id")
                    if machine_id:
                        return self.register_peer(
                            machine_id=str(machine_id),
                            ip=ip,
                            port=int(data.get("port", port)),
                            hostname=str(data.get("hostname", "RemoteDesk-PC")),
                            os_name=str(data.get("os", "Windows")),
                            is_ssl=bool(data.get("ssl", False)),
                        )
        except Exception:
            pass
        return None

    def sweep_subnet_http(self, target_norm_id: Optional[str] = None, timeout: float = 0.28) -> Optional[str]:
        """
        Concurrently scan the local /24 IPv4 subnet for active RemoteDesk Pro hosts over HTTP.
        Works even when Windows Firewall or Wi-Fi routers drop UDP broadcast packets.
        """
        if not self._sweep_lock.acquire(blocking=False):
            return None
        try:
            self._last_http_sweep_ts = time.time()
            local_ips = [ip for ip in get_local_ips() if ip not in ("127.0.0.1", "::1") and "." in ip]
            if not local_ips:
                return None

            # Use the primary LAN IPv4 subnet (prefer 192.168.x.x or 10.x.x.x)
            primary_ip = local_ips[0]
            for ip in local_ips:
                if ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("172."):
                    primary_ip = ip
                    break

            parts = primary_ip.split(".")
            if len(parts) != 4:
                return None
            prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
            candidate_ips = [f"{prefix}{i}" for i in range(1, 255) if f"{prefix}{i}" != primary_ip]

            matched_url: Optional[str] = None
            with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
                future_to_ip = {
                    executor.submit(self._probe_single_http_host, ip, self.http_port, timeout): ip
                    for ip in candidate_ips
                }
                for future in concurrent.futures.as_completed(future_to_ip):
                    entry = future.result()
                    if entry and target_norm_id and entry.get("normalized_id") == target_norm_id:
                        matched_url = entry.get("url")
            return matched_url
        except Exception as e:
            logger.debug(f"HTTP subnet sweep error: {e}")
            return None
        finally:
            self._sweep_lock.release()

    def start(self) -> None:
        """Start background UDP listener and periodic broadcaster threads."""
        if self._running:
            return
        self._running = True
        self._listener_thread = threading.Thread(target=self._udp_listener_loop, daemon=True)
        self._broadcaster_thread = threading.Thread(target=self._udp_broadcast_loop, daemon=True)
        self._listener_thread.start()
        self._broadcaster_thread.start()

    def stop(self) -> None:
        """Stop background discovery threads."""
        self._running = False

    def trigger_broadcast_now(self) -> None:
        """Send an immediate UDP broadcast probe to 255.255.255.255 and directed /24 subnet broadcast addresses."""
        if not self.enabled:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(0.5)
            pkt = self.build_announcement_packet("probe")
            targets = {"255.255.255.255"}
            for ip in get_local_ips():
                parts = ip.split(".")
                if len(parts) == 4 and ip != "127.0.0.1":
                    targets.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
            for target_addr in targets:
                try:
                    sock.sendto(pkt, (target_addr, self.udp_port))
                except Exception:
                    pass
            sock.close()
        except Exception as e:
            logger.debug(f"UDP broadcast probe error: {e}")

    def _udp_listener_loop(self) -> None:
        """Listen for UDP discovery broadcasts on port 21116."""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.udp_port))
            sock.settimeout(1.5)

            while self._running:
                if not self.enabled:
                    time.sleep(1.0)
                    continue
                try:
                    data, addr = sock.recvfrom(4096)
                    sender_ip = addr[0]
                    peer = self.process_incoming_packet(data, sender_ip)
                    # If it was a probe from another peer, reply immediately
                    if peer and not peer.get("is_self"):
                        try:
                            msg = json.loads(data.decode("utf-8"))
                            if msg.get("type") == "probe":
                                reply = self.build_announcement_packet("announce")
                                sock.sendto(reply, (sender_ip, self.udp_port))
                        except Exception:
                            pass
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.debug(f"Discovery recv error: {e}")
                    time.sleep(0.5)
        except Exception as e:
            logger.debug(f"Could not bind UDP discovery port {self.udp_port}: {e}")
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    def _udp_broadcast_loop(self) -> None:
        """Periodically broadcast host presence on LAN."""
        while self._running:
            if self.enabled:
                self.trigger_broadcast_now()
            time.sleep(10.0)
