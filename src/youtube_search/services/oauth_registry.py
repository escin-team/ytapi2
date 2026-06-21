"""
OAuth client registry — one singleton per named provider.

Usage::

    from youtube_search.services.oauth_registry import get_oauth_client

    client = get_oauth_client("google")
    response = await client.request("GET", "https://www.googleapis.com/...")

Credentials are read exclusively from environment variables / Replit Secrets.
Never pass raw credential strings into application code.

Required secrets per provider (set via Replit Secrets panel 🔒):
    OAUTH_CLIENT_ID       — OAuth 2.0 Client ID
    OAUTH_CLIENT_SECRET   — OAuth 2.0 Client Secret
    OAUTH_TOKEN_URL       — Token endpoint  (e.g. https://oauth2.googleapis.com/token)
    OAUTH_AUTH_URL        — Authorization endpoint
    OAUTH_REDIRECT_URI    — Must match what is registered in your OAuth app
    OAUTH_SCOPES          — Space-separated list of scopes
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Optional

from youtube_search.services.oauth_client import OAuthClient, OAuthConfig
from youtube_search.services.token_store import TokenStore

logger = logging.getLogger(__name__)

# provider_name → OAuthClient singleton
_registry: Dict[str, OAuthClient] = {}


def build_config(prefix: str = "OAUTH") -> OAuthConfig:
    """
    Build an OAuthConfig from environment variables.

    All variable names are prefixed (default "OAUTH") so multiple
    providers can coexist:

        OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET / OAUTH_TOKEN_URL / …
        SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET / SPOTIFY_TOKEN_URL / …

    Raises
    ------
    RuntimeError
        If any required secret is missing from the environment.
    """
    def _require(key: str) -> str:
        val = os.getenv(key)
        if not val:
            raise RuntimeError(
                f"Missing required secret: {key}. "
                f"Add it via the Replit Secrets panel (🔒) or set the "
                f"environment variable before starting the server."
            )
        return val

    scopes_raw = os.getenv(f"{prefix}_SCOPES", "")
    scopes = [s.strip() for s in scopes_raw.split() if s.strip()]

    return OAuthConfig(
        client_id=_require(f"{prefix}_CLIENT_ID"),
        client_secret=_require(f"{prefix}_CLIENT_SECRET"),
        token_url=_require(f"{prefix}_TOKEN_URL"),
        auth_url=_require(f"{prefix}_AUTH_URL"),
        redirect_uri=_require(f"{prefix}_REDIRECT_URI"),
        scopes=scopes,
    )


def get_oauth_client(provider: str = "default") -> OAuthClient:
    """
    Return (or lazily create) the singleton OAuthClient for this provider.

    Parameters
    ----------
    provider : str
        Logical name used as the registry key. The env-var prefix is derived
        as provider.upper() (e.g. "google" → "GOOGLE_CLIENT_ID").
        Use "default" to read from plain OAUTH_* vars.
    """
    if provider not in _registry:
        prefix = "OAUTH" if provider == "default" else provider.upper()
        config = build_config(prefix)
        _registry[provider] = OAuthClient(config, store=TokenStore())
        logger.info("OAuth client for provider '%s' initialized.", provider)
    return _registry[provider]


def is_configured(provider: str = "default") -> bool:
    """Return True if all required secrets for this provider exist."""
    prefix = "OAUTH" if provider == "default" else provider.upper()
    required = [
        f"{prefix}_CLIENT_ID",
        f"{prefix}_CLIENT_SECRET",
        f"{prefix}_TOKEN_URL",
        f"{prefix}_AUTH_URL",
        f"{prefix}_REDIRECT_URI",
    ]
    return all(os.getenv(k) for k in required)


async def close_all() -> None:
    """Gracefully close all registered OAuth HTTP clients."""
    for client in _registry.values():
        await client.aclose()
    _registry.clear()
