#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 建立專用 network（已存在就略過）
docker network create searxng-net 2>/dev/null || true

# 移除舊容器（如果存在）
docker rm -f searxng valkey-searxng 2>/dev/null && echo "Removed old containers" || true

# 啟動 Valkey（Redis 相容），僅供 SearXNG 內部使用，不對外暴露 port
docker run -d \
  --name valkey-searxng \
  --network searxng-net \
  valkey/valkey:8-alpine

# 啟動 SearXNG，settings.yml 以 read-only 掛載，防止容器改改檔案權限
docker run -d \
  --name searxng \
  --network searxng-net \
  -p 8888:8080 \
  -v "${SCRIPT_DIR}/settings.yml:/etc/searxng/settings.yml:ro" \
  -e "BASE_URL=http://localhost:8888/" \
  searxng/searxng

echo "SearXNG + Valkey started at http://localhost:8888"
