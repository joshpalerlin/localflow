import sys, os

# Resolve the app source dir from this script's location.  Works whether the
# script is run from the source tree directly or copied into a .app bundle
# (Contents/Resources/) — in the bundle case the caller can override APP_SRC
# via the LOCALFLOW_APP_SRC env var.
APP_SRC = os.environ.get(
    "LOCALFLOW_APP_SRC",
    os.path.dirname(os.path.abspath(__file__))
)
# Try to detect Python version dynamically so a future minor-version bump
# (e.g. 3.15) doesn't break venv lookup.  Falls back to 3.14 for compat.
_py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
VENV_SITE = os.path.join(APP_SRC, "venv", "lib", _py_ver, "site-packages")
if not os.path.isdir(VENV_SITE):
    # Fallback to 3.14 layout
    VENV_SITE = os.path.join(APP_SRC, "venv", "lib", "python3.14", "site-packages")

# Inject venv FIRST — objc/AppKit only live there
if os.path.isdir(VENV_SITE) and VENV_SITE not in sys.path:
    sys.path.insert(0, VENV_SITE)

# Inject app source
if APP_SRC not in sys.path:
    sys.path.insert(0, APP_SRC)

os.chdir(APP_SRC)

# Redirect output to log file (append mode — preserves crash evidence across restarts)
# Rotate if > 500KB to prevent unbounded growth
_log_path = "/tmp/localflow.log"
try:
    if os.path.exists(_log_path) and os.path.getsize(_log_path) > 500_000:
        # Keep last 200KB
        with open(_log_path, "rb") as _f:
            _f.seek(-200_000, 2)
            _tail = _f.read()
        with open(_log_path, "wb") as _f:
            _f.write(b"[Log rotated]\n")
            _f.write(_tail)
except Exception:
    pass
_log = open(_log_path, "a", buffering=1)
print(f"\n{'='*60}\n[Boot] LocalFlow starting at {__import__('time').strftime('%Y-%m-%d %H:%M:%S')}\n{'='*60}", file=_log)
sys.stdout = _log
sys.stderr = _log

# Hide Python from Dock — MUST happen before rumps/AppKit UI initializes.
# Done after venv injection so objc is importable.
try:
    from AppKit import NSApplication
    NSApp = NSApplication.sharedApplication()
    # NSApplicationActivationPolicyAccessory = 1 (menu bar only, no Dock icon)
    NSApp.setActivationPolicy_(1)
    print("[Dock] Activation policy set to Accessory (hidden from Dock)")
except Exception as e:
    print(f"[Dock] Failed to set activation policy: {e}")

# Run the app — override __file__ so APP_DIR resolves to the source dir, not the app bundle
app_path = os.path.join(APP_SRC, "localflow_app.py")
with open(app_path) as f:
    code = compile(f.read(), app_path, "exec")
globs = {"__file__": app_path, "__name__": "__main__", "__builtins__": __builtins__}
exec(code, globs)
