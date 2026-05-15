"""Textual TUI for qwen-agent.  Run: uv run python app.py"""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Collapsible, Footer, Header, Input, Label, Markdown, Rule, Static
from textual import work
from loguru import logger

from agent import Agent
from config import MODEL

logger.remove()
logger.add(
    "agent_tui.log",
    level="DEBUG",
    rotation="5 MB",
    format="{time:HH:mm:ss} | {level:<5} | {message}",
)

CSS = """
/* ─── 訊息捲動區 ─── */
#messages {
    height: 1fr;
    padding: 0 2 1 2;
    scrollbar-gutter: stable;
}

/* 歡迎提示 */
#welcome {
    border: round $surface;
    color: $text-muted;
    padding: 1 2;
    margin: 1 0;
    text-align: center;
}

/* 使用者訊息 */
.user-bubble {
    border-left: thick $primary;
    background: $primary-background;
    padding: 0 1;
    margin: 1 0 0 0;
}

/* 思考面板 */
.thinking {
    border: round $warning;
    margin: 1 0 0 0;
}

/* AI 分隔線 */
Rule.ai-sep {
    color: $success;
    margin: 1 0 0 0;
}

/* AI Markdown 回應 */
Markdown.ai-msg {
    padding: 0 0 0 1;
    margin: 0;
}

/* 統計列 */
.stats {
    color: $text-muted;
    text-align: right;
    padding: 0 1;
    margin: 0 0 1 0;
}

/* ─── 輸入列 ─── */
#input-bar {
    height: 3;
    border: round $primary;
    margin: 0 1 1 1;
    background: $boost;
}

#input-prefix {
    width: auto;
    color: $primary;
    text-style: bold;
    padding: 0 0 0 1;
}

#prompt {
    width: 1fr;
    border: blank;
    background: transparent;
    padding: 0 1 0 0;
}

/* 串流中：邊框變暗，前綴變色 */
#input-bar.streaming {
    border: round $panel;
}

#input-bar.streaming #input-prefix {
    color: $text-muted;
}
"""


class ChatApp(App[None]):
    CSS = CSS

    BINDINGS = [
        Binding("ctrl+c", "quit", "退出", priority=True),
        Binding("escape", "cancel_stream", "取消"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.agent = Agent(model=MODEL)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="messages"):
            yield Static(
                "[dim]輸入訊息開始對話[/dim]  ·  "
                "[dim]/clear[/dim] 清除  "
                "[dim]Esc[/dim] 取消  "
                "[dim]Ctrl+C[/dim] 退出",
                id="welcome",
                markup=True,
            )
        with Horizontal(id="input-bar"):
            yield Label("Duncan ❯", id="input-prefix")
            yield Input(placeholder=f"傳訊息給 {MODEL}...", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "qwen-agent"
        self.sub_title = MODEL
        self.query_one("#prompt", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()

        if text == "/clear":
            await self.query_one("#messages", VerticalScroll).remove_children()
            return

        msgs = self.query_one("#messages", VerticalScroll)
        await msgs.mount(
            Static(f"[bold]Duncan[/bold]\n{text}", markup=True, classes="user-bubble")
        )
        msgs.scroll_end(animate=False)
        self._send(text)

    @work(exclusive=True)
    async def _send(self, text: str) -> None:
        msgs    = self.query_one("#messages",    VerticalScroll)
        bar     = self.query_one("#input-bar",   Horizontal)
        prefix  = self.query_one("#input-prefix", Label)
        prompt  = self.query_one("#prompt",      Input)
        stream_md: Markdown | None = None
        buf: list[str] = []

        bar.add_class("streaming")
        prefix.update("⏳")
        prompt.disabled = True
        prompt.placeholder = "等待回覆..."

        try:
            def _at_bottom() -> bool:
                return msgs.scroll_y >= msgs.max_scroll_y - 3

            async def on_thinking(content: str) -> None:
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
                nonlocal stream_md
                buf.append(chunk)
                if stream_md is None:
                    await msgs.mount(Rule(classes="ai-sep"))
                    stream_md = Markdown("", classes="ai-msg")
                    await msgs.mount(stream_md)
                    await stream_md.update("".join(buf) + " ▋")
                    msgs.scroll_end(animate=False)
                else:
                    await stream_md.update("".join(buf) + " ▋")
                    if _at_bottom():
                        msgs.scroll_end(animate=False)

            async def on_done(elapsed, thinking_s, gen_tok, prompt_tok) -> None:
                if stream_md:
                    await stream_md.update("".join(buf))
                parts: list[str] = []
                if thinking_s:
                    parts.append(f"思考 {thinking_s:.1f}s")
                parts.append(f"共 {elapsed:.1f}s")
                if gen_tok:
                    parts.append(f"{gen_tok} tokens")
                stats = " · ".join(parts)
                at_bot = _at_bottom()
                await msgs.mount(Static(stats, classes="stats", markup=False))
                self.sub_title = f"{MODEL} · {stats}"
                if at_bot:
                    msgs.scroll_end(animate=False)

            await self.agent.chat(
                {"role": "user", "content": text},
                on_token=on_token,
                on_thinking=on_thinking,
                on_done=on_done,
            )
            if stream_md and buf:
                await stream_md.update("".join(buf))
        finally:
            bar.remove_class("streaming")
            prefix.update("Duncan ❯")
            prompt.disabled = False
            prompt.placeholder = f"傳訊息給 {MODEL}..."
            prompt.focus()

    def action_cancel_stream(self) -> None:
        self.agent._cancelled = True


if __name__ == "__main__":
    ChatApp().run()
