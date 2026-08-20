"""Unit tests for AuthManager (PINs, passwords, session tokens, and brute-force protection)."""

import time
import pytest
from core.auth import AuthManager


def test_default_pin_generation():
    """Verify randomly generated PIN is a 6-digit numeric string."""
    auth = AuthManager()
    assert auth.pin is not None
    assert len(auth.pin) == 6
    assert auth.pin.isdigit()
    assert not auth.is_custom_password


def test_custom_pin():
    """Verify custom PIN setting."""
    auth = AuthManager(pin="123456")
    assert auth.pin == "123456"
    assert not auth.is_custom_password


def test_custom_password():
    """Verify custom password setting."""
    auth = AuthManager(password="SuperSecretPassword!99")
    assert auth.password == "SuperSecretPassword!99"
    assert auth.is_custom_password


def test_verify_credentials_success():
    """Verify successful login returns valid token."""
    auth = AuthManager(pin="654321")
    success, token, msg = auth.verify_credentials("654321")
    assert success is True
    assert token is not None
    assert auth.validate_session_token(token) is True


def test_verify_credentials_failure():
    """Verify invalid PIN fails."""
    auth = AuthManager(pin="654321")
    success, token, msg = auth.verify_credentials("000000")
    assert success is False
    assert token is None
    assert "Invalid credentials" in msg


def test_session_token_validation_and_expiration():
    """Verify token signature tampering and expiration detection."""
    auth = AuthManager(token_ttl_seconds=1)
    token = auth.create_session_token()
    assert auth.validate_session_token(token) is True

    # Tampered token
    parts = token.split(":")
    tampered = f"{parts[0]}:{parts[1]}:invalid_signature_hex"
    assert auth.validate_session_token(tampered) is False

    # Expired token
    time.sleep(1.5)
    assert auth.validate_session_token(token) is False


def test_rate_limiting_lockout():
    """Verify brute-force lockout after max failed attempts."""
    auth = AuthManager(pin="999999", max_failed_attempts=3, lockout_duration_seconds=2)
    client_ip = "192.168.1.100"

    # 1st fail
    s, t, _ = auth.verify_credentials("111111", client_ip=client_ip)
    assert s is False
    # 2nd fail
    s, t, _ = auth.verify_credentials("222222", client_ip=client_ip)
    assert s is False
    # 3rd fail -> triggers lockout
    s, t, _ = auth.verify_credentials("333333", client_ip=client_ip)
    assert s is False

    # 4th attempt should be locked out
    locked, remaining = auth.is_locked_out(client_ip)
    assert locked is True
    assert remaining > 0

    s, t, msg = auth.verify_credentials("999999", client_ip=client_ip)
    assert s is False
    assert "Locked out" in msg
