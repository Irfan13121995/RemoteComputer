"""Authentication, PIN generation, session token management, and brute-force protection."""

import hashlib
import hmac
import platform
import secrets
import time
from typing import Dict, Optional, Tuple


class AuthManager:
    """Manages PIN/password authentication, rate limiting, and session tokens."""

    def __init__(
        self,
        pin: Optional[str] = None,
        password: Optional[str] = None,
        token_ttl_seconds: int = 86400,
        max_failed_attempts: int = 5,
        lockout_duration_seconds: int = 30,
    ):
        self._secret_key = secrets.token_bytes(32)
        self.token_ttl = token_ttl_seconds
        self.max_failed_attempts = max_failed_attempts
        self.lockout_duration = lockout_duration_seconds

        # Rate limiting state: ip -> {"failures": int, "lockout_until": float, "last_attempt": float}
        self._rate_limits: Dict[str, Dict[str, float]] = {}

        # Set custom password or auto-generate 6-digit PIN
        if password:
            self.pin = None
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
        node_name = platform.node() or "default-remote-host"
        val = int(hashlib.md5(node_name.encode("utf-8")).hexdigest()[:8], 16) % 900000000 + 100000000
        s = str(val)
        return f"{s[:3]} {s[3:6]} {s[6:]}"

    def regenerate_pin(self) -> str:
        """Regenerate a new 6-digit session PIN."""
        if not self.is_custom_password:
            self.pin = f"{secrets.randbelow(900000) + 100000:06d}"
        return self.pin or ""

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
        """Record an authentication attempt for rate limiting."""
        now = time.time()
        if client_ip not in self._rate_limits:
            self._rate_limits[client_ip] = {
                "failures": 0,
                "lockout_until": 0,
                "last_attempt": now,
            }

        record = self._rate_limits[client_ip]
        record["last_attempt"] = now

        if success:
            record["failures"] = 0
            record["lockout_until"] = 0
        else:
            record["failures"] += 1
            if record["failures"] >= self.max_failed_attempts:
                record["lockout_until"] = now + self.lockout_duration

    def verify_credentials(self, provided_code: str, client_ip: str = "127.0.0.1") -> Tuple[bool, Optional[str], str]:
        """
        Verify PIN or password against host credentials.
        Returns: (success: bool, token: Optional[str], message: str)
        """
        locked, remaining = self.is_locked_out(client_ip)
        if locked:
            return False, None, f"Too many failed attempts. Locked out for {remaining} seconds."

        provided = str(provided_code).strip()
        expected = self.password if self.is_custom_password else self.pin

        if not expected:
            return False, None, "Host authentication is not configured."

        # Constant-time comparison
        valid = hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
        self.record_attempt(client_ip, valid)

        if valid:
            token = self.create_session_token()
            return True, token, "Authenticated successfully."
        else:
            remaining_tries = max(0, self.max_failed_attempts - self._rate_limits[client_ip]["failures"])
            return False, None, f"Invalid credentials. Remaining attempts before cooldown: {remaining_tries}"

    def create_session_token(self) -> str:
        """Create a cryptographically signed timestamped session token."""
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        payload = f"{timestamp}:{nonce}".encode("utf-8")
        signature = hmac.new(self._secret_key, payload, hashlib.sha256).hexdigest()
        return f"{timestamp}:{nonce}:{signature}"

    def validate_session_token(self, token: Optional[str]) -> bool:
        """Validate signature and expiration of a session token."""
        if not token or not isinstance(token, str):
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
