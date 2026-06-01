# Changelog

All notable changes to LocalFlow are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

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
