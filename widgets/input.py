"""Input widgets: HistoryInput and ChatAutoComplete."""
from __future__ import annotations

from pathlib import Path

from textual import events
from textual.widgets import Input
from textual_autocomplete import AutoComplete, DropdownItem, TargetState

from .utils import _IMAGE_EXTS


class HistoryInput(Input):
    """Input with Up/Down history navigation and Ctrl+Z undo.

    Uses _on_key (runs before the binding system) to intercept up/down before
    ScrollView's scroll_up/scroll_down bindings consume them.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._undo_stack: list[tuple[str, int]] = []
        self._multiline_paste: str | None = None
        self._multiline_marker: str | None = None

    def on_paste(self, event: events.Paste) -> None:
        text = event.text
        if "\n" not in text and "\r" not in text:
            return
        event.prevent_default()
        raw = text.replace("\r\n", "\n").replace("\r", "\n").strip()

        last = getattr(self.app, "_last_file_copy", None)
        if last and last.get("text", "").strip() == raw:
            p = last["path"]
            fname = p.name if p else "unknown"
            s, e = last["start_line"], last["end_line"]
            loc = f"{fname}:{s}" if s == e else f"{fname}:{s}-{e}"
            raw = f"```{p.suffix.lstrip('.') if p else ''} {loc}\n{raw}\n```"

        self._multiline_paste = raw
        lines = raw.splitlines()
        preview = lines[0][:40] + ("…" if len(lines[0]) > 40 else "")
        marker = f"[{len(lines)} 行] {preview}"
        self._multiline_marker = marker
        self.value = marker
        self.cursor_position = len(self.value)

    def _on_key(self, event: events.Key) -> None:
        if event.key == "up":
            event.prevent_default()
            event.stop()
            self.app.action_history_prev()  # type: ignore[attr-defined]
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            self.app.action_history_next()  # type: ignore[attr-defined]
        elif event.key == "ctrl+z":
            event.prevent_default()
            event.stop()
            if self._undo_stack:
                val, pos = self._undo_stack.pop()
                self.value = val
                self.cursor_position = min(pos, len(val))
        else:
            _SAVE_BEFORE = frozenset(
                {"backspace", "delete", "ctrl+backspace", "ctrl+delete"}
            )
            if event.key in _SAVE_BEFORE or len(event.key) == 1:
                self._undo_stack.append((self.value, self.cursor_position))
                if len(self._undo_stack) > 200:
                    del self._undo_stack[:-200]


class ChatAutoComplete(AutoComplete):
    """動態補全：斜線指令、@path 檔案、/image 路徑、! bash 路徑。"""

    def _at_prefix(self, text: str) -> str | None:
        """回傳游標前最後一個 @ 之後的路徑片段；@ 後有空白表示已完成，回傳 None。"""
        idx = text.rfind("@")
        if idx == -1:
            return None
        if idx > 0 and text[idx - 1] not in " \t":
            return None  # @ 夾在字中間（如 email）
        fragment = text[idx + 1:]
        if " " in fragment:
            return None  # @ mention 已完成
        return fragment

    def get_search_string(self, state: TargetState) -> str:
        text = state.text
        if text.startswith("/image "):
            return text[7:]
        if text.startswith("/"):
            return text
        at = self._at_prefix(text)
        if at is not None:
            return at
        if text.startswith("!"):
            cmd_part = text[1:]
            tokens = cmd_part.split()
            if not tokens:
                return ""
            cmd0 = tokens[0]
            last = tokens[-1] if not text.endswith(" ") else ""
            if not last or last == cmd0:
                return ""
            if not last.startswith("-"):
                return last
        return ""

    def should_show_dropdown(self, search_string: str) -> bool:
        text = self._target_state.text
        if text.startswith("/image "):
            return self.option_list.option_count > 0
        if self._at_prefix(text) is not None:
            return self.option_list.option_count > 0
        return super().should_show_dropdown(search_string)

    def get_candidates(self, state: TargetState) -> list[DropdownItem]:
        text = state.text
        if text.startswith("/image "):
            return self._image_items(text[7:])
        if text.startswith("/"):
            cmds = getattr(self.app, "SLASH_COMMANDS", [])
            return [
                DropdownItem(main=cmd)
                for cmd, desc in cmds
                if text == "/" or cmd.startswith(text) or text[1:] in cmd
            ]
        at = self._at_prefix(text)
        if at is not None:
            return self._file_items(at)
        if text.startswith("!"):
            cmd_part = text[1:]
            tokens = cmd_part.split()
            if not tokens:
                return []
            cmd0 = tokens[0]
            last = tokens[-1] if not text.endswith(" ") else ""
            if not last or last == cmd0:
                return []
            if not last.startswith("-"):
                if cmd0 == "cd":
                    return self._dir_items(last)
                return self._file_items(last)
        return []

    def _dir_items(self, prefix: str) -> list[DropdownItem]:
        try:
            base = Path(prefix) if prefix else Path(".")
            search_dir = base if base.is_dir() else base.parent
            name_filter = "" if base.is_dir() else base.name.lower()
            entries = sorted(search_dir.iterdir(), key=lambda p: p.name.lower())
            return [
                DropdownItem(main=str(p) + "/", prefix="📁 ")
                for p in entries
                if p.is_dir() and p.name.lower().startswith(name_filter)
            ][:20]
        except Exception:
            return []

    def _file_items(self, prefix: str) -> list[DropdownItem]:
        try:
            base = Path(prefix) if prefix else Path(".")
            search_dir = base if base.is_dir() else base.parent
            name_filter = "" if base.is_dir() else base.name.lower()
            entries = sorted(
                search_dir.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
            return [
                DropdownItem(
                    main=str(p) + ("/" if p.is_dir() else ""),
                    prefix="📁 " if p.is_dir() else "📄 ",
                )
                for p in entries
                if p.name.lower().startswith(name_filter)
            ][:20]
        except Exception:
            return []

    def _image_items(self, prefix: str) -> list[DropdownItem]:
        try:
            base = Path(prefix) if prefix else Path(".")
            search_dir = base if base.is_dir() else base.parent
            name_filter = "" if base.is_dir() else base.name.lower()
            entries = sorted(
                search_dir.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
            return [
                DropdownItem(
                    main=str(p) + ("/" if p.is_dir() else ""),
                    prefix="📁 " if p.is_dir() else "🖼 ",
                )
                for p in entries
                if p.name.lower().startswith(name_filter)
                and (p.is_dir() or p.suffix.lower() in _IMAGE_EXTS)
            ][:20]
        except Exception:
            return []

    def apply_completion(self, value: str, state: TargetState) -> None:
        text = state.text
        target = self.target
        if text.startswith("/image "):
            new_val = "/image " + value
            target.value = new_val
            target.cursor_position = len(new_val)
        elif text.startswith("/"):
            target.value = value
            target.cursor_position = len(value)
        elif self._at_prefix(text) is not None:
            at_idx = text.rfind("@")
            new_val = text[:at_idx + 1] + value
            target.value = new_val
            target.cursor_position = len(new_val)
        elif text.startswith("!"):
            last_space = text.rfind(" ")
            base = text[: last_space + 1] if last_space >= 0 else "!"
            new_val = base + value + ("" if value.endswith("/") else " ")
            target.value = new_val
            target.cursor_position = len(new_val)
        else:
            super().apply_completion(value, state)
        new_state = self._get_target_state()
        self._rebuild_options(new_state, self.get_search_string(new_state))

    def _align_to_target(self) -> None:
        from textual.geometry import Offset

        x, y = self.target.cursor_screen_offset
        _w, h = self.option_list.outer_size
        if h == 0:
            h = min(max(self.option_list.option_count, 1), 12)
        self.absolute_offset = Offset(max(0, x - 1), max(0, y - h))

    def post_completion(self) -> None:
        if self.target.value.endswith("/"):
            return
        self.action_hide()
