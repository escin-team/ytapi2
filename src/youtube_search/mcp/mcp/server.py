"""MCP 伺服器實例和配置

此模組實現 MCP (Model Context Protocol) 伺服器，使用官方 MCP Python SDK
提供的 Server 類，透過 HTTP 傳輸與 MCP 客戶端通訊。

MCP 伺服器職責（US1, FR-001）：
    - 管理客戶端連接生命週期（握手、協議協商、優雅關閉）
    - 註冊和管理可用工具列表（US1-AC2, FR-002）
    - 處理工具調用請求並轉發給具體實現
    - 記錄所有連接事件和工具調用（FR-009）

使用示例：
    from youtube_search.mcp.server import create_mcp_server

    server = create_mcp_server()
    # 伺服器會自動註冊 youtube_search 工具
"""

import json
from typing import Any, Optional

from mcp.server import Server
from mcp.types import CallToolResult, TextContent, Tool

from youtube_search.config import get_settings
from youtube_search.mcp.tools.youtube_search import YouTubeSearchTool
from youtube_search.utils.logger import get_logger

# 初始化日誌
logger = get_logger(__name__)
settings = get_settings()


class MCPServerManager:
    """MCP 伺服器管理器

    此類負責：
    1. 建立和配置 MCP 伺服器實例（FR-001）
    2. 工具的註冊和生命週期管理（FR-002）
    3. 連接事件處理和日誌記錄（FR-009）
    4. 工具調用的請求轉發和回應處理

    滿足需求：
        US1-AC1: 伺服器成功建立連接並回應協定握手
        US1-AC2: 返回所有已註冊的搜尋工具及其描述
        US1-AC3: 執行搜尋並以 MCP 標準格式返回結果
        US1-AC4: 連接中斷時優雅關閉並釋放資源
        FR-001: 提供符合 MCP 協定的伺服器
        FR-002: 自動註冊所有可用工具
        FR-009: 記錄所有工具調用和錯誤
        FR-011: 提供清楚的工具描述
        FR-012: 確保 MCP 回應格式符合協定規範
        FR-013: 支援 HTTP 傳輸模式
    """

    def __init__(self):
        """初始化 MCP 伺服器管理器。"""
        self.server: Optional[Server] = None
        self.tools: dict[str, YouTubeSearchTool] = {}
        self._init_server()
        self._register_tools()

    def _init_server(self) -> None:
        """初始化 MCP 伺服器實例（FR-001）"""
        try:
            # 建立 MCP Server 實例
            self.server = Server(name="youtube-search-mcp")
            logger.info("MCP 伺服器實例建立成功", extra={"server": "youtube-search-mcp"})
        except Exception as e:
            logger.error(
                f"MCP 伺服器初始化失敗：{str(e)}",
                extra={"error": str(e)},
            )
            raise

    def _register_tools(self) -> None:
        """註冊所有可用的工具（FR-002, US1-AC2）"""
        try:
            # 建立 youtube_search 工具實例
            youtube_search_tool = YouTubeSearchTool()
            self.tools["youtube_search"] = youtube_search_tool

            # 在 MCP 伺服器中註冊工具處理器
            if self.server is not None:
                # 使用 list_tools 裝飾器
                self.server.list_tools = self._list_tools_handler
                # 使用 call_tool 裝飾器
                self.server.call_tool = self._call_tool_handler

            logger.info(
                "工具註冊成功",
                extra={
                    "tools": list(self.tools.keys()),
                    "count": len(self.tools),
                },
            )
        except Exception as e:
            logger.error(
                f"工具註冊失敗：{str(e)}",
                extra={"error": str(e)},
            )
            raise

    async def _list_tools_handler(self) -> list[Tool]:
        """處理工具列表查詢請求（US1-AC2, FR-011）

        返回所有已註冊工具的元數據，包括名稱、描述和參數定義。

        Returns:
            Tool 物件列表，每個包含工具的完整元數據
        """
        tools = []

        for tool_name, tool in self.tools.items():
            try:
                # 建立 Tool 物件（FR-012）
                tool_obj = Tool(
                    name=tool.name,
                    description=tool.description,
                    inputSchema=tool.input_schema,
                )
                tools.append(tool_obj)

                logger.debug(
                    f"工具元數據已取得：{tool_name}",
                    extra={"tool": tool_name},
                )
            except Exception as e:
                logger.warning(
                    f"無法取得工具元數據 {tool_name}：{str(e)}",
                    extra={"tool": tool_name, "error": str(e)},
                )

        logger.info(
            "列出工具請求已處理",
            extra={"tool_count": len(tools)},
        )

        return tools

    async def _call_tool_handler(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        """處理工具調用請求（US1-AC3, US2, US3）

        接收 MCP 客戶端的工具調用請求，轉發給相應的工具實現，
        並將結果格式化為 MCP 回應。

        Args:
            name: 工具名稱
            arguments: 工具參數字典

        Returns:
            CallToolResult 物件，包含工具執行結果或錯誤訊息（FR-007）
        """
        logger.debug(
            "收到工具調用請求",
            extra={"tool": name, "arguments": arguments},
        )

        try:
            # 查找工具
            if name not in self.tools:
                error_msg = f"工具 '{name}' 未找到"
                logger.warning(
                    error_msg,
                    extra={"tool": name},
                )
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"錯誤：{error_msg}",
                        )
                    ],
                    isError=True,
                )

            # 調用工具
            tool = self.tools[name]
            result = await tool.execute(arguments)

            # 格式化回應（FR-012）
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=self._format_tool_result(result),
                    )
                ],
                isError=False,
            )

        except Exception as e:
            error_msg = f"工具調用失敗：{str(e)}"
            logger.error(
                error_msg,
                extra={"tool": name, "error": str(e)},
            )

            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=error_msg,
                    )
                ],
                isError=True,
            )

    def _format_tool_result(self, result: Any) -> str:
        """將工具結果格式化為 JSON 字串（FR-012）

        Args:
            result: 工具執行結果物件

        Returns:
            JSON 格式的結果字串
        """
        try:
            # 轉換 YouTubeSearchOutput 為字典
            if hasattr(result, "model_dump"):
                # Pydantic v2
                result_dict = result.model_dump()
            else:
                # 其他類型，嘗試轉為字典
                result_dict = result if isinstance(result, dict) else str(result)

            return json.dumps(result_dict, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"格式化工具結果失敗：{str(e)}", extra={"error": str(e)})
            return str(result)

    def get_server(self) -> Server:
        """取得 MCP 伺服器實例

        Returns:
            配置完成的 MCP Server 實例
        """
        if self.server is None:
            raise RuntimeError("MCP 伺服器未初始化")
        return self.server


# 全局 MCP 伺服器管理器實例
_server_manager: Optional[MCPServerManager] = None


def get_mcp_server_manager() -> MCPServerManager:
    """取得全局 MCP 伺服器管理器實例（單例模式）

    Returns:
        MCPServerManager 實例
    """
    global _server_manager
    if _server_manager is None:
        _server_manager = MCPServerManager()
    return _server_manager


def create_mcp_server() -> Server:
    """建立和配置 MCP 伺服器（主要使用點）

    此函數是 MCP 伺服器的主要建立入口，由 FastAPI 應用初始化時調用。

    Returns:
        配置完成且工具已註冊的 MCP Server 實例
    """
    manager = get_mcp_server_manager()
    return manager.get_server()
