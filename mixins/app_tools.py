"""AppToolsMixin — 將 app-level 指令暴露為 AI tools，注入 main agent。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent import Agent
    from app import ChatApp as _MixinBase
else:
    _MixinBase = object  # type: ignore[assignment,misc]

# ── 靜態 schemas（不依賴 REGISTRY）─────────────────────────────────────────

_LIST_AGENTS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_agents",
        "description": "列出所有可用的 AI agent（本地和全域）。當用戶問「有哪些 agent」、「列出所有 agent」時使用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_LIST_OPEN_AGENTS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_open_agents",
        "description": (
            "查詢目前已開啟的 agent tab 清單。"
            "收到「開啟 XXX」請求時，**先呼叫此工具**確認是否已開啟："
            "已開啟 → 呼叫 load_agent 切換過去；未開啟 → 呼叫 load_agent 載入。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_DESIGN_SYSTEM_PROMPT_TOOL = {
    "type": "function",
    "function": {
        "name": "design_system_prompt",
        "description": (
            "呼叫專用設計模型（CREATE_AGENT_MODEL）生成高品質的 agent system_prompt 和 greeting。"
            "建立 agent 的標準流程：先呼叫此工具取得 system_prompt，再呼叫 create_agent。"
            "例外：用戶已明確提供完整 system_prompt 時可直接呼叫 create_agent。"
            "**必須呼叫此工具取得 system_prompt，嚴禁自行撰寫後跳過。**"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name":        {"type": "string", "description": "agent 名稱"},
                "description": {"type": "string", "description": "agent 的詳細用途描述"},
            },
            "required": ["name", "description"],
        },
    },
}

_LOAD_AGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "load_agent",
        "description": "載入或切換到一個 agent tab。請先呼叫 list_open_agents 確認狀態，再呼叫此工具。**必須呼叫此工具才能實際開啟或切換，嚴禁只用文字假裝已載入。**",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "agent 名稱、暱稱或目錄名"},
            },
            "required": ["name"],
        },
    },
}

_DELETE_AGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "delete_agent",
        "description": "刪除一個 agent（不可恢復）。只有用戶明確要求刪除時才呼叫。**必須呼叫此工具才能實際刪除，嚴禁只用文字假裝已刪除。**",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "agent 名稱或暱稱"},
            },
            "required": ["name"],
        },
    },
}

_CHANGE_DIR_TOOL = {
    "type": "function",
    "function": {
        "name": "change_dir",
        "description": "切換工作目錄。當用戶說「切換到 ~/projects/xxx」、「幫我看某個目錄的專案」時使用。**必須呼叫此工具，嚴禁只用文字假裝已切換。**",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目標目錄路徑，支援 ~ 展開"},
            },
            "required": ["path"],
        },
    },
}

_REMEMBER_TOOL = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": "將重要資訊永久記錄到 .kai/notes.md，下次對話也能看到。當用戶說「記住 XXX」、「把這個記下來」時使用。**必須呼叫此工具才能實際寫入，嚴禁只用文字假裝已記住。**",
        "parameters": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "要記住的內容"},
            },
            "required": ["note"],
        },
    },
}

_RENAME_SESSION_TOOL = {
    "type": "function",
    "function": {
        "name": "rename_session",
        "description": "幫目前這段對話命名，方便之後用 /resume 找到它。當用戶說「幫這次對話取名」或完成明確任務後可主動提議命名。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "簡短描述本次對話主題的名稱"},
            },
            "required": ["name"],
        },
    },
}

_COMPACT_HISTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "compact_history",
        "description": (
            "用 AI 壓縮對話歷史，釋放 context window。"
            "當對話很長、用戶說「壓縮歷史」或「清理 context」時使用。"
            "**必須呼叫此工具，嚴禁只用文字假裝已壓縮。**"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_LIST_MCP_TOOL = {
    "type": "function",
    "function": {
        "name": "list_mcp",
        "description": "列出目前已連線的 MCP server 及工具清單。查詢可用 MCP 工具時使用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_ADD_MCP_TOOL = {
    "type": "function",
    "function": {
        "name": "add_mcp",
        "description": (
            "新增 MCP server 到 ~/.kai/mcp_servers.json。"
            "當用戶說「幫我加 playwright MCP」、「新增 XXX server」時使用。"
            "新增後需呼叫 reload_mcp 生效。"
            "**必須呼叫此工具，嚴禁只用文字假裝已新增。**"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name":    {"type": "string",  "description": "server 名稱"},
                "command": {"type": "string",  "description": "執行指令，例如 'npx'（與 url 二選一）"},
                "args":    {"type": "array",   "items": {"type": "string"}, "description": "指令參數列表"},
                "url":     {"type": "string",  "description": "HTTP/SSE URL（與 command 二選一）"},
            },
            "required": ["name"],
        },
    },
}

_REMOVE_MCP_TOOL = {
    "type": "function",
    "function": {
        "name": "remove_mcp",
        "description": (
            "從 ~/.kai/mcp_servers.json 移除 MCP server。"
            "移除後需呼叫 reload_mcp 生效。"
            "**必須呼叫此工具，嚴禁只用文字假裝已移除。**"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "server 名稱"},
            },
            "required": ["name"],
        },
    },
}

_RELOAD_MCP_TOOL = {
    "type": "function",
    "function": {
        "name": "reload_mcp",
        "description": (
            "重新連線所有 MCP server（新增或移除 server 後需呼叫）。"
            "**必須呼叫此工具，嚴禁只用文字假裝已重載。**"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_SET_TTS_TOOL = {
    "type": "function",
    "function": {
        "name": "set_tts",
        "description": (
            "切換 TTS 語音引擎或關閉語音。"
            "當用戶說「開啟語音」、「關閉語音」、「換成 kokoro」時使用。"
            "可用引擎：edge-tts、melotts、kokoro-api、kokoro、off。"
            "**必須呼叫此工具，嚴禁只用文字假裝已切換。**"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "engine": {"type": "string", "description": "語音引擎：edge-tts / melotts / kokoro-api / kokoro / off"},
            },
            "required": ["engine"],
        },
    },
}

_RELOAD_AGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "reload_agent",
        "description": (
            "重新從 config.json 載入目前 agent 的 system_prompt、model、nickname。"
            "當用戶說「重新載入 agent」、「套用我剛才的修改」時使用。"
            "**必須呼叫此工具才能實際套用，嚴禁只用文字假裝已重載。**"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_RELOAD_CONFIG_TOOL = {
    "type": "function",
    "function": {
        "name": "reload_config",
        "description": (
            "重新載入 .kai/config.json 並套用所有設定（history_keep、model、num_ctx 等）。"
            "當用戶說「重新載入設定」、「套用 config 變更」時使用。"
            "**必須呼叫此工具才能實際套用，嚴禁只用文字假裝已重載。**"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_CLOSE_AGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "close_agent",
        "description": "關閉 agent tab。當用戶說「關閉這個 agent」、「不用了」、「關掉它」時使用。**必須呼叫此工具才能實際關閉，嚴禁只用文字假裝已關閉。**省略 name 則關閉唯一開啟中的 agent tab。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "agent 名稱或暱稱，省略則關閉目前 agent tab"},
            },
            "required": [],
        },
    },
}


class AppToolsMixin(_MixinBase):

    def _inject_app_tools(self, agent: "Agent") -> None:
        """將 app-level tools 注入 agent。只對 main agent 呼叫一次（或切換模型後重注入）。"""
        from core.models import REGISTRY
        models_str = "、".join(REGISTRY.keys())

        create_agent_tool = {
            "type": "function",
            "function": {
                "name": "create_agent",
                "description": (
                    "建立一個新的 AI agent。當用戶說「幫我建一個 agent」、「我需要專門做 X 的 AI」時使用。"
                    "請自行設計完整的 system_prompt，不需要另外詢問。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name":          {"type": "string", "description": "agent 顯示名稱（中文可以）"},
                        "nickname":      {"type": "string", "description": "簡短暱稱 2-4 字，省略則同 name"},
                        "model":         {"type": "string", "description": f"模型名稱，可用：{models_str}"},
                        "system_prompt": {"type": "string", "description": "完整 system prompt；請先呼叫 design_system_prompt 取得"},
                        "description":   {"type": "string", "description": "一行說明這個 agent 的用途"},
                        "greeting":      {"type": "string", "description": "首次對話時 AI 主動開場的 prompt，可省略"},
                        "global_scope":  {"type": "boolean", "description": "true = 存到 ~/.kai/agents/（跨專案）；false = 存到 cwd/agents/（預設）"},
                        "tts": {
                            "type": "object",
                            "description": (
                                "語音設定。用戶提到「需要語音」「語音朗讀」「TTS」「說話」時填入，否則省略。"
                                "格式：{\"engine\": \"edge-tts\", \"voice\": \"zh-TW-HsiaoChenNeural\", \"player\": \"powershell\", \"speed\": 1.0}。"
                                "中文 voice：zh-TW-HsiaoChenNeural（女）/ zh-TW-YunJheNeural（男）；英文：en-US-AriaNeural。"
                            ),
                        },
                    },
                    "required": ["name", "model", "system_prompt"],
                },
            },
        }

        switch_model_tool = {
            "type": "function",
            "function": {
                "name": "switch_model",
                "description": f"切換 AI 模型。當用戶說「換成 XXX 模型」、「用 gemini 回答」時使用。可用：{models_str}。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string", "description": f"目標模型名稱，可用：{models_str}"},
                    },
                    "required": ["model"],
                },
            },
        }

        agent.extra_tools_note = (
            "\n\n## 🖥️ App 操作工具（extra tools）\n"
            "以下工具直接操作應用程式介面（開啟/關閉 tab、切換目錄、寫入檔案等）。\n"
            "**嚴禁用文字假裝已執行——必須呼叫對應工具，操作才會真正發生。**\n"
            "收到「開啟/載入 agent」請求時，必須先呼叫 list_open_agents 確認目前狀態，再決定是否呼叫 load_agent。\n"
            "可用工具：list_open_agents、list_agents、design_system_prompt、create_agent、load_agent、"
            "close_agent、delete_agent、reload_agent、change_dir、switch_model、remember、rename_session、"
            "reload_config、compact_history、list_mcp、add_mcp、remove_mcp、reload_mcp、set_tts。"
        )

        agent.extra_tools = [
            _LIST_OPEN_AGENTS_TOOL,
            _LIST_AGENTS_TOOL,
            _DESIGN_SYSTEM_PROMPT_TOOL,
            create_agent_tool,
            _LOAD_AGENT_TOOL,
            _CLOSE_AGENT_TOOL,
            _DELETE_AGENT_TOOL,
            _RELOAD_AGENT_TOOL,
            _CHANGE_DIR_TOOL,
            switch_model_tool,
            _REMEMBER_TOOL,
            _RENAME_SESSION_TOOL,
            _RELOAD_CONFIG_TOOL,
            _COMPACT_HISTORY_TOOL,
            _LIST_MCP_TOOL,
            _ADD_MCP_TOOL,
            _REMOVE_MCP_TOOL,
            _RELOAD_MCP_TOOL,
            _SET_TTS_TOOL,
        ]
        agent.extra_tools_map = {
            "list_open_agents":      self._app_list_open_agents,
            "list_agents":           self._app_list_agents,
            "design_system_prompt":  self._app_design_system_prompt,
            "create_agent":          self._app_create_agent,
            "load_agent":       self._app_load_agent,
            "close_agent":      self._app_close_agent,
            "delete_agent":     self._app_delete_agent,
            "reload_agent":     self._app_reload_agent,
            "change_dir":       self._app_change_dir,
            "switch_model":     self._app_switch_model,
            "remember":         self._app_remember,
            "rename_session":   self._app_rename_session,
            "reload_config":    self._app_reload_config,
            "compact_history":  self._app_compact_history,
            "list_mcp":         self._app_list_mcp,
            "add_mcp":          self._app_add_mcp,
            "remove_mcp":       self._app_remove_mcp,
            "reload_mcp":       self._app_reload_mcp,
            "set_tts":          self._app_set_tts,
        }

    # ── callbacks ─────────────────────────────────────────────────────────────

    async def _app_list_open_agents(self) -> str:
        from textual.widgets import TabbedContent
        tabs = self.tab_state.agent_tabs
        if not tabs:
            return "目前沒有任何已開啟的 agent tab。"
        try:
            tc = self.query_one("#center-tabs", TabbedContent)
            active_tab = tc.active
        except Exception:
            active_tab = None
        lines = ["目前已開啟的 agent tab："]
        for tab_id, agent in tabs.items():
            name = agent.display_name
            active_mark = "（目前聚焦）" if tab_id == active_tab else ""
            lines.append(f"- {name}  [tab_id: {tab_id}]{active_mark}")
        return "\n".join(lines)

    async def _app_design_system_prompt(self, name: str, description: str) -> str:
        import json
        from core.agent_factory import call_design_model
        import core.config as _cfg
        design_model = _cfg.CREATE_AGENT_MODEL or _cfg.MODEL
        self.notify(f"設計 system prompt 中…（{design_model}）", timeout=30)
        system_prompt, greeting = await call_design_model(name, description)
        return json.dumps({"system_prompt": system_prompt, "greeting": greeting}, ensure_ascii=False)

    async def _app_list_agents(self) -> str:
        from core.agent_factory import list_agents
        agents = list_agents()
        if not agents:
            return "目前沒有任何 agent，可以用 create_agent 建立。"
        lines = ["可用的 agent："]
        for a in agents:
            scope = "本地" if a["scope"] == "local" else "全域"
            nick = f"暱稱：{a['nickname']}，" if a.get("nickname") else ""
            desc = f" — {a['description']}" if a.get("description") else ""
            lines.append(f"- {a['name']}（{nick}模型：{a['model']}，{scope}）{desc}")
        return "\n".join(lines)

    async def _app_create_agent(
        self,
        name: str,
        model: str,
        system_prompt: str,
        nickname: str = "",
        description: str = "",
        greeting: str = "",
        global_scope: bool = False,
        tts: "dict | None" = None,
    ) -> str:
        from core.agent_factory import save_agent, validate_agent
        from widgets.tree import FileTree
        data: dict = {
            "name": name,
            "nickname": nickname or name,
            "model": model,
            "system_prompt": system_prompt,
            "description": description,
            "greeting": greeting,
            "mcp_servers": {},
            "history_keep": 15,
        }
        if tts:
            data["tts"] = tts
        missing = validate_agent(data)
        if missing:
            return f"缺少必要欄位：{missing}"
        adir = save_agent(data, global_scope=global_scope)
        try:
            self.query_one("#file-tree", FileTree).refresh_tree()
        except Exception:
            pass
        scope_label = f"~/.kai/agents/{adir.name}" if global_scope else f"agents/{adir.name}"
        return f"✅ 已建立 agent：{name}（{scope_label}）。可告訴我「載入 {name}」來開啟。"

    async def _app_load_agent(self, name: str) -> str:
        from core.agent_factory import find_agent
        from textual.widgets import TabbedContent
        # 若已開啟，直接切換 tab
        q = name.lower()
        for tab_id, agent in self.tab_state.agent_tabs.items():
            dn = agent.display_name.lower()
            nick = (agent._nickname or "").lower()
            if q == dn or q == nick or q in dn or (nick and q in nick):
                try:
                    tc = self.query_one("#center-tabs", TabbedContent)
                    tc.active = tab_id
                except Exception:
                    pass
                return f"✅ {agent.display_name} 已開啟中，已切換過去。"
        resolved = find_agent(name)
        if not resolved:
            return f"找不到 agent：{name}，可用 list_agents 查詢所有可用 agent。"
        try:
            await self._do_load_agent(resolved)
            return f"✅ 已開啟 agent：{name}"
        except Exception as e:
            return f"開啟失敗：{e}"

    async def _app_close_agent(self, name: str = "") -> str:
        if not name:
            if not self.tab_state.agent_tabs:
                return "目前沒有開啟中的 agent tab"
            if len(self.tab_state.agent_tabs) == 1:
                tab_id, ag = next(iter(self.tab_state.agent_tabs.items()))
                agent_name = ag.display_name
                await self._maybe_close_tab(tab_id)
                return f"✅ 已關閉 agent：{agent_name}"
            names = "、".join(ag.display_name for ag in self.tab_state.agent_tabs.values())
            return f"目前有多個開啟中的 agent：{names}。請指定要關閉哪一個。"
        q = name.lower()
        for tab_id, ag in list(self.tab_state.agent_tabs.items()):
            if q in ag.display_name.lower() or (ag._nickname and q in ag._nickname.lower()):
                agent_name = ag.display_name
                await self._maybe_close_tab(tab_id)
                return f"✅ 已關閉 agent：{agent_name}"
        return f"找不到開啟中的 agent：{name}"

    async def _app_delete_agent(self, name: str) -> str:
        from core.agent_factory import find_all_agents, delete_agent
        from widgets.tree import FileTree
        matches = find_all_agents(name)
        if not matches:
            return f"找不到 agent：{name}"
        if len(matches) > 1:
            names_str = "、".join(f"{a['name']}（{a['scope']}）" for a in matches)
            return f"找到多個符合的 agent：{names_str}。請提供更精確的名稱再重試。"
        scoped = f"{matches[0]['scope']}:{matches[0]['dir']}"
        try:
            delete_agent(scoped)
            try:
                self.query_one("#file-tree", FileTree).refresh_tree()
            except Exception:
                pass
            return f"✅ 已刪除 agent：{matches[0]['name']}"
        except Exception as e:
            return f"刪除失敗：{e}"

    async def _app_change_dir(self, path: str) -> str:
        try:
            await self._do_cd(path)
            return f"✅ 已切換至 {os.getcwd()}"
        except Exception as e:
            return f"切換失敗：{e}"

    async def _app_switch_model(self, model: str) -> str:
        try:
            await self.action_switch_model(model)
            return f"✅ 已切換至 {model}"
        except Exception as e:
            return f"切換失敗：{e}"

    async def _app_remember(self, note: str) -> str:
        notes_path = Path(".kai/notes.md")
        notes_path.parent.mkdir(exist_ok=True)
        with notes_path.open("a", encoding="utf-8") as f:
            f.write(f"\n- {note}")
        self.agent._rebuild_system_prompt()
        return f"✅ 已記住：{note}"

    async def _app_rename_session(self, name: str) -> str:
        self._do_rename(name)
        return f"✅ Session 命名為「{name}」"

    async def _app_reload_agent(self) -> str:
        try:
            await self._do_reload_agent()
            return "✅ agent 已重新載入"
        except Exception as e:
            return f"重新載入失敗：{e}"

    async def _app_reload_config(self) -> str:
        try:
            await self.action_reload_config()
            return "✅ .kai/config.json 已重新載入"
        except Exception as e:
            return f"重新載入失敗：{e}"

    async def _app_compact_history(self) -> str:
        if not self.agent._gemini and not self.agent._deepseek:
            return "需要 GEMINI_API_KEY 或 DEEPSEEK_API_KEY 才能壓縮"
        try:
            summary = await self.agent.compact_history()
            msgs = self._active_msgs()
            await msgs.remove_children()
            await self._render_history_into(msgs, self.agent)
            preview = summary[:80] + "…" if len(summary) > 80 else summary
            return f"✅ 對話已壓縮。摘要：{preview}"
        except Exception as e:
            return f"壓縮失敗：{e}"

    async def _app_list_mcp(self) -> str:
        mcp = self.agent.mcp_client
        if not mcp or not mcp.tool_names:
            return "目前沒有連線中的 MCP server"
        lines = ["已連線的 MCP server："]
        for server, tools in mcp.tools_by_server.items():
            names = "、".join(t["name"] for t in tools) if tools else "（無工具）"
            lines.append(f"- {server}（{len(tools)} 個工具）：{names}")
        return "\n".join(lines)

    async def _app_add_mcp(self, name: str, command: str = "", args: list | None = None, url: str = "") -> str:
        import json
        from core.mcp_client import GLOBAL_MCP_FILE
        if not command and not url:
            return "需要提供 command 或 url"
        data: dict = {}
        if GLOBAL_MCP_FILE.exists():
            try:
                data = json.loads(GLOBAL_MCP_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        entry: dict = {"type": "http", "url": url} if url else {"command": command, "args": args or []}
        data.setdefault("mcpServers", {})[name] = entry
        GLOBAL_MCP_FILE.parent.mkdir(parents=True, exist_ok=True)
        GLOBAL_MCP_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"✅ 已新增 MCP server：{name}。呼叫 reload_mcp 生效。"

    async def _app_remove_mcp(self, name: str) -> str:
        import json
        from core.mcp_client import GLOBAL_MCP_FILE
        if not GLOBAL_MCP_FILE.exists():
            return "~/.kai/mcp_servers.json 不存在"
        try:
            data = json.loads(GLOBAL_MCP_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            return f"讀取失敗：{e}"
        servers = data.get("mcpServers", {})
        if name not in servers:
            available = "、".join(servers.keys()) or "（空）"
            return f"找不到 server：{name}。目前有：{available}"
        del servers[name]
        GLOBAL_MCP_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"✅ 已移除 MCP server：{name}。呼叫 reload_mcp 生效。"

    async def _app_reload_mcp(self) -> str:
        try:
            await self._do_mcp_reload()
            n = len(self.agent.mcp_client.tool_names) if self.agent.mcp_client else 0
            return f"✅ MCP 重新載入完成，共 {n} 個工具"
        except Exception as e:
            return f"重新載入失敗：{e}"

    async def _app_set_tts(self, engine: str) -> str:
        self._do_tts_switch(engine)
        if engine == "off":
            return "✅ TTS 已關閉"
        return f"✅ TTS 已切換為：{engine}"

    # ── 切換模型後重新注入（新 Agent 物件需要重注入）──────────────────────────

    async def action_switch_model(self, model_name: str) -> None:
        await super().action_switch_model(model_name)  # type: ignore[misc]
        try:
            from textual.widgets import TabbedContent
            if self.query_one("#center-tabs", TabbedContent).active == "chat":
                self._inject_app_tools(self.agent)
        except Exception:
            pass
