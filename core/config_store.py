"""Persistent configuration, granular permissions, permanent password, and audit log store.
Inspired by RustDesk's hbb_common::config::Config & LocalConfig stored in %APPDATA%.
"""

import hashlib
import hmac
import json
import logging
import os
import platform
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def get_default_config_dir() -> Path:
    """Resolve OS-appropriate persistent application data directory."""
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "RemoteDeskPro"
        return Path.home() / "AppData" / "Roaming" / "RemoteDeskPro"
    elif platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "RemoteDeskPro"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            return Path(xdg) / "remotedesk"
        return Path.home() / ".config" / "remotedesk"


class ConfigStore:
    """Thread-safe persistent configuration manager for RemoteDesk Pro."""

    DEFAULT_PERMISSIONS = {
        "allow_keyboard_mouse": True,
        "allow_clipboard": True,
        "allow_file_transfer": True,
        "allow_audio": True,
        "allow_tcp_tunnel": True,
        "allow_power_control": True,
        "allow_terminal": True,
        "privacy_mode_allowed": True,
    }

    DEFAULT_NETWORK = {
        "rendezvous_server": "",
        "relay_server": "",
        "relay_key": "",
        "lan_discovery": True,
        "direct_ip_access": True,
        "ip_whitelist": [],
    }

    def __init__(self, config_path: Optional[Path] = None):
        self._lock = threading.RLock()
        if config_path is not None:
            self.config_path = Path(config_path)
            self.config_dir = self.config_path.parent
        else:
            self.config_dir = get_default_config_dir()
            self.config_path = self.config_dir / "config.json"

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Any] = {}
        self.load()

    def _generate_default_machine_id(self) -> str:
        """Generate a deterministic 9-digit Partner ID (e.g., '360 523 800')."""
        node_name = platform.node() or "default-remote-host"
        val = int(hashlib.md5(node_name.encode("utf-8")).hexdigest()[:8], 16) % 900000000 + 100000000
        s = str(val)
        return f"{s[:3]} {s[3:6]} {s[6:]}"

    def load(self) -> Dict[str, Any]:
        """Load configuration from disk or initialize defaults."""
        with self._lock:
            if self.config_path.exists():
                try:
                    raw = self.config_path.read_text(encoding="utf-8")
                    self._data = json.loads(raw)
                except Exception as e:
                    logger.warning(f"Failed to read config {self.config_path}: {e}. Initializing defaults.")
                    self._data = {}
            else:
                self._data = {}

            changed = False
            if "machine_id" not in self._data or not self._data["machine_id"]:
                self._data["machine_id"] = self._generate_default_machine_id()
                changed = True

            if "unattended_enabled" not in self._data:
                self._data["unattended_enabled"] = True
                changed = True

            if "permanent_password_hash" not in self._data:
                self._data["permanent_password_hash"] = ""
                self._data["permanent_password_salt"] = ""
                changed = True

            if "permissions" not in self._data or not isinstance(self._data["permissions"], dict):
                self._data["permissions"] = dict(self.DEFAULT_PERMISSIONS)
                changed = True
            else:
                for k, v in self.DEFAULT_PERMISSIONS.items():
                    if k not in self._data["permissions"]:
                        self._data["permissions"][k] = v
                        changed = True

            if "network" not in self._data or not isinstance(self._data["network"], dict):
                self._data["network"] = dict(self.DEFAULT_NETWORK)
                changed = True
            else:
                for k, v in self.DEFAULT_NETWORK.items():
                    if k not in self._data["network"]:
                        self._data["network"][k] = v
                        changed = True

            if "address_book" not in self._data or not isinstance(self._data["address_book"], list):
                self._data["address_book"] = []
                changed = True

            if "audit_log" not in self._data or not isinstance(self._data["audit_log"], list):
                self._data["audit_log"] = []
                changed = True

            if changed or not self.config_path.exists():
                self.save()

            return self._data

    def save(self) -> None:
        """Persist configuration atomically to disk."""
        with self._lock:
            try:
                self.config_dir.mkdir(parents=True, exist_ok=True)
                tmp_path = self.config_path.with_suffix(".tmp")
                tmp_path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
                tmp_path.replace(self.config_path)
            except Exception as e:
                logger.error(f"Failed to save config to {self.config_path}: {e}")

    # ── Machine ID ────────────────────────────────────────────────────────────
    def get_machine_id(self) -> str:
        with self._lock:
            return self._data.get("machine_id", self._generate_default_machine_id())

    def set_machine_id(self, new_id: str) -> str:
        clean = "".join(ch for ch in new_id if ch.isdigit())
        if len(clean) == 9:
            formatted = f"{clean[:3]} {clean[3:6]} {clean[6:]}"
        else:
            formatted = new_id.strip()
        with self._lock:
            self._data["machine_id"] = formatted
            self.save()
            return formatted

    # ── Permanent Unattended Password ─────────────────────────────────────────
    def has_permanent_password(self) -> bool:
        with self._lock:
            return bool(self._data.get("permanent_password_hash"))

    def set_permanent_password(self, password: Optional[str]) -> bool:
        """Set or clear the permanent unattended access password."""
        with self._lock:
            if not password or not password.strip():
                self._data["permanent_password_hash"] = ""
                self._data["permanent_password_salt"] = ""
                self.save()
                return False

            salt = secrets.token_hex(16)
            dk = hashlib.pbkdf2_hmac(
                "sha256",
                password.strip().encode("utf-8"),
                salt.encode("utf-8"),
                100_000,
            ).hex()
            self._data["permanent_password_salt"] = salt
            self._data["permanent_password_hash"] = dk
            self.save()
            return True

    def verify_permanent_password(self, candidate: str) -> bool:
        """Verify a candidate password against the stored permanent password hash."""
        with self._lock:
            if not self._data.get("unattended_enabled", True):
                return False
            stored_hash = self._data.get("permanent_password_hash", "")
            salt = self._data.get("permanent_password_salt", "")
            if not stored_hash or not salt or not candidate:
                return False

            candidate_hash = hashlib.pbkdf2_hmac(
                "sha256",
                candidate.strip().encode("utf-8"),
                salt.encode("utf-8"),
                100_000,
            ).hex()
            return hmac.compare_digest(stored_hash, candidate_hash)

    # ── Granular Session Permissions ──────────────────────────────────────────
    def get_permissions(self) -> Dict[str, bool]:
        with self._lock:
            return dict(self._data.get("permissions", self.DEFAULT_PERMISSIONS))

    def update_permissions(self, updates: Dict[str, Any]) -> Dict[str, bool]:
        with self._lock:
            perms = self._data.setdefault("permissions", dict(self.DEFAULT_PERMISSIONS))
            for k, v in updates.items():
                if k in self.DEFAULT_PERMISSIONS:
                    perms[k] = bool(v)
            if "unattended_enabled" in updates:
                self._data["unattended_enabled"] = bool(updates["unattended_enabled"])
            self.save()
            return dict(perms)

    # ── Network & Rendezvous Settings ─────────────────────────────────────────
    def get_network_config(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data.get("network", self.DEFAULT_NETWORK))

    def update_network_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            net = self._data.setdefault("network", dict(self.DEFAULT_NETWORK))
            for k in ("rendezvous_server", "relay_server", "relay_key"):
                if k in updates and updates[k] is not None:
                    net[k] = str(updates[k]).strip()
            for k in ("lan_discovery", "direct_ip_access"):
                if k in updates:
                    net[k] = bool(updates[k])
            if "ip_whitelist" in updates and isinstance(updates["ip_whitelist"], list):
                net["ip_whitelist"] = [str(ip).strip() for ip in updates["ip_whitelist"] if str(ip).strip()]
            self.save()
            return dict(net)

    # ── Address Book ──────────────────────────────────────────────────────────
    def get_address_book(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._data.get("address_book", []))

    def save_address_book(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        with self._lock:
            self._data["address_book"] = entries
            self.save()
            return list(entries)

    # ── Audit Log ─────────────────────────────────────────────────────────────
    def add_audit_event(self, event_type: str, client_ip: str = "127.0.0.1", details: str = "") -> Dict[str, Any]:
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "epoch": int(time.time()),
            "event": event_type,
            "client_ip": client_ip,
            "details": details,
        }
        with self._lock:
            log = self._data.setdefault("audit_log", [])
            log.insert(0, entry)
            self._data["audit_log"] = log[:100]
            self.save()
        return entry

    def get_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._data.get("audit_log", [])[:limit])
