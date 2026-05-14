import json
from contextlib import AsyncExitStack

from loguru import logger
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config import MCP_SERVERS_FILE


class MCPClient:
    def __init__(self, servers: dict):
        self._servers = servers
        self._sessions: dict[str, ClientSession] = {}
        self._tool_to_server: dict[str, str] = {}
        self._ollama_tools: list[dict] = []
        self._stack = AsyncExitStack()

    @property
    def tools(self) -> list[dict]:
        return self._ollama_tools

    @property
    def tool_names(self) -> set[str]:
        return set(self._tool_to_server)

    async def start(self):
        await self._stack.__aenter__()
        for server_name, cfg in self._servers.items():
            if server_name.startswith("_"):
                continue
            try:
                params = StdioServerParameters(
                    command=cfg["command"],
                    args=cfg.get("args", []),
                    env=cfg.get("env"),
                )
                read, write = await self._stack.enter_async_context(stdio_client(params))
                session: ClientSession = await self._stack.enter_async_context(
                    ClientSession(read, write)
                )
                await session.initialize()
                self._sessions[server_name] = session

                tools_result = await session.list_tools()
                for tool in tools_result.tools:
                    self._tool_to_server[tool.name] = server_name
                    self._ollama_tools.append({
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or "",
                            "parameters": tool.inputSchema,
                        },
                    })
                logger.info(
                    f"MCP [{server_name}] 已連線，{len(tools_result.tools)} 個工具"
                )
            except Exception as e:
                logger.warning(f"MCP [{server_name}] 連線失敗：{e}")

    async def stop(self):
        await self._stack.aclose()

    async def call(self, tool_name: str, arguments: dict) -> str:
        server_name = self._tool_to_server[tool_name]
        session = self._sessions[server_name]
        result = await session.call_tool(tool_name, arguments)
        parts = [item.text for item in result.content if hasattr(item, "text")]
        return "\n".join(parts) or "[no output]"


def load_mcp_servers() -> dict:
    """載入 mcp_servers.json，檔案不存在時回傳空 dict。"""
    if not MCP_SERVERS_FILE.exists():
        return {}
    try:
        data = json.loads(MCP_SERVERS_FILE.read_text(encoding="utf-8"))
        return data.get("mcpServers", {})
    except Exception as e:
        logger.warning(f"載入 MCP server 設定失敗：{e}")
        return {}
