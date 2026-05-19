"""Chat display widgets: ImageBubble, FileViewer, CopyButton, CapabilityBadge, CapLabel, AIMessage."""
from __future__ import annotations

import asyncio
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.events import Click
from textual.widget import Widget
from textual.widgets import Button, Markdown, Static, TextArea

from .utils import _copy_to_clipboard, _line_height, _open_image


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
            markup=True,
            classes="image-bubble-header",
        )
        yield Static(self._question, markup=False, classes="image-question")
        yield Button("🔍 開啟圖片", classes="img-open-btn", variant="default")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        await asyncio.to_thread(_open_image, self._path)


class FileViewer(TextArea):
    """唯讀檔案檢視器，覆寫 Ctrl+C 以支援 WSL 多行複製。"""

    def action_copy(self) -> None:
        text = self.selected_text
        if not text:
            return
        sel = self.selection
        start_line = sel.start[0] + 1
        end_line = sel.end[0] + 1
        tab_id = self.parent.id if self.parent else None
        file_path: Path | None = (
            self.app._open_file_tabs.get(tab_id) if tab_id else None  # type: ignore[attr-defined]
        )
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
            self.app.query_one("#prompt").focus()
        else:
            ta = self.query_one(".ta-view", TextArea)
            ta.language = "markdown"
            ta.text = self._raw
            ta.styles.height = max(4, min(_line_height(self._raw) + 1, 50))
            self.add_class("select-mode")
            ta.focus()
            self.app.notify(
                "選取模式：拖曳選取 → Ctrl+C 複製  ·  右鍵返回",
                severity="information",
                timeout=3,
            )
