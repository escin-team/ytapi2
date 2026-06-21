"""
OAuth 2.0 flow endpoints.

Two independent auth methods — both produce a valid oauth.json or TokenStore
entry that the services consume automatically.  They can coexist.

━━━ Method A: Authorization Code flow (browser redirect) ━━━━━━━━━━━━━━━━━━━
GET  /oauth/status            — Check whether OAuth is configured and authorized
GET  /oauth/start             — Redirect the user to the provider's consent screen
GET  /oauth/callback          — Receive the authorization code and exchange it
POST /oauth/refresh           — Force-refresh the access token now
POST /oauth/revoke            — Clear stored tokens (logout)

Setup:
  1. Set OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_TOKEN_URL,
     OAUTH_AUTH_URL, OAUTH_REDIRECT_URI, OAUTH_SCOPES via Replit Secrets (🔒).
  2. Visit GET /oauth/start — browser redirect to Google consent screen.
  3. Google calls GET /oauth/callback — tokens stored, done.

━━━ Method B: Device Code flow (CLI or API — no browser redirect needed) ━━━━
POST /oauth/device/start      — Request a device code; returns user_code + URL
POST /oauth/device/poll       — Poll once to check if user has authorized
GET  /oauth/device/status     — Check oauth.json token validity (device flow)
POST /oauth/device/revoke     — Delete oauth.json (device flow logout)

Setup (via API — equivalent to running scripts/oauth_setup.py):
  1. Set OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET via Replit Secrets (🔒).
     Use a "TV and Limited Input devices" client from Google Cloud Console.
  2. POST /oauth/device/start — get user_code and verification_url.
  3. Open verification_url in a browser, enter user_code, click Allow.
  4. POST /oauth/device/poll (every 5 s) until {"status": "authorized"}.
  Done — services now use the token from oauth.json automatically.

Setup (via terminal — manual method, unchanged):
  python scripts/oauth_setup.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse

from youtube_search.services.oauth_registry import (
    get_oauth_client,
    is_configured,
    build_config,
)
from youtube_search.services.token_store import TokenStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/oauth", tags=["OAuth 2.0"])

# ---------------------------------------------------------------------------
# In-memory session store for Device Code sessions
# (process-scoped; one process, one active setup at a time is typical)
# ---------------------------------------------------------------------------

# Maps session_id → DeviceCodeResponse dataclass from auth_provider
_device_sessions: dict[str, object] = {}

# Simple in-memory CSRF state store (process-scoped; fine for a single-worker API)
_pending_states: set[str] = set()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/status", summary="Check OAuth configuration and token status")
async def oauth_status():
    """
    Returns whether OAuth credentials are configured and a valid token exists.
    Does NOT expose any credential values.
    """
    configured = is_configured()
    token_present = False
    token_expired = True

    if configured:
        try:
            client = get_oauth_client()
            store = TokenStore()
            token = await store.load()
            if token:
                token_present = True
                token_expired = token.is_expired
        except Exception as exc:
            logger.warning("OAuth status check error: %s", exc)

    return {
        "oauth_configured": configured,
        "token_present": token_present,
        "token_expired": token_expired,
        "ready": configured and token_present and not token_expired,
        "next_step": (
            None if (configured and token_present and not token_expired)
            else ("Set OAUTH_* secrets via the Replit Secrets panel (🔒), then visit /oauth/start"
                  if not configured
                  else "Visit /oauth/start to authorize the application")
        ),
    }


# ---------------------------------------------------------------------------
# Start (redirect to provider)
# ---------------------------------------------------------------------------

@router.get("/start", summary="Begin the OAuth 2.0 Authorization Code flow")
async def oauth_start():
    """
    Redirects the browser to the OAuth provider's consent screen.
    After granting access the provider will call /oauth/callback.

    Requires: OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_TOKEN_URL,
              OAUTH_AUTH_URL, OAUTH_REDIRECT_URI, OAUTH_SCOPES
    """
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "OAuth credentials not configured. "
                "Add OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_TOKEN_URL, "
                "OAUTH_AUTH_URL, OAUTH_REDIRECT_URI, and OAUTH_SCOPES "
                "via the Replit Secrets panel (🔒)."
            ),
        )

    state = secrets.token_urlsafe(32)
    _pending_states.add(state)

    client = get_oauth_client()
    auth_url = client.authorization_url(state=state)

    logger.info("OAuth flow started; redirecting to provider.")
    return RedirectResponse(url=auth_url, status_code=302)


# ---------------------------------------------------------------------------
# Callback (receive code, exchange for tokens)
# ---------------------------------------------------------------------------

@router.get("/callback", summary="OAuth 2.0 callback — receives the authorization code")
async def oauth_callback(
    code: str = Query(..., description="Authorization code from provider"),
    state: str = Query(..., description="CSRF state token"),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
):
    """
    The OAuth provider redirects here after the user grants (or denies) access.
    Exchanges the one-time code for access + refresh tokens and stores them.
    """
    if error:
        logger.error("OAuth provider returned error: %s — %s", error, error_description)
        raise HTTPException(
            status_code=400,
            detail=f"OAuth authorization denied: {error} — {error_description or ''}",
        )

    if state not in _pending_states:
        logger.warning("OAuth callback received unknown state: %s", state)
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state token.")

    _pending_states.discard(state)

    try:
        client = get_oauth_client()
        token = await client.exchange_code(code)
        logger.info("OAuth code exchanged successfully. Scopes: %s", token.scope)
    except Exception as exc:
        logger.error("OAuth code exchange failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}")

    return JSONResponse({
        "status": "authorized",
        "token_type": token.token_type,
        "scope": token.scope,
        "expires_in_seconds": max(0, int(token.expires_at - __import__("time").time())),
        "has_refresh_token": bool(token.refresh_token),
        "message": (
            "Authorization successful. Tokens stored. "
            "The API will now automatically refresh the access token as needed."
        ),
    })


# ---------------------------------------------------------------------------
# Force refresh
# ---------------------------------------------------------------------------

@router.post("/refresh", summary="Force-refresh the OAuth access token now")
async def oauth_force_refresh():
    """
    Immediately exchanges the stored refresh token for a new access token.
    Normally this happens automatically — use this endpoint only for testing
    or after a manual token revocation.
    """
    if not is_configured():
        raise HTTPException(status_code=503, detail="OAuth not configured.")

    try:
        client = get_oauth_client()
        # Force expiry → next get_valid_token call will refresh
        if client._token:
            client._token.expires_at = 0

        token = await client.get_valid_token()
        return {
            "status": "refreshed",
            "expires_in_seconds": max(0, int(token.expires_at - __import__("time").time())),
            "scope": token.scope,
        }
    except Exception as exc:
        logger.error("Force refresh failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# Revoke / logout
# ---------------------------------------------------------------------------

@router.post("/revoke", summary="Clear stored OAuth tokens")
async def oauth_revoke():
    """
    Removes all stored token data from the process and from the fallback
    file. Does NOT call the provider's revocation endpoint — do that
    separately if required.

    After revoking, visit /oauth/start to re-authorize.
    """
    store = TokenStore()
    await store.clear()

    # Reset the singleton's in-memory token
    if is_configured():
        try:
            client = get_oauth_client()
            client._token = None
        except Exception:
            pass

    logger.info("OAuth tokens revoked.")
    return {"status": "revoked", "message": "Visit /oauth/start to re-authorize."}


# ===========================================================================
# METHOD B — Device Code flow (API equivalent of scripts/oauth_setup.py)
# ===========================================================================
#
# Usage pattern (call from any HTTP client, no browser redirect required):
#
#   1.  POST /oauth/device/start
#       → { session_id, user_code, verification_url, expires_in, interval }
#
#   2.  Open  verification_url  in a browser, sign in, enter  user_code.
#
#   3.  POST /oauth/device/poll?session_id=<id>   (repeat every `interval` s)
#       → { "status": "pending" }       — keep polling
#       → { "status": "authorized", … } — done, token saved to oauth.json
#       → { "status": "expired" }       — restart from step 1
#       → { "status": "denied" }        — user clicked Deny
#
#   4.  GET  /oauth/device/status       — verify the token is live
#
# ===========================================================================


def _device_credentials_configured() -> bool:
    """True when OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET are both set."""
    return bool(
        os.getenv("OAUTH_CLIENT_ID", "").strip()
        and os.getenv("OAUTH_CLIENT_SECRET", "").strip()
    )


# ---------------------------------------------------------------------------
# POST /oauth/device/start
# ---------------------------------------------------------------------------

@router.post(
    "/device/start",
    summary="[Device flow] Step 1 — Request a device code from Google",
    tags=["OAuth 2.0 — Device Code Flow"],
)
async def device_start():
    """
    Initiates the OAuth 2.0 Device Authorization Grant (RFC 8628).

    **Equivalent to running** ``python scripts/oauth_setup.py`` from the terminal.

    Returns a ``user_code`` and ``verification_url`` the operator must visit
    once to authorize the application.  After visiting the URL, call
    ``POST /oauth/device/poll`` (with the returned ``session_id``) every
    ``interval`` seconds until ``status`` is ``"authorized"``.

    **Required secrets** (set via Replit Secrets panel 🔒):
    - ``OAUTH_CLIENT_ID`` — from Google Cloud Console
    - ``OAUTH_CLIENT_SECRET`` — from Google Cloud Console
    - Application type must be **"TV and Limited Input devices"**
    """
    if not _device_credentials_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Device Code flow requires OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET. "
                "Add them via the Replit Secrets panel (🔒). "
                "Create a 'TV and Limited Input devices' OAuth client at "
                "https://console.cloud.google.com/apis/credentials"
            ),
        )

    try:
        from youtube_search.auth.credentials import OAuthCredentials
        from youtube_search.auth.auth_provider import AuthProvider

        creds = await asyncio.to_thread(OAuthCredentials.from_env)
        provider = AuthProvider(creds)
        device = await asyncio.to_thread(provider.request_device_code)
    except Exception as exc:
        logger.error("Device code request failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to obtain device code from Google: {exc}",
        )

    # Store the device response so /device/poll can look it up
    session_id = secrets.token_urlsafe(24)
    _device_sessions[session_id] = device

    logger.info(
        "Device code session started — session_id=%s, expires_in=%ds",
        session_id, device.expires_in,
    )

    return {
        "session_id": session_id,
        "user_code": device.user_code,
        "verification_url": device.verification_url,
        "expires_in": device.expires_in,
        "interval": device.interval,
        "instructions": (
            f"1. Open {device.verification_url} in a browser. "
            f"2. Sign in with your Google account. "
            f"3. Enter code: {device.user_code}. "
            f"4. Click Allow. "
            f"5. Then call POST /oauth/device/poll?session_id={session_id} "
            f"every {device.interval} seconds until status is 'authorized'."
        ),
    }


# ---------------------------------------------------------------------------
# POST /oauth/device/poll
# ---------------------------------------------------------------------------

@router.post(
    "/device/poll",
    summary="[Device flow] Step 2 — Poll once to check authorization status",
    tags=["OAuth 2.0 — Device Code Flow"],
)
async def device_poll(
    session_id: str = Query(..., description="session_id returned by POST /oauth/device/start"),
):
    """
    Polls the Google token endpoint **once** using the stored device code.

    Call this endpoint every ``interval`` seconds (from ``/device/start`` response)
    until the response ``status`` is one of:

    | status          | meaning                                      | action          |
    |-----------------|----------------------------------------------|-----------------|
    | ``pending``     | User has not authorized yet                  | poll again      |
    | ``slow_down``   | Server asked to reduce polling rate          | wait +5 s       |
    | ``authorized``  | Token saved to ``oauth.json``                | **done**        |
    | ``denied``      | User clicked Deny                            | restart setup   |
    | ``expired``     | Device code expired before authorization     | restart setup   |
    """
    if session_id not in _device_sessions:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Session '{session_id}' not found. "
                "Call POST /oauth/device/start first, or the session may have expired."
            ),
        )

    device = _device_sessions[session_id]

    # Check if the device code has expired locally (avoids a pointless network call)
    deadline = time.time() - device.expires_in  # approximate; provider is authoritative
    # We don't track start time here — let the provider tell us via error code instead.

    try:
        from youtube_search.auth.credentials import OAuthCredentials
        from youtube_search.auth.auth_provider import AuthProvider
        import requests as _requests

        creds = await asyncio.to_thread(OAuthCredentials.from_env)
        provider = AuthProvider(creds)

        # Build the token-endpoint payload manually (one shot, no loop)
        payload = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device.device_code,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
        }

        def _poll_once() -> dict:
            resp = provider._session.post(
                creds.token_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            return {"status_code": resp.status_code, "body": resp.json()}

        result = await asyncio.to_thread(_poll_once)
        status_code = result["status_code"]
        body = result["body"]

    except Exception as exc:
        logger.error("Device poll network error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Token endpoint error: {exc}")

    # ── Successful token response ────────────────────────────────────────────
    if status_code == 200 and "access_token" in body:
        try:
            from youtube_search.auth.token import OAuthToken
            from youtube_search.auth.credentials import OAuthCredentials

            creds_obj = await asyncio.to_thread(OAuthCredentials.from_env)
            token = OAuthToken.from_response(body)
            await asyncio.to_thread(token.save, creds_obj.token_file)

            # Clean up session
            _device_sessions.pop(session_id, None)

            logger.info(
                "Device flow authorized — token saved to %s", creds_obj.token_file
            )
            return {
                "status": "authorized",
                "token_type": token.token_type,
                "scope": token.scope,
                "expires_in_seconds": max(0, int(token.seconds_until_expiry)),
                "token_file": str(creds_obj.token_file),
                "message": (
                    "Authorization successful. Token saved to oauth.json. "
                    "All services will now use OAuth authentication automatically."
                ),
            }
        except Exception as exc:
            logger.error("Failed to save device token: %s", exc)
            raise HTTPException(status_code=500, detail=f"Token save failed: {exc}")

    # ── OAuth error codes (RFC 8628 §3.5) ───────────────────────────────────
    error_code: str = body.get("error", "")

    if error_code == "authorization_pending":
        return {"status": "pending", "message": "User has not authorized yet — keep polling."}

    if error_code == "slow_down":
        return {
            "status": "slow_down",
            "message": "Poll less frequently — add 5 seconds to your interval.",
            "recommended_interval_seconds": device.interval + 5,
        }

    if error_code == "access_denied":
        _device_sessions.pop(session_id, None)
        return {
            "status": "denied",
            "message": "The user denied authorization. Call /device/start to restart.",
        }

    if error_code == "expired_token":
        _device_sessions.pop(session_id, None)
        return {
            "status": "expired",
            "message": "Device code expired. Call POST /oauth/device/start to restart.",
        }

    # Unknown error
    _device_sessions.pop(session_id, None)
    logger.error("Unexpected token endpoint error: %s — %s", error_code, body)
    raise HTTPException(
        status_code=502,
        detail=f"Unexpected token endpoint error '{error_code}': {body}",
    )


# ---------------------------------------------------------------------------
# GET /oauth/device/status
# ---------------------------------------------------------------------------

@router.get(
    "/device/status",
    summary="[Device flow] Check oauth.json token validity",
    tags=["OAuth 2.0 — Device Code Flow"],
)
async def device_status():
    """
    Reports whether a valid Device Code token (``oauth.json``) is present.

    This is separate from ``GET /oauth/status``, which checks the
    Authorization Code flow's ``TokenStore``.  Both can be active at the
    same time.
    """
    configured = _device_credentials_configured()
    token_path_str = os.getenv("OAUTH_JSON_PATH", "oauth.json")
    token_path = Path(token_path_str)

    token_present = False
    token_expired = True
    expires_in_seconds: Optional[int] = None
    scope: Optional[str] = None

    if token_path.exists():
        try:
            from youtube_search.auth.token import OAuthToken
            token = await asyncio.to_thread(OAuthToken.load, token_path)
            token_present = True
            token_expired = token.is_expired
            expires_in_seconds = max(0, int(token.seconds_until_expiry))
            scope = token.scope
        except Exception as exc:
            logger.warning("Device status — token load error: %s", exc)

    ready = configured and token_present and not token_expired

    return {
        "method": "device_code",
        "credentials_configured": configured,
        "token_file": token_path_str,
        "token_present": token_present,
        "token_expired": token_expired,
        "expires_in_seconds": expires_in_seconds,
        "scope": scope,
        "ready": ready,
        "next_step": (
            None
            if ready
            else (
                "Set OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET via Replit Secrets (🔒), "
                "then call POST /oauth/device/start"
                if not configured
                else (
                    "Call POST /oauth/device/start to begin authorization"
                    if not token_present
                    else "Token expired — call POST /oauth/device/start to re-authorize"
                )
            )
        ),
    }


# ---------------------------------------------------------------------------
# POST /oauth/device/revoke
# ---------------------------------------------------------------------------

@router.post(
    "/device/revoke",
    summary="[Device flow] Delete oauth.json and clear active sessions",
    tags=["OAuth 2.0 — Device Code Flow"],
)
async def device_revoke():
    """
    Removes ``oauth.json`` from disk and clears any in-flight device sessions.

    Does **not** call Google's token revocation endpoint — do that manually
    at https://myaccount.google.com/permissions if required.

    After revoking, call ``POST /oauth/device/start`` to re-authorize.
    """
    token_path = Path(os.getenv("OAUTH_JSON_PATH", "oauth.json"))
    removed = False

    if token_path.exists():
        try:
            await asyncio.to_thread(token_path.unlink)
            removed = True
            logger.info("oauth.json deleted by /oauth/device/revoke")
        except Exception as exc:
            logger.error("Failed to delete oauth.json: %s", exc)
            raise HTTPException(status_code=500, detail=f"Failed to delete oauth.json: {exc}")

    # Clear all in-flight device sessions
    _device_sessions.clear()

    return {
        "status": "revoked",
        "token_file_removed": removed,
        "message": (
            "oauth.json deleted and all device sessions cleared. "
            "Call POST /oauth/device/start to re-authorize."
        ),
    }


# ---------------------------------------------------------------------------
# GET /oauth/device/setup  — interactive browser UI
# ---------------------------------------------------------------------------

_SETUP_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Google OAuth Setup</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0f1117;
    color: #e2e8f0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }

  .card {
    background: #1a1d27;
    border: 1px solid #2d3148;
    border-radius: 16px;
    padding: 40px;
    width: 100%;
    max-width: 520px;
    box-shadow: 0 24px 64px rgba(0,0,0,0.5);
  }

  .logo-row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 28px;
  }
  .logo-icon {
    width: 40px; height: 40px; border-radius: 10px;
    background: linear-gradient(135deg, #ff4040 0%, #ff0000 100%);
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; flex-shrink: 0;
  }
  .logo-text { font-size: 18px; font-weight: 700; color: #fff; }
  .logo-sub  { font-size: 12px; color: #64748b; margin-top: 1px; }

  h1 { font-size: 22px; font-weight: 700; color: #fff; margin-bottom: 6px; }
  .subtitle { font-size: 14px; color: #64748b; margin-bottom: 28px; line-height: 1.5; }

  /* Status badge */
  .status-badge {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 6px 14px; border-radius: 999px; font-size: 13px; font-weight: 500;
    margin-bottom: 24px;
  }
  .status-badge .dot {
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  }
  .badge-ok    { background: rgba(34,197,94,0.12); color: #22c55e; border: 1px solid rgba(34,197,94,0.25); }
  .badge-warn  { background: rgba(234,179,8,0.12);  color: #eab308; border: 1px solid rgba(234,179,8,0.25); }
  .badge-error { background: rgba(239,68,68,0.12);  color: #ef4444; border: 1px solid rgba(239,68,68,0.25); }
  .badge-ok   .dot { background: #22c55e; box-shadow: 0 0 6px #22c55e; }
  .badge-warn .dot { background: #eab308; box-shadow: 0 0 6px #eab308; }
  .badge-error .dot { background: #ef4444; box-shadow: 0 0 6px #ef4444; }

  /* Primary button */
  .btn-primary {
    width: 100%; padding: 14px 20px; border-radius: 10px; border: none;
    background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
    color: #fff; font-size: 15px; font-weight: 600; cursor: pointer;
    display: flex; align-items: center; justify-content: center; gap: 10px;
    transition: opacity 0.15s, transform 0.1s;
    letter-spacing: 0.01em;
  }
  .btn-primary:hover:not(:disabled) { opacity: 0.88; transform: translateY(-1px); }
  .btn-primary:active:not(:disabled) { transform: translateY(0); }
  .btn-primary:disabled { opacity: 0.45; cursor: not-allowed; }

  .btn-danger {
    width: 100%; padding: 11px 20px; border-radius: 10px;
    border: 1px solid rgba(239,68,68,0.35);
    background: rgba(239,68,68,0.08); color: #ef4444;
    font-size: 14px; font-weight: 500; cursor: pointer;
    transition: background 0.15s;
    margin-top: 10px;
  }
  .btn-danger:hover { background: rgba(239,68,68,0.16); }

  /* Code box */
  .code-box {
    background: #0f1117; border: 1px solid #2d3148; border-radius: 12px;
    padding: 24px; margin: 20px 0; text-align: center;
  }
  .user-code {
    font-family: "SF Mono", "Fira Code", monospace;
    font-size: 38px; font-weight: 800; letter-spacing: 10px;
    color: #fff; display: block; margin-bottom: 12px;
  }
  .verify-url {
    font-size: 13px; color: #94a3b8;
    word-break: break-all; line-height: 1.5;
  }
  .verify-url a { color: #60a5fa; text-decoration: none; }
  .verify-url a:hover { text-decoration: underline; }

  /* Timer */
  .timer-row {
    display: flex; align-items: center; justify-content: space-between;
    margin: 16px 0 8px; font-size: 13px; color: #64748b;
  }
  .timer-value { font-weight: 600; color: #e2e8f0; }
  .progress-bar-wrap {
    width: 100%; height: 4px; background: #2d3148; border-radius: 2px;
    overflow: hidden; margin-bottom: 20px;
  }
  .progress-bar-fill {
    height: 100%; background: linear-gradient(90deg, #3b82f6, #6366f1);
    border-radius: 2px; transition: width 1s linear;
  }

  /* Steps */
  .steps { list-style: none; margin: 20px 0; }
  .steps li {
    display: flex; gap: 12px; align-items: flex-start;
    padding: 10px 0; border-bottom: 1px solid #1e2235; font-size: 14px;
    color: #94a3b8; line-height: 1.5;
  }
  .steps li:last-child { border-bottom: none; }
  .step-num {
    width: 24px; height: 24px; border-radius: 50%; border: 1.5px solid #2d3148;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 700; color: #64748b; flex-shrink: 0;
    margin-top: 1px;
  }
  .step-done .step-num { border-color: #22c55e; color: #22c55e; background: rgba(34,197,94,0.1); }
  .step-active .step-num { border-color: #3b82f6; color: #3b82f6; background: rgba(59,130,246,0.1); }
  .step-done > span:last-child { text-decoration: line-through; color: #4b5563; }
  .step-active > span:last-child { color: #e2e8f0; }

  /* Poll status indicator */
  .poll-row {
    display: flex; align-items: center; gap: 8px;
    font-size: 13px; color: #64748b; margin-top: 4px;
  }
  .poll-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: #3b82f6; animation: pulse 1.2s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%       { opacity: 0.4; transform: scale(0.75); }
  }

  /* Success panel */
  .success-panel {
    background: rgba(34,197,94,0.08); border: 1px solid rgba(34,197,94,0.25);
    border-radius: 12px; padding: 24px; text-align: center;
  }
  .success-icon { font-size: 42px; margin-bottom: 12px; }
  .success-title { font-size: 18px; font-weight: 700; color: #22c55e; margin-bottom: 6px; }
  .success-sub { font-size: 13px; color: #94a3b8; line-height: 1.5; }
  .success-scope {
    display: inline-block; margin-top: 12px; padding: 4px 12px;
    background: rgba(34,197,94,0.12); border-radius: 999px;
    font-size: 12px; color: #4ade80; font-family: monospace;
  }

  /* Error panel */
  .error-panel {
    background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.25);
    border-radius: 12px; padding: 16px; font-size: 13px; color: #fca5a5;
    line-height: 1.5; margin-top: 16px;
  }

  .divider { height: 1px; background: #2d3148; margin: 24px 0; }
  .footer-links { display: flex; gap: 20px; justify-content: center; }
  .footer-links a {
    font-size: 12px; color: #475569; text-decoration: none;
    transition: color 0.15s;
  }
  .footer-links a:hover { color: #94a3b8; }

  .hidden { display: none !important; }
  .spinner { animation: spin 0.8s linear infinite; display: inline-block; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="card">

  <!-- Header -->
  <div class="logo-row">
    <div class="logo-icon">▶</div>
    <div>
      <div class="logo-text">YouTube Music API</div>
      <div class="logo-sub">OAuth 2.0 Device Setup</div>
    </div>
  </div>

  <!-- Current status -->
  <div id="statusBadge" class="status-badge badge-warn">
    <span class="dot"></span>
    <span id="statusText">Checking status…</span>
  </div>

  <!-- ── IDLE PANEL ── -->
  <div id="panelIdle">
    <h1>Connect with Google</h1>
    <p class="subtitle">
      Authorize this API to access YouTube on your behalf.
      No browser redirect required — you'll enter a short code on Google's site.
    </p>

    <ul class="steps" id="stepList">
      <li id="step1" class="step-active">
        <div class="step-num">1</div>
        <span>Click <strong>Connect with Google</strong> below</span>
      </li>
      <li id="step2">
        <div class="step-num">2</div>
        <span>Open the link that appears and enter the code</span>
      </li>
      <li id="step3">
        <div class="step-num">3</div>
        <span>Click Allow on the Google consent screen</span>
      </li>
      <li id="step4">
        <div class="step-num">4</div>
        <span>This page detects authorization automatically</span>
      </li>
    </ul>

    <button id="btnConnect" class="btn-primary" onclick="startFlow()">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/>
        <polyline points="10 17 15 12 10 7"/>
        <line x1="15" y1="12" x2="3" y2="12"/>
      </svg>
      Connect with Google
    </button>

    <div id="errorPanel" class="error-panel hidden"></div>
  </div>

  <!-- ── POLLING PANEL ── -->
  <div id="panelPolling" class="hidden">
    <h1>Authorize on Google</h1>
    <p class="subtitle">Open the link below, sign in, and enter the code.</p>

    <div class="code-box">
      <span id="userCode" class="user-code">—</span>
      <div class="verify-url">
        Go to <a id="verifyLink" href="#" target="_blank" rel="noopener noreferrer">—</a>
        and enter the code above
      </div>
    </div>

    <div class="timer-row">
      <span>Code expires in</span>
      <span id="timerValue" class="timer-value">—</span>
    </div>
    <div class="progress-bar-wrap">
      <div id="progressBar" class="progress-bar-fill" style="width:100%"></div>
    </div>

    <div class="poll-row">
      <div class="poll-dot"></div>
      <span id="pollStatus">Waiting for authorization…</span>
    </div>

    <ul class="steps" style="margin-top:16px">
      <li class="step-done">
        <div class="step-num">✓</div>
        <span>Device code requested</span>
      </li>
      <li class="step-active">
        <div class="step-num">2</div>
        <span>Visit the URL &amp; enter the code above</span>
      </li>
      <li id="step3poll">
        <div class="step-num">3</div>
        <span>Click Allow on Google's consent screen</span>
      </li>
      <li>
        <div class="step-num">4</div>
        <span>Detecting authorization automatically…</span>
      </li>
    </ul>
  </div>

  <!-- ── SUCCESS PANEL ── -->
  <div id="panelSuccess" class="hidden">
    <div class="success-panel">
      <div class="success-icon">✅</div>
      <div class="success-title">Connected to Google</div>
      <div class="success-sub">
        Your OAuth token has been saved. All services now use it automatically
        — searches, downloads, and playlist fetches will use your Google account.
      </div>
      <div id="successScope" class="success-scope hidden"></div>
    </div>

    <div class="divider"></div>

    <button class="btn-danger" onclick="revokeToken()">
      Disconnect &amp; remove token
    </button>
  </div>

  <!-- Footer -->
  <div class="divider" style="margin-top:28px"></div>
  <div class="footer-links">
    <a href="/docs" target="_blank">Swagger UI</a>
    <a href="/oauth/device/status" target="_blank">Token status (JSON)</a>
    <a href="https://console.cloud.google.com/apis/credentials" target="_blank">
      Google Cloud Console
    </a>
  </div>
</div>

<script>
  // ─── State ──────────────────────────────────────────────────────────────
  let sessionId = null;
  let pollTimer = null;
  let countdownTimer = null;
  let expiresIn = 0;
  let totalExpiry = 0;
  let pollInterval = 5;
  let pollCount = 0;

  // ─── Init ────────────────────────────────────────────────────────────────
  window.addEventListener('DOMContentLoaded', checkCurrentStatus);

  async function checkCurrentStatus() {
    try {
      const res = await fetch('/oauth/device/status');
      const data = await res.json();
      if (data.ready) {
        showSuccess({ scope: data.scope, expires_in_seconds: data.expires_in_seconds });
      } else {
        setBadge('warn', 'Not connected — click below to authorize');
      }
    } catch {
      setBadge('error', 'Could not reach API');
    }
  }

  // ─── Flow ────────────────────────────────────────────────────────────────
  async function startFlow() {
    const btn = document.getElementById('btnConnect');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner">⟳</span> Requesting device code…';
    hideError();

    try {
      const res = await fetch('/oauth/device/start', { method: 'POST' });
      const data = await res.json();

      if (!res.ok) {
        showError(data.detail || 'Failed to start device flow.');
        btn.disabled = false;
        btn.innerHTML = '↩ Connect with Google';
        return;
      }

      sessionId   = data.session_id;
      pollInterval = data.interval || 5;
      expiresIn   = data.expires_in;
      totalExpiry = data.expires_in;

      // Populate code box
      document.getElementById('userCode').textContent = data.user_code;
      const link = document.getElementById('verifyLink');
      link.textContent = data.verification_url;
      link.href = data.verification_url;

      showPanel('polling');
      setBadge('warn', 'Waiting for authorization…');
      startCountdown();
      schedulePoll();

    } catch (err) {
      showError('Network error: ' + err.message);
      btn.disabled = false;
      btn.innerHTML = '↩ Connect with Google';
    }
  }

  function schedulePoll() {
    pollTimer = setTimeout(poll, pollInterval * 1000);
  }

  async function poll() {
    if (!sessionId) return;
    pollCount++;
    document.getElementById('pollStatus').textContent =
      `Checking… (attempt ${pollCount})`;

    try {
      const res = await fetch(
        `/oauth/device/poll?session_id=${encodeURIComponent(sessionId)}`,
        { method: 'POST' }
      );
      const data = await res.json();

      switch (data.status) {
        case 'authorized':
          clearTimers();
          showSuccess(data);
          return;

        case 'pending':
          document.getElementById('pollStatus').textContent =
            'Waiting for you to authorize on Google…';
          schedulePoll();
          break;

        case 'slow_down':
          pollInterval = data.recommended_interval_seconds || pollInterval + 5;
          document.getElementById('pollStatus').textContent =
            `Slowing down — polling every ${pollInterval}s`;
          schedulePoll();
          break;

        case 'denied':
          clearTimers();
          showPanel('idle');
          showError('Authorization denied. Click Connect to try again.');
          setBadge('error', 'Authorization denied');
          sessionId = null;
          break;

        case 'expired':
          clearTimers();
          showPanel('idle');
          showError('Code expired. Click Connect to start a new session.');
          setBadge('error', 'Code expired');
          sessionId = null;
          document.getElementById('btnConnect').disabled = false;
          document.getElementById('btnConnect').innerHTML = '↩ Connect with Google';
          break;

        default:
          document.getElementById('pollStatus').textContent =
            'Unknown status: ' + data.status;
          schedulePoll();
      }
    } catch (err) {
      document.getElementById('pollStatus').textContent =
        'Poll error — retrying…';
      schedulePoll();
    }
  }

  async function revokeToken() {
    if (!confirm('Remove the OAuth token? The API will stop using Google authentication.')) return;
    try {
      const res = await fetch('/oauth/device/revoke', { method: 'POST' });
      if (res.ok) {
        showPanel('idle');
        setBadge('warn', 'Not connected — click below to authorize');
        document.getElementById('btnConnect').disabled = false;
        document.getElementById('btnConnect').innerHTML =
          '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg> Connect with Google';
      }
    } catch {
      alert('Failed to revoke token.');
    }
  }

  // ─── Countdown ───────────────────────────────────────────────────────────
  function startCountdown() {
    updateCountdown();
    countdownTimer = setInterval(() => {
      expiresIn--;
      if (expiresIn <= 0) {
        clearTimers();
        if (document.getElementById('panelPolling').classList.contains('hidden') === false) {
          showPanel('idle');
          showError('Code expired. Click Connect to start a new session.');
          setBadge('error', 'Code expired');
          sessionId = null;
          document.getElementById('btnConnect').disabled = false;
          document.getElementById('btnConnect').innerHTML = '↩ Connect with Google';
        }
        return;
      }
      updateCountdown();
    }, 1000);
  }

  function updateCountdown() {
    const m = Math.floor(expiresIn / 60);
    const s = expiresIn % 60;
    document.getElementById('timerValue').textContent =
      m > 0 ? `${m}m ${s}s` : `${s}s`;
    const pct = Math.max(0, (expiresIn / totalExpiry) * 100);
    document.getElementById('progressBar').style.width = pct + '%';
  }

  // ─── UI helpers ──────────────────────────────────────────────────────────
  function showPanel(name) {
    ['idle','polling','success'].forEach(p => {
      document.getElementById('panel' + p.charAt(0).toUpperCase() + p.slice(1))
        .classList.toggle('hidden', p !== name);
    });
  }

  function showSuccess(data) {
    showPanel('success');
    setBadge('ok', 'Connected to Google');
    if (data.scope) {
      const el = document.getElementById('successScope');
      el.textContent = data.scope;
      el.classList.remove('hidden');
    }
  }

  function setBadge(type, text) {
    const badge = document.getElementById('statusBadge');
    badge.className = 'status-badge badge-' + (type === 'ok' ? 'ok' : type === 'error' ? 'error' : 'warn');
    document.getElementById('statusText').textContent = text;
  }

  function showError(msg) {
    const el = document.getElementById('errorPanel');
    el.textContent = '⚠ ' + msg;
    el.classList.remove('hidden');
  }
  function hideError() {
    document.getElementById('errorPanel').classList.add('hidden');
  }

  function clearTimers() {
    if (pollTimer)      { clearTimeout(pollTimer);   pollTimer = null; }
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
  }
</script>
</body>
</html>"""


@router.get(
    "/device/setup",
    response_class=HTMLResponse,
    summary="[Device flow] Interactive browser setup UI",
    tags=["OAuth 2.0 — Device Code Flow"],
    include_in_schema=True,
)
async def device_setup_ui():
    """
    Opens an interactive browser page for the Device Code OAuth flow.

    - Click **Connect with Google** to request a device code.
    - The page shows a large user code and a clickable verification URL.
    - A live countdown timer tracks the code's expiry.
    - The page polls automatically every ``interval`` seconds and detects
      authorization without any manual action.
    - On success, shows a confirmation panel.  On failure, shows a clear
      error with instructions to retry.

    **Equivalent to the full ``scripts/oauth_setup.py`` terminal flow,
    but entirely in the browser.**
    """
    return HTMLResponse(content=_SETUP_PAGE_HTML, status_code=200)
