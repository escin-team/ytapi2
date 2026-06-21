"""
OAuth 2.0 Device Authorization Grant — auth package.

Public surface
--------------
    from youtube_search.auth import OAuthToken, AuthProvider, OAuthSession
    from youtube_search.auth import get_valid_access_token          # async
    from youtube_search.auth import get_valid_access_token_sync     # sync

Quick-start
-----------
    # One-time setup (CLI):
    python scripts/oauth_setup.py

    # Every subsequent use (in application code):
    from youtube_search.auth import OAuthSession
    session = OAuthSession()                       # loads oauth.json automatically
    resp = session.get("https://api.example.com/me")

    # Lightweight token-only access (for yt-dlp, httpx, etc.):
    from youtube_search.auth import get_valid_access_token
    token = await get_valid_access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
"""

from youtube_search.auth.token import OAuthToken
from youtube_search.auth.credentials import OAuthCredentials
from youtube_search.auth.auth_provider import AuthProvider
from youtube_search.auth.session_manager import OAuthSession
from youtube_search.auth.token_provider import (
    get_valid_access_token,
    get_valid_access_token_sync,
    build_auth_header,
)

__all__ = [
    "OAuthToken",
    "OAuthCredentials",
    "AuthProvider",
    "OAuthSession",
    "get_valid_access_token",
    "get_valid_access_token_sync",
    "build_auth_header",
]
