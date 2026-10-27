# Kai — 終端機裡的 AI 指揮塔

用自然語言操作你的系統、一句話生出專業 AI 分身、串接 MCP 生態，全程不離開 terminal。

<!-- TODO(1-8)：demo GIF -->

```
┌─ FileTree ─┬──── Chat / Agent Tabs ────┬─ Sidebar ─┐
│            │  messages                 │ model     │
│            │  streaming AI replies     │ context   │
│            ├───────────────────────────┤ git       │
│            │  [Think] [MCP]            │ MCP tools │
│            │  > prompt input           │           │
└────────────┴───────────────────────────┴───────────┘
```

## ✨ 為什麼是 Kai

### 🏭 Agent Factory：一句話生出專業 AI 分身

```
/create-agent 台股技術分析師，擅長 K 線、均線、籌碼分析
```

由 LLM 設計整份 agent 設定（system prompt、模型、暱稱、語音），存成 `agents/<name>/config.json`。每個分身開在自己的 tab，有獨立的對話歷史、記憶和 MCP 工具，分身之間還能互相傳訊息分工。

### 🗣️ 用說的就能操作 Kai 本身

19 個 app tools 讓 AI 直接執行原本要手打的指令：「打開台股分析師」「切到 gemini-2.5-flash」「把 context7 MCP 加進來」，AI 會自己呼叫對應功能。

### 🔗 MCP 原生整合：不只是聊天，是真的能做事

支援 stdio 與 HTTP transport，設定分全域、專案、單一 agent 三層，執行中可以熱重載與增刪。Kai 會偵測掛上了哪些 MCP 工具（filesystem、browser、[RepoMapper](https://github.com/pdavis68/RepoMapper) 等），自動調整 system prompt 裡的使用規則：例如掛上 RepoMapper 後，AI 會先用 repo map 理解專案結構，而不是逐檔 `cat`。

### 🧩 不被單一供應商綁架

本地 Ollama（免費、資料不出機器）與雲端 Gemini、DeepSeek 隨時切換，對話歷史保留。特別針對 2–4B 的本地小模型調整過工具呼叫流程。

### 🖥️ 為活在 terminal 裡的人打造

Textual 打造的完整 TUI，不是網頁套殼：檔案樹、語法高亮檢視器、多 tab、命令面板、輸入歷史、`!` 直接執行 shell。另外支援 5 種 TTS 引擎，讓 AI 分身開口說話。

## 🚀 快速開始

需求：Python 3.13+、[uv](https://docs.astral.sh/uv/)；本地模型需要 [Ollama](https://ollama.com/)，網路搜尋需要 Docker。

```bash
git clone <repo-url> kai && cd kai
uv sync                     # 需要 kokoro / melotts TTS 時：uv sync --extra tts
cp .env.example .env        # 填入 API keys 與 SEARXNG_SECRET（openssl rand -hex 32）
docker compose up -d        # 啟動 SearXNG（web_search 用，可略過）
uv run python app.py                 # 在目前目錄啟動
uv run python app.py ~/my-project    # 指定工作目錄
```

只用雲端模型的話不需要 Ollama：在 `.env` 設 `MODEL=gemini-2.5-flash` 並填入 `GEMINI_API_KEY`。

## 🏭 Agent Factory

```
/create-agent 台股技術分析師，擅長 K 線、均線、籌碼分析
/create-agent --global 英文寫作教練          # 存到 ~/.kai/agents/，所有專案共用
/load-agent 台股技術分析師
/list-agents
```

每個 Agent 是一個目錄：

```
agents/
  台股技術分析師/
    config.json      ← model、system_prompt、nickname、tts、mcp_servers…
    history.json     ← 該 Agent 的對話歷史
    notes.md         ← /remember 寫入的長期記憶
```

`system_prompt` 支援 `{CWD}`、`{DATE}`、`{AGENT_DIR}` 與 `{{include:檔名}}`；改完 `config.json` 後執行 `/reload-agent` 即可熱更新，對話不中斷。完整欄位見 [docs/usage.md](docs/usage.md)。

## 🔗 MCP 整合

設定檔位置：`~/.kai/mcp_servers.json`（全域）、`<專案>/.kai/mcp_servers.json`（專案），或寫在 agent 的 `config.json`。

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/projects"]
    },
    "my-api": {
      "type": "http",
      "url": "http://localhost:3000/mcp"
    }
  }
}
```

也可以在 Kai 裡直接加：`/mcp-add <name> <cmd> [args]` 或 `/mcp-add <name> --url <url>`。名稱加上 `_` 前綴即停用。詳見 [docs/mcp.md](docs/mcp.md)。

## 🧩 支援的模型

| 模型 | Thinking | Vision | Tools | 後端 |
|------|:---:|:---:|:---:|------|
| `qwen3-vl:4b`（預設） | ✓ | ✓ | ✓ | Ollama |
| `qwen3.5:2b` / `qwen3.5:4b` | — / ✓ | ✓ | ✓ | Ollama |
| `gemma4:e2b` / `gemma4:e4b` | ✓ | ✓ | ✓ | Ollama |
| `gemini-2.0-flash` | — | ✓ | ✓ | Gemini API |
| `gemini-2.5-flash` / `gemini-2.5-pro` | ✓ | ✓ | ✓ | Gemini API |
| `deepseek-v4-flash` / `deepseek-v4-pro` | ✓ | — | ✓ | DeepSeek API |

完整清單與新增方式見 `core/models.py`。執行中用 `/model <name>` 切換。

## ⌨️ 常用指令

| 指令 | 說明 |
|------|------|
| `/model <name>` | 切換模型（保留對話） |
| `/think` | 切換 Thinking 模式 |
| `/cls` | 歸檔並清除對話記憶（`/resume` 可恢復） |
| `/compact` | 壓縮對話歷史，釋放 context |
| `/ask <問題>` | 臨時提問，不寫入歷史（`/save` 可事後保存） |
| `/remember <內容>` | 寫入長期記憶 `.kai/notes.md` |
| `/image <路徑> [問題]` | 傳圖片給 vision 模型 |
| `/cd <路徑>` | 切換工作目錄（每個目錄有獨立歷史） |
| `/tts <engine\|off>` | 切換語音：`edge-tts` `azure-tts` `melotts` `kokoro-api` `kokoro` |
| `!<command>` | 直接執行 shell 指令 |

完整指令與快捷鍵見 [docs/usage.md](docs/usage.md)。

## ⚙️ 設定

優先順序：`<專案>/.kai/config.json` > `~/.kai/config.json` > `.env` > 程式預設值。

`.env` 只放 API 金鑰（範本見 [.env.example](.env.example)），其他調校參數放 `config.json`。執行 `/init-config` 開互動面板查看每個值的來源並匯出，`/reload-config` 免重啟套用。

## 📖 文件

| 文件 | 內容 |
|------|------|
| [usage.md](docs/usage.md) | 使用手冊：指令、檔案樹、Session 管理、模型切換 |
| [mcp.md](docs/mcp.md) | MCP 設定、transport 類型、常用 server |
| [harness-architecture.md](docs/harness-architecture.md) | Agent 基礎設施架構 |
| [agentic-loop.md](docs/agentic-loop.md) | 工具呼叫迴圈的原理與實作 |
| [app-tools-design.md](docs/app-tools-design.md) | 讓 AI 操作 app 本身的設計 |

## 🤝 貢獻

歡迎 issue 與 PR。程式風格見 [CONVENTIONS.md](CONVENTIONS.md)，開發說明見 [DEVELOPMENT.md](DEVELOPMENT.md)。送出前請確認：

```bash
uv run ruff check .
uv run pytest tests/
```

## 📄 License

[MIT](LICENSE)
