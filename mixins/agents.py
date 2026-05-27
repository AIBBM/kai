"""AgentsMixin — /create-agent, /load-agent, /list-agents 指令邏輯。"""
from __future__ import annotations

from textual import work
from textual.containers import VerticalScroll
from textual.widgets import Static, TabbedContent, TabPane

from core.agent import Agent

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
        global_scope = "--global" in description.split()
        if global_scope:
            description = description.replace("--global", "").strip()
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
            adir = save_agent(data, global_scope=global_scope)
            scope_label = f"~/.kai/agents/{adir.name}" if global_scope else f"agents/{adir.name}"
            await msgs.mount(Static(
                f"✅ Agent 已建立：{data['name']}（{scope_label}/）\n"
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
            prompt.placeholder = f"傳訊息給 {self.agent.display_name}..."
            prompt.focus()

    async def _do_load_agent(self, name: str) -> None:
        from core.agent_factory import find_agent, load_agent
        # scope 前綴（來自 AgentsScreen）直接傳入；否則做模糊比對
        if name.startswith(("local:", "global:")):
            resolved = name
        else:
            resolved = find_agent(name) or name
        try:
            data, adir = load_agent(resolved)
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
        # mcp_servers 明確設為 {} 表示不要 MCP；省略或非空則共用 main agent 的 MCP client
        mcp_cfg = data.get("mcp_servers")
        if mcp_cfg != {}:
            agent.mcp_client = self.agent.mcp_client
            agent.mcp_context = getattr(self.agent, "mcp_context", "")
        nickname = data.get("nickname") or data["name"]
        # 不寫 os.environ 以免污染使用相同 model 的 main agent 暱稱
        agent._nickname = nickname
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
        prompt.border_title = f"{SPINNER[0]} {agent.display_name} 開場中…"
        _spin_idx = 0

        def _tick() -> None:
            nonlocal _spin_idx
            _spin_idx = (_spin_idx + 1) % len(SPINNER)
            prompt.border_title = f"{SPINNER[_spin_idx]} {agent.display_name} 開場中…"

        timer = self.set_interval(0.08, _tick)
        stream_msg: AIMessage | None = None
        buf: list[str] = []

        async def on_token(chunk: str) -> None:
            nonlocal stream_msg
            buf.append(chunk)
            if stream_msg is None:
                await scroll.mount(_Static(f"🤖 {agent.display_name}", classes="ai-header", markup=False))
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
                prompt.placeholder = f"傳訊息給 {agent.display_name}..."
                prompt.focus()

    async def _do_reload_agent(self) -> None:
        """重新從 config.json 熱更新執行中的 agent：system_prompt、model、nickname 全部套用。
        若 history.json 已被刪除，同步清除 in-memory history（否則 save_history 會把舊記憶寫回）。
        """
        adir = self.agent._agent_dir
        if not adir:
            self.notify("目前 agent 沒有 config.json（主 chat 不支援此指令）", severity="warning")
            return
        config_path = adir / "config.json"
        if not config_path.exists():
            self.notify(f"找不到 {config_path}", severity="error")
            return
        import json as _json
        try:
            data = _json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.notify(f"讀取 config.json 失敗：{e}", severity="error")
            return

        name = data.get("nickname") or data.get("name", adir.name)
        new_prompt = data.get("system_prompt", "")
        new_model = data.get("model", self.agent.profile.name)
        changes: list[str] = []

        if new_prompt and new_prompt != self.agent._custom_system_prompt:
            changes.append("system_prompt")
        if name != self.agent._nickname:
            changes.append("nickname")

        # model 變更：換 Agent 物件；Agent.__init__ 自動從磁碟載入 history
        if new_model != self.agent.profile.name:
            if new_model not in REGISTRY:
                self.notify(f"未知模型：{new_model}", severity="error")
                return
            old = self.agent
            self.agent = Agent(
                model=new_model,
                history_file=old._history_file,
                agent_dir=old._agent_dir,
                system_prompt=new_prompt or old._custom_system_prompt,
            )
            self.agent.mcp_client = old.mcp_client
            self.agent.mcp_context = getattr(old, "mcp_context", "")
            self.agent._nickname = name
            # 同步更新 _agent_tabs 映射
            for tid, ag in list(self._agent_tabs.items()):
                if ag is old:
                    self._agent_tabs[tid] = self.agent
                    break
            changes.append(f"model → {new_model}")
        else:
            # 以磁碟為準重載 history（不呼叫 save_history，避免覆寫使用者手動編輯）
            if new_prompt:
                self.agent._custom_system_prompt = new_prompt
            self.agent._nickname = name
            self.agent.history = []
            self.agent._rebuild_system_prompt()   # 先把新 system prompt 放進去
            self.agent._load_history()            # 再從磁碟補入 non-system 訊息

        # 更新 UI
        from widgets.sidebar import Sidebar
        from widgets.input import HistoryInput
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.set_model(self.agent.profile.name)
        mcp_sum = self.agent.mcp_client.server_summary if self.agent.mcp_client else {}
        sidebar.set_mcp(mcp_sum)
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.placeholder = f"傳訊息給 {self.agent.display_name}..."
        self._update_subtitle()
        self._sync_cap_bar()

        # 重新渲染聊天畫面（history 可能已有增減）
        msgs = self._active_msgs()
        await msgs.remove_children()
        await self._render_history_into(msgs, self.agent)

        change_str = "、".join(changes) if changes else "（無變更）"
        self.notify(f"✅ {name} 已重載：{change_str}", timeout=4)

    async def _do_list_agents(self) -> None:
        from core.agent_factory import list_agents
        from widgets.agents_screen import AgentsScreen
        agents = list_agents()
        if not agents:
            self.notify("agents/ 和 ~/.kai/agents/ 都是空的，用 /create-agent 建立第一個 agent", severity="warning")
            return

        async def _on_result(dir_name: str | None) -> None:
            if dir_name:
                await self._do_load_agent(dir_name)

        self.push_screen(AgentsScreen(agents), _on_result)
