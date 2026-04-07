#!/usr/bin/env python3
"""
LocalFlow — Free local WhisperFlow replacement
Hold Option key to record, release to transcribe and auto-type.
100% local, zero cloud, zero cost.
"""

import os
import sys
import re
import time
import tempfile
import threading
import subprocess
import numpy as np
import sounddevice as sd
import soundfile as sf
import pyautogui
from pynput import keyboard

# ── Config ────────────────────────────────────────────────────────────────────
WHISPER_BIN    = "/opt/homebrew/bin/whisper"
WHISPER_MODEL  = "base"          # tiny | base | small | medium | large
SAMPLE_RATE    = 16000
CHANNELS       = 1
HOTKEY         = keyboard.Key.alt  # Option key — change to e.g. keyboard.Key.ctrl
ENABLE_CLEANUP = True              # Rule-based filler word removal

# Filler words to strip (case-insensitive, whole-word match)
FILLERS = [
    r"\bum+\b", r"\buh+\b", r"\blike\b", r"\byou know\b",
    r"\bbasically\b", r"\bactually\b", r"\bso\b", r"\bright\b",
    r"\bokay so\b", r"\balright\b", r"\bi mean\b", r"\bkind of\b",
    r"\bsort of\b", r"\bliterally\b",
]

# ── State ─────────────────────────────────────────────────────────────────────
recording      = False
audio_frames   = []
stream         = None
lock           = threading.Lock()

# ── Audio ─────────────────────────────────────────────────────────────────────
def audio_callback(indata, frames, time_info, status):
    with lock:
        if recording:
            audio_frames.append(indata.copy())

def start_recording():
    global recording, audio_frames, stream
    with lock:
        audio_frames = []
        recording = True
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="float32",
        callback=audio_callback,
    )
    stream.start()
    print("🎤 Recording...", flush=True)

def stop_recording():
    global recording, stream
    with lock:
        recording = False
    if stream:
        stream.stop()
        stream.close()
        stream = None
    print("⏹  Stopped.", flush=True)

# ── Transcription ─────────────────────────────────────────────────────────────
def transcribe(wav_path: str) -> str:
    result = subprocess.run(
        [WHISPER_BIN, wav_path, "--model", WHISPER_MODEL,
         "--output_format", "txt", "--output_dir", os.path.dirname(wav_path),
         "--fp16", "False", "--language", "en"],
        capture_output=True, text=True
    )
    txt_path = wav_path.replace(".wav", ".txt")
    if os.path.exists(txt_path):
        text = open(txt_path).read().strip()
        os.remove(txt_path)
        return text
    # fallback: parse stdout
    for line in result.stdout.splitlines():
        line = line.strip()
        if line and not line.startswith("["):
            return line
    return ""

# ── Cleanup ───────────────────────────────────────────────────────────────────
def clean_text(text: str) -> str:
    if not ENABLE_CLEANUP or not text:
        return text

    # Strip leading timestamps if whisper added them
    text = re.sub(r"^\[.*?\]\s*", "", text).strip()

    # Remove filler words
    for filler in FILLERS:
        text = re.sub(filler, "", text, flags=re.IGNORECASE)

    # Clean up whitespace and punctuation artifacts
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([.,!?;:])", r"\1", text)
    text = text.strip()

    # Capitalize first letter
    if text:
        text = text[0].upper() + text[1:]

    # Ensure sentence ends with punctuation
    if text and text[-1] not in ".!?":
        text += "."

    return text

# ── Auto-type ─────────────────────────────────────────────────────────────────
def type_text(text: str):
    if not text:
        print("⚠️  Nothing to type.", flush=True)
        return
    print(f"✅ Typing: {text}", flush=True)
    # Small pause to let the key release propagate before typing
    time.sleep(0.15)
    pyautogui.typewrite(text, interval=0.01)

# ── Main flow ─────────────────────────────────────────────────────────────────
def process_recording():
    with lock:
        frames = list(audio_frames)

    if not frames:
        print("⚠️  No audio captured.", flush=True)
        return

    audio = np.concatenate(frames, axis=0)

    # Need at least 0.5s of audio
    if len(audio) < SAMPLE_RATE * 0.5:
        print("⚠️  Recording too short.", flush=True)
        return

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name

    sf.write(wav_path, audio, SAMPLE_RATE)
    print(f"💾 Saved audio to {wav_path}", flush=True)

    print("🤖 Transcribing...", flush=True)
    raw = transcribe(wav_path)
    os.remove(wav_path)

    if not raw:
        print("⚠️  No speech detected.", flush=True)
        return

    print(f"📝 Raw: {raw}", flush=True)
    cleaned = clean_text(raw)
    print(f"✨ Clean: {cleaned}", flush=True)

    type_text(cleaned)

# ── Hotkey listener ───────────────────────────────────────────────────────────
hotkey_held = False

def on_press(key):
    global hotkey_held
    if key == HOTKEY and not hotkey_held:
        hotkey_held = True
        start_recording()

def on_release(key):
    global hotkey_held
    if key == HOTKEY and hotkey_held:
        hotkey_held = False
        stop_recording()
        threading.Thread(target=process_recording, daemon=True).start()

# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("  LocalFlow — Free Local WhisperFlow")
    print("=" * 50)
    print(f"  Hotkey  : Option (alt) key")
    print(f"  Model   : {WHISPER_MODEL}")
    print(f"  Cleanup : {'on' if ENABLE_CLEANUP else 'off'}")
    print()
    print("  Hold Option → speak → release → text types!")
    print("  Press Ctrl+C to quit.")
    print("=" * 50)

    # Check whisper binary
    if not os.path.exists(WHISPER_BIN):
        print(f"\n❌ Whisper not found at {WHISPER_BIN}")
        print("   Install with: pip install openai-whisper")
        sys.exit(1)

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        try:
            listener.join()
        except KeyboardInterrupt:
            print("\n👋 LocalFlow stopped.")

if __name__ == "__main__":
    main()
