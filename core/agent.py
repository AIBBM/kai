import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import ollama
from loguru import logger
from openai import AsyncOpenAI

from .backends import stream_ollama, stream_openai_compat
from .config import (
    DEEPSEEK_API_KEY,
    GEMINI_API_KEY,
    HISTORY_FILE,
    HISTORY_KEEP,
    MAX_SEARCH_CHARS,
    MAX_TOOL_CALLS,
    NUM_CTX,
    USERNAME,
)
from .models import ModelProfile, get_profile
from .tools import (
    BASH_TOOL,
    FETCH_URLS_TOOL,
    OPEN_AGENT_TOOL,
    SEARCH_TOOL,
    bash_exec,
    fetch_urls,
    run_tool,
    web_search,
)

try:
    from .mcp_client import MCPClient
except ImportError:
    MCPClient = None  # type: ignore


class Agent:
    def __init__(self, model: str, history_file: "Path | None" = None, agent_dir: "Path | None" = None, system_prompt: str | None = None):
        self.profile: ModelProfile = get_profile(model)
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
        self.last_prediction: str = ""
        self._cancelled = False
        self._tts_config: dict | None = None  # 由 _do_load_agent 注入；None 表示不啟用 TTS
        # agent 專屬目錄（存 history.json / AGENT_NOTES.md；None 表示用 cwd）
        self._agent_dir: "Path | None" = agent_dir
        self._history_file: Path = history_file or HISTORY_FILE
        # 固定 system prompt（agent factory 建立的 agent 用此代替通用重建邏輯）
        self._custom_system_prompt: str | None = system_prompt
        self.history_keep: int = HISTORY_KEEP
        self._ollama = ollama.AsyncClient()
        self._gemini: AsyncOpenAI | None = (
            AsyncOpenAI(
                api_key=GEMINI_API_KEY,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            if GEMINI_API_KEY
            else None
        )
        self._deepseek: AsyncOpenAI | None = (
            AsyncOpenAI(
                api_key=DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com",
            )
            if DEEPSEEK_API_KEY
            else None
        )
        self._rebuild_system_prompt()
        self._load_history()

    def _rebuild_system_prompt(self):
        """重建 history[0] system message（日期、cwd、MCP context 每次都可能變）。
        若有 _custom_system_prompt（agent factory 建立的 agent），直接使用，不走通用邏輯。
        """
        if self._custom_system_prompt is not None:
            notes_root = self._agent_dir if self._agent_dir else Path(os.getcwd())
            notes_path = notes_root / "AGENT_NOTES.md"
            notes = notes_path.read_text(encoding="utf-8").strip() if notes_path.exists() else ""
            content = self._custom_system_prompt
            if notes:
                content += f"\n\n## 📝 專案記憶\n{notes}\n\n以上規則必須嚴格遵守。"
            msg = {"role": "system", "content": content}
            if self.history and self.history[0]["role"] == "system":
                self.history[0] = msg
            else:
                self.history.insert(0, msg)
            return
        now = datetime.now().strftime("%Y-%m-%d")
        tool_names: set[str] = self.mcp_client.tool_names if self.mcp_client else set()
        has_mcp = bool(tool_names)
        has_playwright = bool(tool_names & {"browser_navigate", "browser_click", "browser_snapshot"})
        has_filesystem = bool(tool_names & {"read_file", "write_file", "list_directory"})
        has_repomapper = bool(tool_names & {"repo_map", "search_identifiers"})
        cwd = os.getcwd()
        notes_root = self._agent_dir if self._agent_dir else Path(cwd)
        notes_path = notes_root / "AGENT_NOTES.md"
        notes = notes_path.read_text(encoding="utf-8").strip() if notes_path.exists() else ""

        # 動態生成已連線 server 清單
        if has_mcp and self.mcp_client:
            server_parts = [
                f"{name}（{count} 個工具）"
                for name, count in self.mcp_client.server_summary.items()
            ]
            mcp_server_desc = "、".join(server_parts)
        else:
            mcp_server_desc = ""

        from core.config import get_nickname
        nickname = get_nickname(self.profile.name)
        nickname_line = (
            f"你的名字是「{nickname}」。" if nickname != self.profile.name else ""
        )

        system_msg = {
            "role": "system",
            "content": (
                f"目前時間是 {now}（台灣時區 UTC+8）。"
                f"使用者的名字是 {USERNAME}。"
                + nickname_line
                + "若對話歷史有舊的時間記錄一律作廢。"
                + (
                    f"你是 {self.profile.name}，由 DeepSeek 開發的 AI 助手。你不是 Claude，不是 ChatGPT，也不是任何其他 AI。"
                    if self.profile.backend == "deepseek"
                    else ""
                )
                + (
                    f"\n\n## 📝 專案記憶\n{notes}"
                    "\n\n以上規則必須嚴格遵守。"
                    if notes
                    else ""
                )
                + "\n\n每次回覆末尾附上 <next>預測用戶下一句（15字以內）</next>，不解釋此標籤。"
                + (
                    "遇到任何不確定的事實、日期、新聞、時事，必須直接呼叫 web_search 工具。"
                    "**嚴禁說「我要搜尋」或「請稍等」，請直接呼叫工具。**"
                    "只有在 100% 確定時才直接回答，其他情況一律先搜尋。"
                    if self.profile.supports_tools
                    else ""
                )
                + (
                    f"\n\n你已連接以下 MCP server：{mcp_server_desc}。工具清單已內建，無需重複列舉。"
                    + (f"\n- {self.mcp_context.strip()}" if self.mcp_context else "")
                    + f"\n\n## 📁 工作目錄\n- 目前工作目錄：{cwd}"
                    + "\n\n## 🚫 工具優先順序與嚴格限制（違反將導致任務失敗）"
                    + (lambda rules: "\n" + "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules)))(
                        ([
                            "瀏覽器操作：僅限使用 Playwright MCP 工具（如 browser_navigate, browser_click）。嚴禁用 bash 執行 node / npx / playwright CLI。",
                        ] if has_playwright else [])
                        + ([
                            "檔案讀寫：僅限使用 FileSystem MCP 工具（如 read_file, write_file, list_directory）。嚴禁用 bash 執行 cat / ls / cp。",
                        ] if has_filesystem else [])
                        + [
                            "系統指令：bash_exec 僅限用於 MCP 無法涵蓋的純系統操作（如 git, uv, ping）。",
                        ]
                        + ([
                            "環境安全：**絕對禁止**用 bash 執行 npm install / npx / node，嚴禁在專案目錄安裝任何 npm 套件。",
                        ] if has_playwright else [])
                        + [
                            f"檔案輸出：截圖、下載或建立新檔案時，必須儲存於「目前工作目錄」（{cwd}），並於回覆中提供明確的絕對路徑。",
                        ]
                    )
                    + "\n\n## 🛠️ 執行與排錯 SOP"
                    + (
                        f"\n- 程式碼探索：需要了解專案結構或函數定義時，**優先呼叫 `repo_map` 工具**（傳入 project_root=`{cwd}`）取得整體地圖；需定位特定函數或類別時用 `search_identifiers`。嚴禁用 bash cat 逐檔讀取來理解專案架構。"
                        if has_repomapper else ""
                    )
                    + (
                        "\n- 逐步執行：複雜任務請分步進行。網頁操作需確認目標元素已存在於頁面結構中，再進行點擊或截圖。"
                        "\n- 嚴禁盲目重試：當 browser 操作因 selector 找不到元素失敗時，**嚴禁用相同 selector 重試**。必須立即呼叫 `browser_snapshot`（不帶 filename 參數），讀取回傳的頁面結構，找出正確的 ref 或描述後再繼續。"
                        "\n- 參數限制：`browser_take_screenshot` 的 `type` 參數僅接受 \"png\" 或 \"jpeg\"，不需要時請省略。"
                        if has_playwright else ""
                    )
                    + "\n- 嚴禁捏造：回答必須基於工具回傳的真實資料，工具失敗時誠實告知。"
                    "\n- 任務收尾：完成任務後，必須用文字清楚總結最終結果與狀態，不得無聲結束對話。"
                    if has_mcp
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
        """啟動時嘗試從 _history_file 載入過往對話，自動過濾時間問答。"""
        if self._history_file.exists():
            try:
                saved = json.loads(self._history_file.read_text(encoding="utf-8"))
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
                logger.debug(f"載入 {len(filtered)} 則歷史訊息，來自 {self._history_file}")
            except Exception as e:
                logger.warning(f"載入歷史失敗：{e}")

    def _rotate_history(self):
        """記憶條數超過 history_keep * 2 則時自動移除最舊的非-system 訊息。"""
        keep = self.history_keep if self.history_keep else HISTORY_KEEP
        system = [m for m in self.history if m["role"] == "system"]
        rest = [m for m in self.history if m["role"] != "system"]
        if len(rest) > keep * 2:
            rest = rest[-(keep * 2) :]
            self.history = system + rest
            logger.debug(f"歷史 rotate，保留 {len(rest)} 則")

    def reset_history(self):
        """切換工作目錄後清空並重新載入新 cwd 的 history.json。"""
        self.history = []
        self._rebuild_system_prompt()
        self._load_history()

    def save_history(self):
        """將對話歷史寫入 _history_file，只存最近 history_keep 個 pair。"""
        keep = self.history_keep if self.history_keep else HISTORY_KEEP
        system = [m for m in self.history if m["role"] == "system"]
        rest = [m for m in self.history if m["role"] != "system"]
        to_save = system + rest[-(keep * 2):]
        try:
            self._history_file.write_text(
                json.dumps(to_save, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug(f"對話已存入 {self._history_file}，共 {len(to_save)} 則")
        except Exception as e:
            logger.warning(f"儲存歷史失敗：{e}")

    async def compact_history(self) -> str:
        """用 gemini-2.0-flash 摘要現有 history，壓縮 context。回傳摘要文字。"""
        _compact_client = self._gemini or self._deepseek
        if not _compact_client:
            raise RuntimeError("需要 GEMINI_API_KEY 或 DEEPSEEK_API_KEY 才能使用 compact")

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

        _compact_model = "deepseek-v4-flash" if not self._gemini and self._deepseek else "gemini-2.0-flash"
        resp = await _compact_client.chat.completions.create(
            model=_compact_model,
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
        """保留 system message + 最近 history_keep 個 pair 送給 AI。"""
        keep = self.history_keep if self.history_keep else HISTORY_KEEP
        system = [m for m in self.history if m["role"] == "system"]
        rest = [m for m in self.history if m["role"] != "system"]
        return system + rest[-(keep * 2):]

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

    async def chat(self, user_msg: dict, on_token=None, on_thinking=None, on_done=None, on_tool_call=None, on_retry=None):
        """
        單輪對話入口，支援 tool calling loop：
          1. 呼叫模型（stream）
          2. 若模型要求 tool call → 並發執行 → 把結果加入 history → 再次呼叫
          3. 模型不再呼叫工具時結束
        """
        if "images" in user_msg and not self.profile.supports_vision:
            raise ValueError(f"{self.profile.name} 不支援圖片輸入")

        self._rebuild_system_prompt()
        self.history.append(user_msg)
        messages = self._trim_history()

        # 根據模型特性决定要傳哪些工具
        active_tools = []
        if self.profile.supports_tools and self.tools_map:
            active_tools.extend([SEARCH_TOOL, FETCH_URLS_TOOL, BASH_TOOL])
            if "open_agent" in self.tools_map:
                active_tools.append(OPEN_AGENT_TOOL)
        if self.profile.supports_tools and self.mcp_client:
            active_tools.extend(self.mcp_client.tools)

        self._last_usage: tuple = (None, None)
        self._last_cache_hit: int | None = None
        self._t_thinking_end: float | None = None
        tool_call_count = 0
        while True:
            reply_parts: list[str] = []
            tool_calls: list = []
            thinking_parts: list[str] = []
            t_start = time.perf_counter()
            self._t_thinking_end = None

            try:
                if self.profile.backend in ("gemini", "deepseek"):
                    _client = self._gemini if self.profile.backend == "gemini" else self._deepseek
                    await stream_openai_compat(
                        self, _client, messages, active_tools, reply_parts, tool_calls, thinking_parts,
                        on_token, on_thinking,
                    )
                else:
                    await stream_ollama(
                        self, messages, active_tools, reply_parts, tool_calls, thinking_parts,
                        on_token, on_thinking, on_retry,
                    )
            except Exception as e:
                logger.error(f"模型串流錯誤：{e}")
                if self.history and self.history[-1]["role"] == "user":
                    self.history.pop()
                raise

            if self._cancelled:
                self._cancelled = False
                if self.history and self.history[-1]["role"] == "user":
                    self.history.pop()
                return

            t_total = time.perf_counter() - t_start
            usage = self._last_usage
            _cache_str = ""
            if self._last_cache_hit is not None and usage[1]:
                _cache_str = f"，cache hit {self._last_cache_hit}/{usage[1]} ({self._last_cache_hit * 100 // usage[1]}%)"
            if self._t_thinking_end:
                logger.debug(
                    f"思考 {self._t_thinking_end - t_start:.2f}s，總耗時 {t_total:.2f}s "
                    f"| 生成 {usage[0]} tokens，輸入 {usage[1]} tokens{_cache_str}"
                )
            else:
                logger.debug(
                    f"總耗時 {t_total:.2f}s | 生成 {usage[0]} tokens，輸入 {usage[1]} tokens{_cache_str}"
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
                _full_reply = "".join(reply_parts)
                _next_m = re.search(r"\s*<next>(.*?)</next>\s*$", _full_reply, re.DOTALL)
                if _next_m:
                    predicted = _next_m.group(1).strip()
                    _full_reply = _full_reply[: _next_m.start()].rstrip()
                    # 超過 50 字代表模型把回覆內容誤放進 <next>，不顯示為 ghost text
                    self.last_prediction = predicted if len(predicted) <= 50 else ""
                    logger.debug(f"<next> extracted ({len(predicted)}): {repr(predicted)}")
                else:
                    self.last_prediction = ""
                assistant_msg: dict = {"role": "assistant", "content": _full_reply}
                if self.profile.backend == "deepseek" and thinking_parts:
                    assistant_msg["reasoning_content"] = "".join(thinking_parts)
                self.history.append(assistant_msg)
                self._rotate_history()
                self.save_history()
                if on_done:
                    await on_done(t_total, self._t_thinking_end and self._t_thinking_end - t_start, usage[0], usage[1])
                return

            # Build assistant message with tool calls (format differs by backend)
            if self.profile.backend in ("gemini", "deepseek"):
                _asst: dict = {
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
                }
                if self.profile.backend == "deepseek" and thinking_parts:
                    _asst["reasoning_content"] = "".join(thinking_parts)
                messages.append(_asst)
            else:
                messages.append({
                    "role": "assistant",
                    "content": "".join(reply_parts),
                    "tool_calls": tool_calls,
                })

            results = await asyncio.gather(
                *[run_tool(tc, self.tools_map, self.mcp_client) for tc in tool_calls]
            )

            if self._cancelled:
                self._cancelled = False
                if self.history and self.history[-1]["role"] == "user":
                    self.history.pop()
                return

            if on_tool_call:
                for tc, (fn_name, result) in zip(tool_calls, results):
                    raw = getattr(tc.function, "arguments", {})
                    args = json.loads(raw) if isinstance(raw, str) else dict(raw)
                    await on_tool_call(fn_name, args, result)

            # Tool result messages (format differs by backend)
            if self.profile.backend in ("gemini", "deepseek"):
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
                logger.warning(f"已達工具呼叫上限 {MAX_TOOL_CALLS} 次，強制結束")
                return
