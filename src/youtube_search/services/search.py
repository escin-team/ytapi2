"""Search service dengan fix coroutine."""

import logging
from typing import List, Dict, Any
from youtube_search.services.scraper import YouTubeScraper, get_scraper

logger = logging.getLogger(__name__)


class SearchService:
    """Service untuk search YouTube."""
    
    def __init__(self):
        self.scraper = get_scraper()
    
    async def search(self, keyword: str, limit: int = 20, sort_by: str = "relevance") -> List[Dict[str, Any]]:
        """
        Search YouTube videos.
        
        Args:
            keyword: Search query
            limit: Max results
            sort_by: "relevance" or "date"
        
        Returns:
            List of video dicts
        """
        logger.info(f"Searching YouTube: keyword='{keyword}', limit={limit}, sort_by={sort_by}")
        
        # ⭐ PENTING: gunakan async with untuk context manager
        async with self.scraper as scraper:
            results = await scraper.search(keyword=keyword, limit=limit, sort_by=sort_by)
        
        logger.info(f"Search completed: {len(results)} results")
        return results


# Singleton
_search_service: SearchService = None


def get_search_service() -> SearchService:
    """Get singleton SearchService instance."""
    global _search_service
    if _search_service is None:
        _search_service = SearchService()
    return _search_service