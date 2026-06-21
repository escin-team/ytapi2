"""MCP Pydantic 模型定義

定義 MCP YouTube 搜尋工具的所有輸入/輸出模型。
使用 Pydantic v2 風格，支持自動 JSON Schema 生成和 OpenAPI 相容。

模型列表：
    - SearchRequest: MCP 搜尋工具的輸入參數
    - VideoInfo: 單個影片資訊
    - SearchResponse: MCP 搜尋工具的輸出結果

使用範例：
    from youtube_search.mcp.schemas import SearchRequest, SearchResponse, VideoInfo

    # 構建搜尋請求
    req = SearchRequest(query="Python 教學", max_results=5)

    # 構建回應（通常由服務層生成）
    resp = SearchResponse(
        query="Python 教學",
        results=[...],
        total=1
    )
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class YouTubeSearchInput(BaseModel):
    """MCP YouTube 搜尋工具的輸入參數模型（US2/US3）。

    此模型定義了 MCP YouTube 搜尋工具的所有輸入參數，
    用於 MCP 協定的參數驗證。

    屬性：
        keyword: 搜尋關鍵詞，1-200 字元（必填）
            - 主要欄位名稱：keyword
            - 別名（可選）：query
            - 由於 populate_by_name=True，兩個名稱均可接受
        limit: 最大返回結果數，1-100，預設 1（可選）
            - 主要欄位名稱：limit
            - 別名（可選）：max_results
            - 由於 populate_by_name=True，兩個名稱均可接受
        sort_by: 排序方式 ('relevance' 或 'date')，預設 'relevance'（可選）
    
    注意：
        - populate_by_name=True 允許在初始化時使用欄位名稱或別名
        - 初始化後，僅能通過主要欄位名稱訪問（keyword, limit），而非別名
        - 別名（query, max_results）僅在構造時有效，提供向後兼容性
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "keyword": "Python 教學",
                "limit": 5,
                "sort_by": "date",
            }
        },
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    keyword: str = Field(
        ...,
        alias="query",
        min_length=1,
        max_length=200,
        description="搜尋關鍵詞（必填，1-200 字元）。主要欄位名稱為 'keyword'（內部使用），也可使用別名 'query'（外部 API 兼容）提供此參數。",
    )
    limit: int = Field(
        default=1,
        alias="max_results",
        ge=1,
        le=100,
        description="最大返回結果數量（可選，1-100，預設 1）。主要欄位名稱為 'limit'（內部使用），也可使用別名 'max_results'（外部 API 兼容）提供此參數。",
    )
    sort_by: str = Field(
        default="relevance",
        pattern=r"^(relevance|date)$",
        description="排序方式（可選，只能是 'relevance' 或 'date'，預設 'relevance'）",
    )

    @field_validator("keyword")
    @classmethod
    def validate_keyword_not_empty(cls, value: str) -> str:
        """驗證搜尋關鍵詞不為純空白。"""
        if not value or not value.strip():
            raise ValueError("搜尋關鍵字不能為空，請提供 1-200 字符的關鍵字")
        return value.strip()

    # 提供屬性別名以支援向後兼容（用於測試和外部 API）
    @property
    def query(self) -> str:
        """
        返回 keyword 的別名（向後兼容，唯讀）。
        
        注意：這是為了向後兼容而保留的唯讀屬性。
        新代碼應該直接使用 `keyword` 欄位。
        """
        return self.keyword

    @property
    def max_results(self) -> int:
        """
        返回 limit 的別名（向後兼容，唯讀）。
        
        注意：這是為了向後兼容而保留的唯讀屬性。
        新代碼應該直接使用 `limit` 欄位。
        """
        return self.limit


class YouTubeSearchOutput(BaseModel):
    """MCP YouTube 搜尋工具的輸出結果模型（US2/US3）。

    此模型表示 MCP YouTube 搜尋工具完整的回應結果，
    包含搜尋查詢和結果影片清單。

    屬性：
        videos: 搜尋結果影片清單（可為空）
        message: 搜尋結果說明訊息（如結果數量、是否被截斷等）
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "videos": [
                    {
                        "video_id": "dQw4w9WgXcQ",
                        "title": "Python 基礎教學",
                        "channel": "教學頻道",
                        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                        "views": 1500000,
                        "upload_date": "2024-01-15T10:30:00Z",
                    }
                ],
                "message": "找到 1 個結果",
            }
        },
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    videos: list[dict[str, str | int]] = Field(
        default_factory=list,
        description="搜尋結果影片清單，每個影片包含 video_id, title, channel, url, views, upload_date",
    )
    message: str = Field(
        ...,
        max_length=500,
        description="搜尋結果說明訊息（如數量、是否被截斷等）",
    )


# ============================================================================
# 別名導出 - 支援測試和其他模組的不同命名約定
# ============================================================================

SearchRequest = YouTubeSearchInput
SearchResponse = YouTubeSearchOutput


# 簡化的影片資訊模型（用於測試）
class VideoInfo(BaseModel):
    """影片資訊模型"""

    video_id: str
    title: str
    channel: str
    url: str
    views: int = 0
    upload_date: str = ""
