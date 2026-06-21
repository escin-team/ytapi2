"""Download audio endpoint."""

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import logging

from youtube_search.config import get_settings
from youtube_search.services.audio_downloader import AudioDownloader
from youtube_search.services.cache_manager import CacheManagerService

logger = logging.getLogger(__name__)

# Router dengan prefix /api/v1
router = APIRouter(prefix="/api/v1", tags=["Download"])

settings = get_settings()
audio_downloader = AudioDownloader()
cache_manager = CacheManagerService()


class DownloadResponse(BaseModel):
    """Download response model."""
    video_id: str
    title: str
    duration: int
    download_url: Optional[str] = None
    storage_source: str = "cloudinary"
    storage_account: Optional[str] = None
    cached: bool = False
    file_size: Optional[int] = None


class BatchDownloadRequest(BaseModel):
    """Batch download request model."""
    video_ids: list


class PrefetchRequest(BaseModel):
    """Prefetch request model."""
    video_ids: list


@router.api_route("/download/audio", methods=["GET", "POST"], response_model=DownloadResponse)
async def download_audio(
    video_id: str = Query(..., description="YouTube video ID (11 characters)"),
    format: str = Query("link", description="link or stream")
):
    """
    Download a YouTube video as MP3 audio file.
    
    - Check cache first
    - If not cached, download and upload to Cloudinary
    - Return download URL
    """
    try:
        # Validate video_id
        if len(video_id) != 11:
            raise HTTPException(status_code=400, detail="Invalid video_id (must be 11 characters)")
        
        # Check cache first
        cached_audio = await cache_manager.get_cached_audio(video_id)
        
        if cached_audio:
            logger.info(f"Cache HIT for video: {video_id}")
            return DownloadResponse(
                video_id=video_id,
                title=cached_audio.title,
                duration=cached_audio.duration,
                download_url=cached_audio.file_path,
                storage_source=cached_audio.storage_source,
                storage_account=cached_audio.storage_account,
                cached=True,
                file_size=cached_audio.file_size
            )
        
        # Not cached - download now
        logger.info(f"Cache MISS - downloading video: {video_id}")
        
        result = await audio_downloader.download_and_upload(
            video_id=video_id,
            timeout=settings.download_timeout,
            max_duration=settings.max_video_duration,
            bitrate=settings.audio_bitrate
        )
        
        # Cache the result
        await cache_manager.set_cached_audio(result)
        
        return DownloadResponse(
            video_id=video_id,
            title=result.title,
            duration=result.duration,
            download_url=result.file_path,
            storage_source=result.storage_source,
            storage_account=result.storage_account,
            cached=result.cached,  # True jika hit dari Cloudinary existing
            file_size=result.file_size
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Download failed for {video_id}: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DOWNLOAD_FAILED",
                "message": "Download failed",
                "reason": str(e)
            }
        )


@router.post("/download/batch")
async def batch_download(request: BatchDownloadRequest):
    """Download multiple videos as MP3."""
    if len(request.video_ids) > 20:
        raise HTTPException(status_code=400, detail="Max 20 videos per batch")
    
    results = []
    for video_id in request.video_ids:
        try:
            result = await download_audio(video_id=video_id, format="link")
            results.append(result)
        except Exception as e:
            results.append({"video_id": video_id, "error": str(e)})
    
    return {
        "total": len(request.video_ids),
        "successful": len([r for r in results if "download_url" in r]),
        "failed": len([r for r in results if "error" in r]),
        "items": results
    }


@router.post("/download/prefetch")
async def prefetch_download(request: PrefetchRequest, background_tasks: BackgroundTasks):
    """Pre-download videos to cache."""
    if len(request.video_ids) > 50:
        raise HTTPException(status_code=400, detail="Max 50 videos per prefetch")
    
    # Run in background
    for video_id in request.video_ids:
        background_tasks.add_task(
            audio_downloader.download_and_upload,
            video_id=video_id,
            timeout=settings.download_timeout,
            max_duration=settings.max_video_duration,
            bitrate=settings.audio_bitrate
        )
    
    return {
        "status": "prefetch_started",
        "count": len(request.video_ids),
        "video_ids": request.video_ids
    }