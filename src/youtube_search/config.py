"""Configuration management for YouTube Music API."""

from pydantic_settings import BaseSettings
from pydantic import Field
import json
import logging
from typing import List, Dict, Any
from functools import lru_cache

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings with sensible defaults."""
    
    # YouTube Crawler
    youtube_base_url: str = Field(default="https://www.youtube.com/results", env="YOUTUBE_BASE_URL")
    youtube_timeout: int = Field(default=30, env="YOUTUBE_TIMEOUT")  # ⭐ Naikkan timeout
    
    # Redis Cache
    redis_host: str = Field(default="localhost", env="REDIS_HOST")
    redis_port: int = Field(default=6379, env="REDIS_PORT")
    redis_db: int = Field(default=3, env="REDIS_DB")
    redis_password: str = Field(default="", env="REDIS_PASSWORD")
    redis_enabled: bool = Field(default=False, env="REDIS_ENABLED")
    redis_ttl_seconds: int = Field(default=3600, env="REDIS_TTL_SECONDS")
    
    # API Server
    api_host: str = Field(default="0.0.0.0", env="API_HOST")
    api_port: int = Field(default=7860, env="API_PORT")
    api_log_level: str = Field(default="info", env="API_LOG_LEVEL")
    
    # Logging
    log_dir: str = Field(default="logs", env="LOG_DIR")
    log_file_enabled: bool = Field(default=True, env="LOG_FILE_ENABLED")
    log_file_max_bytes: int = Field(default=10485760, env="LOG_FILE_MAX_BYTES")
    log_file_backup_count: int = Field(default=5, env="LOG_FILE_BACKUP_COUNT")
    
    # MCP
    mcp_search_timeout: int = Field(default=15, env="MCP_SEARCH_TIMEOUT")
    mcp_search_retries: int = Field(default=3, env="MCP_SEARCH_RETRIES")
    
    # Feature Flags
    enable_cache: bool = Field(default=True, env="ENABLE_CACHE")
    enable_logging: bool = Field(default=True, env="ENABLE_LOGGING")
    
    # ⭐ Audio Download Configuration (LENGKAP)
    download_dir: str = Field(default="/tmp/youtube_audio", env="DOWNLOAD_DIR")
    download_base_url: str = Field(default="http://localhost:7860/downloads", env="DOWNLOAD_BASE_URL")
    download_timeout: int = Field(default=300, env="DOWNLOAD_TIMEOUT")  # ⭐ INI YANG HILANG!
    max_video_duration: int = Field(default=600, env="MAX_VIDEO_DURATION")
    audio_bitrate: int = Field(default=128, env="AUDIO_BITRATE")
    cache_ttl_hours: int = Field(default=24, env="CACHE_TTL_HOURS")
    
    # Cloudinary
    cloudinary_accounts_json: str = Field(default="[]", env="CLOUDINARY_ACCOUNTS_JSON")
    
    # Rate Limiting
    rate_limit_download_per_hour: int = Field(default=20, env="RATE_LIMIT_DOWNLOAD_PER_HOUR")
    rate_limit_static_per_minute: int = Field(default=60, env="RATE_LIMIT_STATIC_PER_MINUTE")
    rate_limit_enabled: bool = Field(default=True, env="RATE_LIMIT_ENABLED")
    
    @property
    def cache_ttl_seconds(self) -> int:
        return self.cache_ttl_hours * 3600
    
    @property
    def cloudinary_accounts(self) -> List[Dict[str, Any]]:
        try:
            accounts = json.loads(self.cloudinary_accounts_json)
            if isinstance(accounts, list) and len(accounts) > 0:
                logger.info(f"Loaded {len(accounts)} Cloudinary account(s) for failover.")
                return accounts
        except Exception as e:
            logger.error(f"Failed to parse CLOUDINARY_ACCOUNTS_JSON: {e}")
        return []
    
    def validate(self) -> bool:
        if not self.cloudinary_accounts:
            logger.warning("No Cloudinary accounts configured")
        return True
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    settings.validate()
    return settings