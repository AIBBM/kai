# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the Textual TUI (main entry point)
uv run python app.py

# Run the legacy CLI agent
uv run python main.py

# Test web search directly
uv run python core/tools.py "搜尋關鍵字" 3

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
app.py              → Textual TUI (primary); chat UI, file tree, tabs, sidebar
main.py             → legacy Rich/prompt_toolkit CLI (still functional)
core/
  agent.py          → Agent class: history, streaming, tool-call loop
  tools.py          → web_search (SearXNG), run_tool dispatcher
  models.py         → ModelProfile dataclass + REGISTRY
  config.py         → all env-var constants (loaded once at import)
  mcp_client.py     → MCP server lifecycle + tool dispatch
  file_picker.py    → prompt_toolkit file browser (used by main.py /image)
widgets/
  input.py          → HistoryInput, ChatAutoComplete
  tree.py           → RightClickTree, TreeContextMenu, FileTree
  sidebar.py        → ResizeHandle, Sidebar
  chat.py           → ImageBubble, FileViewer, CopyButton, CapabilityBadge, CapLabel, AIMessage
  commands.py       → ChatCommands (command palette provider)
  utils.py          → shared TUI utilities: _copy_to_clipboard, file icons, _line_height, _wrap_box_drawing
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
| `HistoryInput` | widgets/input.py | single-line input with Up/Down history, Ctrl+Z undo, multiline paste intercept |
| `ChatAutoComplete` | widgets/input.py | slash command / path autocomplete |
| `FileTree` | widgets/tree.py | lazy-loading dir tree; double-click opens file tab; Ctrl+click / Space multi-select for batch delete; 2s deep fs watcher |
| `RightClickTree` | widgets/tree.py | Tree subclass; intercepts right-click + Ctrl+click; Space/Ctrl+A/Escape key bindings |
| `TreeContextMenu` | widgets/tree.py | ModalScreen right-click menu; shows "刪除選取 (N 項)" when multi-selected |
| `FileViewer` | widgets/chat.py | TextArea with syntax highlighting + WSL-aware Ctrl+C multi-line copy; mtime polling for external change detection |
| `AIMessage` | widgets/chat.py | AI response widget; Static stream-view during streaming, Markdown md-view on final |
| `Sidebar` | widgets/sidebar.py | cwd / model / context files / git status / MCP tools |
| `ResizeHandle` | widgets/sidebar.py | drag-to-resize handle between file tree and center |
| `ChatCommands` | widgets/commands.py | Ctrl+P command palette: clear, switch model |

### Slash commands

| Command | Action |
|---------|--------|
| `/clear` | Archive history to `.history/`, clear UI (guard: skips if only system msg) |
| `/resume [N]` | List or restore archived sessions from `.history/` |
| `/model <name>` | Switch model (hot-swap, preserves history) |
| `/add <path>` | Add file to context |
| `/del <path>` | Remove file from context |
| `/cd <path>` | Change directory (saves old history, loads new) |
| `/think` | Toggle thinking mode |
| `/debug` | Toggle tool debug cards |
| `/image <path> [q]` | Send image to vision model |
| `/ask <question>` | Ephemeral question — not saved to history |
| `/save` | Promote last `/ask` answer into permanent history |
| `/remember <note>` | Write note to `AGENT_NOTES.md` (persistent across sessions) |

### `!` bash commands

Any input starting with `!` runs as a shell command via `/bin/bash -c` (supports brace expansion, `$((expr))`, `[[` etc.).
Special case:
- `!cd <path>` — intercepted, calls `os.chdir` (subprocess `cd` has no effect on parent process)
- After every bash command: file tree and git sidebar auto-refresh

### File tree multi-select

| Operation | Effect |
|-----------|--------|
| `Ctrl+click` | Toggle item in multi-select (shows green ✓) |
| `Space` | Toggle cursor item; if anchor set, range-select from anchor to cursor |
| `Ctrl+A` | Select all / deselect all visible nodes |
| `Escape` | Clear multi-select (if none, focus prompt) |
| Right-click on selected item | Shows "刪除選取 (N 項)" for batch delete |

Multi-select state is cleared after delete and after `refresh_tree`.

### File tree filesystem watcher

`FileTree._check_external_change` runs every 2 seconds in a background thread (via `run_worker(thread=True)`). It does a deep `os.walk` (skipping `_HIDDEN_DIRS`) comparing all file/dir mtimes. Any external change (VSCode delete, `touch`, etc.) triggers `refresh_tree`. The scan is off the event loop to prevent render glitches.

### FileViewer external change detection

`FileViewer._check_external_change` polls the file's `st_mtime` every 1 second. If changed externally (and not modified locally), it reloads content and shows a toast. `action_save` updates `_mtime` after write to suppress false triggers.

### Streaming rendering

During streaming (`update_content(final=False)`): text is written to a `Static(markup=False)` widget using `RichText(overflow="fold")` — no markdown parsing, O(1) cost regardless of length.

On completion (`update_content(final=True)`): `stream-view` is hidden, `md-view` (Markdown widget) is shown and rendered once. This prevents the slowdown where long responses got progressively slower to render.

### Markdown rendering fixes

- `MarkdownTableCellContents.with_tooltip` is patched to no-op in `ChatApp.__init__` to prevent Tooltip widgets accumulating on Screen's `_tooltips` layer.
- `_wrap_box_drawing()` in `widgets/utils.py` auto-detects lines with ≥3 box-drawing characters (U+2500–U+257F range) and wraps them in a code fence before Markdown render, preventing word-wrap from destroying ASCII diagrams.

### Directory switching (`_do_cd`)

When changing directory (via `/cd` or `!cd`):
1. Save current `history.json` to old cwd
2. `os.chdir(target)`
3. `agent.reset_history()` — clears and reloads from new cwd's `history.json`
4. Clears chat UI and context files
5. Refreshes file tree and sidebar

### Session history (`/cls` and `/resume`)

`/clear` archives `history.json` to `.history/YYYYMMDD_HHMMSS_N-turns.json` before clearing (guard: skips if history has only system message, or if just resumed with no new messages via `_resumed_from` flag).

`/resume` lists archives with timestamp, turn count, and first user message preview. `/resume N` restores session N by copying the archive back to `history.json` and reloading.

### Multiline paste (`HistoryInput`)

When pasting multiline text into the prompt:
- Intercepts in `on_paste`; shows compact `[N 行] preview…` marker in input
- If pasted from `FileViewer` copy, auto-wraps with ` ```lang filename:lines` context
- On submit, replaces marker with full content while preserving any surrounding typed text

### Clipboard (WSL)

`_copy_to_clipboard()` in `widgets/utils.py` tries `xclip` first, then `clip.exe` with UTF-16-LE + BOM (`\xff\xfe`). BOM is required for `clip.exe` to treat input as Unicode; without it CJK characters become mojibake. All `subprocess.run` clipboard calls have `timeout=3` to prevent blocking app shutdown.

### Key bindings

| Key | Action |
|-----|--------|
| `Ctrl+Q` | Quit |
| `Ctrl+P` | Command palette |
| `Ctrl+T` | Toggle file tree |
| `Ctrl+B` | Toggle sidebar |
| `Ctrl+W` | Close active tab (priority binding) |
| `Escape` | Cancel streaming response / clear tree multi-select |
| `↑ / ↓` | Navigate input history |
| `Ctrl+Z` | Undo in input |
| `Space` (in tree) | Toggle current node in multi-select / range-select from anchor |
| `Ctrl+A` (in tree) | Select all / deselect all |

## Agent (`core/agent.py`)

### Tool calling loop (`Agent.chat`)

1. Stream Ollama/Gemini response; collect `msg.thinking`, `msg.content`, `msg.tool_calls`
2. If `tool_calls` present → `asyncio.gather` all tools concurrently → append `tool` messages → repeat
3. Loop exits when model returns no tool calls or `MAX_TOOL_CALLS` is reached
4. `save_history()` is called after every turn
5. `reset_history()` clears and reloads `history.json` from current cwd (used on directory change)

### History

`HISTORY_FILE = Path("history.json")` — relative to cwd at runtime. Each project directory has its own history. Archives go to `.history/` (gitignored).

### Adding a new model

Add one entry to `REGISTRY` in `core/models.py`:

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

1. Define the Ollama tool schema (like `SEARCH_TOOL` in `core/tools.py`)
2. Implement `async def my_tool(...) -> str`
3. Register in `Agent.tools_map` in `core/agent.py`
4. Pass the schema in `active_tools` list inside `chat()`

## Environment variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL` | `qwen3-vl:4b` | Active model |
| `DEBUG_TOOLS` | `0` | Set `1` to enable tool debug cards on startup |
| `NUM_CTX` | `0` | Context window override (0 = model default) |
| `MAX_TOOL_CALLS` | `15` | Max tool calls per turn |
| `TEMPERATURE` | `0.6` | Generation temperature |
| `USERNAME` | `你` | Display name in chat bubbles and prompt title |
| `BASH_TIMEOUT` | `30` | `bash_exec` default timeout (seconds) |
| `SEARXNG_URL` | `http://localhost:8888/search` | SearXNG API endpoint |

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
