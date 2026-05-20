# kai

本地 AI Agent TUI — 整合 Ollama / Gemini 模型、MCP 工具、檔案系統操作，以 Textual 打造的終端機聊天介面。

<!-- screenshot -->

## 功能亮點

- **多模型熱切換**：Ollama（Qwen3、Gemma、DeepSeek-R1 等）與 Gemini（2.0/2.5 Flash/Pro）在同一對話中隨時切換，歷史記錄保留不中斷。
- **串流渲染優化**：串流過程以純文字寫入，完成後才做一次 Markdown 渲染，長回應也不會越來越卡。
- **MCP 工具整合**：透過 MCP 協定連接外部工具 server，模型可直接呼叫；Sidebar 即時顯示已掛載工具清單。
- **FileTree 多選刪除**：Ctrl+Click 逐項選取、Space 範圍選取、Ctrl+A 全選，右鍵一鍵批次刪除。
- **Session 管理**：`/clear` 自動歸檔歷史到 `.history/`，`/resume` 隨時還原任意一份舊對話。
- **Bash 執行**：輸入以 `!` 開頭的指令直接執行 shell，`!cd` 會同步切換 Python 行程的工作目錄。
- **FileViewer**：雙擊檔案在分頁內開啟，支援語法高亮、外部變更自動偵測重載、WSL 相容複製。
- **每個目錄獨立歷史**：`history.json` 存在各專案目錄下，`/cd` 切換目錄時自動載入對應歷史。

## 快速開始

### 前置需求

- [uv](https://docs.astral.sh/uv/) — Python 套件管理器
- [Ollama](https://ollama.com/) — 本地模型執行環境（若使用 Gemini 可略過）
- （選用）Docker — 啟動 SearXNG 網路搜尋

### 安裝

```bash
git clone https://github.com/your-org/kai.git
cd kai
uv sync
```

複製並編輯環境變數：

```bash
cp .env.example .env
# 編輯 .env，填入 MODEL、GEMINI_API_KEY 等
```

### 啟動

```bash
# 主介面（Textual TUI）
uv run python app.py

# 啟動 SearXNG + Valkey（選用，需 Docker）
bash searxng/start.sh

# 舊版 CLI（仍可用）
uv run python main.py
```

## 環境變數

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `MODEL` | `qwen3-vl:4b` | 啟動時使用的模型 |
| `DEBUG_TOOLS` | `0` | 設為 `1` 啟動時顯示工具呼叫 debug 卡片 |
| `NUM_CTX` | `0` | Context window 大小覆寫（`0` 表示使用模型預設值） |
| `MAX_TOOL_CALLS` | `15` | 每輪最多工具呼叫次數 |
| `TEMPERATURE` | `0.6` | 生成溫度 |
| `USERNAME` | `你` | 對話氣泡與提示列顯示名稱 |
| `BASH_TIMEOUT` | `30` | `!` bash 指令的預設逾時秒數 |
| `SEARXNG_URL` | `http://localhost:8888/search` | SearXNG API 端點 |

## 文件

詳細使用說明請參閱 [docs/usage.md](docs/usage.md)。

## License

MIT
