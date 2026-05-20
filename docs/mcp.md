# MCP 整合指南

kai 透過 [Model Context Protocol (MCP)](https://modelcontextprotocol.io) 讓 AI 模型呼叫外部工具，例如資料庫查詢、瀏覽器操作、檔案系統存取等。

## 設定方式

在專案根目錄建立（或編輯）`mcp_servers.json`，格式與 Claude Desktop 完全相容：

```json
{
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/your/path"]
    }
  }
}
```

重啟 kai 後，sidebar 的「MCP 工具」欄位會顯示已連線的 server 與工具數量。

> 以 `_` 開頭的 server 名稱（如 `_playwright`）會被略過，可用來暫時停用而不刪除設定。

---

## Transport 類型

### stdio（預設）

啟動本地子行程，透過 stdin/stdout 通訊。適合本地工具。

```json
{
  "mcpServers": {
    "my-server": {
      "command": "python",
      "args": ["my_server.py"],
      "env": {
        "MY_ENV_VAR": "value"
      }
    }
  }
}
```

### HTTP（Streamable HTTP）

連線到遠端 MCP server，支援自訂 header（可讀取環境變數）。

```json
{
  "mcpServers": {
    "remote-server": {
      "type": "http",
      "url": "https://api.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${env:MY_API_KEY}"
      }
    }
  }
}
```

`${env:VAR_NAME}` 語法會在啟動時從環境變數展開。

---

## 常用現成 Server

以下 server 可直接加進 `mcp_servers.json` 使用，無需另外安裝（`npx` 會自動下載）：

| Server | 功能 | 範例 args |
|--------|------|-----------|
| `@modelcontextprotocol/server-filesystem` | 讀寫本地檔案系統 | `["-y", "...", "/your/path"]` |
| `@modelcontextprotocol/server-postgres` | PostgreSQL 查詢 | `["-y", "...", "postgresql://user:pass@host/db"]` |
| `@modelcontextprotocol/server-sqlite` | SQLite 查詢 | `["-y", "...", "/path/to/db.sqlite"]` |
| `@modelcontextprotocol/server-git` | Git 操作（log、diff、commit） | `["-y", "...", "--repository", "/repo/path"]` |
| `@modelcontextprotocol/server-github` | GitHub API（PR、issue、search） | 需設定 `GITHUB_TOKEN` env |
| `@playwright/mcp` | 瀏覽器自動化 | `["-y", "@playwright/mcp@latest", "--browser", "chromium"]` |

完整清單：[github.com/modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers)

---

## 本專案的設定範例

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/projects"]
    },
    "RepoMapper": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/repomap_server.py"]
    },
    "context7": {
      "type": "http",
      "url": "https://mcp.context7.com/mcp",
      "headers": {
        "CONTEXT7_API_KEY": "${env:CONTEXT7_API_KEY}"
      }
    },
    "_postgres": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://user:pass@localhost/mydb"]
    }
  }
}
```

---

## 寫自己的 MCP Server

最簡單的 Python MCP server：

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-tools")

@mcp.tool()
def my_tool(query: str) -> str:
    """做某件事並回傳結果。"""
    return f"結果：{query}"

if __name__ == "__main__":
    mcp.run()
```

加進 `mcp_servers.json`：

```json
{
  "mcpServers": {
    "my-tools": {
      "command": "python",
      "args": ["my_server.py"]
    }
  }
}
```

詳細文件：[modelcontextprotocol.io/docs/concepts/servers](https://modelcontextprotocol.io/docs/concepts/servers)
