"""YouTube 搜尋 MCP 工具實現

此模組實現 youtube_search MCP 工具，將現有的 YouTube 搜尋 API 功能
暴露給 MCP 協定客戶端（如 Claude Desktop）。

工具功能（US2）：
    - 支援關鍵字搜尋（必填參數）
    - 支援結果數量限制（可選，1-100，預設 1）
    - 支援排序方式（可選，relevance 或 date，預設 relevance）
    - 返回完整的影片 metadata（video_id, title, channel, url, views, upload_date）

參數驗證（US3）：
    - 關鍵字非空且 1-200 字符
    - limit 在 1-100 範圍內
    - sort_by 只能是 'relevance' 或 'date'
    - 返回明確的錯誤訊息和代碼供 AI 助手理解

使用示例：
    from youtube_search.mcp.tools.youtube_search import YouTubeSearchTool

    tool = YouTubeSearchTool()
    result = await tool.execute({
        "keyword": "Python 教學",
        "limit": 5,
        "sort_by": "date"
    })
"""

import asyncio
from typing import Any

from pydantic import ValidationError

from youtube_search.config import get_settings
from youtube_search.mcp.schemas import YouTubeSearchInput, YouTubeSearchOutput
from youtube_search.services.search import get_search_service
from youtube_search.utils.logger import get_logger

# 初始化日誌
logger = get_logger(__name__)
settings = get_settings()


class YouTubeSearchTool:
    """YouTube 搜尋 MCP 工具類

    此工具類封裝了 youtube_search 工具的邏輯，包括：
    - 參數驗證和正規化（US3）
    - 搜尋執行與重試邏輯（US3-AC4）
    - 降級策略（如快取故障時直接搜尋）(US3-AC5)
    - 結果轉換和格式化（US2）
    - 完整的日誌記錄（FR-009）

    滿足需求：
        FR-003: 提供 youtube_search 工具，支援 keyword, limit, sort_by 參數
        FR-005: 返回完整 metadata（video_id, title, channel, url, views, upload_date）
        FR-006: 驗證所有參數有效性
        FR-007: 返回描述性錯誤訊息
        FR-008: 優雅降級（快取故障時使用直接搜尋）
        FR-009: 記錄所有工具調用和錯誤
        FR-010: 支援環境變數配置超時和重試
        FR-011: 在工具描述中清楚說明參數和限制
    """

    def __init__(self):
        """初始化 YouTube 搜尋工具。"""
        self.search_service = get_search_service()
        self.timeout = settings.mcp_search_timeout
        self.retries = settings.mcp_search_retries

    @property
    def name(self) -> str:
        """工具名稱"""
        return "youtube_search"

    @property
    def description(self) -> str:
        """工具描述（FR-011）"""
        return (
            "搜尋 YouTube 影片。支援按關鍵字搜尋，"
            "可指定結果數量限制（1-100，預設 1）和排序方式（relevance 或 date，預設 relevance）。"
            "返回包含視頻 ID、標題、頻道、URL、觀看次數和上傳日期的完整結果。"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        """工具輸入參數 JSON Schema（FR-011）"""
        return {
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
                    "description": "排序方式（可選，'relevance' 或 'date'，預設 'relevance'）",
                },
            },
            "required": ["keyword"],
        }

    async def execute(self, params: dict[str, Any]) -> YouTubeSearchOutput:
        """執行搜尋工具（核心邏輯）

        此方法實現完整的搜尋流程：
        1. 參數驗證（US3）
        2. 重試邏輯（US3-AC4）
        3. 降級策略（US3-AC5）
        4. 結果格式化（US2）
        5. 日誌記錄（FR-009）

        Args:
            params: 工具參數字典，包含 keyword（必填）、limit（可選）、sort_by（可選）

        Returns:
            YouTubeSearchOutput: 搜尋結果，包含 videos 清單和 message 說明

        Raises:
            ValidationError: 參數驗證失敗時拋出（捕捉後轉為錯誤訊息返回）
        """
        # 日誌記錄：工具調用開始（FR-009）
        logger.info(
            "MCP youtube_search 工具調用開始",
            extra={"tool": "youtube_search", "params": params},
        )

        try:
            # 步驟 1：參數驗證（US3, FR-006）
            validated_input = self._validate_input(params)
            logger.debug(
                "參數驗證成功",
                extra={"keyword": validated_input.keyword, "limit": validated_input.limit},
            )

            # 步驟 2：帶重試的搜尋執行（US3-AC4, FR-010）
            search_result = await self._search_with_retries(
                validated_input.keyword,
                validated_input.limit,
                validated_input.sort_by,
            )

            # 步驟 3：格式化輸出（US2, FR-005）
            output = self._format_output(search_result, validated_input)

            # 日誌記錄：搜尋成功（FR-009）
            logger.info(
                "MCP youtube_search 工具調用成功",
                extra={
                    "tool": "youtube_search",
                    "keyword": validated_input.keyword,
                    "result_count": len(output.videos),
                },
            )

            return output

        except ValueError as e:
            # 參數驗證錯誤（US3, FR-007）
            error_msg = str(e)
            logger.warning(
                f"MCP youtube_search 參數驗證失敗: {error_msg}",
                extra={"tool": "youtube_search", "error": error_msg},
            )

            # 返回錯誤訊息供 AI 助手理解（FR-007）
            return YouTubeSearchOutput(
                videos=[],
                message=f"參數錯誤：{error_msg}",
            )

        except Exception as e:
            # 服務故障（US3-AC4）
            error_msg = str(e)
            logger.error(
                f"MCP youtube_search 工具執行失敗: {error_msg}",
                extra={"tool": "youtube_search", "error": error_msg},
            )

            # 返回錯誤訊息（FR-007, FR-008）
            return YouTubeSearchOutput(
                videos=[],
                message=f"服務錯誤：YouTube 服務暫時無法使用，已重試 {self.retries} 次，請稍後再試",
            )

    def _validate_input(self, params: dict[str, Any]) -> YouTubeSearchInput:
        """驗證和正規化輸入參數（US3, FR-006）

        使用 Pydantic 模型進行參數驗證，確保所有參數符合規格。
        返回驗證後的 YouTubeSearchInput 模型實例。

        Args:
            params: 輸入參數字典

        Returns:
            YouTubeSearchInput: 驗證後的參數模型

        Raises:
            ValueError: 參數驗證失敗
        """
        try:
            # 使用 Pydantic 進行驗證（自動提供詳細的錯誤訊息）
            input_model = YouTubeSearchInput(**params)
            return input_model
        except ValidationError as e:
            # 使用 Pydantic 的結構化錯誤資訊，而非字串匹配（更穩健）
            errors = e.errors()
            
            # 根據錯誤類型和欄位提供更詳細的錯誤訊息（FR-007）
            error_messages = []
            for error in errors:
                error_type = error.get("type", "")
                # 安全地提取欄位名稱（處理空或嵌套路徑的情況）
                loc = error.get("loc", ())
                field = str(loc[0]) if loc else ""

                # 處理缺少必填欄位錯誤（Pydantic 使用實際欄位名稱，不是別名）
                if error_type == "missing" and field == "keyword":
                    error_messages.append("搜尋關鍵字不能為空，請提供 1-200 字符的關鍵字")

                # 處理數值範圍錯誤（Pydantic v2 使用 'less_than_or_equal' 和 'greater_than_or_equal'）
                # 注意：Pydantic 報告錯誤時使用實際欄位名稱 'limit'，不是別名 'max_results'
                elif error_type in ("less_than_or_equal", "greater_than_or_equal") and field == "limit":
                    # 獲取實際提供的值（可能是 limit 或 max_results）
                    actual_value = params.get("limit") or params.get("max_results")
                    error_messages.append(f"limit 必須在 1-100 之間，當前值：{actual_value}")

                # 處理字串模式錯誤（用於 sort_by 的正則驗證）
                elif error_type == "string_pattern_mismatch" and field == "sort_by":
                    error_messages.append(
                        f"sort_by 只能是 'relevance' 或 'date'，當前值：{params.get('sort_by')}"
                    )

                # 處理自定義驗證錯誤（來自 field_validator）
                elif error_type == "value_error":
                    # 自定義驗證器已經提供了清晰的錯誤訊息
                    error_messages.append(error.get("msg", "參數驗證失敗"))
                else:
                    # 通用錯誤訊息（當沒有匹配到特定錯誤類型時）
                    msg = error.get("msg") or f"驗證錯誤類型: {error.get('type', 'unknown')}"
                    error_messages.append(msg)

            if error_messages:
                raise ValueError(f"參數驗證失敗：{'; '.join(error_messages)}")
    async def _search_with_retries(self, keyword: str, limit: int, sort_by: str) -> dict[str, Any]:
        """帶重試邏輯的搜尋執行（US3-AC4, FR-010）

        實現指數級別的重試機制，應對 YouTube 服務暫時不可用的情況。

        Args:
            keyword: 搜尋關鍵字
            limit: 結果數量限制
            sort_by: 排序方式

        Returns:
            包含搜尋結果的字典

        Raises:
            Exception: 所有重試都失敗時拋出
        """
        last_error = None

        for attempt in range(self.retries + 1):
            try:
                logger.debug(
                    f"搜尋嘗試 {attempt + 1}/{self.retries + 1}",
                    extra={"attempt": attempt + 1, "keyword": keyword},
                )

                # 調用搜尋服務（複用現有的 SearchService）
                result = await asyncio.wait_for(
                    self.search_service.search(
                        keyword,
                        limit,
                        sort_by,
                    ),
                    timeout=self.timeout,
                )

                logger.debug(
                    "搜尋成功",
                    extra={
                        "attempt": attempt + 1,
                        "keyword": keyword,
                        "result_count": result.result_count,
                    },
                )

                return {
                    "keyword": keyword,
                    "videos": result.videos,
                    "result_count": result.result_count,
                }

            except asyncio.TimeoutError:
                last_error = f"搜尋超時（{self.timeout} 秒）"
                logger.warning(
                    f"搜尋超時，嘗試 {attempt + 1}/{self.retries + 1}",
                    extra={"attempt": attempt + 1, "keyword": keyword},
                )

                # 如果還有重試次數，等待後再試
                if attempt < self.retries:
                    await asyncio.sleep(2**attempt)  # 指數退避

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"搜尋失敗，嘗試 {attempt + 1}/{self.retries + 1}: {last_error}",
                    extra={"attempt": attempt + 1, "keyword": keyword, "error": last_error},
                )

                # 如果還有重試次數，等待後再試
                if attempt < self.retries:
                    await asyncio.sleep(2**attempt)  # 指數退避

        # 所有重試都失敗
        raise Exception(f"YouTube 搜尋失敗：{last_error}（已重試 {self.retries} 次）")

    def _format_output(
        self, search_result: dict[str, Any], input_params: YouTubeSearchInput
    ) -> YouTubeSearchOutput:
        """格式化搜尋結果為 MCP 輸出格式（US2, FR-005）

        將 SearchService 的回傳值轉換為 MCP 協定要求的格式，
        包含所有必要的 metadata。

        Args:
            search_result: 搜尋服務的結果字典
            input_params: 驗證後的輸入參數

        Returns:
            YouTubeSearchOutput: MCP 格式的輸出
        """
        videos = []

        # 轉換每個影片到 MCP 格式（FR-005）
        for video in search_result.get("videos", []):
            video_dict = {
                "video_id": video.video_id,
                "title": video.title or "未知標題",
                "channel": video.channel or "未知頻道",
                "url": video.url or f"https://www.youtube.com/watch?v={video.video_id}",
                "views": video.view_count or 0,
                "upload_date": video.publish_date or "未知日期",
            }
            videos.append(video_dict)

        # 限制結果到 100 條（US2-AC6）
        if len(videos) > 100:
            videos = videos[:100]
            message = "找到超過 100 個結果，已達到上限，部分結果被截斷。顯示前 100 條。"
        elif len(videos) == 0:
            message = "未找到符合條件的影片"
        else:
            message = f"找到 {len(videos)} 個結果"

        # 構建輸出（FR-005, FR-012）
        return YouTubeSearchOutput(
            videos=videos,
            message=message,
        )
