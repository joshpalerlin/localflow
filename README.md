# LocalFlow 🎤

> Free, 100% local WhisperFlow replacement for macOS. Zero cloud. Zero subscription. Zero account.

Hold one key → speak → release → cleaned text auto-types into any app.

---

## What It Does

| Feature | LocalFlow | Wispr Flow |
|---|---|---|
| On-device transcription | ✅ (OpenAI Whisper) | ☁️ Cloud |
| Filler word cleanup | ✅ Rule-based, built-in | ✅ LLM cloud |
| Privacy | ✅ Nothing leaves your Mac | ❌ Audio sent to cloud |
| Cost | **$0 forever** | $10-20/month |
| Open source | ✅ MIT | ❌ |

## Requirements

- macOS 12+ (Apple Silicon recommended)
- Python 3.9+
- Microphone access
- Accessibility access (for auto-typing)

## Install

```bash
git clone https://github.com/YOUR_USERNAME/localflow
cd localflow
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
cd localflow
source venv/bin/activate
python3 localflow.py
```

Then:
1. Click into any text field (Notes, Slack, VS Code, anywhere)
2. Hold the **Option (⌥)** key
3. Speak
4. Release — clean text types automatically

Grant **Microphone** and **Accessibility** permissions when prompted (System Settings → Privacy).

## Config

Edit the top of `localflow.py`:

```python
WHISPER_MODEL  = "base"   # tiny | base | small | medium | large
HOTKEY         = keyboard.Key.alt   # Option key
ENABLE_CLEANUP = True     # Strip filler words (um, uh, like, etc.)
```

Larger models = more accurate, slower. `base` is the sweet spot for M1/M2.

## How Cleanup Works

LocalFlow strips common filler words automatically:
- Removes: `um`, `uh`, `like`, `you know`, `basically`, `literally`, etc.
- Fixes double spaces and punctuation
- Capitalizes first word, adds period if missing

No LLM required — pure rule-based, instant, zero latency.

## License

MIT — free forever. Use it, fork it, ship it.
