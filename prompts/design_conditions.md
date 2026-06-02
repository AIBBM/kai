條件欄位（符合才加入，否則省略）：
- 若用途涉及查詢知識庫、文件庫、RAG、內部資料：
    加入 "datasets": []
    system_prompt 內加入："## 可用知識庫\n\n{DIFY_DATASETS}\n"（系統啟動時自動替換為 KB 清單）
    system_prompt 說明必須優先用 dify_retrieve 工具查詢、回答引用來源文件
- 若用途涉及語音輸出、朗讀、TTS：
    加入 "tts": {"engine": "edge-tts", "voice": "zh-TW-HsiaoChenNeural", "player": "ffplay", "speed": 1.0}
    中文女聲：zh-TW-HsiaoChenNeural，中文男聲：zh-TW-YunJheNeural，英文：en-US-AriaNeural
