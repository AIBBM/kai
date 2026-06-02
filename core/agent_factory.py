"""Agent Factory — /create-agent, /load-agent, /list-agents 的核心邏輯。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = {"name", "model", "system_prompt"}

GLOBAL_AGENTS_DIR = Path.home() / ".kai" / "agents"

_CONDITIONAL_FIELDS = """\
條件欄位（符合才加入，否則省略）：
- 若用途涉及查詢知識庫、文件庫、RAG、內部資料：
    加入 "datasets": []
    system_prompt 內加入："## 可用知識庫\n\n{{DIFY_DATASETS}}\n"（系統啟動時自動替換為 KB 清單）
    system_prompt 說明必須優先用 dify_retrieve 工具查詢、回答引用來源文件
- 若用途涉及語音輸出、朗讀、TTS：
    加入 "tts": {{"engine": "edge-tts", "voice": "zh-TW-HsiaoChenNeural", "player": "ffplay", "speed": 1.0}}
    中文女聲：zh-TW-HsiaoChenNeural，中文男聲：zh-TW-YunJheNeural，英文：en-US-AriaNeural
"""

DESIGN_SYSTEM_PROMPT = """\
你是一個 AI agent 設計師。根據以下資訊，為 agent 設計 system prompt 和開場白。

Agent 名稱：{{name}}
用途描述：{{description}}

請輸出 JSON（不要加任何說明文字，只輸出 JSON）：

{{
  "system_prompt": "完整的 system prompt，包含角色定義、專業知識、行為規範、回覆風格",
  "greeting": "開場白觸發 prompt"
}}

設計原則：
- system_prompt 要具體、有深度，包含專業術語和邊界條件
- system_prompt 需涵蓋角色定義、核心能力、回答風格、限制範圍

""" + _CONDITIONAL_FIELDS

_DESIGN_BASE = """\
你是一個 AI agent 設計師。根據用戶的描述，設計一個完整的 AI agent 設定。

用戶描述：{description}

可用模型：{models}

請輸出 JSON（不要加任何說明文字，只輸出 JSON），格式如下：

{{
  "name": "agent 的顯示名稱（中文可以）",
  "nickname": "簡短暱稱（2-4字，用於 UI 顯示）",
  "model": "選擇最適合的模型名稱",
  "system_prompt": "完整的 system prompt，包含角色定義、專業知識、行為規範、回覆風格",
  "greeting": "開場白觸發 prompt",
  "description": "一句話說明這個 agent 的用途",
  "mcp_servers": {{}},
  "history_keep": 15
}}

設計原則：
- system_prompt 要具體、有深度，包含專業術語和邊界條件
- nickname 要貼切且好記
- model 選擇依任務性質：需要思考選 gemini-2.5-flash，純對話選 deepseek-v4-flash，需視覺選 qwen3-vl:4b
- mcp_servers 保持空物件（用戶可自行擴充）
- history_keep 依任務複雜度調整（10-30）

""" + _CONDITIONAL_FIELDS

DESIGN_FULL_PROMPT = _DESIGN_BASE


async def _call_backend(design_model: str, messages: list[dict]) -> str:
    """直接呼叫 ollama / openai-compat backend，回傳原始文字。"""
    import core.config as _cfg
    from core.models import get_profile
    profile = get_profile(design_model)
    if profile.backend == "ollama":
        import ollama
        resp = await ollama.AsyncClient().chat(model=profile.name, messages=messages)
        return resp.message.content or ""
    from openai import AsyncOpenAI
    if profile.backend == "gemini":
        client = AsyncOpenAI(
            api_key=_cfg.GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    elif profile.backend == "deepseek":
        client = AsyncOpenAI(api_key=_cfg.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    else:
        raise ValueError(f"未知 backend：{profile.backend!r}，請確認 REGISTRY 設定")
    resp = await client.chat.completions.create(model=profile.name, messages=messages)
    return resp.choices[0].message.content or ""


async def call_full_design(description: str) -> dict[str, Any]:
    """用 CREATE_AGENT_MODEL 從自由文字描述生成完整 agent config dict。"""
    import core.config as _cfg
    from core.models import REGISTRY
    design_model = _cfg.CREATE_AGENT_MODEL or _cfg.MODEL
    model_list = ", ".join(REGISTRY.keys())
    prompt = _DESIGN_BASE.format(description=description, models=model_list)
    raw = await _call_backend(design_model, [{"role": "user", "content": prompt}])
    try:
        return extract_json(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"設計模型回傳非 JSON 格式：{e}\n原始回覆：{raw[:300]}") from e


async def call_design_model(name: str, description: str) -> tuple[str, str, dict]:
    """用 CREATE_AGENT_MODEL 生成 agent 設定，回傳 (system_prompt, greeting, extras)。
    extras 可能包含 datasets、tts 等 LLM 自行判斷需要的條件欄位。
    """
    import core.config as _cfg
    design_model = _cfg.CREATE_AGENT_MODEL or _cfg.MODEL
    prompt = DESIGN_SYSTEM_PROMPT.format(name=name, description=description or name)
    raw = await _call_backend(design_model, [{"role": "user", "content": prompt}])
    try:
        data = extract_json(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"設計模型回傳非 JSON 格式：{e}\n原始回覆：{raw[:300]}") from e
    extras = {k: data[k] for k in ("datasets", "tts") if k in data}
    return data.get("system_prompt", ""), data.get("greeting", ""), extras


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


def _escape_json_strings(text: str) -> str:
    """將 JSON 字串值內的 literal 控制字元轉為轉義序列。"""
    result: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            result.append(ch)
            escaped = False
        elif ch == "\\" and in_string:
            result.append(ch)
            escaped = True
        elif ch == '"':
            result.append(ch)
            in_string = not in_string
        elif in_string and ch == "\n":
            result.append("\\n")
        elif in_string and ch == "\r":
            result.append("\\r")
        elif in_string and ch == "\t":
            result.append("\\t")
        else:
            result.append(ch)
    return "".join(result)


def extract_json(text: str) -> dict[str, Any]:
    """從 LLM 回覆中提取 JSON（處理 markdown code block 與字串內控制字元）。"""
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_escape_json_strings(text))
