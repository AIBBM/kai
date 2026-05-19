"""Shared constants and utility functions for the TUI."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from loguru import logger

# LaTeX inline math → Unicode 對照表
_LATEX_MAP: dict[str, str] = {
    # 箭頭
    "rightarrow": "→", "leftarrow": "←", "to": "→",
    "Rightarrow": "⇒", "Leftarrow": "⇐",
    "leftrightarrow": "↔", "Leftrightarrow": "⇔",
    "uparrow": "↑", "downarrow": "↓",
    "nearrow": "↗", "searrow": "↘", "nwarrow": "↖", "swarrow": "↙",
    "implies": "⟹", "iff": "⟺",
    # 比較 / 運算
    "leq": "≤", "geq": "≥", "neq": "≠", "approx": "≈",
    "equiv": "≡", "sim": "∼", "simeq": "≃", "cong": "≅",
    "ll": "≪", "gg": "≫", "propto": "∝",
    "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "cdot": "·", "circ": "∘", "oplus": "⊕", "otimes": "⊗",
    # 集合 / 邏輯
    "in": "∈", "notin": "∉", "ni": "∋",
    "subset": "⊂", "subseteq": "⊆", "supset": "⊃", "supseteq": "⊇",
    "cup": "∪", "cap": "∩", "setminus": "∖", "emptyset": "∅",
    "forall": "∀", "exists": "∃", "nexists": "∄",
    "neg": "¬", "land": "∧", "lor": "∨",
    # 數學符號
    "infty": "∞", "partial": "∂", "nabla": "∇",
    "sum": "∑", "prod": "∏", "int": "∫", "oint": "∮",
    "sqrt": "√", "ldots": "…", "cdots": "⋯", "vdots": "⋮", "ddots": "⋱",
    "therefore": "∴", "because": "∵",
    # 希臘小寫
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "sigma": "σ",
    "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    # 希臘大寫
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ",
    "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}

# 比對 $...$ 但跳過 code fence / inline code
_CODE_FENCE = re.compile(r"```[\s\S]*?```|`[^`]+`")
_INLINE_MATH = re.compile(r"\$\\?([A-Za-z]+)\$")


def _latex_to_unicode(text: str) -> str:
    r"""把 AI 輸出裡的 LaTeX inline math（$\cmd$）轉成 Unicode，跳過 code block。"""
    # 先把 code block/span 位置記下來，避免在 code 裡替換
    protected: list[tuple[int, int]] = []
    for m in _CODE_FENCE.finditer(text):
        protected.append((m.start(), m.end()))

    def _in_code(pos: int) -> bool:
        return any(s <= pos < e for s, e in protected)

    def _replace(m: re.Match) -> str:
        if _in_code(m.start()):
            return m.group(0)
        cmd = m.group(1)
        return _LATEX_MAP.get(cmd, m.group(0))

    return _INLINE_MATH.sub(_replace, text)

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
