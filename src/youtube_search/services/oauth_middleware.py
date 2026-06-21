"""
httpx transport layer that injects OAuth Bearer tokens transparently.

Drop this transport into any existing httpx.AsyncClient and every request
automatically carries a valid, refreshed Authorization header — no changes
needed in calling code.

Usage::

    from youtube_search.services.oauth_middleware import OAuthTransport
    from youtube_search.services.oauth_client import OAuthClient, OAuthConfig

    config = OAuthConfig(...)
    client_obj = OAuthClient(config)

    async with httpx.AsyncClient(
        transport=OAuthTransport(client_obj),
        base_url="https://api.example.com",
    ) as http:
        resp = await http.get("/me")   # Authorization header injected automatically

The transport also handles:
- 401 Unauthorized → force-refresh → single retry (WAF-safe; no hammering)
- Header sanitization: removes any outbound X-Forwarded-For / X-Real-IP that
  could confuse upstream WAFs into treating the request as proxied.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from youtube_search.services.oauth_client import OAuthClient

logger = logging.getLogger(__name__)


class OAuthTransport(httpx.AsyncBaseTransport):
    """
    Async httpx transport that injects and auto-refreshes OAuth Bearer tokens.

    Parameters
    ----------
    oauth_client : OAuthClient
        The initialized OAuthClient that holds the token and can refresh it.
    inner : httpx.AsyncBaseTransport, optional
        The underlying transport (default: httpx.AsyncHTTPTransport with HTTP/2).
    """

    # Headers that reveal proxy / cloud-hosting origin — stripping them
    # reduces WAF fingerprinting risk.
    _STRIP_HEADERS = frozenset({
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
        "via",
        "forwarded",
    })

    def __init__(
        self,
        oauth_client: "OAuthClient",
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._oauth = oauth_client
        self._inner = inner or httpx.AsyncHTTPTransport(http2=True)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # ── 1. Attach a valid access token ───────────────────────────────────
        token = await self._oauth.get_valid_token()
        request = self._inject_auth(request, token.access_token)
        request = self._sanitize_headers(request)

        # ── 2. Fire the request ───────────────────────────────────────────────
        response = await self._inner.handle_async_request(request)

        # ── 3. On 401: refresh once and retry ────────────────────────────────
        if response.status_code == 401:
            logger.warning(
                "Received 401 from %s — forcing token refresh.", request.url
            )
            # Force expiry so get_valid_token triggers a refresh
            if self._oauth._token:
                self._oauth._token.expires_at = 0

            token = await self._oauth.get_valid_token()
            request = self._inject_auth(request, token.access_token)
            response = await self._inner.handle_async_request(request)

        return response

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_auth(request: httpx.Request, access_token: str) -> httpx.Request:
        """Return a copy of the request with the Authorization header set."""
        headers = dict(request.headers)
        headers["Authorization"] = f"Bearer {access_token}"
        return httpx.Request(
            method=request.method,
            url=request.url,
            headers=headers,
            content=request.content,
            extensions=request.extensions,
        )

    @staticmethod
    def _sanitize_headers(request: httpx.Request) -> httpx.Request:
        """Strip proxy-reveal headers that can trigger WAF rules."""
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in OAuthTransport._STRIP_HEADERS
        }
        return httpx.Request(
            method=request.method,
            url=request.url,
            headers=headers,
            content=request.content,
            extensions=request.extensions,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()
