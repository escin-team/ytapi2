"""
OAuth 2.0 Device Authorization Grant — RFC 8628.

Inspired by ytmusicapi/auth/oauth.py:
https://github.com/escin-team/ytmusicapi/blob/b59f59c6faaa75d2b4c0544161ab8fdb54a0c2c3/ytmusicapi/auth

Flow
----
1.  ``request_device_code()``  — POST to the device-auth endpoint; get
    device_code + user_code + verification_url.
2.  Show the user_code and URL to the operator (printed by setup.py).
3.  ``poll_for_token()``       — poll the token endpoint every `interval`
    seconds until:
        • The user completes authorization  → returns OAuthToken
        • ``authorization_pending``         → keep polling
        • ``slow_down``                     → increase interval by 5 s
        • ``access_denied``                 → raise PermissionError
        • ``expired_token``                 → raise TimeoutError
4.  ``refresh_access_token()`` — exchange a refresh_token for a new
    access_token (used by SessionManager on 401 or proactive expiry).

All network errors are retried with exponential back-off up to
``_MAX_RETRIES`` attempts; transient 5xx responses don't abort the flow.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

from youtube_search.auth.credentials import OAuthCredentials
from youtube_search.auth.token import OAuthToken

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_RETRIES: int = 3          # Network retries per single HTTP call
_RETRY_BACKOFF: float = 1.5    # Multiplier for retry sleep
_SLOW_DOWN_INCREMENT: int = 5  # Extra seconds added when server says slow_down
_REQUEST_TIMEOUT: int = 15     # Per-request socket timeout in seconds

# Device Code endpoint error codes (RFC 8628 §3.5)
_ERR_PENDING   = "authorization_pending"
_ERR_SLOW_DOWN = "slow_down"
_ERR_DENIED    = "access_denied"
_ERR_EXPIRED   = "expired_token"


# ---------------------------------------------------------------------------
# Data class returned from request_device_code()
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeviceCodeResponse:
    """
    Parsed response from the device authorization endpoint.

    Attributes
    ----------
    device_code       : Opaque code sent to the token endpoint during polling.
    user_code         : Short human-readable code shown to the operator.
    verification_url  : URL the operator visits to enter the user_code.
    expires_in        : Lifetime of device_code in seconds.
    interval          : Minimum polling interval in seconds (default 5).
    """

    device_code: str
    user_code: str
    verification_url: str
    expires_in: int
    interval: int = 5

    @classmethod
    def from_response(cls, data: dict) -> "DeviceCodeResponse":
        return cls(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_url=data.get("verification_url", data.get("verification_uri", "")),
            expires_in=int(data.get("expires_in", 1800)),
            interval=int(data.get("interval", 5)),
        )


# ---------------------------------------------------------------------------
# Core provider class
# ---------------------------------------------------------------------------

class AuthProvider:
    """
    Device Authorization Grant flow (RFC 8628).

    Parameters
    ----------
    credentials : OAuthCredentials
        Loaded via ``OAuthCredentials.from_env()``.  Secrets must come
        from environment variables — never pass raw strings here.

    Example
    -------
    ::

        creds    = OAuthCredentials.from_env()
        provider = AuthProvider(creds)

        device   = provider.request_device_code()
        print(f"Visit {device.verification_url} and enter: {device.user_code}")

        token    = provider.poll_for_token(device)     # blocks until authorized
        token.save(creds.token_file)
    """

    def __init__(self, credentials: OAuthCredentials) -> None:
        self._creds = credentials
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # Step 1: Request device + user codes
    # ------------------------------------------------------------------

    def request_device_code(self) -> DeviceCodeResponse:
        """
        POST to the device authorization endpoint to get the user_code and
        verification_url the operator needs to complete authorization.

        Returns
        -------
        DeviceCodeResponse
            Contains device_code (for polling) and user_code (for display).

        Raises
        ------
        requests.HTTPError  On a non-2xx response after all retries.
        RuntimeError        If the response is missing required fields.
        """
        payload = {
            "client_id": self._creds.client_id,
            "scope": self._creds.scope_string,
        }

        logger.info(
            "Requesting device code from %s with scopes: %s",
            self._creds.device_auth_url, self._creds.scope_string,
        )

        data = self._post_with_retry(self._creds.device_auth_url, payload)

        for required in ("device_code", "user_code"):
            if required not in data:
                raise RuntimeError(
                    f"Device auth response missing '{required}'. "
                    f"Full response: {data}"
                )

        device = DeviceCodeResponse.from_response(data)
        logger.info(
            "Device code obtained — expires in %d s, poll every %d s",
            device.expires_in, device.interval,
        )
        return device

    # ------------------------------------------------------------------
    # Step 2: Poll until authorized
    # ------------------------------------------------------------------

    def poll_for_token(
        self,
        device: DeviceCodeResponse,
        on_pending: Optional[callable] = None,
    ) -> OAuthToken:
        """
        Blocking poll loop — calls the token endpoint every ``interval``
        seconds until the user authorizes or the device_code expires.

        Parameters
        ----------
        device     : DeviceCodeResponse from ``request_device_code()``.
        on_pending : Optional callback invoked on each ``authorization_pending``
                     tick.  Signature: ``on_pending(elapsed: float, remaining: float)``.

        Returns
        -------
        OAuthToken  Ready to be persisted with ``token.save(path)``.

        Raises
        ------
        PermissionError   If the user explicitly denies access.
        TimeoutError      If the device_code expires before authorization.
        RuntimeError      On unexpected token endpoint errors.
        """
        interval: int = device.interval
        deadline: float = time.time() + device.expires_in

        payload = {
            "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device.device_code,
            "client_id":   self._creds.client_id,
            "client_secret": self._creds.client_secret,
        }

        logger.info(
            "Starting token poll — device code expires in %d s, "
            "polling every %d s.",
            device.expires_in, interval,
        )

        while time.time() < deadline:
            time.sleep(interval)

            try:
                data = self._post_with_retry(self._creds.token_url, payload)
            except requests.HTTPError as exc:
                # 4xx errors from the token endpoint carry an error code in JSON
                try:
                    err_body = exc.response.json()
                except Exception:
                    raise exc

                error_code: str = err_body.get("error", "")
                logger.debug("Poll response error_code=%s", error_code)

                if error_code == _ERR_PENDING:
                    elapsed   = time.time() - (deadline - device.expires_in)
                    remaining = deadline - time.time()
                    logger.debug("Authorization pending — %.0f s remaining", remaining)
                    if on_pending:
                        on_pending(elapsed, remaining)
                    continue

                if error_code == _ERR_SLOW_DOWN:
                    interval += _SLOW_DOWN_INCREMENT
                    logger.info(
                        "Server requested slow-down — new interval: %d s", interval
                    )
                    continue

                if error_code == _ERR_DENIED:
                    raise PermissionError(
                        "The user denied the authorization request."
                    )

                if error_code == _ERR_EXPIRED:
                    raise TimeoutError(
                        "The device code expired before the user authorized.\n"
                        "Run  python scripts/oauth_setup.py  again to restart."
                    )

                raise RuntimeError(
                    f"Unexpected token endpoint error '{error_code}': {err_body}"
                )

            # Successful token response
            token = OAuthToken.from_response(data)
            logger.info("Authorization successful — %r", token)
            return token

        raise TimeoutError(
            "Device code expired (polling loop deadline exceeded).\n"
            "Run  python scripts/oauth_setup.py  again."
        )

    # ------------------------------------------------------------------
    # Step 3: Refresh an expired access token
    # ------------------------------------------------------------------

    def refresh_access_token(self, current_token: OAuthToken) -> OAuthToken:
        """
        Exchange ``current_token.refresh_token`` for a new access token.

        The server may or may not return a new refresh_token; if it doesn't,
        the existing one is reused (RFC 6749 §6).

        Parameters
        ----------
        current_token : The token whose access_token has expired (or is near
                        expiry).  Must have a non-empty ``refresh_token``.

        Returns
        -------
        OAuthToken   Fresh token ready to be persisted.

        Raises
        ------
        ValueError         If current_token has no refresh_token.
        requests.HTTPError On a non-recoverable token endpoint error.
        """
        if not current_token.refresh_token:
            raise ValueError(
                "Cannot refresh: current token has no refresh_token.\n"
                "Run  python scripts/oauth_setup.py  to re-authorize."
            )

        payload = {
            "grant_type":    "refresh_token",
            "refresh_token": current_token.refresh_token,
            "client_id":     self._creds.client_id,
            "client_secret": self._creds.client_secret,
        }

        logger.info("Refreshing access token...")
        data = self._post_with_retry(self._creds.token_url, payload)
        new_token = OAuthToken.from_response(
            data, old_refresh=current_token.refresh_token
        )
        logger.info("Access token refreshed — %r", new_token)
        return new_token

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post_with_retry(self, url: str, payload: dict) -> dict:
        """
        POST ``payload`` as form-encoded data to ``url``.

        Retries up to ``_MAX_RETRIES`` times on network errors or 5xx
        responses, with exponential back-off.  4xx responses (including
        OAuth error codes) are returned to the caller after a single
        attempt so the polling loop can inspect them.

        Returns
        -------
        dict  Parsed JSON response body.

        Raises
        ------
        requests.HTTPError  On non-5xx HTTP errors or exhausted retries.
        """
        sleep = 1.0
        last_exc: Optional[Exception] = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._session.post(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=_REQUEST_TIMEOUT,
                )

                # 4xx with OAuth error body → raise immediately for caller
                if 400 <= resp.status_code < 500:
                    resp.raise_for_status()

                # 5xx → retry
                if resp.status_code >= 500:
                    logger.warning(
                        "Server error %d on attempt %d/%d — retrying in %.1f s",
                        resp.status_code, attempt, _MAX_RETRIES, sleep,
                    )
                    time.sleep(sleep)
                    sleep *= _RETRY_BACKOFF
                    last_exc = requests.HTTPError(response=resp)
                    continue

                resp.raise_for_status()
                return resp.json()

            except requests.ConnectionError as exc:
                logger.warning(
                    "Network error on attempt %d/%d: %s — retrying in %.1f s",
                    attempt, _MAX_RETRIES, exc, sleep,
                )
                time.sleep(sleep)
                sleep *= _RETRY_BACKOFF
                last_exc = exc

        raise last_exc or RuntimeError("All retry attempts exhausted.")
