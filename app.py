"""Textual TUI for qwen-agent.  Run: uv run python app.py"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.worker import Worker

from loguru import logger
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Collapsible,
    Footer,
    Header,
    Input,
    Markdown,
    Static,
    TabbedContent,
    TabPane,
)

from core.agent import Agent
from core.config import BASH_TIMEOUT, DEBUG_TOOLS, MODEL, USERNAME
from core.mcp_client import MCPClient, load_mcp_servers
from core.models import REGISTRY
from widgets.utils import _IMAGE_EXTS, _SUFFIX_TO_LANG
from widgets.chat import AIMessage, CapabilityBadge, CapLabel, ConfirmCloseScreen, FileViewer, ImageBubble
from widgets.commands import ChatCommands
from widgets.input import ChatAutoComplete, HistoryInput
from widgets.sidebar import ResizeHandle, Sidebar
from widgets.tree import FileTree

logger.remove()
logger.add(
    "agent_tui.log",
    level="DEBUG",
    rotation="5 MB",
    retention=3,
    format="{time:HH:mm:ss} | {level:<5} | {message}",
)

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
PROMPT_HISTORY_FILE = Path(".prompt_history")
# Mouse event debris that leaks into Input when Playwright/WSLg resets terminal mouse
# tracking state. Textual consumes ESC and [ but the rest falls through as keystrokes:
#   SGR full:    ESC[<35;80;25M  →  <35;80;25M  or  35;80;25M
#   SGR partial: 80;25M
# Pattern: optional ESC, optional [, optional <, then semicolon-separated numbers + M/m
_MOUSE_SEQ = re.compile(r"(?:\x1b\[?)?<?(?:\d+;)+\d+[Mm]")


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
    for tool_name in ("query", "execute_query", "pg_query", "read_query"):
        if tool_name in mcp.tool_names:
            try:
                result = await mcp.call(tool_name, {"sql": sql})
                return f"可查詢的資料表：\n{result}"
            except Exception as e:
                logger.warning(f"schema 查詢失敗（{tool_name}）：{e}")
                break
    return ""


class ChatApp(App[None]):
    CSS_PATH = "app.tcss"
    COMMANDS = App.COMMANDS | {ChatCommands}

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("ctrl+p", "command_palette", "命令"),
        Binding("ctrl+l", "clear_screen", "清除畫面"),
        Binding("ctrl+t", "toggle_file_tree", "檔案樹"),
        Binding("ctrl+b", "toggle_sidebar", "側邊欄"),
        Binding("ctrl+w", "close_tab", "關閉標籤", priority=True),
        Binding("escape", "cancel_stream", "取消"),
    ]

    SLASH_COMMANDS: list[tuple[str, str]] = []

    def __init__(self) -> None:
        super().__init__()
        # Textual 8 的 MarkdownTableCellContents 在每個 header cell 呼叫 .with_tooltip()，
        # Tooltip 掛在 Screen._tooltips 層，累積多了會干擾佈局。直接 patch 成 no-op。
        try:
            from textual.widgets._markdown import MarkdownTableCellContents
            MarkdownTableCellContents.with_tooltip = lambda self, *a, **kw: self
        except Exception:
            pass
        self.agent = Agent(model=MODEL)
        self.scroll_sensitivity_y = 5.0
        self._spin_idx = 0
        self._spin_label = "生成中"
        self._at_mention_re = re.compile(r'(?:(?<=\s)|^)@(\S+)')
        self._debug_tools = DEBUG_TOOLS
        self._think_override: bool | None = None
        self._input_history: list[str] = _load_prompt_history()
        self._history_idx: int = -1
        self._history_draft: str = ""
        self._mcp_task: asyncio.Task | None = None
        self._mcp_done: asyncio.Event | None = None
        self._prev_cwd: str = os.getcwd()
        self._open_file_tabs: dict[str, Path] = {}
        self._file_tab_counter = 0
        self._last_file_copy: dict | None = None
        self._bash_proc: asyncio.subprocess.Process | None = None
        self._bash_worker: "Worker | None" = None  # type: ignore[name-defined]
        self._resumed_from: Path | None = None  # /resume 載入的歸檔路徑，有新訊息後清除
        self._last_ephemeral: list[dict] | None = None  # /ask 最後一輪的問答，/save 用

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="app-grid"):
            yield FileTree(id="file-tree")
            yield ResizeHandle("file-tree", id="file-tree-handle")
            with TabbedContent(initial="chat", id="center-tabs"):
                with TabPane("💬 Chat", id="chat"):
                    with VerticalScroll(id="messages"):
                        yield Static(
                            "[dim]輸入訊息開始對話[/dim]  ·  "
                            "[dim]Ctrl+P[/dim] 命令  "
                            "[dim]Ctrl+T[/dim] 檔案樹  "
                            "[dim]Ctrl+B[/dim] 側邊欄  "
                            "[dim]Ctrl+W[/dim] 關閉標籤  "
                            "[dim]右鍵 AI 訊息[/dim] 選取複製",
                            id="welcome",
                            markup=True,
                        )
            yield ResizeHandle("sidebar", id="sidebar-handle", right_anchor=True)
            yield Sidebar(id="sidebar")
        with Horizontal(id="input-row"):
            yield HistoryInput(placeholder=f"傳訊息給 {MODEL}...", id="prompt")
        yield ChatAutoComplete(target="#prompt", candidates=None)
        with Horizontal(id="cap-bar"):
            yield CapabilityBadge("think", "think")
            yield CapLabel("vision", "vision")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "qwen-agent"
        self.sub_title = MODEL
        self.query_one("#prompt", HistoryInput).focus()
        self._update_prompt_title()
        self._render_history()
        self.__class__.SLASH_COMMANDS = [
            ("/clear", "清除畫面"),
            ("/ask", "臨時提問，不污染對話歷史  /ask <問題>"),
            ("/save", "將上一次 /ask 的回答存入對話歷史"),
            ("/cls", "清除記憶並歸檔（可 /resume 恢復）"),
            ("/resume", "列出歸檔 session  /resume [編號]"),
            ("/remember", "記住規則  /remember <內容>"),
            ("/compact", "用 Gemini 壓縮對話歷史（釋放 context）"),
            ("/image", "傳送圖片給模型  /image <路徑> [問題]"),
            ("/debug", "切換工具除錯模式（顯示工具參數與回應）"),
            ("/think", "切換 Thinking 模式開／關"),
            ("/cd", "切換工作目錄  /cd <路徑>"),
        ] + [(f"/model {m}", f"切換到 {m}") for m in REGISTRY]
        self._sync_cap_bar()
        self.set_interval(15, lambda: self.query_one("#sidebar", Sidebar).refresh_git())
        self._mcp_done = asyncio.Event()
        self._mcp_task = asyncio.get_event_loop().create_task(self._mcp_lifecycle())

    @work
    async def _render_history(self) -> None:
        """把 agent.history 渲染到 chat 視窗（啟動 / /cd 後呼叫）。"""
        msgs = self.query_one("#messages", VerticalScroll)
        for msg in self.agent.history:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "system":
                continue
            if role == "user":
                if isinstance(content, list):
                    text = next(
                        (p.get("text", "") for p in content if p.get("type") == "text"),
                        "",
                    )
                else:
                    text = content
                if text:
                    await msgs.mount(
                        Static(
                            f"[bold]{USERNAME}[/bold]\n{text}",
                            markup=True,
                            classes="user-bubble",
                        )
                    )
            elif role == "assistant" and content:
                await msgs.mount(
                    Static(
                        f"🤖 {self.agent.profile.name}",
                        classes="ai-header",
                        markup=False,
                    )
                )
                ai_msg = AIMessage()
                await msgs.mount(ai_msg)
                await ai_msg.update_content(content, final=True)
        msgs.scroll_end(animate=False)

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
            fs_paths = [
                arg
                for name, cfg in servers.items()
                if not name.startswith("_") and cfg.get("command") in ("npx", "node")
                for arg in cfg.get("args", [])
                if arg.startswith("/")
            ]
            if fs_paths:
                self.agent.mcp_context += f"\n檔案系統可存取目錄：{', '.join(fs_paths)}"
            self.agent._rebuild_system_prompt()
            sidebar.set_mcp(mcp.server_summary)
            logger.info(f"MCP 初始化完成：{mcp.tool_names}")
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
        hist = self.agent.history
        if hist and hist[-1]["role"] == "user":
            hist.pop()
        self.agent.save_history()
        if self._bash_proc and self._bash_proc.returncode is None:
            try:
                os.killpg(os.getpgid(self._bash_proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.exit()

    def on_unmount(self) -> None:
        pass

    # ── 輸入事件 ──────────────────────────────────────────────────────────────

    def action_history_prev(self) -> None:
        prompt = self.query_one("#prompt", HistoryInput)
        if not prompt.has_focus or prompt.disabled:
            return
        ac = self.query_one(ChatAutoComplete)
        if ac.display:
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
        ac = self.query_one(ChatAutoComplete)
        if ac.display:
            return
        if self._history_idx == -1:
            return
        self._history_idx -= 1
        if self._history_idx == -1:
            prompt.value = self._history_draft
        else:
            prompt.value = self._input_history[-(self._history_idx + 1)]
        prompt.cursor_position = len(prompt.value)

    def on_paste(self, event: events.Paste) -> None:
        if _MOUSE_SEQ.search(event.text):
            event.prevent_default()

    def on_input_changed(self, event: Input.Changed) -> None:
        if _MOUSE_SEQ.search(event.value):
            event.input.value = ""
            event.input.cursor_position = 0

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = self.query_one("#prompt", HistoryInput)
        if prompt._multiline_paste is not None:
            paste = prompt._multiline_paste
            marker = prompt._multiline_marker or ""
            prompt._multiline_paste = None
            prompt._multiline_marker = None
            raw_value = event.value
            if marker and marker in raw_value:
                text = raw_value.replace(marker, paste, 1).strip()
            else:
                text = paste
        else:
            text = event.value.strip()
        if not text:
            return
        event.input.clear()
        if not self._input_history or self._input_history[-1] != text:
            self._input_history.append(text)
            _append_prompt_history(text)
        self._history_idx = -1
        self._history_draft = ""

        if text.startswith("!"):
            cmd = text[1:].strip()
            if cmd:
                self._bash_worker = self._run_bash(cmd)
            return

        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            if cmd == "/ask":
                if not arg:
                    self.notify("用法：/ask <問題>", severity="warning")
                else:
                    self._send(arg, ephemeral=True)
            elif cmd == "/save":
                if not self._last_ephemeral:
                    self.notify("沒有可保存的 /ask 回答", severity="warning")
                else:
                    self.agent.history.extend(self._last_ephemeral)
                    self.agent.save_history()
                    self._last_ephemeral = None
                    self.notify("已存入對話歷史", timeout=2)
            elif cmd == "/clear":
                await self.action_clear_screen()
            elif cmd == "/cls":
                await self.action_clear()
            elif cmd == "/resume":
                await self._handle_resume(arg)
            elif cmd == "/remember":
                if not arg:
                    self.notify("用法：/remember <要記住的內容>", severity="warning")
                else:
                    notes_path = Path(os.getcwd()) / "AGENT_NOTES.md"
                    with open(notes_path, "a", encoding="utf-8") as _f:
                        _f.write(f"\n- {arg}")
                    self.agent._rebuild_system_prompt()
                    self.notify("✅ 已記錄到 AGENT_NOTES.md", severity="information")
                    self.query_one("#file-tree", FileTree).refresh_tree()
            elif cmd == "/compact":
                await self.action_compact()
            elif cmd == "/model" and arg:
                await self.action_switch_model(arg)
            elif cmd == "/model":
                self.notify("用法：/model <模型名稱>", severity="warning")
            elif cmd == "/debug":
                self._debug_tools = not self._debug_tools
                label = "🔧 開啟" if self._debug_tools else "關閉"
                self.notify(f"工具除錯模式：{label}", severity="information", timeout=3)
                self._update_prompt_title()
            elif cmd == "/think":
                self.action_toggle_think()
            elif cmd == "/cd":
                if arg:
                    try:
                        await self._do_cd(arg)
                    except Exception as e:
                        self.notify(f"切換失敗：{e}", severity="error")
                else:
                    self.notify(f"目前目錄：{os.getcwd()}", severity="information")
            elif cmd == "/image":
                if not arg:
                    self.notify("用法：/image <圖片路徑> [問題]", severity="warning")
                elif not self.agent.profile.supports_vision:
                    self.notify(
                        f"{self.agent.profile.name} 不支援圖片輸入", severity="error"
                    )
                else:
                    img_parts = arg.split(" ", 1)
                    img_path = img_parts[0].strip()
                    question = (
                        img_parts[1].strip() if len(img_parts) > 1 else "請分析這張圖片"
                    )
                    p = Path(img_path).expanduser().resolve()
                    if not p.exists():
                        self.notify(f"找不到圖片：{img_path}", severity="error")
                    else:
                        msgs = self.query_one("#messages", VerticalScroll)
                        await msgs.mount(ImageBubble(str(p), question))
                        msgs.scroll_end(animate=False)
                        self._send(question, images=[str(p)])
            else:
                self.notify(f"未知指令：{cmd}", severity="warning")
            return

        msgs = self.query_one("#messages", VerticalScroll)
        await msgs.mount(
            Static(f"[bold]{USERNAME}[/bold]\n{text}", markup=True, classes="user-bubble")
        )
        msgs.scroll_end(animate=False)

        at_files = self._parse_at_files(text)
        if at_files:
            file_blocks = "\n\n".join(
                f"# 檔案：{p}\n```\n{c}\n```" for p, c in at_files
            )
            full_text = f"{file_blocks}\n\n{text}"
        else:
            full_text = text

        self._send(full_text)

    # ── Actions ───────────────────────────────────────────────────────────────

    _AT_SKIP_DIRS = frozenset({
        ".git", ".venv", "venv", "env", "__pycache__",
        ".mypy_cache", ".ruff_cache", ".pytest_cache",
        "dist", "build", ".next", ".nuxt",
        "node_modules", ".idea", ".vscode",
    })
    _AT_SKIP_SUFFIXES = frozenset({
        ".pyc", ".pyo", ".so", ".dll", ".exe",
        ".o", ".a", ".lib", ".obj",
    })

    def _dir_tree(self, root: Path, prefix: str = "", depth: int = 0) -> list[str]:
        if depth > 4:
            return [prefix + "…"]
        lines: list[str] = []
        try:
            entries = sorted(root.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except PermissionError:
            return [prefix + "(permission denied)"]
        for entry in entries:
            if entry.name.startswith(".") and entry.name not in {".env", ".env.local"}:
                continue
            if entry.name in self._AT_SKIP_DIRS:
                continue
            if entry.is_file() and entry.suffix.lower() in self._AT_SKIP_SUFFIXES:
                continue
            connector = "├── " if entry != entries[-1] else "└── "
            lines.append(prefix + connector + entry.name + ("/" if entry.is_dir() else ""))
            if entry.is_dir():
                ext = "│   " if entry != entries[-1] else "    "
                lines.extend(self._dir_tree(entry, prefix + ext, depth + 1))
        return lines

    def _parse_at_files(self, text: str) -> list[tuple[str, str]]:
        """解析訊息中的 @path：檔案注入內容，目錄注入 tree 結構。"""
        files: list[tuple[str, str]] = []
        seen: set[str] = set()
        for m in self._at_mention_re.finditer(text):
            raw = m.group(1)
            try:
                p = Path(raw).expanduser().resolve()
                if str(p) in seen:
                    continue
                seen.add(str(p))
                if p.is_file():
                    files.append((str(p), p.read_text(encoding="utf-8", errors="replace")))
                elif p.is_dir():
                    tree_lines = [p.name + "/"] + self._dir_tree(p)
                    files.append((str(p), "[目錄結構]\n" + "\n".join(tree_lines)))
            except Exception:
                pass
        return files

    def _update_prompt_title(self) -> None:
        base = f"[bold]{USERNAME}[/bold] [dim]❯[/dim]"
        suffix = "  [dim]🔧 debug[/dim]" if self._debug_tools else ""
        self.query_one("#prompt", HistoryInput).border_title = base + suffix

    def _thinking_active(self) -> bool:
        if self._think_override is not None:
            return self._think_override
        return self.agent.profile.supports_thinking

    def _sync_cap_bar(self) -> None:
        profile = self.agent.profile
        thinking_on = self._thinking_active()

        think = self.query_one("#cap-think", CapabilityBadge)
        think.remove_class("cap-think-on", "cap-inactive", "cap-unsupported")
        if thinking_on:
            think.add_class("cap-think-on")
        elif not profile.supports_thinking:
            think.add_class("cap-unsupported")
        else:
            think.add_class("cap-inactive")

        vision = self.query_one("#cap-vision", CapLabel)
        vision.display = bool(profile.supports_vision)

    def action_toggle_think(self) -> None:
        current = self._thinking_active()
        self._think_override = not current
        self._sync_cap_bar()
        label = "開啟 💡" if self._think_override else "關閉"
        self.notify(f"Thinking：{label}", severity="information", timeout=2)

    def action_toggle_file_tree(self) -> None:
        ft = self.query_one("#file-tree", FileTree)
        ft.display = not ft.display
        self.query_one("#file-tree-handle", ResizeHandle).display = ft.display

    async def on_click(self, event: events.Click) -> None:
        widget, region = self.screen.get_widget_at(event.screen_x, event.screen_y)
        wid = widget.id or "" if widget else ""
        if not wid.startswith("--content-tab-ft-"):
            return
        tab_id = wid[len("--content-tab-"):]
        if event.screen_x >= region.x + region.width - 2:
            event.stop()
            await self._maybe_close_tab(tab_id)

    def on_file_tree_file_chosen(self, event: FileTree.FileChosen) -> None:
        path = event.path
        if path.suffix.lower() in _IMAGE_EXTS:
            prompt = self.query_one("#prompt", HistoryInput)
            prompt.value = f"/image {path} "
            prompt.cursor_position = len(prompt.value)
            prompt.focus()
        else:
            self._open_file_tab(path)

    @work
    async def _open_file_tab(self, path: Path) -> None:
        tc = self.query_one("#center-tabs", TabbedContent)
        for tid, p in self._open_file_tabs.items():
            if p == path:
                tc.active = tid
                return
        self._file_tab_counter += 1
        tab_id = f"ft-{self._file_tab_counter}"
        try:
            content = await asyncio.to_thread(
                path.read_text, encoding="utf-8", errors="replace"
            )
        except Exception as e:
            self.notify(f"無法讀取 {path.name}：{e}", severity="error")
            return
        lang = _SUFFIX_TO_LANG.get(path.suffix.lower())
        ta = FileViewer(
            content,
            language=lang,
            show_line_numbers=True,
            soft_wrap=False,
            file_path=path,
        )
        self._open_file_tabs[tab_id] = path
        await tc.add_pane(TabPane(f"{path.name} ×", ta, id=tab_id))
        tc.active = tab_id

    async def action_close_tab(self) -> None:
        tc = self.query_one("#center-tabs", TabbedContent)
        active_id = tc.active
        if not active_id or active_id == "chat":
            return
        await self._maybe_close_tab(active_id)

    async def _maybe_close_tab(self, tab_id: str) -> None:
        """關閉 tab，若有未存檔修改則先以 modal 詢問確認。"""
        try:
            pane = self.query_one(f"TabPane#{tab_id}")
            viewer = pane.query_one(FileViewer)
            if viewer._modified:
                fname = viewer._file_path.name if viewer._file_path else "檔案"

                def _on_confirm(confirmed: bool | None) -> None:
                    if confirmed:
                        self._open_file_tabs.pop(tab_id, None)
                        self.query_one("#center-tabs", TabbedContent).remove_pane(tab_id)

                self.push_screen(ConfirmCloseScreen(fname), callback=_on_confirm)
                return  # 等 callback 決定是否關閉
        except Exception:
            pass
        self._open_file_tabs.pop(tab_id, None)
        await self.query_one("#center-tabs", TabbedContent).remove_pane(tab_id)

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.display = not sidebar.display
        self.query_one("#sidebar-handle", ResizeHandle).display = sidebar.display

    async def _do_cd(self, arg: str) -> None:
        """切換工作目錄：存舊 history → chdir → 重載新 history → 清 UI。"""
        target = Path(self._prev_cwd if arg == "-" else arg).expanduser().resolve()
        if not target.is_dir():
            self.notify(f"目錄不存在：{target}", severity="error")
            return
        self.agent.save_history()
        self._prev_cwd = os.getcwd()
        os.chdir(target)
        self.query_one("#sb-cwd", Static).update(str(target))
        self.query_one("#file-tree", FileTree).refresh_tree()
        self.agent.reset_history()
        await self.query_one("#messages", VerticalScroll).remove_children()
        self._render_history()
        self.notify(f"已切換至 {target}", severity="information")

    async def action_compact(self) -> None:
        if not self.agent._gemini and not self.agent._deepseek:
            self.notify("需要 GEMINI_API_KEY 或 DEEPSEEK_API_KEY 才能使用 compact", severity="warning")
            return
        msgs = self.query_one("#messages", VerticalScroll)
        await msgs.mount(Static("⚡ 正在用 Gemini 壓縮對話歷史…", classes="error-msg", markup=False))
        msgs.scroll_end(animate=False)
        try:
            await self.agent.compact_history()
            await msgs.remove_children()
            self._render_history()
            self.notify("✅ 對話已壓縮", severity="information")
        except Exception as e:
            self.notify(f"compact 失敗：{e}", severity="error")

    async def action_clear_screen(self) -> None:
        await self.query_one("#messages", VerticalScroll).remove_children()

    async def action_clear(self) -> None:
        real_msgs = [m for m in self.agent.history if m.get("role") != "system"]
        if not real_msgs:
            self.notify("沒有對話記錄，無需清除", severity="information", timeout=2)
            return
        await self.query_one("#messages", VerticalScroll).remove_children()
        hist_file = Path(os.getcwd()) / "history.json"
        if hist_file.exists() and self._resumed_from is None:
            from datetime import datetime
            hist_dir = Path(os.getcwd()) / ".history"
            hist_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            hist_file.rename(hist_dir / f"history_{ts}.json")
            self.notify(f"歷史已歸檔至 .history/history_{ts}.json", timeout=3)
        elif self._resumed_from is not None:
            hist_file.unlink(missing_ok=True)
            self.notify("已清除（resume 後未新增對話，不重複歸檔）", timeout=3)
        self.agent.history = []
        self.agent._rebuild_system_prompt()
        self.agent.save_history()
        self.sub_title = self.agent.profile.name
        self.query_one("#file-tree", FileTree).refresh_tree()

    async def _handle_resume(self, arg: str) -> None:
        import json as _json
        cwd = Path(os.getcwd())
        hist_dir = cwd / ".history"
        archives = sorted(hist_dir.glob("history_*.json"), reverse=True) if hist_dir.exists() else []
        if not archives:
            self.notify("沒有任何歸檔 session", severity="warning")
            return
        if not arg:
            lines = []
            for i, p in enumerate(archives, 1):
                try:
                    msgs = _json.loads(p.read_text(encoding="utf-8"))
                    user_msgs = [m for m in msgs if m.get("role") == "user"]
                    count = len(user_msgs)
                    first = user_msgs[0]["content"] if user_msgs else ""
                    if isinstance(first, list):
                        first = next((b["text"] for b in first if b.get("type") == "text"), "")
                    preview = str(first)[:50].replace("\n", " ")
                    # 從檔名解析時間 history_YYYYMMDD_HHMMSS.json
                    stem = p.stem  # history_20260520_153022
                    try:
                        dt = stem[8:]  # 20260520_153022
                        ts_display = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]} {dt[9:11]}:{dt[11:13]}:{dt[13:15]}"
                    except Exception:
                        ts_display = stem
                except Exception:
                    count, preview, ts_display = 0, "(無法讀取)", p.name
                lines.append(f"[{i}] {ts_display}  {count} 則對話  {preview}…")
            msgs_area = self.query_one("#messages", VerticalScroll)
            await msgs_area.mount(Static("\n".join(lines)))
            self.notify("/resume <編號> 載入對應 session", timeout=5)
            return
        try:
            idx = int(arg) - 1
            archive = archives[idx]
        except (ValueError, IndexError):
            self.notify(f"編號無效，共 {len(archives)} 個歸檔", severity="error")
            return
        try:
            import shutil
            shutil.copy2(archive, cwd / "history.json")
        except Exception as e:
            self.notify(f"載入失敗：{e}", severity="error")
            return
        self.query_one("#messages", VerticalScroll).remove_children()
        self.agent.reset_history()
        self._render_history()
        self._resumed_from = archive
        self.notify(f"已載入：{archive.name}（有新對話後才會重新歸檔）", timeout=4)

    async def action_switch_model(self, model_name: str) -> None:
        if model_name not in REGISTRY:
            self.notify(f"未知模型：{model_name}", severity="error")
            return
        if model_name == self.agent.profile.name:
            self.notify("已是目前模型", severity="information")
            return
        old_history = self.agent.history[:]
        old_mcp = self.agent.mcp_client
        old_ctx = getattr(self.agent, "mcp_context", "")
        self.agent = Agent(model=model_name)
        self.agent.history = old_history
        self.agent.mcp_client = old_mcp
        self.agent.mcp_context = old_ctx
        self.agent._rebuild_system_prompt()
        self._think_override = None
        self._sync_cap_bar()
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.placeholder = f"傳訊息給 {model_name}..."
        self.sub_title = model_name
        self.query_one("#sidebar", Sidebar).set_model(model_name)
        self.notify(f"已切換至 {model_name}", severity="information")

    # ── Streaming worker ──────────────────────────────────────────────────────

    @work(exclusive=True)
    async def _send(self, text: str, images: list[str] | None = None, ephemeral: bool = False) -> None:
        msgs = self.query_one("#messages", VerticalScroll)
        prompt = self.query_one("#prompt", HistoryInput)
        stream_msg: AIMessage | None = None
        buf: list[str] = []

        self.agent._cancelled = False
        if not ephemeral:
            self._resumed_from = None  # 有新訊息，解除 resume 保護
        _history_snapshot = [m.copy() for m in self.agent.history] if ephemeral else None
        self._spin_idx = 0
        self._spin_label = "等待回覆"

        def _tick() -> None:
            self._spin_idx = (self._spin_idx + 1) % len(SPINNER)
            dbg = "  [dim]🔧 debug[/dim]" if self._debug_tools else ""
            prompt.border_title = f"{SPINNER[self._spin_idx]} {self._spin_label}{dbg}"

        timer = self.set_interval(0.08, _tick)
        prompt.add_class("streaming")
        prompt.border_title = f"{SPINNER[0]} 等待回覆"
        prompt.disabled = True
        prompt.placeholder = ""

        try:

            def _at_bottom() -> bool:
                return msgs.scroll_y >= msgs.max_scroll_y - 3

            _last_render: float = 0.0

            async def on_thinking(content: str) -> None:
                self._spin_label = "思考中"
                preview = content[:2000] + ("…" if len(content) > 2000 else "")
                await msgs.mount(
                    Collapsible(
                        Static(preview, markup=False),
                        title="💡 思考過程",
                        collapsed=True,
                        classes="thinking",
                    )
                )
                msgs.scroll_end(animate=False)

            async def on_token(chunk: str) -> None:
                nonlocal stream_msg, _last_render
                self._spin_label = "生成中"
                buf.append(chunk)
                if stream_msg is None:
                    await msgs.mount(
                        Static(
                            f"🤖 {self.agent.profile.name}",
                            classes="ai-header",
                            markup=False,
                        )
                    )
                    stream_msg = AIMessage()
                    await msgs.mount(stream_msg)
                now = asyncio.get_event_loop().time()
                if now - _last_render >= 0.1:
                    _last_render = now
                    await stream_msg.update_content("".join(buf))
                    if _at_bottom():
                        msgs.scroll_end(animate=False)

            async def on_done(elapsed, thinking_s, gen_tok, prompt_tok) -> None:
                if stream_msg:
                    await stream_msg.update_content("".join(buf), final=True)
                elif not buf:
                    await msgs.mount(
                        Static(
                            "🤖 [dim]（已完成，無文字回覆）[/dim]",
                            markup=True,
                            classes="ai-header",
                        )
                    )
                parts: list[str] = []
                if thinking_s:
                    parts.append(f"思考 {thinking_s:.1f}s")
                parts.append(f"共 {elapsed:.1f}s")
                if gen_tok:
                    parts.append(f"{gen_tok} tokens")
                if prompt_tok:
                    if self.agent.profile.backend == "ollama":
                        num_ctx = self.agent.profile.num_ctx
                        pct = prompt_tok * 100 // num_ctx
                        parts.append(f"ctx {pct}% ({prompt_tok}/{num_ctx})")
                    else:
                        parts.append(f"輸入 {prompt_tok} tokens")
                cache_hit = self.agent._last_cache_hit
                if cache_hit is not None and prompt_tok:
                    hit_pct = cache_hit * 100 // prompt_tok
                    parts.append(f"cache {hit_pct}%")
                stats = " · ".join(parts)
                at_bot = _at_bottom()
                await msgs.mount(Static(stats, classes="stats", markup=False))
                self.sub_title = f"{self.agent.profile.name} · {stats}"
                self.query_one("#sidebar", Sidebar).refresh_git()
                self.query_one("#file-tree", FileTree).refresh_tree()
                if at_bot:
                    msgs.scroll_end(animate=False)

            async def on_tool_call(name: str, args: dict, result: str) -> None:
                nonlocal stream_msg, buf
                self._spin_label = "工具回應中"
                # 工具卡出現前先把目前的回覆定稿，讓下一輪在工具卡之後建新的 AIMessage
                if stream_msg and buf:
                    await stream_msg.update_content("".join(buf), final=True)
                stream_msg = None
                buf.clear()
                # 只比對工具層自己產生的錯誤前綴，避免檔案內容含 "error" 字串誤判
                _ERR_PREFIXES = (
                    "[MCP Error]",
                    "[Error]",
                    "[ValidationError]",
                    "[stderr]",
                    "Traceback (most recent call last)",
                )
                _exit_m = __import__("re").search(r"\[exit code (\d+)\]", result)
                _exit_code = int(_exit_m.group(1)) if _exit_m else 0
                is_err = _exit_code >= 2 or any(kw in result for kw in _ERR_PREFIXES)
                is_warn = not is_err and _exit_code == 1
                # 檔案系統變更工具：成功時刷新檔案樹
                _FS_MUTATING = {"write_file", "create_directory", "move_file", "delete_file", "edit_file"}
                if not is_err and not is_warn and name in _FS_MUTATING:
                    self.query_one("#file-tree", FileTree).refresh_tree()
                icon = "❌" if is_err else ("⚠️" if is_warn else "🔧")
                card_class = "tool-debug tool-debug-error" if is_err else "tool-debug"
                if not self._debug_tools:
                    if is_err:
                        args_preview = json.dumps(args, ensure_ascii=False)
                        if len(args_preview) > 300:
                            args_preview = args_preview[:300] + "…"
                        body = f"參數：{args_preview}\n\n{result[:600]}"
                        await msgs.mount(
                            Collapsible(
                                Static(body, markup=False),
                                title=f"{icon} {name}  [dim]工具錯誤[/dim]",
                                collapsed=True,
                                classes=card_class,
                            )
                        )
                        if _at_bottom():
                            msgs.scroll_end(animate=False)
                    return
                args_str = json.dumps(args, ensure_ascii=False, indent=2)
                cap = 1000
                result_preview = result[:cap] + (
                    f"\n…（共 {len(result)} 字元）" if len(result) > cap else ""
                )
                body = f"**參數**\n```json\n{args_str}\n```\n\n**回應**\n```\n{result_preview}\n```"
                at_bot = _at_bottom()
                await msgs.mount(
                    Collapsible(
                        Markdown(body),
                        title=f"{icon} {name}",
                        collapsed=not is_err,
                        classes=card_class,
                    )
                )
                if at_bot:
                    msgs.scroll_end(animate=False)

            async def on_retry() -> None:
                nonlocal stream_msg, buf
                buf.clear()
                if stream_msg:
                    await stream_msg.update_content("")

            self.agent.profile.supports_thinking = self._thinking_active()
            user_msg: dict = {"role": "user", "content": text}
            if images:
                user_msg["images"] = images
            await self.agent.chat(
                user_msg,
                on_token=on_token,
                on_thinking=on_thinking,
                on_done=on_done,
                on_tool_call=on_tool_call,
                on_retry=on_retry,
            )
            if stream_msg and buf:
                await stream_msg.update_content("".join(buf), final=True)

        except Exception as e:
            err_str = str(e).lower()
            if any(kw in err_str for kw in ("context", "too long", "window", "kv cache")) and (self.agent._gemini or self.agent._deepseek):
                # Context overflow → auto compact via gemini-2.0-flash，然後重試
                logger.warning(f"context overflow，啟動 autocompact：{e}")
                try:
                    await msgs.mount(Static("⚡ Context 已滿，正在用 Gemini 壓縮對話歷史…", classes="error-msg", markup=False))
                    msgs.scroll_end(animate=False)
                    await self.agent.compact_history()
                    logger.info("autocompact 完成，重試對話")
                    await msgs.mount(Static("✅ 已壓縮，繼續對話", classes="error-msg", markup=False))
                    msgs.scroll_end(animate=False)
                    stream_msg = None
                    buf.clear()
                    await self.agent.chat(
                        user_msg,
                        on_token=on_token,
                        on_thinking=on_thinking,
                        on_done=on_done,
                        on_tool_call=on_tool_call,
                        on_retry=on_retry,
                    )
                    if stream_msg and buf:
                        await stream_msg.update_content("".join(buf), final=True)
                except Exception as e2:
                    logger.exception("compact + retry error")
                    await msgs.mount(Static(f"❌ 壓縮後仍失敗：{e2}", classes="error-msg", markup=False))
                    msgs.scroll_end(animate=False)
            elif isinstance(e, TimeoutError):
                logger.error(f"Ollama stream timeout：{e}")
                await msgs.mount(Static(f"⏱ Ollama 無回應（逾時）：{e}", classes="error-msg", markup=False))
                msgs.scroll_end(animate=False)
            else:
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
            if ephemeral and _history_snapshot is not None:
                new_msgs = self.agent.history[len(_history_snapshot):]
                self._last_ephemeral = new_msgs if new_msgs else None
                self.agent.history = _history_snapshot
                self.agent.save_history()
                if self._last_ephemeral:
                    self.notify("💾 /save 可將此回答存入對話歷史", timeout=4)

    # ── Bash 直接執行 ─────────────────────────────────────────────────────────

    @work
    async def _run_bash(self, cmd: str) -> None:
        stripped = cmd.strip()
        if (
            stripped == "cd"
            or stripped.startswith("cd ")
            or stripped.startswith("cd\t")
        ):
            arg = stripped[2:].strip() or str(Path.home())
            try:
                await self._do_cd(arg)
            except Exception as e:
                self.notify(f"切換失敗：{e}", severity="error")
            return

        msgs = self.query_one("#messages", VerticalScroll)
        prompt = self.query_one("#prompt", HistoryInput)
        await msgs.mount(
            Static(f"[bold]$[/bold] {cmd}", markup=True, classes="user-bubble")
        )
        msgs.scroll_end(animate=False)
        prompt.disabled = True
        prompt.add_class("streaming")
        prompt.border_title = "⚙ 執行中..."
        try:
            env = {
                **os.environ,
                "CLICOLOR_FORCE": "1",
                "COLORTERM": "truecolor",
                "TERM": "xterm-256color",
                "COLUMNS": "120",
            }
            proc = await asyncio.create_subprocess_exec(
                "/bin/bash", "-c", cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
            self._bash_proc = proc
            out_widget = Static(Text(""), markup=False, classes="bash-output")
            await msgs.mount(out_widget)
            lines: list[str] = []

            def _refresh() -> None:
                raw = "".join(lines) or "(no output)"
                try:
                    out_widget.update(Text.from_ansi(raw))
                except Exception:
                    out_widget.update(Text(raw))
                msgs.scroll_end(animate=False)

            async def _read(stream: asyncio.StreamReader, prefix: str = "") -> None:
                async for line in stream:
                    lines.append(prefix + line.decode(errors="replace"))
                    _refresh()

            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        _read(proc.stdout),
                        _read(proc.stderr, "[stderr] "),
                        proc.wait(),
                    ),
                    timeout=BASH_TIMEOUT,
                )
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                await proc.communicate()
                lines.append(f"\n⏱ 逾時（>{BASH_TIMEOUT}s）")
                _refresh()
        except Exception as e:
            await msgs.mount(
                Static(f"❌ {e or type(e).__name__}", markup=False, classes="error-msg")
            )
        finally:
            msgs.scroll_end(animate=False)
            prompt.disabled = False
            prompt.remove_class("streaming")
            self._update_prompt_title()
            prompt.placeholder = f"傳訊息給 {self.agent.profile.name}..."
            prompt.focus()
            self._bash_proc = None
            self._bash_worker = None
            self.query_one("#sidebar", Sidebar).refresh_git()
            self.query_one("#file-tree", FileTree).refresh_tree()

    def action_cancel_stream(self) -> None:
        self.agent._cancelled = True
        if self._bash_proc and self._bash_proc.returncode is None:
            try:
                os.killpg(os.getpgid(self._bash_proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        if self._bash_worker and self._bash_worker.is_running:
            self._bash_worker.cancel()


if __name__ == "__main__":
    ChatApp().run()
