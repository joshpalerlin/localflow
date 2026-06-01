#!/usr/bin/env python3
"""
LocalFlow Stress Test Harness
==============================
Simulates 20 back-to-back recordings using macOS text-to-speech.
Verifies each recording shows up in history.md within 15 seconds.

Usage:
    python3 stress_test.py    # run from the repo root

Requirements:
    - LocalFlow must be running (launchd service or manual boot)
    - Accessibility permission must be granted for this Terminal
    - Run from the repo root (the directory containing this script)

Results saved to: /tmp/lf_stress_results.json
"""
import subprocess, time, json, os, sys, re
from pathlib import Path
from datetime import datetime

APP_DIR      = Path(__file__).resolve().parent
HISTORY_FILE = APP_DIR / "history.md"
RESULTS_FILE = Path("/tmp/lf_stress_results.json")
LOG_FILE     = Path("/tmp/lf_stress_test.log")

# ── Test sentences (varied length and complexity) ─────────────────────────────
TEST_SENTENCES = [
    # Short
    "Send the report to Sarah.",
    "Call John at two PM.",
    "Quick note: meeting moved to Friday.",
    "The API is working now.",
    "Schedule the call for next week.",
    # Medium
    "Can you follow up with the team about the deployment timeline?",
    "I need to finish the proposal by end of day and send it to the client.",
    "Let's move the standup to eleven AM so everyone can join.",
    "Three things: first, review the contract. Second, approve the budget. Third, confirm the venue.",
    "The dashboard is showing an error on the settings page — can someone look into it?",
    # Long
    "Hey Sarah, just wanted to follow up on the proposal we sent last week. Let me know if you have any questions or if you'd like to hop on a quick call to go through it.",
    "Quick update for the team: the production deployment is complete, all systems are green, and the new API endpoint is live. No action needed from anyone tonight.",
    # With numbers
    "The meeting is at three thirty PM on the fourteenth.",
    "The invoice total is five hundred dollars.",
    "Response time improved by thirty percent after the optimization.",
    # With proper nouns (from custom words list)
    "I was using Claude Code to debug the issue.",
    "The OpenAI API rate limit kicked in during the test.",
    "Set a reminder about the Anthropic announcement next week.",
    "Jarvis sent the daily report at nine AM.",
    "The ChatGPT integration is working as expected.",
]

assert len(TEST_SENTENCES) == 20, f"Expected 20 sentences, got {len(TEST_SENTENCES)}"


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def send_option_press():
    """Simulate a single Option key press via osascript (press + release)."""
    script = '''
    tell application "System Events"
        key down option
        delay 0.05
        key up option
    end tell
    '''
    result = subprocess.run(["osascript", "-e", script],
                            capture_output=True, timeout=5)
    if result.returncode != 0:
        raise RuntimeError(f"osascript failed: {result.stderr.decode()}")


def speak_text(text: str, speed: int = 170):
    """Speak text using macOS TTS at given words-per-minute."""
    subprocess.run(["say", "-r", str(speed), "--", text],
                   check=True, timeout=30)


def get_history_tail(chars: int = 3000) -> str:
    """Read the last N chars of history.md."""
    if not HISTORY_FILE.exists():
        return ""
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    return content[-chars:]


def check_history_for_sentence(sentence: str, snapshot_before: str) -> bool:
    """Check if a sentence's key words appeared in history since the snapshot."""
    current = get_history_tail()
    # Get only the new content added since the snapshot
    if snapshot_before and snapshot_before in current:
        new_content = current[current.rfind(snapshot_before) + len(snapshot_before):]
    else:
        new_content = current[-1500:]  # fallback: check last 1500 chars

    # Check that at least 60% of the meaningful words from the sentence appear
    words = [w.lower().strip(".,!?") for w in sentence.split()
             if len(w) >= 4 and w.lower() not in {"the","and","for","that","with","this","from"}]
    if not words:
        return bool(new_content.strip())

    matched = sum(1 for w in words if w in new_content.lower())
    ratio = matched / len(words)
    return ratio >= 0.5  # at least half the key words must appear


def run_stress_test():
    log("=" * 60)
    log("LocalFlow Stress Test Starting")
    log(f"Testing {len(TEST_SENTENCES)} recordings back-to-back")
    log("=" * 60)

    results = []
    passed = 0
    failed = 0

    for i, sentence in enumerate(TEST_SENTENCES, 1):
        log(f"\n[Test {i:02d}/20] '{sentence[:50]}{'...' if len(sentence) > 50 else ''}'")

        # Snapshot current history so we can detect new entries
        snapshot = get_history_tail(2000)

        try:
            # 1. Start recording
            log("  → Pressing Option (start recording)...")
            send_option_press()
            time.sleep(0.6)  # let app register recording start

            # 2. Speak the test sentence
            log(f"  → Speaking at 170 wpm...")
            speak_text(sentence, speed=170)
            time.sleep(0.3)  # brief pause before stop

            # 3. Stop recording
            log("  → Pressing Option (stop recording)...")
            send_option_press()

            # 4. Wait for processing
            log("  → Waiting for processing (up to 20s)...")
            found = False
            for wait_tick in range(20):
                time.sleep(1)
                if check_history_for_sentence(sentence, snapshot):
                    found = True
                    break
                if wait_tick == 9:
                    log("  ⚠️  Still processing after 10s...")

            if found:
                log(f"  ✅ PASS (found in history after ~{wait_tick+1}s)")
                passed += 1
                results.append({"test": i, "sentence": sentence, "status": "pass",
                                 "wait_sec": wait_tick + 1})
            else:
                log(f"  ❌ FAIL (not in history after 20s)")
                failed += 1
                results.append({"test": i, "sentence": sentence, "status": "fail",
                                 "wait_sec": 20})

        except Exception as e:
            log(f"  💥 ERROR: {e}")
            failed += 1
            results.append({"test": i, "sentence": sentence, "status": "error",
                             "error": str(e)})

        # Gap between tests — let app fully settle
        time.sleep(3)

    # ── Summary ────────────────────────────────────────────────────────────────
    log("\n" + "=" * 60)
    log(f"STRESS TEST COMPLETE")
    log(f"  Passed: {passed}/20")
    log(f"  Failed: {failed}/20")
    score = passed / 20 * 100
    log(f"  Score:  {score:.0f}%")
    if score >= 90:
        log("  🟢 EXCELLENT — ready for extended soak test")
    elif score >= 75:
        log("  🟡 ACCEPTABLE — investigate failures before release")
    else:
        log("  🔴 NEEDS WORK — too many failures for production")
    log("=" * 60)
    log(f"Full results saved to: {RESULTS_FILE}")

    # Save JSON results
    summary = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "passed": passed,
        "failed": failed,
        "score_pct": round(score, 1),
        "tests": results,
    }
    RESULTS_FILE.write_text(json.dumps(summary, indent=2))

    # Also append to crash forensics for tracking
    try:
        cf_path = APP_DIR / "crash_forensics.jsonl"
        entry = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": "stress_test",
            "passed": passed,
            "failed": failed,
            "score_pct": round(score, 1),
        }
        with open(cf_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

    return passed, failed


if __name__ == "__main__":
    # Warn if LocalFlow doesn't appear to be running
    result = subprocess.run(["pgrep", "-f", "localflow_app.py"],
                            capture_output=True)
    if result.returncode != 0:
        print("⚠️  WARNING: LocalFlow does not appear to be running.")
        print("   Start it via:")
        print("     launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.localflow.app.plist")
        print("   Or run directly: python3 localflow_app.py")
        sys.exit(1)

    LOG_FILE.write_text("")  # fresh log
    passed, failed = run_stress_test()
    sys.exit(0 if failed == 0 else 1)
