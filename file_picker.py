import asyncio
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.screen import Point
from prompt_toolkit.widgets import Frame

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}


def _build_entries(directory: Path) -> list[tuple[str, Path]]:
    """回傳 (顯示名, Path) 清單：先列目錄，再列圖片檔。"""
    entries: list[tuple[str, Path]] = []
    try:
        items = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        return []
    if directory.parent != directory:
        entries.append(("📁 ..", directory.parent))
    for p in items:
        if p.is_dir():
            entries.append((f"📁 {p.name}/", p))
        elif p.suffix.lower() in IMAGE_EXTS:
            entries.append((f"🖼  {p.name}", p))
    return entries


def _run_file_picker(start: Path) -> str | None:
    """單一 Application 圖形檔案瀏覽器，導覽不重啟。"""
    HEADER_LINES = 2
    state = {
        "current": start.resolve(),
        "entries": [],
        "idx": 0,
        "result": "__pending__",
    }

    def cd(directory: Path):
        state["current"] = directory.resolve()
        state["entries"] = _build_entries(state["current"])
        state["idx"] = 0

    cd(start)

    def get_text():
        body = f"<b> 📂 {state['current']}</b>  <ansibrightblack>↑↓ 移動  Enter 選擇/進入  Esc 取消</ansibrightblack>\n\n"
        for i, (label, _) in enumerate(state["entries"]):
            if i == state["idx"]:
                body += f"<reverse> {label} </reverse>\n"
            else:
                body += f"  {label}\n"
        if not state["entries"]:
            body += "  <ansibrightblack>(此目錄無圖片或子目錄)</ansibrightblack>\n"
        return HTML(body)

    def get_cursor_position():
        return Point(x=0, y=HEADER_LINES + state["idx"])

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        if state["entries"]:
            state["idx"] = (state["idx"] - 1) % len(state["entries"])

    @kb.add("down")
    def _down(event):
        if state["entries"]:
            state["idx"] = (state["idx"] + 1) % len(state["entries"])

    @kb.add("enter")
    def _enter(event):
        if not state["entries"]:
            return
        _, path = state["entries"][state["idx"]]
        if path.is_dir():
            cd(path)
        else:
            state["result"] = str(path)
            event.app.exit()

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event):
        state["result"] = None
        event.app.exit()

    layout = Layout(
        HSplit([
            Frame(
                Window(
                    content=FormattedTextControl(
                        get_text,
                        focusable=True,
                        get_cursor_position=get_cursor_position,
                    ),
                    height=15,
                )
            )
        ])
    )
    app = Application(layout=layout, key_bindings=kb, full_screen=False, mouse_support=False)
    app.run()
    r = state["result"]
    return r if r != "__pending__" else None


async def pick_image(start: Path | None = None) -> str | None:
    """非同步包裝，從主執行緒安全地呼叫同步 TUI。"""
    return await asyncio.get_running_loop().run_in_executor(
        None, _run_file_picker, start or Path.cwd()
    )
