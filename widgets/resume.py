"""ResumeScreen — 歸檔 session 選擇器 ModalScreen。"""
from __future__ import annotations

import json
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option


class ResumeScreen(ModalScreen[int | None]):
    """顯示歸檔 session 清單；選取後 dismiss(index)，Escape dismiss(None)。"""

    BINDINGS = [
        Binding("escape", "dismiss_or_cancel", show=False),
        Binding("q", "dismiss_or_cancel", show=False),
        Binding("r", "start_rename", "重命名"),
    ]

    DEFAULT_CSS = """
    ResumeScreen {
        align: center middle;
    }
    #resume-dialog {
        width: 82%;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #resume-title {
        text-style: bold;
        color: $accent;
        content-align: center middle;
        height: 1;
        margin-bottom: 1;
    }
    #resume-list {
        height: 1fr;
    }
    #rename-input {
        display: none;
        margin-top: 1;
    }
    #resume-hint {
        color: $text-muted;
        content-align: center middle;
        height: 1;
        margin-top: 1;
    }
    """

    def __init__(self, entries: list[dict], archives: list[Path], names_file: Path) -> None:
        """
        entries:    list of dicts with keys: ts, count, preview
        archives:   corresponding archive Path list (same order as entries)
        names_file: path to .history/.names.json
        dismiss value: selected index (0-based) or None if cancelled
        """
        super().__init__()
        self._entries = entries
        self._archives = archives
        self._names_file = names_file
        self._names: dict[str, str] = {}
        if names_file.exists():
            try:
                self._names = json.loads(names_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        self._rename_idx: int | None = None

    # ── build ─────────────────────────────────────────────────────────────────

    def _make_option(self, i: int) -> Option:
        e = self._entries[i]
        name = self._names.get(self._archives[i].name, "")
        return Option(
            Text.assemble(
                (f"[{i + 1:2d}]  ", "dim"),
                (name + "  ", "bold yellow") if name else ("", ""),
                (e["ts"], "bold cyan"),
                ("  ", ""),
                (f"{e['count']:3d} 則  ", "green"),
                (e["preview"] + "…", ""),
            ),
            id=str(i),
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-dialog"):
            yield Label("📂 歸檔 Session", id="resume-title")
            yield OptionList(*[self._make_option(i) for i in range(len(self._entries))], id="resume-list")
            yield Input(placeholder="輸入名稱… Enter 確認  Esc 取消", id="rename-input")
            yield Label("[dim]↑↓ 選擇  Enter / 點擊 載入  r 重命名  Esc 關閉[/dim]", id="resume-hint")

    # ── rename flow ───────────────────────────────────────────────────────────

    def action_start_rename(self) -> None:
        ol = self.query_one("#resume-list", OptionList)
        idx = ol.highlighted
        if idx is None:
            return
        self._rename_idx = idx
        inp = self.query_one("#rename-input", Input)
        inp.value = self._names.get(self._archives[idx].name, "")
        inp.display = True
        inp.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if self._rename_idx is None:
            return
        idx = self._rename_idx
        name = event.value.strip()
        key = self._archives[idx].name
        if name:
            self._names[key] = name
        else:
            self._names.pop(key, None)
        try:
            self._names_file.parent.mkdir(exist_ok=True)
            self._names_file.write_text(
                json.dumps(self._names, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        self._finish_rename(idx)

    def action_dismiss_or_cancel(self) -> None:
        inp = self.query_one("#rename-input", Input)
        if inp.display:
            self._finish_rename(self._rename_idx)
        else:
            self.dismiss(None)

    def _finish_rename(self, restore_idx: int | None) -> None:
        inp = self.query_one("#rename-input", Input)
        inp.display = False
        self._rename_idx = None
        # 更新該列的顯示
        if restore_idx is not None:
            ol = self.query_one("#resume-list", OptionList)
            ol.replace_option_prompt(str(restore_idx), self._make_option(restore_idx).prompt)
        self.query_one("#resume-list", OptionList).focus()

    # ── select ────────────────────────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self.query_one("#rename-input", Input).display:
            return  # 正在改名，忽略點擊
        self.dismiss(int(event.option_id))
