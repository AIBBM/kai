import asyncio
import json
import os
import re
import time
from datetime import datetime
from types import SimpleNamespace

import httpx
import ollama
from openai import AsyncOpenAI
from loguru import logger
from rich import box
from rich.live import Live
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from .config import (
    GEMINI_API_KEY,
    HISTORY_FILE,
    HISTORY_KEEP,
    MAX_SEARCH_CHARS,
    MAX_TOOL_CALLS,
    NUM_CTX,
    TEMPERATURE,
    TOP_K,
    TOP_P,
    USERNAME,
)
from .models import ModelProfile, get_profile
from .tools import (
    BASH_TOOL,
    SEARCH_TOOL,
    bash_exec,
    clean_thinking,
    fetch_urls,
    run_tool,
    web_search,
    FETCH_URLS_TOOL,
)

try:
    from .mcp_client import MCPClient
except ImportError:
    MCPClient = None  # type: ignore


class Agent:
    def __init__(self, model: str):
        self.profile: ModelProfile = get_profile(model)
        self.console = Console()
        self.history: list[dict] = []
        # 工具對映表：新工具只需在這裡登記
        self.tools_map: dict = {
            "web_search": web_search,
            "fetch_urls": fetch_urls,
            "bash_exec": bash_exec,
        }
        if NUM_CTX:
            self.profile.num_ctx = NUM_CTX
        self.mcp_client: "MCPClient | None" = None
        self.mcp_context: str = ""
        self._cancelled = False
        self._ollama = ollama.AsyncClient()
        self._gemini: AsyncOpenAI | None = (
            AsyncOpenAI(
                api_key=GEMINI_API_KEY,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            if GEMINI_API_KEY
            else None
        )
        self._reset_date()
        self._load_history()

    def _reset_date(self):
        """把今天日期寫入 history[0]，跨日後呼叫可更新。"""
        now = datetime.now().strftime("%Y-%m-%d")
        system_msg = {
            "role": "system",
            "content": (
                f"目前時間是 {now}（台灣時區 UTC+8）。"
                f"使用者的名字是 {USERNAME}。"
                "這是系統提供的最新時間，絕對正確。"
                "如果對話歷史中有舊的時間記錄，一律以此為準，不得引用歷史中的時間。"
                + (
                    "你有 web_search 工具可以查詢即時資訊。"
                    "遇到任何你不確定的事實、日期、新聞、時事，直接呼叫 web_search，不要說『我要搜尋』或『請稍等』，直接呼叫工具。"
                    "只有在確定自己知道答案時才直接回答，其他情況一律先搜尋再回答。"
                    if self.profile.supports_tools
                    else ""
                )
                # + "回覆時不要使用 --- 水平分隔線。"
                + (
                    f"\n\n你也有資料庫查詢工具可以存取台股資料。{self.mcp_context}"
                    if self.mcp_context
                    else ""
                )
                + (
                    f"\n\n## MCP 工具"
                    f"\n目前工作目錄：{os.getcwd()}。"
                    "\n你已連接檔案系統與瀏覽器等 MCP 工具，請依據任務直接呼叫，工具清單已內建於系統，無需重複列舉。"
                    "\n\n### 工具優先順序與限制（必須嚴格遵守）"
                    "\n- 瀏覽器操作 → 只能使用 Playwright MCP 工具（browser_navigate / browser_click 等），嚴禁用 bash 執行 node / npx / playwright CLI。"
                    "\n- 檔案讀寫 → 只能使用 FileSystem MCP 工具（read_file / write_file / list_directory 等），嚴禁用 bash 執行 cat / ls / cp。"
                    "\n- 系統指令 → bash_exec 僅限 MCP 無法涵蓋的純系統操作（git、uv、ping 等）。"
                    "\n- 環境安全 → 嚴禁用 bash 執行 npm install / npx / node，絕對禁止在專案目錄安裝任何 npm 套件。"
                    f"\n- 檔案輸出 → 截圖、下載或建立新檔案時，請儲存於目前工作目錄（{os.getcwd()}），並於回覆中提供明確的絕對路徑。"
                    "\n\n### 執行與排錯原則"
                    "\n- 複雜任務請逐步完成：網頁操作需先確認頁面已載入，再進行點擊或截圖。"
                    "\n- 嚴禁捏造：回答必須基於工具回傳的真實資料，工具失敗時誠實告知，不得編造結果。"
                    "\n- 選擇器排錯：selector 找不到元素時，**必須立即呼叫 `browser_snapshot`（不要帶 filename 參數）**，閱讀回傳的頁面結構，再從中找正確的元素 ref 或描述，嚴禁用相同 selector 重複重試。"
                    "\n- 截圖參數：`browser_take_screenshot` 的 `type` 參數只接受 `\"png\"` 或 `\"jpeg\"`，不需要時可省略；不可傳其他值。"
                    "\n- 任務收尾：完成任務後，必須用文字清楚告知最終結果與狀態，不得無聲結束。"
                    if self.mcp_client and self.mcp_client.tool_names
                    else ""
                )
            ),
        }
        if self.history and self.history[0]["role"] == "system":
            self.history[0] = system_msg
        else:
            self.history.insert(0, system_msg)

    # 時間相關的關鍵字，匹配到即視為時間問答訊息
    _TIME_KEYWORDS = ("現在時間", "幾點", "幾点", "時間是", "時區", "UTC")

    def _is_time_message(self, msg: dict) -> bool:
        """判斷訊息是否與時間查詢相關，避免舊時間沙染新對話。"""
        content = msg.get("content", "")
        return any(kw in content for kw in self._TIME_KEYWORDS)

    def _load_history(self):
        """啟動時嘗試從 HISTORY_FILE 載入過往對話，自動過濾時間問答。"""
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
        """記憶條數超過 HISTORY_KEEP * 2 則時自動移除最舊的非-system 訊息。"""
        system = [m for m in self.history if m["role"] == "system"]
        rest = [m for m in self.history if m["role"] != "system"]
        if len(rest) > HISTORY_KEEP * 2:
            rest = rest[-(HISTORY_KEEP * 2) :]
            self.history = system + rest
            logger.debug(f"歷史 rotate，保留 {len(rest)} 則")

    def reset_history(self):
        """切換工作目錄後清空並重新載入新 cwd 的 history.json。"""
        self.history = []
        self._reset_date()
        self._load_history()

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

    async def compact_history(self) -> str:
        """用 gemini-2.0-flash 摘要現有 history，壓縮 context。回傳摘要文字。"""
        if not self._gemini:
            raise RuntimeError("需要 GEMINI_API_KEY 才能使用 compact")

        parts = []
        for msg in self.history:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "system":
                continue
            if isinstance(content, list):
                content = next(
                    (p.get("text", "") for p in content if p.get("type") == "text"), ""
                )
            if content:
                label = "使用者" if role == "user" else "助理"
                parts.append(f"[{label}]\n{content}")

        if not parts:
            return ""

        conversation = "\n\n---\n\n".join(parts)
        prompt = (
            "請將以下對話歷史精簡摘要，繁體中文輸出，保留：\n"
            "1. 使用者的主要目標與需求\n"
            "2. 重要決策、結論、已完成的事\n"
            "3. 關鍵程式碼片段或技術細節\n"
            "4. 待辦事項或未完成的工作\n\n"
            f"對話歷史：\n\n{conversation}"
        )

        resp = await self._gemini.chat.completions.create(
            model="gemini-2.0-flash",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        summary = resp.choices[0].message.content or ""

        system = [m for m in self.history if m["role"] == "system"]
        self.history = system + [{"role": "assistant", "content": f"[對話摘要]\n{summary}"}]
        self.save_history()
        logger.info(f"compact_history 完成，history 壓縮至 {len(self.history)} 則")
        return summary

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
                    subtitle=f"[dim]{self.profile.name}[/dim]",
                    border_style="yellow",
                    expand=False,
                    padding=(0, 2),
                )
            )

    def _render_search_table(self, results: list[tuple[str, str]]):
        """將 web_search 結果摘要顯示為表格。"""
        table = Table(
            title="🔍 搜尋結果摘要",
            box=box.SIMPLE_HEAD,
            show_lines=False,
            title_style="bold cyan",
        )
        table.add_column("#", style="dim", width=3)
        table.add_column("標題", style="white", no_wrap=False)
        n = 0
        for _, content in results:
            for line in content.split("\n"):
                if line.startswith("["):
                    n += 1
                    table.add_row(str(n), line)
        self.console.print(table)

    def _trim_tool_result(self, content: str) -> str:
        """HTML 去標簽；超長截斷。"""
        if re.search(r"<html|<!doctype", content[:200], re.IGNORECASE):
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s+", " ", content).strip()
        if len(content) > MAX_SEARCH_CHARS:
            content = (
                content[:MAX_SEARCH_CHARS] + f"\n...[已截斷，共 {len(content)} 字元]"
            )
        return content

    def _render_mcp_result(self, tool_name: str, content: str):
        """MCP tool 結果顯示為 Panel。"""
        self.console.print(
            Panel(
                content[:MAX_SEARCH_CHARS]
                + ("..." if len(content) > MAX_SEARCH_CHARS else ""),
                title=f"[bold cyan]🔧 {tool_name}[/bold cyan]",
                border_style="cyan",
                expand=True,
            )
        )

    def _start_live(self) -> "Live":
        self.console.print(Rule("[bold green]🤖 AI[/bold green]", style="green"))
        live = Live(Markdown(""), console=self.console, transient=True, refresh_per_second=7, vertical_overflow="crop")
        live.start()
        return live

    async def _stream_ollama(
        self, messages, active_tools, reply_parts, tool_calls, thinking_parts,
        on_token, on_thinking, on_retry,
    ) -> "Live | None":
        """Ollama 串流，含 XML 解析錯誤自動重試。回傳 Live（CLI 模式）或 None。"""
        _xml_retry = 0
        _XML_RETRY_MAX = 2
        while True:
            try:
                stream = await self._ollama.chat(
                    model=self.profile.name,
                    messages=messages,
                    think=self.profile.supports_thinking,
                    tools=active_tools,
                    stream=True,
                    options={"num_ctx": self.profile.num_ctx, "temperature": TEMPERATURE, "top_k": TOP_K, "top_p": TOP_P},
                )
            except httpx.ConnectError:
                self.console.print("[bold red]❌ 無法連線 Ollama，請確認服務正常運行。[/bold red]")
                raise

            live: "Live | None" = None
            try:
                async for chunk in stream:
                    if self._cancelled:
                        break
                    msg = chunk.message
                    self._last_usage = (getattr(chunk, "eval_count", None), getattr(chunk, "prompt_eval_count", None))

                    if msg.thinking:
                        thinking_parts.append(clean_thinking(msg.thinking))

                    if msg.content:
                        if not reply_parts:
                            if thinking_parts:
                                self._t_thinking_end = time.perf_counter()
                                if on_thinking:
                                    await on_thinking("".join(thinking_parts))
                                else:
                                    self._render_thinking("".join(thinking_parts))
                            if not on_token:
                                live = self._start_live()
                        reply_parts.append(msg.content)
                        if on_token:
                            await on_token(msg.content)
                        elif live:
                            live.update(Markdown("".join(reply_parts)))

                    for tc in msg.tool_calls or []:
                        tool_calls.append(tc)
                return live

            except ollama.ResponseError as e:
                if live:
                    live.stop()
                if "XML syntax error" in str(e) and _xml_retry < _XML_RETRY_MAX:
                    _xml_retry += 1
                    logger.warning(f"XML 解析錯誤，第 {_xml_retry}/{_XML_RETRY_MAX} 次重試：{e}")
                    reply_parts.clear()
                    tool_calls.clear()
                    thinking_parts.clear()
                    if on_retry:
                        await on_retry()
                    continue
                raise

    async def _stream_gemini(
        self, messages, active_tools, reply_parts, tool_calls, thinking_parts,
        on_token, on_thinking,
    ) -> "Live | None":
        """Gemini 串流（OpenAI-compatible endpoint）。"""
        from openai import NOT_GIVEN, RateLimitError
        try:
            stream = await self._gemini.chat.completions.create(
                model=self.profile.name,
                messages=messages,
                tools=active_tools or NOT_GIVEN,
                temperature=TEMPERATURE,
                top_p=TOP_P,
                stream=True,
                stream_options={"include_usage": True},
            )
        except RateLimitError as e:
            logger.warning(f"Gemini 配額不足：{e}")
            raise RuntimeError(f"❌ Gemini 配額已用盡，請稍後再試或升級至付費方案。\n{e}") from e

        _tc_buf: dict[int, dict] = {}
        live: "Live | None" = None

        async for chunk in stream:
            if self._cancelled:
                break
            if chunk.usage:
                self._last_usage = (chunk.usage.completion_tokens, chunk.usage.prompt_tokens)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta.content:
                if not reply_parts:
                    if thinking_parts:
                        self._t_thinking_end = time.perf_counter()
                        if on_thinking:
                            await on_thinking("".join(thinking_parts))
                        else:
                            self._render_thinking("".join(thinking_parts))
                    if not on_token:
                        live = self._start_live()
                reply_parts.append(delta.content)
                if on_token:
                    await on_token(delta.content)
                elif live:
                    live.update(Markdown("".join(reply_parts)))

            for tc_chunk in (delta.tool_calls or []):
                i = tc_chunk.index
                if i not in _tc_buf:
                    _tc_buf[i] = {"id": tc_chunk.id or "", "name": "", "args": ""}
                if tc_chunk.function:
                    _tc_buf[i]["name"] += tc_chunk.function.name or ""
                    _tc_buf[i]["args"] += tc_chunk.function.arguments or ""

        for idx in sorted(_tc_buf):
            d = _tc_buf[idx]
            tc = SimpleNamespace(
                id=d["id"],
                function=SimpleNamespace(name=d["name"], arguments=d["args"]),
            )
            tool_calls.append(tc)
        return live

    async def chat(self, user_msg: dict, on_token=None, on_thinking=None, on_done=None, on_tool_call=None, on_retry=None):
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
        active_tools = []
        if self.profile.supports_tools and self.tools_map:
            active_tools.extend([SEARCH_TOOL, FETCH_URLS_TOOL, BASH_TOOL])
        if self.profile.supports_tools and self.mcp_client:
            active_tools.extend(self.mcp_client.tools)

        self._last_usage: tuple = (None, None)
        self._t_thinking_end: float | None = None
        tool_call_count = 0
        while True:
            reply_parts: list[str] = []
            tool_calls: list = []
            thinking_parts: list[str] = []
            t_start = time.perf_counter()
            self._t_thinking_end = None

            live: "Live | None" = None
            try:
                if self.profile.backend == "gemini":
                    live = await self._stream_gemini(
                        messages, active_tools, reply_parts, tool_calls, thinking_parts,
                        on_token, on_thinking,
                    )
                else:
                    live = await self._stream_ollama(
                        messages, active_tools, reply_parts, tool_calls, thinking_parts,
                        on_token, on_thinking, on_retry,
                    )
            except Exception as e:
                if live:
                    live.stop()
                logger.error(f"模型串流錯誤：{e}")
                if self.history and self.history[-1]["role"] == "user":
                    self.history.pop()
                if on_token:
                    raise
                self.console.print(f"[bold red]❌ 模型錯誤：{e}[/bold red]")
                return

            if live:
                live.stop()

            if self._cancelled:
                self._cancelled = False
                self.console.print("[yellow]\n⏹ 已取消[/yellow]")
                if self.history and self.history[-1]["role"] == "user":
                    self.history.pop()
                return

            if reply_parts and not on_token:
                self.console.print(Markdown("".join(reply_parts)))

            t_total = time.perf_counter() - t_start
            usage = self._last_usage
            if self._t_thinking_end:
                logger.debug(
                    f"思考 {self._t_thinking_end - t_start:.2f}s，總耗時 {t_total:.2f}s "
                    f"| 生成 {usage[0]} tokens，輸入 {usage[1]} tokens"
                )
            else:
                logger.debug(
                    f"總耗時 {t_total:.2f}s | 生成 {usage[0]} tokens，輸入 {usage[1]} tokens"
                )

            if not tool_calls:
                if not reply_parts:
                    # Model produced only thinking with no text — avoid polluting history with empty entry
                    logger.warning("模型未產生文字回覆（僅思考）")
                    if self.history and self.history[-1]["role"] == "user":
                        self.history.pop()
                    if on_done:
                        await on_done(t_total, self._t_thinking_end and self._t_thinking_end - t_start, usage[0], usage[1])
                    return
                self.history.append(
                    {"role": "assistant", "content": "".join(reply_parts)}
                )
                self._rotate_history()
                self.save_history()
                if on_done:
                    await on_done(t_total, self._t_thinking_end and self._t_thinking_end - t_start, usage[0], usage[1])
                return

            # Build assistant message with tool calls (format differs by backend)
            if self.profile.backend == "gemini":
                messages.append({
                    "role": "assistant",
                    "content": "".join(reply_parts) or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                                if isinstance(tc.function.arguments, str)
                                else json.dumps(tc.function.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in tool_calls
                    ],
                })
            else:
                messages.append({
                    "role": "assistant",
                    "content": "".join(reply_parts),
                    "tool_calls": tool_calls,
                })

            with self.console.status(
                "[bold cyan]正在執行工具...[/bold cyan]", spinner="dots"
            ):
                results = await asyncio.gather(
                    *[
                        run_tool(tc, self.tools_map, self.mcp_client)
                        for tc in tool_calls
                    ]
                )

            if on_tool_call:
                for tc, (fn_name, result) in zip(tool_calls, results):
                    raw = getattr(tc.function, "arguments", {})
                    args = json.loads(raw) if isinstance(raw, str) else dict(raw)
                    await on_tool_call(fn_name, args, result)
            else:
                web_results = [(fn, r) for fn, r in results if fn == "web_search"]
                other_results = [(fn, r) for fn, r in results if fn != "web_search"]
                if web_results:
                    self._render_search_table(web_results)
                for fn_name, result in other_results:
                    self._render_mcp_result(fn_name, result)

            # Tool result messages (format differs by backend)
            if self.profile.backend == "gemini":
                for tc, (fn_name, result) in zip(tool_calls, results):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": self._trim_tool_result(result),
                    })
            else:
                for fn_name, result in results:
                    messages.append({
                        "role": "tool",
                        "content": self._trim_tool_result(result),
                        "name": fn_name,
                    })

            tool_call_count += 1
            if tool_call_count >= MAX_TOOL_CALLS:
                logger.warning(f"已達工具呼叫上限 {MAX_TOOL_CALLS} 次，強制結束")
                if self.history and self.history[-1]["role"] == "user":
                    self.history.pop()
                self.save_history()
                if on_tool_call:
                    # TUI mode: raise so _send() can show error widget
                    raise RuntimeError(f"已達工具呼叫上限 {MAX_TOOL_CALLS} 次，對話強制結束")
                self.console.print(
                    f"[bold red]⚠️ 已達工具呼叫上限 {MAX_TOOL_CALLS} 次，強制結束[/bold red]"
                )
                return
