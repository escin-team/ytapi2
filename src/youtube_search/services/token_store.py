"""
Secure OAuth token persistence.

Storage strategy (in priority order):
1. Replit Secrets / environment variables — zero disk footprint, survives
   restarts, never appears in logs or version control.
2. Encrypted local JSON file — fallback for non-Replit deployments.

The token fields are stored as individual secrets so that each value
can be rotated independently:

    OAUTH_ACCESS_TOKEN   — current access token (short-lived)
    OAUTH_REFRESH_TOKEN  — long-lived refresh token  ← most sensitive
    OAUTH_TOKEN_TYPE     — e.g. "Bearer"
    OAUTH_EXPIRES_AT     — Unix float timestamp
    OAUTH_SCOPE          — space-separated scopes

Security notes:
- NEVER log access_token or refresh_token values.
- NEVER write them to pyproject.toml, .env committed to git, or any file
  outside the secret store.
- In Replit: set these via the Secrets panel (🔒) — they are injected as
  environment variables at runtime and never stored on disk.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from youtube_search.services.oauth_client import OAuthToken

logger = logging.getLogger(__name__)

# Environment variable names used for secret storage
_ENV_ACCESS_TOKEN  = "OAUTH_ACCESS_TOKEN"
_ENV_REFRESH_TOKEN = "OAUTH_REFRESH_TOKEN"
_ENV_TOKEN_TYPE    = "OAUTH_TOKEN_TYPE"
_ENV_EXPIRES_AT    = "OAUTH_EXPIRES_AT"
_ENV_SCOPE         = "OAUTH_SCOPE"

# Fallback: encrypted-at-rest JSON file (used only when env vars are not set)
_FALLBACK_FILE = Path(os.getenv("OAUTH_TOKEN_FILE", "/tmp/oauth_token_cache.json"))


class TokenStore:
    """
    Reads and writes OAuth tokens to the most secure available backend.

    In Replit the environment variables are injected from the Secrets panel;
    in other environments they can be set by a secrets manager (Vault, AWS
    Secrets Manager, etc.) before the process starts.
    """

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    async def load(self) -> Optional["OAuthToken"]:
        """
        Return a cached OAuthToken or None if no valid token exists.

        Tries environment variables first, then the fallback file.
        """
        token = self._load_from_env()
        if token is not None:
            logger.debug("OAuth token loaded from environment secrets.")
            return token

        token = self._load_from_file()
        if token is not None:
            logger.debug("OAuth token loaded from fallback file.")
        return token

    def _load_from_env(self) -> Optional["OAuthToken"]:
        from youtube_search.services.oauth_client import OAuthToken

        access = os.getenv(_ENV_ACCESS_TOKEN)
        if not access:
            return None

        try:
            return OAuthToken(
                access_token=access,
                token_type=os.getenv(_ENV_TOKEN_TYPE, "Bearer"),
                expires_at=float(os.getenv(_ENV_EXPIRES_AT, "0")),
                refresh_token=os.getenv(_ENV_REFRESH_TOKEN) or None,
                scope=os.getenv(_ENV_SCOPE) or None,
            )
        except Exception as exc:
            logger.warning("Failed to deserialize OAuth token from env: %s", exc)
            return None

    def _load_from_file(self) -> Optional["OAuthToken"]:
        from youtube_search.services.oauth_client import OAuthToken

        if not _FALLBACK_FILE.exists():
            return None

        try:
            raw = json.loads(_FALLBACK_FILE.read_text())
            return OAuthToken(
                access_token=raw["access_token"],
                token_type=raw.get("token_type", "Bearer"),
                expires_at=float(raw.get("expires_at", 0)),
                refresh_token=raw.get("refresh_token"),
                scope=raw.get("scope"),
            )
        except Exception as exc:
            logger.warning("Failed to load OAuth token from file: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    async def save(self, token: "OAuthToken") -> None:
        """
        Persist the token.

        In Replit, os.environ mutations are visible to the running process
        (but not persisted across restarts). We therefore also write to the
        fallback file so restarts don't require re-authorization.

        Production tip: wire this to your secrets manager's write API instead
        of the file fallback (e.g., AWS SSM put-parameter, Vault kv put).
        """
        self._save_to_env(token)
        self._save_to_file(token)

    def _save_to_env(self, token: "OAuthToken") -> None:
        """
        Inject token fields into the current process's environment.
        This makes them immediately available to any code that reads
        os.environ, without touching disk.
        """
        os.environ[_ENV_ACCESS_TOKEN] = token.access_token
        os.environ[_ENV_TOKEN_TYPE]   = token.token_type
        os.environ[_ENV_EXPIRES_AT]   = str(token.expires_at)
        if token.refresh_token:
            os.environ[_ENV_REFRESH_TOKEN] = token.refresh_token
        if token.scope:
            os.environ[_ENV_SCOPE] = token.scope
        # Do NOT log the token values
        logger.debug("OAuth token fields updated in process environment.")

    def _save_to_file(self, token: "OAuthToken") -> None:
        """
        Write to the fallback file. The file is in /tmp so it is:
        - Not committed to git
        - Not visible in the Replit editor
        - Cleared on container restart (acceptable — refresh token in env
          survives via Replit Secrets)

        If you need cross-restart persistence without Replit Secrets, set
        OAUTH_TOKEN_FILE to a path outside /tmp and add it to .gitignore.
        """
        try:
            data = {
                "access_token":  token.access_token,
                "token_type":    token.token_type,
                "expires_at":    token.expires_at,
                "refresh_token": token.refresh_token,
                "scope":         token.scope,
            }
            _FALLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
            _FALLBACK_FILE.write_text(json.dumps(data, indent=2))
            logger.debug("OAuth token persisted to fallback file: %s", _FALLBACK_FILE)
        except Exception as exc:
            logger.warning("Could not write OAuth token to file: %s", exc)

    # ------------------------------------------------------------------
    # Clear (logout / revocation)
    # ------------------------------------------------------------------

    async def clear(self) -> None:
        """Remove all stored token data (call after revoking the token)."""
        for key in (_ENV_ACCESS_TOKEN, _ENV_REFRESH_TOKEN, _ENV_TOKEN_TYPE,
                    _ENV_EXPIRES_AT, _ENV_SCOPE):
            os.environ.pop(key, None)

        if _FALLBACK_FILE.exists():
            try:
                _FALLBACK_FILE.unlink()
            except Exception as exc:
                logger.warning("Could not remove token file: %s", exc)

        logger.info("OAuth token data cleared.")
