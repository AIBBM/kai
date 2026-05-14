# CONVENTIONS.md

## 語言與文字

所有對話回覆、程式碼註解、log 訊息、UI 文字（Rich Panel/Rule/status）、錯誤訊息，一律使用**繁體中文**。

避免中國大陸用語：
- 「專案」而非「項目」
- 「程式碼」而非「代碼」
- 「資料庫」而非「數據庫」
- 「函式」而非「函數」
- 「字串」而非「字符串」

## 命名規則

| 對象 | 規則 | 範例 |
|------|------|------|
| 函式、變數、模組 | `snake_case` | `run_tool`, `tool_names` |
| 類別 | `PascalCase` | `MCPClient`, `ModelProfile` |
| 常數（模組層級） | `UPPER_SNAKE` | `MAX_TOOL_CALLS`, `SEARCH_TOOL` |
| 私有 / 內部方法 | `_` 前綴 | `_reset_date`, `_cancelled` |
| 停用的 MCP server | `_` 前綴（於 mcp_servers.json）| `_twstock-db` |

## 程式碼風格

- **型別標注**：所有公開函式和方法都加 type hints；回傳值標注不可省略。
- **async 優先**：I/O 操作（HTTP、subprocess、MCP）一律用 `async def`；blocking 呼叫透過 `asyncio.to_thread()` 隔離。
- **資料類別**：純資料結構用 `@dataclass`（如 `ModelProfile`）；跨系統邊界輸入用 `pydantic.BaseModel` 驗證（如 `WebSearchArgs`）。
- **不要寫多餘的 fallback**：只在系統邊界（使用者輸入、外部 API）做驗證，內部呼叫信任型別標注。
- **不寫明顯的 what 型註解**：只有非顯而易見的 why（特殊限制、繞過 bug）才加。

## Log 層級慣例

| 層級 | 用途 |
|------|------|
| `DEBUG` | 效能計時、token 統計、工具呼叫細節 |
| `WARNING` | 可恢復的失敗（搜尋重試、schema 查詢失敗） |
| `ERROR` | 工具呼叫完全失敗、MCP 錯誤 |

log 只輸出到 stderr（`loguru` 設定於 `config.py`），不干擾 Rich 的 stdout 輸出。

## 對話行為規範

- **避免重複回答**：如果發現自己連續兩次回答相同的內容，立即停止並向使用者承認遇到邏輯錯誤。
- **優先執行指令**：收到明確指令時直接執行，不重複確認已理解的問題。

## Rich 渲染慣例

- AI 回覆用裸 `Markdown`，不包 Panel（避免複製時混入框線字元）。
- MCP 工具結果用 `Panel(expand=True)`，避免長內容溢出邊框。
- 搜尋結果用 `Table`（`box.SIMPLE_HEAD`），不加橫線。
- 思考過程用黃色 Panel。
