"""
Async-compatible OAuth token provider.

Acts as a thin, framework-agnostic bridge between the sync
``OAuthSession`` (requests-based) and async services that use httpx or
yt-dlp.  Callers receive a plain Bearer token string — they never have to
touch the token lifecycle themselves.

Design
------
- **Async-safe**: the blocking I/O and any network refresh call are
  executed in a thread pool via ``asyncio.to_thread()``, so the event
  loop is never stalled.
- **Graceful degradation**: returns ``None`` when OAuth is not configured
  (no ``OAUTH_CLIENT_ID`` / ``OAUTH_CLIENT_SECRET``) or ``oauth.json``
  does not exist — callers fall back to cookies / unauthenticated mode.
- **Single lock**: a module-level threading.Lock prevents concurrent
  refreshes when multiple threads call the sync path simultaneously (e.g.
  from yt-dlp's thread pool).
- **Fail-open**: any unexpected error during load/refresh is logged and
  returns the existing (possibly stale) token rather than crashing the
  caller.

Usage
-----
In async services (scraper, playlist)::

    from youtube_search.auth.token_provider import get_valid_access_token

    token = await get_valid_access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

In sync code (yt-dlp options builder)::

    from youtube_search.auth.token_provider import get_valid_access_token_sync

    token = get_valid_access_token_sync()
    if token:
        ydl_opts["http_headers"]["Authorization"] = f"Bearer {token}"
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level lock prevents concurrent refreshes in threaded sync callers.
_refresh_lock = threading.Lock()

# Default token file path — overridable via OAUTH_JSON_PATH env var.
_TOKEN_PATH = Path(os.getenv("OAUTH_JSON_PATH", "oauth.json"))


def _credentials_configured() -> bool:
    """Return True only when both required env vars are set and non-empty."""
    return bool(
        os.getenv("OAUTH_CLIENT_ID", "").strip()
        and os.getenv("OAUTH_CLIENT_SECRET", "").strip()
    )


def get_valid_access_token_sync() -> Optional[str]:
    """
    Synchronous version — safe to call from blocking contexts (e.g. yt-dlp
    option builders, Celery tasks, script entrypoints).

    Returns
    -------
    str | None
        A valid Bearer access token, or ``None`` when OAuth is not
        configured or ``oauth.json`` is absent.

    Thread-safety
    -------------
    Protected by a module-level lock so only one thread performs a network
    refresh at a time; others wait and reuse the result.
    """
    if not _credentials_configured():
        logger.debug("OAuth credentials not configured — skipping.")
        return None

    token_path = _TOKEN_PATH

    # ── Load ─────────────────────────────────────────────────────────────
    try:
        from youtube_search.auth.token import OAuthToken
        token = OAuthToken.load(token_path)
    except FileNotFoundError:
        logger.debug("oauth.json not found — Device Code setup not run yet.")
        return None
    except Exception as exc:
        logger.warning("Failed to load oauth.json: %s", exc)
        return None

    # ── Proactive refresh if expired ──────────────────────────────────────
    if token.is_expired:
        with _refresh_lock:
            # Double-checked: another thread may have refreshed already.
            try:
                token = OAuthToken.load(token_path)
            except Exception:
                pass

            if token.is_expired:
                logger.info(
                    "OAuth token expired (%.0f s overdue) — refreshing.",
                    -token.seconds_until_expiry,
                )
                try:
                    from youtube_search.auth.credentials import OAuthCredentials
                    from youtube_search.auth.auth_provider import AuthProvider

                    creds = OAuthCredentials.from_env()
                    provider = AuthProvider(creds)
                    token = provider.refresh_access_token(token)
                    token.save(token_path)
                    logger.info("OAuth token refreshed and saved.")
                except Exception as exc:
                    logger.warning(
                        "Token refresh failed: %s — using stale token (may 401).", exc
                    )

    return token.access_token


async def get_valid_access_token() -> Optional[str]:
    """
    Async wrapper — safe to call from coroutines (httpx scrapers, FastAPI
    route handlers, etc.).

    Delegates the blocking load + optional network refresh to a thread pool
    so the event loop is never stalled.

    Returns
    -------
    str | None
        A valid Bearer access token, or ``None`` when OAuth is not set up.

    Example
    -------
    ::

        token = await get_valid_access_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # else: fall back to cookie / unauthenticated path
    """
    return await asyncio.to_thread(get_valid_access_token_sync)


def build_auth_header(token: str) -> dict[str, str]:
    """Return ``{"Authorization": "Bearer <token>"}`` from a raw token string."""
    return {"Authorization": f"Bearer {token}"}
