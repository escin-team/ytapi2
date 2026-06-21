"""Pydantic model for search result payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from pydantic import BaseModel, Field, model_validator

from youtube_search.models.video import Video


class SearchResult(BaseModel):
    """一次搜尋結果的聚合模型。"""

    model_config = {
        "populate_by_name": True,
        "str_strip_whitespace": True,
    }

    search_keyword: str = Field(
        ..., min_length=1, max_length=200, description="搜尋關鍵字"
    )
    result_count: int = Field(
        ..., ge=0, le=100, description="返回影片數量，需與 videos 長度一致"
    )
    videos: List[Video] = Field(default_factory=list, description="影片清單")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        description="搜尋時間戳記（ISO 8601 UTC，秒級精度）",
    )

    @model_validator(mode="after")
    def validate_result_count(self) -> "SearchResult":
        """Ensure result_count matches provided videos length."""
        if self.result_count != len(self.videos):
            raise ValueError("result_count 必須等於 videos 長度")
        return self
