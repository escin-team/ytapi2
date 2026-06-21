"""Structured logging helpers."""

import logging
import time
from typing import Optional

from youtube_search.config import get_settings

# ANSI color codes
COLORS = {
    "DEBUG": "\033[36m",  # Cyan
    "INFO": "\033[32m",  # Green
    "WARNING": "\033[33m",  # Yellow
    "ERROR": "\033[31m",  # Red
    "CRITICAL": "\033[35m",  # Magenta
    "RESET": "\033[0m",  # Reset
    "BOLD": "\033[1m",  # Bold
    "DIM": "\033[2m",  # Dim
}

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)-30s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class BaseExtraFormatter(logging.Formatter):
    """Base formatter with extra fields support."""

    def get_extra_items(self, record: logging.LogRecord) -> dict:
        """Extract extra fields from log record."""
        standard_attrs = {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "thread",
            "threadName",
            "exc_info",
            "exc_text",
            "stack_info",
            "asctime",
        }

        return {
            k: v
            for k, v in record.__dict__.items()
            if k not in standard_attrs and not k.startswith("_") and k != "taskName"
        }


class ExtraFormatter(BaseExtraFormatter):
    """Custom formatter with colors and structured extra fields for console."""

    def format(self, record: logging.LogRecord) -> str:
        # Add color to level name
        levelname = record.levelname
        if levelname in COLORS:
            record.levelname = f"{COLORS[levelname]}{COLORS['BOLD']}{levelname}{COLORS['RESET']}"

        # Format base message
        base_msg = super().format(record)

        # Add location info (dimmed)
        location = (
            f"{COLORS['DIM']}[{record.filename}:{record.lineno} {record.funcName}]{COLORS['RESET']}"
        )

        # Get extra items
        extra_items = self.get_extra_items(record)

        # Format extra fields with colors
        if extra_items:
            extra_parts = []
            for k, v in sorted(extra_items.items()):
                key_colored = f"{COLORS['DIM']}{k}{COLORS['RESET']}"
                if k in ("error", "error_type", "dns_error"):
                    value_colored = f"{COLORS['ERROR']}{v}{COLORS['RESET']}"
                elif k in ("status", "resolved_ip"):
                    value_colored = f"{COLORS['INFO']}{v}{COLORS['RESET']}"
                else:
                    value_colored = str(v)
                extra_parts.append(f"{key_colored}={value_colored}")

            extra_str = " ".join(extra_parts)
            return f"{base_msg} {location}\n    {COLORS['DIM']}↳{COLORS['RESET']} {extra_str}"

        return f"{base_msg} {location}"


class PlainExtraFormatter(BaseExtraFormatter):
    """Plain formatter without colors for file output."""

    def format(self, record: logging.LogRecord) -> str:
        # Format base message
        base_msg = super().format(record)

        # Get extra items
        extra_items = self.get_extra_items(record)

        # Format extra fields without colors
        if extra_items:
            extra_str = " ".join(f"{k}={v}" for k, v in sorted(extra_items.items()))
            return f"{base_msg} | {extra_str}"

        return base_msg


def configure_logging(level: Optional[str] = None) -> None:
    """Configure root logger with UTC timestamps and file output."""
    from logging.handlers import RotatingFileHandler
    from pathlib import Path

    settings = get_settings()
    effective_level = (level or settings.api_log_level).upper()
    logging.Formatter.converter = time.gmtime

    # Remove existing handlers to avoid duplicates
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Configure console handler with colors
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ExtraFormatter(LOG_FORMAT, DATE_FORMAT))
    root_logger.addHandler(console_handler)

    # Configure file handler if enabled
    if settings.log_file_enabled:
        # Create log directory if it doesn't exist
        log_dir = Path(settings.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        # Generate log filename with date
        log_file = log_dir / f"youtube_search_{time.strftime('%Y%m%d')}.log"

        # Create rotating file handler
        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=settings.log_file_max_bytes,
            backupCount=settings.log_file_backup_count,
            encoding="utf-8",
        )

        # Use plain format for file (no colors)
        file_format = (
            "%(asctime)s %(levelname)-8s %(name)-30s "
            "%(message)s [%(filename)s:%(lineno)d %(funcName)s]"
        )
        file_formatter = PlainExtraFormatter(file_format, DATE_FORMAT)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    root_logger.setLevel(effective_level)


# Audio Download Feature (Feature 004) - Download-specific logging utilities
class DownloadLogFormatter(logging.Formatter):
    """音檔下載特定日誌格式化器。"""

    DOWNLOAD_FORMAT = "%(asctime)s [%(levelname)-8s] [下載] %(message)s [%(filename)s:%(lineno)d]"

    def __init__(self):
        """初始化下載日誌格式化器。"""
        super().__init__(self.DOWNLOAD_FORMAT, DATE_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        """格式化下載日誌記錄。"""
        return super().format(record)


def get_download_logger(name: str) -> logging.Logger:
    """
    取得下載模組專用的記錄器。

    Args:
        name: 記錄器名稱

    Returns:
        logging.Logger: 配置好的記錄器
    """
    logger = logging.getLogger(name)

    # 若記錄器已有處理器，直接返回
    if logger.handlers:
        return logger

    # 建立下載特定的處理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(DownloadLogFormatter())
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Return module logger ensuring base configuration exists."""

    if not logging.getLogger().handlers:
        configure_logging()
    return logging.getLogger(name)
