"""
YouTube scraper with OAuth 2.0 priority and multi-layer fallback.

Search priority
---------------
1. **YouTube direct — OAuth Bearer** (NEW): uses the YouTube Data API or
   direct search with an ``Authorization: Bearer`` header.  Most reliable
   when a valid ``oauth.json`` exists.  Skipped silently when OAuth is
   not configured.
2. **Invidious API**: public, no credentials needed — most reliable on
   free hosting.
3. **curl-cffi browser impersonation**: bypasses many SSL/TLS
   fingerprint checks.
4. **youtube-search package**: lightweight fallback library.
5. **YouTube direct — unauthenticated / cookies**: last resort; frequently
   blocked in cloud environments.

The token is loaded once per ``search()`` call and passed down to
``_search_youtube_oauth()``.  All other methods are unchanged so that the
existing fallback chain continues to work as before.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from typing import Any, Dict, List, Optional

import httpx

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    curl_requests = None  # type: ignore[assignment]
    CURL_CFFI_AVAILABLE = False

from youtube_search.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

INVIDIOUS_INSTANCES = [
    "https://inv.tux.pizza",
    "https://invidious.perennialte.ch",
    "https://yewtu.be",
    "https://invidious.fdn.fr",
    "https://vid.puffyan.us",
    "https://invidious.privacyredirect.com",
    "https://iv.datura.network",
    "https://invidious.io.lol",
    "https://yt.artemislena.eu",
    "https://invidious.tiekoetter.com",
]

COOKIE_FILE_PATHS = [
    "/workspace/attached_assets/cookies_1781947987715.txt",
    "./attached_assets/cookies_1781947987715.txt",
    "/app/attached_assets/cookies_1781947987715.txt",
    os.path.expanduser("~/.config/youtube-cookies.txt"),
]

# Browser-like header baseline for all authenticated requests.
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


# ---------------------------------------------------------------------------
# Helper: parse HTML ytInitialData video entries
# ---------------------------------------------------------------------------

def _parse_yt_html(html: str, limit: int) -> List[Dict[str, Any]]:
    """Extract video_id + title pairs from a raw YouTube search HTML page."""
    pattern = r'"videoId":"([^"]+)".*?"title":\{"runs":\[\{"text":"([^"]+)"'
    videos: List[Dict[str, Any]] = []
    for video_id, title in re.findall(pattern, html)[:limit]:
        try:
            import json as _json
            decoded_title = _json.loads(f'"{title}"')
        except Exception:
            decoded_title = title
        videos.append({
            "video_id": video_id,
            "title": decoded_title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "channel": None,
            "channel_url": None,
            "publish_date": None,
            "view_count": 0,
            "description": "",
            "duration": None,
        })
    return videos


# ---------------------------------------------------------------------------
# Scraper class
# ---------------------------------------------------------------------------

class YouTubeScraper:
    """
    Multi-layer YouTube search with OAuth 2.0 as the highest-priority path.
    """

    def __init__(self) -> None:
        self.client: Optional[httpx.AsyncClient] = None
        self.curl_session: Optional[Any] = None
        self.settings = get_settings()
        self.cookies = self._load_cookies()

    # ------------------------------------------------------------------
    # Cookie file loader (legacy path)
    # ------------------------------------------------------------------

    def _load_cookies(self) -> Optional[str]:
        """Load YouTube/Google cookies from a Netscape cookie file."""
        for path in COOKIE_FILE_PATHS:
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        content = f.read()
                    youtube_lines = [
                        line for line in content.splitlines()
                        if not line.startswith("#") and line.strip()
                        and len(line.split("\t")) >= 6
                        and ("youtube.com" in line or "google.com" in line)
                    ]
                    if youtube_lines:
                        logger.info(
                            "Loaded %d YouTube/Google cookies from %s",
                            len(youtube_lines), path,
                        )
                        return "\n".join(youtube_lines)
                except Exception as exc:
                    logger.warning("Failed to load cookies from %s: %s", path, exc)
        logger.debug("No YouTube cookies found — unauthenticated fallback active.")
        return None

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "YouTubeScraper":
        verify_ssl = os.getenv("YTDL_VERIFY_SSL", "true").lower() == "true"

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                max(self.settings.youtube_timeout, 30) * 2, connect=15.0
            ),
            follow_redirects=True,
            verify=verify_ssl,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
            http2=True,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )

        if CURL_CFFI_AVAILABLE and curl_requests is not None:
            self.curl_session = curl_requests.Session(
                impersonate="chrome120",
                timeout=max(self.settings.youtube_timeout, 30),
                allow_redirects=True,
            )
        else:
            self.curl_session = None
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None
        if self.curl_session:
            self.curl_session.close()
            self.curl_session = None

    # ------------------------------------------------------------------
    # Public search entry-point
    # ------------------------------------------------------------------

    async def search(
        self,
        keyword: str,
        limit: int = 20,
        sort_by: str = "relevance",
    ) -> List[Dict[str, Any]]:
        """
        Search YouTube with a graceful multi-layer fallback.

        Priority (highest → lowest):
          1. YouTube direct with OAuth Bearer token  ← NEW
          2. Invidious API
          3. curl-cffi browser impersonation
          4. youtube-search fallback package
          5. YouTube direct unauthenticated / cookies

        Args:
            keyword: Search query string.
            limit:   Maximum number of results.
            sort_by: ``"relevance"`` (default) or ``"date"``.
        """
        # ── Attempt to get OAuth token once for this search ─────────────
        oauth_token: Optional[str] = None
        try:
            from youtube_search.auth.token_provider import get_valid_access_token
            oauth_token = await get_valid_access_token()
        except Exception as exc:
            logger.debug("OAuth token fetch skipped: %s", exc)

        # ── Priority 1: YouTube direct with OAuth Bearer token ──────────
        if oauth_token:
            try:
                results = await self._search_youtube_oauth(keyword, limit, sort_by, oauth_token)
                if results:
                    logger.info(
                        "OAuth authenticated search succeeded: %d results.", len(results)
                    )
                    return results
            except Exception as exc:
                logger.warning("OAuth search failed (%s) — falling back.", exc)

        # ── Priority 2: Invidious API ────────────────────────────────────
        try:
            results = await self._search_invidious(keyword, limit, sort_by)
            if results:
                logger.info("Invidious search succeeded: %d results.", len(results))
                return results
        except Exception as exc:
            logger.warning("Invidious search failed: %s", exc)

        # ── Priority 3: curl-cffi browser impersonation ──────────────────
        try:
            results = await self._search_curl_cffi(keyword, limit, sort_by)
            if results:
                logger.info("curl-cffi search succeeded: %d results.", len(results))
                return results
        except Exception as exc:
            logger.warning("curl-cffi search failed: %s", exc)

        # ── Priority 4: youtube-search package ───────────────────────────
        try:
            results = await self._search_fallback_package(keyword, limit)
            if results:
                logger.info("Fallback package search succeeded: %d results.", len(results))
                return results
        except Exception as exc:
            logger.warning("Fallback package search failed: %s", exc)

        # ── Priority 5: YouTube direct unauthenticated ───────────────────
        try:
            results = await self._search_youtube_direct(keyword, limit, sort_by)
            if results:
                logger.info("YouTube direct search succeeded: %d results.", len(results))
                return results
        except Exception as exc:
            logger.error("YouTube direct search failed: %s", exc)

        logger.warning("All search methods exhausted for query: %r", keyword)
        return []

    # ------------------------------------------------------------------
    # Priority 1 (NEW): OAuth authenticated YouTube direct search
    # ------------------------------------------------------------------

    async def _search_youtube_oauth(
        self,
        keyword: str,
        limit: int,
        sort_by: str,
        oauth_token: str,
    ) -> List[Dict[str, Any]]:
        """
        Search YouTube directly with an OAuth 2.0 Bearer token.

        The Authorization header eliminates cookie dependency and bypasses
        most WAF rules that flag unauthenticated cloud-hosted requests.
        Proxy-reveal headers (X-Forwarded-For, Via) are explicitly absent.
        """
        search_url = f"{self.settings.youtube_base_url}?search_query={keyword}"
        if sort_by == "date":
            search_url += "&sp=CAI%253D"

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            **_AUTH_HEADERS,
            "Authorization": f"Bearer {oauth_token}",
        }

        response = await self.client.get(
            search_url,
            headers=headers,
            timeout=max(self.settings.youtube_timeout, 30),
        )
        response.raise_for_status()

        results = _parse_yt_html(response.text, limit)
        if not results:
            raise ValueError("OAuth search returned HTML but no video entries were parsed.")
        return results

    # ------------------------------------------------------------------
    # Priority 2: Invidious API (unchanged)
    # ------------------------------------------------------------------

    async def _search_invidious(
        self, keyword: str, limit: int, sort_by: str
    ) -> List[Dict[str, Any]]:
        """Search via Invidious API — most reliable on free hosting."""
        sort_param = {
            "relevance": "relevance",
            "date": "upload_date",
            "views": "view_count",
            "rating": "rating",
        }.get(sort_by, "relevance")

        for instance in INVIDIOUS_INSTANCES:
            try:
                api_url = f"{instance}/api/v1/search"
                response = await self.client.get(
                    api_url,
                    params={"q": keyword, "sort_by": sort_param, "type": "video", "page": 1},
                    timeout=15.0,
                )
                response.raise_for_status()
                data = response.json()

                videos: List[Dict[str, Any]] = []
                for item in data:
                    if item.get("type") != "video" or len(videos) >= limit:
                        continue
                    thumbnails = item.get("videoThumbnails", [])
                    thumbnail_url = next(
                        (t.get("url") for t in thumbnails if t.get("quality") in ("medium", "high")),
                        thumbnails[0].get("url") if thumbnails else None,
                    )
                    videos.append({
                        "video_id": item.get("videoId"),
                        "title": item.get("title"),
                        "url": f"https://www.youtube.com/watch?v={item.get('videoId')}",
                        "channel": item.get("author"),
                        "channel_url": item.get("authorUrl"),
                        "publish_date": item.get("publishedText"),
                        "view_count": item.get("viewCount", 0) or 0,
                        "description": (item.get("description") or "")[:200],
                        "duration": item.get("lengthSeconds"),
                        "thumbnail": thumbnail_url,
                    })

                if videos:
                    return videos

            except Exception as exc:
                logger.warning("Invidious instance %s failed: %s", instance, exc)

        return []

    # ------------------------------------------------------------------
    # Priority 3: curl-cffi (unchanged)
    # ------------------------------------------------------------------

    async def _search_curl_cffi(
        self, keyword: str, limit: int, sort_by: str
    ) -> List[Dict[str, Any]]:
        """Search YouTube using curl-cffi browser impersonation."""
        search_url = f"{self.settings.youtube_base_url}?search_query={keyword}"
        if sort_by == "date":
            search_url += "&sp=CAI%253D"

        response = await asyncio.to_thread(
            lambda: self.curl_session.get(
                search_url,
                headers={
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                },
                timeout=max(self.settings.youtube_timeout, 30),
            )
        )
        return _parse_yt_html(response.text, limit)

    # ------------------------------------------------------------------
    # Priority 4: youtube-search package (unchanged)
    # ------------------------------------------------------------------

    async def _search_fallback_package(
        self, keyword: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Search via the youtube-search package (ultimate fallback)."""
        import importlib.util
        import sys

        pkg_path: Optional[str] = None
        for p in sys.path:
            if "site-packages" in p:
                candidate = f"{p}/youtube_search/__init__.py"
                if os.path.exists(candidate):
                    pkg_path = candidate
                    break
        if pkg_path is None:
            raise ImportError("youtube-search package not found in site-packages")

        spec = importlib.util.spec_from_file_location("_yt_search_pkg", pkg_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        YoutubeSearch = mod.YoutubeSearch

        results = await asyncio.to_thread(
            lambda: YoutubeSearch(keyword, max_results=limit).to_dict()
        )

        videos: List[Dict[str, Any]] = []
        for item in results:
            video_id = item.get("id")
            url_suffix = item.get("url_suffix", "")
            videos.append({
                "video_id": video_id,
                "title": item.get("title"),
                "url": f"https://www.youtube.com{url_suffix}",
                "channel": item.get("channel"),
                "channel_url": None,
                "publish_date": item.get("publish_time"),
                "view_count": self._parse_view_count(item.get("views", "0")),
                "description": (item.get("long_desc") or "")[:200],
                "duration": self._parse_duration(item.get("duration", "0:00")),
                "thumbnail": f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
            })
        return videos

    # ------------------------------------------------------------------
    # Priority 5: YouTube direct unauthenticated / cookies (unchanged)
    # ------------------------------------------------------------------

    async def _search_youtube_direct(
        self, keyword: str, limit: int, sort_by: str
    ) -> List[Dict[str, Any]]:
        """
        Direct YouTube search — unauthenticated or cookie-based.

        This is the lowest-priority fallback; it is frequently blocked in
        cloud environments.  Prefer any of the methods above.
        """
        search_url = f"{self.settings.youtube_base_url}?search_query={keyword}"
        if sort_by == "date":
            search_url += "&sp=CAI%253D"

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        }

        cookies_dict: Optional[dict] = None
        if self.cookies:
            cookies_dict = {}
            for line in self.cookies.splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    cookies_dict[parts[5]] = parts[6]

        response = await self.client.get(search_url, headers=headers, cookies=cookies_dict)
        response.raise_for_status()
        return _parse_yt_html(response.text, limit)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_view_count(views_str: str) -> int:
        try:
            match = re.search(r"[\d,]+", str(views_str).replace(",", ""))
            return int(match.group()) if match else 0
        except Exception:
            return 0

    @staticmethod
    def _parse_duration(duration_str: str) -> int:
        try:
            parts = str(duration_str).split(":")
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            return 0
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------

_scraper_instance: Optional[YouTubeScraper] = None


def get_scraper() -> YouTubeScraper:
    """Return the process-level singleton ``YouTubeScraper``."""
    global _scraper_instance
    if _scraper_instance is None:
        _scraper_instance = YouTubeScraper()
    return _scraper_instance


async def search_youtube(
    keyword: str, limit: int = 20, sort_by: str = "relevance"
) -> List[Dict[str, Any]]:
    """
    Public convenience function for YouTube search.

    Args:
        keyword: Search query.
        limit:   Maximum results (default 20).
        sort_by: ``"relevance"`` (default) or ``"date"``.
    """
    async with get_scraper() as scraper:
        return await scraper.search(keyword, limit, sort_by)
