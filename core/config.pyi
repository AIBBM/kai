from pathlib import Path

KAI_DIR: Path
HISTORY_FILE: Path
BUNDLED_MCP_FILE: Path

MODEL: str
CREATE_AGENT_MODEL: str
COMPACT_MODEL: str
SEARXNG_URL: str
NUM_CTX: int
MAX_TOOL_CALLS: int
MAX_SEARCH_CHARS: int
SEARCH_NUM_RESULTS: int
SEARCH_TIMEOUT: float
SEARCH_RETRIES: int
HISTORY_KEEP: int
USERNAME: str
BASH_TIMEOUT: int
TEMPERATURE: float
TOP_K: int
TOP_P: float
FETCH_MAX_CHARS: int
OLLAMA_STREAM_TIMEOUT: int
MCP_TOOL_TIMEOUT: float
DEBUG_TOOLS: bool
GEMINI_API_KEY: str
DEEPSEEK_API_KEY: str
JINA_API_KEY: str
AZURE_TTS_KEY: str
AZURE_TTS_REGION: str
AZURE_TTS_ENDPOINT: str
DIFY_API_URL: str
DIFY_API_KEY: str
DIFY_TOP_K: int
DIFY_RERANK_PROVIDER: str
DIFY_RERANK_MODEL: str

def get_nickname(model: str) -> str: ...
def ensure_kai_dir() -> Path: ...
def reload_env() -> None: ...
def load_kai_config() -> dict: ...
def current_kai_config() -> dict: ...
def apply_kai_config(cfg: dict) -> None: ...
