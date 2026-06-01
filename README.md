<p align="center">
  <img src="localflow-banner.png" alt="LocalFlow — Local Mac dictation. No API keys. No cloud." />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/macOS-13%2B%20Apple_Silicon-black?logo=apple" alt="macOS" />
  <img src="https://img.shields.io/badge/Python-3.14-blue?logo=python" alt="Python" />
  <img src="https://img.shields.io/github/license/joshpalerlin/localflow?color=green" alt="License" />
  <img src="https://img.shields.io/github/v/release/joshpalerlin/localflow?include_prereleases&sort=semver" alt="Latest release" />
  <img src="https://img.shields.io/github/stars/joshpalerlin/localflow?style=social" alt="Stars" />
</p>

<!-- demo GIF goes here once recorded -->

---

## Why LocalFlow?

- **🔒 Truly private.** Your audio never touches a server. No cloud transcription, no API key sent anywhere. If you dictate company secrets, client data, or sensitive thoughts, this matters.
- **💰 $0 forever.** Wispr Flow is $15/month. FreeFlow needs a paid Groq API key. LocalFlow costs nothing, ever, by design.
- **✈️ Works offline.** Airplane, train tunnel, secure environment — LocalFlow works the same. No internet, no problem.
- **🧠 Smarter post-processing.** Voice quote markers, garbage / hallucination filter, anti-ranking guard for numbered speech, and an on-device LLM cleanup pipeline. A few of these aren't documented in any competitor we've checked.
- **📂 MIT open source.** Read the code, fork it, ship your own version. Built on top of Whisper + Apple's MLX framework.

### Detailed comparison

> **TL;DR** — LocalFlow does fewer things than Wispr Flow, but does the things that matter to a privacy-conscious user without compromise: truly on-device transcription AND cleanup, truly free, MIT-licensed, and with a few speech-intelligence features no competitor has documented.

> Comparison based on each vendor's public documentation as of June 2026. Sources cited per feature in [docs/competitor-research.md](https://github.com/joshpalerlin/localflow/issues) — vendor features change frequently, please verify if it matters to your decision.

#### 🔒 Privacy & Data

| | Wispr Flow | FreeFlow | VoiceInk | **LocalFlow** |
|---|---|---|---|---|
| Audio stays on your device | ❌ Cloud | ❌ Cloud default / ✅ Optional with Ollama | ⚠️ Hybrid — cloud STT options + cloud LLM enhancement (BYOK) | ✅ Always |
| Works offline | ❌ | ❌ default / ✅ optional | ⚠️ Only if you avoid the cloud options | ✅ Always |
| Telemetry | Significant (usage, sessions, billing) | None (Groq sees audio) | ❓ Not explicitly documented | None |
| No API key field exists in the app | ❌ | ❌ | ❌ (BYOK supported) | ✅ |

#### 💰 Cost

| | Wispr Flow | FreeFlow | VoiceInk | **LocalFlow** |
|---|---|---|---|---|
| Price | **$15/month** | Free app | $25 / $39 / $49 lifetime (1/2/3 Macs) | **$0 forever** |
| API key required | No | Yes (Groq) | Optional (for cloud features) | **No** |
| Open source license | ❌ Proprietary | MIT | GPL v3 (copyleft) | **MIT (most permissive)** |

#### ⚡ Speed

| | Wispr Flow | FreeFlow | VoiceInk | **LocalFlow** |
|---|---|---|---|---|
| Transcription latency | **~0.3s** (cloud) | ~0.5s (cloud) | Varies (cloud routes fastest) | ~2s (local) |
| Consistent latency offline | ❌ | ❌ default | Slower when forced local | ✅ |

#### 🧠 Post-processing intelligence

| Feature | Wispr Flow | FreeFlow | VoiceInk | **LocalFlow** |
|---|---|---|---|---|
| Custom vocabulary / Personal Dictionary | ✅ + categories + usage ranking | ✅ basic list | ✅ Personal Dictionary + Word Replacements | ✅ + consonant-skeleton phonetic matching |
| App-context aware | ✅ Personalized Style per app category | ✅ context-aware cleanup | ✅ **Power Mode** (per-app AND per-website with per-context prompts) | ✅ frontmost-app detection |
| Edit Mode (voice-rewrite selected text) | ✅ Command Mode (cloud-processed) | ✅ | ✅ AI Assistant Mode (requires cloud AI) | ✅ (local MLX) |
| Spoken punctuation (say "quotation mark" → `"`) | ✅ ("quotation mark" / "period") | ❌ | ❌ | ❌ (planned) |
| "Quote-unquote X" wrap pattern (`"quote-unquote premium"` → `"premium"`) | ❌ (not documented) | ❌ | ❌ | ✅ |
| Self-correction trigger phrases (`"wait no"`, `"scratch that"`, `"sorry I meant"`) | ✅ Backtrack (triggers: "actually", "scratch that", restatements) | ❓ Implicit via LLM only | ❌ | ✅ (7 documented triggers) |
| Self-learning from user corrections | ✅ Auto-add to dictionary on spelling fix | ❌ | ❌ | ✅ Clipboard watcher |
| Garbage / hallucination filter (Whisper nonsense detection) | ❓ Not documented | ❌ Partial (anti-name only) | ❌ (has open bug report) | ✅ |
| Anti-ranking guard ("number one priority is X" stays as sentence) | ❌ Numbers trigger lists per docs | ❌ | ❓ Depends on user prompt | ✅ |
| Crash-recovery audio buffer | ✅ (added May 2026) | ❌ | ❓ Not documented | ✅ |
| **On-device LLM cleanup** | ❌ Cloud | ❌ Cloud default / ✅ via local Ollama | ❌ Cloud LLM (BYOK) | ✅ MLX Llama 3.2 1B |

#### 🛠️ Platform & Distribution

| | Wispr Flow | FreeFlow | VoiceInk | **LocalFlow** |
|---|---|---|---|---|
| macOS | ✅ | ✅ | ✅ (14.4+) | ✅ |
| Windows / iOS | ✅ | ❌ | ❌ | ❌ |
| Intel + Apple Silicon | ✅ | ✅ | ✅ | ❌ (M1+ only) |
| Signed `.dmg` installer | ✅ | ✅ | ✅ | ❌ (source install — [#2](https://github.com/joshpalerlin/localflow/issues/2)) |
| Homebrew cask | ❌ | ❌ | ✅ | ❌ ([#2](https://github.com/joshpalerlin/localflow/issues/2)) |
| Accepts PRs | ❌ Proprietary | ✅ | ❌ (explicitly closed despite open source) | ✅ |
| Stars | n/a (closed source) | ~1.8k | ~5.1k | <100 (just launched) |

**The honest pitch:**

- Need **cloud speed and multi-platform** (Mac + Windows + iOS)? → **Wispr Flow** ($15/mo).
- Want **polished native Mac app with deep per-app customization, willing to pay**? → **VoiceInk** ($25-$49 lifetime).
- Want **a cloud-flexible setup you can swap LLMs on**? → **FreeFlow** (Groq default, swap to Ollama).
- Want **truly on-device transcription AND cleanup, $0 forever, MIT-licensed, with a few speech features no one else has**? → **LocalFlow**.

**Where we honestly lose:** stars, polish, signed `.dmg`, Homebrew, Intel support, transcription speed, depth of per-app customization (VoiceInk's Power Mode is the gold standard), and spoken-punctuation parity with Wispr Flow.

**Where we honestly win:** truly on-device LLM cleanup (the only one in the category), the "quote-unquote X" wrap pattern specifically, the garbage / hallucination filter, the anti-ranking guard, MIT vs proprietary/GPL, and zero recurring cost.

---

## Features

- **Hotkey dictation** — tap your modifier key, speak, tap again. Text appears.
- **Local Whisper** transcription (`small.en` on CPU int8 — fast and accurate)
- **Local LLM cleanup** via MLX Llama 3.2 1B (4-bit) — adds punctuation, capitalization, paragraph breaks, drops "um/uh"
- **Custom vocabulary** — keep proper nouns and brand names spelled right ("Codex", "Claude", "n8n")
- **Self-learning** — when LocalFlow pastes a word wrong, select the corrected version, hit Cmd+C, and it remembers the fix for next time
- **Voice quote markers** — say *"quote-unquote premium research"* → outputs `"premium research"`
- **Self-correction detection** — say *"use React, wait no, use Vue"* → outputs just `Use Vue` (triggers: `wait no`, `scratch that`, `i mean`, `no wait`, `correction`, `strike that`, `sorry`)
- **Edit Mode** — select text in any app, dictate a transformation like *"make this shorter"* or *"turn this into bullets"* and LocalFlow rewrites the selection. Locally processed via MLX.
- **Anti-ranking guard** — say *"number one priority is speed, number two priority is cost"* and it stays as a sentence (no surprise bullet conversion)
- **App-context aware** — adjusts tone for Slack vs. email vs. code editor
- **Garbage filter** — if Whisper hallucinates on background noise, LocalFlow shows ❌ instead of pasting nonsense
- **Crash recovery** — audio buffer survives crashes; nothing is lost
- **Auto-paste** — works in any app: Chrome, Slack, VS Code, iMessage, Notes, anywhere
- **Cancel/rescue flow** — hit ESC during recording to abort

---

## Requirements

- **macOS on Apple Silicon** (M1, M2, M3, M4) — Intel Macs not supported
- **16 GB RAM minimum** (LocalFlow uses ~1.2 GB at idle with models loaded)
- **3 GB free disk** (for model weights)
- **Python 3.14** via Homebrew

---

## Install

### One-line install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/joshpalerlin/localflow/main/install.sh | bash
```

This installs Homebrew (if missing), Python 3.14, creates a virtualenv, downloads dependencies, sets up a launchd agent so LocalFlow starts on boot, and launches the app. About 5 minutes including model downloads (~1.1 GB).

### Manual install

```bash
# 1. Clone
git clone https://github.com/joshpalerlin/localflow.git
cd localflow

# 2. Run installer
./install.sh
```

### First-run permissions

macOS will prompt for two permissions the first time you press the hotkey:

1. **Microphone** — to hear you
2. **Accessibility** — to paste text into other apps

Grant both. LocalFlow won't work without them.

---

## Usage

1. Click into any text field (email, Slack, Notes, anywhere)
2. Press your hotkey (default: **Option / Alt**)
3. Speak naturally
4. Press the hotkey again to stop
5. Wait 1-3 seconds — cleaned text appears in your field

### Examples

| You say | LocalFlow pastes |
|---|---|
| "hey sarah just wanted to follow up on the proposal" | `Hey Sarah, just wanted to follow up on the proposal.` |
| "i think we should use react, wait no, we should use vue" | `We should use Vue.` |
| "this is quote-unquote premium research" | `This is "premium research".` |
| "check the ENOS pipeline" *(once "n8n" is learned)* | `Check the n8n pipeline.` |

### Cancel / rescue

If you start a recording and want to bail out, press **ESC** during the recording. You get a 5-second window: hit ESC again to cancel permanently, or do nothing and the text pastes normally.

---

## Configuration

Edit `config.json` in the project root, or use the menu bar dropdown:

- **Hotkey** — choose `alt`, `cmd`, or `ctrl`
- **Custom words** — your personal dictionary of proper nouns, brand names, jargon
- **Whisper model** — `small.en` (English, fast) or `small` (multilingual)
- **Self-correction triggers** — phrases that trigger "drop the mistake" behavior

---

## Troubleshooting

### Hotkey doesn't work after install

LocalFlow needs **Accessibility** permission to listen for your hotkey and paste text. macOS asks for this on first use, but the permission attaches to the specific Python binary inside the virtualenv.

If the hotkey isn't working:

1. Open **System Settings → Privacy & Security → Accessibility**
2. Look for `python3` or `python3.14` in the list — toggle it ON
3. If it's not in the list, click `+` and add `~/localflow/venv/bin/python3`
4. Restart LocalFlow: `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.localflow.app.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.localflow.app.plist`

### "Models are still downloading"

First run downloads ~1.1 GB. Watch progress:

```bash
tail -f /tmp/localflow.log
```

If the download seems stuck (no progress for >2 min), check your internet connection. HuggingFace can rate-limit anonymous downloads.

### Restore broken models

If model weights get corrupted or wiped:

```bash
~/localflow/restore-models.sh
```

This forces a fresh download into `~/localflow/models/`.

---

## Known limitations

- **Apple Silicon only.** MLX requires M-series chips. Intel Macs not supported.
- **English-first.** Default model is `small.en`. Multilingual works but accuracy drops outside English.
- **First boot is slow.** Downloads ~1.1 GB of model weights on first run (one-time).
- **Long recordings.** Aim for under 90 seconds per recording. Longer ones still work but processing takes longer.
- **Background noise.** Whisper occasionally hallucinates on noisy audio. The garbage filter catches most, but not all.

---

## Roadmap

> Honest "what I'm working on next" — not promises. Real progress is tracked in [GitHub issues](https://github.com/joshpalerlin/localflow/issues).

### v0.3.0 — Frictionless install (next release)

- Signed `.dmg` installer with Apple notarization ([#2](https://github.com/joshpalerlin/localflow/issues/2))
- Homebrew tap: `brew install --cask localflow` ([#2](https://github.com/joshpalerlin/localflow/issues/2))
- **Auto-update mechanism** — Sparkle-style version checking so users don't have to `git pull` ([#9](https://github.com/joshpalerlin/localflow/issues/9))
- Reduce first-run download from 1.1 GB → under 500 MB ([#6](https://github.com/joshpalerlin/localflow/issues/6))
- Demo GIF in the README ([#1](https://github.com/joshpalerlin/localflow/issues/1))

### v0.4.0 — Stats + smarter learning

- **Stats dashboard** in the menu bar: total words dictated, WPM, day streak ([#7](https://github.com/joshpalerlin/localflow/issues/7))
- **Personal Dictionary** — categorized entries (proper nouns / jargon / replacements), per-entry pronunciation hints, dashboard editor ([#10](https://github.com/joshpalerlin/localflow/issues/10))
- More aggressive self-learning — detect edits across sessions, not just next-recording
- Better multilingual accuracy on `small` (multilingual) model ([#3](https://github.com/joshpalerlin/localflow/issues/3))
- Per-app custom dictionaries — Slack tone vs Mail tone ([#8](https://github.com/joshpalerlin/localflow/issues/8))

### v0.5.0+ — Native Swift app

- Full Swift rewrite for distribution as a real `.app` bundle
- Mac App Store submission
- Optional paid tier (sync settings across Macs, cloud backup of learned corrections)
  while keeping the Python source forever free

### Won't do

- Cloud-only mode (defeats the privacy premise)
- Windows / Linux support (MLX is Apple Silicon only — would require a fundamentally different architecture)
- Telemetry of any kind

---

## How it works

```mermaid
flowchart LR
    A([🎙️ Hotkey press]) --> B[Mic capture<br/>PortAudio]
    B --> C[Whisper small.en<br/>local transcription]
    C --> D[Custom words +<br/>voice commands]
    D --> E[MLX Llama 3.2 1B<br/>punctuation + cleanup]
    E --> F[pbcopy + Cmd+V]
    F --> G([📝 Text in your app])

    style A fill:#1f2937,stroke:#60a5fa,stroke-width:2px,color:#fff
    style B fill:#374151,stroke:#9ca3af,color:#fff
    style C fill:#374151,stroke:#9ca3af,color:#fff
    style D fill:#374151,stroke:#9ca3af,color:#fff
    style E fill:#374151,stroke:#9ca3af,color:#fff
    style F fill:#374151,stroke:#9ca3af,color:#fff
    style G fill:#065f46,stroke:#34d399,stroke-width:2px,color:#fff
```

**Every step runs on your Mac.** No network calls. No audio uploads. No telemetry. The only time LocalFlow touches the internet is the one-time model download on first run.

For the detailed architecture (every subsystem, every post-processing stage, every config flag), see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

This is a hobby project — support is best-effort. Bug reports get fixed fastest when they include macOS version, Mac model, the raw line from `/tmp/localflow.log`, and what you expected vs. what happened.

---

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting and the project's privacy guarantees.

---

## Star history

<a href="https://star-history.com/#joshpalerlin/localflow&Date">
  <img src="https://api.star-history.com/svg?repos=joshpalerlin/localflow&type=Date" alt="Star History Chart" width="600" />
</a>

If LocalFlow helps you, a star is the cheapest thank-you and the only signal that tells me to keep building.

---

## License

MIT. See [LICENSE](LICENSE). Use it, fork it, ship your own version — just don't blame me if it transcribes "ENOS" instead of "n8n" on first run (it learns fast).

---

## Credits

Built by [Josh Paler Lin](https://github.com/joshpalerlin). Standing on the shoulders of:

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CTranslate2 port of OpenAI Whisper
- [mlx-lm](https://github.com/ml-explore/mlx-lm) — Apple's MLX framework for local LLMs
- [Meta Llama 3.2](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct) — the cleanup brain
- [rumps](https://github.com/jaredks/rumps) — Python menu bar apps for macOS
- [pynput](https://github.com/moses-palmer/pynput) — keyboard event listening
