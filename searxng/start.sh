#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# SEARXNG_SECRET：優先用環境變數，否則從專案根目錄的 .env 讀取
SEARXNG_SECRET="${SEARXNG_SECRET:-$(grep -E '^SEARXNG_SECRET=' "${SCRIPT_DIR}/../.env" 2>/dev/null | cut -d= -f2-)}"
if [ -z "$SEARXNG_SECRET" ]; then
  echo "請在 .env 設定 SEARXNG_SECRET（openssl rand -hex 32）" >&2
  exit 1
fi

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
  -e "SEARXNG_SECRET=${SEARXNG_SECRET}" \
  searxng/searxng

echo "SearXNG + Valkey started at http://localhost:8888"
