# sync-models

同步 Ollama 已安裝模型到 `core/models.py` REGISTRY 與 `.env` model 清單。

## 執行步驟

1. 執行 `ollama list` 取得目前已安裝的模型清單（跳過 `NAME` header）

2. 執行 `ollama show <model>` 取得每個模型的：
   - 實際參數量（parameters）
   - context length（max ctx）
   - quantization

3. 執行以下指令取得每個模型的實際能力（最準確的來源）：
   ```
   curl -s http://localhost:11434/api/show -d '{"name":"<model>"}' | jq '.capabilities'
   ```
   回傳範例：`["completion", "vision", "tools", "thinking"]`

   **依 capabilities 欄位對應**：
   - 含 `tools`     → `supports_tools=True`，否則 `False`
   - 含 `thinking`  → `supports_thinking=True`，否則 `False`
   - 含 `vision`    → `supports_vision=True`，否則 `False`

   **例外（覆蓋 capabilities 的刻意設定，不要修改）**：
   - `qwen3.5:2b`：`supports_thinking=False`（Ollama 雖回報 thinking，但 2B 推理品質差，開啟只增加延遲）
   - `qwen2.5-coder:3b`：`supports_tools=False`（Ollama 0.24 不解析此 template 的 `<tool_call>`，tool call 輸出為純文字 content）

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
