# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the agent
uv run python main.py

# Test web search directly
uv run python tools.py "搜尋關鍵字" 3

# Lint
uv run ruff check .
uv run ruff format .

# Start SearXNG + Valkey
bash searxng/start.sh
```

No test suite exists. Functional testing is done by running the agent directly.

## Architecture

Single-process async CLI agent. Entry point is `main.py`; the conversation loop calls `agent.py` which drives Ollama via streaming and handles tool calling.

```
main.py          → prompt loop, /image routing, Esc key watcher (_watch_esc)
file_picker.py   → prompt_toolkit TUI for interactive image selection
agent.py         → Agent class: history, tool-call loop, Rich rendering
tools.py         → web_search (SearXNG), run_tool dispatcher
models.py        → ModelProfile dataclass + REGISTRY (one entry per Ollama model)
config.py        → all env-var constants (loaded once at import)
```

### Tool calling loop (`agent.py: Agent.chat`)

1. Stream Ollama response; collect `msg.thinking`, `msg.content`, `msg.tool_calls`
2. If `tool_calls` present → `asyncio.gather` all tools concurrently → append `tool` messages → repeat
3. Loop exits when model returns no tool calls or `MAX_TOOL_CALLS` is reached
4. `save_history()` is called after every turn (not just on exit)

### Esc key cancellation (`main.py: _watch_esc`)

During `agent.chat()`, a concurrent `asyncio.to_thread` task watches stdin for Esc (`\x1b`). When detected it sets `agent._cancelled = True`; the stream loop in `agent.py` checks this flag on every chunk and breaks early.

- Uses `tty.setcbreak()` (not `setraw()`) — disables canonical mode but keeps `OPOST` so Rich output `\n`→`\r\n` translation stays intact.
- Started only after `prompt_async()` returns (prompt_toolkit has fully released the terminal).
- `done` event + `finally: tcsetattr(...)` guarantees terminal is restored even if cancelled.

### Adding a new model

Add one entry to `REGISTRY` in `models.py`. Current models: `qwen3:4b`, `qwen3:8b`, `qwen3-vl:4b`, `qwen3.5:4b`, `gemma3:4b`, `gemma4:e2b/e4b`, `deepseek-r1:1.5b`, `llama3.2:3b`. The profile controls `supports_thinking`, `supports_vision`, `supports_tools`, and `num_ctx`. `NUM_CTX` in `.env` overrides all profiles at runtime.

### Adding a new tool

1. Define the Ollama tool schema (like `SEARCH_TOOL` in `tools.py`)
2. Implement an `async def my_tool(...) -> str` function
3. Register in `Agent.tools_map` in `agent.py`
4. Pass the schema in `active_tools` list inside `chat()`

### SearXNG

Runs in Docker on port 8888 with a Valkey sidecar for rate-limiting. Config is `searxng/settings.yml` (mounted read-only). `formats: [html, json]` must remain enabled for the JSON API used by `web_search`. `limiter: false` is intentional — this is a local private instance.

## Key dependencies

- `ollama` — native Python client; used directly (not via LangChain) to access `msg.thinking` and `tool_calls`
- `rich` — all terminal rendering; AI responses use bare `Markdown` (no Panel) to avoid border characters in copy-paste
- `prompt_toolkit` — async prompt with file history; also powers the `/image` file browser TUI
- `tenacity` — retry logic in `web_search`
- `loguru` — structured logging to stderr only, leaving stdout clean for Rich
