"""
OAuth 2.0 credential configuration.

Inspired by the ytmusicapi/auth credential model:
https://github.com/escin-team/ytmusicapi/blob/b59f59c6faaa75d2b4c0544161ab8fdb54a0c2c3/ytmusicapi/auth

Credentials are loaded exclusively from environment variables / Replit Secrets.
They are NEVER hard-coded here or written to disk.

Required secrets (set via Replit Secrets panel 🔒 or .env file):
    OAUTH_CLIENT_ID       — OAuth 2.0 client ID (use a "TV/Device" application type
                            in Google Cloud Console for Device Code flow)
    OAUTH_CLIENT_SECRET   — OAuth 2.0 client secret

Optional overrides:
    OAUTH_DEVICE_AUTH_URL — Device authorization endpoint
                            (default: Google's)
    OAUTH_TOKEN_URL       — Token endpoint
                            (default: Google's)
    OAUTH_SCOPES          — Space-separated list of scopes
                            (default: YouTube read/write + email)
    OAUTH_JSON_PATH       — Path to oauth.json token file
                            (default: oauth.json in project root)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env if present (development convenience; Replit Secrets take priority)
load_dotenv(override=False)

# ---------------------------------------------------------------------------
# Google OAuth 2.0 Device Authorization endpoint defaults
# ---------------------------------------------------------------------------
_DEFAULT_DEVICE_AUTH_URL = "https://oauth2.googleapis.com/device/code"
_DEFAULT_TOKEN_URL       = "https://oauth2.googleapis.com/token"
_DEFAULT_SCOPES          = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.readonly",
]
_DEFAULT_JSON_PATH = Path(
    os.getenv("OAUTH_JSON_PATH", "oauth.json")
)


@dataclass(frozen=True)
class OAuthCredentials:
    """
    Immutable snapshot of OAuth 2.0 provider credentials.

    Load with :meth:`from_env` — never construct manually with raw secret
    strings in application code.
    """

    client_id: str
    client_secret: str
    device_auth_url: str = _DEFAULT_DEVICE_AUTH_URL
    token_url: str       = _DEFAULT_TOKEN_URL
    scopes: list[str]    = field(default_factory=lambda: list(_DEFAULT_SCOPES))
    token_file: Path     = _DEFAULT_JSON_PATH

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "OAuthCredentials":
        """
        Build credentials from environment variables.

        Raises
        ------
        EnvironmentError
            If OAUTH_CLIENT_ID or OAUTH_CLIENT_SECRET are missing.
        """
        client_id = os.getenv("OAUTH_CLIENT_ID", "").strip()
        client_secret = os.getenv("OAUTH_CLIENT_SECRET", "").strip()

        if not client_id:
            raise EnvironmentError(
                "OAUTH_CLIENT_ID is not set.\n"
                "→ Add it via the Replit Secrets panel (🔒) or your .env file.\n"
                "→ Use a 'TV and Limited Input devices' OAuth client from "
                "https://console.cloud.google.com/apis/credentials"
            )
        if not client_secret:
            raise EnvironmentError(
                "OAUTH_CLIENT_SECRET is not set.\n"
                "→ Add it via the Replit Secrets panel (🔒) or your .env file."
            )

        scopes_raw = os.getenv("OAUTH_SCOPES", "").strip()
        scopes = scopes_raw.split() if scopes_raw else list(_DEFAULT_SCOPES)

        return cls(
            client_id=client_id,
            client_secret=client_secret,
            device_auth_url=os.getenv("OAUTH_DEVICE_AUTH_URL", _DEFAULT_DEVICE_AUTH_URL),
            token_url=os.getenv("OAUTH_TOKEN_URL", _DEFAULT_TOKEN_URL),
            scopes=scopes,
            token_file=Path(os.getenv("OAUTH_JSON_PATH", str(_DEFAULT_JSON_PATH))),
        )

    @property
    def scope_string(self) -> str:
        """Space-separated scope string for OAuth requests."""
        return " ".join(self.scopes)
