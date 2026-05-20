import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

MODEL = os.getenv("MODEL", "qwen3-vl:4b")  # 使用的 Ollama 模型
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8888/search")  # SearXNG API
NUM_CTX = int(os.getenv("NUM_CTX", "0"))  # context window 大小，0 表示使用模型預設
MAX_TOOL_CALLS = int(os.getenv("MAX_TOOL_CALLS", "15"))  # 單輪工具呼叫上限
MAX_SEARCH_CHARS = int(os.getenv("MAX_SEARCH_CHARS", "8000"))
SEARCH_NUM_RESULTS = int(os.getenv("SEARCH_NUM_RESULTS", "5"))  # 搜尋結果字元上限
SEARCH_TIMEOUT = float(os.getenv("SEARCH_TIMEOUT", "8.0"))  # 搜尋逾時秒數
SEARCH_RETRIES = int(os.getenv("SEARCH_RETRIES", "2"))  # 搜尋重試次數
HISTORY_KEEP = int(os.getenv("HISTORY_KEEP", "10"))  # 對話歷史保留則數
HISTORY_FILE = Path(os.getenv("HISTORY_FILE", "history.json"))  # 歷史儲存路徑
USERNAME = os.getenv("USERNAME", "你")  # 提示符顯示名稱
MCP_SERVERS_FILE = Path(os.getenv("MCP_SERVERS_FILE", "mcp_servers.json"))  # MCP server 設定檔
BASH_TIMEOUT = int(os.getenv("BASH_TIMEOUT", "30"))  # bash_exec 預設超時秒數
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.6"))  # 生成溫度（0=確定性，1=創意）
TOP_K = int(os.getenv("TOP_K", "20"))                 # top-k 採樣（0=停用）
TOP_P = float(os.getenv("TOP_P", "0.95"))             # nucleus sampling
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")      # Google Gemini API key
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")  # DeepSeek API key
JINA_API_KEY = os.getenv("JINA_API_KEY", "")          # Jina Reader API key（可選，有 key 額度更高）
FETCH_MAX_CHARS = int(os.getenv("FETCH_MAX_CHARS", "12000"))  # fetch_url 回傳字元上限
OLLAMA_STREAM_TIMEOUT = int(os.getenv("OLLAMA_STREAM_TIMEOUT", "120"))  # Ollama 每個 chunk 最長等待秒數
DEBUG_TOOLS = os.getenv("DEBUG_TOOLS", "0").strip().lower() in ("1", "true", "yes")  # 啟動時開啟工具除錯模式

# 只輸出 DEBUG 以上的 log 到 stderr，不干擾 stdout 的對話輸出
logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <5}</level> | {message}",
)
