# Changelog

All notable changes to LocalFlow are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

---

## [0.2.3] — 2026-06-06

Voice Mode toggle — dictation OR translation, both 100% on-device.

### Added

- **Voice Mode menu** with two first-class options:
  - **Dictation** (default) — transcribe what you say in the selected language
  - **Translate to →** — speak in any language, output text in a chosen target. 14 target languages supported.
- **English target** uses Whisper's official `task="translate"` (best quality path — auto-detects source language, outputs English).
- **Non-English targets** (Spanish, French, Chinese Traditional/Simplified, Japanese, Korean, etc.) use Whisper's language-mismatch mechanism for meaning-preserving translation.
- **Auto-switch to multilingual model** when entering Translate mode if currently on `small.en`.

### Fixed

- **Garbage detector false-positive on CJK languages.** `_is_likely_garbage` previously used `.split()` to count words per second of audio, which massively under-counts Chinese / Japanese / Korean (no word spacing) and silently dropped valid CJK transcriptions as "extreme_word_mismatch." The detector now switches to characters-per-second for CJK content (normal CJK speech is 5-8 cps; extreme garbage threshold is < 1.0 cps). Includes 10 new regression tests covering the failing case from the log.

### Notes

- Translation runs on Whisper's multilingual model — same engine as OpenAI Whisper. Output is meaning-accurate for everyday messaging. For high-stakes professional translation (legal, medical, technical) use a dedicated translator.

---

## [0.2.2] — 2026-06-06

Multilingual support unlocked — 14 explicit languages, no auto-detect.

### Added

- **14-language menu** in the menu bar:
  - **Latin (full pipeline works well):** English, Spanish, French, German, Italian, Portuguese
  - **Slavic (full pipeline works well):** Polish, Russian
  - **CJK (Whisper transcription works; MLX cleanup may mangle punctuation):** Chinese (Traditional), Chinese (Simplified), Japanese, Korean
  - **Other scripts (experimental):** Hindi, Arabic
- **Traditional Chinese bias** — picking "Chinese (Traditional)" passes Whisper a Traditional-character `initial_prompt` to bias output toward Traditional rather than Simplified characters.
- **Auto-switch to multilingual model** — when user picks a non-English language, the app auto-switches Whisper from `small.en` to `small` (multilingual).

### Changed

- **Removed the V1 English-only force-lock** at config-load time. The app now respects the saved language preference instead of overriding it back to "en" on every boot.
- **Updated transcribe calls** (both main dictation and crash-recovery) to use the user's selected language instead of a hardcoded "en".

### Fixed

- **Silent model-load failure** — when a Whisper model fails to download (e.g. network blocked, HuggingFace unreachable), the app now:
  - Preserves the previously-loaded model (so dictation keeps working in the previous language)
  - Shows a clear macOS notification: "⚠️ Model download failed — check internet, VPN may be blocking HuggingFace"
  - Logs the failure clearly instead of printing "✅ Models ready!" misleadingly

### Design decisions

- **No "Auto-Detect" option in the menu.** Whisper auto-detect on the 244M-param `small` model is unreliable on short utterances (which dominate dictation use). Silent wrong-language output is a worse UX than requiring the user to pick once. Explicit beats wrong-guess.
- **CJK languages ship with English-trained MLX cleanup.** Per-language MLX prompt tuning + MLX-skip for CJK is planned for v0.3.0.

---

## [0.2.1] — 2026-06-01

Cleanup patch after the first public release.

### Fixed

- **Multi-word custom vocabulary regression** — `"Cloud Code"` no longer fails
  to correct to `"Claude Code"`.  The single-word `protected_words` guard
  (introduced in 0.2.0 to block `click → claude`) was over-applied to the
  multi-word path, breaking phrase-level corrections that have an anchoring
  exact-match token.  The fix scopes that guard to single-word matches only
  and keeps the stricter `_COMMON_WORDS` check on multi-word tokens.

### Changed

- Menu bar icon is now actually loaded by the app — the bundled
  `menubar_icon.png` was previously shipped but never used.  Now wired
  through `rumps.App(icon=..., template=True)` so it shows in the menu bar.
- Removed unused `groq==1.1.2` from `requirements.txt` (legacy from an
  earlier Groq cloud-routing prototype that was already deleted from the
  code).
- Removed `LocalFlow.command` — it conflicted with the launchd-managed
  install path.  The installer now manages app lifecycle cleanly.

---

## [0.2.0] — 2026-06-01

First public open-source release. Adds the post-processing intelligence that
sets LocalFlow apart from cloud dictation tools.

### Added

- **Self-learning corrections** — when LocalFlow misses a word, the user
  selects the correct version and hits Cmd+C. A background clipboard watcher
  detects the corrected word, matches it against what was last pasted
  (using text similarity + consonant-skeleton phonetic matching), and saves
  the substitution as a learned correction for next time.
- **Voice quote markers** — say `"quote-unquote premium research"` and the
  output is wrapped in real quotation marks: `"premium research"`. Supports
  three patterns: sarcastic (`quote unquote X`), bracketed (`quote X
  unquote`), and formal (`open quote X close quote`).
- **Consonant-skeleton phonetic matching** for custom vocabulary, with a
  protected-words guard so common English verbs (click, cloud, code, etc.)
  can never be falsely replaced by similar-sounding brand names (claude,
  codex, etc.).
- **Anti-ranking guard** in the smart-bullets formatter — prevents sentences
  like *"number one priority is speed, number two priority is cost"* from
  being mangled into bullet points.
- **Garbage / hallucination filter** — when Whisper produces nonsense on
  noisy audio, LocalFlow shows a clear `❌ Couldn't hear clearly` instead of
  pasting bad text.
- **Crash-recovery audio buffer** — if the app crashes mid-recording, the
  audio survives and is recovered on next boot.
- **Persistent CGEventTap** for keyboard listening — fixes "hotkey dies
  between recordings" issue on long-running sessions.
- **Dynamic MLX token budget** — long recordings get proportionally more
  output tokens so they don't get truncated mid-sentence.
- **Boot-time model integrity check** — verifies Whisper and MLX weights
  are present at startup; sends a macOS notification if anything's missing.
- **Local model cache** — model weights live in `~/localflow/models/` under
  the app's control instead of the shared HuggingFace cache, so external
  cache wipes can never break LocalFlow.
- **Restore script** — `restore-models.sh` for one-command recovery if the
  model cache gets wiped.

### Changed

- `install.sh` rewritten for non-coders — installs Homebrew + Python 3.14
  automatically, sets up launchd with `KeepAlive=true`, hardens against the
  most common install failures.
- README rewritten with a comparison table vs. Wispr Flow and FreeFlow,
  feature highlights, troubleshooting, and architecture diagram.
- `.gitignore` expanded to keep user state out of the repo (models,
  learned corrections, history, crash logs).

### Fixed

- `"clicked"` no longer gets phonetic-matched to `"claude"` (protected list).
- `"still"` no longer gets phonetic-matched to `"stealth"`.
- HTML tag stripping in MLX output now preserves spaces between sentences.
- Self-correction phrases (`"wait no"`, `"actually"`) don't fire inside
  unrelated sentences.

---

## [0.1.0] — 2026-04

Initial private release. Core dictation loop:

- Whisper `small.en` transcription on CPU
- MLX Llama 3.2 1B 4-bit for punctuation/capitalization cleanup
- Menu bar UI via rumps
- Hotkey listening via pynput + NSEvent
- pbcopy + osascript paste mechanism
- launchd background agent with auto-restart
- Flask dashboard on `localhost:5050`
