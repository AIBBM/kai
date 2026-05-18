"""Textual TUI for qwen-agent.  Run: uv run python app.py"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from functools import partial
from pathlib import Path

from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import Hit, Hits, Provider
from textual.containers import Horizontal, VerticalScroll
from textual import events
from textual.events import Click
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.message import Message
from textual.widgets import Button, Collapsible, Footer, Header, Input, Markdown, OptionList, Static, TabbedContent, TabPane, TextArea, Tree
from textual.widgets._tree import TreeNode
from textual import work
from loguru import logger
from textual_autocomplete import AutoComplete, DropdownItem, TargetState

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
    """Input with Up/Down history navigation and Ctrl+Z undo.

    Uses _on_key (runs before the binding system) to intercept up/down before
    ScrollView's scroll_up/scroll_down bindings consume them.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._undo_stack: list[tuple[str, int]] = []
        self._multiline_paste: str | None = None   # 暫存多行貼上內容
        self._multiline_marker: str | None = None  # 對應的佔位摘要字串

    def on_paste(self, event: events.Paste) -> None:
        text = event.text
        if "\n" not in text and "\r" not in text:
            return
        event.prevent_default()
        raw = text.replace("\r\n", "\n").replace("\r", "\n").strip()

        # If this paste matches the last FileViewer copy, prepend filename + line context
        last = getattr(self.app, "_last_file_copy", None)
        if last and last.get("text", "").strip() == raw:
            p = last["path"]
            fname = p.name if p else "unknown"
            s, e = last["start_line"], last["end_line"]
            loc = f"{fname}:{s}" if s == e else f"{fname}:{s}-{e}"
            raw = f"```{p.suffix.lstrip('.') if p else ''} {loc}\n{raw}\n```"

        self._multiline_paste = raw
        lines = raw.splitlines()
        preview = lines[0][:40] + ("…" if len(lines[0]) > 40 else "")
        marker = f"[{len(lines)} 行] {preview}"
        self._multiline_marker = marker
        self.value = marker
        self.cursor_position = len(self.value)

    def _on_key(self, event: events.Key) -> None:
        if event.key == "up":
            event.prevent_default()
            event.stop()
            self.app.action_history_prev()  # type: ignore[attr-defined]
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            self.app.action_history_next()  # type: ignore[attr-defined]
        elif event.key == "ctrl+z":
            event.prevent_default()
            event.stop()
            if self._undo_stack:
                val, pos = self._undo_stack.pop()
                self.value = val
                self.cursor_position = min(pos, len(val))
        else:
            _SAVE_BEFORE = frozenset({"backspace", "delete", "ctrl+backspace", "ctrl+delete"})
            if event.key in _SAVE_BEFORE or len(event.key) == 1:
                self._undo_stack.append((self.value, self.cursor_position))
                if len(self._undo_stack) > 200:
                    del self._undo_stack[:-200]


_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".svg"})
_HIDDEN_DIRS = frozenset({"__pycache__", ".git", ".venv", "node_modules", ".mypy_cache", ".ruff_cache"})

_SUFFIX_TO_LANG: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".jsx": "javascript", ".tsx": "typescript",
    ".json": "json", ".md": "markdown", ".css": "css",
    ".html": "html", ".sh": "bash", ".bash": "bash",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".rs": "rust", ".go": "go", ".c": "c", ".cpp": "cpp",
    ".java": "java", ".sql": "sql", ".xml": "xml",
}


class ResizeHandle(Widget):
    """拖拉分隔條，調整相鄰 widget 的寬度。"""

    def __init__(self, target_id: str, min_w: int = 10, max_w: int = 80, **kwargs) -> None:
        super().__init__(**kwargs)
        self._target_id = target_id
        self._min_w = min_w
        self._max_w = max_w
        self._dragging = False

    def render(self) -> str:
        return ""

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._dragging = True
        self.capture_mouse()
        self.add_class("dragging")
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()
            self.remove_class("dragging")

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        target = self.app.query_one(f"#{self._target_id}")
        # screen_x 是游標的絕對 x 座標；target 從螢幕左側 target.region.x 開始
        # 所以新寬度 = 游標位置 - target 起點
        new_w = max(self._min_w, min(self._max_w, event.screen_x - target.region.x))
        target.styles.width = new_w


class TreeContextMenu(ModalScreen):
    """檔案樹右鍵選單。"""

    DEFAULT_CSS = """
    TreeContextMenu {
        background: transparent;
        align: left top;
    }
    TreeContextMenu OptionList {
        width: 22;
        height: auto;
        max-height: 12;
        border: round $primary;
        background: $surface;
    }
    """

    def __init__(self, path: Path | None, sx: int, sy: int) -> None:
        super().__init__()
        self._path = path
        self._sx = sx
        self._sy = sy

    def compose(self) -> ComposeResult:
        yield OptionList("複製檔名", id="ctx-options")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        x = min(self._sx, self.app.size.width - 24)
        y = min(self._sy, self.app.size.height - 6)
        ol.styles.offset = (x, y)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self._path and event.option_index == 0:
            path = self._path
            app = self.app
            app.notify(f"已複製：{_relative_display(path)}", timeout=1.5)
            self.dismiss()
            app.run_worker(lambda: _copy_filename_bg(app, path), thread=True)
        else:
            self.dismiss()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 3:
            return
        widget, _ = self.screen.get_widget_at(event.screen_x, event.screen_y)
        ol = self.query_one(OptionList)
        if ol in widget.ancestors_with_self:
            return
        new_path = self._node_at_screen(event.screen_x, event.screen_y)
        self.dismiss()
        if new_path:
            self.app.push_screen(TreeContextMenu(new_path, event.screen_x, event.screen_y))

    def _node_at_screen(self, sx: int, sy: int) -> Path | None:
        try:
            tree = self.app.query_one(RightClickTree)
            r = tree.region
            if not r.contains(sx, sy):
                return None
            line = int(sy - r.y) + int(tree.scroll_y)
            node = tree.get_node_at_line(line)
            path = node.data if node else None
            if isinstance(path, Path):
                tree.move_cursor(node)
                return path
        except Exception:
            pass
        return None

    def on_click(self, event: events.Click) -> None:
        widget, _ = self.screen.get_widget_at(event.screen_x, event.screen_y)
        ol = self.query_one(OptionList)
        if ol not in widget.ancestors_with_self:
            self.dismiss()


class RightClickTree(Tree):
    """Tree subclass that intercepts right-click before internal _on_mouse_down stops it."""

    class NodeRightClicked(Message):
        def __init__(self, path: Path, sx: int, sy: int) -> None:
            super().__init__()
            self.path = path
            self.sx = sx
            self.sy = sy

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 3:
            return
        line = int(event.y) + int(self.scroll_y)
        node = self.get_node_at_line(line)
        path = node.data if node else None
        if isinstance(path, Path):
            self.move_cursor(node)
            event.stop()
            self.post_message(self.NodeRightClicked(path, event.screen_x, event.screen_y))


class FileTree(Widget):
    """左側懶載入檔案樹；選取檔案後填入 prompt。"""

    class FileChosen(Message):
        def __init__(self, path: Path) -> None:
            super().__init__()
            self.path = path

    def compose(self) -> ComposeResult:
        cwd = Path(os.getcwd())
        tree: RightClickTree[Path] = RightClickTree(cwd.name)
        tree.root.data = cwd
        tree.root.expand()
        yield tree

    def on_mount(self) -> None:
        self._populate(self.query_one(RightClickTree).root)
        self._last_selected: Path | None = None
        self._last_select_time: float = 0.0

    def _populate(self, node: "TreeNode[Path]") -> None:
        path: Path = node.data
        if not path or not path.is_dir():
            return
        node.remove_children()
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            node.add_leaf("🔒 無權限", data=None)
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name in _HIDDEN_DIRS:
                continue
            if entry.is_dir():
                child = node.add(f"📁 {entry.name}", data=entry)
                child.add_leaf("…", data=None)        # 佔位符讓節點顯示為可展開
            else:
                icon = "🖼 " if entry.suffix.lower() in _IMAGE_EXTS else "📄 "
                node.add_leaf(f"{icon}{entry.name}", data=entry)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        if node.data and isinstance(node.data, Path) and node.data.is_dir():
            children = list(node.children)
            if len(children) == 1 and children[0].data is None:
                self._populate(node)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        path: Path | None = event.node.data
        if not path or not path.is_file():
            return
        now = time.monotonic()
        if path == self._last_selected and now - self._last_select_time < 0.4:
            self.post_message(FileTree.FileChosen(path))
            self._last_selected = None
        else:
            self._last_selected = path
            self._last_select_time = now

    def on_right_click_tree_node_right_clicked(self, event: RightClickTree.NodeRightClicked) -> None:
        self.app.push_screen(TreeContextMenu(event.path, event.sx, event.sy))

    def refresh_tree(self) -> None:
        tree = self.query_one(RightClickTree)
        cwd = Path(os.getcwd())
        tree.root.label = cwd.name
        tree.root.data = cwd
        self._populate(tree.root)
        tree.root.expand()


class ChatAutoComplete(AutoComplete):
    """動態補全：斜線指令、/add /del /image 路徑、! bash 路徑。"""

    def get_search_string(self, state: TargetState) -> str:
        text = state.text
        if text.startswith("/add "):
            return text[5:]
        if text.startswith("/del "):
            return text[5:]
        if text.startswith("/image "):
            return text[7:]
        if text.startswith("/"):
            return text
        if text.startswith("!"):
            cmd_part = text[1:]
            spaces = cmd_part.count(" ")
            tokens = cmd_part.split()
            last = tokens[-1] if tokens and not text.endswith(" ") else ""
            path_like = last and ("." in last or "/" in last or last.startswith("~"))
            if last and not last.startswith("-") and (spaces >= 2 or path_like):
                return last
        return ""

    def should_show_dropdown(self, search_string: str) -> bool:
        text = self._target_state.text
        if text.startswith(("/add ", "/del ", "/image ")):
            return self.option_list.option_count > 0
        return super().should_show_dropdown(search_string)

    def get_candidates(self, state: TargetState) -> list[DropdownItem]:
        text = state.text
        if text.startswith("/add "):
            return self._file_items(text[5:])
        if text.startswith("/image "):
            return self._image_items(text[7:])
        if text.startswith("/del "):
            ctx = getattr(self.app, "_context_files", [])
            search = text[5:].lower()
            return [
                DropdownItem(main=fp, prefix="🗑 ")
                for fp, _ in ctx
                if not search or search in fp.lower()
            ][:20]
        if text.startswith("/"):
            cmds = getattr(self.app, "SLASH_COMMANDS", [])
            return [
                DropdownItem(main=cmd)
                for cmd, desc in cmds
                if text == "/" or cmd.startswith(text) or text[1:] in cmd
            ]
        if text.startswith("!"):
            cmd_part = text[1:]
            spaces = cmd_part.count(" ")
            tokens = cmd_part.split()
            last = tokens[-1] if tokens and not text.endswith(" ") else ""
            path_like = last and ("." in last or "/" in last or last.startswith("~"))
            if last and not last.startswith("-") and (spaces >= 2 or path_like):
                return self._file_items(last)
        return []

    def _file_items(self, prefix: str) -> list[DropdownItem]:
        try:
            base = Path(prefix) if prefix else Path(".")
            search_dir = base if base.is_dir() else base.parent
            name_filter = "" if base.is_dir() else base.name.lower()
            entries = sorted(search_dir.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            return [
                DropdownItem(
                    main=str(p) + ("/" if p.is_dir() else ""),
                    prefix="📁 " if p.is_dir() else "📄 ",
                )
                for p in entries
                if p.name.lower().startswith(name_filter)
            ][:20]
        except Exception:
            return []

    def _image_items(self, prefix: str) -> list[DropdownItem]:
        try:
            base = Path(prefix) if prefix else Path(".")
            search_dir = base if base.is_dir() else base.parent
            name_filter = "" if base.is_dir() else base.name.lower()
            entries = sorted(search_dir.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            return [
                DropdownItem(
                    main=str(p) + ("/" if p.is_dir() else ""),
                    prefix="📁 " if p.is_dir() else "🖼 ",
                )
                for p in entries
                if p.name.lower().startswith(name_filter)
                and (p.is_dir() or p.suffix.lower() in _IMAGE_EXTS)
            ][:20]
        except Exception:
            return []

    def apply_completion(self, value: str, state: TargetState) -> None:
        text = state.text
        target = self.target
        if text.startswith(("/add ", "/del ", "/image ")):
            slash_end = text.index(" ") + 1
            new_val = text[:slash_end] + value
            target.value = new_val
            target.cursor_position = len(new_val)
        elif text.startswith("/"):
            target.value = value
            target.cursor_position = len(value)
        elif text.startswith("!"):
            last_space = text.rfind(" ")
            base = text[:last_space + 1] if last_space >= 0 else "!"
            new_val = base + value + ("" if value.endswith("/") else " ")
            target.value = new_val
            target.cursor_position = len(new_val)
        else:
            super().apply_completion(value, state)
        new_state = self._get_target_state()
        self._rebuild_options(new_state, self.get_search_string(new_state))

    def _align_to_target(self) -> None:
        from textual.geometry import Offset
        x, y = self.target.cursor_screen_offset
        _w, h = self.option_list.outer_size
        # outer_size is 0 before first layout; fall back to option count
        if h == 0:
            h = min(max(self.option_list.option_count, 1), 12)
        # Input lives at the bottom — always show dropdown above the cursor
        self.absolute_offset = Offset(max(0, x - 1), max(0, y - h))

    def post_completion(self) -> None:
        # 選到目錄時保持開啟繼續瀏覽
        if self.target.value.endswith("/"):
            return
        self.action_hide()


def _relative_display(path: Path) -> str:
    """回傳相對於 cwd 的路徑字串；資料夾加尾斜線。"""
    try:
        rel = path.relative_to(Path(os.getcwd()))
    except ValueError:
        rel = path
    return str(rel) + ("/" if path.is_dir() else "")


def _copy_filename_bg(app: "App", path: Path) -> None:
    """背景複製路徑，失敗才補 error toast。"""
    ok = _copy_to_clipboard(_relative_display(path))
    if not ok:
        app.call_from_thread(app.notify, "複製失敗", severity="warning", timeout=1.5)


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
        from rich.text import Text as RichText
        try:
            out = subprocess.run(
                ["git", "-c", "color.status=always", "status", "--short"],
                capture_output=True, text=True, timeout=2, cwd=os.getcwd(),
            ).stdout.strip()
            result = RichText.from_ansi(out) if out else RichText("無變更", style="dim")
        except Exception:
            result = RichText("非 git 目錄", style="dim")
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
        try:
            self.query_one("#sb-mcp", Static).update(text)
        except Exception:
            pass


class ImageBubble(Widget):
    """使用者傳送的圖片訊息：metadata card + 開啟按鈕。"""

    def __init__(self, path: str, question: str) -> None:
        super().__init__(classes="image-bubble")
        self._path = path
        self._question = question

    def compose(self) -> ComposeResult:
        from PIL import Image
        name = Path(self._path).name
        try:
            img = Image.open(self._path)
            w, h = img.size
            size_kb = Path(self._path).stat().st_size // 1024
            meta = f"{w}×{h}  {size_kb} KB"
        except Exception:
            meta = "（無法讀取）"
        yield Static(
            f"[bold]Duncan[/bold]  🖼 {name}  [dim]{meta}[/dim]",
            markup=True, classes="image-bubble-header",
        )
        yield Static(self._question, markup=False, classes="image-question")
        yield Button("🔍 開啟圖片", classes="img-open-btn", variant="default")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        await asyncio.to_thread(_open_image, self._path)


def _open_image(path: str) -> None:
    """用系統預覽器開啟圖片（WSL → cmd.exe start，Linux → xdg-open，macOS → open）。"""
    import shutil
    try:
        if shutil.which("wslpath"):                         # WSL
            r = subprocess.run(["wslpath", "-w", path],
                               capture_output=True, text=True)
            win_path = r.stdout.strip() if r.returncode == 0 else path
            # cmd.exe /c start "" "<path>" 用預設程式開啟，空字串是視窗標題佔位
            subprocess.run(["cmd.exe", "/c", "start", "", win_path],
                           check=False, capture_output=True)
        elif shutil.which("xdg-open"):                      # Linux
            subprocess.run(["xdg-open", path], check=False, capture_output=True)
        elif shutil.which("open"):                          # macOS
            subprocess.run(["open", path], check=False, capture_output=True)
    except Exception as e:
        logger.warning(f"開啟圖片失敗：{e}")


def _copy_to_clipboard(text: str) -> bool:
    """複製文字到剪貼簿。依序嘗試 xclip → clip.exe（WSL）。"""
    try:
        subprocess.run(["xclip", "-selection", "clipboard"],
                       input=text.encode(), check=True, capture_output=True, timeout=3)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    try:
        # clip.exe 需要 UTF-16-LE + BOM（\xff\xfe）才會以 Unicode 模式讀取。
        # 沒有 BOM 時會當 ANSI 解讀，中文會亂碼；
        # 有 BOM 時 \r\n 在 UTF-16-LE 中是 \x0d\x00\x0a\x00，clip.exe 可正確處理多行。
        win_text = text.replace("\n", "\r\n")
        bom = b"\xff\xfe"
        subprocess.run(["clip.exe"],
                       input=bom + win_text.encode("utf-16-le"),
                       check=True, capture_output=True, timeout=3)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return False


class FileViewer(TextArea):
    """唯讀檔案檢視器，覆寫 Ctrl+C 以支援 WSL 多行複製。"""

    def action_copy(self) -> None:
        text = self.selected_text
        if not text:
            return
        sel = self.selection
        start_line = sel.start[0] + 1   # 1-indexed
        end_line   = sel.end[0] + 1
        # parent is TabPane whose id is the tab_id key in _open_file_tabs
        tab_id = self.parent.id if self.parent else None
        file_path: Path | None = self.app._open_file_tabs.get(tab_id) if tab_id else None  # type: ignore[attr-defined]
        self.app._last_file_copy = {  # type: ignore[attr-defined]
            "text": text,
            "path": file_path,
            "start_line": start_line,
            "end_line": end_line,
        }
        self._copy_worker(text)

    @work(thread=True)
    def _copy_worker(self, text: str) -> None:
        ok = _copy_to_clipboard(text)
        msg = "已複製" if ok else "複製失敗（需要 xclip 或 clip.exe）"
        sev = "information" if ok else "warning"
        self.app.call_from_thread(self.app.notify, msg, severity=sev, timeout=1.5)


class CopyButton(Button):
    """程式碼區塊右上角的複製按鈕。"""

    def __init__(self, code: str) -> None:
        super().__init__("⎘", classes="copy-btn")
        self._code = code

    async def on_button_pressed(self) -> None:
        ok = await asyncio.to_thread(_copy_to_clipboard, self._code)
        if ok:
            self.app.notify("已複製", timeout=1.5)
        else:
            self.app.notify("複製失敗（需要 xclip 或 clip.exe）", severity="warning")


class CapabilityBadge(Button):
    """可點擊功能標籤（目前只有 think）。"""

    def __init__(self, key: str, label: str) -> None:
        super().__init__(label, id=f"cap-{key}", classes="cap-badge")
        self._key = key

    def on_button_pressed(self) -> None:
        if self._key == "think":
            self.app.action_toggle_think()  # type: ignore[attr-defined]


class CapLabel(Static):
    """純顯示功能標籤（vision 等）。"""

    def __init__(self, key: str, label: str) -> None:
        super().__init__(label, id=f"cap-{key}", classes="cap-badge cap-label")


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
            await self._add_copy_buttons()

    async def _add_copy_buttons(self) -> None:
        from textual.widgets._markdown import MarkdownFence
        md = self.query_one(".md-view", Markdown)
        for fence in md.query(MarkdownFence):
            if fence.query(CopyButton):
                continue
            if fence.code:
                await fence.mount(CopyButton(fence.code))

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
        Binding("ctrl+t", "toggle_file_tree", "檔案樹"),
        Binding("ctrl+b", "toggle_sidebar", "側邊欄"),
        Binding("ctrl+w", "close_tab", "關閉標籤", priority=True),
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
        self.scroll_sensitivity_y = 5.0
        self._spin_idx = 0
        self._spin_label = "生成中"
        self._context_files: list[tuple[str, str]] = []  # (path, content)
        self._debug_tools = False
        self._think_override: bool | None = None  # None = 依 profile 預設
        self._input_history: list[str] = _load_prompt_history()
        self._history_idx: int = -1
        self._history_draft: str = ""
        self._mcp_task: asyncio.Task | None = None
        self._mcp_done: asyncio.Event | None = None
        self._prev_cwd: str = os.getcwd()
        self._open_file_tabs: dict[str, Path] = {}
        self._file_tab_counter = 0
        self._last_file_copy: dict | None = None  # {text, path, start_line, end_line}

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
                            id="welcome", markup=True,
                        )
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
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.focus()
        self._update_prompt_title()
        self.__class__.SLASH_COMMANDS = (
            [
                ("/clear",  "清除對話記錄"),
                ("/add",    "加入檔案到上下文  /add <路徑>"),
                ("/del",    "移除上下文檔案  /del <路徑>"),
                ("/image",  "傳送圖片給模型  /image <路徑> [問題]"),
                ("/debug",  "切換工具除錯模式（顯示工具參數與回應）"),
                ("/think",  "切換 Thinking 模式開／關"),
                ("/cd",     "切換工作目錄  /cd <路徑>"),
            ]
            + [(f"/model {m}", f"切換到 {m}") for m in REGISTRY]
        )
        self._sync_cap_bar()
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
        # Strip dangling user message if interrupted mid-stream (no AI reply yet)
        hist = self.agent.history
        if hist and hist[-1]["role"] == "user":
            hist.pop()
        self.agent.save_history()
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
        # Belt-and-suspenders: if sequences arrive as bracketed paste, drop the whole paste
        if _MOUSE_SEQ.search(event.text):
            event.prevent_default()

    def on_input_changed(self, event: Input.Changed) -> None:
        # Mouse debris from Playwright/WSLg — clear immediately
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
                    self.notify(f"{self.agent.profile.name} 不支援圖片輸入", severity="error")
                else:
                    img_parts = arg.split(" ", 1)
                    img_path = img_parts[0].strip()
                    question = img_parts[1].strip() if len(img_parts) > 1 else "請分析這張圖片"
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

    def _thinking_active(self) -> bool:
        """目前是否啟用 thinking（override 優先，否則依 profile）。"""
        if self._think_override is not None:
            return self._think_override
        return self.agent.profile.supports_thinking

    def _sync_cap_bar(self) -> None:
        """同步功能標籤列的視覺狀態。"""
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
        # × is the last char of the label; Tab has padding:0 1 each side
        # so × sits at region.x + region.width - 2
        if event.screen_x >= region.x + region.width - 2:
            event.stop()
            tc = self.query_one("#center-tabs", TabbedContent)
            self._open_file_tabs.pop(tab_id, None)
            await tc.remove_pane(tab_id)

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
            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
        except Exception as e:
            self.notify(f"無法讀取 {path.name}：{e}", severity="error")
            return
        lang = _SUFFIX_TO_LANG.get(path.suffix.lower())
        ta = FileViewer(content, read_only=True, language=lang,
                        show_line_numbers=True, soft_wrap=False)
        self._open_file_tabs[tab_id] = path
        await tc.add_pane(TabPane(f"{path.name} ×", ta, id=tab_id))
        tc.active = tab_id

    async def action_close_tab(self) -> None:
        tc = self.query_one("#center-tabs", TabbedContent)
        active_id = tc.active
        if not active_id or active_id == "chat":
            return
        self._open_file_tabs.pop(active_id, None)
        await tc.remove_pane(active_id)

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.display = not sidebar.display

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
        self._context_files.clear()
        self.query_one("#sidebar", Sidebar).set_files([])
        await self.query_one("#messages", VerticalScroll).remove_children()
        self.notify(f"已切換至 {target}", severity="information")

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
        old_mcp = self.agent.mcp_client
        old_ctx = getattr(self.agent, "mcp_context", "")
        self.agent = Agent(model=model_name)
        self.agent.history = old_history
        self.agent.mcp_client = old_mcp
        self.agent.mcp_context = old_ctx
        self.agent._reset_date()
        self._think_override = None
        self._sync_cap_bar()
        prompt = self.query_one("#prompt", HistoryInput)
        prompt.placeholder = f"傳訊息給 {model_name}..."
        self.sub_title = model_name
        self.query_one("#sidebar", Sidebar).set_model(model_name)
        self.notify(f"已切換至 {model_name}", severity="information")

    # ── Streaming worker ──────────────────────────────────────────────────────

    @work(exclusive=True)
    async def _send(self, text: str, images: list[str] | None = None) -> None:
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

            _last_render: float = 0.0

            async def on_thinking(content: str) -> None:
                self._spin_label = "思考中"
                preview = content[:2000] + ("…" if len(content) > 2000 else "")
                await msgs.mount(
                    Collapsible(Static(preview, markup=False),
                                title="💡 思考過程", collapsed=True, classes="thinking")
                )
                msgs.scroll_end(animate=False)

            async def on_token(chunk: str) -> None:
                nonlocal stream_msg, _last_render
                self._spin_label = "生成中"
                buf.append(chunk)
                if stream_msg is None:
                    await msgs.mount(Static(f"🤖 {self.agent.profile.name}", classes="ai-header", markup=False))
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
                    # Model completed with no text output (tool-only turn)
                    await msgs.mount(Static("🤖 [dim]（已完成，無文字回覆）[/dim]", markup=True, classes="ai-header"))
                parts: list[str] = []
                if thinking_s:
                    parts.append(f"思考 {thinking_s:.1f}s")
                parts.append(f"共 {elapsed:.1f}s")
                if gen_tok:
                    parts.append(f"{gen_tok} tokens")
                if prompt_tok:
                    num_ctx = self.agent.profile.num_ctx
                    pct = prompt_tok * 100 // num_ctx
                    parts.append(f"ctx {pct}% ({prompt_tok}/{num_ctx})")
                stats = " · ".join(parts)
                at_bot = _at_bottom()
                await msgs.mount(Static(stats, classes="stats", markup=False))
                self.sub_title = f"{self.agent.profile.name} · {stats}"
                self.query_one("#sidebar", Sidebar).refresh_git()
                if at_bot:
                    msgs.scroll_end(animate=False)

            async def on_tool_call(name: str, args: dict, result: str) -> None:
                self._spin_label = "工具回應中"
                _ERR_KEYWORDS = ("error", "Error", "exception", "not found", "Not Found",
                                 "failed", "Failed", "errno", "traceback", "Traceback",
                                 "[stderr]", "[exit code")
                is_err = any(kw in result for kw in _ERR_KEYWORDS)
                icon = "❌" if is_err else "🔧"
                card_class = "tool-debug tool-debug-error" if is_err else "tool-debug"
                if not self._debug_tools:
                    if is_err:
                        # 即使 debug 模式關閉，錯誤仍顯示（收合）
                        await msgs.mount(
                            Collapsible(
                                Static(result[:400], markup=False),
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
                result_preview = result[:cap] + (f"\n…（共 {len(result)} 字元）" if len(result) > cap else "")
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
        # cd 是 shell built-in，子行程執行不影響 app cwd，改為直接呼叫 os.chdir
        stripped = cmd.strip()
        if stripped == "cd" or stripped.startswith("cd ") or stripped.startswith("cd\t"):
            arg = stripped[2:].strip() or str(Path.home())
            try:
                await self._do_cd(arg)
            except Exception as e:
                self.notify(f"切換失敗：{e}", severity="error")
            return

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
            self.query_one("#sidebar", Sidebar).refresh_git()
            self.query_one("#file-tree", FileTree).refresh_tree()

    def action_cancel_stream(self) -> None:
        self.agent._cancelled = True


if __name__ == "__main__":
    ChatApp().run()
