"""Agent Factory — /create-agent, /load-agent, /list-agents 的核心邏輯。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

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


def _slugify(name: str) -> str:
    """將 agent 名稱轉為合法目錄名（保留中文、移除特殊字元）。"""
    slug = name.strip().replace(" ", "-").replace("/", "-")
    slug = re.sub(r'[<>:"/\\|?*]', "-", slug)
    return slug or "agent"


def agents_dir() -> Path:
    """回傳 agents/ 目錄（相對 cwd），不存在則建立。"""
    d = Path.cwd() / "agents"
    d.mkdir(exist_ok=True)
    return d


def agent_dir(name: str) -> Path:
    """回傳 agents/<slug>/ 目錄（不存在則建立）。"""
    d = agents_dir() / _slugify(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_agents() -> list[dict[str, Any]]:
    """列出所有 agents/*/config.json，回傳 metadata 清單。"""
    result = []
    base = agents_dir()
    for p in sorted(base.iterdir()):
        if not p.is_dir():
            continue
        cfg = p / "config.json"
        if not cfg.exists():
            continue
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            result.append({
                "dir": p.name,
                "name": data.get("name", p.name),
                "nickname": data.get("nickname", ""),
                "model": data.get("model", ""),
                "description": data.get("description", ""),
            })
        except Exception:
            pass
    return result


def load_agent(name: str) -> tuple[dict[str, Any], Path]:
    """載入 agents/<name>/config.json，回傳 (data, agent_dir_path)。"""
    base = agents_dir()
    candidates = [
        base / name / "config.json",
        base / _slugify(name) / "config.json",
        base / name.lower().replace(" ", "-") / "config.json",
    ]
    for p in candidates:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")), p.parent
    raise FileNotFoundError(f"找不到 agent：{name}（搜尋路徑：{[str(c.parent) for c in candidates]}）")


def save_agent(data: dict[str, Any]) -> Path:
    """儲存 agent config 到 agents/<slug>/config.json，回傳 agent 目錄路徑。

    首次建立時同步寫入初始 history.json（含 system message）；
    已存在則保留舊 history 不覆蓋。
    """
    d = agent_dir(data["name"])
    (d / "config.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    history_file = d / "history.json"
    if not history_file.exists():
        history_file.write_text(
            json.dumps(
                [{"role": "system", "content": data["system_prompt"]}],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
    return d


def validate_agent(data: dict[str, Any]) -> list[str]:
    """回傳缺少的必要欄位清單。"""
    return [f for f in REQUIRED_FIELDS if not data.get(f)]


def extract_json(text: str) -> dict[str, Any]:
    """從 LLM 回覆中提取 JSON（處理 markdown code block）。"""
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1)
    return json.loads(text.strip())
