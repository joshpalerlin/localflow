#!/bin/bash
set -e

LOCALFLOW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/Applications/LocalFlow.app"
echo "Building LocalFlow.app..."

# Find the Homebrew Python framework binary
FRAMEWORK_PYTHON="$(ls /opt/homebrew/Cellar/python@3.14/*/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python 2>/dev/null | head -1)"
if [ -z "$FRAMEWORK_PYTHON" ]; then
    echo "ERROR: Homebrew Python 3.14 framework not found."
    exit 1
fi

# Verify venv site-packages exist
if [ ! -d "$LOCALFLOW_DIR/venv/lib/python3.14/site-packages" ]; then
    echo "ERROR: venv not found. Run install.sh first."
    exit 1
fi

CLANG_BIN="$(command -v clang || true)"
if [ -z "$CLANG_BIN" ]; then
    echo "ERROR: clang not found. Install Xcode Command Line Tools first."
    exit 1
fi

# Clean old app
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

# Build a tiny native launcher instead of using a shell script as the bundle
# executable. Finder, Launchpad, and codesign classify script-only app bundles as
# generic, which can make macOS ignore CFBundleIconFile and show a placeholder.
LAUNCHER_SRC="$(mktemp /tmp/localflow_launcher.XXXXXX.c)"
trap 'rm -f "$LAUNCHER_SRC"' EXIT
cat > "$LAUNCHER_SRC" << C_SRC
#include <stdlib.h>
#include <unistd.h>

int main(void) {
    const char *python = "$FRAMEWORK_PYTHON";
    const char *boot = "$APP_DIR/Contents/Resources/boot_localflow.py";
    setenv("PYTHONUNBUFFERED", "1", 1);
    execl(python, python, boot, (char *)0);
    return 127;
}
C_SRC
"$CLANG_BIN" -Os "$LAUNCHER_SRC" -o "$APP_DIR/Contents/MacOS/LocalFlow"

# Copy boot script and icon to Resources
cp "$LOCALFLOW_DIR/boot_localflow.py" "$APP_DIR/Contents/Resources/"
cp "$LOCALFLOW_DIR/AppIcon.icns" "$APP_DIR/Contents/Resources/"

# Create Info.plist
cat << 'PLIST' > "$APP_DIR/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>LocalFlow</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon.icns</string>
    <key>CFBundleIdentifier</key>
    <string>com.jpl.LocalFlow</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>LocalFlow</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.2</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>LocalFlow needs microphone access to capture your speech.</string>
</dict>
</plist>
PLIST

printf 'APPL????' > "$APP_DIR/Contents/PkgInfo"

# Dynamic build version — busts macOS icon cache on every rebuild
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $(date +%s)" "$APP_DIR/Contents/Info.plist"

# Ensure Python.framework has NSMicrophoneUsageDescription (survives Homebrew upgrades)
PYTHON_PLIST="$(dirname "$FRAMEWORK_PYTHON")/../../Info.plist"
if [ -f "$PYTHON_PLIST" ]; then
    /usr/libexec/PlistBuddy -c "Set :NSMicrophoneUsageDescription 'LocalFlow needs microphone access to capture your speech.'" "$PYTHON_PLIST" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string 'LocalFlow needs microphone access to capture your speech.'" "$PYTHON_PLIST"
    codesign --force --deep --sign - "$(dirname "$FRAMEWORK_PYTHON")/../../../.." 2>/dev/null
fi

# Ad-hoc code sign so the bundle seal is valid
codesign --force --deep --sign - "$APP_DIR"

# Finder can keep showing the generic app placeholder even when LaunchServices
# resolves CFBundleIconFile correctly. Set the package's Finder custom icon too,
# matching what "Paste icon in Get Info" does, so Finder and Launchpad agree.
PYTHONPATH="$LOCALFLOW_DIR/venv/lib/python3.14/site-packages" "$FRAMEWORK_PYTHON" << PY
from AppKit import NSImage, NSWorkspace

app = "$APP_DIR"
icon_path = "$APP_DIR/Contents/Resources/AppIcon.icns"
icon = NSImage.alloc().initWithContentsOfFile_(icon_path)
if icon is None:
    raise SystemExit("ERROR: failed to load AppIcon.icns")
if not NSWorkspace.sharedWorkspace().setIcon_forFile_options_(icon, app, 0):
    raise SystemExit("ERROR: failed to set Finder custom icon")
PY

# Register the final signed bundle with LaunchServices so Finder and Launchpad
# pick up the current CFBundleIconFile instead of a stale placeholder.
touch "$APP_DIR"
/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister -f "$APP_DIR" 2>/dev/null

# Sync to /Applications so Launchpad picks it up too
if [ -d /Applications ]; then
    rm -rf /Applications/LocalFlow.app
    cp -R "$APP_DIR" /Applications/LocalFlow.app
    echo "Synced to /Applications/LocalFlow.app"
fi

echo ""
echo "LocalFlow.app built at $APP_DIR"
echo ""
echo "ACCESSIBILITY: Enable 'LocalFlow-python' in"
echo "  System Settings → Privacy & Security → Accessibility"
echo ""
codesign -vv "$APP_DIR" 2>&1 && echo "Code signature: VALID" || echo "Code signature: INVALID"
