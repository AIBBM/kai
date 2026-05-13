import asyncio
import json
import time
from datetime import datetime

import httpx
import ollama
from loguru import logger
from rich import box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from config import HISTORY_FILE, HISTORY_KEEP, MAX_TOOL_CALLS
from models import ModelProfile, get_profile
from tools import SEARCH_TOOL, budget_results, clean_thinking, run_tool, web_search


class QwenAgent:
    def __init__(self, model: str):
        self.profile: ModelProfile = get_profile(model)
        self.console = Console()
        self.history: list[dict] = []
        # 工具對映表：新工具只需在這裡登記
        self.tools_map: dict = {
            "web_search": web_search,
            # "k8s_monitor": k8s_monitor,
        }
        self._reset_date()
        self._load_history()

    def _reset_date(self):
        """把今天日期寫入 history[0]，跨日後呼叫可更新。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        system_msg = {
            "role": "system",
            "content": (
                f"目前時間是 {now}（台灣時區 UTC+8）。"
                "這是系統提供的最新時間，絕對正確。"
                "如果對話歷史中有舊的時間記錄，一律以此為準，不得引用歷史中的時間。"
            ),
        }
        if self.history and self.history[0]["role"] == "system":
            self.history[0] = system_msg
        else:
            self.history.insert(0, system_msg)

    # 時間相關的關鍵字，匹配到即視為時間問答訊息
    _TIME_KEYWORDS = ("現在時間", "幾點", "幾点", "是指", "時間是", "時區", "UTC")

    def _is_time_message(self, msg: dict) -> bool:
        """判斷訊息是否與時間查詢相關，避免舊時間沙染新對話。"""
        content = msg.get("content", "")
        return any(kw in content for kw in self._TIME_KEYWORDS)

    def _load_history(self):
        """啟動時岝試從 HISTORY_FILE 載入過往對話，自動過濾時間問答。"""
        if HISTORY_FILE.exists():
            try:
                saved = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                non_system = [m for m in saved if m.get("role") != "system"]

                # 成對過濾：user 問時間 + 對應的 assistant 回覆一起移除
                filtered, skip_next = [], False
                for msg in non_system:
                    if skip_next:
                        skip_next = False
                        continue
                    if msg.get("role") == "user" and self._is_time_message(msg):
                        skip_next = True  # 一併跳過後面的 assistant 回覆
                        logger.debug(f"過濾舊時間問答：{msg['content'][:30]}...")
                        continue
                    filtered.append(msg)

                self.history.extend(filtered)
                logger.debug(f"載入 {len(filtered)} 則歷史訊息，來自 {HISTORY_FILE}")
            except Exception as e:
                logger.warning(f"載入歷史失敗：{e}")

    def _rotate_history(self):
        """記憑體超過 HISTORY_KEEP * 2 則時自動移除最舊的非-system 訊息。"""
        system = [m for m in self.history if m["role"] == "system"]
        rest = [m for m in self.history if m["role"] != "system"]
        if len(rest) > HISTORY_KEEP * 2:
            rest = rest[-(HISTORY_KEEP * 2) :]
            self.history = system + rest
            logger.debug(f"歷史 rotate，保留 {len(rest)} 則")

    def save_history(self):
        """將對話歷史寫入 HISTORY_FILE，只存最近 HISTORY_KEEP * 2 則。"""
        system = [m for m in self.history if m["role"] == "system"]
        rest = [m for m in self.history if m["role"] != "system"]
        to_save = system + rest[-(HISTORY_KEEP * 2) :]
        try:
            HISTORY_FILE.write_text(
                json.dumps(to_save, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug(f"對話已存入 {HISTORY_FILE}，共 {len(to_save)} 則")
        except Exception as e:
            logger.warning(f"儲存歷史失敗：{e}")

    def _trim_history(self) -> list[dict]:
        """保留 system message + 最近 HISTORY_KEEP 則訊息。"""
        system = [m for m in self.history if m["role"] == "system"]
        rest = [m for m in self.history if m["role"] != "system"]
        return system + rest[-HISTORY_KEEP:]

    def _render_thinking(self, content: str):
        """將思考內容渲染成黃色 Panel。"""
        if content.strip():
            self.console.print(
                Panel(
                    content.strip(),
                    title="[bold yellow]💡 思考中[/bold yellow]",
                    subtitle="[dim]Qwen Reasoning Engine[/dim]",
                    border_style="yellow",
                    expand=False,
                    padding=(0, 2),
                )
            )

    def _render_search_table(self, results: list[tuple[str, str]]):
        """將搜尋結果摘要顯示為表格。"""
        table = Table(
            title="🔍 搜尋結果摘要",
            box=box.SIMPLE_HEAD,
            show_lines=False,
            title_style="bold cyan",
        )
        table.add_column("#", style="dim", width=3)
        table.add_column("標題", style="white")
        for i, (_, content) in enumerate(results, 1):
            table.add_row(str(i), content.split("\n")[0][:60])
        self.console.print(table)

    async def chat(self, user_msg: dict):
        """
        單輪對話入口，支援 tool calling loop：
          1. 呼叫模型（stream）
          2. 若模型要求 tool call → 並發執行 → 把結果加入 history → 再次呼叫
          3. 模型不再呼叫工具時結束
        """
        # 如果傳入圖片但模型不支援，提醒並結束
        if "images" in user_msg and not self.profile.supports_vision:
            self.console.print(
                f"[bold red]❌ {self.profile.name} 不支援圖片輸入。[/bold red]"
            )
            return

        self._reset_date()
        self.history.append(user_msg)
        messages = self._trim_history()

        # 根據模型特性决定要傳哪些工具
        active_tools = (
            [SEARCH_TOOL] if self.profile.supports_tools and self.tools_map else []
        )

        tool_call_count = 0
        while True:
            reply_parts: list[str] = []
            tool_calls: list = []  # 用 list 保留所有 tool call，同名工具不會互覆
            thinking_parts: list[str] = []
            t_start = time.perf_counter()
            t_thinking_end: float | None = None
            last_chunk = None
            reply_live: Live | None = None

            try:
                stream = ollama.chat(
                    model=self.profile.name,
                    messages=messages,
                    think=self.profile.supports_thinking,  # 依 profile 開關
                    tools=active_tools,
                    stream=True,
                    options={"num_ctx": self.profile.num_ctx},
                )
            except httpx.ConnectError:
                self.console.print(
                    "[bold red]❌ 無法連線 Ollama，請確認服務正常運行。[/bold red]"
                )
                self.history.pop()
                return

            for chunk in stream:
                msg = chunk.message
                last_chunk = chunk

                # 思考過程：累積內容，流程結束後一次性渲染成 Panel
                if msg.thinking:
                    thinking_parts.append(clean_thinking(msg.thinking))

                # 正式回答：用 Live 即時更新 Markdown
                if msg.content:
                    if not reply_parts:
                        if thinking_parts:
                            t_thinking_end = time.perf_counter()
                            self._render_thinking("".join(thinking_parts))
                        self.console.print(
                            Rule("[bold green]🤖 AI[/bold green]", style="green")
                        )
                        reply_live = Live(console=self.console, refresh_per_second=10)
                        reply_live.start()
                    reply_parts.append(msg.content)
                    if reply_live:
                        # 直接渲染 Markdown，不包 Panel，避免復製時混入邊框字元
                        reply_live.update(Markdown("".join(reply_parts)))

                for tc in msg.tool_calls or []:
                    tool_calls.append(tc)

            if reply_live:
                reply_live.stop()

            t_total = time.perf_counter() - t_start
            usage = (
                getattr(last_chunk, "eval_count", None),
                getattr(last_chunk, "prompt_eval_count", None),
            )
            if t_thinking_end:
                logger.debug(
                    f"思考 {t_thinking_end - t_start:.2f}s，總耗時 {t_total:.2f}s "
                    f"| 生成 {usage[0]} tokens，輸入 {usage[1]} tokens"
                )
            else:
                logger.debug(
                    f"總耗時 {t_total:.2f}s | 生成 {usage[0]} tokens，輸入 {usage[1]} tokens"
                )

            if not tool_calls:
                self.history.append(
                    {"role": "assistant", "content": "".join(reply_parts)}
                )
                self._rotate_history()  # 記憑體超限時自動移除舊訊息
                return

            messages.append(
                {
                    "role": "assistant",
                    "content": "".join(reply_parts),
                    "tool_calls": tool_calls,
                }
            )

            with self.console.status(
                "[bold cyan]正在搜尋中...[/bold cyan]", spinner="dots"
            ):
                results = await asyncio.gather(
                    *[run_tool(tc, self.tools_map) for tc in tool_calls]
                )

            results = budget_results(list(results))
            self._render_search_table(results)

            for fn_name, result in results:
                messages.append({"role": "tool", "content": result, "name": fn_name})

            tool_call_count += 1
            if tool_call_count >= MAX_TOOL_CALLS:
                self.console.print(
                    f"[bold red]⚠️ 已達搜尋上限 {MAX_TOOL_CALLS} 次，強制結束[/bold red]"
                )
                return
