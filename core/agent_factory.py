"""Agent Factory — /create-agent, /load-agent, /list-agents 的核心邏輯。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

AGENTS_DIR = Path("agents")

REQUIRED_FIELDS = {"name", "model", "system_prompt"}

CREATE_AGENT_PROMPT = """\
你是一個 AI agent 設計師。根據用戶的描述，設計一個完整的 AI agent 設定。

用戶描述：{description}

可用模型：{models}

請輸出一個 JSON（不要加任何說明文字，只輸出 JSON），格式如下：

{{
  "name": "agent 的顯示名稱（中文可以）",
  "nickname": "簡短暱稱（2-4字，用於 UI 顯示）",
  "model": "選擇最適合的模型名稱",
  "system_prompt": "完整的 system prompt，包含角色定義、專業知識、行為規範、回覆風格",
  "mcp_servers": {{}},
  "history_keep": 15,
  "description": "一句話說明這個 agent 的用途"
}}

設計原則：
- system_prompt 要具體、有深度，包含專業術語和邊界條件
- nickname 要貼切且好記
- model 選擇依任務性質：需要思考選 gemini-2.5-flash，純對話選 deepseek-v4-flash，需視覺選 qwen3-vl:4b
- mcp_servers 保持空物件（用戶可自行擴充）
- history_keep 依任務複雜度調整（10-30）
"""


def agents_dir() -> Path:
    """回傳 agents/ 目錄（相對 cwd），不存在則建立。"""
    d = Path.cwd() / "agents"
    d.mkdir(exist_ok=True)
    return d


def list_agents() -> list[dict[str, Any]]:
    """列出所有 agents/*.json，回傳 metadata 清單。"""
    result = []
    for p in sorted(agents_dir().glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            result.append({
                "file": p.stem,
                "name": data.get("name", p.stem),
                "nickname": data.get("nickname", ""),
                "model": data.get("model", ""),
                "description": data.get("description", ""),
            })
        except Exception:
            pass
    return result


def load_agent(name: str) -> dict[str, Any]:
    """載入 agents/<name>.json，回傳完整 dict。"""
    candidates = [
        agents_dir() / f"{name}.json",
        agents_dir() / f"{name.lower().replace(' ', '-')}.json",
    ]
    for p in candidates:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"找不到 agent：{name}")


def save_agent(data: dict[str, Any]) -> Path:
    """儲存 agent config，回傳檔案路徑。"""
    slug = data["name"].lower().replace(" ", "-").replace("/", "-")
    # 移除非 ASCII 特殊字元，保留中文
    import re
    slug = re.sub(r'[<>:"/\\|?*]', "-", slug)
    path = agents_dir() / f"{slug}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def validate_agent(data: dict[str, Any]) -> list[str]:
    """回傳缺少的必要欄位清單。"""
    return [f for f in REQUIRED_FIELDS if not data.get(f)]


def extract_json(text: str) -> dict[str, Any]:
    """從 LLM 回覆中提取 JSON（處理 markdown code block）。"""
    import re
    # 嘗試去掉 ```json ... ```
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1)
    return json.loads(text.strip())
