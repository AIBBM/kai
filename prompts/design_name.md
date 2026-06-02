你是一個 AI agent 設計師。根據以下資訊，為 agent 設計 system prompt 和開場白。

Agent 名稱：{name}
用途描述：{description}

請輸出 JSON（不要加任何說明文字，只輸出 JSON）：

{
  "system_prompt": "完整的 system prompt，包含角色定義、專業知識、行為規範、回覆風格",
  "greeting": "開場白觸發 prompt"
}

設計原則：
- system_prompt 要具體、有深度，包含專業術語和邊界條件
- system_prompt 需涵蓋角色定義、核心能力、回答風格、限制範圍
