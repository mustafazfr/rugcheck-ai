#!/usr/bin/env bash
# Start the SolScout bot loop + dashboard in the background. Logs/PIDs under ./logs.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"
mkdir -p logs
export PYTHONUNBUFFERED=1   # live log output (else stdout is block-buffered and logs look "stuck")

# ensure Ollama is running — the per-coin AI analysis (Qwen) needs it
if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "· starting Ollama (for AI analysis)…"
  nohup ollama serve >> logs/ollama.log 2>&1 &
  for i in $(seq 1 15); do curl -s http://localhost:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
fi
curl -s http://localhost:11434/api/tags >/dev/null 2>&1 && echo "✓ Ollama up (AI analysis on)" || echo "⚠ Ollama down — AI analysis will be skipped"

start() { # name, cmd...
  local name="$1"; shift
  local pidfile="logs/$name.pid"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "• $name already running (pid $(cat "$pidfile"))"; return
  fi
  pkill -f "solscout $name" 2>/dev/null && sleep 1 || true   # clear any orphan holding the port
  nohup "$@" >> "logs/$name.log" 2>&1 &
  echo $! > "$pidfile"
  echo "✓ started $name (pid $!) → logs/$name.log"
}

start run  uv run solscout run
start dashboard  uv run solscout dashboard
start discover  uv run solscout discover   # free, on-chain recurring-holder smart-money discovery (ADR-036)

# Keep the Mac awake while the bot runs so it doesn't pause when you walk away (no idle/disk/system sleep).
# Tied to the run process's lifetime via -w, so it stops itself when the bot stops. NOTE: this can't beat
# lid-close sleep on battery — to run unattended, leave the lid OPEN (plugged in is best).
if [[ -f logs/caffeinate.pid ]] && kill -0 "$(cat logs/caffeinate.pid 2>/dev/null)" 2>/dev/null; then
  echo "• caffeinate already on (pid $(cat logs/caffeinate.pid))"
else
  RUNPID="$(cat logs/run.pid 2>/dev/null || true)"
  if [[ -n "$RUNPID" ]] && command -v caffeinate >/dev/null 2>&1; then
    nohup caffeinate -ims -w "$RUNPID" >/dev/null 2>&1 &
    echo $! > logs/caffeinate.pid
    echo "✓ caffeinate on (pid $!) — Mac won't idle-sleep while the bot runs"
  fi
fi

echo
echo "Bot + dashboard + discovery running (PAPER) — free/keyless (GeckoTerminal + DexScreener + Helius)."
echo "  http://localhost:8787      make status   make logs   make down"
echo "· quality-tier paper BUYs start immediately; smart-tier kicks in as discover fills the watchlist."
echo "· Mac kept awake (caffeinate). For unattended runs keep the lid OPEN + plugged in."
