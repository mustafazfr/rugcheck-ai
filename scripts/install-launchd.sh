#!/usr/bin/env bash
# Install a macOS launchd agent that keeps `solscout run` alive 24/7 (restarts on crash, starts at login).
# Paper mode — safe. Live trading stays OFF (both flags required + LiveExecutor is locked).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.solscout.bot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UV="$(command -v uv || true)"
[[ -z "$UV" ]] && { echo "uv not found on PATH"; exit 1; }
mkdir -p "$DIR/logs" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
    <array><string>$UV</string><string>run</string><string>solscout</string><string>run</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$(dirname "$UV"):/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/logs/service.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/service.log</string>
</dict></plist>
EOF

echo "wrote $PLIST"
# modern (bootstrap) with fallback to legacy (load)
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"
echo "✓ service '$LABEL' installed & started (paper). Logs: logs/service.log"
echo "  dashboard: run \`make dash\` or \`solscout dashboard\` separately."
echo "  stop/remove: make uninstall-service"
