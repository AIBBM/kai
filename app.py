"""Textual TUI for qwen-agent.  Run: uv run python app.py"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from functools import partial
from pathlib import Path

from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import Hit, Hits, Provider
from textual.containers import Horizontal, VerticalScroll
from textual import events
from textual.events import Click
from textual.widget import Widget
from textual.widgets import Collapsible, Footer, Header, Input, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option
from textual import work
from loguru import logger

from agent import Agent
from config import BASH_TIMEOUT, MODEL
from models import REGISTRY
from mcp_client import MCPClient, load_mcp_servers

logger.remove()
logger.add(
    "agent_tui.log",
    level="DEBUG",
    rotation="5 MB",
    format="{time:HH:mm:ss} | {level:<5} | {message}",
)

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
PROMPT_HISTORY_FILE = Path(".prompt_history")


def _load_prompt_history() -> list[str]:
    """讀取 prompt_toolkit FileHistory 格式，回傳最舊→最新排列的清單。"""
    if not PROMPT_HISTORY_FILE.exists():
        return []
    entries: list[str] = []
    current: list[str] = []
    for line in PROMPT_HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("+"):
            current.append(line[1:])
        elif not line.strip() and current:
            entries.append("\n".join(current))
            current = []
    if current:
        entries.append("\n".join(current))
    return entries


def _append_prompt_history(text: str) -> None:
    """將一筆輸入追加到 prompt_toolkit FileHistory 格式的檔案。"""
    from datetime import datetime
    lines = "\n".join(f"+{ln}" for ln in text.splitlines())
    entry = f"# {datetime.now()}\n{lines}\n\n"
    try:
        with PROMPT_HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        logger.warning(f"寫入 prompt history 失敗：{e}")


async def _fetch_db_schema(mcp: MCPClient) -> str:
    """查詢資料庫 table 清單，產生給模型的 schema hint（與 main.py 共用邏輯）。"""
    sql = (
        "SELECT hypertable_name AS table_name, 'hypertable' AS type "
        "FROM timescaledb_information.hypertables "
        "UNION ALL "
        "SELECT relname, 'table' FROM pg_class cl "
        "JOIN pg_namespace ns ON ns.oid = cl.relnamespace "
        "WHERE ns.nspname = 'public' AND cl.relkind = 'r' "
        "AND relname NOT IN (SELECT hypertable_name FROM timescaledb_information.hypertables) "
        "AND relname NOT LIKE '%backup%' "
        "ORDER BY table_name"
    )
    # 不同 postgres MCP server 暴露的工具名不同，依序探測第一個可用的
    for tool_name in ("query", "execute_query", "pg_query", "read_query"):
        if tool_name in mcp.tool_names:
            try:
                result = await mcp.call(tool_name, {"sql": sql})
                return f"可查詢的資料表：\n{result}"
            except Exception as e:
                logger.warning(f"schema 查詢失敗（{tool_name}）：{e}")
                break
    return ""


def _line_height(text: str, cols: int = 100) -> int:
    total = 0
    for line in text.split("\n"):
        total += max(1, (len(line) + cols - 1) // cols)
    return total


class HistoryInput(Input):
    """Input with Up/Down history navigation.

    Uses _on_key (runs before the binding system) to intercept up/down before
    ScrollView's scroll_up/scroll_down bindings consume them.
    """

    def _on_key(self, event: events.Key) -> None:
        if event.key == "up":
            event.prevent_default()
            event.stop()
            self.app.action_history_prev()  # type: ignore[attr-defined]
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            self.app.action_history_next()  # type: ignore[attr-defined]


class NavigableOptionList(OptionList):
    """OptionList 子類：在檔案瀏覽模式下支援字母鍵循環跳轉。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._jump_paths: list[str] = []
        self._letter_idx: dict[str, int] = {}

    def set_file_mode(self, paths: list[str]) -> None:
        self._jump_paths = list(paths)
        self._letter_idx = {}

    def clear_file_mode(self) -> None:
        self._jump_paths = []
        self._letter_idx = {}

    def on_key(self, event) -> None:
        if not self._jump_paths:
            return
        key = event.key
        if len(key) == 1 and key.isalpha():
            letter = key.lower()
            candidates = [
                i for i, p in enumerate(self._jump_paths)
                if Path(p).name.lower().startswith(letter)
            ]
            if not candidates:
                return
            event.prevent_default()
            # advance past last match; wrap around when exhausted
            last = self._letter_idx.get(letter, -1)
            nexts = [i for i in candidates if i > last]
            next_idx = nexts[0] if nexts else candidates[0]
            self._letter_idx[letter] = next_idx
            self.highlighted = next_idx


class Sidebar(Widget):
    """工作目錄、上下文檔案、模型、Git 狀態側邊欄。"""

    def compose(self) -> ComposeResult:
        yield Static("📂 工作目錄", classes="sidebar-title", markup=True)
        yield Static(os.getcwd(), classes="sidebar-content", id="sb-cwd", markup=False)
        yield Static("🤖 模型", classes="sidebar-title", markup=True)
        yield Static(MODEL, classes="sidebar-content", id="sb-model", markup=False)
        yield Static("📄 上下文檔案", classes="sidebar-title", markup=True)
        yield Static("[dim]（無）[/dim]", classes="sidebar-content", id="sb-files", markup=True)
        yield Static("📋 Git 變更", classes="sidebar-title", markup=True)
        yield Static("", classes="sidebar-content", id="sb-git", markup=True)
        yield Static("🔧 MCP 工具", classes="sidebar-title", markup=True)
        yield Static("[dim]初始化中...[/dim]", classes="sidebar-content", id="sb-mcp", markup=True)

    def on_mount(self) -> None:
        self._refresh_git_worker()

    @work(thread=True)
    def _refresh_git_worker(self) -> None:
        try:
            out = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True, text=True, timeout=2, cwd=os.getcwd(),
            ).stdout.strip()
            result = out or "[dim]無變更[/dim]"
        except Exception:
            result = "[dim]非 git 目錄[/dim]"
        self.app.call_from_thread(self.query_one("#sb-git", Static).update, result)

    def refresh_git(self) -> None:
        self._refresh_git_worker()

    def set_model(self, name: str) -> None:
        self.query_one("#sb-model", Static).update(name)

    def set_files(self, paths: list[str]) -> None:
        text = "\n".join(f"[green]✓[/green] {p}" for p in paths) if paths else "[dim]（無）[/dim]"
        self.query_one("#sb-files", Static).update(text)

    def set_mcp(self, summary: dict[str, int]) -> None:
        if summary:
            lines = [f"[green]{name}[/green] [dim]{n}t[/dim]" for name, n in summary.items()]
            text = "\n".join(lines)
        else:
            text = "[dim]未啟用[/dim]"
        self.query_one("#sb-mcp", Static).update(text)


class AIMessage(Widget):
    """AI 回應：預設 Markdown 渲染，右鍵切換 TextArea 選取模式。"""

    can_focus = False
    _raw: str = ""

    def compose(self) -> ComposeResult:
        yield Markdown("", classes="md-view")
        yield TextArea(
            "",
            read_only=True,
            soft_wrap=True,
            show_line_numbers=False,
            tab_behavior="focus",
            classes="ta-view",
        )

    async def update_content(self, text: str, final: bool = False) -> None:
        self._raw = text
        await self.query_one(".md-view", Markdown).update(text)
        if final:
            ta = self.query_one(".ta-view", TextArea)
            ta.text = text
            ta.styles.height = max(4, min(_line_height(text) + 1, 50))

    def on_click(self, event: Click) -> None:
        if event.button != 3:
            return
        if "select-mode" in self.classes:
            self.remove_class("select-mode")
            self.app.query_one("#prompt", HistoryInput).focus()
        else:
            ta = self.query_one(".ta-view", TextArea)
            ta.language = "markdown"
            ta.text = self._raw
            ta.styles.height = max(4, min(_line_height(self._raw) + 1, 50))
            self.add_class("select-mode")
            ta.focus()
            self.app.notify(
                "選取模式：拖曳選取 → Ctrl+C 複製  ·  右鍵返回",
                severity="information", timeout=3,
            )


class ChatCommands(Provider):
    def _all_hits(self, app: "ChatApp") -> list[Hit]:
        current = app.agent.profile.name
        hits = [Hit(1.0, "清除對話", app.action_clear, help="清除所有訊息與對話記錄")]
        for model_name in REGISTRY:
            is_cur = model_name == current
            hits.append(Hit(
                1.0 if is_cur else 0.8,
                f"切換模型  {model_name}{'  ✓' if is_cur else ''}",
                partial(app.action_switch_model, model_name),
                help="目前使用中" if is_cur else f"切換到 {model_name}",
            ))
        return hits

    async def discover(self) -> Hits:
        for hit in self._all_hits(self.app):  # type: ignore
            yield hit

    async def search(self, query: str) -> Hits:
        app: ChatApp = self.app  # type: ignore
        matcher = self.matcher(query)
        for hit in self._all_hits(app):
            label = hit.match_display if isinstance(hit.match_display, str) else str(hit.match_display)
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, matcher.highlight(label), hit.command, help=hit.help)


class ChatApp(App[None]):
    CSS_PATH = "app.tcss"
    COMMANDS = App.COMMANDS | {ChatCommands}

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("ctrl+p", "command_palette", "命令"),
        Binding("ctrl+b", "toggle_sidebar", "側邊欄"),
        Binding("escape", "cancel_stream", "取消"),
    ]

    SLASH_COMMANDS: list[tuple[str, str]] = []

    def __init__(self) -> None:
        super().__init__()
        # Textual 8 adds .with_tooltip() to every Markdown table cell; the Tooltip
        # widget mounts on Screen's _tooltips layer (not inside AIMessage), so CSS
        # selectors can't reach it, and it uses inline display=True which overrides CSS.
        self._disable_tooltips = True
        self.agent = Agent(model=MODEL)
        self._spin_idx = 0
        self._spin_label = "生成中"
        self._context_files: list[tuple[str, str]] = []  # (path, content)
        self._debug_tools = False
        self._input_history: list[str] = _load_prompt_history()
        self._history_idx: int = -1
        self._history_draft: str = ""
        self._mcp_task: asyncio.Task | None = None
        self._mcp_done: asyncio.Event | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="app-grid"):
            with VerticalScroll(id="messages"):
                yield Static(
                    "[dim]輸入訊息開始對話[/dim]  ·  "
                    "[dim]Ctrl+P[/dim] 命令  "
                    "[dim]Ctrl+B[/dim] 側邊欄  "
                    "[dim]右鍵 AI 訊息[/dim] 選取複製",
                    id="welcome", markup=True,
                )
            yield Sidebar(id="sidebar")
        yield NavigableOptionList(id="cmd-overlay")
        yield HistoryInput(placeholder=f"傳訊息給 {MODEL}...", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "qwen-agent"
        self.sub_title = MODEL
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.focus()
        self._update_prompt_title()
        self.__class__.SLASH_COMMANDS = (
            [
                ("/clear", "清除對話記錄"),
                ("/add",   "加入檔案到上下文  /add <路徑>"),
                ("/del",   "移除上下文檔案  /del <路徑>"),
                ("/debug", "切換工具除錯模式（顯示工具參數與回應）"),
            ]
            + [(f"/model {m}", f"切換到 {m}") for m in REGISTRY]
        )
        self.query_one("#cmd-overlay", NavigableOptionList).border_title = "指令"
        # 每 15 秒輪詢 Git 狀態
        self.set_interval(15, lambda: self.query_one("#sidebar", Sidebar).refresh_git())
        # start MCP in a dedicated asyncio task so start() and stop() share the same
        # task context — anyio cancel scopes must enter and exit in the same task
        self._mcp_done = asyncio.Event()
        self._mcp_task = asyncio.get_event_loop().create_task(self._mcp_lifecycle())

    async def _mcp_lifecycle(self) -> None:
        """Single long-lived asyncio task: MCP start → keep-alive → stop.

        Keeping start() and stop() in the same task avoids anyio cross-task
        cancel scope errors that occur when stop() is called from on_unmount.
        """
        sidebar = self.query_one("#sidebar", Sidebar)
        servers = load_mcp_servers()
        if not servers:
            sidebar.set_mcp({})
            return
        mcp = MCPClient(servers, errlog=open(os.devnull, "w"))
        try:
            await mcp.start()
            if not mcp.tool_names:
                sidebar.set_mcp({})
                return
            self.agent.mcp_client = mcp
            self.agent.mcp_context = await _fetch_db_schema(mcp)
            # 補充 filesystem 可存取路徑到 context
            fs_paths = [
                arg for name, cfg in servers.items()
                if not name.startswith("_") and cfg.get("command") in ("npx", "node")
                for arg in cfg.get("args", []) if arg.startswith("/")
            ]
            if fs_paths:
                self.agent.mcp_context += f"\n檔案系統可存取目錄：{', '.join(fs_paths)}"
            self.agent._reset_date()  # 重建 system prompt，注入 MCP 工具清單
            sidebar.set_mcp(mcp.server_summary)
            logger.info(f"MCP 初始化完成：{mcp.tool_names}")
            # Block until quit signal; stop() must run here in the same task
            await self._mcp_done.wait()
        except asyncio.CancelledError:
            pass
        finally:
            self.agent.mcp_client = None
            await mcp.stop()

    async def action_quit(self) -> None:
        if self._mcp_done:
            self._mcp_done.set()
        if self._mcp_task and not self._mcp_task.done():
            try:
                await asyncio.wait_for(self._mcp_task, timeout=2.0)
            except BaseException:
                self._mcp_task.cancel()
        self.exit()

    def on_unmount(self) -> None:
        pass

    # ── 輸入事件 ──────────────────────────────────────────────────────────────

    def action_history_prev(self) -> None:
        prompt = self.query_one("#prompt", HistoryInput)
        if not prompt.has_focus or prompt.disabled:
            return
        if self.query_one("#cmd-overlay", NavigableOptionList).display:
            return
        if not self._input_history:
            return
        if self._history_idx == -1:
            self._history_draft = prompt.value
        self._history_idx = min(self._history_idx + 1, len(self._input_history) - 1)
        prompt.value = self._input_history[-(self._history_idx + 1)]
        prompt.cursor_position = len(prompt.value)

    def action_history_next(self) -> None:
        prompt = self.query_one("#prompt", HistoryInput)
        if not prompt.has_focus or prompt.disabled:
            return
        if self.query_one("#cmd-overlay", NavigableOptionList).display:
            return
        if self._history_idx == -1:
            return
        self._history_idx -= 1
        if self._history_idx == -1:
            prompt.value = self._history_draft
        else:
            prompt.value = self._input_history[-(self._history_idx + 1)]
        prompt.cursor_position = len(prompt.value)

    def on_input_changed(self, event: Input.Changed) -> None:
        value = event.value
        overlay = self.query_one("#cmd-overlay", NavigableOptionList)

        # /add <prefix> → 瀏覽目錄
        if value.startswith("/add "):
            self._show_file_browser(overlay, value[5:])
            return

        # /del <prefix> → 列出 context 檔案
        if value.startswith("/del "):
            self._show_del_list(overlay, value[5:])
            return

        if not value.startswith("/"):
            overlay.clear_file_mode()
            overlay.display = False
            return

        overlay.clear_file_mode()
        matches = [(cmd, desc) for cmd, desc in self.SLASH_COMMANDS if cmd.startswith(value)]
        overlay.clear_options()
        for cmd, desc in matches:
            overlay.add_option(Option(f"{cmd}  [dim]{desc}[/dim]", id=cmd))
        overlay.display = bool(matches)

    def _show_file_browser(self, overlay: NavigableOptionList, prefix: str) -> None:
        try:
            was_visible = overlay.display
            base = Path(prefix) if prefix else Path(".")
            search_dir = base if base.is_dir() else base.parent
            name_filter = "" if base.is_dir() else base.name.lower()
            entries = sorted(search_dir.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            matches = [p for p in entries if p.name.lower().startswith(name_filter)][:20]
            overlay.clear_options()
            paths: list[str] = []
            for p in matches:
                icon = "📁" if p.is_dir() else "📄"
                path_str = str(p)
                overlay.add_option(Option(f"{icon} {path_str}", id=f"__add__{path_str}"))
                paths.append(path_str)
            overlay.set_file_mode(paths)
            overlay.border_title = "選擇檔案  ↓/字母鍵 跳轉"
            overlay.display = bool(matches)
            # only auto-focus on first open; don't steal focus on every keystroke update
            if not was_visible and overlay.display:
                overlay.focus()
        except Exception:
            overlay.display = False

    def _show_del_list(self, overlay: NavigableOptionList, prefix: str) -> None:
        overlay.clear_file_mode()
        overlay.clear_options()
        matches = [fp for fp, _ in self._context_files
                   if not prefix or prefix.lower() in fp.lower()]
        for fp in matches:
            overlay.add_option(Option(f"🗑  {Path(fp).name}  [dim]{fp}[/dim]", id=f"__del__{fp}"))
        if not matches:
            overlay.add_option(Option("[dim]沒有上下文檔案[/dim]", id="__noop__"))
        overlay.border_title = "移除檔案"
        overlay.display = True

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        cmd_id = event.option_id or ""
        prompt = self.query_one("#prompt", HistoryInput)
        overlay = self.query_one("#cmd-overlay", NavigableOptionList)
        overlay.border_title = "指令"
        overlay.clear_file_mode()
        overlay.display = False

        if cmd_id.startswith("__add__"):
            await self._add_file(cmd_id[7:])
            prompt.value = ""
            prompt.focus()
        elif cmd_id.startswith("__del__"):
            await self._del_file(cmd_id[7:])
            prompt.value = ""
            prompt.focus()
        elif cmd_id == "__noop__":
            prompt.focus()
        else:
            prompt.value = cmd_id + " "
            prompt.cursor_position = len(prompt.value)
            prompt.focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        self.query_one("#cmd-overlay", NavigableOptionList).display = False
        if not self._input_history or self._input_history[-1] != text:
            self._input_history.append(text)
            _append_prompt_history(text)
        self._history_idx = -1
        self._history_draft = ""

        if text.startswith("!"):
            cmd = text[1:].strip()
            if cmd:
                self._run_bash(cmd)
            return

        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            if cmd == "/clear":
                await self.action_clear()
            elif cmd == "/model" and arg:
                await self.action_switch_model(arg)
            elif cmd == "/model":
                self.notify("用法：/model <模型名稱>", severity="warning")
            elif cmd == "/add" and arg:
                await self._add_file(arg)
            elif cmd == "/add":
                self.notify("用法：/add <檔案路徑>", severity="warning")
            elif cmd == "/del" and arg:
                await self._del_file(arg)
            elif cmd == "/del":
                self.notify("用法：/del <檔案路徑>", severity="warning")
            elif cmd == "/debug":
                self._debug_tools = not self._debug_tools
                label = "🔧 開啟" if self._debug_tools else "關閉"
                self.notify(f"工具除錯模式：{label}", severity="information", timeout=3)
                self._update_prompt_title()
            else:
                self.notify(f"未知指令：{cmd}", severity="warning")
            return

        msgs = self.query_one("#messages", VerticalScroll)
        await msgs.mount(
            Static(f"[bold]Duncan[/bold]\n{text}", markup=True, classes="user-bubble")
        )
        msgs.scroll_end(animate=False)

        # 組合上下文檔案 + 使用者訊息
        if self._context_files:
            file_blocks = "\n\n".join(
                f"# 檔案：{p}\n```\n{c}\n```" for p, c in self._context_files
            )
            full_text = f"{file_blocks}\n\n{text}"
        else:
            full_text = text

        self._send(full_text)

    # ── Actions ───────────────────────────────────────────────────────────────

    async def _del_file(self, path: str) -> None:
        p = str(Path(path).expanduser().resolve())
        before = len(self._context_files)
        self._context_files = [(fp, fc) for fp, fc in self._context_files if fp != p]
        if len(self._context_files) < before:
            self.query_one("#sidebar", Sidebar).set_files([fp for fp, _ in self._context_files])
            self.notify(f"已移除：{Path(p).name}", severity="information")
        else:
            self.notify(f"找不到：{path}", severity="warning")

    async def _add_file(self, path: str) -> None:
        try:
            p = Path(path).expanduser().resolve()
            content = p.read_text(encoding="utf-8", errors="replace")
            # 避免重複加入同一路徑
            self._context_files = [(fp, fc) for fp, fc in self._context_files if fp != str(p)]
            self._context_files.append((str(p), content))
            self.query_one("#sidebar", Sidebar).set_files([fp for fp, _ in self._context_files])
            self.notify(f"已加入：{p.name}（{len(content)} 字元）", severity="information")
        except Exception as e:
            self.notify(f"無法讀取 {path}：{e}", severity="error")

    def _update_prompt_title(self) -> None:
        base = "Duncan ❯"
        suffix = "  [bold yellow]🔧 debug[/bold yellow]" if self._debug_tools else ""
        self.query_one("#prompt", HistoryInput).border_title = base + suffix

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.display = not sidebar.display

    async def action_clear(self) -> None:
        await self.query_one("#messages", VerticalScroll).remove_children()
        self.agent.history = []
        self.agent._reset_date()
        self.agent.save_history()
        self._context_files.clear()
        self.query_one("#sidebar", Sidebar).set_files([])
        self.sub_title = self.agent.profile.name

    async def action_switch_model(self, model_name: str) -> None:
        if model_name not in REGISTRY:
            self.notify(f"未知模型：{model_name}", severity="error")
            return
        if model_name == self.agent.profile.name:
            self.notify("已是目前模型", severity="information")
            return
        old_history = self.agent.history[:]
        self.agent = Agent(model=model_name)
        self.agent.history = old_history
        self.agent._reset_date()
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.placeholder = f"傳訊息給 {model_name}..."
        self.sub_title = model_name
        self.query_one("#sidebar", Sidebar).set_model(model_name)
        self.notify(f"已切換至 {model_name}", severity="information")

    # ── Streaming worker ──────────────────────────────────────────────────────

    @work(exclusive=True)
    async def _send(self, text: str) -> None:
        msgs   = self.query_one("#messages", VerticalScroll)
        prompt = self.query_one("#prompt",   HistoryInput)
        stream_msg: AIMessage | None = None
        buf: list[str] = []

        self._spin_idx = 0
        self._spin_label = "等待回覆"

        def _tick() -> None:
            self._spin_idx = (self._spin_idx + 1) % len(SPINNER)
            prompt.border_title = f"{SPINNER[self._spin_idx]} {self._spin_label}"

        timer = self.set_interval(0.08, _tick)
        prompt.add_class("streaming")
        prompt.border_title = f"{SPINNER[0]} 等待回覆"
        prompt.disabled = True
        prompt.placeholder = ""

        try:
            def _at_bottom() -> bool:
                return msgs.scroll_y >= msgs.max_scroll_y - 3

            async def on_thinking(content: str) -> None:
                self._spin_label = "思考中"
                preview = content[:2000] + ("…" if len(content) > 2000 else "")
                await msgs.mount(
                    Collapsible(Static(preview, markup=False),
                                title="💡 思考過程", collapsed=True, classes="thinking")
                )
                msgs.scroll_end(animate=False)

            async def on_token(chunk: str) -> None:
                nonlocal stream_msg
                self._spin_label = "生成中"
                buf.append(chunk)
                if stream_msg is None:
                    await msgs.mount(Static(f"🤖 {self.agent.profile.name}", classes="ai-header", markup=False))
                    stream_msg = AIMessage()
                    await msgs.mount(stream_msg)
                await stream_msg.update_content("".join(buf))
                if _at_bottom():
                    msgs.scroll_end(animate=False)

            async def on_done(elapsed, thinking_s, gen_tok, prompt_tok) -> None:
                if stream_msg:
                    await stream_msg.update_content("".join(buf), final=True)
                parts: list[str] = []
                if thinking_s:
                    parts.append(f"思考 {thinking_s:.1f}s")
                parts.append(f"共 {elapsed:.1f}s")
                if gen_tok:
                    parts.append(f"{gen_tok} tokens")
                stats = " · ".join(parts)
                at_bot = _at_bottom()
                await msgs.mount(Static(stats, classes="stats", markup=False))
                self.sub_title = f"{self.agent.profile.name} · {stats}"
                self.query_one("#sidebar", Sidebar).refresh_git()
                if at_bot:
                    msgs.scroll_end(animate=False)

            async def on_tool_call(name: str, args: dict, result: str) -> None:
                self._spin_label = "工具回應中"
                if not self._debug_tools:
                    return
                args_str = json.dumps(args, ensure_ascii=False, indent=2)
                cap = 1000
                result_preview = result[:cap] + (f"\n…（共 {len(result)} 字元）" if len(result) > cap else "")
                body = f"**參數**\n```json\n{args_str}\n```\n\n**回應**\n```\n{result_preview}\n```"
                at_bot = _at_bottom()
                await msgs.mount(
                    Collapsible(
                        Markdown(body),
                        title=f"🔧 {name}",
                        collapsed=True,
                        classes="tool-debug",
                    )
                )
                if at_bot:
                    msgs.scroll_end(animate=False)

            await self.agent.chat(
                {"role": "user", "content": text},
                on_token=on_token,
                on_thinking=on_thinking,
                on_done=on_done,
                on_tool_call=on_tool_call,
            )
            if stream_msg and buf:
                await stream_msg.update_content("".join(buf), final=True)

        except Exception as e:
            logger.exception("chat error")
            await msgs.mount(Static(f"❌ 錯誤：{e}", classes="error-msg", markup=False))
            msgs.scroll_end(animate=False)

        finally:
            timer.stop()
            prompt.remove_class("streaming")
            self._update_prompt_title()
            prompt.disabled = False
            prompt.placeholder = f"傳訊息給 {self.agent.profile.name}..."
            prompt.focus()

    # ── Bash 直接執行 ─────────────────────────────────────────────────────────

    @work
    async def _run_bash(self, cmd: str) -> None:
        msgs   = self.query_one("#messages", VerticalScroll)
        prompt = self.query_one("#prompt",   HistoryInput)
        await msgs.mount(
            Static(f"[bold]$[/bold] {cmd}", markup=True, classes="user-bubble")
        )
        msgs.scroll_end(animate=False)
        prompt.disabled = True
        prompt.add_class("streaming")
        prompt.border_title = "⚙ 執行中..."
        try:
            # Force ANSI color: commands (ls, git, grep…) check these vars before emitting codes
            env = {
                **os.environ,
                "CLICOLOR_FORCE": "1",
                "COLORTERM": "truecolor",
                "TERM": "xterm-256color",
                "COLUMNS": "120",
            }
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=BASH_TIMEOUT)
            out = stdout.decode(errors="replace").strip()
            err = stderr.decode(errors="replace").strip()
            parts = []
            if out:
                parts.append(out)
            if err:
                parts.append(f"[stderr]\n{err}")
            raw = "\n".join(parts) or "(no output)"
            await msgs.mount(Static(Text.from_ansi(raw), classes="bash-output"))
        except Exception as e:
            await msgs.mount(Static(f"❌ {e}", markup=False, classes="error-msg"))
        finally:
            msgs.scroll_end(animate=False)
            prompt.disabled = False
            prompt.remove_class("streaming")
            self._update_prompt_title()
            prompt.placeholder = f"傳訊息給 {self.agent.profile.name}..."
            prompt.focus()

    def action_cancel_stream(self) -> None:
        overlay = self.query_one("#cmd-overlay", NavigableOptionList)
        if overlay.has_focus and overlay.display:
            overlay.display = False
            overlay.clear_file_mode()
            self.query_one("#prompt", HistoryInput).focus()
            return
        self.agent._cancelled = True


if __name__ == "__main__":
    ChatApp().run()
