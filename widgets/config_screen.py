"""ConfigScreen — 互動式 config 面板：選取 key 後匯出到 local / global。"""
from __future__ import annotations

import json
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Label

_KEY_META: list[tuple[str, str, str]] = [
    # (key, 單位/型別, 說明)
    ("model",                 "str",   "使用的模型"),
    ("create_agent_model",    "str",   "/create-agent 設計模型（空=同 model）"),
    ("compact_model",         "str",   "/compact 壓縮模型（空=自動選擇）"),
    ("history_keep",          "pair",  "對話記憶保留幾個來回"),
    ("debug_tools",           "bool",  "顯示工具呼叫除錯卡片"),
    ("temperature",           "0-1",   "生成溫度（低=確定，高=創意）"),
    ("num_ctx",               "int",   "Context window 大小（0=模型預設）"),
    ("top_k",                 "int",   "Top-K 採樣（0=停用）"),
    ("top_p",                 "0-1",   "Nucleus sampling"),
    ("max_tool_calls",        "int",   "每輪工具呼叫上限"),
    ("bash_timeout",          "秒",    "bash_exec 指令逾時"),
    ("username",              "str",   "對話中顯示的使用者名稱"),
    ("searxng_url",           "url",   "SearXNG 搜尋 API 位址"),
    ("fetch_max_chars",       "字元",  "fetch_url 單頁回傳上限"),
    ("max_search_chars",      "字元",  "搜尋結果總字元上限"),
    ("search_num_results",    "int",   "每次搜尋回傳筆數"),
    ("search_timeout",        "秒",    "搜尋請求逾時"),
    ("search_retries",        "int",   "搜尋失敗重試次數"),
    ("ollama_stream_timeout", "秒",    "Ollama 每個 chunk 最長等待"),
    ("mcp_tool_timeout",      "秒",    "MCP 單次工具呼叫逾時"),
    ("dify_api_url",          "url",   "Dify 伺服器位址"),
    ("dify_top_k",            "int",   "Dify RAG 回傳段落數"),
    ("dify_rerank_provider",  "str",   "Dify rerank 供應商（xinference）"),
    ("dify_rerank_model",     "str",   "Dify rerank 模型名稱"),
]

_API_KEY_HINT = (
    "API 金鑰請設定在 .env：\n"
    "  GEMINI_API_KEY=...    DEEPSEEK_API_KEY=...    JINA_API_KEY=...    DIFY_API_KEY=..."
)

_CHECKED   = "[green]✓[/green]"
_UNCHECKED = "[dim]○[/dim]"


class ConfigScreen(ModalScreen[None]):
    """互動式 config 面板：Space 選取，L 匯出 local，G 匯出 global。"""

    BINDINGS = [
        Binding("escape,q", "dismiss",       show=False),
        Binding("space",    "toggle_row",    "選取/取消", show=True),
        Binding("a",        "toggle_all",    "全選/全不選", show=True),
        Binding("l",        "export_local",  "→ local",  show=True),
        Binding("g",        "export_global", "→ global", show=True),
    ]

    DEFAULT_CSS = """
    ConfigScreen {
        align: center middle;
    }
    #config-dialog {
        width: 92%;
        max-height: 88%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #config-title {
        text-style: bold;
        color: $accent;
        content-align: center middle;
        height: 1;
        margin-bottom: 1;
    }
    #config-table {
        height: 1fr;
    }
    #config-apikey {
        color: $warning;
        margin-top: 1;
        padding: 0 1;
    }
    #config-hint {
        color: $text-muted;
        content-align: center middle;
        height: 1;
        margin-top: 1;
    }
    """

    def __init__(self, values: dict) -> None:
        super().__init__()
        self._values = values
        self._selected: set[str] = {key for key, _, _ in _KEY_META}
        self._col_check: object = None

    def compose(self) -> ComposeResult:
        with Vertical(id="config-dialog"):
            yield Label("⚙️  Config 選取匯出", id="config-title")
            yield DataTable(cursor_type="row", id="config-table")
            yield Label(_API_KEY_HINT, id="config-apikey")
            yield Label(
                "[dim]Esc 關閉  Space 選取  A 全選  L→local  G→global[/dim]",
                id="config-hint",
            )

    def on_mount(self) -> None:
        dt = self.query_one("#config-table", DataTable)
        (self._col_check, *_) = dt.add_columns("", "key", "目前值", "單位", "說明")
        for key, unit, desc in _KEY_META:
            val = self._values.get(key, "—")
            dt.add_row(self._mark(key), key, str(val), unit, desc, key=key)
        dt.focus()

    # ------------------------------------------------------------------
    def _mark(self, key: str) -> str:
        return _CHECKED if key in self._selected else _UNCHECKED

    def _refresh_row(self, key: str) -> None:
        dt = self.query_one("#config-table", DataTable)
        dt.update_cell(key, self._col_check, self._mark(key), update_width=False)

    # ------------------------------------------------------------------
    def action_toggle_row(self) -> None:
        dt = self.query_one("#config-table", DataTable)
        idx = dt.cursor_row
        if not (0 <= idx < len(_KEY_META)):
            return
        key = _KEY_META[idx][0]
        if key in self._selected:
            self._selected.discard(key)
        else:
            self._selected.add(key)
        self._refresh_row(key)

    def action_toggle_all(self) -> None:
        all_keys = {key for key, _, _ in _KEY_META}
        self._selected = set() if self._selected == all_keys else all_keys
        for key, _, _ in _KEY_META:
            self._refresh_row(key)

    # ------------------------------------------------------------------
    def _export(self, path: Path, label: str) -> None:
        cfg = {k: v for k, v in self._values.items() if k in self._selected}
        if not cfg:
            self.notify("沒有選取任何 key", severity="warning")
            return
        if path.exists():
            self.notify(
                f"{label} 已存在，略過（先手動刪除再執行）",
                severity="warning",
                timeout=5,
            )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        self.notify(f"✅ 已寫入 {label}（{len(cfg)} 個 key）", severity="information", timeout=4)

    def action_export_local(self) -> None:
        self._export(Path(".kai") / "config.json", "cwd/.kai/config.json")

    def action_export_global(self) -> None:
        self._export(Path.home() / ".kai" / "config.json", "~/.kai/config.json")
