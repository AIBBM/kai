import asyncio

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.panel import Panel

from agent import Agent
from config import HISTORY_FILE, MODEL, SEARXNG_URL
from file_picker import pick_image


def parse_input(user_input: str) -> dict | None:
    """
    解析使用者輸入：
      /image       → 互動式檔案選擇器（回傳 None，由 main 處理）
      /image <路徑> <問題>  → 直接帶參數
      其餘          → 一般文字訊息
    """
    if user_input == "/image":
        return None
    if user_input.startswith("/image "):
        parts = user_input[7:].strip().split(" ", 1)
        image_path = parts[0]
        question = parts[1] if len(parts) > 1 else "請分析這張圖片"
        return {"role": "user", "content": question, "images": [image_path]}
    return {"role": "user", "content": user_input}


async def prompt_image(session: PromptSession) -> dict:
    """互動式選圖 + 輸入問題，組成 user message。"""
    image_path = await pick_image()
    if image_path is None:
        return {"role": "user", "content": "（取消）"}
    question = (await session.prompt_async("問題: ")).strip()
    if not question:
        question = "請分析這張圖片"
    return {"role": "user", "content": question, "images": [image_path]}


async def main():
    agent = Agent(model=MODEL)
    agent.console.print(
        Panel(
            f"[bold]Model:[/bold] {agent.profile.name}\n"
            f"[bold]Vision:[/bold] {agent.profile.supports_vision} · "
            f"[bold]Thinking:[/bold] {agent.profile.supports_thinking}\n"
            f"[bold]SearXNG:[/bold] {SEARXNG_URL}\n"
            f"[bold]History:[/bold] {HISTORY_FILE}\n"
            "輸入 [bold cyan]quit[/bold cyan] 離開 · "
            "[bold cyan]/image[/bold cyan] 選圖片",
            title="[bold magenta]🧠 Qwen Agent[/bold magenta]",
            border_style="magenta",
        )
    )

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
                msg = parse_input(user_input)
                if msg is None:
                    msg = await prompt_image(session)
                if msg.get("content") == "（取消）":
                    continue
                await agent.chat(msg)
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
