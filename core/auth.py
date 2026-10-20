"""Authentication, PIN generation, permanent unattended password verification,
active session registry, IP whitelisting, and brute-force protection.
Inspired by RustDesk's dual authentication (Temporary PIN + Permanent Password).
"""

import hashlib
import hmac
import platform
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

from core.config_store import ConfigStore


class AuthManager:
    """Manages temporary PIN + permanent password authentication, rate limiting, and active sessions."""

    def __init__(
        self,
        pin: Optional[str] = None,
        password: Optional[str] = None,
        token_ttl_seconds: int = 86400,
        max_failed_attempts: int = 5,
        lockout_duration_seconds: int = 30,
        config_store: Optional[ConfigStore] = None,
    ):
        self._secret_key = secrets.token_bytes(32)
        self.token_ttl = token_ttl_seconds
        self.max_failed_attempts = max_failed_attempts
        self.lockout_duration = lockout_duration_seconds
        self.config_store = config_store

        # Rate limiting state: ip -> {"failures": int, "lockout_until": float, "last_attempt": float}
        self._rate_limits: Dict[str, Dict[str, float]] = {}

        # Active sessions registry: session_id -> {"id", "token_prefix", "client_ip", "connected_at", "mode", "revoked"}
        self._active_sessions: Dict[str, Dict[str, Any]] = {}
        self._revoked_tokens: set[str] = set()

        # Set custom password or auto-generate 6-digit PIN
        if password:
            self.pin = f"{secrets.randbelow(900000) + 100000:06d}"
            self.password = password
            self.is_custom_password = True
        elif pin:
            self.pin = str(pin).strip()
            self.password = None
            self.is_custom_password = False
        else:
            self.pin = f"{secrets.randbelow(900000) + 100000:06d}"
            self.password = None
            self.is_custom_password = False

    def get_machine_id(self) -> str:
        """Return a formatted 9-digit Partner ID for this host (e.g. '349 443 493')."""
        if self.config_store is not None:
            return self.config_store.get_machine_id()
        if not hasattr(self, "_ephemeral_machine_id"):
            val = secrets.randbelow(900_000_000) + 100_000_000
            s = str(val)
            self._ephemeral_machine_id = f"{s[:3]} {s[3:6]} {s[6:]}"
        return self._ephemeral_machine_id

    def regenerate_pin(self) -> str:
        """Regenerate a new 6-digit session PIN."""
        self.pin = f"{secrets.randbelow(900000) + 100000:06d}"
        return self.pin

    def is_locked_out(self, client_ip: str) -> Tuple[bool, int]:
        """Check if an IP address is currently locked out."""
        now = time.time()
        record = self._rate_limits.get(client_ip)
        if not record:
            return False, 0

        lockout_until = record.get("lockout_until", 0)
        if now < lockout_until:
            remaining = int(lockout_until - now) + 1
            return True, remaining

        # If lockout expired, reset failures
        if lockout_until > 0 and now >= lockout_until:
            record["failures"] = 0
            record["lockout_until"] = 0

        return False, 0

    def record_attempt(self, client_ip: str, success: bool) -> None:
        """Record an authentication attempt for rate limiting with exponential lockout backoff."""
        now = time.time()
        if client_ip not in self._rate_limits:
            self._rate_limits[client_ip] = {
                "failures": 0,
                "lockout_until": 0,
                "lockout_count": 0,
                "last_attempt": now,
            }

        record = self._rate_limits[client_ip]
        record["last_attempt"] = now

        if success:
            record["failures"] = 0
            record["lockout_until"] = 0
            record["lockout_count"] = 0
        else:
            record["failures"] += 1
            if record["failures"] >= self.max_failed_attempts:
                lockout_num = int(record.get("lockout_count", 0))
                multiplier = min(10, 2 ** lockout_num)
                record["lockout_until"] = now + (self.lockout_duration * multiplier)
                record["lockout_count"] = lockout_num + 1

    def verify_credentials(self, provided_code: str, client_ip: str = "127.0.0.1") -> Tuple[bool, Optional[str], str]:
        """
        Verify PIN, CLI password, or persistent Unattended Permanent Password against host credentials.
        Returns: (success: bool, token: Optional[str], message: str)
        """
        # Check IP Whitelist if configured
        if self.config_store is not None:
            net_cfg = self.config_store.get_network_config()
            whitelist = net_cfg.get("ip_whitelist", [])
            if whitelist and client_ip not in ("127.0.0.1", "::1", "testclient") and client_ip not in whitelist:
                self.config_store.add_audit_event("AUTH_BLOCKED_IP", client_ip, "IP not in whitelist")
                return False, None, f"Access denied: IP {client_ip} is not in the host IP whitelist."

        locked, remaining = self.is_locked_out(client_ip)
        if locked:
            return False, None, f"Too many failed attempts. Locked out for {remaining} seconds."

        provided = str(provided_code).strip()
        if not provided:
            return False, None, "Password or PIN is required."

        valid = False
        auth_method = "PIN"

        # 1. Check CLI custom password if set
        if self.is_custom_password and self.password:
            if hmac.compare_digest(provided.encode("utf-8"), self.password.encode("utf-8")):
                valid = True
                auth_method = "Password"

        # 2. Check temporary 6-digit PIN (unless custom CLI password overrides default PIN)
        if not valid and not self.is_custom_password and self.pin:
            if hmac.compare_digest(provided.encode("utf-8"), self.pin.encode("utf-8")):
                valid = True
                auth_method = "One-Time PIN"

        # 3. Check persistent Unattended Access Permanent Password in ConfigStore
        if not valid and self.config_store is not None:
            if self.config_store.verify_permanent_password(provided):
                valid = True
                auth_method = "Unattended Permanent Password"

        self.record_attempt(client_ip, valid)

        if valid:
            token = self.create_session_token(client_ip=client_ip, auth_method=auth_method)
            if self.config_store is not None:
                self.config_store.add_audit_event("LOGIN_SUCCESS", client_ip, f"Authenticated via {auth_method}")
            return True, token, f"Authenticated successfully ({auth_method})."
        else:
            if self.config_store is not None:
                self.config_store.add_audit_event("LOGIN_FAILED", client_ip, "Invalid PIN or password")
            remaining_tries = max(0, self.max_failed_attempts - self._rate_limits[client_ip]["failures"])
            return False, None, f"Invalid credentials. Remaining attempts before cooldown: {remaining_tries}"

    def create_session_token(self, client_ip: str = "127.0.0.1", auth_method: str = "PIN") -> str:
        """Create a cryptographically signed timestamped session token and register the session."""
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        payload = f"{timestamp}:{nonce}".encode("utf-8")
        signature = hmac.new(self._secret_key, payload, hashlib.sha256).hexdigest()
        token = f"{timestamp}:{nonce}:{signature}"

        session_id = nonce[:12]
        self._active_sessions[session_id] = {
            "session_id": session_id,
            "token": token,
            "client_ip": client_ip,
            "auth_method": auth_method,
            "connected_at": time.strftime("%H:%M:%S"),
            "timestamp": int(time.time()),
        }
        return token

    def validate_session_token(self, token: Optional[str]) -> bool:
        """Validate signature, revocation status, and expiration of a session token."""
        if not token or not isinstance(token, str):
            return False

        if token in self._revoked_tokens:
            return False

        parts = token.split(":")
        if len(parts) != 3:
            return False

        timestamp_str, nonce, signature = parts
        try:
            timestamp = int(timestamp_str)
        except ValueError:
            return False

        # Check expiration
        now = time.time()
        if now - timestamp > self.token_ttl or timestamp > now + 60:
            return False

        # Verify HMAC signature
        payload = f"{timestamp_str}:{nonce}".encode("utf-8")
        expected_sig = hmac.new(self._secret_key, payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected_sig)

    def list_active_sessions(self) -> List[Dict[str, Any]]:
        """Return list of currently active authenticated sessions."""
        now = time.time()
        result = []
        for sid, info in list(self._active_sessions.items()):
            if (now - info["timestamp"]) > self.token_ttl or info["token"] in self._revoked_tokens:
                self._active_sessions.pop(sid, None)
                continue
            result.append(
                {
                    "session_id": sid,
                    "client_ip": info["client_ip"],
                    "auth_method": info["auth_method"],
                    "connected_at": info["connected_at"],
                }
            )
        return result

    def revoke_session(self, session_id: str) -> bool:
        """Disconnect / kick an active session by its session_id."""
        info = self._active_sessions.pop(session_id, None)
        if not info:
            return False
        self._revoked_tokens.add(info["token"])
        if self.config_store is not None:
            self.config_store.add_audit_event("SESSION_KICKED", info["client_ip"], f"Revoked session {session_id}")
        return True
