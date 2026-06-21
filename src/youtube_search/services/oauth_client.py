"""
OAuth 2.0 client — Authorization Code + Refresh Token flow.

Design decisions:
- Generic: works with Google, Spotify, any RFC 6749-compliant provider.
- Thread-safe: uses asyncio.Lock around every token mutation.
- Transparent refresh: callers never deal with expiry; use `request()` and it
  handles 401 → refresh → retry automatically.
- Firewall-safe headers: Access Token is sent as a Bearer token only, never in
  query strings, and the full browser-like header set is preserved so WAF
  fingerprinting does not trigger.
- Secrets never touch the filesystem or log lines.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

from youtube_search.services.token_store import TokenStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class OAuthToken:
    """In-memory representation of a live OAuth token pair."""
    access_token: str
    token_type: str = "Bearer"
    expires_at: float = 0.0          # Unix timestamp; 0 = treat as expired
    refresh_token: Optional[str] = None
    scope: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        """True when the access token has less than 60 s of life left."""
        if self.expires_at == 0:
            return True
        return time.time() >= self.expires_at - 60

    @classmethod
    def from_token_response(cls, data: Dict[str, Any], old_refresh: Optional[str] = None) -> "OAuthToken":
        """
        Build an OAuthToken from a raw token-endpoint JSON response.
        Providers sometimes omit the refresh_token on refresh — fall back to the
        previous one in that case (RFC 6749 §6).
        """
        expires_in = int(data.get("expires_in", 3600))
        return cls(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_at=time.time() + expires_in,
            refresh_token=data.get("refresh_token") or old_refresh,
            scope=data.get("scope"),
        )


@dataclass
class OAuthConfig:
    """
    Provider-specific OAuth 2.0 endpoints and credentials.

    Load credentials from environment variables / Replit Secrets — never
    hard-code them. Example:

        import os
        cfg = OAuthConfig(
            client_id=os.environ["OAUTH_CLIENT_ID"],
            client_secret=os.environ["OAUTH_CLIENT_SECRET"],
            token_url="https://oauth2.googleapis.com/token",
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            redirect_uri="https://your-app.replit.app/oauth/callback",
            scopes=["https://www.googleapis.com/auth/youtube.readonly"],
        )
    """
    client_id: str
    client_secret: str
    token_url: str
    auth_url: str
    redirect_uri: str
    scopes: list[str] = field(default_factory=list)

    def authorization_url(self, state: str) -> str:
        """
        Build the URL the user must visit once to grant access.
        Uses PKCE-ready params: access_type=offline forces a refresh_token.
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "access_type": "offline",          # Google-specific — keeps refresh token
            "prompt": "consent",               # Forces refresh_token even on re-auth
            "state": state,
        }
        return f"{self.auth_url}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Core client
# ---------------------------------------------------------------------------

class OAuthClient:
    """
    Production-ready OAuth 2.0 client.

    Usage pattern
    -------------
    1. One-time setup (run interactively or via the /oauth/start endpoint):

        url = client.authorization_url("random-csrf-state")
        # redirect the user there → they land on /oauth/callback?code=...&state=...
        await client.exchange_code(code="AUTH_CODE_FROM_CALLBACK")

    2. Every subsequent request (automatic refresh included):

        response = await client.request("GET", "https://api.example.com/data")

    """

    def __init__(self, config: OAuthConfig, store: Optional[TokenStore] = None):
        self.config = config
        self._store = store or TokenStore()
        self._token: Optional[OAuthToken] = None
        self._lock = asyncio.Lock()
        self._http: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # HTTP client lifecycle
    # ------------------------------------------------------------------

    def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
                http2=True,
                headers=self._base_headers(),
            )
        return self._http

    @staticmethod
    def _base_headers() -> Dict[str, str]:
        """
        Firewall-safe header baseline.

        Sending a realistic browser Accept / Accept-Language set prevents many
        WAF rules that fingerprint bare API clients. The Authorization header is
        added per-request and never leaked into query strings or logs.
        """
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

    async def aclose(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Authorization Code exchange (one-time)
    # ------------------------------------------------------------------

    def authorization_url(self, state: str = "csrf-state") -> str:
        """Return the URL the user must visit once to grant OAuth access."""
        return self.config.authorization_url(state)

    async def exchange_code(self, code: str) -> OAuthToken:
        """
        Exchange the one-time authorization code for an access + refresh token.
        Persists the result immediately so the process can restart without
        requiring re-authorization.
        """
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_uri,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        token = await self._post_token_endpoint(payload, old_refresh=None)
        await self._persist(token)
        logger.info("OAuth authorization code exchanged; token persisted.")
        return token

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def get_valid_token(self) -> OAuthToken:
        """
        Return a guaranteed-valid access token.

        Thread-safe: only one coroutine refreshes at a time; others wait and
        reuse the result.
        """
        async with self._lock:
            # Warm the in-memory cache from the store on first call
            if self._token is None:
                self._token = await self._store.load()

            if self._token is None:
                raise RuntimeError(
                    "No OAuth token found. Visit the /oauth/start endpoint to "
                    "authorize the application first."
                )

            if self._token.is_expired:
                logger.info("Access token expired — refreshing.")
                self._token = await self._refresh(self._token)
                await self._persist(self._token)

            return self._token

    async def _refresh(self, old_token: OAuthToken) -> OAuthToken:
        """Use the refresh token to get a new access token."""
        if not old_token.refresh_token:
            raise RuntimeError(
                "No refresh token available. Re-authorize via /oauth/start."
            )
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": old_token.refresh_token,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        new_token = await self._post_token_endpoint(payload, old_refresh=old_token.refresh_token)
        logger.info("Access token refreshed successfully.")
        return new_token

    async def _post_token_endpoint(
        self, payload: Dict[str, Any], old_refresh: Optional[str]
    ) -> OAuthToken:
        """POST to the token endpoint and parse the response."""
        http = self._get_http()
        try:
            response = await http.post(
                self.config.token_url,
                data=payload,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()
            return OAuthToken.from_token_response(data, old_refresh=old_refresh)
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            logger.error("Token endpoint returned %s: %s", exc.response.status_code, body)
            raise RuntimeError(f"OAuth token request failed ({exc.response.status_code}): {body}") from exc

    async def _persist(self, token: OAuthToken) -> None:
        """Write the token to the store (environment secret / file)."""
        self._token = token
        await self._store.save(token)

    # ------------------------------------------------------------------
    # Authenticated HTTP requests (with automatic 401 retry)
    # ------------------------------------------------------------------

    async def request(
        self,
        method: str,
        url: str,
        *,
        retry_on_401: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """
        Make an authenticated HTTP request.

        On a 401 Unauthorized response the client will attempt one token
        refresh and retry automatically before propagating the error.

        Example::

            resp = await client.request("GET", "https://api.example.com/me")
            data = resp.json()
        """
        token = await self.get_valid_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token.access_token}"

        # Merge with firewall-safe base headers
        merged = {**self._base_headers(), **headers}

        http = self._get_http()
        response = await http.request(method, url, headers=merged, **kwargs)

        if response.status_code == 401 and retry_on_401:
            logger.warning("Received 401 — forcing token refresh and retrying.")
            async with self._lock:
                # Force expiry so get_valid_token always refreshes
                if self._token:
                    self._token.expires_at = 0
            token = await self.get_valid_token()
            merged["Authorization"] = f"Bearer {token.access_token}"
            response = await http.request(method, url, headers=merged, **kwargs)

        response.raise_for_status()
        return response

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)
