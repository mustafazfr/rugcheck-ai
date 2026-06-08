#!/usr/bin/env bash
# (Re)generate the double-click desktop shortcuts for rugcheck.ai. Idempotent — re-run after moving the repo.
# Shortcuts are THIN wrappers around scripts/ (logic lives in the repo, not the .command files).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# macOS is case-INSENSITIVE, so `pwd` can echo the wrong case (/desktop vs /Desktop). Re-derive the TRUE
# on-disk case by matching each segment against real dir entries, so the baked path is clean.
DIR="$(python3 - "$DIR" <<'PY' 2>/dev/null || echo "$DIR"
import os, sys
cur = os.sep
for seg in os.path.abspath(sys.argv[1]).split(os.sep)[1:]:
    try:
        seg = next((e for e in os.listdir(cur) if e.lower() == seg.lower()), seg)
    except OSError:
        pass
    cur = os.path.join(cur, seg)
print(cur)
PY
)"
DEST="${1:-$HOME/Desktop}"
mkdir -p "$DEST"

# remove the old trading-era shortcuts (the project pivoted to the web product)
for old in "Start SolScout" "Stop SolScout" "SolScout Durum" "SolScout Panel"; do rm -f "$DEST/$old.command"; done

read -r -d '' HEADER <<'EOF' || true
#!/bin/bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
DIR="__REPO__"
cd "$DIR" || { echo "rugcheck.ai klasörü bulunamadı: $DIR"; read -n1 -s; exit 1; }
EOF

write() { # filename, body
  local path="$DEST/$1"
  { printf '%s\n\n' "$HEADER"; printf '%s\n' "$2"; } | sed "s#__REPO__#$DIR#g" > "$path"
  chmod +x "$path"
  echo "✓ $path"
}

write "Start rugcheck.ai.command" '
clear
echo "▶  rugcheck.ai başlatılıyor…"
echo "   (ücretsiz · on-chain Solana token forensics)"
echo
./scripts/web-up.sh
echo
echo "Tarayıcıda açıldı: http://127.0.0.1:8000"
echo "Bir token mint adresi yapıştır → tam güvenlik raporu."
echo "Bu pencereyi kapatabilirsin — sunucu arka planda çalışır."
echo "(Durdurmak: «Stop rugcheck.ai»)"
'

write "Stop rugcheck.ai.command" '
clear
echo "■  rugcheck.ai durduruluyor…"
./scripts/web-down.sh
echo
echo "✅ Durdu. Bu pencereyi kapatabilirsin."
'

echo
echo "Masaüstü kısayolları güncellendi → $DEST"
echo "Çift tıkla: «Start rugcheck.ai» · «Stop rugcheck.ai»"
