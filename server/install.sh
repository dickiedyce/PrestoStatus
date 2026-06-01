#!/usr/bin/env bash
set -euo pipefail

PLIST_NAME="com.prestostatus.server"
PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/${PLIST_NAME}.plist"
PLIST_DST="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

echo "PrestoStatus server installer"
echo ""

# Resolve the WorkingDirectory to the actual server/ path
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Copy plist
cp "$PLIST_SRC" "$PLIST_DST"

# Patch WorkingDirectory to absolute path
if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "s|<string>.*server</string>|<string>${SCRIPT_DIR}</string>|" "$PLIST_DST"
fi

echo "  Installed plist to $PLIST_DST"
echo ""

# Unload any existing instance
launchctl bootout "gui/$(id -u)/${PLIST_NAME}" 2>/dev/null || true

# Load the new one
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"

echo "  Launch agent started."
echo ""
echo "Check logs:   tail -f /tmp/prestostatus.stderr.log"
echo "Stop service: launchctl bootout gui/$(id -u)/${PLIST_NAME}"
echo ""
echo "NOTE: Ensure your terminal app has Full Disk Access in System Settings."
