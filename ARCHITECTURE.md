# LocalFlow Architecture

This document explains how LocalFlow turns a hotkey press into pasted text. It's intended for contributors who want to understand the codebase before making changes.

---

## High-level pipeline

```
hotkey press
    ↓
mic captures audio (PortAudio via sounddevice)
    ↓
audio frames → temp .wav file
    ↓
faster-whisper transcribes → raw text
    ↓
post-processing chain (pre-LLM, deterministic):
   • custom_words fuzzy match
   • domain phrase corrections
   • safe learned corrections (regex)
   • voice quote markers
   • self-correction phrases
    ↓
MLX Llama 3.2 1B (4-bit) → punctuation + capitalization + paragraph breaks
    ↓
post-processing (post-LLM):
   • HTML tag strip
   • smart bullets / anti-ranking guard
   • paragraph splitter
    ↓
pbcopy → clipboard
    ↓
osascript Cmd+V keystroke → text appears in active app
```

Every step runs on the user's Mac. No network calls during dictation. The only network use is the first-run model download from HuggingFace.

---

## File map

| File | Role |
|---|---|
| `localflow_app.py` | Main app. ~3000 lines. Contains UI, hotkey listener, audio capture, model loading, post-processing chain, dashboard server, watchdog. |
| `boot_localflow.py` | Bootstrap launcher. Injects venv into `sys.path`, redirects stdout/stderr to `/tmp/localflow.log`, hides Python from the macOS Dock via `NSApplication.setActivationPolicy_(1)`. |
| `dashboard.py` | Flask server on `localhost:5050` for viewing history, editing custom words, viewing settings. |
| `install.sh` | One-command installer. Handles Homebrew, Python 3.14, venv, dependencies, launchd plist, app startup. |
| `restore-models.sh` | Re-downloads models if local cache is wiped. |
| `requirements.txt` | Python dependencies. |
| `config.example.json` | Template config file. Users copy to `config.json` and personalize. |
| `templates/` | Flask HTML templates for the dashboard. |
| `tests/` | Pytest suite for post-processing functions. |

---

## Tech stack

- **Speech-to-text:** `faster-whisper` (CTranslate2 port of Whisper) using `small.en` on CPU with `int8` quantization. Lower latency than GPU on Apple Silicon for short clips.
- **LLM cleanup:** `mlx-lm` running `mlx-community/Llama-3.2-1B-Instruct-4bit`. Loads in ~1 second, generates ~200 tokens/sec on M-series.
- **Menu bar UI:** `rumps` (Python wrapper around AppKit's NSStatusBar).
- **Hotkey listener:** `pynput` for basic key events + `CGEventTap` (via `Quartz` PyObjC bindings) as a persistent always-on listener that survives sleep/wake.
- **Audio capture:** `sounddevice` (PortAudio binding) + `soundfile` for WAV I/O.
- **Paste mechanism:** `pbcopy` (writes to clipboard) + `osascript` (synthesizes Cmd+V keystroke via `System Events`).
- **Process supervision:** macOS `launchd` with `KeepAlive=true` and `ThrottleInterval=10` for auto-restart on crash.
- **Web dashboard:** `Flask` on `localhost:5050`.

---

## Key subsystems

### 1. Persistent CGEventTap

The keyboard listener is a `CGEventTap` started at boot and running on its own thread for the lifetime of the app. Earlier versions started/stopped the tap per recording, which created a window where the hotkey would die if the listener thread crashed between recordings.

The tap is registered with `kCGEventTapOptionListenOnly` so it does not block the keystroke from reaching other apps. A watchdog thread restarts the tap if its thread dies.

### 2. Custom word matcher (`_apply_custom_words`)

Fuzzy-matches every n-gram in the transcript against the user's custom dictionary using two strategies:

1. **Text similarity** (`difflib.SequenceMatcher`) with thresholds tuned by word length.
2. **Consonant skeleton phonetic match** for cases where Whisper produces a phonetically similar but textually distant word (e.g. "Kodak" → "Codex"). Strips vowels, normalizes consonant sounds (c→k, x→ks, ph→f), and requires the first two consonants to agree to prevent false positives like "hockey" → "hotkey".

A **protected words list** prevents common English verbs (click, cloud, code, claim, jar, whip, hockey) from being phonetic-matched into brand names.

### 3. Self-learning loop

After every paste, two systems try to detect user corrections:

1. **Field snapshot** — at the start of each new recording, the app silently does `Cmd+A → Cmd+C → Right arrow` on the focused field, compares the result to what was last pasted, and saves any word substitutions as learned corrections.
2. **Clipboard watcher** — runs in the background for 60+ seconds after each paste, watching for the user to copy a corrected version.

Learned corrections are saved to `learned_corrections.json`. They feed back into the pipeline as either deterministic regex (for uncommon words) or LLM hints (for common words that need context-aware decisions).

### 4. Post-processing chain order

The order matters. Pre-LLM steps clean known errors deterministically; the LLM handles the rest with context.

```
raw whisper output
    ↓
_apply_custom_words()         brand-name corrections
_apply_domain_phrase_corrections()
_apply_learned_corrections_safe()
_apply_voice_quote_marks()    "quote-unquote X" → "X"
_apply_self_corrections()     "wait no, actually X" → "X"
    ↓
clean_with_mlx()              LLM adds punctuation, capitalization, paragraph breaks
    ↓
_extract_clean()              strip XML wrapper tags
_flatten_lists()              prevent unwanted list formatting
_smart_bullets()              detect intentional lists, format them
_split_paragraphs()           ensure paragraph breaks every 2-3 sentences
```

### 5. Garbage / hallucination filter

`_is_likely_garbage()` runs after Whisper transcription. Flags include:

- Output that's too short relative to audio duration (Whisper hallucinated silence)
- Repeating word patterns (Whisper stuck in a loop)
- Common Whisper hallucination phrases ("thank you for watching", "subscribe")

When triggered, LocalFlow displays `❌ Couldn't hear clearly` and skips the paste. This prevents the user from getting a clean-looking but completely wrong paragraph.

### 6. Crash recovery audio buffer

Audio frames are streamed to a temp WAV file as they arrive. If the app crashes mid-recording, the partial WAV survives and is recovered on next boot. The recovered audio is preserved in `crash_forensics.jsonl` for debugging.

### 7. Local model cache

Models are stored in `models/` (LocalFlow's own folder under the app directory) rather than the shared HuggingFace cache (`~/.cache/huggingface/`). The `HF_HOME` environment variable is set before any HuggingFace import so the library writes there.

A boot-time integrity check verifies both models exist before declaring "Models ready". If anything's missing, the user sees a macOS notification immediately, and the `restore-models.sh` script can re-pull from HuggingFace.

### 8. Dynamic MLX token budget

Long recordings produce long transcripts. A fixed 200-token output budget would truncate them mid-generation, losing punctuation in the back half. The budget scales with input length:

```python
max_tokens = max(250, raw_word_count * 2 + 100)
```

For a 200-word input, the budget is ~500 tokens — enough to write the cleaned text in full.

---

## Config file

`config.json` is auto-generated on first run from `config.example.json`. User-editable via the menu bar UI or by editing the file directly.

Key fields:

- `trigger_key` — `alt`, `cmd`, or `ctrl`
- `whisper_model` — `small.en` (English) or `small` (multilingual)
- `custom_words` — proper nouns the LLM should preserve / Whisper should be corrected against
- `snippets` — exact-match phrase substitutions
- `auto_bullets` — try to detect numbered/bulleted lists in speech and format them
- `app_context` — adapt output style to frontmost app

---

## launchd configuration

LocalFlow runs as a per-user launchd agent (not a system daemon). The plist lives at:

```
~/Library/LaunchAgents/com.localflow.app.plist
```

Key settings:

- `KeepAlive=true` — auto-restart on crash
- `ThrottleInterval=10` — wait 10s between restarts to avoid crash-loop hammering
- `LSUIElement=true` — no Dock icon
- `RunAtLoad=true` — start on login

Lifecycle commands:

```bash
# Stop
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.localflow.app.plist

# Start
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.localflow.app.plist

# Watch logs
tail -f /tmp/localflow.log
```

---

## Contributing

PRs welcome. Areas where help would be especially valuable:

- Swift port (replaces the Python+launchd setup with a signed `.app` bundle, makes distribution easier)
- Larger/multilingual Whisper model fallback for non-English users
- More aggressive learned-corrections detection (track edits across sessions, not just next-recording snapshots)
- Test coverage for the post-processing chain

See `tests/` for the existing test patterns.
