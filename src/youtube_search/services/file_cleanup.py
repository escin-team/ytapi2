"""檔案清理服務 - 管理已過期的下載音檔。

本服務負責：
1. 掃描下載目錄找出孤立檔案
2. 刪除不在快取索引中的檔案
3. 定期清理過期的快取記錄
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

try:
    from redis import Redis
    REDIS_AVAILABLE = True
except ImportError:
    Redis = None  # type: ignore[assignment,misc]
    REDIS_AVAILABLE = False

from youtube_search.config import get_settings
from youtube_search.services.cache_manager import CacheManagerService

logger = logging.getLogger(__name__)


class FileCleanupService:
    """管理已下載音檔的清理與維護。"""

    def __init__(
        self,
        cache_manager: Optional[CacheManagerService] = None,
        redis_client=None,
    ):
        """
        初始化檔案清理服務。

        Args:
            cache_manager: 快取管理器實例
            redis_client: Redis 客戶端
        """
        self.config = get_settings()
        self.download_dir = Path(self.config.download_dir)

        if not self.download_dir.exists():
            self.download_dir.mkdir(parents=True, exist_ok=True)

        # 初始化或使用提供的快取管理器
        if cache_manager:
            self.cache_manager = cache_manager
        else:
            if redis_client:
                self.cache_manager = CacheManagerService(redis_client)
            else:
                self.cache_manager = CacheManagerService()

        logger.info(f"檔案清理服務已初始化，目錄: {self.download_dir}")

    async def scan_orphaned_files(self) -> list[Path]:
        """
        掃描下載目錄找出孤立檔案（不在快取中的檔案）。

        Returns:
            list[Path]: 孤立檔案路徑清單
        """
        logger.info("開始掃描孤立檔案...")

        orphaned_files = []
        cached_video_ids = await self.cache_manager.get_all_cached_video_ids()

        try:
            for file_path in self.download_dir.glob("*.mp3"):
                # 從檔名中提取影片 ID（格式: video_id_title.mp3）
                file_name = file_path.name
                parts = file_name.split("_", 1)

                if len(parts) < 1:
                    orphaned_files.append(file_path)
                    continue

                video_id = parts[0]

                # 檢查影片 ID 是否在快取中
                if video_id not in cached_video_ids:
                    orphaned_files.append(file_path)
                    logger.debug(f"發現孤立檔案: {file_path}")

            logger.info(f"掃描完成，發現 {len(orphaned_files)} 個孤立檔案")
            return orphaned_files

        except Exception as e:
            logger.error(f"掃描孤立檔案時出錯: {str(e)}")
            return []

    async def delete_expired_files(self) -> int:
        """
        刪除不在 Redis 索引中的檔案。

        Returns:
            int: 刪除的檔案數量
        """
        logger.info("開始刪除過期檔案...")

        deleted_count = 0
        orphaned_files = await self.scan_orphaned_files()

        for file_path in orphaned_files:
            try:
                file_path.unlink()
                deleted_count += 1
                logger.info(f"已刪除過期檔案: {file_path.name}")
            except Exception as e:
                logger.error(f"刪除檔案 {file_path} 時出錯: {str(e)}")

        logger.info(f"刪除完成，共刪除 {deleted_count} 個檔案")
        return deleted_count

    async def cleanup_task(self) -> dict[str, int]:
        """
        執行完整的清理任務。

        包含：
        1. 掃描孤立檔案
        2. 刪除已過期的檔案
        3. 清理過期的快取記錄

        Returns:
            dict: 清理統計資訊
                {
                    "scanned": 掃描的檔案數,
                    "deleted": 刪除的檔案數,
                    "cache_cleaned": 清理的快取項數
                }
        """
        logger.info("=" * 50)
        logger.info("開始執行完整清理任務...")
        logger.info("=" * 50)

        try:
            # 掃描孤立檔案
            orphaned = await self.scan_orphaned_files()
            scanned_count = len(orphaned)

            # 刪除過期檔案
            deleted_count = await self.delete_expired_files()

            # 清理過期快取
            cache_cleaned = await self.cache_manager.cleanup_expired_cache()

            result = {
                "scanned": scanned_count,
                "deleted": deleted_count,
                "cache_cleaned": cache_cleaned,
            }

            logger.info("=" * 50)
            logger.info("清理任務完成:")
            logger.info(f"  掃描檔案: {scanned_count}")
            logger.info(f"  刪除檔案: {deleted_count}")
            logger.info(f"  清理快取: {cache_cleaned}")
            logger.info("=" * 50)

            return result

        except Exception as e:
            logger.exception(f"清理任務執行失敗: {str(e)}")
            return {
                "scanned": 0,
                "deleted": 0,
                "cache_cleaned": 0,
            }

    def get_directory_stats(self) -> dict[str, int | float]:
        """
        獲取下載目錄統計資訊。

        Returns:
            dict: 目錄統計資訊
                {
                    "total_files": 總檔案數,
                    "total_size_mb": 總大小（MB）,
                    "oldest_file_hours": 最舊檔案年齡（小時）
                }
        """
        try:
            import time

            total_files = 0
            total_size = 0
            oldest_mtime = None

            for file_path in self.download_dir.glob("*.mp3"):
                total_files += 1
                total_size += file_path.stat().st_size

                mtime = file_path.stat().st_mtime
                if oldest_mtime is None or mtime < oldest_mtime:
                    oldest_mtime = mtime

            total_size_mb = total_size / (1024 * 1024)
            oldest_hours = 0
            if oldest_mtime:
                oldest_hours = (time.time() - oldest_mtime) / 3600

            return {
                "total_files": total_files,
                "total_size_mb": round(total_size_mb, 2),
                "oldest_file_hours": round(oldest_hours, 2),
            }

        except Exception as e:
            logger.error(f"獲取目錄統計資訊時出錯: {str(e)}")
            return {
                "total_files": 0,
                "total_size_mb": 0,
                "oldest_file_hours": 0,
            }
