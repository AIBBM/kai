# App-Level Tools 設計討論

## 背景

kai 有許多 `/指令`（create-agent、load-agent、cd、model…），目前只能由使用者手動輸入。
目標：讓 AI 能以自然語言理解使用者意圖，自動呼叫對應的 app 操作。

---

## 設計選項

### Option A：App-level tools（callback 注入）— **採用**

在 `Agent` 上加兩個 optional 欄位：

```python
self.extra_tools: list[dict] = []           # JSON schema（description 給 AI 讀）
self.extra_tools_map: dict[str, Callable] = {}  # 實作（callback 到 ChatApp 方法）
```

`ChatApp` 在 app 啟動時將 `_do_create_agent`、`_do_load_agent` 等注入 main agent。
`Agent.chat()` 把 `extra_tools` 合進 `active_tools`、`extra_tools_map` 合進 tool dispatch。

**只有 main agent 獲得這些工具**，sub-agent tab 不注入，因此注入只做一次，沒有重複注入問題。

### Option B：解析 AI 回覆中的 slash command 格式

streaming 結束後掃 `[[CMD: /create-agent 描述]]`，格式 fragile，AI 連鎖操作做不到。

### Option C：AI 建議 + 使用者一鍵執行

顯示「▶ 執行」按鈕，使用者確認才跑。最保守，但多步驟自動化做不到。

---

## LLM Tool-Calling 機制

### AI 怎麼知道有哪些工具

每次呼叫 LLM API 時，`active_tools` 隨 request 一起送出。LLM 讀每個 tool 的
`description` 做語意比對，決定要不要呼叫、呼叫哪個。**description 就是說明書**，
寫越具體（附上使用情境範例）命中率越高。

```python
{
    "type": "function",
    "function": {
        "name": "create_agent",
        "description": "建立一個新的 AI agent。當用戶說「幫我建 agent」、「我需要一個專門做 X 的 AI」時使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "agent 的用途描述，例如：「一個專門回答 Python 問題的助手」"
                }
            },
            "required": ["description"]
        }
    }
}
```

### 每次 API call 送出的內容

```
messages = [
    {"role": "system",    "content": "...system prompt..."},  # 1. system prompt
    {"role": "user",      "content": "..."},                  # 2. history
    {"role": "assistant", "content": "..."},                  #    (trimmed 到 HISTORY_KEEP pair)
    ...
    {"role": "user",      "content": "目前這則輸入"},
]

tools = active_tools   # 3. tool schemas（含 app-level tools）
```

### Tool-calling 來回（單次 turn 內）

```
用戶：「幫我建一個寫 SQL 的助手」
  ↓
LLM 輸出 tool_call: create_agent(description="一個專門寫 SQL 的助手")
  ↓
app 執行 _do_create_agent(...)
  ↓
{"role": "tool", "content": "已建立"} 回傳給 LLM
  ↓
LLM 最終回覆：「✅ 已幫你建立 SQL 助手，輸入 /load-agent 開啟」
```

Tool 來回只活在 `Agent.chat()` 的局部 `messages` 裡，**不寫入 history**。
存入 history 的只有最終 user + assistant 兩則。

---

## 可開放為 AI tool 的指令

### 有意義（AI 自主操作）

| 指令 | tool 名稱 | 說明 |
|------|----------|------|
| `/create-agent` | `create_agent` | 設計並建立 agent |
| `/load-agent` | `load_agent` | 開啟 agent tab |
| `/list-agents` | `list_agents` | 查詢現有 agents |
| `/delete-agent` | `delete_agent` | 刪除 agent |
| `/cd` | `change_dir` | 切換工作目錄 |
| `/model` | `switch_model` | 切換模型 |
| `/remember` | `remember` | 寫筆記到 notes.md |
| `/rename` | `rename_session` | 幫這段對話命名 |
| `/mcp-add` | `mcp_add` | 新增 MCP server |
| `/mcp-del` | `mcp_del` | 移除 MCP server |
| `/compact` | `compact_history` | 壓縮 history |
| `/tts` | `set_tts` | 切換語音引擎 |

### 不適合開放

| 指令 | 原因 |
|------|------|
| `/clear` | 破壞性，應由使用者確認 |
| `/resume` | 開 modal picker，純 UI 動作 |
| `/init-config` | 開 modal，非指令操作 |
| `/ask` / `/save` | meta 指令，AI 呼叫無意義 |
| `/think` / `/debug` | 偏好切換，使用者控制較合理 |
| `/image` | 使用者選檔案的動作 |

---

## 實作計畫

1. `core/agent.py`：加 `extra_tools` + `extra_tools_map` 欄位，`chat()` 合入 dispatch
2. 新增 `mixins/app_tools.py`：定義所有 tool schemas + callback factories，`AppToolsMixin`
3. `ChatApp.__init__` 呼叫 `_inject_app_tools(self.agent)`（只對 main agent 執行一次）
4. 優先實作 agent 系列（create / load / list / delete）+ cd + model + remember，其餘後補
