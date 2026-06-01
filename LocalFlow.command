#!/bin/bash
# LocalFlow Launcher — runs under Terminal.app's TCC permissions
# Double-click this file to start LocalFlow

# Kill any existing instance
pkill -f "localflow_app.py" 2>/dev/null

cd "$(dirname "$0")"
source venv/bin/activate
python3 localflow_app.py > /tmp/localflow.log 2>&1 &

echo "✅ LocalFlow started!"
echo "🎙️ Look for the microphone icon in your menu bar."
sleep 2
