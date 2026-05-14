import asyncio
import select
import sys
import termios
import tty

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from rich.panel import Panel

SLASH_COMMANDS = {
    "/clear": "清空畫面（保留對話記憶）",
    "/image": "互動式選圖並傳給模型",
}


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/"):
            for cmd, desc in SLASH_COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(
                        cmd[len(text):],
                        display=cmd,
                        display_meta=desc,
                    )


from agent import Agent
from config import HISTORY_FILE, MODEL, SEARXNG_URL, USERNAME
from loguru import logger
from file_picker import pick_image
from mcp_client import MCPClient, load_mcp_servers


async def _fetch_db_schema(mcp: MCPClient) -> str:
    """啟動時查詢資料庫 table 清單，產生給模型的 schema hint。"""
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


async def _watch_esc(agent: Agent, done: asyncio.Event):
    """在 agent.chat() 期間偵測 Esc 鍵，觸發後設 _cancelled = True。"""
    def _read():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while not done.is_set():
                r, _, _ = select.select([sys.stdin], [], [], 0.1)
                if r:
                    ch = sys.stdin.read(1)
                    if ch == "\x1b":
                        agent._cancelled = True
                        return
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    await asyncio.to_thread(_read)


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

    servers = load_mcp_servers()
    if servers:
        mcp = MCPClient(servers)
        await mcp.start()
        agent.mcp_client = mcp
        agent.mcp_context = await _fetch_db_schema(mcp)
        fs_paths = [
            arg for name, cfg in servers.items()
            if not name.startswith("_") and cfg.get("command") in ("npx", "node")
            for arg in cfg.get("args", []) if arg.startswith("/")
        ]
        if fs_paths:
            agent.mcp_context += f"\n檔案系統可存取目錄：{', '.join(fs_paths)}"

    mcp_info = (
        f"[bold]MCP:[/bold] {', '.join(agent.mcp_client.tool_names)}"
        if agent.mcp_client and agent.mcp_client.tool_names
        else "[bold]MCP:[/bold] [dim]未啟用[/dim]"
    )
    agent.console.print(
        Panel(
            f"[bold]Model:[/bold] {agent.profile.name}\n"
            f"[bold]Vision:[/bold] {agent.profile.supports_vision} · "
            f"[bold]Thinking:[/bold] {agent.profile.supports_thinking}\n"
            f"[bold]SearXNG:[/bold] {SEARXNG_URL}\n"
            f"{mcp_info}\n"
            f"[bold]History:[/bold] {HISTORY_FILE}\n"
            "輸入 [bold cyan]quit[/bold cyan] 離開 · "
            "[bold cyan]/image[/bold cyan] 選圖片",
            title="[bold magenta]🧠 Qwen Agent[/bold magenta]",
            border_style="magenta",
        )
    )

    session: PromptSession = PromptSession(
        history=FileHistory(".prompt_history"),
        completer=SlashCompleter(),
        complete_while_typing=True,
    )

    try:
        while True:
            try:
                user_input = await session.prompt_async(f"{USERNAME}: ")
                user_input = user_input.strip()
            except EOFError:
                break
            if user_input.lower() in ("quit", "exit", "bye"):
                break
            if not user_input:
                continue
            if user_input == "/clear":
                agent.console.clear()
                continue
            if user_input.startswith("!"):
                cmd = user_input[1:].strip()
                if cmd:
                    from tools import bash_exec
                    result = await bash_exec(cmd)
                    agent.console.print(result)
                continue
            try:
                msg = parse_input(user_input)
                if msg is None:
                    msg = await prompt_image(session)
                if msg.get("content") == "（取消）":
                    continue
                done = asyncio.Event()
                watcher = asyncio.create_task(_watch_esc(agent, done))
                try:
                    await agent.chat(msg)
                finally:
                    done.set()
                    await watcher
            except (KeyboardInterrupt, asyncio.CancelledError):
                agent.console.print("[yellow]⚠️ 中斷[/yellow]")
                if agent.history and agent.history[-1]["role"] == "user":
                    agent.history.pop()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass

    if agent.mcp_client:
        await agent.mcp_client.stop()
    agent.save_history()
    agent.console.print("[bold magenta]👋 再見！[/bold magenta]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
