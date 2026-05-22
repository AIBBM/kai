"""MCPMixin — /mcp-list, /mcp-reload, /mcp-add, /mcp-del 指令邏輯。"""
from __future__ import annotations

import asyncio
import json


class MCPMixin:
    async def _do_mcp_list(self) -> None:
        from widgets.mcp_list import MCPListScreen
        mcp = self.agent.mcp_client
        if not mcp or not mcp.tool_names:
            self.notify("目前沒有連線中的 MCP server", severity="warning")
            return
        self.push_screen(MCPListScreen(mcp.tools_by_server))

    async def _do_mcp_reload(self) -> None:
        self.notify("🔄 重新連線 MCP...", severity="information")
        if self._mcp_done:
            self._mcp_done.set()
        if self._mcp_task and not self._mcp_task.done():
            try:
                await asyncio.wait_for(self._mcp_task, timeout=3.0)
            except BaseException:
                self._mcp_task.cancel()
        self._mcp_done = asyncio.Event()
        self._mcp_task = asyncio.get_event_loop().create_task(self._mcp_lifecycle())
        self.notify("✅ MCP 重新載入完成", severity="information")

    async def _do_mcp_add(self, arg: str) -> None:
        from core.config import MCP_SERVERS_FILE
        if not arg:
            self.notify("用法：/mcp-add <name> <command> [args...]  或  /mcp-add <name> --url <url>", severity="warning")
            return
        parts = arg.split()
        if len(parts) < 2:
            self.notify("用法：/mcp-add <name> <command> [args...]  或  /mcp-add <name> --url <url>", severity="warning")
            return
        name, *rest = parts
        data: dict = {}
        if MCP_SERVERS_FILE.exists():
            try:
                data = json.loads(MCP_SERVERS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        if rest[0] == "--url":
            if len(rest) < 2:
                self.notify("用法：/mcp-add <name> --url <url>", severity="warning")
                return
            entry: dict = {"type": "http", "url": rest[1]}
        else:
            command, *extra_args = rest
            entry = {"command": command, "args": extra_args}
        data.setdefault("mcpServers", {})[name] = entry
        MCP_SERVERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.notify(f"✅ 已新增 {name}，執行 /mcp-reload 生效", severity="information")

    async def _do_mcp_del(self, arg: str) -> None:
        from core.config import MCP_SERVERS_FILE
        if not arg:
            self.notify("用法：/mcp-del <name>", severity="warning")
            return
        name = arg.strip()
        if not MCP_SERVERS_FILE.exists():
            self.notify("mcp_servers.json 不存在", severity="error")
            return
        try:
            data = json.loads(MCP_SERVERS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            self.notify(f"讀取失敗：{e}", severity="error")
            return
        servers = data.get("mcpServers", {})
        if name not in servers:
            self.notify(f"找不到 server：{name}", severity="error")
            return
        del servers[name]
        MCP_SERVERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.notify(f"✅ 已移除 {name}，執行 /mcp-reload 生效", severity="information")
