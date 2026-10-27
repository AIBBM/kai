# Agent 記憶體與 System Prompt 機制

## 每次對話送出的內容

```
chat() 呼叫時：

1. _rebuild_system_prompt()
   → 重讀 notes.md
   → 覆寫記憶體的 history[0]（system message）

2. history.append(user_msg)

3. messages = _trim_history()
   → system[0] + 最近 N pair（history_keep，預設 10）

4. 送給 AI：messages + 當輪 tool_calls / tool results（不存入 history）
```

## history.json vs 實際送給 AI 的 messages

| | `history.json` | 送給 AI 的 `messages` |
|---|---|---|
| system message | ✓ 存（上次 rebuild 版本） | ✓ 送（當下最新版） |
| user / assistant 對話 | ✓ 全部存 | 只送最近 N pair |
| tool_calls | ✗ 不存 | ✓ 當輪才有 |
| tool results | ✗ 不存 | ✓ 當輪才有 |

tool 的中間步驟（呼叫、回傳）只存在當輪 `messages`，結束後丟棄。下一輪 AI 只看到最終的 assistant 文字。

## notes.md 與 `/remember`

`/remember <內容>` 寫入 `notes.md`，立即呼叫 `_rebuild_system_prompt()`，下一輪對話生效。

- 主 agent：寫到 `<cwd>/.kai/notes.md`
- subagent：寫到 `agents/<name>/notes.md`

注入位置在 system message 尾端：

```
## 📝 專案記憶
- 你記住的內容

以上規則必須嚴格遵守。
```

刪掉 `notes.md` 的內容後，**送出下一條訊息**時 `_rebuild_system_prompt()` 重讀檔案，notes 就從 system message 消失。

## System Prompt 模板變數

`config.json` 的 `system_prompt` 支援展開，由 `_resolve_prompt()` 處理：

| 變數 | 展開為 |
|------|--------|
| `{AGENT_DIR}` | agent 目錄絕對路徑 |
| `{CWD}` | 目前工作目錄 |
| `{DATE}` | 今天日期 `YYYY-MM-DD` |
| `{{include:filename}}` | 載入 `<agent_dir>/filename` 全文 |

展開時機：`/load-agent`、`/list-agents` 選擇、`/reload-agent`、`/cd`。

用途：避免寫死路徑，大型參考文件（API 文件、規格書）可獨立維護再 include 進來。

```json
{
  "system_prompt": "你的 agent 目錄是 {AGENT_DIR}。\n\n以下是 API 文件：\n{{include:api_docs.md}}"
}
```

## 透過編輯 history.json 控制 AI 的記憶

直接刪掉 `history.json` 裡的 pair，AI 下次就不知道那些事。

**陷阱**：改完檔案不會自動 sync 回記憶體，需要觸發 reload：

| 觸發方式 | 重讀 history.json |
|---|---|
| `/cd .` | ✓ |
| `/reload-agent` | ✓（model 換或不換都會重讀） |
| `/load-agent <name>` | ✓ |
| `/list-agents` 面板選擇 | ✓ |
| `/resume` | ✓ |
| 重開 app | ✓ |

改完直接繼續對話，AI 還是用記憶體裡的舊 pair。

## system message 的版本問題

`history.json` 裡存的 system message 是**上次 rebuild 時的快照**，不一定是最新的（例如你改了 `notes.md` 但還沒送訊息）。實際送給 AI 的 system message 永遠是 `chat()` 開頭 `_rebuild_system_prompt()` 當下重建的，不是從 `history.json` 讀的舊版本。
