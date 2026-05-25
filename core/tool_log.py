"""tool_log.py — Tool call 除錯日誌，DEBUG_TOOLS=1 時才寫檔。

格式（tool_calls.log，append mode）：
  [YYYY-MM-DD HH:MM:SS.mmm] #N  tool_name
  ARGS:
    {json pretty-print}
  RESULT: (前 2000 字元，超過截斷)
  ────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import DEBUG_TOOLS

_LOG_FILE = "tool_calls.log"
_MAX_RESULT = 2000
_SEPARATOR = "─" * 64

_call_counter: int = 0
_session_written: bool = False


def _log_path() -> Path:
    """動態取得目前 cwd 下的 log 路徑，跟著 /cd 變。"""
    return Path(_LOG_FILE)


def _write_session_header() -> None:
    """每次 process 啟動後第一次寫 log 時，插入 session 分隔線。"""
    global _session_written
    if _session_written:
        return
    _session_written = True
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n{'═' * 64}\n  SESSION START  {ts}\n{'═' * 64}\n"
    try:
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(header)
    except Exception:
        pass


def log_turn_start(turn: int) -> None:
    """記錄新的對話輪次開始（chat() 被呼叫）。"""
    if not DEBUG_TOOLS:
        return
    _write_session_header()
    ts = datetime.now().strftime("%H:%M:%S")
    try:
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(f"\n▶ Turn #{turn}  [{ts}]\n")
    except Exception:
        pass


def log_tool_call(tool_name: str, args: dict, result: str) -> None:
    """記錄單次 tool call 的名稱、參數、回應。DEBUG_TOOLS=0 時直接返回。"""
    if not DEBUG_TOOLS:
        return
    global _call_counter
    _call_counter += 1

    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    args_str = json.dumps(args, ensure_ascii=False, indent=2)
    if len(result) > _MAX_RESULT:
        result_str = result[:_MAX_RESULT] + f"\n…（截斷，共 {len(result)} 字元）"
    else:
        result_str = result

    block = (
        f"[{ts}] #{_call_counter}  {tool_name}\n"
        f"ARGS:\n{args_str}\n"
        f"RESULT:\n{result_str}\n"
        f"{_SEPARATOR}\n"
    )
    try:
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(block)
    except Exception:
        pass
