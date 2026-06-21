"""Sorting utilities for video results."""

from __future__ import annotations

from typing import List, Literal

from youtube_search.models.video import Video


class VideoSorter:
    """Sort video results by various criteria."""

    @staticmethod
    def sort(videos: List[Video], sort_by: Literal["relevance", "date"]) -> List[Video]:
        """Sort videos according to specified strategy."""

        if sort_by == "relevance":
            # YouTube's default order is already by relevance
            return videos
        elif sort_by == "date":
            return VideoSorter._sort_by_date(videos)
        return videos

    @staticmethod
    def _sort_by_date(videos: List[Video]) -> List[Video]:
        """Sort by publish_date descending; videos without dates go to end."""

        # Separate videos with and without dates
        with_dates = [v for v in videos if v.publish_date]
        without_dates = [v for v in videos if not v.publish_date]

        # Sort videos with dates in descending order (newest first)
        with_dates.sort(key=lambda v: v.publish_date, reverse=True)

        # Return videos with dates first, then those without
        return with_dates + without_dates


def get_sorter() -> VideoSorter:
    """Return sorter instance."""

    return VideoSorter()
