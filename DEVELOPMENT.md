# DEVELOPMENT.md

開發筆記：設計決策、踩過的坑、不明顯的規則。

---

## 設定系統

三層，優先度由高到低：

```
.env          ← 使用者 override（實際生效）
config.py     ← 應用程式預設值（.env 未設定時生效）
models.py     ← 各模型的 num_ctx（NUM_CTX=0 時生效）
```

**規則：只在 `.env` 放真正需要 override 的東西。** 改預設值改 `config.py`；改特定模型的 context window 改 `models.py`。

### `.env` 應該有什麼

| 設定 | 說明 |
|------|------|
| `MODEL` | 目前使用的 Ollama 模型 |
| `SEARXNG_URL` | SearXNG API 位址（換機器時改） |
| `USERNAME` | 顯示名稱 |
| `CONTEXT7_API_KEY` | Context7 API 金鑰 |
| `NUM_CTX=8192` | 只在需要限制 VRAM 時才取消註解；否則用各模型預設值 |

所有其他調校參數（`MAX_TOOL_CALLS`、`MAX_SEARCH_CHARS` 等）**不應出現在 `.env`**，由 `config.py` 預設值控制。

### `NUM_CTX` 的語意

- `.env` 未設定（或 `NUM_CTX=0`）→ 每個模型用 `models.py` REGISTRY 裡各自的 `num_ctx`
- `.env` 設定 `NUM_CTX=N` → **覆蓋所有模型**，全部用 N

覆蓋行為在 `agent.py:Agent.__init__`：
```python
if NUM_CTX:
    self.profile.num_ctx = NUM_CTX
```

### `config.py` 預設值（2025-05 版）

| 常數 | 預設 | 說明 |
|------|------|------|
| `MAX_TOOL_CALLS` | 15 | 單輪工具呼叫上限；Playwright 多步瀏覽需要 10+ |
| `MAX_SEARCH_CHARS` | 8000 | MCP/搜尋結果截斷；browser_snapshot 需要 ≥8K 才看得到搜尋框 |
| `BASH_TIMEOUT` | 30 | bash_exec 逾時秒數 |
| `HISTORY_KEEP` | 10 | 保留的對話輪數 |

---

## 模型設定（models.py）

各模型的 `num_ctx` 對應 Playwright agentic 任務的需求：

| 模型 | num_ctx | 原因 |
|------|---------|------|
| `qwen3.5:4b` | 16384 | 多步工具呼叫累積 prompt，8192 不夠 |
| `qwen3-vl:4b` | 16384 | 同上 |
| `qwen3:8b` | 16384 | 同上 |
| `qwen3:4b` | 8192 | 無 vision，任務較輕 |
| `gemma3/4` | 8192 | 同上 |

`get_profile()` 回傳 `dataclasses.replace()` 的 copy，避免 `Agent.__init__` 修改 `num_ctx` 時汙染 REGISTRY。

---

## TUI 架構（app.py）

CLI 版（`main.py`）之外，`app.py` 是 Textual TUI。

### MCP 生命週期

MCP client 用獨立的 `asyncio.create_task()` 長駐執行：

```python
# on_mount
self._mcp_task = asyncio.get_event_loop().create_task(self._mcp_lifecycle())

async def _mcp_lifecycle(self):
    await mcp.start()
    await self._mcp_done.wait()   # 等待退出訊號
    await mcp.stop()              # start/stop 同一 task
```

**為什麼不用 `@work`**：anyio cancel scope 必須在同一個 asyncio task 中 enter/exit。若 `start()` 和 `stop()` 在不同 task，會 deadlock 或 `RuntimeError`。`asyncio.create_task()` 保證整個生命週期在同一 task。

**退出流程**（`action_quit`）：
1. `_mcp_done.set()` → 讓 `_mcp_lifecycle` 解除 wait，進入 finally 執行 `mcp.stop()`
2. `asyncio.wait_for(_mcp_task, timeout=2.0)` → 等 MCP 正常關閉
3. `agent.save_history()` → 確保歷史寫入（包含串流中途強制退出的情況）
4. `self.exit()`

### MCP stderr 處理

MCP stdio server 啟動時會印 startup log，會汙染 TUI 畫面。`MCPClient` 接受 `errlog: TextIO` 參數；TUI 傳 `open(os.devnull, 'w')` 靜音：

```python
mcp = MCPClient(servers, errlog=open(os.devnull, "w"))
```

注意：必須是有真實 file descriptor 的物件（`fileno()` 可用）。`io.TextIOBase` 子類沒有 `fileno()`，anyio 會拋 `UnsupportedOperation`。

### 滑鼠事件洩漏到 Input

Playwright 或 WSLg 切換視窗焦點時會重置 terminal mouse tracking，產生 SGR/X10 binary 序列洩入 Textual `Input`：

```
ESC[<35;80;25M  →  Textual 吃掉 ESC[，剩 <35;80;25M 逐字進 Input
```

處理方式（`on_input_changed`）：偵測到任何 mouse sequence pattern 就直接清空整個 input，因為 user 是在移動滑鼠，不是打字：

```python
_MOUSE_SEQ = re.compile(r"(?:\x1b\[?)?<?(?:\d+;)+\d+[Mm]")

def on_input_changed(self, event):
    if _MOUSE_SEQ.search(event.value):
        event.input.value = ""
        return
```

### 工具呼叫錯誤顯示

`agent.py` 裡有兩種靜默失敗要注意：

1. **`ollama.ResponseError`**（context 溢出等）：原本只 `console.print()`（CLI 可見，TUI 不可見），現在加了 `logger.error()` + TUI 模式 re-raise
2. **`MAX_TOOL_CALLS` 超限**：同樣問題，現在加了 `logger.warning()` + TUI 模式 `raise RuntimeError`

判斷「TUI 模式」的方法：`on_token` callback 是否設定（TUI 呼叫 `chat()` 時會傳 callback，CLI 不傳）。

---

## 已知限制

- `browser_snapshot` 回傳 ~400KB accessibility tree；`MAX_SEARCH_CHARS=8000` 截斷後模型只看到前 8K chars。複雜頁面可能需要更大的值（留意 prompt token 數）。
- Playwright 在 WSL2 需要 `--no-sandbox`（Chromium sandbox 需要 kernel namespace，WSL2 不支援）。
- `browser_navigate` 預設用 playwright-managed `chrome-for-testing`；系統 Chrome 不適用，要另外安裝：`npx @playwright/mcp install-browser chrome-for-testing`。
