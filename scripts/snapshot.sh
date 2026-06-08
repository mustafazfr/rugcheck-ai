#!/usr/bin/env bash
# A fast, browser-free "how is the bot doing?" glance. Read-only: pidfiles + sqlite3 (no Python/uv startup).
# Shared by the desktop shortcuts and `make glance`.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"
DB="${SOLSCOUT_DB:-solscout.db}"

dot() { # name -> "● RUNNING" / "○ stopped"
  local pidfile="logs/$1.pid"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; then
    printf "\033[32m●\033[0m %s" "$2"
  else
    printf "\033[90m○\033[0m %s" "$2"
  fi
}

q() { sqlite3 "$DB" "$1" 2>/dev/null; }

echo "════════════════════════════════════════════════"
echo "   📡  SolScout — durum (PAPER)"
echo "════════════════════════════════════════════════"
printf "  %s   %s   %s   %s\n\n" "$(dot run Bot)" "$(dot dashboard Panel)" "$(dot discover Keşif)" "$(dot caffeinate Uyanık)"

if [[ ! -f "$DB" ]]; then
  echo "  Henüz veri yok. 'Start SolScout' ile başlat."
  echo "════════════════════════════════════════════════"
  exit 0
fi

open_n=$(q "SELECT COUNT(*) FROM positions WHERE closed=0")
open_sol=$(q "SELECT printf('%.3f',COALESCE(SUM(sol_invested),0)) FROM positions WHERE closed=0")
pnl_today=$(q "SELECT printf('%+.4f',COALESCE(SUM(realized_pnl_sol),0)) FROM positions WHERE closed=1 AND closed_at>=date('now')")
closed=$(q "SELECT COUNT(*) FROM positions WHERE closed=1")
wins=$(q "SELECT COALESCE(SUM(realized_pnl_sol>0),0) FROM positions WHERE closed=1")
wl=$(q "SELECT COUNT(*) FROM wallets_watchlist")
links=$(q "SELECT COUNT(*) FROM wallet_winners")
queue=$(q "SELECT COUNT(*) FROM reeval_queue")
buys=$(q "SELECT COUNT(*) FROM decisions WHERE verdict='BUY'")
buy_smart=$(q "SELECT COUNT(*) FROM decisions WHERE verdict='BUY' AND tier='smart'")
watch=$(q "SELECT COUNT(*) FROM decisions WHERE verdict='WATCH'")
reject=$(q "SELECT COUNT(*) FROM decisions WHERE verdict='REJECT'")
credits=$(q "SELECT printf('%.0f',COALESCE(credits,0)) FROM credit_usage WHERE month=strftime('%Y-%m','now')")
# recent BUYs: symbol (from candidate raw_meta if present) + score
last_buys=$(q "SELECT COALESCE(json_extract(c.raw_meta,'\$.symbol'), substr(d.mint,1,5))||'('||CAST(round(d.composite_score) AS INT)||')'
               FROM decisions d LEFT JOIN candidates c ON c.mint=d.mint
               WHERE d.verdict='BUY' ORDER BY d.id DESC LIMIT 4" | paste -sd' ' -)

printf "  Açık pozisyon : %s  (%s SOL)\n" "${open_n:-0}" "${open_sol:-0.000}"
printf "  Bugünkü PnL   : %s SOL   (kapanan %s: %sK)\n" "${pnl_today:-+0.0000}" "${closed:-0}" "${wins:-0}"
printf "  Watchlist     : %s cüzdan   (kazanan-bağı %s)\n" "${wl:-0}" "${links:-0}"
printf "  Kararlar      : BUY %s (akıllı %s) · WATCH %s · REJECT %s\n" "${buys:-0}" "${buy_smart:-0}" "${watch:-0}" "${reject:-0}"
printf "  Sıra / kredi  : %s bekleyen · %s Helius kredi (bu ay)\n" "${queue:-0}" "${credits:-0}"
[[ -n "${last_buys// }" ]] && printf "  Son alımlar   : %s\n" "$last_buys"

# one honest line of guidance based on the actual state
echo "  ──"
if [[ "${wl:-0}" == "0" ]]; then
  echo "  ℹ  Watchlist boş → şimdilik sadece QUALITY-tier alım. 'Keşif' çalışınca dolacak."
elif [[ "${buys:-0}" == "0" ]]; then
  echo "  ℹ  Henüz alım yok — eşikler şu anki adaylar için yüksek olabilir, FUNNEL loguna bak."
else
  echo "  ✓  Bot kendi başına tarıyor, alıyor ve pozisyonları yönetiyor (paper)."
fi
echo "════════════════════════════════════════════════"
