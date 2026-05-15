"""Textual TUI for qwen-agent.  Run: uv run python app.py"""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Footer, Header, Input, Markdown, Rule, Static
from textual import work
from loguru import logger

from agent import Agent
from config import MODEL

# Textual 控制整個畫面，loguru 轉到 log 檔避免閃爍
logger.remove()
logger.add(
    "agent_tui.log",
    level="DEBUG",
    rotation="5 MB",
    format="{time:HH:mm:ss} | {level:<5} | {message}",
)

CSS = """
#messages {
    height: 1fr;
    padding: 1 2;
}

/* 使用者訊息：左側藍色粗邊 */
.user-bubble {
    background: $primary-background;
    border-left: thick $primary;
    padding: 0 1;
    margin: 0 0 1 0;
}

/* 思考面板：黃色框，預設收合 */
.thinking {
    border: round $warning;
    margin: 0 0 1 0;
}

/* AI 回應分隔線 */
Rule.ai-sep {
    color: $success;
    margin: 0;
}

/* AI Markdown 回應 */
Markdown.ai-msg {
    padding: 0 0 0 1;
    margin: 0;
}

/* 回應後的統計列 */
.stats {
    color: $text-disabled;
    text-align: right;
    padding: 0 1;
    margin: 0 0 1 0;
}

/* 歡迎提示 */
#welcome {
    border: dashed $primary-background;
    color: $text-muted;
    padding: 1;
    margin: 0 0 1 0;
}

/* 輸入框 */
#prompt {
    border: tall $primary;
    margin: 0 2 0 2;
    background: $boost;
}

#prompt:disabled {
    border: tall $text-disabled;
    background: $surface;
    color: $text-disabled;
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
        msgs = self.query_one("#messages", VerticalScroll)
        prompt = self.query_one("#prompt", Input)
        stream_md: Markdown | None = None
        buf: list[str] = []

        prompt.disabled = True
        prompt.placeholder = "⏳ 等待回覆..."

        try:
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
                if stream_md is None:
                    await msgs.mount(Rule(classes="ai-sep"))
                    stream_md = Markdown("", classes="ai-msg")
                    await msgs.mount(stream_md)
                buf.append(chunk)
                await stream_md.update("".join(buf) + " ▋")
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
                await msgs.mount(Static(stats, classes="stats", markup=False))
                self.sub_title = f"{MODEL} · {stats}"
                msgs.scroll_end(animate=False)

            await self.agent.chat(
                {"role": "user", "content": text},
                on_token=on_token,
                on_thinking=on_thinking,
                on_done=on_done,
            )
            # 工具 loop 返回時 on_done 不呼叫，確保串流游標清除
            if stream_md and buf:
                await stream_md.update("".join(buf))
        finally:
            prompt.disabled = False
            prompt.placeholder = f"傳訊息給 {MODEL}..."
            prompt.focus()

    def action_cancel_stream(self) -> None:
        self.agent._cancelled = True


if __name__ == "__main__":
    ChatApp().run()
