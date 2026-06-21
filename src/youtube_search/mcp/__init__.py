"""MCP (Model Context Protocol) 層 - YouTube 搜尋服務的 MCP 整合

此模組提供 MCP 伺服器實現，允許 AI 助手（如 Claude Desktop）
透過 MCP 協定調用 YouTube 搜尋功能。

MCP 是一個開放標準協定，定義了 AI 助手與本地工具之間的通訊方式。

主要組件：
    - server: MCP 伺服器實例和配置
    - router: FastAPI 路由掛載
    - tools: MCP 工具定義和實現

典型使用方式：
    from youtube_search.mcp import server
    from youtube_search.mcp.tools import youtube_search

模組架構：
    youtube_search/mcp/
    ├── __init__.py       # 本模組初始化
    ├── server.py         # MCP 伺服器實現
    ├── router.py         # FastAPI 路由
    ├── schemas.py        # 資料模型和驗證
    └── tools/
        ├── __init__.py   # 工具模組初始化
        └── youtube_search.py  # YouTube 搜尋工具

性能優化：
    本模組支持延遲導入（lazy import）以提升初始加載效能。
    可選地在使用時才導入各子模組。
"""

__version__ = "0.1.0"

__all__ = [
    "server",
    "router",
    "tools",
]


def __dir__():
    """定義模組的公開 API"""
    return sorted(__all__)
