#!/usr/bin/env bash
# Stop the background SolScout bot loop + dashboard.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

stop() {
  local name="$1"; local pidfile="logs/$name.pid"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    kill "$(cat "$pidfile")" 2>/dev/null || true
    echo "✓ stopped $name (pid $(cat "$pidfile"))"
  else
    echo "• $name not running"
  fi
  rm -f "$pidfile"
}

stop run
stop dashboard
stop discover
stop caffeinate   # release the sleep-preventer (it also self-exits when run dies via -w)

# safety net: sweep any orphaned processes whose pidfile was lost (e.g. crash / port collision)
for name in run dashboard discover; do
  pkill -f "solscout $name" 2>/dev/null && echo "  (swept orphan solscout $name)" || true
done
pkill -f "caffeinate -ims -w" 2>/dev/null || true
