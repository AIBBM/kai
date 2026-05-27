import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# kai 資料目錄（相對 cwd，跟著 os.chdir 變）
KAI_DIR = Path(".kai")
# kai package 目錄（全域，不跟著 cwd 走）
_KAI_PKG_DIR = Path(__file__).parent.parent

MODEL = os.getenv("MODEL", "qwen3-vl:4b")  # 使用的 Ollama 模型
CREATE_AGENT_MODEL = os.getenv("CREATE_AGENT_MODEL", "")  # /create-agent 設計用 model（空字串 = 同 MODEL）
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8888/search")  # SearXNG API
NUM_CTX = int(os.getenv("NUM_CTX", "0"))  # context window 大小，0 表示使用模型預設
MAX_TOOL_CALLS = int(os.getenv("MAX_TOOL_CALLS", "15"))  # 單輪工具呼叫上限
MAX_SEARCH_CHARS = int(os.getenv("MAX_SEARCH_CHARS", "8000"))
SEARCH_NUM_RESULTS = int(os.getenv("SEARCH_NUM_RESULTS", "5"))  # 搜尋結果字元上限
SEARCH_TIMEOUT = float(os.getenv("SEARCH_TIMEOUT", "8.0"))  # 搜尋逾時秒數
SEARCH_RETRIES = int(os.getenv("SEARCH_RETRIES", "2"))  # 搜尋重試次數
HISTORY_KEEP = int(os.getenv("HISTORY_KEEP", "10"))  # 對話歷史保留則數
HISTORY_FILE = Path(os.getenv("HISTORY_FILE", str(KAI_DIR / "history.json")))  # 歷史儲存路徑
USERNAME = os.getenv("USERNAME", "你")  # 提示符顯示名稱
# bundled mcp_servers.json（隨 package 走），僅供首次啟動複製到 ~/.kai/ 用
BUNDLED_MCP_FILE = _KAI_PKG_DIR / "mcp_servers.json"
BASH_TIMEOUT = int(os.getenv("BASH_TIMEOUT", "30"))  # bash_exec 預設超時秒數
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.6"))  # 生成溫度（0=確定性，1=創意）
TOP_K = int(os.getenv("TOP_K", "20"))                 # top-k 採樣（0=停用）
TOP_P = float(os.getenv("TOP_P", "0.95"))             # nucleus sampling
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")      # Google Gemini API key
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")  # DeepSeek API key
JINA_API_KEY = os.getenv("JINA_API_KEY", "")          # Jina Reader API key（可選，有 key 額度更高）
FETCH_MAX_CHARS = int(os.getenv("FETCH_MAX_CHARS", "12000"))  # fetch_url 回傳字元上限
OLLAMA_STREAM_TIMEOUT = int(os.getenv("OLLAMA_STREAM_TIMEOUT", "120"))  # Ollama 每個 chunk 最長等待秒數
MCP_TOOL_TIMEOUT = float(os.getenv("MCP_TOOL_TIMEOUT", "60.0"))        # MCP 單次工具呼叫逾時秒數
DEBUG_TOOLS = os.getenv("DEBUG_TOOLS", "0").strip().lower() in ("1", "true", "yes")  # 啟動時開啟工具除錯模式


def _normalize(model: str) -> str:
    """qwen3-vl:4b → qwen3_vl_4b（供 NICKNAME_ 查找用）"""
    import re
    return re.sub(r"[^a-zA-Z0-9]", "_", model)


def get_nickname(model: str) -> str:
    """回傳 .env 設定的 NICKNAME_<model>；若無則回傳原名。"""
    return os.getenv(f"NICKNAME_{_normalize(model)}", model)


def ensure_kai_dir() -> Path:
    """確保 cwd/.kai/ 存在，回傳路徑。每次 os.chdir 後需重新呼叫。"""
    d = Path(".kai")
    d.mkdir(exist_ok=True)
    return d


def load_kai_config() -> dict:
    """讀取 ~/.kai/config.json（全域）+ cwd/.kai/config.json（專案），shallow merge。
    專案設定優先；兩者皆不存在時回傳 {}。
    """
    def _read(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    global_cfg = _read(Path.home() / ".kai" / "config.json")
    local_cfg = _read(Path(".kai") / "config.json")
    return {**global_cfg, **local_cfg}


def current_kai_config() -> dict:
    """回傳目前所有生效的 config 值（可寫入 config.json 的 key）。"""
    g = globals()
    return {
        "model":                  g["MODEL"],
        "create_agent_model":     g["CREATE_AGENT_MODEL"],
        "history_keep":           g["HISTORY_KEEP"],
        "debug_tools":            g["DEBUG_TOOLS"],
        "temperature":            g["TEMPERATURE"],
        "num_ctx":                g["NUM_CTX"],
        "top_k":                  g["TOP_K"],
        "top_p":                  g["TOP_P"],
        "max_tool_calls":         g["MAX_TOOL_CALLS"],
        "bash_timeout":           g["BASH_TIMEOUT"],
        "username":               g["USERNAME"],
        "searxng_url":            g["SEARXNG_URL"],
        "fetch_max_chars":        g["FETCH_MAX_CHARS"],
        "max_search_chars":       g["MAX_SEARCH_CHARS"],
        "search_num_results":     g["SEARCH_NUM_RESULTS"],
        "search_timeout":         g["SEARCH_TIMEOUT"],
        "search_retries":         g["SEARCH_RETRIES"],
        "ollama_stream_timeout":  g["OLLAMA_STREAM_TIMEOUT"],
        "mcp_tool_timeout":       g["MCP_TOOL_TIMEOUT"],
    }


def apply_kai_config(cfg: dict) -> None:
    """將 config dict 套用到 module globals。API key 類不支援（留在 .env）。"""
    g = globals()
    _STR   = {"model": "MODEL", "create_agent_model": "CREATE_AGENT_MODEL",
              "searxng_url": "SEARXNG_URL", "username": "USERNAME"}
    _INT   = {"history_keep": "HISTORY_KEEP", "num_ctx": "NUM_CTX",
              "max_tool_calls": "MAX_TOOL_CALLS", "max_search_chars": "MAX_SEARCH_CHARS",
              "search_num_results": "SEARCH_NUM_RESULTS", "search_retries": "SEARCH_RETRIES",
              "bash_timeout": "BASH_TIMEOUT", "fetch_max_chars": "FETCH_MAX_CHARS",
              "ollama_stream_timeout": "OLLAMA_STREAM_TIMEOUT"}
    _FLOAT = {"temperature": "TEMPERATURE", "top_k": "TOP_K", "top_p": "TOP_P",
              "search_timeout": "SEARCH_TIMEOUT", "mcp_tool_timeout": "MCP_TOOL_TIMEOUT"}
    _BOOL  = {"debug_tools": "DEBUG_TOOLS"}
    for key, attr in _STR.items():
        if key in cfg:
            g[attr] = str(cfg[key])
    for key, attr in _INT.items():
        if key in cfg:
            g[attr] = int(cfg[key])
    for key, attr in _FLOAT.items():
        if key in cfg:
            g[attr] = float(cfg[key])
    for key, attr in _BOOL.items():
        if key in cfg:
            v = cfg[key]
            g[attr] = bool(v) if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")

# 只輸出 DEBUG 以上的 log 到 stderr，不干擾 stdout 的對話輸出
logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <5}</level> | {message}",
)
