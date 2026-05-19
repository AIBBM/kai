"""Shared constants and utility functions for the TUI."""
from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

_IMAGE_EXTS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".svg"}
)
_HIDDEN_DIRS = frozenset(
    {"__pycache__", ".git", ".venv", "node_modules", ".mypy_cache", ".ruff_cache"}
)

_EXT_ICONS: dict[str, str] = {
    # Python
    ".py": "🐍",
    ".pyi": "🐍",
    ".ipynb": "📓",
    # Web
    ".html": "🌐",
    ".htm": "🌐",
    ".css": "🎨",
    ".scss": "🎨",
    ".sass": "🎨",
    ".js": "📜",
    ".mjs": "📜",
    ".cjs": "📜",
    ".ts": "📘",
    ".tsx": "📘",
    ".jsx": "📜",
    # Data / config
    ".json": "📋",
    ".jsonc": "📋",
    ".json5": "📋",
    ".yaml": "⚙️ ",
    ".yml": "⚙️ ",
    ".toml": "⚙️ ",
    ".ini": "⚙️ ",
    ".cfg": "⚙️ ",
    ".conf": "⚙️ ",
    ".env": "🔑",
    ".env.local": "🔑",
    ".env.example": "🔑",
    # Docs
    ".md": "📝",
    ".mdx": "📝",
    ".rst": "📝",
    ".txt": "📝",
    # Shell
    ".sh": "⚡",
    ".bash": "⚡",
    ".zsh": "⚡",
    ".fish": "⚡",
    # Images
    ".png": "🖼 ",
    ".jpg": "🖼 ",
    ".jpeg": "🖼 ",
    ".gif": "🖼 ",
    ".webp": "🖼 ",
    ".bmp": "🖼 ",
    ".tiff": "🖼 ",
    ".tif": "🖼 ",
    ".svg": "🖼 ",
    ".ico": "🖼 ",
    # Compiled / binary
    ".pyc": "⚙️ ",
    ".so": "⚙️ ",
    ".dll": "⚙️ ",
    ".exe": "⚙️ ",
    # Archives
    ".zip": "📦",
    ".tar": "📦",
    ".gz": "📦",
    ".bz2": "📦",
    ".xz": "📦",
    ".7z": "📦",
    # Database
    ".db": "🗄️ ",
    ".sqlite": "🗄️ ",
    ".sqlite3": "🗄️ ",
    # Lock / deps
    ".lock": "🔒",
    # Docker / CI
    "dockerfile": "🐳",
    ".dockerignore": "🐳",
    # Git
    ".gitignore": "🚫",
    ".gitattributes": "🚫",
    # Languages
    ".rs": "🦀",
    ".go": "🐹",
    ".java": "☕",
    ".kt": "☕",
    ".c": "🔧",
    ".cpp": "🔧",
    ".cc": "🔧",
    ".h": "🔧",
    ".hpp": "🔧",
    ".rb": "💎",
    ".php": "🐘",
    ".swift": "🍎",
    ".cs": "🔷",
    ".lua": "🌙",
    ".r": "📊",
    ".jl": "📊",
}


def _file_icon(path: Path) -> str:
    suffix = path.suffix.lower()
    name_lower = path.name.lower()
    return _EXT_ICONS.get(name_lower) or _EXT_ICONS.get(suffix, "📄") + " "


_SUFFIX_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".json": "json",
    ".md": "markdown",
    ".css": "css",
    ".html": "html",
    ".sh": "bash",
    ".bash": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".cpp": "cpp",
    ".java": "java",
    ".sql": "sql",
    ".xml": "xml",
}


def _line_height(text: str, cols: int = 100) -> int:
    total = 0
    for line in text.split("\n"):
        total += max(1, (len(line) + cols - 1) // cols)
    return total


def _open_image(path: str) -> None:
    """用系統預覽器開啟圖片（WSL → cmd.exe start，Linux → xdg-open，macOS → open）。"""
    import shutil

    try:
        if shutil.which("wslpath"):
            r = subprocess.run(["wslpath", "-w", path], capture_output=True, text=True)
            win_path = r.stdout.strip() if r.returncode == 0 else path
            subprocess.run(
                ["cmd.exe", "/c", "start", "", win_path],
                check=False,
                capture_output=True,
            )
        elif shutil.which("xdg-open"):
            subprocess.run(["xdg-open", path], check=False, capture_output=True)
        elif shutil.which("open"):
            subprocess.run(["open", path], check=False, capture_output=True)
    except Exception as e:
        logger.warning(f"開啟圖片失敗：{e}")


def _copy_to_clipboard(text: str) -> bool:
    """複製文字到剪貼簿。依序嘗試 xclip → clip.exe（WSL）。"""
    try:
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text.encode(),
            check=True,
            capture_output=True,
            timeout=3,
        )
        return True
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        pass
    try:
        # clip.exe 需要 UTF-16-LE + BOM（\xff\xfe）才會以 Unicode 模式讀取。
        win_text = text.replace("\n", "\r\n")
        bom = b"\xff\xfe"
        subprocess.run(
            ["clip.exe"],
            input=bom + win_text.encode("utf-16-le"),
            check=True,
            capture_output=True,
            timeout=3,
        )
        return True
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        pass
    return False
