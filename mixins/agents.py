"""AgentsMixin — /create-agent, /load-agent, /list-agents 指令邏輯。"""
from __future__ import annotations

import os

from textual.containers import VerticalScroll
from textual.widgets import Static, TabbedContent, TabPane

from core.agent import Agent
from core.config import _normalize
from core.models import REGISTRY
from widgets.tree import FileTree


class AgentsMixin:
    async def _do_create_agent(self, description: str) -> None:
        from core.agent_factory import CREATE_AGENT_PROMPT, extract_json, save_agent, validate_agent
        msgs = self._active_msgs()
        await msgs.mount(Static(f"🏭 正在設計 agent：{description}", markup=False, classes="bash-output"))
        msgs.scroll_end(animate=False)
        model_list = ", ".join(REGISTRY.keys())
        prompt_text = CREATE_AGENT_PROMPT.format(description=description, models=model_list)
        _history_snapshot = [m.copy() for m in self.agent.history]
        try:
            reply_parts: list[str] = []
            async def _collect(chunk: str) -> None:
                reply_parts.append(chunk)
            await self.agent.chat(
                {"role": "user", "content": prompt_text},
                on_token=_collect,
                on_done=None,
            )
            raw = "".join(reply_parts)
            data = extract_json(raw)
            missing = validate_agent(data)
            if missing:
                self.notify(f"LLM 回覆缺少欄位：{missing}", severity="error")
                return
            adir = save_agent(data)
            await msgs.mount(Static(
                f"✅ Agent 已建立：{data['name']}（agents/{adir.name}/）\n"
                f"   model: {data['model']}  nickname: {data.get('nickname', '—')}\n"
                f"   輸入 /load-agent {adir.name} 載入",
                markup=False, classes="bash-output",
            ))
            msgs.scroll_end(animate=False)
            self.query_one("#file-tree", FileTree).refresh_tree()
        except Exception as e:
            self.notify(f"建立失敗：{e}", severity="error")
        finally:
            self.agent.history = _history_snapshot
            self.agent.save_history()

    async def _do_load_agent(self, name: str) -> None:
        from core.agent_factory import load_agent
        try:
            data, adir = load_agent(name)
        except FileNotFoundError as e:
            self.notify(str(e), severity="error")
            return
        model = data.get("model", self.agent.profile.name)
        if model not in REGISTRY:
            self.notify(f"未知模型：{model}，改用 {self.agent.profile.name}", severity="warning")
            model = self.agent.profile.name
        agent = Agent(
            model=model,
            history_file=adir / "history.json",
            agent_dir=adir,
            system_prompt=data["system_prompt"],
        )
        agent.mcp_client = self.agent.mcp_client
        agent.mcp_context = getattr(self.agent, "mcp_context", "")
        nickname = data.get("nickname") or data["name"]
        os.environ[f"NICKNAME_{_normalize(model)}"] = nickname
        if "history_keep" in data:
            agent.history_keep = int(data["history_keep"])
        tab_idx = len(self._agent_tabs)
        tab_id = f"agent-{tab_idx}"
        scroll = VerticalScroll(id=f"msgs-agent-{tab_idx}")
        pane = TabPane(f"🤖 {nickname} ×", scroll, id=tab_id)
        tc = self.query_one("#center-tabs", TabbedContent)
        await tc.add_pane(pane)
        tc.active = tab_id
        self._agent_tabs[tab_id] = agent
        self.notify(f"✅ 已開啟 agent：{data['name']}", timeout=3)

    async def _do_list_agents(self) -> None:
        from core.agent_factory import list_agents
        from rich.box import SIMPLE
        from rich.table import Table as RichTable
        agents = list_agents()
        msgs = self._active_msgs()
        if not agents:
            await msgs.mount(Static("📂 agents/ 目錄是空的，用 /create-agent 建立第一個 agent", markup=False, classes="bash-output"))
            msgs.scroll_end(animate=False)
            return
        tbl = RichTable(box=SIMPLE, show_header=True, expand=True, padding=(0, 1))
        tbl.add_column("目錄", style="cyan", no_wrap=True)
        tbl.add_column("名稱", style="bold")
        tbl.add_column("暱稱")
        tbl.add_column("模型", style="dim")
        tbl.add_column("說明", style="dim")
        for a in agents:
            tbl.add_row(a["dir"], a["name"], a.get("nickname", "—"), a["model"], a.get("description", "—"))
        await msgs.mount(Static(tbl))
        msgs.scroll_end(animate=False)
