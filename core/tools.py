import asyncio
import re
import time

import httpx
from loguru import logger
from pydantic import BaseModel, ValidationError, field_validator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import BASH_TIMEOUT, FETCH_MAX_CHARS, JINA_API_KEY, MAX_SEARCH_CHARS, SEARCH_NUM_RESULTS, SEARCH_RETRIES, SEARCH_TIMEOUT, SEARXNG_URL

OPEN_AGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "open_agent",
        "description": (
            "Open a specialized sub-agent tab in the UI. "
            "Use when the user asks to switch to, open, or activate a named agent (e.g. '幫我開台股分析師'). "
            "Pass the agent's name or a descriptive keyword as the query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Agent name, nickname, or description keyword"},
            },
            "required": ["query"],
        },
    },
}

BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash_exec",
        "description": (
            "Execute a bash command and return stdout/stderr. "
            "Use for file operations, running scripts, installing packages, checking system state. "
            "Prefer chaining commands with && over multiple tool calls."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Bash command to execute"},
            },
            "required": ["command"],
        },
    },
}

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web and return titles, URLs, and short snippets. "
            "Results are summaries only — use fetch_urls to read the full content of specific pages."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    },
}

FETCH_URLS_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch_urls",
        "description": (
            "Fetch the full Markdown content of 1–5 web pages concurrently via Jina Reader. "
            "Use after web_search when snippets are not enough and you need the complete article or documentation. "
            "Pass a single-element list for one URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of full URLs to fetch (max 5)",
                }
            },
            "required": ["urls"],
        },
    },
}

# 對應 ANSI escape code、控制字元、常見模型隱藏標簽
CLEAN_PATTERN = re.compile(
    r"\x1b\[[0-9;]*[mABCDHJKSTf]"
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
    r"|<\/?(?:think|thinking|scratchpad|internal)[^>]*>"
)


def clean_thinking(text: str) -> str:
    """移除 ANSI 跳脫碼、控制字元、常見隱藏標簽。"""
    return CLEAN_PATTERN.sub("", text)


class WebSearchArgs(BaseModel):
    """web_search 入參格式，對 AI 傳入的參數做強型別校驗。"""

    query: str
    num_results: int = SEARCH_NUM_RESULTS

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query 不能為空字串")
        return v.strip()

    @field_validator("num_results")
    @classmethod
    def num_results_range(cls, v: int) -> int:
        if not 1 <= v <= 10:
            raise ValueError("num_results 必須介於 1-10")
        return v


def estimate_tokens(text: str) -> int:
    """粗估 token 數：中文字元 ÷ 1.5，其餘字元 ÷ 4。"""
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - cjk
    return int(cjk / 1.5 + other / 4)


def budget_results(results: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """控制搜尋結果的總字元數在 MAX_SEARCH_CHARS 以內。"""
    total = sum(len(r) for _, r in results)
    if total <= MAX_SEARCH_CHARS:
        return results
    approx_tokens = estimate_tokens("".join(r for _, r in results))
    logger.warning(
        f"搜尋結果共 {total} 字元（約 {approx_tokens} tokens），超出限制 {MAX_SEARCH_CHARS}，壓縮中"
    )
    compressed, budget = [], MAX_SEARCH_CHARS
    for fn_name, content in results:
        if budget <= 0:
            break
        compressed.append((fn_name, content[:budget]))
        budget -= len(content)
    return compressed


async def web_search(query: str, num_results: int = SEARCH_NUM_RESULTS) -> str:
    """非同步打 SearXNG API，使用 tenacity 做 exponential backoff 重試。"""
    t0 = time.perf_counter()

    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:

        @retry(
            stop=stop_after_attempt(SEARCH_RETRIES + 1),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
            before_sleep=lambda state: logger.warning(
                f"搜尋 '{query}' 第 {state.attempt_number} 次失敗，重試中..."
            ),
            reraise=True,
        )
        async def _fetch() -> list:
            resp = await client.get(SEARXNG_URL, params={"q": query, "format": "json"})
            resp.raise_for_status()
            return resp.json().get("results", [])

        try:
            items = (await _fetch())[:num_results]
        except Exception as e:
            logger.error(f"搜尋 '{query}' 全部重試失敗：{e}")
            return f"[Search failed: {e}]"

    elapsed = time.perf_counter() - t0
    logger.debug(f"搜尋 '{query}' 完成，{len(items)} 筆，{elapsed:.2f}s")
    if not items:
        return "No results found."

    pairs = [
        (r["title"], f"[{r['title']}]({r['url']})\n{r.get('content', '')}")
        for r in items
    ]
    budgeted = budget_results(pairs)
    result = "\n\n".join(text for _, text in budgeted)
    logger.debug(f"傳給模型的內容（{len(result)} 字元）:\n{result}")
    return result


def _jina_headers() -> dict:
    h = {"Accept": "application/json"}
    if JINA_API_KEY:
        h["Authorization"] = f"Bearer {JINA_API_KEY}"
    return h


async def _fetch_one(url: str, client: httpx.AsyncClient, max_chars: int) -> str:
    try:
        resp = await client.get(
            f"https://r.jina.ai/{url}", headers=_jina_headers(), follow_redirects=True
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        title = data.get("title", "")
        page_url = data.get("url", url)
        content = data.get("content", "").strip()
        if len(content) > max_chars:
            cut = content.rfind("\n\n", 0, max_chars)
            content = content[: cut if cut > 0 else max_chars]
            content += f"\n\n…（共 {len(content)} 字元，已截斷）"
        logger.debug(f"fetch_url {url} → {len(content)} 字元")
        return f"# {title}\nURL: {page_url}\n\n{content}"
    except Exception as e:
        logger.warning(f"fetch_url 失敗：{url}：{e}")
        return f"[Error] fetch_url 失敗：{e}"


async def fetch_url(url: str) -> str:
    """用 Jina Reader 抓取單一網頁完整內容，回傳結構化 Markdown。"""
    async with httpx.AsyncClient(timeout=20.0) as client:
        return await _fetch_one(url, client, FETCH_MAX_CHARS)


async def fetch_urls(urls: list) -> str:
    """並行抓取多個網頁（最多 5 個），共用同一個 httpx 連線池。"""
    targets = urls[:5]
    per_limit = max(FETCH_MAX_CHARS // len(targets), 2000)
    async with httpx.AsyncClient(timeout=20.0) as client:
        results = await asyncio.gather(
            *[_fetch_one(u, client, per_limit) for u in targets]
        )
    return "\n\n---\n\n".join(results)


async def bash_exec(command: str, timeout: int = 0) -> str:
    """執行 bash 指令，回傳 stdout + stderr。

    使用 proc.wait() 而非 communicate()，只等 shell 本身退出。
    讀取用獨立 task，shell 退出後立即 cancel，避免背景程序繼承 pipe 導致 deadlock。
    """
    t = timeout or BASH_TIMEOUT

    async def _collect(reader: asyncio.StreamReader, chunks: list[bytes]) -> None:
        try:
            while chunk := await reader.read(65536):
                chunks.append(chunk)
        except Exception:
            pass

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []
        out_task = asyncio.create_task(_collect(proc.stdout, out_chunks))
        err_task = asyncio.create_task(_collect(proc.stderr, err_chunks))

        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), timeout=t)
        except asyncio.TimeoutError:
            timed_out = True
        finally:
            # shell 退出（或超時）後立即停止讀取，防止背景程序的 pipe 繼承造成卡死
            out_task.cancel()
            err_task.cancel()
            await asyncio.gather(out_task, err_task, return_exceptions=True)

        if timed_out:
            logger.debug(f"bash_exec timeout {t}s: {command[:60]}")
            return f"[Error] 超時 {t}s"

        out = b"".join(out_chunks).decode(errors="replace").strip()
        err = b"".join(err_chunks).decode(errors="replace").strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr]\n{err}" if proc.returncode != 0 else err)
        result = "\n".join(parts) or "(no output)"
        logger.debug(f"bash_exec exit={proc.returncode}: {command[:60]}")
        if proc.returncode != 0:
            result = f"[exit code {proc.returncode}]\n{result}"
        return result

    except Exception as e:
        return f"[Error] {e}"


async def run_tool(tc, tools_map: dict, mcp_client=None) -> tuple[str, str]:
    """由工具對映表動態分發；MCP tool 優先，其次本地 tools_map。"""
    import json

    fn_name = tc.function.name
    raw_args = tc.function.arguments
    raw = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

    # MCP tool
    if mcp_client and fn_name in mcp_client.tool_names:
        try:
            result = await mcp_client.call(fn_name, raw)
            return fn_name, result
        except Exception as e:
            logger.error(f"MCP tool '{fn_name}' 呼叫失敗：{e}")
            return fn_name, f"[MCP Error] {e}"

    # 本地 tool
    if fn_name not in tools_map:
        return fn_name, f"[Error] 未知工具：{fn_name}"

    fn = tools_map[fn_name]
    if fn_name == "web_search":
        try:
            args = WebSearchArgs.model_validate(raw)
        except ValidationError as e:
            error_msg = f"[ValidationError] {e.error_count()} 個錯誤:\n{e}"
            logger.warning(f"tool call 參數校驗失敗：{error_msg}")
            return fn_name, error_msg
        return fn_name, await fn(args.query, args.num_results)

    return fn_name, await fn(**raw)


if __name__ == "__main__":
    import sys

    query = sys.argv[1] if len(sys.argv) > 1 else "台北天氣"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    print(asyncio.run(web_search(query, n)))
