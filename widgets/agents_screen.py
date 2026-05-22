"""AgentsScreen — agent 清單選擇器 ModalScreen（DataTable）。"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Label


class AgentsScreen(ModalScreen[str | None]):
    """顯示 agents 清單；選取後 dismiss(dir_name)，Escape dismiss(None)。"""

    BINDINGS = [Binding("escape,q", "dismiss", show=False)]

    DEFAULT_CSS = """
    AgentsScreen {
        align: center middle;
    }
    #agents-dialog {
        width: 90%;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #agents-title {
        text-style: bold;
        color: $accent;
        content-align: center middle;
        height: 1;
        margin-bottom: 1;
    }
    #agents-table {
        height: 1fr;
    }
    #agents-hint {
        color: $text-muted;
        content-align: center middle;
        height: 1;
        margin-top: 1;
    }
    """

    def __init__(self, agents: list[dict]) -> None:
        super().__init__()
        self._agents = agents

    def compose(self) -> ComposeResult:
        with Vertical(id="agents-dialog"):
            yield Label("🤖 Agent 清單", id="agents-title")
            yield DataTable(cursor_type="row", id="agents-table")
            yield Label("[dim]↑↓ 選擇  Enter / 點擊 載入  Esc 關閉[/dim]", id="agents-hint")

    def on_mount(self) -> None:
        dt = self.query_one("#agents-table", DataTable)
        dt.add_columns("目錄", "名稱", "暱稱", "模型", "說明")
        for a in self._agents:
            dt.add_row(
                a["dir"],
                a["name"],
                a.get("nickname") or "—",
                a["model"],
                a.get("description") or "—",
                key=a["dir"],
            )
        dt.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value))
