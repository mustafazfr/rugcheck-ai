#!/usr/bin/env bash
# Stop the rugcheck.ai web app.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"
if [[ -f logs/web.pid ]] && kill -0 "$(cat logs/web.pid 2>/dev/null)" 2>/dev/null; then
  kill "$(cat logs/web.pid)" 2>/dev/null || true
  echo "✓ stopped rugcheck.ai (pid $(cat logs/web.pid))"
else
  echo "• rugcheck.ai not running"
fi
rm -f logs/web.pid
pkill -f "uvicorn solscout.web.api" 2>/dev/null && echo "  (swept orphan uvicorn)" || true
