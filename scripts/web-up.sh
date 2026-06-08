#!/usr/bin/env bash
# Start the rugcheck.ai web app in the background and open it. Logs/PIDs under ./logs.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"
mkdir -p logs
export PYTHONUNBUFFERED=1
PORT="${PORT:-8000}"

# Ollama powers the AI analyst note — start it if present (optional; the app degrades without it).
if command -v ollama >/dev/null 2>&1 && ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
  nohup ollama serve >> logs/ollama.log 2>&1 &
  for i in $(seq 1 12); do curl -s http://localhost:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
fi

if [[ -f logs/web.pid ]] && kill -0 "$(cat logs/web.pid 2>/dev/null)" 2>/dev/null; then
  echo "• rugcheck.ai already running (pid $(cat logs/web.pid)) → http://127.0.0.1:$PORT"
else
  pkill -f "uvicorn solscout.web.api" 2>/dev/null && sleep 1 || true
  nohup uv run uvicorn solscout.web.api:app --host 127.0.0.1 --port "$PORT" >> logs/web.log 2>&1 &
  echo $! > logs/web.pid
  echo "✓ started rugcheck.ai (pid $!) → logs/web.log"
fi

# wait until it answers, then open the browser
for i in $(seq 1 20); do curl -s -o /dev/null "http://127.0.0.1:$PORT/api/health" 2>/dev/null && break; sleep 1; done
command -v open >/dev/null 2>&1 && open "http://127.0.0.1:$PORT" >/dev/null 2>&1 || true
echo
echo "rugcheck.ai running → http://127.0.0.1:$PORT   (stop: ./scripts/web-down.sh)"
