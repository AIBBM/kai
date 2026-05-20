"""File tree widgets: RightClickTree, TreeContextMenu, FileTree."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Label, OptionList, Tree
from textual.widgets._tree import TreeNode

from .utils import _HIDDEN_DIRS, _copy_to_clipboard, _file_icon


class ConfirmDeleteScreen(ModalScreen[bool]):
    """刪除確認對話框。"""

    DEFAULT_CSS = """
    ConfirmDeleteScreen {
        align: center middle;
    }
    ConfirmDeleteScreen Vertical {
        width: 50;
        height: auto;
        padding: 1 2;
        border: round $error;
        background: $surface;
    }
    ConfirmDeleteScreen Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }
    ConfirmDeleteScreen Horizontal {
        width: 100%;
        height: auto;
        align: center middle;
    }
    ConfirmDeleteScreen Button {
        margin: 0 1;
    }
    """

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def compose(self) -> ComposeResult:
        kind = "資料夾" if self._path.is_dir() else "檔案"
        with Vertical():
            yield Label(f"刪除{kind}？")
            yield Label(self._path.name, markup=False)
            with Horizontal():
                yield Button("刪除", id="confirm", variant="error")
                yield Button("取消", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel").focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


def _relative_display(path: Path) -> str:
    """回傳相對於 cwd 的路徑字串；資料夾加尾斜線。"""
    try:
        rel = path.relative_to(Path(os.getcwd()))
    except ValueError:
        rel = path
    return str(rel) + ("/" if path.is_dir() else "")


def _copy_filename_bg(app, path: Path) -> None:
    """背景複製路徑，失敗才補 error toast。"""
    ok = _copy_to_clipboard(_relative_display(path))
    if not ok:
        app.call_from_thread(app.notify, "複製失敗", severity="warning", timeout=1.5)


class RightClickTree(Tree):
    """Tree subclass that intercepts right-click before internal _on_mouse_down stops it."""

    class NodeRightClicked(Message):
        def __init__(self, path: Path, sx: int, sy: int) -> None:
            super().__init__()
            self.path = path
            self.sx = sx
            self.sy = sy

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 3:
            return
        line = int(event.y) + int(self.scroll_y)
        node = self.get_node_at_line(line)
        path = node.data if node else None
        if isinstance(path, Path):
            self.move_cursor(node)
            event.stop()
            self.post_message(
                self.NodeRightClicked(path, event.screen_x, event.screen_y)
            )


class TreeContextMenu(ModalScreen):
    """檔案樹右鍵選單。"""

    DEFAULT_CSS = """
    TreeContextMenu {
        background: transparent;
        align: left top;
    }
    TreeContextMenu OptionList {
        width: 22;
        height: auto;
        max-height: 12;
        border: round $primary;
        background: $surface;
    }
    """

    _OPT_COPY = 0
    _OPT_DELETE = 1

    def __init__(self, path: Path | None, sx: int, sy: int) -> None:
        super().__init__()
        self._path = path
        self._sx = sx
        self._sy = sy

    def compose(self) -> ComposeResult:
        yield OptionList("複製檔名", "刪除", id="ctx-options")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        x = min(self._sx + 6, self.app.size.width - 24)
        y = min(self._sy, self.app.size.height - 8)
        ol.styles.offset = (x, y)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not self._path:
            self.dismiss()
            return
        if event.option_index == self._OPT_COPY:
            path = self._path
            app = self.app
            app.notify(f"已複製：{_relative_display(path)}", timeout=1.5)
            self.dismiss()
            app.run_worker(lambda: _copy_filename_bg(app, path), thread=True)
        elif event.option_index == self._OPT_DELETE:
            path = self._path
            app = self.app
            self.dismiss()

            async def _do_delete() -> None:
                confirmed = await app.push_screen(
                    ConfirmDeleteScreen(path), wait_for_dismiss=True
                )
                if not confirmed:
                    return
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                except Exception as e:
                    app.notify(f"刪除失敗：{e}", severity="error")
                    return
                try:
                    app.query_one(FileTree).refresh_tree()
                except Exception:
                    pass
                app.notify(f"已刪除：{path.name}", timeout=2)

            app.call_after_refresh(lambda: app.run_worker(_do_delete()))
        else:
            self.dismiss()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 3:
            return
        widget, _ = self.screen.get_widget_at(event.screen_x, event.screen_y)
        ol = self.query_one(OptionList)
        if ol in widget.ancestors_with_self:
            return
        new_path = self._node_at_screen(event.screen_x, event.screen_y)
        self.dismiss()
        if new_path:
            self.app.push_screen(
                TreeContextMenu(new_path, event.screen_x, event.screen_y)
            )

    def _node_at_screen(self, sx: int, sy: int) -> Path | None:
        try:
            tree = self.app.query_one(RightClickTree)
            r = tree.region
            if not r.contains(sx, sy):
                return None
            line = int(sy - r.y) + int(tree.scroll_y)
            node = tree.get_node_at_line(line)
            path = node.data if node else None
            if isinstance(path, Path):
                tree.move_cursor(node)
                return path
        except Exception:
            pass
        return None

    def on_click(self, event: events.Click) -> None:
        widget, _ = self.screen.get_widget_at(event.screen_x, event.screen_y)
        ol = self.query_one(OptionList)
        if ol not in widget.ancestors_with_self:
            self.dismiss()


class FileTree(Widget):
    """左側懶載入檔案樹；選取檔案後填入 prompt。"""

    class FileChosen(Message):
        def __init__(self, path: Path) -> None:
            super().__init__()
            self.path = path

    def compose(self) -> ComposeResult:
        cwd = Path(os.getcwd())
        tree: RightClickTree[Path] = RightClickTree(cwd.name)
        tree.root.data = cwd
        tree.root.expand()
        yield tree

    def on_mount(self) -> None:
        self._populate(self.query_one(RightClickTree).root)
        self._last_selected: Path | None = None
        self._last_select_time: float = 0.0

    def _populate(self, node: TreeNode[Path]) -> None:
        path: Path | None = node.data
        if not path or not path.is_dir():
            return
        node.remove_children()
        try:
            entries = sorted(
                path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
        except PermissionError:
            node.add_leaf("🔒 無權限", data=None)
            return
        for entry in entries:
            if entry.name in _HIDDEN_DIRS:
                continue
            if entry.is_dir():
                child = node.add(f"📁 {entry.name}", data=entry)
                child.add_leaf("…", data=None)
            else:
                node.add_leaf(f"{_file_icon(entry)}{entry.name}", data=entry)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        if node.data and isinstance(node.data, Path) and node.data.is_dir():
            children = list(node.children)
            if len(children) == 1 and children[0].data is None:
                self._populate(node)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        path: Path | None = event.node.data
        if not path or not path.is_file():
            return
        now = time.monotonic()
        if path == self._last_selected and now - self._last_select_time < 0.4:
            self.post_message(FileTree.FileChosen(path))
            self._last_selected = None
        else:
            self._last_selected = path
            self._last_select_time = now

    def on_right_click_tree_node_right_clicked(
        self, event: RightClickTree.NodeRightClicked
    ) -> None:
        self.app.push_screen(TreeContextMenu(event.path, event.sx, event.sy))

    def refresh_tree(self) -> None:
        tree = self.query_one(RightClickTree)
        cwd = Path(os.getcwd())
        tree.root.label = cwd.name
        tree.root.data = cwd
        self._populate(tree.root)
        tree.root.expand()
