"""
Playlist scraper with OAuth 2.0 priority and Invidious fallback.

Fetch priority
--------------
1. **YouTube direct — OAuth Bearer** (NEW): uses the Bearer token from
   ``oauth.json`` if available, bypassing WAF cookie checks.
2. **Invidious API**: public, no credentials needed — most reliable on
   free hosting.
3. **YouTube direct — unauthenticated**: last resort, frequently blocked
   in cloud environments.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from youtube_search.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Invidious instances for playlist fetching.
INVIDIOUS_INSTANCES = [
    "https://vid.puffyan.us",
    "https://invidious.fdn.fr",
    "https://inv.tux.pizza",
    "https://invidious.perennialte.ch",
    "https://iv.ggtyler.dev",
]

# Browser-like header baseline for authenticated requests.
_AUTH_HEADERS: dict[str, str] = {
    "Accept": "application/json, text/html, application/xhtml+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class PlaylistScraper:
    """Scraper for YouTube playlist metadata with OAuth 2.0 support."""

    def __init__(self) -> None:
        self.client: Optional[httpx.AsyncClient] = None
        self.settings = get_settings()

    async def __aenter__(self) -> "PlaylistScraper":
        import os
        verify_ssl = os.getenv("YTDL_VERIFY_SSL", "true").lower() == "true"

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                max(self.settings.youtube_timeout, 30) * 2, connect=15.0
            ),
            follow_redirects=True,
            verify=verify_ssl,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            http2=True,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    async def get_playlist_metadata(
        self,
        playlist_url: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Fetch playlist metadata.

        Priority:
          1. YouTube direct with OAuth Bearer token (when available)
          2. Invidious API
          3. YouTube direct unauthenticated

        Args:
            playlist_url:  Full YouTube playlist URL.
            force_refresh: Ignored (reserved for cache integration).
        """
        playlist_id = self._extract_playlist_id(playlist_url)
        if not playlist_id:
            raise ValueError("Invalid playlist URL — missing 'list' parameter.")

        logger.info("Fetching playlist: %s", playlist_id)

        # ── Priority 1: OAuth authenticated direct fetch ─────────────────
        oauth_token: Optional[str] = None
        try:
            from youtube_search.auth.token_provider import get_valid_access_token
            oauth_token = await get_valid_access_token()
        except Exception as exc:
            logger.debug("OAuth token unavailable: %s", exc)

        if oauth_token:
            try:
                result = await self._get_from_youtube_oauth(
                    playlist_id, playlist_url, oauth_token
                )
                if result:
                    logger.info(
                        "OAuth playlist fetch succeeded: %d tracks.",
                        len(result.get("tracks", [])),
                    )
                    return result
            except Exception as exc:
                logger.warning("OAuth playlist fetch failed (%s) — falling back.", exc)

        # ── Priority 2: Invidious ────────────────────────────────────────
        try:
            result = await self._get_from_invidious(playlist_id, playlist_url)
            if result:
                logger.info(
                    "Invidious playlist fetch succeeded: %d tracks.",
                    len(result.get("tracks", [])),
                )
                return result
        except Exception as exc:
            logger.warning("Invidious playlist failed: %s", exc)

        # ── Priority 3: YouTube direct unauthenticated ───────────────────
        logger.warning("Falling back to unauthenticated YouTube direct request.")
        response = await self.client.get(playlist_url)
        response.raise_for_status()

        return {
            "playlist_id": playlist_id,
            "url": playlist_url,
            "title": "Playlist",
            "video_count": 0,
            "partial": True,
            "tracks": [],
        }

    # ------------------------------------------------------------------
    # Priority 1 (NEW): OAuth authenticated YouTube playlist page
    # ------------------------------------------------------------------

    async def _get_from_youtube_oauth(
        self,
        playlist_id: str,
        playlist_url: str,
        oauth_token: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch the YouTube playlist page with an OAuth Bearer token.

        The Authorization header prevents WAF cookie-based blocking and
        ensures the session is treated as a logged-in user request.
        """
        headers = {
            "User-Agent": _USER_AGENT,
            **_AUTH_HEADERS,
            "Authorization": f"Bearer {oauth_token}",
        }

        response = await self.client.get(
            playlist_url, headers=headers, timeout=20.0
        )
        response.raise_for_status()

        html = response.text

        # Extract basic playlist metadata from ytInitialData JSON embedded in HTML.
        title_match = re.search(r'"title":\{"simpleText":"([^"]+)"', html)
        title = title_match.group(1) if title_match else "Playlist"

        video_ids = re.findall(r'"videoId":"([^"]+)"', html)
        video_titles = re.findall(
            r'"title":\{"runs":\[\{"text":"([^"]+)"', html
        )

        tracks: List[Dict[str, Any]] = []
        for i, (vid, ttl) in enumerate(
            zip(video_ids, video_titles or [""]*len(video_ids)), start=1
        ):
            tracks.append({
                "video_id": vid,
                "title": ttl,
                "channel": None,
                "channel_url": None,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "publish_date": None,
                "duration": None,
                "view_count": 0,
                "position": i,
            })

        if not tracks:
            return None

        return {
            "playlist_id": playlist_id,
            "url": playlist_url,
            "title": title,
            "video_count": len(tracks),
            "partial": False,
            "tracks": tracks,
        }

    # ------------------------------------------------------------------
    # Priority 2: Invidious API (unchanged)
    # ------------------------------------------------------------------

    async def _get_from_invidious(
        self, playlist_id: str, playlist_url: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch playlist metadata from Invidious API."""
        for instance in INVIDIOUS_INSTANCES:
            try:
                api_url = f"{instance}/api/v1/playlists/{playlist_id}"
                response = await self.client.get(api_url, timeout=20.0)
                response.raise_for_status()

                data = response.json()
                tracks: List[Dict[str, Any]] = [
                    {
                        "video_id": v.get("videoId"),
                        "title": v.get("title"),
                        "channel": v.get("author"),
                        "channel_url": v.get("authorUrl"),
                        "url": f"https://www.youtube.com/watch?v={v.get('videoId')}",
                        "publish_date": v.get("publishedText"),
                        "duration": self._format_duration(v.get("lengthSeconds", 0)),
                        "view_count": v.get("viewCount", 0),
                        "position": i,
                    }
                    for i, v in enumerate(data.get("videos", []), start=1)
                ]

                return {
                    "playlist_id": playlist_id,
                    "url": playlist_url,
                    "title": data.get("title", "Playlist"),
                    "video_count": data.get("videoCount", len(tracks)),
                    "partial": False,
                    "tracks": tracks,
                }

            except Exception as exc:
                logger.warning("Invidious instance %s failed: %s", instance, exc)

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_playlist_id(url: str) -> Optional[str]:
        """Extract the playlist ID from a YouTube URL."""
        match = re.search(r"list=([a-zA-Z0-9_-]+)", url)
        return match.group(1) if match else None

    @staticmethod
    def _format_duration(seconds: int) -> str:
        """Format seconds into ``MM:SS`` or ``HH:MM:SS``."""
        if seconds < 3600:
            return f"{seconds // 60}:{seconds % 60:02d}"
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------

_playlist_scraper_instance: Optional[PlaylistScraper] = None


def get_playlist_scraper() -> PlaylistScraper:
    """Return the process-level singleton ``PlaylistScraper``."""
    global _playlist_scraper_instance
    if _playlist_scraper_instance is None:
        _playlist_scraper_instance = PlaylistScraper()
    return _playlist_scraper_instance
