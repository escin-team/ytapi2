"""Metadata normalization service for consistent data formatting."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from youtube_search.models.playlist import Track
from youtube_search.models.video import Video
from youtube_search.utils.logger import get_logger

logger = get_logger(__name__)


class MetadataNormalizer:
    """Normalize and clean video metadata for consistency."""

    @staticmethod
    def normalize_video(video: Video) -> Video:
        """Apply normalization rules to a Video instance."""

        # Normalize publish_date from relative text to ISO 8601 if possible
        normalized_date = MetadataNormalizer._normalize_publish_date(video.publish_date)

        # Clean and truncate fields to ensure they fit schema constraints
        normalized_title = MetadataNormalizer._clean_text(video.title, max_length=500)
        normalized_channel = MetadataNormalizer._clean_text(
            video.channel, max_length=200
        )
        normalized_description = MetadataNormalizer._clean_text(
            video.description, max_length=5000
        )

        return Video(
            video_id=video.video_id,
            title=normalized_title,
            url=video.url,
            channel=normalized_channel,
            channel_url=video.channel_url,
            publish_date=normalized_date,
            view_count=video.view_count,
            description=normalized_description,
        )

    @staticmethod
    def normalize_track(track: Track) -> Track:
        """Apply normalization rules to a Track instance.

        Ensures all fields conform to schema constraints and removes invalid data.
        
        Raises:
            ValueError: If video_id format is invalid (Pydantic will enforce this)
        """
        # Clean text fields
        normalized_title = MetadataNormalizer._clean_text(track.title, max_length=500)
        normalized_channel = MetadataNormalizer._clean_text(track.channel, max_length=200)

        # Reconstruct URL if needed (video_id validation is handled by Pydantic)
        url = track.url or Track.build_url(track.video_id)

        # Keep publish_date and duration as-is (preserve original format from YouTube)
        # These are often relative times that should not be normalized

        return Track(
            video_id=track.video_id,
            title=normalized_title or track.title,  # Fallback to original if empty after cleaning
            channel=normalized_channel,
            channel_url=track.channel_url,
            url=url,
            publish_date=track.publish_date,
            duration=track.duration,
            view_count=track.view_count,
            position=track.position,
        )

    @staticmethod
    def _normalize_publish_date(relative_text: Optional[str]) -> Optional[str]:
        """Convert relative time text to ISO 8601 timestamp (best effort)."""

        if not relative_text:
            return None

        # Parse patterns like "2 days ago", "3 weeks ago", "1 month ago", "1 year ago"
        match = re.search(
            r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago",
            relative_text,
            re.I,
        )
        if not match:
            # If no match, return None (cannot parse)
            return None

        amount = int(match.group(1))
        unit = match.group(2).lower()

        # Calculate approximate timestamp
        now = datetime.now(timezone.utc)
        delta_map = {
            "second": timedelta(seconds=amount),
            "minute": timedelta(minutes=amount),
            "hour": timedelta(hours=amount),
            "day": timedelta(days=amount),
            "week": timedelta(weeks=amount),
            "month": timedelta(days=amount * 30),  # Approximate
            "year": timedelta(days=amount * 365),  # Approximate
        }
        delta = delta_map.get(unit)
        if not delta:
            return None

        estimated_date = now - delta
        return estimated_date.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _clean_text(text: Optional[str], max_length: int) -> Optional[str]:
        """Strip whitespace and truncate text to max_length."""

        if text is None:
            return None
        cleaned = text.strip()
        if not cleaned:
            return None
        if len(cleaned) > max_length:
            return cleaned[:max_length]
        return cleaned


def get_normalizer() -> MetadataNormalizer:
    """Return normalizer instance."""

    return MetadataNormalizer()
