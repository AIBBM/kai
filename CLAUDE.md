# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the Textual TUI (main entry point)
uv run python app.py

# Run the legacy CLI agent
uv run python main.py

# Test web search directly
uv run python tools.py "搜尋關鍵字" 3

# Lint
uv run ruff check .
uv run ruff format .

# Start SearXNG + Valkey
bash searxng/start.sh
```

No test suite exists. Functional testing is done by running the app directly.

## Architecture

Two entry points sharing the same core:

```
app.py           → Textual TUI (primary); chat UI, file tree, tabs, sidebar
main.py          → legacy Rich/prompt_toolkit CLI (still functional)
agent.py         → Agent class: history, streaming, tool-call loop
tools.py         → web_search (SearXNG), run_tool dispatcher
models.py        → ModelProfile dataclass + REGISTRY
config.py        → all env-var constants (loaded once at import)
mcp_client.py    → MCP server lifecycle + tool dispatch
file_picker.py   → prompt_toolkit file browser (used by main.py /image)
```

## TUI (`app.py`)

### Layout

```
┌─ FileTree ─┬─── TabbedContent ────────────────┬─ Sidebar ─┐
│ RightClick │  Chat tab  │  file tabs (×)       │ cwd       │
│ Tree       │  messages  │  FileViewer          │ model     │
│            │            │  (syntax highlight)  │ context   │
│            ├────────────┴──────────────────────┤ git       │
│            │  cap-bar (Think / MCP badges)     │ MCP tools │
│            │  #prompt (HistoryInput)           │           │
└────────────┴───────────────────────────────────┴───────────┘
```

### Key widgets

| Widget | File | Purpose |
|--------|------|---------|
| `ChatApp` | app.py | root App, all event handlers |
| `HistoryInput` | app.py | single-line input with Up/Down history, Ctrl+Z undo, multiline paste intercept |
| `FileTree` | app.py | lazy-loading dir tree; double-click opens file tab |
| `RightClickTree` | app.py | Tree subclass; intercepts right-click before `_on_mouse_down` stops propagation |
| `TreeContextMenu` | app.py | ModalScreen right-click menu; handles cross-menu right-click via `on_mouse_down` |
| `FileViewer` | app.py | TextArea with syntax highlighting + WSL-aware Ctrl+C multi-line copy |
| `AIMessage` | app.py | AI response widget; markdown view + text-area select mode |
| `Sidebar` | app.py | cwd / model / context files / git status / MCP tools |

### Slash commands

| Command | Action |
|---------|--------|
| `/clear` | Clear chat history and UI |
| `/model <name>` | Switch model (hot-swap, preserves history) |
| `/add <path>` | Add file to context |
| `/del <path>` | Remove file from context |
| `/cd <path>` | Change directory (saves old history, loads new) |
| `/think` | Toggle thinking mode |
| `/debug` | Toggle tool debug cards |
| `/image <path> [q]` | Send image to vision model |

### `!` bash commands

Any input starting with `!` runs as a shell command. Special case:
- `!cd <path>` — intercepted, calls `os.chdir` (subprocess `cd` has no effect on parent process)
- After every bash command: file tree and git sidebar auto-refresh

### Directory switching (`_do_cd`)

When changing directory (via `/cd` or `!cd`):
1. Save current `history.json` to old cwd
2. `os.chdir(target)`
3. `agent.reset_history()` — clears and reloads from new cwd's `history.json`
4. Clears chat UI and context files
5. Refreshes file tree and sidebar

### Multiline paste (`HistoryInput`)

When pasting multiline text into the prompt:
- Intercepts in `on_paste`; shows compact `[N 行] preview…` marker in input
- If pasted from `FileViewer` copy, auto-wraps with ` ```lang filename:lines` context
- On submit, replaces marker with full content while preserving any surrounding typed text

### Clipboard (WSL)

`_copy_to_clipboard()` tries `xclip` first, then `clip.exe` with UTF-16-LE + BOM (`\xff\xfe`). BOM is required for `clip.exe` to treat input as Unicode; without it CJK characters become mojibake. All `subprocess.run` clipboard calls have `timeout=3` to prevent blocking app shutdown.

### Key bindings

| Key | Action |
|-----|--------|
| `Ctrl+Q` | Quit |
| `Ctrl+P` | Command palette |
| `Ctrl+T` | Toggle file tree |
| `Ctrl+B` | Toggle sidebar |
| `Ctrl+W` | Close active tab (priority binding) |
| `Escape` | Cancel streaming response |
| `↑ / ↓` | Navigate input history |
| `Ctrl+Z` | Undo in input |

## Agent (`agent.py`)

### Tool calling loop (`Agent.chat`)

1. Stream Ollama/Gemini response; collect `msg.thinking`, `msg.content`, `msg.tool_calls`
2. If `tool_calls` present → `asyncio.gather` all tools concurrently → append `tool` messages → repeat
3. Loop exits when model returns no tool calls or `MAX_TOOL_CALLS` is reached
4. `save_history()` is called after every turn
5. `reset_history()` clears and reloads `history.json` from current cwd (used on directory change)

### History

`HISTORY_FILE = Path("history.json")` — relative to cwd at runtime. Each project directory has its own history.

### Adding a new model

Add one entry to `REGISTRY` in `models.py`:

| 模型 | thinking | vision | tools | backend |
|------|----------|--------|-------|---------|
| `qwen3-vl:4b` | ✓ | ✓ | ✓ | ollama |
| `qwen3:4b/8b` | ✓ | - | ✓ | ollama |
| `qwen3.5:2b` | - | ✓ | ✓ | ollama |
| `qwen3.5:4b` | ✓ | ✓ | ✓ | ollama |
| `gemma3:4b` | - | ✓ | ✓ | ollama |
| `gemma4:e2b/e4b` | ✓ | ✓ | ✓ | ollama |
| `deepseek-r1:1.5b` | ✓ | - | ✗ | ollama |
| `MFDoom/deepseek-r1-tool-calling:1.5b` | - | - | ✓ | ollama |
| `qwen2.5-coder:7b` | - | - | ✗ | ollama |
| `gemini-2.0-flash` | - | ✓ | ✓ | gemini |
| `gemini-2.5-flash` | ✓ | ✓ | ✓ | gemini |
| `gemini-2.5-pro` | ✓ | ✓ | ✓ | gemini |

`get_profile()` returns a `dataclasses.replace()` copy to avoid mutating REGISTRY. `NUM_CTX` in `.env` overrides all profiles at runtime.

### Adding a new tool

1. Define the Ollama tool schema (like `SEARCH_TOOL` in `tools.py`)
2. Implement `async def my_tool(...) -> str`
3. Register in `Agent.tools_map` in `agent.py`
4. Pass the schema in `active_tools` list inside `chat()`

## SearXNG

Runs in Docker on port 8888 with a Valkey sidecar for rate-limiting. Config is `searxng/settings.yml` (mounted read-only). `formats: [html, json]` must remain enabled for the JSON API used by `web_search`. `limiter: false` is intentional — this is a local private instance.

## Key dependencies

- `textual` — TUI framework (with `[syntax]` extra for tree-sitter highlighting)
- `textual-autocomplete` — slash command / path autocomplete in prompt
- `ollama` — native Python client; used directly to access `msg.thinking` and `tool_calls`
- `rich` — rendering in legacy CLI and agent internals
- `prompt_toolkit` — legacy CLI prompt + file picker
- `tenacity` — retry logic in `web_search`
- `loguru` — structured logging to `agent_tui.log` (stderr in legacy CLI)
