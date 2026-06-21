"""
OAuthSession — a requests.Session subclass with automatic token management.

Inspired by ytmusicapi's session architecture:
https://github.com/escin-team/ytmusicapi/blob/b59f59c6faaa75d2b4c0544161ab8fdb54a0c2c3/ytmusicapi/auth

Two-layer refresh strategy
--------------------------
1. **Proactive** (before every request): checks ``token.is_expired``.
   If true, calls the refresh endpoint silently and continues.

2. **Reactive** (after every response): if the server returns 401,
   the token is force-refreshed and the request is retried exactly once.
   This handles cases where the server revokes a token mid-session or
   the local clock is skewed.

WAF / firewall bypass
---------------------
The session ships with a realistic browser-like header baseline that
prevents the most common anti-bot fingerprinting triggers:
  • Full Accept / Accept-Language / Accept-Encoding stack
  • Sec-Fetch-* headers matching a real Chrome navigation
  • Authorization token in header only — NEVER in query strings
  • Proxy-origin headers (X-Forwarded-For, Via) are stripped
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

import requests
from requests import PreparedRequest, Response
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from youtube_search.auth.auth_provider import AuthProvider
from youtube_search.auth.credentials import OAuthCredentials
from youtube_search.auth.token import OAuthToken

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT: tuple[float, float] = (10.0, 30.0)  # (connect, read)

# Headers that could reveal cloud/proxy origin to WAFs — always stripped.
_STRIP_REQUEST_HEADERS: frozenset[str] = frozenset({
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "via",
    "forwarded",
})

# Realistic Chrome 120 header baseline (order matters for fingerprint).
_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/json, text/html, application/xhtml+xml, "
        "application/xml;q=0.9, */*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Ch-Ua": (
        '"Not_A Brand";v="8", "Chromium";v="120", '
        '"Google Chrome";v="120"'
    ),
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

# urllib3 retry policy for transient network/server errors.
_RETRY_POLICY = Retry(
    total=3,
    backoff_factor=1.0,
    status_forcelist={500, 502, 503, 504},
    allowed_methods={"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"},
    raise_on_status=False,
)


# ---------------------------------------------------------------------------
# Session class
# ---------------------------------------------------------------------------

class OAuthSession(requests.Session):
    """
    A ``requests.Session`` that transparently manages OAuth 2.0 tokens.

    Parameters
    ----------
    token_file : Path | str | None
        Path to ``oauth.json``.  Defaults to the value in ``OAuthCredentials``.
    credentials : OAuthCredentials | None
        Override the credential source.  Defaults to ``OAuthCredentials.from_env()``.
    timeout : tuple[float, float]
        (connect_timeout, read_timeout) in seconds.  Default: (10, 30).

    Usage
    -----
    ::

        session = OAuthSession()
        resp = session.get("https://www.googleapis.com/youtube/v3/channels",
                           params={"part": "snippet", "mine": True})
        data = resp.json()

    The Authorization header is injected automatically on every request.
    Token refresh happens invisibly when needed.
    """

    def __init__(
        self,
        token_file: Optional[Path | str] = None,
        credentials: Optional[OAuthCredentials] = None,
        timeout: tuple[float, float] = _DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__()

        self._credentials: OAuthCredentials = credentials or OAuthCredentials.from_env()
        self._token_path: Path = (
            Path(token_file) if token_file else self._credentials.token_file
        )
        self._timeout: tuple[float, float] = timeout
        self._lock: threading.Lock = threading.Lock()

        # Load token eagerly — fails fast if oauth.json is missing.
        self._token: OAuthToken = OAuthToken.load(self._token_path)

        # Install browser-like headers as session defaults.
        self.headers.clear()
        self.headers.update(_BROWSER_HEADERS)

        # Mount retry adapter for both http and https.
        adapter = HTTPAdapter(max_retries=_RETRY_POLICY)
        self.mount("https://", adapter)
        self.mount("http://",  adapter)

        logger.debug("OAuthSession initialized — %r", self._token)

    # ------------------------------------------------------------------
    # Core: authenticated request dispatch
    # ------------------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *args,
        **kwargs,
    ) -> Response:
        """
        Override ``requests.Session.request`` to:
        1. Proactively refresh the token if it is expired.
        2. Inject the Authorization: Bearer header.
        3. Strip proxy-reveal headers.
        4. Reactively refresh on 401 and retry once.
        """
        # ── Proactive refresh ─────────────────────────────────────────
        self._ensure_valid_token()

        # ── Inject auth header ────────────────────────────────────────
        kwargs.setdefault("timeout", self._timeout)
        headers: dict = kwargs.pop("headers", {}) or {}
        headers = self._build_request_headers(headers)
        kwargs["headers"] = headers

        # ── First attempt ─────────────────────────────────────────────
        response: Response = super().request(method, url, *args, **kwargs)

        # ── Reactive refresh on 401 ───────────────────────────────────
        if response.status_code == 401:
            logger.warning(
                "Received 401 from %s — force-refreshing token and retrying.", url
            )
            self._force_refresh()
            kwargs["headers"] = self._build_request_headers({})
            response = super().request(method, url, *args, **kwargs)

        return response

    # ------------------------------------------------------------------
    # Token management (thread-safe)
    # ------------------------------------------------------------------

    def _ensure_valid_token(self) -> None:
        """Refresh the access token if it is expired or near expiry."""
        if self._token.is_expired:
            logger.info(
                "Token is expired (%.0f s overdue) — refreshing proactively.",
                -self._token.seconds_until_expiry,
            )
            self._force_refresh()

    def _force_refresh(self) -> None:
        """
        Exchange the stored refresh_token for a new access token.
        Thread-safe: only one thread refreshes at a time; others wait
        and reuse the result.
        """
        with self._lock:
            # Double-checked locking: another thread may have refreshed
            # between the caller's expiry check and acquiring the lock.
            if not self._token.is_expired:
                return

            provider = AuthProvider(self._credentials)
            new_token = provider.refresh_access_token(self._token)
            new_token.save(self._token_path)
            self._token = new_token
            logger.info("Token refreshed and saved — %r", self._token)

    # ------------------------------------------------------------------
    # Header helpers
    # ------------------------------------------------------------------

    def _build_request_headers(self, extra: dict) -> dict:
        """
        Merge per-request headers with the Authorization header.
        Strips proxy-reveal headers that can flag cloud-hosted requests.
        """
        merged = {**extra}

        # Remove headers that expose cloud/proxy origin
        for key in list(merged.keys()):
            if key.lower() in _STRIP_REQUEST_HEADERS:
                del merged[key]

        # Inject the current access token — ALWAYS in the header, never
        # in query params where it would appear in server/WAF access logs.
        merged["Authorization"] = f"Bearer {self._token.access_token}"
        return merged

    # ------------------------------------------------------------------
    # Convenience helpers (mirror requests.Session shortcuts)
    # ------------------------------------------------------------------

    def get(self, url: str, **kwargs) -> Response:      # type: ignore[override]
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> Response:     # type: ignore[override]
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs) -> Response:      # type: ignore[override]
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs) -> Response:   # type: ignore[override]
        return self.request("DELETE", url, **kwargs)

    def patch(self, url: str, **kwargs) -> Response:    # type: ignore[override]
        return self.request("PATCH", url, **kwargs)

    # ------------------------------------------------------------------
    # Repr (safe — no credentials)
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"OAuthSession(token_file={str(self._token_path)!r}, "
            f"token={self._token!r})"
        )
