#!/usr/bin/env bash
# LocalFlow — One-command installer for macOS
#
# Usage:
#   ./install.sh                                  (from a cloned repo)
#   curl -fsSL .../install.sh | bash              (one-liner)
#
# What this does:
#   1. Checks you're on Apple Silicon macOS
#   2. Installs Homebrew if missing
#   3. Installs Python 3.14 if missing
#   4. Clones the LocalFlow repo (if running via curl)
#   5. Creates a virtualenv and installs Python dependencies
#   6. Writes a launchd plist so LocalFlow starts on login
#   7. Starts LocalFlow (which downloads models on first run, ~1.1 GB)

set -euo pipefail

LOCALFLOW_REPO="https://github.com/joshpalerlin/localflow.git"
LOCALFLOW_DIR_DEFAULT="$HOME/localflow"
PLIST_LABEL="com.localflow.app"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

# ─── pretty output ──────────────────────────────────────────────────────
bold() { printf "\033[1m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
red() { printf "\033[31m%s\033[0m\n" "$*"; }
step() { printf "\n\033[1;36m→ %s\033[0m\n" "$*"; }

# ─── 1. Platform check ─────────────────────────────────────────────────
step "Checking platform"
if [[ "$(uname)" != "Darwin" ]]; then
    red "❌ LocalFlow requires macOS. Detected: $(uname)"
    exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
    red "❌ LocalFlow requires Apple Silicon (M1/M2/M3/M4)."
    red "   Intel Macs are not supported because MLX requires M-series chips."
    exit 1
fi
green "✓ macOS on Apple Silicon"

# ─── 2. Homebrew ───────────────────────────────────────────────────────
step "Checking Homebrew"
if ! command -v brew &> /dev/null; then
    yellow "Homebrew not found — installing now..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for current shell
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi
green "✓ Homebrew installed"

# ─── 3. Python 3.14 ────────────────────────────────────────────────────
step "Checking Python 3.14"
if ! brew list python@3.14 &> /dev/null; then
    yellow "Python 3.14 not found — installing via Homebrew..."
    brew install python@3.14
fi
PYTHON_BIN="/opt/homebrew/opt/python@3.14/bin/python3.14"
if [[ ! -x "$PYTHON_BIN" ]]; then
    red "❌ Python 3.14 install seems broken. Try: brew reinstall python@3.14"
    exit 1
fi
green "✓ Python 3.14 at $PYTHON_BIN"

# ─── 4. Source repo location ───────────────────────────────────────────
step "Locating LocalFlow source"
# If we're already inside the repo (./install.sh case), use that.
# Otherwise (curl | bash case), clone into ~/localflow.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
if [[ -f "$SCRIPT_DIR/localflow_app.py" ]]; then
    LOCALFLOW_DIR="$SCRIPT_DIR"
    green "✓ Using existing clone at $LOCALFLOW_DIR"
else
    LOCALFLOW_DIR="$LOCALFLOW_DIR_DEFAULT"
    if [[ -d "$LOCALFLOW_DIR/.git" ]]; then
        yellow "Existing clone found at $LOCALFLOW_DIR — pulling latest..."
        git -C "$LOCALFLOW_DIR" pull --ff-only
    else
        yellow "Cloning $LOCALFLOW_REPO → $LOCALFLOW_DIR"
        git clone "$LOCALFLOW_REPO" "$LOCALFLOW_DIR"
    fi
    green "✓ Source at $LOCALFLOW_DIR"
fi
cd "$LOCALFLOW_DIR"

# ─── 5. Virtualenv + dependencies ──────────────────────────────────────
step "Setting up virtualenv"
if [[ ! -d venv ]]; then
    "$PYTHON_BIN" -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
green "✓ venv at $LOCALFLOW_DIR/venv"

step "Installing Python dependencies (this takes a minute)"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
green "✓ Dependencies installed"

# ─── 6. launchd agent ─────────────────────────────────────────────────
step "Installing launchd background agent"
mkdir -p "$(dirname "$PLIST_PATH")"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${LOCALFLOW_DIR}/venv/bin/python3</string>
        <string>${LOCALFLOW_DIR}/localflow_app.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${LOCALFLOW_DIR}</string>

    <key>StandardOutPath</key>
    <string>/tmp/localflow.log</string>

    <key>StandardErrorPath</key>
    <string>/tmp/localflow.log</string>

    <key>RunAtLoad</key>
    <true/>

    <!-- Relaunch automatically if the app crashes or is killed -->
    <key>KeepAlive</key>
    <true/>

    <!-- Wait 10s between relaunch attempts to avoid crash-loop hammering -->
    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>ProcessType</key>
    <string>Interactive</string>

    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
EOF
green "✓ launchd plist installed at $PLIST_PATH"

# ─── 7. Restart LocalFlow ──────────────────────────────────────────────
step "Starting LocalFlow"
UID_NUM=$(id -u)
launchctl bootout "gui/${UID_NUM}" "$PLIST_PATH" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/${UID_NUM}" "$PLIST_PATH"
green "✓ LocalFlow launched"

# ─── 8. Done ───────────────────────────────────────────────────────────
echo ""
bold "✅ Installation complete!"
echo ""
echo "What happens now:"
echo "  1. LocalFlow is running in your menu bar (look top-right for 🎙️)"
echo "  2. First run downloads ~1.1 GB of model weights (one-time, ~5 min)"
echo "  3. Watch progress: tail -f /tmp/localflow.log"
echo "  4. When you see '✅ Models ready!', LocalFlow is ready to use"
echo ""
bold "First use:"
echo "  • macOS will ask for Microphone + Accessibility permissions — grant both"
echo "  • Click into any text field, press Option, speak, press Option again"
echo "  • Your dictated text appears in 1-3 seconds"
echo ""
bold "Useful commands:"
echo "  • Stop:     launchctl bootout gui/${UID_NUM} ${PLIST_PATH}"
echo "  • Start:    launchctl bootstrap gui/${UID_NUM} ${PLIST_PATH}"
echo "  • Logs:     tail -f /tmp/localflow.log"
echo "  • Restore:  ${LOCALFLOW_DIR}/restore-models.sh"
echo ""
