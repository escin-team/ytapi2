"""Playlist coordination service for URL validation and metadata retrieval."""

from __future__ import annotations

from typing import Optional

import anyio

from youtube_search.models.playlist import Playlist
from youtube_search.services.cache import CacheService, get_cache_service
from youtube_search.services.normalizer import MetadataNormalizer, get_normalizer
from youtube_search.services.playlist_scraper import (
    PlaylistScraper,
    get_playlist_scraper,
)
from youtube_search.utils.logger import get_logger
from youtube_search.utils.validators import extract_playlist_id_from_url

logger = get_logger(__name__)


class PlaylistService:
    """Orchestrates playlist operations: URL parsing, validation, scraping, and retrieval of full playlist metadata including all tracks.

    This service validates playlist URLs, scrapes playlist data, normalizes track metadata, and returns a Playlist model containing complete metadata and all tracks.
    """

    def __init__(
        self,
        scraper: Optional[PlaylistScraper] = None,
        normalizer: Optional[MetadataNormalizer] = None,
        cache: Optional[CacheService] = None,
    ) -> None:
        self.scraper = scraper or get_playlist_scraper()
        self.normalizer = normalizer or get_normalizer()
        self.cache = cache or get_cache_service()

    def validate_and_parse_url(self, playlist_url: str) -> str:
        """Validate playlist URL and extract playlist_id.

        Raises:
            InvalidParameterError: If URL format is invalid
            MissingParameterError: If required parameters are missing
        """
        return extract_playlist_id_from_url(playlist_url)

    async def get_playlist_metadata(
        self, playlist_url: str, force_refresh: bool = False
    ) -> Playlist:
        """Retrieve full playlist metadata with all tracks (US2+).

        This method fetches and normalizes all tracks from the playlist.

        Args:
            playlist_url: YouTube playlist URL containing list parameter
            force_refresh: Force fetch from YouTube (skip cache)

        Returns:
            Playlist model with full metadata and tracks

        Raises:
            InvalidParameterError: If URL is invalid (400)
            PlaylistNotFoundError: If playlist does not exist (404)
            PlaylistForbiddenError: If playlist is private/restricted (403)
            PlaylistGoneError: If playlist is deleted (410)
            PlaylistScrapingError: If scraping fails (502)
        """
        # Parse and validate URL
        playlist_id = self.validate_and_parse_url(playlist_url)

        logger.info(f"Processing playlist_id: {playlist_id}")

        # Check cache (unless force_refresh)
        if not force_refresh:
            cached_playlist = self.cache.get(f"playlist:{playlist_id}", model_class=Playlist)
            if cached_playlist:
                logger.debug(f"Cache hit for playlist_id: {playlist_id}")
                return cached_playlist

        # Scrape playlist tracks (runs in thread pool)
        logger.debug(f"Scraping playlist: {playlist_url}")
        tracks_raw, partial, scrape_metadata = await anyio.to_thread.run_sync(
            self.scraper.fetch_playlist, playlist_url
        )

        # Normalize tracks
        normalized_tracks = [self.normalizer.normalize_track(track) for track in tracks_raw]

        # Build Playlist model
        playlist = Playlist(
            playlist_id=playlist_id,
            url=playlist_url,
            title=scrape_metadata.get("title"),
            video_count=scrape_metadata.get("video_count"),
            partial=partial,
            tracks=normalized_tracks,
        )

        # Cache only complete playlists (partial=false)
        if not partial and len(normalized_tracks) > 0:
            logger.debug(
                f"Caching complete playlist: {playlist_id} with {len(normalized_tracks)} tracks"
            )
            self.cache.set(f"playlist:{playlist_id}", playlist)
        elif partial:
            logger.warning(
                f"Skipping cache for partial playlist: {playlist_id} "
                f"(reason: {scrape_metadata.get('partial_reason')})"
            )

        logger.info(
            f"Playlist fetch complete: {playlist_id}, tracks={len(normalized_tracks)}, "
            f"partial={partial}",
            extra={
                "playlist_id": playlist_id,
                "track_count": len(normalized_tracks),
                "partial": partial,
                "partial_reason": scrape_metadata.get("partial_reason"),
                "elapsed_seconds": scrape_metadata.get("elapsed_seconds"),
                "continuation_batches": scrape_metadata.get("continuation_batches"),
            },
        )

        return playlist


_service: Optional[PlaylistService] = None


def get_playlist_service() -> PlaylistService:
    """Provide a singleton-ish PlaylistService instance."""
    global _service
    if _service is None:
        _service = PlaylistService()
    return _service
