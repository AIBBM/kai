import asyncio

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.panel import Panel

from agent import QwenAgent
from config import HISTORY_FILE, MODEL, SEARXNG_URL


def parse_input(user_input: str) -> dict:
    """
    解析使用者輸入，支援指令模式：
      /image <path> <問題>  → 將圖片與問題一起傳給模型（多模態）
      其餘                 → 一般文字訊息
    """
    if user_input.startswith("/image "):
        parts = user_input[7:].strip().split(" ", 1)
        image_path = parts[0]
        question = parts[1] if len(parts) > 1 else "請分析這張圖片"
        return {"role": "user", "content": question, "images": [image_path]}
    return {"role": "user", "content": user_input}


async def main():
    agent = QwenAgent(model=MODEL)
    agent.console.print(
        Panel(
            f"[bold]Model:[/bold] {agent.profile.name}\n"
            f"[bold]Vision:[/bold] {agent.profile.supports_vision} · "
            f"[bold]Thinking:[/bold] {agent.profile.supports_thinking}\n"
            f"[bold]SearXNG:[/bold] {SEARXNG_URL}\n"
            f"[bold]History:[/bold] {HISTORY_FILE}\n"
            "輸入 [bold cyan]quit[/bold cyan] 離開 · "
            "[bold cyan]/image <路徑> <問題>[/bold cyan] 傳圖片",
            title="[bold magenta]🧠 Qwen Agent[/bold magenta]",
            border_style="magenta",
        )
    )

    # FileHistory 讓上下鍵可翻展輸入歷史
    session: PromptSession = PromptSession(history=FileHistory(".prompt_history"))

    try:
        while True:
            try:
                user_input = await session.prompt_async("你: ")
                user_input = user_input.strip()
            except EOFError:
                break
            if user_input.lower() in ("quit", "exit", "bye"):
                break
            if not user_input:
                continue
            try:
                await agent.chat(parse_input(user_input))
            except (KeyboardInterrupt, asyncio.CancelledError):
                agent.console.print("[yellow]⚠️ 中斷[/yellow]")
                if agent.history and agent.history[-1]["role"] == "user":
                    agent.history.pop()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass

    agent.save_history()
    agent.console.print("[bold magenta]👋 再見！[/bold magenta]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
