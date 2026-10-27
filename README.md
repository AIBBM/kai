# kai

本地 AI Agent TUI — 在終端機裡跑多模型對話、工具呼叫、Agent 管理。

```
┌─ FileTree ─┬──── Chat / Agent Tabs ────┬─ Sidebar ─┐
│            │  messages                 │ model     │
│            │  streaming AI replies     │ context   │
│            ├───────────────────────────┤ git       │
│            │  [Think] [Vision]         │ MCP tools │
│            │  > prompt input           │           │
└────────────┴───────────────────────────┴───────────┘
```

## 快速開始

```bash
uv sync                     # 需要 kokoro / melotts TTS 時：uv sync --extra tts
cp .env.example .env        # 填入 API keys 與 SEARXNG_SECRET
docker compose up -d        # 啟動 SearXNG（web_search 用）
uv run python app.py
uv run python app.py ~/my-project   # 指定工作目錄
```

## 支援模型

| 模型 | Thinking | Vision | Tools | 後端 |
|------|----------|--------|-------|------|
| `qwen3-vl:4b` | ✓ | ✓ | ✓ | Ollama |
| `qwen3:4b/8b` | ✓ | — | ✓ | Ollama |
| `gemma4:e2b/e4b` | ✓ | ✓ | ✓ | Ollama |
| `gemini-2.0-flash` | — | ✓ | ✓ | Gemini API |
| `gemini-2.5-flash` | ✓ | ✓ | ✓ | Gemini API |
| `gemini-2.5-pro` | ✓ | ✓ | ✓ | Gemini API |
| `deepseek-v4-flash` | ✓ | — | ✓ | DeepSeek API |
| `deepseek-v4-pro` | ✓ | — | ✓ | DeepSeek API |

完整清單見 `core/models.py`。

## 環境設定（.env）

```env
MODEL=qwen3-vl:4b           # 預設模型
GEMINI_API_KEY=...          # Google Gemini API key
DEEPSEEK_API_KEY=...        # DeepSeek API key
USERNAME=你                  # 對話泡泡顯示名稱
NICKNAME_qwen3_vl_4b=小Q    # 模型暱稱（NICKNAME_<model 特殊字元換底線>）
BASH_TIMEOUT=30             # bash 指令逾時秒數
```

## 斜線指令

| 指令 | 說明 |
|------|------|
| `/model <name>` | 切換模型 |
| `/nickname [名字]` | 查詢或設定目前模型暱稱 |
| `/think` | 切換 Thinking 模式 |
| `/clear` | 清除畫面（保留對話記憶） |
| `/cls` | 歸檔並清除對話記憶（可 `/resume` 恢復） |
| `/resume [N]` | 列出或載入歸檔 session |
| `/rename <名稱>` | 命名目前 session |
| `/ask <問題>` | 臨時提問，不污染對話歷史 |
| `/save` | 將上一次 `/ask` 存入歷史 |
| `/remember <內容>` | 寫入 `.kai/notes.md` 永久記憶 |
| `/compact` | 用 Gemini 壓縮對話歷史（釋放 context） |
| `/image <路徑> [問題]` | 傳圖片給 vision 模型 |
| `/cd <路徑>` | 切換工作目錄（各目錄有獨立歷史） |
| `/debug` | 切換工具除錯模式 |
| `/mcp-list` | 顯示已連線 MCP server 與工具 |
| `/mcp-add <name> <cmd> [args]` | 新增 stdio MCP server |
| `/mcp-add <name> --url <url>` | 新增 HTTP MCP server |
| `/mcp-del <name>` | 移除 MCP server |
| `/mcp-reload` | 重新載入 MCP servers |
| `/create-agent <描述>` | 用 AI 設計新 Agent |
| `/load-agent <name>` | 在新 tab 載入 Agent |
| `/list-agents` | 列出所有已建立的 Agents |
| `/delete-agent [name]` | 刪除 Agent（省略開多選面板） |
| `/close-agent [name]` | 關閉 Agent tab（省略關目前 tab） |
| `/greeting on\|off` | 開關 subagent 開場白 |
| `/reload-agent` | 熱重載當前 Agent config（保留對話歷史） |
| `/reload-config` | 熱重載 `.kai/config.json` 設定 |
| `/init-config` | 互動式 ConfigScreen 匯出設定 |
| `/tts <engine\|off>` | 切換 TTS 引擎或關閉：`edge-tts` `azure-tts` `melotts` `kokoro` `off` |

## Agent Factory

用自然語言描述需求，kai 自動生成 Agent：

```
/create-agent 台股技術分析師，擅長 K 線、均線、籌碼分析
/load-agent 台股技術分析師
```

每個 Agent 有獨立目錄：

```
agents/
  台股技術分析師/
    config.json      ← model, system_prompt, nickname...
    history.json     ← 該 Agent 的對話歷史
    .history/        ← /cls 歸檔
```

## MCP

在 `mcp_servers.json` 設定外部工具：

```json
{
  "mcpServers": {
    "postgres": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://..."]
    },
    "my-api": {
      "type": "http",
      "url": "http://localhost:3000/mcp"
    }
  }
}
```

## 快捷鍵

| 鍵 | 功能 |
|----|------|
| `Ctrl+Q` | 退出 |
| `Ctrl+P` | 命令面板 |
| `Ctrl+T` | 顯示/隱藏檔案樹 |
| `Ctrl+B` | 顯示/隱藏側邊欄 |
| `Ctrl+W` | 關閉目前 tab |
| `Ctrl+L` | 清除畫面 |
| `Escape` | 取消串流 |
| `Tab / →` | 接受 ghost text 預測 |
| `↑ / ↓` | 輸入歷史導航 |

## 架構

```
app.py              → Textual TUI 主程式
core/
  agent.py          → Agent 類別：串流、工具呼叫迴圈、歷史管理
  agent_factory.py  → /create-agent 邏輯，agents/ 目錄管理
  backends.py       → stream_ollama / stream_openai_compat
  tools/            → web_search, fetch_urls, bash_exec, dify_retrieve, run_tool
  models.py         → ModelProfile + REGISTRY
  config.py         → 環境變數常數
  mcp_client.py     → MCP server 生命週期 + 工具 dispatch
  tts.py            → TTS 引擎抽象（edge-tts, azure-tts, melotts, kokoro）
mixins/
  streaming.py      → _send worker、串流回呼、TTS 播放
  agents.py         → /create-agent, /load-agent, /list-agents, /reload-agent
  app_tools.py      → AI 可呼叫的 app tools（切換模型、TTS、MCP…）
  session.py        → /cd, /compact, /clear, /resume, /model 等
  bash.py           → ! 指令執行
  mcp.py            → /mcp-list, /mcp-add, /mcp-del, /mcp-reload
widgets/
  input.py          → HistoryInput（歷史導航、ghost text）、ChatAutoComplete
  chat.py           → AIMessage、FileViewer、ImageBubble、CapabilityBadge
  tree.py           → FileTree（多選、右鍵選單、fs watcher）
  sidebar.py        → Sidebar、ResizeHandle
  commands.py       → Ctrl+P 命令面板
  utils.py          → 剪貼簿、檔案圖示、工具函數
  mcp_list.py       → /mcp-list ModalScreen
```
