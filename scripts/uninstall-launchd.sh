#!/usr/bin/env bash
# Stop & remove the SolScout launchd agent.
set -euo pipefail
LABEL="com.solscout.bot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "✓ service '$LABEL' stopped & removed"
