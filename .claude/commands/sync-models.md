# sync-models

同步 Ollama 已安裝模型到 `core/models.py` REGISTRY 與 `.env` model 清單。

## 執行步驟

1. 執行 `ollama list` 取得目前已安裝的模型清單（跳過 `NAME` header）

2. 執行 `ollama show <model>` 取得每個模型的：
   - 實際參數量（parameters）
   - context length（max ctx）
   - quantization

3. 執行 `ollama show <model> --modelfile` 檢查 TEMPLATE，判斷實際能力：

   **supports_tools 判斷（template 優先，名稱為輔）**：
   - template 含 `.Tools` 且有 `<tool_call>` 或 `tool_call` 格式 → `True`
   - template 不含 `.Tools` → `False`
   - 無法判斷時：名稱含 `coder` 的舊版模型預設 `False`，其餘 `True`

   **supports_thinking 判斷（template 優先，名稱為輔）**：
   - template 含 `.Thinking` 或 `<think>` 或 `thinking` 關鍵字 → `True`
   - 名稱含 `r1`、`qwq`、`qwen3`（非 qwen3.5:2b）、`gemma4`、`gemini-2.5` → `True`
   - qwen3.5:2b 例外：即使 template 支援，`supports_thinking=False`（2B 推理能力不足）

   **supports_vision 判斷（名稱為主）**：
   - 名稱含 `vl`、`vision`、`llava`、`gemma`、`qwen3.5` → `True`

4. 讀取 `core/models.py` 目前的 REGISTRY

5. **比對差異**：
   - 已安裝但不在 REGISTRY → 新增（用下方規則推算 num_ctx）
   - 在 REGISTRY（非 Gemini）但未安裝 → 移除
   - 已在 REGISTRY 的模型 → **重新驗證 supports_tools / supports_thinking**（template 是事實來源），num_ctx 保留不動

6. **新模型 num_ctx 推算規則（RTX 3050 4 GB）**：
   - 模型 < 2 GB：16384
   - 模型 2–4 GB：8192（部分 KV 可 spill 到 RAM）
   - 模型 > 4 GB（GPU 放不下）：8192

7. 更新 `core/models.py`：保留 Gemini section，ollama 區塊依安裝清單重寫

8. 更新 `.env` MODEL 區塊：
   - 保留目前 active 的 MODEL= 那行
   - 其餘已安裝 ollama 模型改成 `# MODEL=<name>`
   - Gemini 模型放最後一組 comments
   - 移除不再安裝的舊 comments

9. 輸出 diff 摘要：新增了哪些、移除了哪些、`.env` 更動了什麼
