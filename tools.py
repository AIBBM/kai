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

from config import MAX_SEARCH_CHARS, SEARCH_RETRIES, SEARCH_TIMEOUT, SEARXNG_URL

# Ollama tool calling 格式定義
SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for up-to-date information.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
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
    """移除 ANSI 跳脸碼、控制字元、常見隱藏標簽。"""
    return CLEAN_PATTERN.sub("", text)


class WebSearchArgs(BaseModel):
    """web_search 入參格式，對 AI 傳入的參數做強型別校驗。"""

    query: str
    num_results: int = 3

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


async def web_search(query: str, num_results: int = 3) -> str:
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


async def run_tool(tc, tools_map: dict) -> tuple[str, str]:
    """由工具對映表動態分發，校驗參數後執行。"""
    import json

    fn_name = tc.function.name
    raw_args = tc.function.arguments
    raw = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

    if fn_name not in tools_map:
        return fn_name, f"[Error] 未知工具：{fn_name}"

    try:
        args = WebSearchArgs.model_validate(raw)
    except ValidationError as e:
        error_msg = f"[ValidationError] {e.error_count()} 個錯誤:\n{e}"
        logger.warning(f"tool call 參數校驗失敗：{error_msg}")
        return fn_name, error_msg

    fn = tools_map[fn_name]
    return fn_name, await fn(args.query, args.num_results)


if __name__ == "__main__":
    import sys

    query = sys.argv[1] if len(sys.argv) > 1 else "台北天氣"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    print(asyncio.run(web_search(query, n)))
