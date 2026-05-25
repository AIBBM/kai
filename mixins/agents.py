"""AgentsMixin — /create-agent, /load-agent, /list-agents 指令邏輯。"""
from __future__ import annotations

import os

from textual import work
from textual.containers import VerticalScroll
from textual.widgets import Static, TabbedContent, TabPane

from core.agent import Agent
from core.config import _normalize, get_nickname
from core.models import REGISTRY
from mixins.streaming import SPINNER
from widgets.input import HistoryInput
from widgets.tree import FileTree

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app import ChatApp as _MixinBase
else:
    _MixinBase = object  # type: ignore[assignment,misc]


class AgentsMixin(_MixinBase):
    @work(exclusive=True)
    async def _do_create_agent(self, description: str) -> None:
        from core.agent_factory import CREATE_AGENT_PROMPT, extract_json, save_agent, validate_agent
        msgs = self._active_msgs()
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.disabled = True
        prompt.border_title = f"{SPINNER[0]} 設計 agent 中…"
        _spin_idx = 0

        def _tick() -> None:
            nonlocal _spin_idx
            _spin_idx = (_spin_idx + 1) % len(SPINNER)
            prompt.border_title = f"{SPINNER[_spin_idx]} 設計 agent 中…"

        timer = self.set_interval(0.08, _tick)
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
            timer.stop()
            self.agent.history = _history_snapshot
            self.agent.save_history()
            prompt.disabled = False
            self._update_prompt_title()
            prompt.placeholder = f"傳訊息給 {get_nickname(self.agent.profile.name)}..."
            prompt.focus()

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
        # TTS config：True → 預設設定，dict → 自訂 voice/player
        tts_raw = data.get("tts")
        if tts_raw is True:
            agent._tts_config = {"voice": "en-US-AriaNeural"}
        elif isinstance(tts_raw, dict) and tts_raw:
            agent._tts_config = tts_raw
        else:
            agent._tts_config = None
        tab_idx = len(self._agent_tabs)
        tab_id = f"agent-{tab_idx}"
        scroll = VerticalScroll(id=f"msgs-agent-{tab_idx}")
        pane = TabPane(f"🤖 {nickname} ×", scroll, id=tab_id)
        tc = self.query_one("#center-tabs", TabbedContent)
        await tc.add_pane(pane)
        self._agent_tabs[tab_id] = agent
        tc.active = tab_id
        self.agent = agent
        await self._render_history_into(scroll, agent)
        self.notify(f"✅ 已開啟 agent：{data['name']}", timeout=3)
        # 若 config 有 greeting 且是全新對話，觸發 AI 主動開場
        greeting = data.get("greeting", "").strip()
        fresh = not any(m.get("role") != "system" for m in agent.history)
        if greeting and fresh:
            self._trigger_greeting(agent, scroll, greeting)

    @work
    async def _trigger_greeting(self, agent: "Agent", scroll: "VerticalScroll", greeting_prompt: str) -> None:
        """讓 agent 主動說第一句話。trigger 不存入 history，只保留 assistant 回覆。"""
        from textual.widgets import Static as _Static
        from widgets.chat import AIMessage
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.disabled = True
        prompt.border_title = f"{SPINNER[0]} {get_nickname(agent.profile.name)} 開場中…"
        _spin_idx = 0

        def _tick() -> None:
            nonlocal _spin_idx
            _spin_idx = (_spin_idx + 1) % len(SPINNER)
            prompt.border_title = f"{SPINNER[_spin_idx]} {get_nickname(agent.profile.name)} 開場中…"

        timer = self.set_interval(0.08, _tick)
        stream_msg: AIMessage | None = None
        buf: list[str] = []

        async def on_token(chunk: str) -> None:
            nonlocal stream_msg
            buf.append(chunk)
            if stream_msg is None:
                await scroll.mount(_Static(f"🤖 {get_nickname(agent.profile.name)}", classes="ai-header", markup=False))
                stream_msg = AIMessage()
                await scroll.mount(stream_msg)
            await stream_msg.update_content("".join(buf))
            scroll.scroll_end(animate=False)

        async def on_done(*_) -> None:
            if stream_msg and buf:
                await stream_msg.update_content("".join(buf), final=True)

        try:
            await agent.chat(
                {"role": "user", "content": greeting_prompt},
                on_token=on_token,
                on_done=on_done,
            )
            # 把 trigger user message 從 history 移除，只保留 assistant 開場白
            agent.history = [
                m for m in agent.history
                if not (m.get("role") == "user" and m.get("content") == greeting_prompt)
            ]
            agent.save_history()
        except Exception as e:
            self.notify(f"greeting 失敗：{e}", severity="warning")
        finally:
            timer.stop()
            prompt.disabled = False
            if self.agent is agent:
                self._update_prompt_title()
                prompt.placeholder = f"傳訊息給 {get_nickname(agent.profile.name)}..."
                prompt.focus()

    async def _do_reload_agent(self) -> None:
        """重新從 config.json 讀取 system_prompt，就地更新正在執行的 agent，不中斷對話。"""
        adir = self.agent._agent_dir
        if not adir:
            self.notify("目前 agent 沒有 config.json（主 chat 不支援此指令）", severity="warning")
            return
        config_path = adir / "config.json"
        if not config_path.exists():
            self.notify(f"找不到 {config_path}", severity="error")
            return
        import json
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.notify(f"讀取 config.json 失敗：{e}", severity="error")
            return
        new_prompt = data.get("system_prompt", "")
        if not new_prompt:
            self.notify("config.json 沒有 system_prompt 欄位", severity="warning")
            return
        self.agent._custom_system_prompt = new_prompt
        self.agent._rebuild_system_prompt()
        self.agent.save_history()
        name = data.get("nickname") or data.get("name", adir.name)
        self.notify(f"✅ {name} system prompt 已重載", timeout=3)

    async def _do_list_agents(self) -> None:
        from core.agent_factory import list_agents
        from widgets.agents_screen import AgentsScreen
        agents = list_agents()
        if not agents:
            self.notify("agents/ 目錄是空的，用 /create-agent 建立第一個 agent", severity="warning")
            return

        async def _on_result(dir_name: str | None) -> None:
            if dir_name:
                await self._do_load_agent(dir_name)

        self.push_screen(AgentsScreen(agents), _on_result)
