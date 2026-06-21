"""Input validation utilities for API parameters."""

from __future__ import annotations

import re
from typing import Literal, Optional
from urllib.parse import parse_qs, urlparse

from youtube_search.utils.errors import InvalidParameterError, MissingParameterError

_PLAYLIST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{6,50}$")
_PLAYLIST_ALLOWED_DOMAINS = {
    "www.youtube.com",
    "youtube.com",
    "m.youtube.com",
    "youtu.be",
}


def validate_keyword(keyword: Optional[str]) -> str:
    """Validate keyword presence and length constraints (1-200 chars)."""

    if keyword is None or keyword.strip() == "":
        raise MissingParameterError("keyword 為必須參數")
    value = keyword.strip()
    if not (1 <= len(value) <= 200):
        raise InvalidParameterError(
            "keyword 長度必須介於 1-200 字元", "INVALID_KEYWORD_LENGTH"
        )
    return value


def validate_limit(limit: Optional[int]) -> int:
    """Validate result limit within 1-100 inclusive; default to 1 when None."""

    if limit is None:
        return 1
    if not (1 <= limit <= 100):
        raise InvalidParameterError("limit 必須在 1-100 之間", "INVALID_LIMIT")
    return limit


def validate_sort_by(sort_by: Optional[str]) -> Literal["relevance", "date"]:
    """Validate sort_by field; defaults to relevance."""

    if sort_by is None:
        return "relevance"
    value = sort_by.strip().lower()
    if value not in {"relevance", "date"}:
        raise InvalidParameterError(
            "sort_by 僅支援 relevance 或 date", "INVALID_SORT_BY"
        )
    return value  # type: ignore[return-value]


def validate_playlist_id(playlist_id: Optional[str]) -> str:
    """Validate playlist_id length and character set."""

    if playlist_id is None or playlist_id.strip() == "":
        raise MissingParameterError("playlist_id 為必須參數")

    value = playlist_id.strip()
    if not _PLAYLIST_ID_PATTERN.fullmatch(value):
        raise InvalidParameterError(
            "playlist_id 僅允許英數及 _ -，長度 6-50", "INVALID_PLAYLIST_ID"
        )
    return value


def extract_playlist_id_from_url(playlist_url: Optional[str]) -> str:
    """Extract and validate playlist_id from a YouTube playlist URL."""

    if playlist_url is None or playlist_url.strip() == "":
        raise MissingParameterError("playlist_url 為必須參數")

    normalized_url = playlist_url.strip()
    parsed = urlparse(normalized_url)

    if parsed.scheme not in {"http", "https"}:
        raise InvalidParameterError(
            "playlist_url 必須為 http 或 https", "INVALID_PLAYLIST_URL_SCHEME"
        )

    domain = parsed.netloc.lower()
    if domain not in _PLAYLIST_ALLOWED_DOMAINS:
        raise InvalidParameterError(
            "playlist_url 僅支援 youtube.com 或 youtu.be 網域",
            "INVALID_PLAYLIST_DOMAIN",
        )

    query_params = parse_qs(parsed.query)
    playlist_id = query_params.get("list", [None])[0]
    if playlist_id is None:
        raise InvalidParameterError("playlist_url 缺少 list 參數", "PLAYLIST_ID_NOT_FOUND")

    return validate_playlist_id(playlist_id)


# Audio Download Feature (Feature 004) - Download-related validators


def validate_video_id(video_id: Optional[str]) -> str:
    """
    驗證 YouTube 影片 ID 格式。

    Args:
        video_id: YouTube 影片 ID

    Returns:
        str: 驗證後的影片 ID

    Raises:
        MissingParameterError: 影片 ID 缺失
        InvalidParameterError: 影片 ID 格式無效
    """
    if video_id is None or video_id.strip() == "":
        raise MissingParameterError("video_id 為必須參數")

    value = video_id.strip()

    # YouTube 影片 ID 為 11 個字元，由英數、連字符和底線組成
    if not re.match(r"^[a-zA-Z0-9_-]{11}$", value):
        raise InvalidParameterError(
            "video_id 格式無效（應為 11 個英數字符），",
            "INVALID_VIDEO_ID",
        )

    return value


def validate_duration(duration: Optional[int], max_duration: int) -> bool:
    """
    驗證影片長度是否在允許範圍內。

    Args:
        duration: 影片長度（秒）
        max_duration: 最大允許長度（秒）

    Returns:
        bool: 長度是否有效

    Raises:
        InvalidParameterError: 長度超過限制
    """
    if duration is None:
        return True

    if duration <= 0:
        raise InvalidParameterError(
            "影片長度必須大於 0",
            "INVALID_DURATION",
        )

    if duration > max_duration:
        raise InvalidParameterError(
            f"影片長度超過限制（{duration}秒 > {max_duration}秒）",
            "DURATION_EXCEEDED",
        )

    return True


def sanitize_filename(filename: str) -> str:
    """
    清理檔名中的特殊字元。

    移除不安全的文件系統字符，保留字母、數字、中文、連字符和底線。

    Args:
        filename: 原始檔名

    Returns:
        str: 清理後的檔名
    """
    # 移除特殊字元，保留字母、數字、中文、連字符和底線
    filename = re.sub(r'[<>:"/\\|?*]', "", filename)
    filename = re.sub(r"\s+", "_", filename)
    # 移除首尾的點和連字符
    filename = filename.strip(".-")
    # 限制長度（檔案系統限制）
    max_length = 200
    return filename[:max_length]


def generate_download_url(base_url: str, video_id: str, filename: str) -> str:
    """
    生成公開下載連結。

    Args:
        base_url: 基礎 URL（例如 http://localhost:8000/downloads）
        video_id: 影片 ID（可為空，用於 ZIP 檔案）
        filename: 檔名（可包含副檔名如 .zip、.mp3）

    Returns:
        str: 完整下載連結

    Examples:
        - generate_download_url("http://localhost:8000/downloads", "abc123", "My Song.mp3")
          -> "http://localhost:8000/downloads/abc123_My_Song.mp3"
        - generate_download_url("http://localhost:8000/downloads", "", "batch_2023.zip")
          -> "http://localhost:8000/downloads/batch_2023.zip"
    """
    base_url = base_url.rstrip("/")
    safe_filename = sanitize_filename(filename)

    # 如果 video_id 為空，直接返回 URL
    if not video_id:
        return f"{base_url}/{safe_filename}"

    # 檢查 filename 是否已包含副檔名
    if "." in safe_filename:
        # 已有副檔名，直接組合
        return f"{base_url}/{video_id}_{safe_filename}"
    else:
        # 沒有副檔名，預設為 .mp3
        return f"{base_url}/{video_id}_{safe_filename}.mp3"
