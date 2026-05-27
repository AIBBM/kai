"""AgentsScreen — agent 清單選擇器 ModalScreen（DataTable）。"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label


class ConfirmDeleteAgentScreen(ModalScreen[bool]):
    """刪除 agent 確認對話框。"""

    BINDINGS = [
        Binding("y", "confirm", show=False),
        Binding("n,escape", "cancel", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmDeleteAgentScreen {
        align: center middle;
    }
    ConfirmDeleteAgentScreen Vertical {
        width: 52;
        height: auto;
        padding: 1 2;
        border: round $error;
        background: $surface;
    }
    ConfirmDeleteAgentScreen Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }
    ConfirmDeleteAgentScreen Horizontal {
        width: 100%;
        height: auto;
        align: center middle;
    }
    ConfirmDeleteAgentScreen Button {
        margin: 0 1;
    }
    """

    def __init__(self, agent_name: str, scope_path: str, dir_name: str) -> None:
        super().__init__()
        self._agent_name = agent_name
        self._scope_path = scope_path
        self._dir_name = dir_name

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"刪除 agent？")
            yield Label(f"[bold]{self._agent_name}[/bold]  [dim]({self._scope_path}/{self._dir_name})[/dim]", markup=True)
            with Horizontal():
                yield Button("刪除", id="confirm", variant="error")
                yield Button("取消", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class AgentsScreen(ModalScreen[str | None]):
    """顯示 agents 清單；選取後 dismiss("scope:dir_name")，Escape dismiss(None)。

    mode="load"   → 選取後載入 agent
    mode="delete" → 選取後刪除 agent
    """

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
    AgentsScreen.delete-mode #agents-dialog {
        border: thick $error;
    }
    #agents-title {
        text-style: bold;
        color: $accent;
        content-align: center middle;
        height: 1;
        margin-bottom: 1;
    }
    AgentsScreen.delete-mode #agents-title {
        color: $error;
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

    def __init__(self, agents: list[dict], mode: str = "load") -> None:
        super().__init__()
        self._agents = agents
        self._mode = mode
        if mode == "delete":
            self.add_class("delete-mode")

    def compose(self) -> ComposeResult:
        title = "🗑 刪除 Agent" if self._mode == "delete" else "🤖 Agent 清單"
        hint = "[dim]↑↓ 選擇  Enter / 點擊 刪除  Esc 關閉[/dim]" if self._mode == "delete" \
            else "[dim]↑↓ 選擇  Enter / 點擊 載入  Esc 關閉[/dim]"
        with Vertical(id="agents-dialog"):
            yield Label(title, id="agents-title")
            yield DataTable(cursor_type="row", id="agents-table")
            yield Label(hint, id="agents-hint")

    def on_mount(self) -> None:
        dt = self.query_one("#agents-table", DataTable)
        dt.add_columns("來源", "名稱", "暱稱", "模型", "說明")
        for a in self._agents:
            scope_label = "🏠 本地" if a.get("scope") == "local" else "🌐 全域"
            dt.add_row(
                scope_label,
                a["name"],
                a.get("nickname") or "—",
                a["model"],
                a.get("description") or "—",
                key=f"{a.get('scope', 'local')}:{a['dir']}",
            )
        dt.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value))
