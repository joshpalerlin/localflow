# Contributing to LocalFlow

Thanks for considering a contribution! LocalFlow is a hobby project — support and review are best-effort, but every PR and issue is read.

---

## Before you open an issue

1. Check the [Troubleshooting section in the README](README.md#troubleshooting) — many problems are first-run setup.
2. Search [existing issues](https://github.com/joshpalerlin/localflow/issues) — your bug may already be tracked.
3. Look at `/tmp/localflow.log` — most failure modes leave a clear log line.

### When filing a bug, please include

- Your Mac model (e.g. M2 MacBook Air)
- macOS version (Apple → About This Mac)
- The relevant lines from `/tmp/localflow.log`
- What you said / typed
- What LocalFlow output vs what you expected

A minimal reproducible example is gold — *"I said X, expected Y, got Z, here's the log line"* gets fixed 10× faster than *"it doesn't work."*

---

## Before you open a PR

### Small fixes (typos, single-line bug fixes, doc improvements)

Open a PR directly. No need to discuss first.

### Bigger changes (new features, refactors, dependency changes)

Open an issue first to talk through the approach. Saves you and me from a wasted afternoon if the change doesn't fit the project direction.

### What this project IS

- A 100% local, MIT-licensed macOS dictation app
- Apple Silicon-first (M-series chips)
- Privacy and zero-cost are non-negotiable design constraints
- Python + rumps + MLX, deliberately

### What this project is NOT

- A cloud dictation app (not even optionally — cloud routing was explicitly removed)
- A Windows/Linux app (out of scope; MLX is Apple-only)
- A multilingual transcription tool (English-first; multilingual works but isn't a priority)

PRs that change the project's core nature will be politely declined.

---

## Development setup

```bash
# Clone + run installer (sets up venv, dependencies, launchd)
git clone https://github.com/joshpalerlin/localflow.git
cd localflow
./install.sh

# Run the app manually instead of via launchd
source venv/bin/activate
python3 localflow_app.py
```

For iteration, stop the launchd agent and run the app directly so you see logs in your terminal:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.localflow.app.plist
source venv/bin/activate
python3 localflow_app.py
```

---

## Code style

- Python 3.14, no type-stub gymnastics — keep it readable.
- Plain `print()` for logs (they go to `/tmp/localflow.log` via launchd's stdout redirect).
- Inline comments explain *why*, not *what*.
- Match the surrounding code's style if you're editing existing code — don't reformat unrelated lines.

---

## Running tests

```bash
source venv/bin/activate
pytest tests/
```

The test suite focuses on the post-processing chain (custom-word matching, self-correction, voice quotes, paragraph splitting). Audio capture and macOS-specific code (CGEventTap, NSEvent) are not unit-testable — those get exercised via dogfood use.

---

## What's helpful right now

If you want to contribute but don't have a specific bug, here are high-value areas:

- **Test coverage** for `_apply_custom_words`, `_apply_self_corrections`, `_apply_voice_quote_marks`, `_smart_bullets` — every false positive / false negative we catch in code is one less surprise for users.
- **Swift native rewrite** (long-term roadmap) — the install ceiling for non-coders is the Python+venv+launchd setup. A signed `.dmg` would 10× the install rate.
- **Larger Whisper model fallback** for users with M3 Max / M4 chips — `medium.en` is 5× slower but much more accurate, and the hardware can handle it now.
- **Learned-corrections session continuity** — currently corrections persist via `learned_corrections.json`, but the detection mechanism only triggers on Cmd+C. A more aggressive auto-detect (without invasive Cmd+A snapshots) would be welcome.

---

## License

By contributing, you agree your contribution will be licensed under the MIT License (the same license as the rest of the project).
