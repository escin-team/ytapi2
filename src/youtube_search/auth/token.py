"""
OAuth 2.0 token model and oauth.json persistence.

Storage layout (oauth.json):
{
    "access_token":  "...",
    "refresh_token": "...",
    "token_type":    "Bearer",
    "expires_at":    1719999999.0,   ← Unix float timestamp
    "scope":         "https://..."
}

Security notes:
- oauth.json contains long-lived credentials. Add it to .gitignore immediately.
- In production: replace the file backend with Replit Secrets or a secrets
  manager (Vault, AWS SSM) — the save/load interface stays identical.
- Token values are NEVER written to log lines; only metadata (expiry, scope).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# How many seconds before actual expiry we consider the token "expired".
# Refreshing 60 s early prevents clock-skew race conditions.
_EXPIRY_MARGIN_SECONDS = 60


@dataclass
class OAuthToken:
    """
    In-memory representation of a live OAuth 2.0 token pair.

    Attributes
    ----------
    access_token  : Short-lived credential sent in Authorization header.
    refresh_token : Long-lived credential used to renew access_token silently.
    token_type    : Always "Bearer" for standard OAuth 2.0 providers.
    expires_at    : Absolute Unix timestamp when access_token expires.
    scope         : Space-separated list of granted scopes (may be None).
    """

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_at: float = 0.0
    scope: Optional[str] = None

    # ------------------------------------------------------------------
    # Expiry helpers
    # ------------------------------------------------------------------

    @property
    def is_expired(self) -> bool:
        """True when the access token needs refreshing (60 s safety margin)."""
        if self.expires_at == 0:
            return True
        return time.time() >= (self.expires_at - _EXPIRY_MARGIN_SECONDS)

    @property
    def seconds_until_expiry(self) -> float:
        """Remaining lifetime in seconds (may be negative if already expired)."""
        return self.expires_at - time.time()

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_response(
        cls,
        data: Dict[str, Any],
        old_refresh: Optional[str] = None,
    ) -> "OAuthToken":
        """
        Build a token from a raw token-endpoint JSON response.

        The ``expires_in`` field (seconds from now) is converted to an
        absolute ``expires_at`` timestamp so the value remains correct
        after a process restart.

        RFC 6749 §6 allows the server to omit ``refresh_token`` on refresh
        responses; in that case the previous refresh token is reused.
        """
        expires_in: int = int(data.get("expires_in", 3600))
        refresh = data.get("refresh_token") or old_refresh
        if not refresh:
            raise ValueError(
                "Token response contains no refresh_token and no previous "
                "refresh token was provided."
            )
        return cls(
            access_token=data["access_token"],
            refresh_token=refresh,
            token_type=data.get("token_type", "Bearer"),
            expires_at=time.time() + expires_in,
            scope=data.get("scope"),
        )

    @classmethod
    def load(cls, path: Path) -> "OAuthToken":
        """
        Load a token from an oauth.json file.

        Raises
        ------
        FileNotFoundError   If the file does not exist.
        ValueError          If required fields are missing or malformed.
        """
        if not path.exists():
            raise FileNotFoundError(
                f"oauth.json not found at '{path}'.\n"
                f"Run  python scripts/oauth_setup.py  to authorize first."
            )
        try:
            raw: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"oauth.json is not valid JSON: {exc}") from exc

        for required in ("access_token", "refresh_token"):
            if not raw.get(required):
                raise ValueError(f"oauth.json is missing required field: {required}")

        token = cls(
            access_token=raw["access_token"],
            refresh_token=raw["refresh_token"],
            token_type=raw.get("token_type", "Bearer"),
            expires_at=float(raw.get("expires_at", 0)),
            scope=raw.get("scope"),
        )
        logger.debug(
            "Token loaded from %s — expires in %.0f s, scope=%s",
            path, token.seconds_until_expiry, token.scope,
        )
        return token

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        """
        Write the token to oauth.json.

        The file is written atomically (write to tmp, then rename) so a
        crash mid-write cannot corrupt an existing valid token file.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        data = {
            "access_token":  self.access_token,
            "refresh_token": self.refresh_token,
            "token_type":    self.token_type,
            "expires_at":    self.expires_at,
            "scope":         self.scope,
        }
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
        logger.debug(
            "Token saved to %s — expires in %.0f s",
            path, self.seconds_until_expiry,
        )

    # ------------------------------------------------------------------
    # Display helper (safe — no secrets in repr)
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"OAuthToken(type={self.token_type!r}, "
            f"expires_in={self.seconds_until_expiry:.0f}s, "
            f"has_refresh={bool(self.refresh_token)}, "
            f"scope={self.scope!r})"
        )
