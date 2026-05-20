# 客戶群分析

## 有客戶群，但很「窄」

這不是大眾產品，而是特定族群的重度工具。潛在客戶大概落在以下幾類人：

---

## 目標客群

### 1. 資深後端 / DevOps 工程師（最核心）

**為什麼會買單：**
- 本來就活在終端機裡（tmux / screen / Neovim 使用者）
- 不喜歡離開 terminal 去開瀏覽器用 ChatGPT
- 想要一個「能直接操作電腦」的 AI，而不是只能聊天的 chatbot
- 需要 AI 幫他們操作 Docker、Git、SSH、curl API 測試

**痛點：**
> 「我要查某個 API 文件，然後寫個 curl 測試，再讀個 log 檔找錯誤 — 我不想開六個分頁，我只要在終端機一句話搞定。」

**規模預估：** 台灣約數千～萬人，全球可能數十萬人。

---

### 2. AI / ML 研究人員

**為什麼會買單：**
- 常需要跟 Ollama 等本地模型互動
- 要在 terminal 裡快速切換不同模型做測試（gemma4 → qwen3.5 → deepseek）
- 需要「可複現的實驗記錄」— 完整對話歷史
- 搭配 `/cd` 切換目錄，不同專案有自己的對話上下文

**痛點：**
> 「我同時在跑三個實驗，每個專案的對話歷史要分開。而且我想在 qwen3.5 和 deepseek 之間快速切換比較結果。」

---

### 3. MCP 生態圈早期採用者

**為什麼會買單：**
- MCP（Model Context Protocol）是 2025 年新興標準
- 這個專案是少數原生支援 MCP 的工具
- 對想玩 MCP server 串接的人來說，這是現成的 playground

**注意：** 這群人目前非常小眾，屬於先驅者。

---

### 4. 想擺脫訂閱費的人

**為什麼會買單：**
- 支援 Ollama 本地模型 — 完全免費、離線可用
- 支援 Gemini API（有免費額度）和 DeepSeek API（極便宜）
- 對比 ChatGPT Pro / Claude Pro（各 $20/月），長期可省一筆

**注意：** 這些人需要有一定技術能力自己架設和維護。

---

## 不適合的人

| 類別 | 原因 |
|------|------|
| 一般使用者 | 連 terminal 都不會開，更別說裝 Ollama + Docker |
| 前端設計師 | 沒有 terminal 工作流程 |
| 只想聊天的人 | GUI chatbot 體驗更好，還支援圖片、語音 |
| 企業 IT 部門 | 需要維運、管理後台、RBAC，這專案完全沒有 |
| IDE 使用者 | VS Code / JetBrains 已經有 Copilot、Cursor，整合更深 |

---

## 市場規模估算

| 層級 | 全球人數估算 |
|------|-------------|
| 所有軟體工程師 | ~3,000 萬 |
| 其中「重度 terminal 使用者」 | ~300 萬（10%） |
| 其中「會想嘗試 terminal AI agent」 | ~30 萬（10%） |
| 其中「願意安裝維護一套本地工具」 | ~3 萬（10%） |

**核心目標市場：約 1～3 萬人（全球）**

這不是獨角獸規模，但做一個小型開源專案或付費工具養活自己是綽綽有餘的（參考 Warp、Kitty、Alacritty 這些 terminal 工具的使用者忠誠度）。

---

## 競爭對手比較

| 工具 | 定位 | 與本專案的差異 |
|------|------|---------------|
| Open Interpreter | CLI 語音控制電腦 | 沒有 TUI，純指令互動 |
| Aider | AI 配對程式設計 | 專注 git diff 驅動開發，無通用 agent |
| Claude Code | Anthropic 官方 CLI | 封閉、只支援 Claude、無本地模型 |
| Cursor / Windsurf | IDE 內建 AI | 有 GUI，但只在 IDE 裡，terminal 體驗不同 |
| Shell-GPT (sgpt) | AI shell 建議 | 只能建議指令，不能自主操作電腦 |

**本專案的獨特定位：**
- 唯一：Textual TUI + 工具呼叫 + MCP + 多模型切換（本地 + API）的完整組合
- 唯一：能做到「幫我搜尋、瀏覽網頁、讀檔案、執行指令」的 terminal 工具
- 唯一：完全開源且支援 Gemini / DeepSeek / Ollama 三種後端

---

## 商業化方向建議

| 方向 | 做法 |
|------|------|
| 開源社群版 | 維持現狀，建立 GitHub Stars |
| Pro 授權 | 加 MCP marketplace、企業 SSO、持久化歷史資料庫 |
| 企業維運版 | 針對 DevOps team，提供 managed MCP server、共用工具註冊 |
| 一鍵安裝包 | 包成 Homebrew cask / Docker image，降低安裝門檻 |

---

## 結論

有客戶群，但不大。這是一個「小眾的精品工具」，不是大眾產品。

它的價值在於：
1. **做對一件事**：terminal 原生的 AI agent 體驗，目前市場上沒有一個工具做得好
2. **站位正確**：押注 MCP 標準 + 本地模型趨勢，方向是對的
3. **品質夠高**：Textual TUI 不是隨便生的 prototype，是真的可以 daily drive 的產品

**建議路線：**
- 先開源累積社群（GitHub Stars = 最好的履歷）
- 觀察 6 個月看 MCP 生態會不會起飛
- 如果起來了，再決定要不要商業化

> 一句話總結：不會賺大錢，但會有一群死忠用戶愛死它。
