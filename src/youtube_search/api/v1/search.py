"""Search endpoint dengan fix coroutine await."""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging
from datetime import datetime

from youtube_search.services.search import SearchService, get_search_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Search"])


@router.get("/search")
async def search_videos(
    keyword: str = Query(..., min_length=1, max_length=200, description="Search keyword"),
    limit: int = Query(20, ge=1, le=100, description="Number of results"),
    sort_by: str = Query("relevance", description="Sort by: relevance or date")
):
    """Search YouTube videos by keyword."""
    try:
        service = get_search_service()
        
        # ⭐ PENTING: await async method dengan benar
        results = await service.search(keyword=keyword, limit=limit, sort_by=sort_by)
        
        return {
            "search_keyword": keyword,
            "result_count": len(results),
            "videos": results,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "YOUTUBE_UNAVAILABLE",
                "message": "YouTube search service temporarily unavailable",
                "reason": str(e),
                "status": 503
            }
        )