"""MCP 工具模組 - 定義所有可用的 MCP 工具

此模組包含 YouTube 搜尋服務通過 MCP 協定暴露的工具定義。
每個工具都是一個可由 MCP 客戶端（如 Claude Desktop）調用的功能。

現有工具：
    1. youtube_search - 搜尋 YouTube 影片
       - 輸入：搜尋關鍵字、結果數量限制（可選，預設 1）、排序方式（可選，預設 relevance）
       - 輸出：結構化的影片資訊列表（video_id、標題、頻道、觀看次數、URL、上傳日期等）
       - 使用場景：在 Claude Desktop 中快速搜尋 YouTube 影片

未來工具（待實現）：
    - youtube_get_video_details - 獲取單個影片的詳細信息
    - youtube_get_channel_info - 獲取頻道信息和統計數據
    - youtube_get_trending - 獲取當前熱門影片列表
    - youtube_get_video_comments - 獲取影片評論

工具使用示例：
    from youtube_search.mcp.tools import youtube_search, AVAILABLE_TOOLS

    # 查詢可用工具
    print(AVAILABLE_TOOLS)

    # 直接導入工具實現
    from youtube_search.mcp.tools.youtube_search import YouTubeSearchTool
"""

__version__ = "0.1.0"

# 工具清單定義
# 包含所有已註冊的工具及其元數據
# 根據 FR-003, FR-011, US2 規範定義
AVAILABLE_TOOLS = {
    "youtube_search": {
        "name": "youtube_search",
        "description": "在 YouTube 上搜尋影片並返回結構化結果。支援關鍵字搜尋、結果數量限制和排序選項。",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": "搜尋關鍵詞（必填，1-200 字元）",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 1,
                    "description": "最大返回結果數（可選，1-100，預設 1）",
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["relevance", "date"],
                    "default": "relevance",
                    "description": "排序方式（可選，'relevance'(相關性) 或 'date'(日期)，預設 'relevance'）",
                },
            },
            "required": ["keyword"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "videos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "video_id": {
                                "type": "string",
                                "description": "YouTube 影片 ID（11 字元）",
                            },
                            "title": {"type": "string", "description": "影片標題"},
                            "channel": {"type": "string", "description": "頻道名稱"},
                            "url": {"type": "string", "description": "影片完整 URL"},
                            "views": {"type": "integer", "description": "觀看次數"},
                            "upload_date": {
                                "type": "string",
                                "description": "上傳日期（ISO 8601 格式，如 2024-01-15T10:30:00Z）",
                            },
                        },
                    },
                    "description": "影片搜尋結果列表（最多 100 條，超過時被截斷）",
                },
                "message": {
                    "type": "string",
                    "description": "搜尋結果說明訊息（含結果數、是否被截斷等信息）",
                },
            },
        },
    },
}

# 導出聲明
# 定義模組的公開 API
__all__ = [
    "youtube_search",
    "AVAILABLE_TOOLS",
    "__version__",
]
