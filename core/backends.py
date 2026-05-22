"""Streaming backend implementations for Ollama and OpenAI-compatible APIs.

Each function takes an Agent instance as first argument and mutates its
_last_usage / _last_cache_hit / _t_thinking_end fields in-place, the same
way the former Agent methods did.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import ollama
from loguru import logger
from openai import AsyncOpenAI

from .config import OLLAMA_STREAM_TIMEOUT, TEMPERATURE, TOP_K, TOP_P
from .tools import clean_thinking

if TYPE_CHECKING:
    from .agent import Agent


async def stream_ollama(
    agent: "Agent",
    messages: list,
    active_tools: list,
    reply_parts: list[str],
    tool_calls: list,
    thinking_parts: list[str],
    on_token,
    on_thinking,
    on_retry,
) -> None:
    """Ollama 串流，含 XML 解析錯誤自動重試。"""
    _xml_retry = 0
    _XML_RETRY_MAX = 2
    while True:
        try:
            stream = await agent._ollama.chat(
                model=agent.profile.name,
                messages=messages,
                think=agent.profile.supports_thinking,
                tools=active_tools,
                stream=True,
                options={"num_ctx": agent.profile.num_ctx, "temperature": TEMPERATURE, "top_k": TOP_K, "top_p": TOP_P},
            )
        except httpx.ConnectError:
            raise

        try:
            _it = stream.__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(_it.__anext__(), timeout=OLLAMA_STREAM_TIMEOUT)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    raise asyncio.TimeoutError(f"Ollama 超過 {OLLAMA_STREAM_TIMEOUT}s 未回應，可能已卡住")
                if agent._cancelled:
                    break
                msg = chunk.message
                agent._last_usage = (getattr(chunk, "eval_count", None), getattr(chunk, "prompt_eval_count", None))

                if msg.thinking:
                    thinking_parts.append(clean_thinking(msg.thinking))

                if msg.content:
                    if not reply_parts and thinking_parts:
                        agent._t_thinking_end = time.perf_counter()
                        if on_thinking:
                            await on_thinking("".join(thinking_parts))
                    reply_parts.append(msg.content)
                    if on_token:
                        await on_token(msg.content)

                for tc in msg.tool_calls or []:
                    tool_calls.append(tc)
            return

        except ollama.ResponseError as e:
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


async def stream_openai_compat(
    agent: "Agent",
    client: AsyncOpenAI,
    messages: list,
    active_tools: list,
    reply_parts: list[str],
    tool_calls: list,
    thinking_parts: list[str],
    on_token,
    on_thinking,
) -> None:
    """OpenAI-compatible 串流（Gemini / DeepSeek）。"""
    from openai import NOT_GIVEN, RateLimitError
    is_deepseek = agent.profile.backend == "deepseek"
    extra: dict = {}
    if is_deepseek and agent.profile.supports_thinking:
        extra["extra_body"] = {"thinking": {"type": "enabled"}}
        extra["reasoning_effort"] = "high"
    elif is_deepseek:
        extra["extra_body"] = {"thinking": {"type": "disabled"}}
    try:
        stream = await client.chat.completions.create(
            model=agent.profile.name,
            messages=messages,
            tools=active_tools or NOT_GIVEN,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            stream=True,
            stream_options={"include_usage": True},
            **extra,
        )
    except RateLimitError as e:
        logger.warning(f"API 配額不足（{agent.profile.backend}）：{e}")
        raise RuntimeError(f"❌ API 配額已用盡，請稍後再試。\n{e}") from e

    _tc_buf: dict[int, dict] = {}

    async for chunk in stream:
        if agent._cancelled:
            break
        if chunk.usage:
            agent._last_usage = (chunk.usage.completion_tokens, chunk.usage.prompt_tokens)
            hit = getattr(chunk.usage, "prompt_cache_hit_tokens", None)
            if hit is None and getattr(chunk.usage, "model_extra", None):
                hit = chunk.usage.model_extra.get("prompt_cache_hit_tokens")
            if hit is not None:
                agent._last_cache_hit = hit
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        reasoning = getattr(delta, "reasoning_content", None) or (
            delta.model_extra.get("reasoning_content")
            if getattr(delta, "model_extra", None)
            else None
        )
        if reasoning:
            thinking_parts.append(reasoning)

        if delta.content:
            if not reply_parts and thinking_parts:
                agent._t_thinking_end = time.perf_counter()
                if on_thinking:
                    await on_thinking("".join(thinking_parts))
            reply_parts.append(delta.content)
            if on_token:
                await on_token(delta.content)

        for tc_chunk in (delta.tool_calls or []):
            i = tc_chunk.index
            if i not in _tc_buf:
                _tc_buf[i] = {"id": tc_chunk.id or "", "name": "", "args": ""}
            if tc_chunk.function:
                _tc_buf[i]["name"] += tc_chunk.function.name or ""
                _tc_buf[i]["args"] += tc_chunk.function.arguments or ""

    for idx in sorted(_tc_buf):
        d = _tc_buf[idx]
        tool_calls.append(SimpleNamespace(
            id=d["id"],
            function=SimpleNamespace(name=d["name"], arguments=d["args"]),
        ))
