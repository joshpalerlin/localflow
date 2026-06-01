#!/usr/bin/env bash
# LocalFlow — Models Restore Script
#
# Use this if LocalFlow's models folder gets wiped or corrupted.
# Forces a fresh download into ~/localflow/models/hub/ via the running app.
#
# Usage:
#   ~/localflow/restore-models.sh
#
# What it does:
#   1. Stops LocalFlow (graceful shutdown via launchctl bootout)
#   2. Restarts LocalFlow (launchctl bootstrap)
#   3. The app boots, sees models missing, downloads them from HuggingFace
#   4. Tails the log so you can watch the progress
#
# To monitor download progress: tail -f /tmp/localflow.log

set -e

APP_DIR="$HOME/localflow"
MODELS_DIR="$APP_DIR/models"
PLIST="$HOME/Library/LaunchAgents/com.localflow.app.plist"
UID_NUM=$(id -u)

echo "🔄 LocalFlow Models Restore"
echo "   Models dir: $MODELS_DIR"
echo ""

mkdir -p "$MODELS_DIR"

echo "→ Stopping LocalFlow..."
launchctl bootout gui/"$UID_NUM" "$PLIST" 2>/dev/null || true
sleep 2

echo "→ Restarting LocalFlow..."
launchctl bootstrap gui/"$UID_NUM" "$PLIST"

echo ""
echo "✅ LocalFlow restarted. Watch download progress with:"
echo "   tail -f /tmp/localflow.log"
echo ""
echo "Models will be ready when you see: ✅ Models ready!"
