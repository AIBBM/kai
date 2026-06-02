"""Backward-compat shim — 實際內容已移入 core/tools/。"""
from .tools import *  # noqa: F401, F403
from .tools import (
    BASH_TOOL,
    CLEAN_PATTERN,
    DIFY_RETRIEVE_TOOL,
    FETCH_URLS_TOOL,
    OPEN_AGENT_TOOL,
    SEARCH_TOOL,
    WebSearchArgs,
    bash_exec,
    clean_thinking,
    dify_retrieve,
    fetch_dify_datasets,
    fetch_url,
    fetch_urls,
    run_tool,
    web_search,
)

if __name__ == "__main__":
    import asyncio
    import sys
    query = sys.argv[1] if len(sys.argv) > 1 else "台北天氣"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    print(asyncio.run(web_search(query, n)))
