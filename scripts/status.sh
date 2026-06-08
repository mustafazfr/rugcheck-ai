#!/usr/bin/env bash
# Show whether the background bot/dashboard are running, plus recent log tails.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

check() {
  local name="$1"; local pidfile="logs/$name.pid"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "● $name RUNNING (pid $(cat "$pidfile"))"
  else
    echo "○ $name stopped"
  fi
}

check run
check dashboard
check discover
check caffeinate
echo
echo "── recent bot log (logs/run.log) ──"
[[ -f logs/run.log ]] && tail -n 12 logs/run.log || echo "(no log yet)"
