# Security Policy

LocalFlow takes security and privacy seriously — they're the whole point of the project.

---

## Supported versions

LocalFlow is in early alpha. Only the latest `main` branch is supported.

| Version | Supported |
|---------|-----------|
| `main`  | ✅ |
| pre-`v0.2.0` | ❌ |

---

## Reporting a vulnerability

If you find a security issue, **please do not open a public GitHub issue.** Instead, email the maintainer privately.

You can find a current email by checking the GitHub profile at [github.com/joshpalerlin](https://github.com/joshpalerlin).

Include:

- A description of the vulnerability
- Reproduction steps or proof-of-concept code
- Your assessment of severity / impact
- (Optional) Your name and how you'd like to be credited if a fix ships

**Expected response time:** within 7 days for initial acknowledgement. Patch timeline depends on severity.

---

## What counts as a security issue

LocalFlow runs locally on your Mac with Accessibility and Microphone permissions. These permissions are powerful. A security issue includes:

- Anything that exfiltrates audio, transcripts, or clipboard contents off the user's Mac
- Code paths that execute arbitrary shell commands based on user-controllable input (e.g. config files, learned corrections, custom words)
- Privilege escalation past what the user explicitly granted
- Bypass of the "100% local" guarantee (e.g. accidentally hitting a remote API)
- Unsafe deserialization of `config.json`, `learned_corrections.json`, or `history.md`
- Path traversal in file operations
- Race conditions that could cause data loss in `learned_corrections.json` or `history.md`

---

## What does NOT count as a security issue

- "It records my voice when I press the hotkey" — that is the design.
- "It pastes text into the focused window" — that is the design.
- Bugs that crash the app (file a regular issue — those are bugs, not security holes).
- Performance issues (file a regular issue).
- Whisper occasionally mishearing words (file a regular issue with the raw transcript).

---

## Privacy guarantees

LocalFlow makes these promises:

1. **No audio leaves your Mac.** All transcription happens locally via `faster-whisper` + MLX.
2. **No telemetry.** The app makes zero outbound network calls during normal operation.
3. **First-run model downloads** are the only network activity. These hit HuggingFace's CDN to pull Whisper and Llama model weights. After the first run, no network is needed.
4. **History is local-only.** `history.md` lives in the LocalFlow directory and is `.gitignore`'d by default.
5. **Learned corrections are local-only.** `learned_corrections.json` never leaves the disk.

If you discover any code path that violates one of these guarantees, that is a critical security issue. Please report it.

---

## Verifying release signatures

> **Note for v0.2.0:** Pre-built signed `.dmg` releases are not yet shipped. Until they are, the only install path is `./install.sh` from a git clone — which means you're running code straight from this repository. **Read the source before running.** The full `localflow_app.py` is ~3000 lines, the install script is ~180 lines. Both are auditable in under an hour.
>
> When notarized `.dmg` releases ship, this section will document how to verify their Apple Developer ID signature with `codesign -dvv` and SHA-256 checksums.

---

## Threat model

LocalFlow is designed against these threats:

- **Network adversaries** — defeated by running everything locally
- **Cloud-provider lock-in / data harvesting** — defeated by having no cloud dependency
- **Accidental personal-data leaks in logs** — `history.md` and `crash_forensics.jsonl` are gitignored; `/tmp/localflow.log` is never auto-uploaded

LocalFlow is NOT designed against:

- A compromised macOS install — if your OS is compromised, everything you run is too.
- A user who deliberately uploads their `history.md` to a public location.
- Whisper / Llama hallucinations producing offensive text from your audio. Garbage filter catches most; some slip through.
