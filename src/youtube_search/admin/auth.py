"""Session management — HMAC-signed cookies, no external deps."""

import hashlib
import hmac
import json
import os
import secrets
import time

_SECRET = os.getenv("SESSION_SECRET", secrets.token_hex(32))
_MAX_AGE = 8 * 3600  # 8 hours
_COOKIE_NAME = "admin_session"


def _sign(payload: str) -> str:
    sig = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify(token: str) -> str | None:
    """Return payload string if valid, else None."""
    try:
        payload, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(sig, expected):
        return None
    return payload


def create_session_cookie() -> str:
    """Create a signed session token valid for _MAX_AGE seconds."""
    payload = json.dumps({"t": int(time.time()), "id": secrets.token_hex(8)},
                         separators=(",", ":"))
    import base64
    b64 = base64.urlsafe_b64encode(payload.encode()).decode()
    return _sign(b64)


def validate_session_cookie(token: str | None) -> bool:
    """Return True if the session token is present, genuine, and not expired."""
    if not token:
        return False
    payload_b64 = _verify(token)
    if payload_b64 is None:
        return False
    try:
        import base64
        data = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        return (int(time.time()) - data["t"]) < _MAX_AGE
    except Exception:
        return False


COOKIE_NAME = _COOKIE_NAME
