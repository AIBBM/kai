#!/bin/bash
# 錄製 demo GIF：重建 demo 目錄 → vhs → 無論成敗都清理 vhs 啟動的程序
set -u
cd "$(dirname "$0")/.."

cleanup() {
  pkill -f "app.py /tmp/kai-demo" 2>/dev/null
  pkill -x ttyd 2>/dev/null
  pkill -f "\.cache/rod/browser" 2>/dev/null
}
trap cleanup EXIT

# 記憶體保護：可用記憶體低於門檻就中止錄影（vhs 的 Chromium 與 GIF 轉檔很吃記憶體）
MIN_AVAILABLE_MB="${MIN_AVAILABLE_MB:-800}"
watchdog() {
  while sleep 2; do
    avail=$(awk '/MemAvailable/ {print int($2 / 1024)}' /proc/meminfo)
    if [ "$avail" -lt "$MIN_AVAILABLE_MB" ]; then
      echo "可用記憶體剩 ${avail}MB，中止錄影" >&2
      pkill -x vhs
      cleanup
      return
    fi
  done
}

demo/setup.sh
mkdir -p demo/out
watchdog &
WATCHDOG_PID=$!
timeout "${RECORD_TIMEOUT:-600}" vhs demo/hero.tape
status=$?
kill "$WATCHDOG_PID" 2>/dev/null
exit "$status"
