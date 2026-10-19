"""TCP Tunneling / Port Forwarding Manager.
Inspired by RustDesk's src/port_forward.rs.
Allows creating local-to-remote TCP port forwards with live connection & traffic metrics.
"""

import asyncio
import logging
import socket
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TcpTunnelInstance:
    """Represents an active TCP port forwarding listener."""

    def __init__(
        self,
        tunnel_id: str,
        name: str,
        listen_host: str,
        listen_port: int,
        target_host: str,
        target_port: int,
    ):
        self.tunnel_id = tunnel_id
        self.name = name or f"Tunnel :{listen_port} -> {target_host}:{target_port}"
        self.listen_host = listen_host
        self.listen_port = int(listen_port)
        self.target_host = target_host
        self.target_port = int(target_port)

        self.created_at = time.strftime("%H:%M:%S")
        self.active_connections = 0
        self.total_connections = 0
        self.bytes_forwarded = 0
        self.running = False

        self._server_sock: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Bind listening socket and spawn accept thread."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.listen_host, self.listen_port))
        # Update port if 0 (ephemeral port) was requested
        self.listen_port = sock.getsockname()[1]
        sock.listen(32)
        sock.settimeout(1.0)

        self._server_sock = sock
        self.running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def stop(self) -> None:
        """Stop listening and close server socket."""
        self.running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None

    def _accept_loop(self) -> None:
        while self.running and self._server_sock:
            try:
                client_sock, _ = self._server_sock.accept()
                self.total_connections += 1
                self.active_connections += 1
                t = threading.Thread(target=self._handle_client, args=(client_sock,), daemon=True)
                t.start()
            except socket.timeout:
                continue
            except Exception:
                break

    def _handle_client(self, client_sock: socket.socket) -> None:
        target_sock = None
        try:
            target_sock = socket.create_connection((self.target_host, self.target_port), timeout=5.0)
            t1 = threading.Thread(target=self._pipe, args=(client_sock, target_sock), daemon=True)
            t2 = threading.Thread(target=self._pipe, args=(target_sock, client_sock), daemon=True)
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        except Exception as e:
            logger.debug(f"TCP tunnel connection error ({self.target_host}:{self.target_port}): {e}")
        finally:
            self.active_connections = max(0, self.active_connections - 1)
            for s in (client_sock, target_sock):
                if s:
                    try:
                        s.close()
                    except Exception:
                        pass

    def _pipe(self, src: socket.socket, dst: socket.socket) -> None:
        try:
            while self.running:
                data = src.recv(16384)
                if not data:
                    break
                dst.sendall(data)
                self.bytes_forwarded += len(data)
        except Exception:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except Exception:
                pass

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.tunnel_id,
            "name": self.name,
            "listen_host": self.listen_host,
            "listen_port": self.listen_port,
            "target_host": self.target_host,
            "target_port": self.target_port,
            "active_connections": self.active_connections,
            "total_connections": self.total_connections,
            "bytes_forwarded": self.bytes_forwarded,
            "running": self.running,
            "created_at": self.created_at,
        }


class TcpTunnelManager:
    """Manages all active TCP port-forwarding tunnels."""

    def __init__(self):
        self._lock = threading.RLock()
        self._tunnels: Dict[str, TcpTunnelInstance] = {}

    def create_tunnel(
        self,
        listen_port: int,
        target_host: str = "127.0.0.1",
        target_port: int = 80,
        listen_host: str = "127.0.0.1",
        name: str = "",
    ) -> Dict[str, Any]:
        """Create and start a new TCP port forwarding tunnel."""
        tunnel_id = uuid.uuid4().hex[:8]
        instance = TcpTunnelInstance(
            tunnel_id=tunnel_id,
            name=name,
            listen_host=listen_host,
            listen_port=int(listen_port),
            target_host=target_host.strip() or "127.0.0.1",
            target_port=int(target_port),
        )
        instance.start()
        with self._lock:
            self._tunnels[tunnel_id] = instance
        return instance.to_dict()

    def stop_tunnel(self, tunnel_id: str) -> bool:
        """Stop and remove a TCP tunnel by ID."""
        with self._lock:
            inst = self._tunnels.pop(tunnel_id, None)
        if not inst:
            return False
        inst.stop()
        return True

    def list_tunnels(self) -> List[Dict[str, Any]]:
        """Return all active TCP tunnels."""
        with self._lock:
            return [t.to_dict() for t in self._tunnels.values()]

    def stop_all(self) -> None:
        with self._lock:
            tunnels = list(self._tunnels.values())
            self._tunnels.clear()
        for t in tunnels:
            t.stop()
