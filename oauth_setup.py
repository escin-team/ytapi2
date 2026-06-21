#!/usr/bin/env python3
"""
OAuth 2.0 Device Authorization Grant — one-time setup CLI.

Run this script ONCE to authorize the application:

    python scripts/oauth_setup.py

What it does
------------
1. Reads OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET from environment /
   .env file (see .env.example or set via Replit Secrets panel 🔒).
2. Requests a device code + user code from Google's OAuth endpoint.
3. Prints clear instructions for the operator.
4. Polls silently until the operator authorizes (or the code expires).
5. Writes the access_token + refresh_token to oauth.json.

After this script completes successfully, the application will use
OAuthSession to authenticate all requests automatically — no further
manual steps are needed until you explicitly revoke the token.

Security reminders
------------------
• oauth.json contains long-lived credentials — add it to .gitignore.
• Never commit oauth.json to version control.
• The script never logs token values, only metadata.
"""

from __future__ import annotations

import sys
import os
import time
import logging
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: ensure the project src/ is on sys.path so auth imports work
# when the script is run from the project root.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env", override=False)

from youtube_search.auth.credentials import OAuthCredentials
from youtube_search.auth.auth_provider import AuthProvider, DeviceCodeResponse
from youtube_search.auth.token import OAuthToken

# ---------------------------------------------------------------------------
# Logging: INFO to stdout, no noise during setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,   # suppress library debug spam
    format="%(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------

def _banner(title: str) -> None:
    width = 60
    print("\n" + "═" * width)
    print(f"  {title}")
    print("═" * width)


def _step(n: int, text: str) -> None:
    print(f"\n  [{n}] {text}")


def _success(text: str) -> None:
    print(f"\n  ✅ {text}")


def _error(text: str) -> None:
    print(f"\n  ❌ {text}", file=sys.stderr)


def _info(text: str) -> None:
    print(f"      {text}")


# ---------------------------------------------------------------------------
# Spinner / progress indicator
# ---------------------------------------------------------------------------

_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_spinner_idx = 0


def _tick(elapsed: float, remaining: float) -> None:
    """Called by poll_for_token on each authorization_pending tick."""
    global _spinner_idx
    frame = _SPINNER[_spinner_idx % len(_SPINNER)]
    _spinner_idx += 1
    print(
        f"\r      {frame}  Waiting for authorization…  "
        f"({int(remaining)}s remaining)   ",
        end="",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Main setup flow
# ---------------------------------------------------------------------------

def run_setup() -> None:
    _banner("YouTube Music API — OAuth 2.0 Device Setup")

    # ── Step 1: Load credentials ─────────────────────────────────────
    _step(1, "Loading OAuth credentials from environment…")
    try:
        credentials = OAuthCredentials.from_env()
    except EnvironmentError as exc:
        _error(str(exc))
        _info("Add OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET via:")
        _info("  • Replit Secrets panel (🔒), or")
        _info("  • a .env file in the project root.")
        _info("")
        _info("Google Cloud setup instructions:")
        _info("  1. Go to https://console.cloud.google.com/apis/credentials")
        _info("  2. Create an OAuth 2.0 Client ID")
        _info("  3. Application type: 'TV and Limited Input devices'")
        _info("  4. Copy the Client ID and Client Secret")
        sys.exit(1)

    _info(f"Client ID   : {credentials.client_id[:12]}…  (from env)")
    _info(f"Scopes      : {credentials.scope_string}")
    _info(f"Token file  : {credentials.token_file}")

    # ── Step 2: Request device code ──────────────────────────────────
    _step(2, "Requesting device code from Google…")
    provider = AuthProvider(credentials)

    try:
        device: DeviceCodeResponse = provider.request_device_code()
    except Exception as exc:
        _error(f"Failed to get device code: {exc}")
        sys.exit(1)

    # ── Step 3: Show instructions to operator ────────────────────────
    _step(3, "Action required — complete authorization in your browser:")
    print()
    print("  ┌─────────────────────────────────────────────────────┐")
    print(f"  │  Visit  : {device.verification_url:<42}│")
    print(f"  │  Code   : {device.user_code:<42}│")
    print(f"  │  Expires: {device.expires_in} seconds                             │")
    print("  └─────────────────────────────────────────────────────┘")
    print()
    _info("Steps:")
    _info(f"  a) Open  {device.verification_url}  in a browser.")
    _info(f"  b) Sign in with your Google account.")
    _info(f"  c) Enter the code: {device.user_code}")
    _info(f"  d) Click 'Allow' to grant access.")
    _info("")
    _info("This script will detect the authorization automatically.")

    # ── Step 4: Poll until authorized ───────────────────────────────
    _step(4, "Waiting for authorization (polling silently)…")
    print()

    try:
        token: OAuthToken = provider.poll_for_token(device, on_pending=_tick)
    except PermissionError:
        print()  # clear spinner line
        _error("Authorization was denied by the user.")
        sys.exit(1)
    except TimeoutError as exc:
        print()
        _error(str(exc))
        sys.exit(1)
    except Exception as exc:
        print()
        _error(f"Unexpected error during polling: {exc}")
        sys.exit(1)

    print()  # clear spinner line

    # ── Step 5: Save token ───────────────────────────────────────────
    _step(5, "Saving token to disk…")
    try:
        token.save(credentials.token_file)
    except Exception as exc:
        _error(f"Failed to save token to {credentials.token_file}: {exc}")
        sys.exit(1)

    # ── Done ─────────────────────────────────────────────────────────
    _banner("Setup Complete")
    _success(f"oauth.json written to: {credentials.token_file.resolve()}")
    _info("")
    _info("Token details:")
    _info(f"  Type     : {token.token_type}")
    _info(f"  Expires  : in {int(token.seconds_until_expiry)} seconds "
          f"(auto-refreshed on expiry)")
    _info(f"  Scope    : {token.scope or 'not reported by server'}")
    _info("")
    _info("Security reminder:")
    _info("  • Add oauth.json to .gitignore")
    _info("  • Never commit oauth.json to version control")
    _info("")
    _info("You can now start the API server:")
    _info("  PYTHONPATH=src python -m uvicorn main:app --host 0.0.0.0 --port 5000")
    print()


# ---------------------------------------------------------------------------
# Gitignore guard — warn if oauth.json is not ignored
# ---------------------------------------------------------------------------

def _check_gitignore() -> None:
    gitignore = _PROJECT_ROOT / ".gitignore"
    if not gitignore.exists():
        return
    content = gitignore.read_text(encoding="utf-8")
    if "oauth.json" not in content:
        print(
            "\n  ⚠️  WARNING: oauth.json is not in your .gitignore.\n"
            "     Run:  echo 'oauth.json' >> .gitignore\n"
            "     to prevent accidentally committing your credentials.\n"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _check_gitignore()
    run_setup()
