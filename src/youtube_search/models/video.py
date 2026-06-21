"""Pydantic models for YouTube video metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Video(BaseModel):
    """YouTube 影片元數據模型。"""

    model_config = {
        "populate_by_name": True,
        "str_strip_whitespace": True,
    }

    video_id: str = Field(
        ..., pattern=r"^[a-zA-Z0-9_-]{11}$", description="YouTube 影片 ID"
    )
    title: Optional[str] = Field(
        default=None, max_length=500, description="影片標題（可為空）"
    )
    url: Optional[str] = Field(default=None, description="影片 URL")
    channel: Optional[str] = Field(
        default=None, max_length=200, description="頻道名稱（可為空）"
    )
    channel_url: Optional[str] = Field(default=None, description="頻道 URL")
    publish_date: Optional[str] = Field(
        default=None, description="發佈日期（ISO 8601，秒級精度）"
    )
    view_count: Optional[int] = Field(
        default=None, ge=0, description="觀看次數（非負整數，可為空）"
    )
    description: Optional[str] = Field(
        default=None, max_length=5000, description="影片描述（可為空）"
    )

    @field_validator("publish_date")
    @classmethod
    def validate_publish_date(cls, value: Optional[str]) -> Optional[str]:
        """Validate ISO 8601 (RFC 3339) timestamp if provided."""

        if value is None:
            return value
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            raise ValueError("publish_date 必須為 ISO 8601 格式")
        return value

    @field_validator("video_id")
    @classmethod
    def validate_video_id(cls, value: str) -> str:
        """Ensure video_id is not empty after stripping."""

        if not value:
            raise ValueError("video_id 不可為空")
        return value

    @classmethod
    def build_url(cls, video_id: str) -> str:
        """Helper to construct a standard watch URL."""

        return f"https://www.youtube.com/watch?v={video_id}"

    @classmethod
    def build_timestamp(cls) -> str:
        """Build ISO 8601 UTC timestamp with second precision."""

        return (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
