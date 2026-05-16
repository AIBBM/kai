import json
import os
from contextlib import AsyncExitStack

from loguru import logger
from mcp import ClientSession, StdioServerParameters
import httpx
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

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

    @property
    def server_summary(self) -> dict[str, int]:
        """Connected server → tool count."""
        counts: dict[str, int] = {}
        for server in self._tool_to_server.values():
            counts[server] = counts.get(server, 0) + 1
        return counts

    async def start(self):
        await self._stack.__aenter__()
        for server_name, cfg in self._servers.items():
            if server_name.startswith("_"):
                continue
            try:
                transport_type = cfg.get("type", "stdio")
                if transport_type == "http":
                    url = cfg["url"]
                    raw_headers = cfg.get("headers", {})
                    def _expand(v: str) -> str:
                        import re
                        v = re.sub(r"\$\{env:([^}]+)\}", lambda m: os.getenv(m.group(1), m.group(0)), v)
                        return os.path.expandvars(v)
                    headers = {k: _expand(v) for k, v in raw_headers.items()}
                    http_client = httpx.AsyncClient(headers=headers)
                    await self._stack.enter_async_context(http_client)
                    read, write, _ = await self._stack.enter_async_context(
                        streamable_http_client(url, http_client=http_client)
                    )
                else:
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
            except BaseException as e:
                causes = e.exceptions if isinstance(e, BaseExceptionGroup) else [e]
                for cause in causes:
                    logger.warning(f"MCP [{server_name}] 連線失敗：{type(cause).__name__}: {cause}")

    async def stop(self):
        await self._stack.aclose()

    async def call(self, tool_name: str, arguments: dict) -> str:
        server_name = self._tool_to_server[tool_name]
        session = self._sessions[server_name]
        logger.debug(f"MCP call [{server_name}] {tool_name}({arguments})")
        result = await session.call_tool(tool_name, arguments)
        parts = [item.text for item in result.content if hasattr(item, "text")]
        output = "\n".join(parts) or "[no output]"
        logger.debug(f"MCP result [{tool_name}] isError={result.isError} len={len(output)}")
        return output


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
