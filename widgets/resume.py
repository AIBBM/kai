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
        Binding("d", "delete", "刪除"),
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

    def __init__(self, entries: list[dict], archives: list[Path], names_file: Path, current_archive: Path | None = None) -> None:
        """
        entries:        list of dicts with keys: ts, count, preview
        archives:       corresponding archive Path list (same order as entries)
        names_file:     path to .history/.names.json
        current_archive: 目前正在使用的歸檔路徑（_resumed_from），用於標記當前 session
        dismiss value:  selected index (0-based) or None if cancelled
        """
        super().__init__()
        self._entries = entries
        self._archives = archives
        self._names_file = names_file
        self._current_archive = current_archive
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
        arch = self._archives[i]
        name = self._names.get(arch.name, "")
        is_current = self._current_archive is not None and arch == self._current_archive
        return Option(
            Text.assemble(
                ("▶ " if is_current else "  ", "bold green"),
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
            yield Label("[dim]↑↓ 選擇  Enter / 點擊 載入  r 重命名  d 刪除  Esc 關閉[/dim]", id="resume-hint")

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

    # ── delete ───────────────────────────────────────────────────────────────

    def action_delete(self) -> None:
        if self.query_one("#rename-input", Input).display:
            return  # 正在改名，忽略
        ol = self.query_one("#resume-list", OptionList)
        idx = ol.highlighted
        if idx is None:
            return
        arch = self._archives[idx]
        # 刪除實體檔案
        try:
            arch.unlink(missing_ok=True)
        except Exception:
            pass
        # 清理 .names.json
        self._names.pop(arch.name, None)
        try:
            self._names_file.write_text(
                json.dumps(self._names, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        # 如果刪的是目前 resumed session，清除標記
        if self._current_archive == arch:
            self._current_archive = None
        # 從清單移除並重建（序號需要重排）
        self._entries.pop(idx)
        self._archives.pop(idx)
        if not self._entries:
            self.dismiss(None)
            return
        self._rebuild_list(min(idx, len(self._entries) - 1))

    def _rebuild_list(self, highlight: int) -> None:
        """清空並重建 OptionList，保持序號連續。"""
        ol = self.query_one("#resume-list", OptionList)
        ol.clear_options()
        for i in range(len(self._entries)):
            ol.add_option(self._make_option(i))
        ol.highlighted = highlight

    # ── select ────────────────────────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self.query_one("#rename-input", Input).display:
            return  # 正在改名，忽略點擊
        self.dismiss(int(event.option_id))
