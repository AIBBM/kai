"""Textual TUI for qwen-agent.  Run: uv run python app.py"""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Input, Markdown, Static
from textual import work

from loguru import logger

from agent import Agent
from config import MODEL, SEARXNG_URL

# Textual 控制整個畫面，loguru 寫 stderr 會閃爍——改寫到 log 檔
logger.remove()
logger.add("agent_tui.log", level="DEBUG", rotation="5 MB",
           format="{time:HH:mm:ss} | {level:<5} | {message}")


CSS = """
VerticalScroll {
    height: 1fr;
    padding: 0 1;
}
.user-bubble {
    background: $boost;
    padding: 0 1;
    margin: 1 0 0 0;
}
.thinking-bubble {
    border: round $warning;
    padding: 0 1;
    margin: 1 0 0 0;
    color: $warning;
}
.ai-divider {
    color: $success;
    margin: 1 0 0 0;
    text-align: center;
}
Markdown.ai-msg {
    margin: 0 0 1 0;
}
Input {
    dock: bottom;
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
        yield VerticalScroll(id="messages")
        yield Input(placeholder="輸入訊息...", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"qwen-agent ◆ {self.agent.profile.name}"
        self.sub_title = f"SearXNG: {SEARXNG_URL}"
        self.query_one("#prompt", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        msgs = self.query_one("#messages", VerticalScroll)
        await msgs.mount(
            Static(
                f"[bold cyan]Duncan:[/bold cyan] {text}",
                markup=True,
                classes="user-bubble",
            )
        )
        msgs.scroll_end(animate=False)
        self._send(text)

    @work(exclusive=True)
    async def _send(self, text: str) -> None:
        msgs = self.query_one("#messages", VerticalScroll)
        stream_md: Markdown | None = None
        buf: list[str] = []

        async def on_thinking(content: str) -> None:
            preview = content[:500] + ("…" if len(content) > 500 else "")
            await msgs.mount(Static(f"💡 思考中\n{preview}", classes="thinking-bubble"))
            msgs.scroll_end(animate=False)

        async def on_token(chunk: str) -> None:
            nonlocal stream_md
            if stream_md is None:
                await msgs.mount(Static("─── 🤖 AI ───", classes="ai-divider", markup=False))
                stream_md = Markdown("", classes="ai-msg")
                await msgs.mount(stream_md)
            buf.append(chunk)
            await stream_md.update("".join(buf))
            msgs.scroll_end(animate=False)

        async def on_done(elapsed, thinking_s, gen_tok, prompt_tok) -> None:
            parts = []
            if thinking_s:
                parts.append(f"思考 {thinking_s:.1f}s")
            parts.append(f"共 {elapsed:.1f}s")
            if gen_tok:
                parts.append(f"生成 {gen_tok} tokens")
            self.sub_title = " · ".join(parts)

        await self.agent.chat(
            {"role": "user", "content": text},
            on_token=on_token,
            on_thinking=on_thinking,
            on_done=on_done,
        )

    def action_cancel_stream(self) -> None:
        self.agent._cancelled = True


if __name__ == "__main__":
    ChatApp().run()
