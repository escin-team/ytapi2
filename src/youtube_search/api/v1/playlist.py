"""API route definitions for playlist metadata endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from youtube_search.models.playlist import Playlist
from youtube_search.services.playlist import PlaylistService, get_playlist_service
from youtube_search.utils.errors import AppError
from youtube_search.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["playlist"])


@router.get(
    "/playlist/metadata",
    response_model=Playlist,
    summary="取得播放列表元數據",
    response_model_exclude_none=True,
    responses={
        200: {
            "description": "成功返回播放列表元數據",
            "content": {
                "application/json": {
                    "example": {
                        "playlist_id": "PLtest123",
                        "url": "https://www.youtube.com/playlist?list=PLtest123",
                        "title": "Learning Python",
                        "video_count": 50,
                        "partial": False,
                        "fetched_at": "2025-12-08T10:30:45Z",
                        "tracks": [
                            {
                                "video_id": "dQw4w9WgXcQ",
                                "title": "Python Basics",
                                "channel": "Tech Academy",
                                "channel_url": "https://www.youtube.com/channel/UCxxxxx",
                                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                                "publish_date": "2 years ago",
                                "duration": "45:30",
                                "view_count": 1000000,
                                "position": 1,
                            }
                        ],
                    }
                }
            },
        },
        400: {
            "description": "無效的播放列表 URL 或缺少必要參數",
            "content": {
                "application/json": {
                    "example": {
                        "code": "INVALID_PARAMETER",
                        "message": "playlist_url 缺少 list 參數",
                        "reason": None,
                        "trace_id": "550e8400-e29b-41d4-a716-446655440000",
                        "playlist_id": None,
                        "status": 400,
                    }
                }
            },
        },
        403: {
            "description": "播放列表私密或無法存取",
            "content": {
                "application/json": {
                    "example": {
                        "code": "PLAYLIST_FORBIDDEN",
                        "message": "播放列表私密或無法存取",
                        "trace_id": "550e8400-e29b-41d4-a716-446655440001",
                        "status": 403,
                    }
                }
            },
        },
        404: {
            "description": "播放列表不存在",
            "content": {
                "application/json": {
                    "example": {
                        "code": "PLAYLIST_NOT_FOUND",
                        "message": "播放列表不存在",
                        "trace_id": "550e8400-e29b-41d4-a716-446655440002",
                        "status": 404,
                    }
                }
            },
        },
        410: {
            "description": "播放列表已刪除",
            "content": {
                "application/json": {
                    "example": {
                        "code": "PLAYLIST_GONE",
                        "message": "播放列表已刪除",
                        "trace_id": "550e8400-e29b-41d4-a716-446655440003",
                        "status": 410,
                    }
                }
            },
        },
        502: {
            "description": "無法從 YouTube 提取播放列表資料",
            "content": {
                "application/json": {
                    "example": {
                        "code": "PLAYLIST_SCRAPING_ERROR",
                        "message": "無法從 YouTube 提取播放列表資料",
                        "reason": "ytInitialData 提取失敗",
                        "trace_id": "550e8400-e29b-41d4-a716-446655440004",
                        "status": 502,
                    }
                }
            },
        },
    },
)
async def get_playlist_metadata(
    playlist_url: str = Query(
        ..., min_length=10, description="YouTube 播放列表 URL（需包含 list 參數）"
    ),
    force_refresh: bool = Query(False, description="是否強制重新取得（略過快取）"),
    service: PlaylistService = Depends(get_playlist_service),
) -> Playlist:
    """Handle GET /api/v1/playlist/metadata requests.

    Retrieves complete playlist metadata including all tracks by scraping YouTube.

    Args:
        playlist_url: YouTube playlist URL containing list parameter
        force_refresh: Force refresh from YouTube (skip cache)
        service: PlaylistService dependency

    Returns:
        Playlist model with complete metadata and all tracks

    Error Responses:
        400: Invalid URL format (missing list parameter, invalid domain)
        403: Playlist is private or restricted
        404: Playlist not found or does not exist
        410: Playlist has been deleted
        502: Error scraping from YouTube
    """
    try:
        logger.debug(
            f"GET /playlist/metadata: playlist_url={playlist_url}, force_refresh={force_refresh}"
        )
        return await service.get_playlist_metadata(
            playlist_url=playlist_url, force_refresh=force_refresh
        )
    except AppError as exc:
        error_payload = exc.to_response()
        logger.warning(
            f"AppError: {exc.error_code} - {str(exc)}",
            extra={"error_code": exc.error_code, "trace_id": error_payload.get("trace_id")},
        )
        return JSONResponse(status_code=exc.status_code, content=error_payload)
    except Exception as exc:  # pragma: no cover - unexpected failures
        logger.error(f"Unexpected error in /playlist/metadata: {str(exc)}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "內部服務錯誤"}) from exc
