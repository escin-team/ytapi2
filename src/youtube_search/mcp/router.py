"""MCP 路由配置 - FastAPI 路由掛載和 MCP 伺服器集成

此模組定義了 MCP 相關的 FastAPI 路由，用於將 MCP 伺服器
集成到主 YouTube 搜尋 API 應用中。

MCP 是一個開放標準協定，定義了 AI 助手（如 Claude Desktop）
與本地工具之間的通訊方式。此模組提供了必要的 HTTP 端點
來支持 MCP 通訊、工具發現和健康檢查。

路由結構（前綴 /mcp）：
    - GET /mcp/health - MCP 伺服器健康檢查
    - GET /mcp/tools - 獲取可用工具列表和元數據
    - POST /mcp/messages - MCP 消息處理入口（SSE）- 待實現

典型集成方式：
    from fastapi import FastAPI
    from youtube_search.mcp.router import router as mcp_router

    app = FastAPI()
    app.include_router(mcp_router)

性能考慮：
    - 所有端點均為異步（async）以提升並發處理性能
    - /tools 端點支持快速的工具發現和元數據查詢
    - /health 端點用於負載均衡器健康檢查

版本歷史：
    - 0.1.0: 初始實現，包含 /health 和 /tools 端點
"""

import logging
from typing import Any

from fastapi import APIRouter

# 初始化日誌記錄器
logger = logging.getLogger(__name__)

# ============================================================================
# MCP 路由器配置
# ============================================================================

router = APIRouter(
    prefix="/mcp",
    tags=["mcp"],
    responses={
        404: {"description": "端點未找到"},
        500: {"description": "伺服器內部錯誤"},
    },
)

# ============================================================================
# 常數定義
# ============================================================================

MCP_VERSION = "0.1.0"
SERVICE_NAME = "youtube-search-mcp"


# ============================================================================
# 健康檢查端點
# ============================================================================


@router.get("/health", response_model=dict[str, Any])
async def mcp_health() -> dict[str, Any]:
    """MCP 伺服器健康檢查端點

    此端點用於檢驗 MCP 伺服器的健康狀態。可被負載均衡器、
    容器編排系統（如 Docker 或 Kubernetes）或監控系統調用
    以確認服務是否運行正常。

    返回值：
        dict: 包含以下欄位的字典
            - status (str): "healthy" 表示服務健康
            - service (str): 服務名稱
            - version (str): MCP 伺服器版本
            - timestamp (str): ISO 8601 格式的伺服器時間

    範例回應：
        {
            "status": "healthy",
            "service": "youtube-search-mcp",
            "version": "0.1.0",
            "timestamp": "2024-01-15T10:30:45.123Z"
        }

    HTTP 狀態碼：
        - 200: 服務健康
        - 500: 服務異常（異常情況下返回）
    """
    from datetime import datetime, timezone

    try:
        return {
            "status": "healthy",
            "service": SERVICE_NAME,
            "version": MCP_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"健康檢查失敗: {e}", exc_info=True)
        return {
            "status": "unhealthy",
            "service": SERVICE_NAME,
            "version": MCP_VERSION,
            "error": str(e),
        }


# ============================================================================
# 工具列表端點
# ============================================================================


@router.get("/tools", response_model=dict[str, Any])
async def mcp_tools() -> dict[str, Any]:
    """獲取可用的 MCP 工具列表和元數據

    此端點返回所有已註冊的 MCP 工具的完整清單及其元數據。
    包括工具名稱、描述、輸入/輸出 Schema 等信息，
    用於工具發現和動態呼叫。

    工具發現流程：
        1. 客戶端調用此端點
        2. 獲取可用工具列表
        3. 根據工具 Schema 構建適當的請求
        4. 通過 MCP 協定調用工具

    返回值：
        dict: 包含以下欄位的字典
            - tools (list[str]): 可用工具名稱列表
            - total (int): 工具總數
            - tools_detail (dict): 詳細的工具元數據和 Schema

    範例回應：
        {
            "tools": ["youtube_search"],
            "total": 1,
            "tools_detail": {
                "youtube_search": {
                    "name": "youtube_search",
                    "description": "在 YouTube 上搜尋影片...",
                    "input_schema": {...},
                    "output_schema": {...}
                }
            }
        }

    HTTP 狀態碼：
        - 200: 成功獲取工具列表
        - 500: 工具載入失敗

    異常處理：
        若工具模組載入失敗，仍會返回 200 狀態碼但工具清單為空，
        並包含錯誤信息用於調試。
    """
    try:
        from youtube_search.mcp.tools import AVAILABLE_TOOLS, __version__

        return {
            "tools": list(AVAILABLE_TOOLS.keys()),
            "total": len(AVAILABLE_TOOLS),
            "tools_detail": AVAILABLE_TOOLS,
            "version": __version__,
        }
    except ImportError as e:
        logger.error(f"無法載入工具模組: {e}", exc_info=True)
        return {
            "tools": [],
            "total": 0,
            "tools_detail": {},
            "error": f"工具模組載入失敗: {str(e)}",
        }
    except Exception as e:
        logger.error(f"獲取工具列表時出錯: {e}", exc_info=True)
        return {
            "tools": [],
            "total": 0,
            "tools_detail": {},
            "error": str(e),
        }


# ============================================================================
# MCP 消息處理端點 (未來實現)
# ============================================================================

# @router.post("/messages")
# async def mcp_messages(request: Request) -> StreamingResponse:
#     """MCP 消息處理入口，支持 Server-Sent Events (SSE)
#
#     此端點用於處理 MCP 客戶端的消息請求，並通過 Server-Sent Events
#     協定返回流式響應。SSE 是實時、長連接通訊的標準方式。
#
#     請求格式：
#         POST /mcp/messages
#         Content-Type: application/json
#
#         {
#             "method": "tools/list",
#             "params": {}
#         }
#
#     返回格式：
#         Content-Type: text/event-stream
#
#         data: {...}\\n\\n
#
#     支持的 MCP 方法：
#         - tools/list: 列出所有可用工具
#         - tools/call: 調用指定工具
#         - resources/list: 列出可用資源
#
#     HTTP 狀態碼：
#         - 200: 流式連接建立
#         - 400: 請求格式不正確
#         - 405: 方法不支持
#     """
#     pass

# ============================================================================
# 路由元數據
# ============================================================================

# 路由端點列表
ROUTER_ENDPOINTS = [
    {
        "method": "GET",
        "path": "/mcp/health",
        "description": "MCP 伺服器健康檢查",
        "tags": ["mcp", "health"],
    },
    {
        "method": "GET",
        "path": "/mcp/tools",
        "description": "獲取可用工具列表",
        "tags": ["mcp", "tools"],
    },
]


__all__ = ["router", "ROUTER_ENDPOINTS", "MCP_VERSION", "SERVICE_NAME"]
