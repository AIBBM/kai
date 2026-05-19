# sync-models

同步 Ollama 已安裝模型到 `core/models.py` REGISTRY 與 `.env` model 清單。

## 執行步驟

1. 執行 `ollama list` 取得目前已安裝的模型清單（跳過 `NAME` header）

2. 執行 `ollama show <model>` 取得每個模型的：
   - 實際參數量（parameters）
   - context length（max ctx）
   - quantization

3. 讀取 `core/models.py` 目前的 REGISTRY

4. **比對差異**：
   - 已安裝但不在 REGISTRY → 新增（用下方規則推算 num_ctx）
   - 在 REGISTRY（非 Gemini）但未安裝 → 移除
   - 已在 REGISTRY 的模型 → 保留不動（不覆蓋已調整過的設定）

5. **新模型 num_ctx 推算規則（RTX 3050 4 GB）**：
   - 模型 < 2 GB：16384
   - 模型 2–3 GB：8192
   - 模型 3–4 GB：8192（部分 KV spill 到 RAM）
   - 模型 > 4 GB（GPU 放不下）：8192

6. **新模型 capability 推算規則**：
   - 名稱含 `vl`、`vision`、`llava`、`gemma` → `supports_vision=True`
   - 名稱含 `r1`、`qwq`、`qwen3`、`gemma4`、`gemini-2.5` → `supports_thinking=True`
   - 名稱含 `coder` 且 Ollama template 已知不含 function calling → `supports_tools=False`
   - 其餘預設 `supports_tools=True`

7. 更新 `core/models.py`：保留 Gemini section，ollama 區塊依安裝清單重寫

8. 更新 `.env` MODEL 區塊：
   - 保留目前 active 的 MODEL= 那行
   - 其餘已安裝 ollama 模型改成 `# MODEL=<name>`
   - Gemini 模型放最後一組 comments
   - 移除不再安裝的舊 comments

9. 輸出 diff 摘要：新增了哪些、移除了哪些、`.env` 更動了什麼
