"""Pydantic models for playlist metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _iso_timestamp() -> str:
    """Build ISO 8601 UTC timestamp with second precision."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Track(BaseModel):
    """Track metadata contained in a playlist."""

    model_config = {
        "populate_by_name": True,
        "str_strip_whitespace": True,
    }

    video_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{11}$", description="YouTube 影片 ID")
    title: str = Field(..., min_length=1, max_length=500, description="歌曲/影片標題")
    channel: Optional[str] = Field(default=None, max_length=200, description="頻道或藝人名稱")
    channel_url: Optional[str] = Field(default=None, description="頻道連結")
    url: str = Field(..., description="影片 URL")
    publish_date: Optional[str] = Field(
        default=None, description="發布時間（相對時間字串，保持原樣）"
    )
    duration: Optional[str] = Field(default=None, description="影片長度（如 3:45），若無則為 None")
    view_count: Optional[int] = Field(default=None, ge=0, description="觀看次數")
    position: Optional[int] = Field(default=None, ge=1, description="在播放列表中的排序序號")

    @field_validator("video_id")
    @classmethod
    def validate_video_id(cls, value: str) -> str:
        """Ensure video_id is not empty after stripping."""

        if not value:
            raise ValueError("video_id 不可為空")
        return value

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        """Ensure title is not blank."""

        if not value.strip():
            raise ValueError("title 不可為空白")
        return value

    @classmethod
    def build_url(cls, video_id: str) -> str:
        """Helper to construct a standard watch URL."""

        return f"https://www.youtube.com/watch?v={video_id}"


class Playlist(BaseModel):
    """Playlist metadata and aggregated tracks."""

    model_config = {
        "populate_by_name": True,
        "str_strip_whitespace": True,
    }

    playlist_id: str = Field(
        ...,
        min_length=6,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="播放列表 ID（由 URL list 參數解析）",
    )
    url: str = Field(..., description="原始 playlist URL，需包含 list 參數")
    title: Optional[str] = Field(default=None, max_length=500, description="播放列表標題")
    video_count: Optional[int] = Field(
        default=None, ge=0, description="預估曲目數；若缺失則以 tracks 長度為準"
    )
    partial: bool = Field(..., description="若因超時/缺 token 僅回傳部分曲目，則為 true")
    fetched_at: Optional[str] = Field(
        default_factory=_iso_timestamp,
        description="爬取完成時間（ISO 8601 UTC，秒級精度）",
    )
    tracks: list[Track] = Field(default_factory=list, description="曲目清單")

    @field_validator("playlist_id")
    @classmethod
    def validate_playlist_id(cls, value: str) -> str:
        """Ensure playlist_id is not empty."""

        if not value:
            raise ValueError("playlist_id 不可為空")
        return value

    @field_validator("fetched_at")
    @classmethod
    def validate_fetched_at(cls, value: Optional[str]) -> Optional[str]:
        """Validate ISO 8601 timestamp format if provided."""

        if value is None:
            return value
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            raise ValueError("fetched_at 必須為 ISO 8601 格式")
        return value

    @model_validator(mode="after")
    def adjust_video_count(self) -> "Playlist":
        """Ensure video_count fallback aligns with tracks length when missing."""

        if self.video_count is None:
            self.video_count = len(self.tracks)
        return self
