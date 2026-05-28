"""ChatApp 狀態群組 — 將 ChatApp.__init__ 的零散 self._xxx 依語意分組。

每個 dataclass 代表一個狀態域，掛在 ChatApp 上以具名屬性存取：
  self.session    — SessionState
  self.input_state — InputState
  self.ui_state   — UIState
  self.tab_state  — TabState

asyncio 生命週期物件（_mcp_task、_bash_proc、_tts_future 等）
不屬於任何狀態域，仍以 self._xxx 存放於 ChatApp。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent import Agent


@dataclass
class SessionState:
    """Session 識別與目錄導航狀態。"""

    resumed_from: Path | None = None
    tab_resumed_from: dict[str, Path | None] = field(default_factory=dict)
    name: str = ""
    tab_names: dict[str, str] = field(default_factory=dict)
    prev_cwd: str = ""


@dataclass
class InputState:
    """提示列輸入歷史、游標導航與暫存狀態。"""

    history: list[str] = field(default_factory=list)
    history_idx: int = -1
    history_draft: str = ""
    last_send_text: str = ""
    restore_input: bool = False
    last_ephemeral: list[dict] | None = None


@dataclass
class UIState:
    """UI 控制旗標與 spinner 狀態。"""

    debug_tools: bool = False
    think_override: bool | None = None
    spin_idx: int = 0
    spin_label: str = "生成中"


@dataclass
class TabState:
    """Tab 管理：agent tabs、檔案 tab 計數、pending 工具排程。"""

    agent_tabs: dict[str, "Agent"] = field(default_factory=dict)
    main_agent: "Agent | None" = None
    file_tab_counter: int = 0
    pending_open_agent: str | None = None
