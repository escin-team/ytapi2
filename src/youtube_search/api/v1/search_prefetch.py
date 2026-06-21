"""Search and prefetch endpoint."""

import asyncio
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from typing import List

from youtube_search.services.search import SearchService, get_search_service
from youtube_search.services.audio_downloader import AudioDownloader
from youtube_search.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1", tags=["Search"])


async def prefetch_video(video_id: str):
    """Background task untuk prefetch video."""
    try:
        downloader = AudioDownloader()
        await downloader.download_and_upload(
            video_id=video_id,
            timeout=settings.download_timeout,
            max_duration=settings.max_video_duration,
            bitrate=settings.audio_bitrate
        )
        logger.info(f"✅ Prefetch completed: {video_id}")
    except Exception as e:
        logger.error(f"❌ Prefetch failed for {video_id}: {e}")


@router.post("/search-and-prefetch")
async def search_and_prefetch(
    background_tasks: BackgroundTasks,
    keyword: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(10, ge=1, le=50),
    sort_by: str = Query("relevance")
):
    """Search videos AND auto-prefetch top 2 for instant playback."""
    try:
        service = get_search_service()
        results = await service.search(keyword=keyword, limit=limit, sort_by=sort_by)
        
        # Prefetch top 2 videos di background
        prefetch_queued = []
        for video in results[:2]:
            video_id = video.get("video_id")
            if video_id:
                background_tasks.add_task(prefetch_video, video_id)
                prefetch_queued.append(video_id)
        
        return {
            "search_keyword": keyword,
            "result_count": len(results),
            "videos": results,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "prefetch_queued": prefetch_queued,
            "prefetch_count": len(prefetch_queued)
        }
        
    except Exception as e:
        logger.error(f"Search and prefetch failed: {e}")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "YOUTUBE_UNAVAILABLE",
                "message": "YouTube service temporarily unavailable",
                "reason": str(e),
                "status": 503
            }
        )