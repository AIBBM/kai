"""StreamingMixin — AI 串流回覆 worker。"""
from __future__ import annotations

import asyncio
import json
import re

from loguru import logger
from textual import work
from textual.widgets import Collapsible, Markdown, Static

from core.config import get_nickname
from widgets.chat import AIMessage
from widgets.input import HistoryInput
from widgets.sidebar import Sidebar
from widgets.tree import FileTree

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app import ChatApp as _MixinBase
else:
    _MixinBase = object  # type: ignore[assignment,misc]

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class StreamingMixin(_MixinBase):
    @work(exclusive=True)
    async def _send(self, text: str, images: list[str] | None = None, ephemeral: bool = False, original_text: str | None = None) -> None:
        # 快照 agent 與 msgs：worker 執行期間 self.agent 可能因 tab 切換被替換，
        # 固定使用送出當下的 agent，避免回覆跑到錯誤的 tab。
        agent = self.agent
        msgs = self._active_msgs()
        prompt = self.query_one("#prompt", HistoryInput)
        stream_msg: AIMessage | None = None
        buf: list[str] = []

        agent._cancelled = False
        if not ephemeral:
            self._resumed_from = None
            # 清除持久化 resume 狀態：有新訊息代表已脫離 resume 快照
            _state = agent._history_file.resolve().parent / ".history" / ".resumed_from"
            _state.unlink(missing_ok=True)
            try:
                from textual.widgets import TabbedContent
                tab_id = self.query_one("#center-tabs", TabbedContent).active or "chat"
                self._tab_resumed_from.pop(tab_id, None)
            except Exception:
                pass
        _history_snapshot = [m.copy() for m in agent.history] if ephemeral else None
        self._spin_idx = 0
        self._spin_label = "等待回覆"

        def _tick() -> None:
            self._spin_idx = (self._spin_idx + 1) % len(SPINNER)
            dbg = "  [dim]🔧 debug[/dim]" if self._debug_tools else ""
            prompt.border_title = f"{SPINNER[self._spin_idx]} {self._spin_label}{dbg}"

        timer = self.set_interval(0.08, _tick)
        prompt.add_class("streaming")
        prompt.border_title = f"{SPINNER[0]} 等待回覆"
        prompt.disabled = True
        prompt.placeholder = ""

        try:
            def _at_bottom() -> bool:
                return msgs.scroll_y >= msgs.max_scroll_y - 3

            _last_render: float = 0.0

            async def on_thinking(content: str) -> None:
                self._spin_label = "思考中"
                preview = content[:2000] + ("…" if len(content) > 2000 else "")
                await msgs.mount(Collapsible(
                    Static(preview, markup=False),
                    title="💡 思考過程",
                    collapsed=True,
                    classes="thinking",
                ))
                msgs.scroll_end(animate=False)

            async def on_token(chunk: str) -> None:
                nonlocal stream_msg, _last_render
                self._spin_label = "生成中"
                buf.append(chunk)
                if stream_msg is None:
                    await msgs.mount(Static(f"🤖 {get_nickname(agent.profile.name)}", classes="ai-header", markup=False))
                    stream_msg = AIMessage()
                    await msgs.mount(stream_msg)
                now = asyncio.get_event_loop().time()
                if now - _last_render >= 0.1:
                    _last_render = now
                    content = "".join(buf)
                    idx = content.find("<next>")
                    if idx != -1:
                        content = content[:idx].rstrip()
                    await stream_msg.update_content(content)
                    if _at_bottom():
                        msgs.scroll_end(animate=False)

            async def on_done(elapsed, thinking_s, gen_tok, prompt_tok) -> None:
                # 取 agent 已處理好的最終內容（已正確剝離 <next> 標籤），
                # 避免 buf regex 在 <next> 非結尾時截錯
                _hist = agent.history
                if _hist and _hist[-1]["role"] == "assistant":
                    _display = str(_hist[-1]["content"])
                else:
                    _display = re.sub(r"\s*<next>.*?</next>\s*$", "", "".join(buf), flags=re.DOTALL).rstrip()
                if stream_msg:
                    await stream_msg.update_content(_display, final=True)
                elif not buf:
                    await msgs.mount(Static("🤖 [dim]（已完成，無文字回覆）[/dim]", markup=True, classes="ai-header"))
                parts: list[str] = []
                if thinking_s:
                    parts.append(f"思考 {thinking_s:.1f}s")
                parts.append(f"共 {elapsed:.1f}s")
                if gen_tok:
                    parts.append(f"{gen_tok} tokens")
                if prompt_tok:
                    if agent.profile.backend == "ollama":
                        num_ctx = agent.profile.num_ctx
                        pct = prompt_tok * 100 // num_ctx
                        parts.append(f"ctx {pct}% ({prompt_tok}/{num_ctx})")
                    else:
                        parts.append(f"輸入 {prompt_tok} tokens")
                cache_hit = agent._last_cache_hit
                if cache_hit is not None and prompt_tok:
                    parts.append(f"cache {cache_hit * 100 // prompt_tok}%")
                stats = " · ".join(parts)
                at_bot = _at_bottom()
                await msgs.mount(Static(stats, classes="stats", markup=False))
                self.sub_title = f"{get_nickname(agent.profile.name)} · {stats}"
                self.query_one("#sidebar", Sidebar).refresh_git()
                self.query_one("#file-tree", FileTree).refresh_tree()
                if at_bot:
                    msgs.scroll_end(animate=False)
                # TTS：整段回覆完成後一次播放，等前一個完全結束才開新的
                _tts_cfg = getattr(agent, "_tts_config", None)
                if _tts_cfg and _display and not ephemeral:
                    from core.tts import speak
                    from loguru import logger as _log
                    _prev = getattr(self, "_tts_task", None)
                    if _prev and not _prev.done():
                        _prev.cancel()
                        try:
                            await _prev
                        except Exception:
                            pass
                    _engine = _tts_cfg.get("engine", "edge-tts")
                    _log.debug(f"tts: schedule engine={_engine} chars={len(_display)}")
                    self._tts_task = asyncio.create_task(speak(
                        _display,
                        voice=_tts_cfg.get("voice", "en-US-AriaNeural"),
                        player=_tts_cfg.get("player", "auto"),
                        engine=_engine,
                        lang=_tts_cfg.get("lang", "en-us"),
                        host=_tts_cfg.get("host", "localhost"),
                        port=int(_tts_cfg.get("port", 8886)),
                        speed=float(_tts_cfg.get("speed", 1.0)),
                    ))

            async def on_tool_call(name: str, args: dict, result: str) -> None:
                nonlocal stream_msg, buf
                self._spin_label = "工具回應中"
                if stream_msg and buf:
                    _pre = re.sub(r"\s*<next>.*?</next>\s*$", "", "".join(buf), flags=re.DOTALL).rstrip()
                    await stream_msg.update_content(_pre, final=True)
                stream_msg = None
                buf.clear()
                _ERR_PREFIXES = ("[MCP Error]", "[Error]", "[ValidationError]", "[stderr]", "Traceback (most recent call last)")
                _exit_m = re.search(r"\[exit code (\d+)\]", result)
                _exit_code = int(_exit_m.group(1)) if _exit_m else 0
                is_err = _exit_code >= 2 or any(kw in result for kw in _ERR_PREFIXES)
                is_warn = not is_err and _exit_code == 1
                _FS_MUTATING = {"write_file", "create_directory", "move_file", "delete_file", "edit_file"}
                if not is_err and not is_warn and name in _FS_MUTATING:
                    self.query_one("#file-tree", FileTree).refresh_tree()
                icon = "❌" if is_err else ("⚠️" if is_warn else "🔧")
                card_class = "tool-debug tool-debug-error" if is_err else "tool-debug"
                if not self._debug_tools:
                    if is_err:
                        args_preview = json.dumps(args, ensure_ascii=False)
                        if len(args_preview) > 300:
                            args_preview = args_preview[:300] + "…"
                        body = f"參數：{args_preview}\n\n{result[:600]}"
                        await msgs.mount(Collapsible(
                            Static(body, markup=False),
                            title=f"{icon} {name}  [dim]工具錯誤[/dim]",
                            collapsed=True,
                            classes=card_class,
                        ))
                        if _at_bottom():
                            msgs.scroll_end(animate=False)
                    return
                args_str = json.dumps(args, ensure_ascii=False, indent=2)
                cap = 1000
                result_preview = result[:cap] + (f"\n…（共 {len(result)} 字元）" if len(result) > cap else "")
                body = f"**參數**\n```json\n{args_str}\n```\n\n**回應**\n```\n{result_preview}\n```"
                at_bot = _at_bottom()
                await msgs.mount(Collapsible(
                    Markdown(body),
                    title=f"{icon} {name}",
                    collapsed=not is_err,
                    classes=card_class,
                ))
                if at_bot:
                    msgs.scroll_end(animate=False)

            async def on_retry() -> None:
                nonlocal stream_msg, buf
                buf.clear()
                if stream_msg:
                    await stream_msg.update_content("")

            agent.profile.supports_thinking = self._thinking_active()
            user_msg: dict = {"role": "user", "content": text}
            if images:
                user_msg["images"] = images
            await agent.chat(
                user_msg,
                on_token=on_token,
                on_thinking=on_thinking,
                on_done=on_done,
                on_tool_call=on_tool_call,
                on_retry=on_retry,
            )

        except Exception as e:
            err_str = str(e).lower()
            _overflow_kws = (
                "context", "too long", "window", "kv cache",
                "resource_exhausted", "maximum context", "exceeds the maximum",
                "token limit", "context length", "prompt is too long",
                "reduce the length",
            )
            if any(kw in err_str for kw in _overflow_kws) and (agent._gemini or agent._deepseek):
                logger.warning(f"context overflow，啟動 autocompact：{e}")
                try:
                    await msgs.mount(Static("⚡ Context 已滿，正在用 Gemini 壓縮對話歷史…", classes="error-msg", markup=False))
                    msgs.scroll_end(animate=False)
                    await agent.compact_history()
                    logger.info("autocompact 完成，重試對話")
                    await msgs.mount(Static("✅ 已壓縮，繼續對話", classes="error-msg", markup=False))
                    msgs.scroll_end(animate=False)
                    stream_msg = None
                    buf.clear()
                    retry_content = original_text if original_text else text
                    retry_msg: dict = {"role": "user", "content": retry_content}
                    if images:
                        retry_msg["images"] = images
                    await agent.chat(
                        retry_msg,
                        on_token=on_token,
                        on_thinking=on_thinking,
                        on_done=on_done,
                        on_tool_call=on_tool_call,
                        on_retry=on_retry,
                    )
                    if stream_msg and buf:
                        await stream_msg.update_content("".join(buf), final=True)
                except Exception as e2:
                    logger.exception("compact + retry error")
                    await msgs.mount(Static(f"❌ 壓縮後仍失敗：{e2}", classes="error-msg", markup=False))
                    msgs.scroll_end(animate=False)
            elif isinstance(e, TimeoutError):
                logger.error(f"Ollama stream timeout：{e}")
                await msgs.mount(Static(f"⏱ Ollama 無回應（逾時）：{e}", classes="error-msg", markup=False))
                msgs.scroll_end(animate=False)
            else:
                logger.exception("chat error")
                await msgs.mount(Static(f"❌ 錯誤：{e}", classes="error-msg", markup=False))
                msgs.scroll_end(animate=False)

        finally:
            timer.stop()
            prompt.remove_class("streaming")
            self._update_prompt_title()
            prompt.disabled = False
            prompt.placeholder = f"傳訊息給 {get_nickname(agent.profile.name)}..."
            prompt.focus()
            if ephemeral and _history_snapshot is not None:
                new_msgs = agent.history[len(_history_snapshot):]
                self._last_ephemeral = new_msgs if new_msgs else None
                agent.history = _history_snapshot
                agent.save_history()
                if self._last_ephemeral:
                    self.notify("💾 /save 可將此回答存入對話歷史", timeout=4)

        if not ephemeral:
            prediction = agent.last_prediction
            if prediction:
                prompt.show_prediction(prediction)
            _pending = getattr(self, "_pending_open_agent", None)
            if _pending:
                self._pending_open_agent = None
                await self._do_load_agent(_pending)
