"""Agent Factory — /create-agent, /load-agent, /list-agents 的核心邏輯。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = {"name", "model", "system_prompt"}

GLOBAL_AGENTS_DIR = Path.home() / ".kai" / "agents"

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
  "greeting": "開場白觸發 prompt，例如：「請以你的角色主動打招呼，介紹你自己和你能提供的幫助」",
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


def local_agents_dir() -> Path:
    """cwd/agents/（專案專屬，可 commit）。"""
    d = Path.cwd() / "agents"
    d.mkdir(exist_ok=True)
    return d


def global_agents_dir() -> Path:
    """~/.kai/agents/（跨專案共用）。"""
    d = GLOBAL_AGENTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def agents_dir() -> Path:
    """向後相容別名，回傳 local agents 目錄。"""
    return local_agents_dir()


def agent_dir(name: str, global_scope: bool = False) -> Path:
    """回傳 agent 目錄（不存在則建立）。"""
    base = global_agents_dir() if global_scope else local_agents_dir()
    d = base / _slugify(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_agents() -> list[dict[str, Any]]:
    """列出 local（cwd/agents/）和 global（~/.kai/agents/）的所有 agent。

    回傳清單中每筆有 scope: "local" | "global"。
    local 排在前面；同一 scope 內按目錄名排序。
    """
    result = []
    for scope, base in (("local", Path.cwd() / "agents"), ("global", GLOBAL_AGENTS_DIR)):
        if not base.exists():
            continue
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
                    "scope": scope,
                    "name": data.get("name", p.name),
                    "nickname": data.get("nickname", ""),
                    "model": data.get("model", ""),
                    "description": data.get("description", ""),
                })
            except Exception:
                pass
    return result


def _name_matches(a: dict[str, Any], q: str) -> bool:
    """精確或 substring 比對 name / nickname（不含 dir）。"""
    name = a["name"].lower()
    nick = (a["nickname"] or "").lower()
    return q == name or q == nick or q in name or bool(nick and q in nick)


def find_all_agents(query: str) -> list[dict[str, Any]]:
    """回傳所有符合 query 的 agent dict（含 scope 欄位），local 排前面。

    比對：精確 dir 名、精確/substring name 或 nickname。
    """
    q = query.strip().lower()
    return [a for a in list_agents() if a["dir"].lower() == q or _name_matches(a, q)]


def find_agent(query: str) -> str | None:
    """模糊比對 agent，回傳 "local:<dir>" 或 "global:<dir>"；找不到回傳 None。

    比對優先順序：
      1. 精確 dir 名
      2. 精確 name 或 nickname
      3. name / nickname 包含 query（substring）
      4. query 包含 name / nickname
      5. description 包含 query
    同優先級內 local 優先。
    """
    q = query.strip().lower()
    agents = list_agents()

    checks = [
        lambda a: a["dir"].lower() == q,
        lambda a: a["name"].lower() == q or (a["nickname"] or "").lower() == q,
        lambda a: q in a["name"].lower() or bool(a["nickname"] and q in a["nickname"].lower()),
        lambda a: a["name"].lower() in q or bool(a["nickname"] and a["nickname"].lower() in q),
        lambda a: bool(a.get("description") and q in a["description"].lower()),
    ]
    for check in checks:
        for scope in ("local", "global"):
            for a in agents:
                if a["scope"] == scope and check(a):
                    return f"{scope}:{a['dir']}"
    return None


def load_agent(name: str) -> tuple[dict[str, Any], Path]:
    """載入 agent config。

    name 可為：
    - "local:<dir>"  / "global:<dir>"  — 指定 scope
    - 純 dir 名稱 — 搜尋 local 後再 global
    """
    scope: str | None = None
    if name.startswith("local:"):
        scope, name = "local", name[6:]
    elif name.startswith("global:"):
        scope, name = "global", name[7:]

    bases: list[Path] = []
    if scope == "local" or scope is None:
        bases.append(Path.cwd() / "agents")
    if scope == "global" or scope is None:
        bases.append(GLOBAL_AGENTS_DIR)

    for base in bases:
        for candidate in (name, _slugify(name), name.lower().replace(" ", "-")):
            p = base / candidate / "config.json"
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8")), p.parent

    raise FileNotFoundError(
        f"找不到 agent：{name}（搜尋：{[str(b) for b in bases]}）"
    )


def save_agent(data: dict[str, Any], global_scope: bool = False) -> Path:
    """儲存 agent config；首次建立時同步寫入初始 history.json。"""
    d = agent_dir(data["name"], global_scope=global_scope)
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


def delete_agent(scoped_name: str) -> str:
    """刪除 agent 目錄（含 config.json / history.json），回傳 agent 顯示名稱。"""
    import shutil
    _, adir = load_agent(scoped_name)
    try:
        display_name = json.loads((adir / "config.json").read_text(encoding="utf-8")).get("name", adir.name)
    except Exception:
        display_name = adir.name
    shutil.rmtree(adir)
    return display_name


def validate_agent(data: dict[str, Any]) -> list[str]:
    """回傳缺少的必要欄位清單。"""
    return [f for f in REQUIRED_FIELDS if not data.get(f)]


def extract_json(text: str) -> dict[str, Any]:
    """從 LLM 回覆中提取 JSON（處理 markdown code block）。"""
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1)
    return json.loads(text.strip())
