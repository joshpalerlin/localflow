import sys, os
# Ensure venv packages are always importable regardless of how we're launched
_venv_site = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "venv", "lib", "python3.14", "site-packages")
if os.path.isdir(_venv_site) and _venv_site not in sys.path:
    sys.path.insert(0, _venv_site)

import rumps
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import json
import time
import os
import re
import subprocess
import tempfile
import warnings

# ── Model cache: pin to LocalFlow's own folder ─────────────────────────────────
# HuggingFace's default cache (~/.cache/huggingface) is shared across all Python
# apps and can be wiped by disk-cleanup tools or HF's own LRU eviction.  Pin our
# cache to a folder LocalFlow owns so external cleanups can never break us.
# Done BEFORE the WhisperModel import below so the env vars take effect on the
# first load call.  setdefault preserves any HF_HOME already set by the user.
_LOCALFLOW_MODELS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "models"
)
os.makedirs(_LOCALFLOW_MODELS_DIR, exist_ok=True)
os.environ.setdefault("HF_HOME", _LOCALFLOW_MODELS_DIR)
os.environ.setdefault("HUGGINGFACE_HUB_CACHE",
                      os.path.join(_LOCALFLOW_MODELS_DIR, "hub"))

from pathlib import Path
from pynput import keyboard
import sounddevice as sd
import soundfile as sf
import numpy as np
from faster_whisper import WhisperModel

warnings.filterwarnings("ignore")

# ── Network probe ──────────────────────────────────────────────────────────────
def is_online(timeout: float = 1.0) -> bool:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("8.8.8.8", 53))
        s.close()
        return True
    except Exception:
        return False

# ── Paths ──────────────────────────────────────────────────────────────────────
APP_DIR     = Path(__file__).parent
CONFIG_FILE = APP_DIR / "config.json"

DEFAULT_CONFIG = {
    "trigger_key":    "alt",           # alt | cmd | ctrl
    "whisper_model":  "small.en",      # small.en (English) | small (multilingual)
    "language":       "en",            # en | auto | zh | fr | de | ja | ko | pt | hi | es ...
    "custom_words":   [],              # personal dictionary → fed to Whisper initial_prompt
    "snippets":       {},              # {"trigger phrase": "full expansion text"}
}

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text())
            cfg = {**DEFAULT_CONFIG, **saved}
            # V1 Lock: Force English regardless of saved state
            cfg["language"] = "en"
            # Strip legacy keys removed in V1
            for _legacy in ("hotkey_mode", "use_cloud_llm", "groq_api_key", "tone_mode"):
                cfg.pop(_legacy, None)
            return cfg
        except Exception:
            pass
    CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    return DEFAULT_CONFIG.copy()

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

# ── Key mapping ────────────────────────────────────────────────────────────────
KEY_MAP = {
    "alt":  {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r},
    "cmd":  {keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r},
    "ctrl": {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r},
}

# ── NSEvent modifier monitoring ────────────────────────────────────────────────
try:
    from AppKit import NSEvent as _NSEvent
    _HAS_NSEVENT = True
except ImportError:
    _HAS_NSEVENT = False

# ── CGEventTap backup (catches modifier events NSEvent may silently drop) ──────
# CGEventTap is lower-level than NSEvent monitors — it taps directly into the
# event stream before AppKit processes it.  Used as a backup to guarantee
# toggle-stop detection even when macOS drops FlagsChanged events.
try:
    from Quartz import (
        CGEventTapCreate       as _CGTapCreate,
        CGEventTapEnable       as _CGTapEnable,
        CGEventGetFlags        as _CGGetFlags,
        CGEventMaskBit         as _CGMaskBit,
        CFMachPortCreateRunLoopSource as _CFMachPortSource,
        CFRunLoopAddSource     as _CFRunLoopAdd,
        CFRunLoopGetCurrent    as _CFRunLoopCurrent,
        CFRunLoopRun           as _CFRunLoopRun,
        CFRunLoopStop          as _CFRunLoopStop,
        CFMachPortInvalidate   as _CFMachPortInvalidate,
        kCGSessionEventTap,
        kCGHeadInsertEventTap,
        kCGEventTapOptionListenOnly,
        kCGEventFlagsChanged,
        kCFRunLoopCommonModes,
    )
    _HAS_CGTAP = True
except ImportError:
    _HAS_CGTAP = False

_NS_MOD_FLAGS = {
    "alt":  0x80000,   # NSAlternateKeyMask
    "cmd":  0x100000,  # NSCommandKeyMask
    "ctrl": 0x40000,   # NSControlKeyMask
}
_NS_MOD_KEYS = {
    "alt":  keyboard.Key.alt,
    "cmd":  keyboard.Key.cmd,
    "ctrl": keyboard.Key.ctrl,
}
_NS_CMD_MASK = 0x100000  # NSCommandKeyMask — used to detect Command Mode

# ── App context (PyObjC NSWorkspace) ──────────────────────────────────────────
_APP_CONTEXT_MAP = {
    "com.tinyspeck.slackmacgap":              "Slack (casual team chat)",
    "com.apple.mail":                         "Apple Mail (email, formal)",
    "com.microsoft.Outlook":                  "Outlook (email, formal)",
    "ru.keepcoder.Telegram":                  "Telegram (casual messaging)",
    "com.apple.MobileSMS":                    "iMessage (casual messaging)",
    "com.apple.Notes":                        "Apple Notes (personal notes)",
    "com.microsoft.VSCode":                   "VS Code (code editor — use plain text, code syntax, no fancy formatting)",
    "com.todesktop.230313mzl4w4u92":          "Cursor (code editor — use plain text, code syntax)",
    "com.openai.chat":                        "ChatGPT (AI prompt — be concise and clear)",
    "com.anthropic.claudefordesktop":         "Claude (AI prompt — be concise and clear)",
    "com.google.Chrome":                      "web browser",
    "com.apple.Safari":                       "Safari browser",
    "com.notion.id":                          "Notion (docs/notes)",
    "com.superhuman.chat":                    "Superhuman (email, professional)",
    "com.hnc.Discord":                        "Discord (casual chat)",
    "com.basecamp.basecamp3":                 "Basecamp (team project management)",
}

def get_frontmost_app_context() -> str:
    try:
        from AppKit import NSWorkspace
        app  = NSWorkspace.sharedWorkspace().frontmostApplication()
        bid  = app.bundleIdentifier() or ""
        name = app.localizedName() or ""
        return _APP_CONTEXT_MAP.get(bid, name)
    except Exception:
        return ""

# ── Microphone TCC permission ─────────────────────────────────────────────────
def _check_accessibility_and_prompt() -> bool:
    """
    Check if process has Accessibility permission.
    - First launch: show macOS permission dialog (prompt=True) so user can grant access.
    - Subsequent launches: silent check (prompt=False). If not trusted, show a quiet
      notification instead of the intrusive macOS popup + System Settings window.
    Returns True if already trusted.
    """
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        from CoreFoundation import CFDictionaryCreate, kCFTypeDictionaryKeyCallBacks, kCFTypeDictionaryValueCallBacks
        import objc

        # Determine if this is the first launch (never prompted before)
        cfg = load_config()
        has_prompted = cfg.get("_accessibility_prompted", False)

        # First launch → prompt=True (shows macOS dialog). After that → prompt=False (silent).
        should_prompt = not has_prompted

        prompt_key = objc.lookUpClass('NSString').stringWithUTF8String_(b"AXTrustedCheckOptionPrompt")
        bool_val   = objc.lookUpClass('NSNumber').numberWithBool_(should_prompt)
        opts = CFDictionaryCreate(
            None,
            [prompt_key], [bool_val], 1,
            kCFTypeDictionaryKeyCallBacks,
            kCFTypeDictionaryValueCallBacks,
        )
        trusted = bool(AXIsProcessTrustedWithOptions(opts))

        # Mark that we've prompted at least once (so future restarts stay silent)
        if should_prompt and not has_prompted:
            cfg["_accessibility_prompted"] = True
            save_config(cfg)

        if not trusted:
            if should_prompt:
                print("[Accessibility] NOT trusted — first-launch prompt shown.", flush=True)
            else:
                print("[Accessibility] NOT trusted — silent check (already prompted before).", flush=True)
                # Quiet notification instead of intrusive popup
                try:
                    import rumps
                    rumps.notification(
                        "LocalFlow",
                        "Accessibility Required",
                        "Toggle 'LocalFlow-python' ON in System Settings → Privacy → Accessibility.",
                    )
                except Exception:
                    pass
        else:
            print("[Accessibility] Trusted ✓", flush=True)
        return trusted
    except Exception as e:
        print(f"[Accessibility] Check failed: {e}", flush=True)
        return True  # Don't block startup if check itself errors

def _request_mic_permission():
    import ctypes, threading as _threading
    try:
        libobjc = ctypes.CDLL('/usr/lib/libobjc.dylib')
        ctypes.CDLL('/System/Library/Frameworks/AVFoundation.framework/AVFoundation')
        libobjc.objc_getClass.restype   = ctypes.c_void_p
        libobjc.objc_getClass.argtypes  = [ctypes.c_char_p]
        libobjc.sel_registerName.restype  = ctypes.c_void_p
        libobjc.sel_registerName.argtypes = [ctypes.c_char_p]
        NSString   = libobjc.objc_getClass(b"NSString")
        sel_str    = libobjc.sel_registerName(b"stringWithUTF8String:")
        libobjc.objc_msgSend.restype  = ctypes.c_void_p
        libobjc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
        media_type = libobjc.objc_msgSend(NSString, sel_str, b"soun")
        AVCaptureDevice = libobjc.objc_getClass(b"AVCaptureDevice")
        sel_auth = libobjc.sel_registerName(b"authorizationStatusForMediaType:")
        libobjc.objc_msgSend.restype  = ctypes.c_long
        libobjc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        status = libobjc.objc_msgSend(AVCaptureDevice, sel_auth, media_type)
        print(f"[Mic] TCC status: {status}", flush=True)
        if status == 3:
            print("[Mic] Already authorized.", flush=True)
            return
        if status in (1, 2):
            return
        event       = _threading.Event()
        granted_box = [False]
        INVOKE_FN   = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_ubyte)
        def _invoke(_block, granted):
            granted_box[0] = bool(granted)
            event.set()
        invoke_fn = INVOKE_FN(_invoke)
        class _BlockDescriptor(ctypes.Structure):
            _fields_ = [("reserved", ctypes.c_ulong), ("size", ctypes.c_ulong)]
        class _Block(ctypes.Structure):
            _fields_ = [("isa", ctypes.c_void_p), ("flags", ctypes.c_int),
                        ("reserved_", ctypes.c_int), ("invoke", ctypes.c_void_p),
                        ("descriptor", ctypes.c_void_p)]
        descriptor       = _BlockDescriptor(0, ctypes.sizeof(_Block))
        block            = _Block()
        nscgb            = ctypes.c_void_p.in_dll(libobjc, "_NSConcreteGlobalBlock")
        block.isa        = nscgb.value
        block.flags      = (1 << 28)
        block.reserved_  = 0
        block.invoke     = ctypes.cast(invoke_fn, ctypes.c_void_p).value
        block.descriptor = ctypes.cast(ctypes.pointer(descriptor), ctypes.c_void_p).value
        sel_req = libobjc.sel_registerName(b"requestAccessForMediaType:completionHandler:")
        libobjc.objc_msgSend.restype  = None
        libobjc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.c_void_p, ctypes.c_void_p]
        libobjc.objc_msgSend(AVCaptureDevice, sel_req, media_type, ctypes.byref(block))
        event.wait(timeout=30)
        print(f"[Mic] Permission {'GRANTED' if granted_box[0] else 'DENIED'}", flush=True)
    except Exception:
        import traceback
        print(f"[Mic] Error:\n{traceback.format_exc()}", flush=True)

# ── Models ─────────────────────────────────────────────────────────────────────
_whisper_model = None
_current_model_name = None
_mlx_model     = None
_mlx_tokenizer = None

SAMPLE_RATE = 16000
CHANNELS    = 1

LLM_MODEL_ID  = "mlx-community/Llama-3.2-1B-Instruct-4bit"

_loading_model = False   # True while downloading/loading models — watchdog backs off

def _verify_models_on_disk() -> tuple[bool, list]:
    """Check that both Whisper and MLX models exist in the local cache.
    Returns (all_present, list_of_missing).  Called at boot for early warning —
    if missing, user sees a notification BEFORE pressing the hotkey instead of
    discovering it the hard way.
    """
    hub_dir = os.path.join(_LOCALFLOW_MODELS_DIR, "hub")
    targets = {
        "Whisper": "models--Systran--faster-whisper-small.en",
        "MLX Brain": "models--mlx-community--Llama-3.2-1B-Instruct-4bit",
    }
    missing = []
    for label, folder in targets.items():
        path = os.path.join(hub_dir, folder)
        snapshots = os.path.join(path, "snapshots")
        if not os.path.isdir(snapshots):
            missing.append(label)
            continue
        # snapshots dir must contain at least one revision folder with files
        try:
            revs = [r for r in os.scandir(snapshots) if r.is_dir()]
            if not revs or not any(os.scandir(r.path) for r in revs):
                missing.append(label)
        except Exception:
            missing.append(label)
    return (len(missing) == 0, missing)

def load_models(whisper_model_name: str):
    global _whisper_model, _mlx_model, _mlx_tokenizer, _current_model_name, _loading_model
    _loading_model = True

    # Integrity check — warn the user if models are missing before downloading
    ok, missing = _verify_models_on_disk()
    if not ok:
        msg = f"Missing: {', '.join(missing)} — re-downloading from HuggingFace"
        print(f"[Models] {msg}", flush=True)
        try:
            rumps.notification("LocalFlow", "⚠️ Models missing", msg)
        except Exception:
            pass

    online = is_online()
    if not online:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)
    print(f"Loading Whisper '{whisper_model_name}'...", flush=True)
    try:
        _whisper_model = WhisperModel(whisper_model_name, device="cpu", compute_type="int8")
        _current_model_name = whisper_model_name
    except Exception as e:
        print(f"[Whisper] Load failed: {e}", flush=True)
    if _mlx_model is None:
        print("Loading MLX Brain...", flush=True)
        try:
            from mlx_lm import load as mlx_load
            _mlx_model, _mlx_tokenizer = mlx_load(LLM_MODEL_ID)
        except Exception as e:
            print(f"[MLX] Skipping: {e}", flush=True)
    _loading_model = False
    print("✅ Models ready! Press your trigger key to start dictating.", flush=True)

# ── Personal dictionary → Whisper initial_prompt ──────────────────────────────
def _build_whisper_prompt(cfg: dict) -> str:
    # CRITICAL: Do NOT include custom_words in initial_prompt.
    # Whisper's language model over-applies the hint list and substitutes similar-sounding
    # common words with proper nouns from the list — e.g. "topic" → "Anthropic".
    # Custom word correction is handled deterministically AFTER transcription by
    # _apply_custom_words(), which uses fuzzy phonetic matching with proper thresholds.
    return "Clean natural speech. Use natural punctuation. No filler words. Use digits for numbers."

# ── Voice quote markers (deterministic, runs BEFORE the LLM) ─────────────────
# Spoken patterns the user can dictate to insert real quote marks:
#   "quote-unquote premium research"   → "premium research"   (sarcastic/scare quotes)
#   "quote unquote premium research"   → "premium research"   (same, no hyphen)
#   "quote premium research unquote"   → "premium research"   (explicit closing)
#   "open quote X close quote"         → "X"                  (formal)
#
# For sarcastic mode (no explicit closing), the phrase ends at the next
# preposition/conjunction/auxiliary verb — these never start a noun phrase,
# so they're reliable stop signals.  Captures 1-3 words max.
_QUOTE_STOP = (
    r"(?:to|of|and|or|but|with|for|from|in|on|at|by|into|onto|"
    r"that|which|who|when|where|while|as|like|than|then|so|because|if|"
    r"compared|versus|vs|via|"
    r"is|are|was|were|has|have|will|would|can|could|may|might|should|"
    r"shall|do|does|did|been|being|am)"
)
_VOICE_QUOTE_SARCASTIC_RE = re.compile(
    r"\bquote[\s\-]+unquote[\s,]+"
    r"(\w+"                                      # first word always captured
    rf"(?:\s+(?!{_QUOTE_STOP}\b)\w+){{0,2}})",   # next 0-2 words if not stops
    re.IGNORECASE
)
_VOICE_QUOTE_BRACKETED_RE = re.compile(
    r"\bquote[\s,]+(.+?)[\s,]+unquote\b",
    re.IGNORECASE
)
_VOICE_QUOTE_OPENCLOSE_RE = re.compile(
    r"\bopen[\s\-]+quote[\s,]+(.+?)[\s,]+close[\s\-]+quote\b",
    re.IGNORECASE
)

def _apply_voice_quote_marks(text: str) -> str:
    """Convert spoken quote markers into real quote characters.

    Order matters: bracketed patterns first (most specific), then sarcastic.
    """
    # "open quote X close quote" — most specific, formal
    text = _VOICE_QUOTE_OPENCLOSE_RE.sub(r'"\1"', text)
    # "quote X unquote" — explicit bracketing
    text = _VOICE_QUOTE_BRACKETED_RE.sub(r'"\1"', text)
    # "quote unquote X" / "quote-unquote X" — sarcastic, leading
    text = _VOICE_QUOTE_SARCASTIC_RE.sub(r'"\1"', text)
    return text

# ── Self-correction post-processor (deterministic, runs BEFORE the LLM) ──────
_SELFCORRECT_TRIGGERS = [
    r"wait,?\s*no",
    r"scratch\s+that",
    r"sorry,?\s*(?:i\s+meant)?",
    r"i\s+mean",
    r"no\s+wait",
    r"correction",
    r"strike\s+that",
]
# Sentence-level: a whole sentence that's nothing but trigger phrases.
# Matches single triggers ("Wait no.") AND chained triggers ("Actually, scratch that.")
_SELFCORRECT_SENT_RE = re.compile(
    r"^\s*(?:(?:" + "|".join(_SELFCORRECT_TRIGGERS) + r")[\s,.!?]*)+\s*$",
    re.IGNORECASE
)
# Inline: trigger embedded mid-sentence ("send to John, wait no, send to Sarah")
_SELFCORRECT_INLINE_RE = re.compile(
    r"(?i)(.+?)[,\s]+(?:" + "|".join(_SELFCORRECT_TRIGGERS) + r")[,\s]+(.+)"
)
# Leading: trigger at the START of a sentence followed by a comma ("Wait no, Wednesday at 4 PM.")
# Comma requirement prevents false positives on adverbial use like "Actually I think Monday is better."
_SELFCORRECT_LEADING_RE = re.compile(
    r"^\s*(?:(?:" + "|".join(_SELFCORRECT_TRIGGERS) + r")\s*,\s*)+(.+)$",
    re.IGNORECASE
)

def _apply_self_corrections(text: str) -> str:
    """
    Detect spoken self-corrections and rewrite to drop the mistake.

    Two cases handled:
      A. INLINE — trigger inside a single sentence:
         "send it to John, wait no, send it to Sarah" → "send it to Sarah"

      B. CROSS-SENTENCE — Whisper splits the trigger into its own sentence:
         "Send to John on Tuesday. Wait no. Send to Sarah on Wednesday."
         → "Send to Sarah on Wednesday."
         (drop previous sentence + the trigger sentence, keep what follows)
    """
    if not text or len(text) < 10:
        return text

    sentences = re.split(r'(?<=[.!?])\s+', text)
    out = []
    for sent in sentences:
        # Case B: this sentence is JUST a trigger → drop previous + skip this
        if _SELFCORRECT_SENT_RE.match(sent):
            if out:
                out.pop()
            continue
        # Case C: trigger at start of sentence with comma ("Wait no, X.") → drop previous, keep X
        m = _SELFCORRECT_LEADING_RE.match(sent)
        if m:
            if out:
                out.pop()
            after = m.group(1).strip()
            if after:
                after = after[0].upper() + after[1:]
            out.append(after)
            continue
        # Case A: inline trigger within this sentence
        m = _SELFCORRECT_INLINE_RE.match(sent)
        if m:
            after = m.group(2).strip()
            if after:
                after = after[0].upper() + after[1:]
            out.append(after)
        else:
            out.append(sent)
    return " ".join(out)

# ── Audio normalization ───────────────────────────────────────────────────────
def _normalize_audio(audio: np.ndarray, target_rms: float = 0.08) -> np.ndarray:
    """Normalize audio amplitude to a consistent RMS level before passing to Whisper.

    Whisper transcription quality degrades significantly on very quiet or very loud
    audio. Normalizing to a consistent RMS of ~0.08 (roughly -22 dBFS) keeps the
    signal in Whisper's sweet spot.

    Safety caps:
    - Won't boost near-silence (< 0.001 RMS) — silence stays silent.
    - Max 8x gain — avoids amplifying background noise into the main signal.
    - Clips to [-1, 1] to prevent float overflow artifacts.
    """
    rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
    if rms < 0.001:
        return audio   # near-silence — don't boost
    scale = min(target_rms / rms, 8.0)
    normalized = (audio * scale).clip(-1.0, 1.0)
    print(f"[Audio] normalize: rms={rms:.4f} → scale={scale:.2f}x", flush=True)
    return normalized

# ── Crash forensics: structured flight-data recorder ─────────────────────────
_APP_START_TIME = time.time()

def _log_crash_event(crash_type: str, **kwargs):
    """Append a structured crash/freeze event to crash_forensics.jsonl.

    Every crash, freeze, timeout, and audio-loss event is logged here so we can
    diagnose patterns across sessions without relying on the raw text log.
    """
    try:
        log_path = APP_DIR / "crash_forensics.jsonl"
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "type": crash_type,
            "uptime_sec": int(time.time() - _APP_START_TIME),
        }
        entry.update(kwargs)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"[CrashForensics] {crash_type}: {kwargs}", flush=True)
    except Exception as e:
        print(f"[CrashForensics] log failed: {e}", flush=True)

# ── Freeze log: track every watchdog-detected stall for dogfood phase ────────
def _log_freeze(kind: str, stuck_for_sec: int):
    """Append a freeze event to ~/localflow/freeze-log.md. Used during 5-day dogfood
    to verify the app is freeze-free before pushing to GitHub."""
    try:
        log_path = APP_DIR / "freeze-log.md"
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            front_app = get_frontmost_app_context()
        except Exception:
            front_app = "unknown"
        line = f"- {ts} | kind={kind} | stuck={stuck_for_sec}s | front_app={front_app}\n"
        if not log_path.exists():
            log_path.write_text("# LocalFlow Freeze Log\n\n"
                                "Every entry = a watchdog-detected stall.\n"
                                "Goal: 5 consecutive days with zero entries before public release.\n\n")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[FreezeLog] error: {e}")

_RECORDING_WATCHDOG_MIN_SEC = 60
_RECORDING_WATCHDOG_NO_AUDIO_SEC = 15
_MAX_RECORDING_SEC = 15 * 60
_MAX_PROCESSING_AUDIO_SEC = 15 * 60
_OVERSIZE_AUDIO_RECOVERY_SEC = 3 * 60

def _recording_watchdog_should_abort_oversize(elapsed_sec: float) -> bool:
    """Abort impossible/stale toggle recordings before they become multi-hour buffers."""
    return elapsed_sec > _MAX_RECORDING_SEC

def _trim_oversize_audio_for_processing(audio_np, sample_rate: int = SAMPLE_RATE):
    """Keep the most recent audio if a stale recording buffer reached processing.

    This is a recovery path for missed stop events.  It prevents Whisper from
    chewing on hours of ambient audio while still preserving the latest speech,
    which is usually the recording Josh just tried to finish.
    """
    audio_dur = len(audio_np) / sample_rate if sample_rate else 0
    if audio_dur <= _MAX_PROCESSING_AUDIO_SEC:
        return audio_np, False, audio_dur
    keep_samples = int(sample_rate * _OVERSIZE_AUDIO_RECOVERY_SEC)
    if keep_samples <= 0:
        return audio_np, False, audio_dur
    return audio_np[-keep_samples:], True, audio_dur

def _recording_watchdog_should_recover(elapsed_sec: float, frame_count: int,
                                       last_frame_count: int,
                                       seconds_since_audio_progress: float,
                                       has_stream: bool) -> bool:
    """Recover only recordings that are truly stuck, not merely long.

    Toggle mode means Josh can intentionally speak for more than a minute.
    A long recording is only "stuck" if the mic stream is gone or audio frames
    have stopped arriving.
    """
    if elapsed_sec <= _RECORDING_WATCHDOG_MIN_SEC:
        return False
    if not has_stream:
        return True
    if frame_count <= last_frame_count and seconds_since_audio_progress >= _RECORDING_WATCHDOG_NO_AUDIO_SEC:
        return True
    return False

# ── Garbage detector: skip MLX when raw Whisper output is hallucinated ───────
def _is_likely_garbage(raw: str, audio_dur: float) -> tuple:
    """
    Detect when Whisper hallucinated on noisy audio. Conservative — needs 2+
    independent signals to flag, to avoid false positives on legit short speech.

    Returns (is_garbage: bool, reason: str). Reason is for logs only.

    Failure modes this catches:
    - "Okay, this is... oop, safety. Sir, hotel to zero, by the path."
      (ellipsis + interjection + heavy fragmentation)
    - 9 seconds of audio → "Tested." (severe word/duration mismatch)
    - "[BLANK_AUDIO]" or similar Whisper internal markers leaking through

    What it does NOT flag (legit):
    - "Yes." or "No." (short audio, short response — ratio rule skips < 3s)
    - "Send the proposal to Sarah by Friday." (clean sentence, no markers)
    - "I think... maybe Friday works." (one ellipsis only, 1 signal not enough)
    """
    if not raw:
        return False, ""

    signals = []

    # Signal 1: Ellipsis = Whisper uncertainty marker
    if "..." in raw:
        signals.append("ellipsis")

    # Signal 2: Hallucinated interjections / Whisper internal tokens
    interjection_patterns = ["oop,", "oop ", "uh-oh", "[blank_audio]", "(speaking)",
                             "(silence)", "(pause)", "[silence]"]
    raw_lc = raw.lower()
    if any(p in raw_lc for p in interjection_patterns):
        signals.append("interjection")

    # Signal 3: Heavy fragmentation — 3+ short comma chunks (≤3 words each)
    chunks = [c.strip() for c in raw.split(",") if c.strip()]
    if len(chunks) >= 3 and all(len(c.split()) <= 3 for c in chunks):
        signals.append("fragments")

    # Signal 4: Word/duration mismatch — only flag if audio > 3s
    # Normal speech = 2-3 words/sec. Below 0.8 wps for 3+s = Whisper missed most words.
    words = raw.split()
    if audio_dur > 3.0:
        wps = len(words) / audio_dur
        # Extreme mismatch (< 0.4 wps for 3+s) = Whisper basically gave up. Flag alone.
        if wps < 0.4:
            return True, "extreme_word_mismatch"
        if wps < 0.8:
            signals.append("too_few_words")

    # Conservative: require 2+ signals to flag
    if len(signals) >= 2:
        return True, "+".join(signals)

    return False, ""

# ── Custom-words fuzzy pre-correction (deterministic, runs BEFORE the LLM) ────
def _consonant_skeleton(word: str) -> str:
    """Reduce a word to its consonant skeleton for phonetic comparison.

    Normalises common consonant-sound equivalences so that words like
    "kodak" and "codex" produce similar skeletons ("kdk" vs "kdks")
    even though their SequenceMatcher ratio is only 0.40.

    Used as a FALLBACK when text-similarity matching fails — catches
    Whisper brand-name substitutions (Kodak for Codex, etc.).
    """
    w = word.lower()
    # Normalise digraphs / equivalences BEFORE single-char passes
    w = w.replace('ck', 'k').replace('ph', 'f').replace('ght', 't')
    w = w.replace('qu', 'kw').replace('x', 'ks')
    w = w.replace('c', 'k')   # hard-c → k  (codex → kodeks)
    # Strip vowels and silent chars
    return ''.join(ch for ch in w if ch not in 'aeiou')

def _multiword_custom_match_ok(transcribed: str, canonical: str, protected_words: set) -> bool:
    """Guard multi-word custom vocabulary against loose whole-phrase matches.

    Whole-phrase similarity made "local like" look close enough to "Local Flow".
    For multi-word entries, each token must independently be exact or clearly
    phonetically close, and common filler words cannot stand in for brand words.
    """
    trans_tokens = transcribed.lower().split()
    canon_tokens = canonical.lower().split()
    if len(trans_tokens) != len(canon_tokens):
        return False

    import difflib
    # Multi-word context provides strong anchoring, so we DON'T check the
    # local protected_words set here (it's tuned for aggressive single-word
    # protection like blocking "cloud" → "claude").  Example: "Cloud Code"
    # → "Claude Code" is safe because exact-match "Code" anchors the phrase.
    # BUT we still check _COMMON_WORDS — that catches truly ambiguous basic
    # English verbs like "could" → "claude" where the input phrase ("could
    # code") is grammatically valid and shouldn't be reinterpreted.
    for trans_token, canon_token in zip(trans_tokens, canon_tokens):
        if trans_token == canon_token:
            continue
        if trans_token in globals().get("_COMMON_WORDS", set()):
            return False
        len_ratio = min(len(trans_token), len(canon_token)) / max(len(trans_token), len(canon_token))
        ratio = difflib.SequenceMatcher(None, trans_token, canon_token).ratio()
        skel_trans = _consonant_skeleton(trans_token)
        skel_canon = _consonant_skeleton(canon_token)
        skel_ratio = (
            difflib.SequenceMatcher(None, skel_trans, skel_canon).ratio()
            if skel_trans and skel_canon else 0.0
        )
        if len_ratio < 0.55 or (ratio < 0.68 and skel_ratio < 0.82):
            return False
    return True

def _apply_custom_words(text: str, custom_words: list) -> str:
    """
    Fuzzy-match every n-gram in the transcription against the user's custom
    dictionary. Replace close phonetic matches with the canonical spelling.
    Runs BEFORE the LLM so corrections are deterministic even when the local
    LLM is too weak to follow vocabulary instructions.

    Locks replaced positions so single-word entries ("codex") don't re-match
    fragments of an already-replaced multi-word entry ("claude code").
    """
    if not custom_words or not text:
        return text
    import difflib
    sorted_words = sorted(custom_words, key=lambda w: -len(w.split()))
    words = text.split()
    if not words:
        return text
    locked = [False] * len(words)
    canonical_exact = {
        re.sub(r"[^\w\s]+$", "", w).lower()
        for w in custom_words
        if w and len(w.split()) == 1
    }
    acronym_confusions = {
        "glm": {"gom", "g lm", "g l m"},
    }
    protected_common_words = {
        # Already-known false-positive triggers
        "topic", "topics", "anthropic", "agent", "agents", "agency",
        "markdown", "model", "models", "location", "locations",
        # Common UI verbs that phonetic-match "claude" / "codex"
        "click", "clicks", "clicked", "clicking",
        "cloud", "clouds", "clued", "clue", "clues",
        "code", "codes", "coded", "coding", "codec",
        "claim", "claims", "claimed", "claiming",
        "close", "closed", "closes", "closing",
        # Common nouns/verbs that phonetic-match "jarvis"
        "jar", "jars", "jarred", "jarring",
        "java",  # already aliased via snippets, also keep protected
        # Common words that phonetic-match "whoop"
        "whip", "whips", "whipped", "whipping",
        "hoop", "hoops", "wisp",
        # Common words that phonetic-match "hotkey"
        "hockey",
        # Common words that phonetic-match "apify"
        "apply", "applies", "applied", "applying",
        # Common words that phonetic-match "openai"
        "open", "opens", "opened", "opening",
        # Common words that phonetic-match "codex"
        "codecs", "decode", "decodes", "decoded", "decoding",
    }
    for canonical in sorted_words:
        n = len(canonical.split())
        if n < 1 or len(words) < n:
            continue
        # Short single-word canonicals (≤6 chars like "codex", "claude") get exact-match
        # only — fuzzy matching on tiny words has too many false positives (e.g. "code"→"Codex").
        # Longer/multi-word canonicals use ratio-based fuzzy match + length ratio guard.
        is_short_single = (n == 1 and len(canonical) <= 6)
        i = 0
        while i <= len(words) - n:
            if any(locked[i:i+n]):
                i += 1
                continue
            window = " ".join(words[i:i+n])
            stripped = re.sub(r'[^\w\s]+$', '', window)
            trailing = window[len(stripped):]
            # Handle possessive "'s" — strip before comparison, restore after replacement.
            # "openclow's" would otherwise score lower than "openclow" against "openclaw"
            # because the apostrophe and 's' count as non-matching chars.
            possessive = ""
            if stripped.endswith("'s") or stripped.endswith("’s"):
                possessive = stripped[-2:]
                stripped = stripped[:-2]
            stripped_lc = stripped.lower()
            canonical_lc = canonical.lower()

            should_replace = False
            is_exact = (stripped_lc == canonical_lc)
            is_acronym_confusion = (
                canonical_lc in acronym_confusions
                and stripped_lc in acronym_confusions[canonical_lc]
            )
            if is_exact:
                # Exact match — lock the position so other rules don't touch it
                pass
            elif is_acronym_confusion:
                should_replace = True
            elif stripped_lc in canonical_exact:
                # If the transcript already matches a known vocabulary word
                # exactly (e.g. OpenAI), do not let a longer fuzzy word
                # (e.g. OpenClaw) steal it before its own exact pass.
                pass
            elif stripped_lc in protected_common_words:
                pass
            elif is_short_single:
                # Skip fuzzy for short single words (≤6 chars like "codex", "claude", "jarvis").
                # Fuzzy matching on these produces false positives like "code"→"Codex".
                pass
            else:
                # LENGTH RATIO GUARD (primary defence against "topic"→"Anthropic"):
                # Reject match if the input word is much shorter than the canonical.
                # e.g. "topic" (5) vs "anthropic" (9): len_ratio = 0.56 → BLOCKED.
                # This fires before the similarity check, so even a high character-overlap
                # score can't produce a false positive across very different lengths.
                len_ratio = min(len(stripped_lc), len(canonical_lc)) / max(len(stripped_lc), len(canonical_lc))
                if len_ratio >= 0.65:
                    ratio = difflib.SequenceMatcher(None, stripped_lc, canonical_lc).ratio()
                    # Long proper nouns (>7 chars) get a lower threshold — they drift more
                    # phonetically (e.g. "openclouds" → "openclaw") but have fewer false
                    # positives due to their length specificity.
                    threshold = 0.65 if len(canonical_lc) > 7 else 0.78
                    if ratio >= threshold:
                        should_replace = True
                if should_replace and n > 1:
                    should_replace = _multiword_custom_match_ok(
                        stripped_lc, canonical_lc, protected_common_words
                    )

            # PHONETIC FALLBACK: catches brand-name substitutions where text
            # similarity is too low (e.g. "Kodak"→"Codex" ratio=0.40) but the
            # consonant skeleton matches closely ("kdk"≈"kdks" ratio=0.86).
            # Guards: both words >3 chars, first 2 consonants must agree
            # (blocks "hockey"→"hotkey" where "hk"≠"ht"), skeleton ratio ≥0.85.
            if (not should_replace and not is_exact
                    and len(stripped_lc) > 3 and len(canonical_lc) > 3
                    and stripped_lc not in protected_common_words
                    and stripped_lc not in globals().get("_COMMON_WORDS", set())):
                skel_w = _consonant_skeleton(stripped_lc)
                skel_c = _consonant_skeleton(canonical_lc)
                if (skel_w and skel_c and len(skel_w) >= 2 and len(skel_c) >= 2
                        and skel_w[:2] == skel_c[:2]):
                    skel_ratio = difflib.SequenceMatcher(None, skel_w, skel_c).ratio()
                    if skel_ratio >= 0.85:
                        should_replace = True
                if should_replace and n > 1:
                    should_replace = _multiword_custom_match_ok(
                        stripped_lc, canonical_lc, protected_common_words
                    )

            if should_replace:
                replacement = canonical if canonical.isupper() else (
                    canonical.title() if stripped[:1].isupper() else canonical
                )
                words[i:i+n] = [replacement + possessive + trailing]
                locked[i:i+n] = [True]
                i += 1
            elif is_exact:
                locked[i:i+n] = [True] * n
                i += n
            else:
                i += 1
    return " ".join(words)

def _apply_domain_phrase_corrections(text: str) -> str:
    """Fix high-confidence phrase errors for Josh's local workflows.

    These are intentionally contextual.  We should not teach "hang" → "ping"
    globally, but "location hang from the only tracks app" is a clear
    OwnTracks/location-ping dictation miss.
    """
    if not text:
        return text

    text = re.sub(r"\bone[-\s]+time\b", "one-time", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\blocation\s+hang\s+from\s+the\s+only\s+tracks\s+app\b",
        "location ping from the OwnTracks app",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bOwnTracks\s+app\s+that\s+we\s+want\s+you\s+to\s+do\s+right\s+now[.!?]?$",
        "OwnTracks app. Is that what you want me to do right now?",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\brequest\s+and\s+(?:pen|pin|hang)\s+where\s+i\s+am\b",
        "request and ping where I am",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bhome\s+(?:OwnTracks|tracks)\s+ping\b",
        "OwnTracks ping",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\blocal\s+like\s+project\b",
        "local project",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bif\s+not,?\s+then\s+oh\s+good\b",
        "If not, then all good",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(on|in|during|for)\s+the\s+face\s+(\d+)\b",
        r"\1 the phase \2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(or|and)\s+face\s+(\d+)\b",
        r"\1 phase \2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bat\s+the\s+gym\s+and\s+since\s+i\s+cannot\b",
        "at the gym and seems that it cannot",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bYou\s+don't\s+have\s+to\s+fully\s+agree\.\s+You\s+might\s+be\s+wrong\b",
        "You don't have to fully agree. He might be wrong",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bpeople\s+thinking\b",
        "critical thinking",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\brank\s+stone\s+only\b",
        "brainstorm only",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bwhen\s+i\s+lock\s+in\b",
        "when I log in",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bto\s+lock\s+into\s+codex\b",
        "to log into codex",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bshould\s+i\s+take\s+your\s+workout\s+down\b",
        "Should I take pre-workout now",
        text,
        flags=re.IGNORECASE,
    )
    return text

# ── Spoken number / time / currency normalization ───────────────────────────
_NUM_WORDS = {
    'zero':0, 'oh':0, 'one':1, 'two':2, 'three':3, 'four':4, 'five':5,
    'six':6, 'seven':7, 'eight':8, 'nine':9, 'ten':10, 'eleven':11, 'twelve':12,
    'thirteen':13, 'fourteen':14, 'fifteen':15, 'sixteen':16, 'seventeen':17,
    'eighteen':18, 'nineteen':19, 'twenty':20, 'thirty':30, 'forty':40,
    'fifty':50, 'sixty':60, 'seventy':70, 'eighty':80, 'ninety':90,
}
_SCALES = {'hundred':100, 'thousand':1000, 'million':1_000_000, 'billion':1_000_000_000}

def _words_to_int(phrase: str):
    """Convert 'twenty three' → 23, 'one hundred fifty' → 150. Returns None if not parseable."""
    tokens = re.findall(r'[a-z]+', phrase.lower())
    if not tokens or not all(t in _NUM_WORDS or t in _SCALES or t == 'and' for t in tokens):
        return None
    if tokens[0] in _SCALES and tokens[0] != 'hundred':
        return None
    total = 0; current = 0
    used_large_scales = set()
    for t in tokens:
        if t == 'and': continue
        if t in _NUM_WORDS:
            current += _NUM_WORDS[t]
        elif t in _SCALES:
            scale = _SCALES[t]
            if scale == 100:
                current = max(current, 1) * scale
            else:
                if scale in used_large_scales:
                    return None
                used_large_scales.add(scale)
                total += max(current, 1) * scale
                current = 0
    return total + current

def _normalize_numbers(text: str) -> str:
    """Convert spoken numbers, times, currency to digit form."""
    number_words = '|'.join(list(_NUM_WORDS) + list(_SCALES) + ['and'])
    # Currency with digits already: "500 dollars" → "$500", "99.99 dollars" → "$99.99"
    # Must run BEFORE the word-form regex so "500 dollars" is caught first.
    text = re.sub(r'\$?(\d[\d,]*(?:\.\d+)?)\s+(?:dollars?|bucks?)\b', r'$\1', text, flags=re.IGNORECASE)
    # Currency: "twenty bucks", "fifty dollars", "ten bucks"
    def _money(m):
        n = _words_to_int(m.group(1))
        return f"${n}" if n is not None else m.group(0)
    text = re.sub(rf'\b((?:(?:{number_words})\s+){{0,5}}(?:{number_words}))\s+(?:bucks?|dollars?)\b',
                  _money, text, flags=re.IGNORECASE)

    # Times: "three thirty PM", "ten o'clock", "five PM", "twelve fifteen AM"
    def _time(m):
        h = _words_to_int(m.group(1))
        mn = m.group(2)
        meridian = m.group(3).upper().replace('.', '')
        if h is None: return m.group(0)
        if mn:
            mn_num = _words_to_int(mn)
            if mn_num is None or mn_num >= 60: return m.group(0)
            return f"{h}:{mn_num:02d} {meridian}"
        return f"{h} {meridian}"
    text = re.sub(
        r"\b((?:[a-z]+\s+){0,2}[a-z]+)(?:\s+((?:[a-z]+\s+){0,2}[a-z]+))?\s+(a\.?m\.?|p\.?m\.?)\b",
        _time, text, flags=re.IGNORECASE
    )
    # "ten o'clock" → "10 o'clock"
    text = re.sub(r"\b((?:[a-z]+\s+){0,2}[a-z]+)\s+o'?clock\b",
                  lambda m: (lambda n: f"{n} o'clock" if n is not None else m.group(0))(_words_to_int(m.group(1))),
                  text, flags=re.IGNORECASE)

    # LLM-converted time fix: "3.30 pm" → "3:30 PM" (European format → US format, uppercase meridian)
    # Also handles "3.30pm" (no space), "3:30pm" (lowercase meridian).
    text = re.sub(
        r'\b(\d{1,2})[.:](\d{2})\s*([ap])\.?m\.?\b',
        lambda m: f"{int(m.group(1))}:{m.group(2)} {m.group(3).upper()}M",
        text, flags=re.IGNORECASE
    )
    # Standalone "3 pm" / "3pm" → "3 PM" (insert space, uppercase meridian)
    text = re.sub(
        r'\b(\d{1,2})\s*([ap])\.?m\.?\b',
        lambda m: f"{m.group(1)} {m.group(2).upper()}M",
        text, flags=re.IGNORECASE
    )

    # Percent: "twenty percent" → "20%"
    text = re.sub(r'\b((?:[a-z]+\s+){0,3}[a-z]+)\s+percent\b',
                  lambda m: (lambda n: f"{n}%" if n is not None else m.group(0))(_words_to_int(m.group(1))),
                  text, flags=re.IGNORECASE)

    # Generic standalone large numbers: "two hundred fifty" → "250" (≥21 only — keep "five" as "five")
    def _big(m):
        n = _words_to_int(m.group(0))
        return str(n) if n is not None and n >= 21 else m.group(0)
    text = re.sub(r'\b(?:(?:'+ '|'.join(_NUM_WORDS) + r'|hundred|thousand|million|billion|and)\s+){1,6}(?:'+ '|'.join(_NUM_WORDS) + r'|hundred|thousand|million|billion)\b',
                  _big, text, flags=re.IGNORECASE)
    return text

# ── Acronym auto-uppercase ───────────────────────────────────────────────────
_ACRONYMS = {
    'pdf','api','url','ui','ux','ai','ml','llm','gpu','cpu','ram','ssd','usb',
    'html','css','js','json','xml','yaml','sql','nosql','aws','gcp','sdk','cli',
    'ide','vpn','dns','http','https','ssh','ftp','smtp','imap','tcp','udp','ip',
    'os','io','db','ci','cd','qa','seo','sem','crm','erp','saas','paas','iaas',
    'kpi','roi','ceo','cto','cfo','coo','vp','hr','pr','b2b','b2c','faq','tba',
    'eta','etc','tldr','imo','imho','fyi','asap','rsvp','dm','iot','ar','vr','xr',
    'nft','ico','ipo','sec','irs','dmv','fbi','cia','nasa','eu','uk','usa',
    'glm',
    # NOTE: 'us' and 'un' removed — pronouns/words too common in dictation, false positives.
    # 'usa' kept because it's only ever the country.
}
def _apply_acronyms(text: str) -> str:
    def _upper(m):
        w = m.group(0)
        return w.upper() if w.lower() in _ACRONYMS else w
    return re.sub(r'\b[a-zA-Z]{2,5}\b', _upper, text)

# ── Formatting cues (post-LLM regex) ─────────────────────────────────────────
_FORMATTING_CUES = [
    (r'\bnew paragraph\b',  '\n\n'),
    (r'\bnew line\b',       '\n'),
    (r'\bnext bullet\b',    '\n• '),
    (r'\bbullet point\b',   '\n• '),
    (r'\bmake a list\b',    '\n• '),
    (r'\bopen paren(?:thesis)?\b',  '('),
    (r'\bclose paren(?:thesis)?\b', ')'),
    (r'\bopen bracket\b',   '['),
    (r'\bclose bracket\b',  ']'),
]

def _apply_formatting_cues(text: str) -> str:
    for pattern, repl in _FORMATTING_CUES:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text

# ── Snippet expansion (post-LLM) ──────────────────────────────────────────────
def _apply_snippets(text: str, snippets: dict) -> str:
    for trigger, expansion in snippets.items():
        if not trigger.strip():
            continue
        pattern = r"(?<!\w)" + re.escape(trigger.strip()) + r"(?!\w)"
        text = re.sub(pattern, expansion, text, flags=re.IGNORECASE)
    return text

# ── Learned corrections (from hybrid clipboard watcher) ──────────────────────
_LEARNED_CORRECTIONS_PATH = None  # set after APP_DIR is confirmed

# Common English words that must NEVER be overridden by deterministic regex.
# These appear constantly in normal speech — replacing them would corrupt
# most dictations.  Corrections involving these words are passed to the LLM
# as context-aware hints instead.
_COMMON_WORDS = frozenset("""
    a about after again against all am an and any are as at be because been
    before being below between both but by can could did do does doing done
    down during each even every few for from further get gets go going gone
    got had has have having he her here hers herself him himself his how i if
    in into is it its itself just know let like ll long look make me might
    more most much must my myself no nor not now of off on once one only or
    other our ours ourselves out over own part put re really right s same say
    she should so some still such sure t take tell than that the their theirs
    them themselves then there these they thing think this those through time
    to too two under until up us use used very want was way we well were what
    when where which while who whom why will with won would yes yet you your
    yours yourself yourselves back come day end find first give good great
    hand help here high home house keep kind last left life little long look
    made make man may mean men might mind miss much must name need never new
    next night number off old only open order part place play point put quite
    read real rest right room run said same say school set show side since
    small start state still stop sure take tell than that them then there
    thing think three time turn two under upon us use very want water way
    well went were what when while will with word work world year also always
    another ask away big came come could day different does don end even
    found get give going good got great had hand has have help here high
    him home house how its just keep kind know large last let life like line
    little long look made make man many may me men might more most move much
    must my name need never new next no not now number of off often old on
    one only open other our out over own part people place point put quite
    rather read right run said same saw say second see seem set she should
    show side since small so some something sometimes stand start state still
    stop such take tell than that the them then there these they thing think
    this those though thought three through time to together too turn two
    under up us use very want was water way we well went were what when where
    which while who why will with without word work world would write year
    you young steel
""".split())

_COMMON_CORRECTION_WORDS = _COMMON_WORDS | frozenset("""
    im i'm ive i've id i'd ill i'll youre you're youve you've youd you'd youll
    you'll hes he's shes she's its it's thats that's theres there's were we're
    weve we've wed we'd well we'll theyre they're theyve they've theyd they'd
    theyll they'll dont don't doesnt doesn't didnt didn't cant can't couldnt
    couldn't wont won't wouldnt wouldn't shouldnt shouldn't isnt isn't arent
    aren't wasnt wasn't werent weren't havent haven't hasnt hasn't hadnt hadn't
    bro bruh gonna wanna kinda sorta
""".split())

_LEARNED_STRIP_CHARS = " \t\r\n.,!?;:'\"()[]{}<>“”‘’`"

def _normalize_learned_key(text: str) -> str:
    """Normalize a candidate correction key before safety checks."""
    text = (text or "").strip().lower()
    text = text.replace("’", "'").replace("‘", "'")
    return text.strip(_LEARNED_STRIP_CHARS)

def _is_safe_learned_correction(old_word: str, new_word: str) -> bool:
    """Return True only for deterministic corrections safe to apply blindly.

    Full-sentence edits are often contextual.  If Josh changes
    "it's fucked up bro" to "this fucked up broken thing", that is useful
    feedback, but it is not safe evidence that "it's" should always become
    "this" or "bro" should always become "broken thing".
    """
    old_clean = _normalize_learned_key(old_word)
    new_clean = _normalize_learned_key(new_word)

    if not old_clean or not new_clean or old_clean == new_clean:
        return False
    if " " in old_clean or " " in new_clean:
        return False
    if len(old_clean) < 4:
        return False
    if old_clean in _COMMON_CORRECTION_WORDS:
        return False
    if any(part in _COMMON_CORRECTION_WORDS for part in re.split(r"[-_/]", old_clean)):
        return False

    # Keep blind replacement for obvious domain-token fixes like enos → n8n.
    # Plain English-looking word swaps are left for context-aware prompting.
    return bool(re.search(r"[^a-z']", old_clean + new_clean) or re.search(r"\d", new_clean))

def _apply_learned_corrections_safe(text: str) -> str:
    """Apply ONLY safe learned corrections — where the old word is NOT a
    common English word (e.g. "enos" → "n8n").  These are unambiguous:
    "enos" is never a real word, so blind regex is correct.

    Corrections where the old word IS a common word (e.g. "still" → "stealth")
    are skipped here and instead passed to the LLM as context-aware hints.
    """
    path = APP_DIR / "learned_corrections.json"
    if not path.exists():
        return text
    try:
        corrections = json.loads(path.read_text())
        for old_word, new_word in corrections.items():
            old_clean = _normalize_learned_key(old_word)
            if not _is_safe_learned_correction(old_word, str(new_word)):
                continue  # handled by LLM context hints, not blind regex
            text = re.sub(r'\b' + re.escape(old_clean) + r'\b', str(new_word),
                          text, flags=re.IGNORECASE)
    except Exception:
        pass
    return text

# ── Paste ─────────────────────────────────────────────────────────────────────
def type_text(text: str):
    try:
        p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        p.communicate(input=text.encode("utf-8"))
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" using command down'],
            check=False, timeout=3, capture_output=True
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            print(f"[Paste] osascript failed (rc={result.returncode}): {stderr}", flush=True)
        else:
            print(f"[Paste] OK — {len(text)} chars pasted", flush=True)
    except subprocess.TimeoutExpired:
        print("[Paste] osascript timeout — keystroke dropped", flush=True)
    except Exception as e:
        print(f"[Paste] error: {e}", flush=True)

def _clipboard_read() -> str:
    try:
        return subprocess.check_output(["pbpaste"]).decode("utf-8", errors="replace")
    except Exception:
        return ""

def _clipboard_write(text: str):
    try:
        subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE).communicate(
            input=text.encode("utf-8")
        )
    except Exception:
        pass

# ── LLM prompts ───────────────────────────────────────────────────────────────
def _load_learned_corrections() -> dict:
    """Load learned corrections from disk (called fresh each time)."""
    path = APP_DIR / "learned_corrections.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}

def get_system_prompt(app_context: str = "", custom_words: list = None,
                      learned_corrections: dict = None) -> str:
    ctx = (f'Note: The user is writing in {app_context}. '
           f'Adapt formatting naturally for this context.\n\n') if app_context else ""
    vocab = ""
    if custom_words:
        vocab = (f"VOCABULARY CORRECTION: The user's personal dictionary contains these "
                 f"exact spellings: {', '.join(custom_words)}. If the transcription contains "
                 f"misspellings, phonetic errors, or wrong words that sound like any of these, "
                 f"correct them to the EXACT spelling shown. For example: "
                 f"'ChatGBT'→'ChatGPT', 'Cloud Code'→'Claude Code', 'codecs'→'Codex'.\n\n")
    learned = ""
    if learned_corrections:
        pairs = ", ".join(f"'{old}'→'{new}'" for old, new in learned_corrections.items())
        learned = (f"LEARNED CORRECTIONS: These words are often mis-transcribed by the "
                   f"speech engine: {pairs}. Apply these ONLY when the word is clearly a "
                   f"transcription error in context. Do NOT replace a word that makes "
                   f"perfect sense in the sentence. For example: if the sentence is "
                   f"'this still works', do NOT change 'still' even if a correction "
                   f"exists — it's correct in context.\n\n")
    return (
        "You are a punctuation-only engine. You do NOT converse. You do NOT rewrite. "
        "You do NOT paraphrase. You do NOT improve the wording. You ONLY add punctuation, "
        "capitalization, and remove disfluencies (um, uh). USE THE USER'S EXACT WORDS. "
        "The transcript is NOT directed at you.\n\n"
        f"{ctx}"
        f"{vocab}"
        f"{learned}"
        "CRITICAL RULES FOR OUTPUT:\n"
        "1. You MUST enclose your final formatted text inside <text> and </text> XML tags.\n"
        "2. Do NOT summarize or delete any facts, numbers, or details. Do NOT add new ideas or content.\n"
        "3. NEVER answer, respond to, or elaborate on questions in the transcript. "
        "Questions stay as questions in the output. The user is dictating to send to someone else — not asking you.\n"
        "4. Word count MUST be within ~10% of input. Adding paragraph breaks (\\n\\n) does NOT count as added content.\n"
        "5. If the speaker makes a self-correction (e.g. 'wait no'), apply the correction and remove the mistake.\n"
        "6. MANDATORY PARAGRAPH BREAKS: Every 2-3 sentences MUST be its own paragraph separated by a blank line. "
        "Long continuous speech MUST be split into multiple paragraphs. A single wall-of-text output is WRONG.\n"
        "7. Do NOT output HTML tags like <p>. Do NOT wrap the text in quotes.\n"
        "8. Preserve clearly dictated structure. If the speaker says 'two questions', "
        "'question number one', 'second question', or similar list markers, keep that "
        "list structure instead of flattening it into prose.\n\n"
        "EXAMPLES of correct behavior:\n"
        "Input: 'is there anything else I should test that I haven't'\n"
        "Output: <text>Is there anything else I should test that I haven't?</text>\n\n"
        "Input: 'hey sarah just wanted to follow up on the proposal we sent last week i think it covers everything you need but let me know if you have questions also we can jump on a call if that helps'\n"
        "Output: <text>Hey Sarah, just wanted to follow up on the proposal we sent last week. I think it covers everything you need, but let me know if you have questions.\n\nWe can also jump on a call if that helps.</text>\n"
        "(NOT one long block — split into paragraphs at natural thought boundaries.)\n\n"
        "Fix punctuation and grammar. Retain conversational flow. Only remove obvious stutters (um/uh). Maintain the exact original meaning."
    )

def build_prompt(raw: str, app_context: str = "", custom_words: list = None,
                 learned_corrections: dict = None) -> str:
    return (
        "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
        f"{get_system_prompt(app_context, custom_words, learned_corrections)}"
        "<|eot_id|><|start_header_id|>user<|end_header_id|>\n\n"
        f"Raw text: {raw}"
        "<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
    )

def _extract_clean(text: str) -> str:
    match = re.search(r"<text>\s*(.*?)\s*</text>", text, flags=re.DOTALL | re.IGNORECASE)
    result = match.group(1).strip() if match else text.strip()
    result = re.sub(r"</?text>", "", result, flags=re.IGNORECASE)
    # Block-level tags (p, br, div, li, headings) used as sentence/paragraph separators:
    # replace with a SPACE so adjacent words stay separated after the tag is removed.
    # Without this, "problem.<p>I know" → "problem.I know" (spaces eaten).
    result = re.sub(r"\s*</?(?:p|br|div|li|ul|ol|h[1-6])\s*/?>\s*",
                    " ", result, flags=re.IGNORECASE)
    # Strip any remaining inline HTML/XML tags the model may have added
    result = re.sub(r"</?[a-zA-Z][a-zA-Z0-9]*\s*[^>]*>", "", result)
    # Collapse any runs of spaces left after tag removal
    result = re.sub(r' {2,}', ' ', result)
    result = result.strip()
    if result.startswith('"') and result.endswith('"'):
        result = result[1:-1].strip()
    if result.startswith("'") and result.endswith("'"):
        result = result[1:-1].strip()
    result = re.sub(r"^\s*<[^>]+>", "", result).strip()
    result = re.sub(r"<[^>]+>\s*$", "", result).strip()
    return result

_FILLER_TOKENS = {"um", "uh", "erm", "hmm", "hm", "mm", "mhm", "ah", "eh"}

def _remove_filler_words(text: str) -> str:
    """Remove safe standalone filler words/phrases after transcription.

    This runs even when MLX falls back to raw text, so common spoken filler does
    not depend on the local LLM obeying the prompt. Keep this conservative:
    remove obvious fillers, but do not remove useful words like "like" or every
    leading "so" because Josh often uses them as real sentence structure.
    """
    if not text:
        return text

    # Remove "so" only when it is clearly just introducing filler.
    text = re.sub(
        r"^\s*so\s*[, ]+(?=(?:you\s+know|you\s+see|um|uh|erm|hmm|hm|mm|mhm|ah|eh)\b)",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Multi-word fillers first. Preserve meaningful uses of the same words in
    # other contexts by requiring word boundaries and optional surrounding commas.
    patterns = [
        r"\b(?:you\s+know)(?:\s+what\s+i\s+mean)?\b",
        r"\b(?:you\s+see)\b",
    ]
    for pattern in patterns:
        text = re.sub(r"\s*,?\s*" + pattern + r"\s*,?\s*", " ", text,
                      flags=re.IGNORECASE)

    # Single-token fillers.
    filler_alt = "|".join(re.escape(t) for t in sorted(_FILLER_TOKENS, key=len, reverse=True))
    text = re.sub(rf"\s*,?\s*\b(?:{filler_alt})\b\s*,?\s*", " ", text,
                  flags=re.IGNORECASE)

    # Clean punctuation/spacing left by removals.
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"([,.!?;:]){2,}", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"^[,.;:\-\s]+", "", text).strip()
    return text

def _semantic_tokens(text: str) -> list:
    """Words/numbers that must survive MLX punctuation cleanup in the same order."""
    tokens = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text.lower())
    return [t for t in tokens if t not in _FILLER_TOKENS]

def _number_signature(tokens: list) -> list:
    sig = []
    for token in tokens:
        if token.isdigit():
            sig.append(int(token))
        elif token in _NUM_WORDS:
            sig.append(_NUM_WORDS[token])
    return sig

def _terminal_punctuation_count(text: str) -> int:
    return len(re.findall(r"[.!?]", text or ""))

def _is_punctuation_regression(raw: str, cleaned: str) -> bool:
    """True when MLX flattened useful sentence punctuation from long speech."""
    raw_words = len((raw or "").split())
    if raw_words < 25:
        return False
    raw_terms = _terminal_punctuation_count(raw)
    cleaned_terms = _terminal_punctuation_count(cleaned)
    if raw_terms < 3:
        return False
    return cleaned_terms <= max(1, raw_terms // 2)

def _mlx_changed_meaning(raw: str, cleaned: str) -> tuple:
    """Return True when MLX did more than punctuation/capitalization cleanup."""
    import difflib

    raw_tokens = _semantic_tokens(raw)
    out_tokens = _semantic_tokens(cleaned)
    if not out_tokens:
        return True, "empty_output"

    raw_nums = _number_signature(raw_tokens)
    out_nums = _number_signature(out_tokens)
    if raw_nums != out_nums:
        return True, f"number_change(raw={raw_nums}, out={out_nums})"

    # Short dictations are where the 1B model is most tempted to "complete" or
    # reinterpret the text ("1, 2, 3, 4" -> "5"). For these, only punctuation
    # and capitalization are allowed; token order must stay exact.
    if len(raw_tokens) <= 7:
        if raw_tokens != out_tokens:
            return True, "short_token_change"
        return False, ""

    order_ratio = difflib.SequenceMatcher(None, raw_tokens, out_tokens).ratio()
    if order_ratio < 0.78:
        return True, f"token_order_change({order_ratio:.0%})"

    return False, ""

def clean_with_mlx(raw: str, app_context: str = "",
                   custom_words: list = None,
                   learned_corrections: dict = None) -> str:
    if _mlx_model is None:
        return raw
    import mlx_lm
    from mlx_lm.generate import make_sampler
    sampler = make_sampler(temp=0.1, min_p=0.05)

    # Dynamic token budget — MUST scale with input length, otherwise long
    # recordings get truncated mid-generation and lose all punctuation.
    # Rule of thumb: each input word ≈ 1.3 tokens; output also adds punctuation
    # and XML tags. Budget 2x input words + 100 buffer, floor at 250.
    raw_word_count = len(raw.split())
    max_tokens = max(250, raw_word_count * 2 + 100)

    # 8s hard timeout on MLX. Daemon thread so orphan doesn't block on hang.
    import queue as _q
    result_q = _q.Queue()
    def _do_mlx():
        try:
            result_q.put(("ok", mlx_lm.generate(
                _mlx_model, _mlx_tokenizer,
                prompt=build_prompt(raw, app_context, custom_words, learned_corrections),
                max_tokens=max_tokens, verbose=False, sampler=sampler,
            ).strip()))
        except Exception as e:
            result_q.put(("err", str(e)))
    t0 = time.time()
    threading.Thread(target=_do_mlx, daemon=True).start()
    try:
        status, val = result_q.get(timeout=8)
    except _q.Empty:
        print(f"[MLX] TIMEOUT after 8s — falling back to raw transcript", flush=True)
        return raw
    print(f"[MLX] generate took {time.time()-t0:.1f}s (budget={max_tokens}t, in={raw_word_count}w)", flush=True)
    if status != "ok":
        print(f"[MLX] error: {val} — falling back to raw", flush=True)
        return raw
    cleaned = _extract_clean(val)

    # Hallucination safety net: if MLX output is dramatically longer than the input,
    # the 1B model went into "answer the question" mode. Discard and return raw.
    raw_words = len(raw.split())
    out_words = len(cleaned.split())
    max_allowed = max(int(raw_words * 1.3), raw_words + 10)
    if out_words > max_allowed:
        print(f"[MLX] Output too long (raw={raw_words}w, out={out_words}w) — falling back to raw", flush=True)
        return raw

    # Word-overlap check: model must reuse most of the user's actual words.
    # If <55% of input words appear in output, it's a paraphrase, not a clean-up.
    if raw_words >= 8:
        raw_set = set(w.lower().strip(",.!?;:'\"") for w in raw.split())
        out_set = set(w.lower().strip(",.!?;:'\"") for w in cleaned.split())
        overlap = len(raw_set & out_set) / max(len(raw_set), 1)
        if overlap < 0.55:
            print(f"[MLX] Paraphrase detected (overlap={overlap:.0%}) — falling back to raw", flush=True)
            return raw

    # Repetitive character hallucination: the 1B model sometimes gets stuck
    # repeating a character (most commonly dots after an ellipsis).
    # Collapse any run of 4+ identical characters to 3 (an ellipsis).
    cleaned = re.sub(r'(.)\1{3,}', r'\1\1\1', cleaned)

    if _is_punctuation_regression(raw, cleaned):
        print("[MLX] Punctuation regression — falling back to raw transcript", flush=True)
        return raw

    changed, reason = _mlx_changed_meaning(raw, cleaned)
    if changed:
        print(f"[MLX] Meaning changed ({reason}) — falling back to raw", flush=True)
        return raw

    return cleaned

# ── Command Mode LLM transform ────────────────────────────────────────────────
def _command_transform(selection: str, instruction: str) -> str:
    sys_prompt = (
        "You are a text transformation engine. Apply the user's instruction to the selected text. "
        "Output ONLY the transformed result inside <text></text> tags. "
        "Do NOT explain, comment, or add anything extra."
    )
    user_msg = f"Selected text:\n{selection}\n\nInstruction: {instruction}"

    if _mlx_model is None:
        return selection
    import mlx_lm
    from mlx_lm.generate import make_sampler
    prompt = (
        "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
        f"{sys_prompt}"
        "<|eot_id|><|start_header_id|>user<|end_header_id|>\n\n"
        f"{user_msg}"
        "<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
    )
    sampler = make_sampler(temp=0.3, min_p=0.05)
    result = mlx_lm.generate(
        _mlx_model, _mlx_tokenizer,
        prompt=prompt, max_tokens=400, verbose=False, sampler=sampler,
    ).strip()
    return _extract_clean(result)

def _flatten_llm_lists(text: str) -> str:
    """Safety net: if the LLM created numbered list items (1. ... 2. ...) but
    smart_bullets later decides this isn't a real list, we'd be left with the
    LLM's formatting. This function detects LLM-added numbered/bulleted items
    upfront and flattens them back to prose so smart_bullets has the final say.
    Runs BEFORE smart_bullets — smart_bullets can re-bullet if patterns warrant.
    """
    lines = text.split('\n')
    list_lines = [l for l in lines if re.match(r'^\s*(?:\d+[\.\)]|•|\*|—|–|-)\s+\S', l)]
    if len(list_lines) < 2:
        return text  # not enough markers — leave alone

    # Flatten: strip the markers and rejoin as prose
    flat_parts = []
    for line in lines:
        if re.match(r'^\s*(?:\d+[\.\)]|•|\*|—|–|-)\s+\S', line):
            stripped = re.sub(r'^\s*(?:\d+[\.\)]|•|\*|—|–|-)\s+', '', line).strip()
            # Ensure sentence ending
            if stripped and stripped[-1] not in '.!?':
                stripped += '.'
            flat_parts.append(stripped)
        elif line.strip():
            flat_parts.append(line.strip())
    return ' '.join(flat_parts)


def _normalize_prose_line_breaks(text: str) -> str:
    """Collapse arbitrary single LLM line breaks inside prose.

    Blank lines are paragraph breaks. Single newlines inside normal prose are
    usually model formatting noise and make pasted dictation feel scrambled.
    Lists are preserved because each line is meaningful structure.
    """
    if not text:
        return text

    paragraphs = re.split(r'\n\s*\n', text.strip())
    out = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if re.search(r'^\s*(?:•|\*|-|\d+[\.\)])\s+', para, re.MULTILINE):
            out.append(para)
            continue
        para = re.sub(r'\s*\n\s*', ' ', para)
        para = re.sub(r' {2,}', ' ', para).strip()
        out.append(para)
    return '\n\n'.join(out)


def _longest_sentence_word_count(text: str) -> int:
    if not text:
        return 0
    sentences = re.split(r'[.!?]+(?:\s+|$)', text)
    return max((len(s.split()) for s in sentences if s.strip()), default=0)


def _repair_long_runons(text: str) -> str:
    """Add conservative sentence breaks to very long run-on dictation.

    Whisper often emits one huge sentence during long speech.  The local LLM can
    miss that too, so this deterministic pass only fires on obvious run-ons and
    only at strong discourse markers that naturally start a new thought.
    """
    if _longest_sentence_word_count(text) < 60:
        return text

    markers = [
        "and then",
        "but basically",
        "so i think",
        "tell me what you think",
        "what about",
        "or still",
        "and within",
        "like there's",
        "and you need",
        "is this like",
    ]

    def _cap_marker(marker: str) -> str:
        return marker[:1].upper() + marker[1:]

    repaired = text
    for marker in markers:
        pattern = re.compile(
            rf"(?<![.!?\n])\s+({re.escape(marker)})\b",
            flags=re.IGNORECASE,
        )

        def _replace(m):
            before = repaired[:m.start()]
            after = repaired[m.end():]
            tail = re.split(r'[.!?\n]', before)[-1]
            # Do not create tiny fragments; this pass is only for large run-ons.
            if len(tail.split()) < 18 or len(after.split()) < 5:
                return m.group(0)
            return ". " + _cap_marker(m.group(1))

        repaired = pattern.sub(_replace, repaired)

    return repaired


def _split_paragraphs(text: str) -> str:
    """Deterministic paragraph splitter. The 1B LLM doesn't reliably honor
    paragraph-break instructions, so we enforce structure here.

    Rule: any paragraph with > 3 sentences gets split into chunks of 2 sentences.
    Skips text that's already a list (bullets/numbered) — those stay as-is.
    """
    # Skip if already structured as list
    if re.search(r'^\s*(?:•|\*|-|\d+[\.\)])\s+', text, re.MULTILINE):
        return text

    paragraphs = re.split(r'\n\s*\n', text)
    out = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Sentence split: period/exclaim/question followed by space + capital letter
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', para)
        sentences = [s.strip() for s in sentences if s.strip()]
        if len(sentences) <= 3:
            out.append(para)
        else:
            chunks = []
            for i in range(0, len(sentences), 2):
                chunks.append(' '.join(sentences[i:i+2]))
            out.append('\n\n'.join(chunks))
    return '\n\n'.join(out)

def _format_spoken_question_list(text: str) -> str:
    """Preserve dictated "two questions / question number one / second question" structure."""
    if not text:
        return text

    pattern = re.compile(
        r"^(?P<intro>.*?\btwo\s+questions?)\s*[.:]?\s*"
        r"(?:question\s+number\s+one|number\s+one|first\s+question)\s*[.:]?\s*"
        r"(?P<first>.*?)\s+"
        r"(?:and\s+)?(?:second\s+question|question\s+number\s+two|number\s+two)\s*[.:]?\s*"
        r"(?P<second>.+)$",
        flags=re.IGNORECASE | re.DOTALL,
    )
    m = pattern.match(text.strip())
    if not m:
        return text

    intro = m.group("intro").strip().rstrip(".,;:")
    first = m.group("first").strip().rstrip()
    second = m.group("second").strip()
    second = re.sub(r"\?\s+when\b", "? When", second, flags=re.IGNORECASE)
    second = re.sub(r"\bwhat\s+is\s+near\s+me,\s*whatever\b", "what is near me?", second,
                    flags=re.IGNORECASE)
    second = re.sub(r"\?([.!?]+)$", "?", second)

    return f"{intro}:\n\n1) {first}\n\n2) {second}"


def _smart_bullets(text: str) -> str:
    """
    Auto-format as bullets ONLY when the content is actually list-like.
    Never converts plain prose. Detects:
      - Ordinal sequences: "first... second... third..."
      - Number-form: "number one... number two... number three..."
      - Count + noun: "three things: A, B, and C"
    Returns original text unchanged if no list pattern found.
    """
    # Pre-normalize "Number one/two/three" → "first/second/third" so Pattern 1 can detect them.
    # Only fires when 2+ such markers exist — avoids false positive on phrases like
    # "Number one priority" or "the number two reason".
    _num_re = re.compile(r'\bnumber\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b', re.IGNORECASE)
    _num_positions = list(_num_re.finditer(text))
    if len(_num_positions) >= 2:
        _ord_map = {'one':'First','two':'Second','three':'Third','four':'Fourth','five':'Fifth',
                    'six':'Sixth','seven':'Seventh','eight':'Eighth','nine':'Ninth','ten':'Tenth'}
        # RANKING GUARD: "number one priority... number two priority..." is a ranking adjective,
        # not a list marker. If ≥(n-1) occurrences share the same immediately-following word
        # (the ranking noun), skip pre-normalization so Pattern 1 never fires on it.
        _following = []
        for _m in _num_positions:
            _tail = text[_m.end():].lstrip()
            _fw = re.match(r'[a-zA-Z]+', _tail)
            _following.append(_fw.group(0).lower() if _fw else '')
        _top_fw = max(set(_following), key=_following.count) if _following else ''
        _skip_prenorm = bool(_top_fw and _following.count(_top_fw) >= max(2, len(_following) - 1))
        if not _skip_prenorm:
            text = _num_re.sub(lambda m: _ord_map[m.group(1).lower()], text)

    tl = text.lower()

    # ── Pattern 1: ordinal words (first/second/third…) ──────────────────────────
    ordinals = ["first", "second", "third", "fourth", "fifth", "sixth",
                "seventh", "eighth", "ninth", "tenth", "finally", "lastly"]
    hits = [w for w in ordinals if re.search(rf'\b{w}\b', tl)]
    if len(hits) >= 2:
        ord_re = re.compile(
            r'\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|'
            r'ninth|tenth|finally|lastly)\b[,:]?\s*', flags=re.IGNORECASE)
        # Split keeping the position of the FIRST ordinal so we can preserve the intro
        first_match = ord_re.search(text)
        if first_match:
            intro = text[:first_match.start()].strip().rstrip('.,;:')
            rest  = text[first_match.start():]
            # Split the rest by ordinal markers — this gives us the actual list items
            parts = ord_re.split(rest)
            items = [p.strip().rstrip('.,;') for p in parts if p.strip()]
            if len(items) >= 2:
                # Detach trailing closing sentence from the LAST item (e.g. "Tuesday. Let me know if...")
                last = items[-1]
                # If the last item ends with one item-content + ". <new sentence>", split them
                trailing_match = re.match(r'(.*?[.!?])\s+([A-Z].*)$', last, re.DOTALL)
                outro = ""
                if trailing_match:
                    items[-1] = trailing_match.group(1).rstrip('.,;')
                    outro = trailing_match.group(2).strip()
                # ANTI-RANKING GUARD: if most items share the same starting CONTENT word
                # (e.g. "priority is X", "priority now is Y"), this is a ranking,
                # not a list — return the original text unchanged.
                # Skip common articles/determiners so "the deployment / the budget / the plan"
                # doesn't falsely trigger the guard.
                _SKIP_ART = {'the','a','an','to','my','our','your','this','that','these','those'}
                _fw_list = []
                for _it in items:
                    for _w in _it.split():
                        if _w.lower() not in _SKIP_ART:
                            _fw_list.append(_w.lower())
                            break
                if _fw_list:
                    _top_item_fw = max(set(_fw_list), key=_fw_list.count)
                    if _fw_list.count(_top_item_fw) >= max(2, len(_fw_list) - 1):
                        return text  # ranking pattern — do not bullet-ise

                # Validate item lengths just like Pattern 2 to avoid false positives
                lengths = [len(i) for i in items if i]
                if lengths and 8 <= min(lengths) and max(lengths) <= 100:
                    bullets = "\n".join(f"• {i}" for i in items if i)
                    parts_out = []
                    if intro:
                        parts_out.append(intro + ":")
                    parts_out.append(bullets)
                    if outro:
                        parts_out.append(outro)
                    return "\n\n".join(parts_out)

    # ── Pattern 2: "N things/points/steps/reasons[:.] A, B, and C" ─────────────
    m = re.search(
        r'\b(?:two|three|four|five|six|seven|eight|nine|ten|\d+)\s+'
        r'(?:things?|points?|items?|reasons?|steps?|ways?|tips?|rules?)\b',
        tl)
    if m:
        # Find the separator — colon or period immediately after the matched phrase
        intro_end = m.end()
        sep_match = re.search(r'[:.]\s*', text[intro_end:])
        if sep_match:
            cut = intro_end + sep_match.end()
            after_sep = text[cut:]
            intro    = text[:cut].rstrip()
            # Split items on comma+and / semicolons
            items = re.split(r',\s*|;\s*', after_sep)
            items = [re.sub(r'^and\s+', '', i.strip(), flags=re.IGNORECASE).rstrip('.,;')
                     for i in items if i.strip()]
            if len(items) >= 2:
                # Validate item lengths — real list items are short parallel phrases.
                # Too short (< 12): bare ordinal label ("Number one", "First") not an item.
                # Too long (> 100): mid-sentence comma split, not a list boundary.
                lengths = [len(i) for i in items if i]
                if not lengths or min(lengths) < 12 or max(lengths) > 100:
                    return text  # bad split — natural speech comma, not a real list
                return intro + "\n" + "\n".join(f"• {i}" for i in items if i)

    return text  # not a list — return unchanged


# ═══════════════════════════════════════════════════════════════════════════════
class LocalFlowApp(rumps.App):
    def __init__(self):
        # Use the bundled icon as a template image (auto-inverts for
        # light/dark menu bar).  Falls back to the microphone emoji if
        # the icon file is missing (e.g. partial repo clone).
        _icon_path = str(APP_DIR / "menubar_icon.png")
        if os.path.isfile(_icon_path):
            super().__init__("LocalFlow", icon=_icon_path, template=True)
            self._idle = ""  # icon-only at idle
        else:
            super().__init__("🎙️")
            self._idle = "🎙️"
        self.cfg = load_config()

        # ── Build menu ─────────────────────────────────────────────────────────

        # Language submenu
        self._lang_menu = rumps.MenuItem("Language: English")
        self._lang_items = {}
        _langs = [("English",    "en"),  ("Auto-Detect", "auto"),
                  ("Chinese",    "zh"),  ("Spanish",     "es"),
                  ("French",     "fr"),  ("German",      "de"),
                  ("Japanese",   "ja"),  ("Korean",      "ko"),
                  ("Portuguese", "pt"),  ("Hindi",       "hi"),
                  ("Arabic",     "ar"),  ("Russian",     "ru")]
        for label, code in _langs:
            item = rumps.MenuItem(label, callback=self._make_lang_cb(code))
            self._lang_items[code] = item
        self._lang_menu.update(list(self._lang_items.values()))

        # Speed/Accuracy toggle menu item
        _is_medium = self.cfg.get("whisper_model", "small.en") == "medium.en"
        self._speed_item = rumps.MenuItem(
            "🐢 Accurate mode ON" if _is_medium else "⚡ Fast mode ON",
            callback=self._toggle_speed_accuracy
        )

        self.menu = [
            rumps.MenuItem("Enabled", callback=None),
            None,
            rumps.MenuItem("Change Hotkey",     callback=self._change_hotkey),
            self._speed_item,
            None,
            rumps.MenuItem("⚙️ Open UI Dashboard",   callback=self._open_dashboard),
            None,
            rumps.MenuItem("Add Custom Word",   callback=self._add_custom_word),
            rumps.MenuItem("Add Voice Snippet", callback=self._add_snippet),
            None,
            rumps.MenuItem("🔄 Reset State (if stuck)", callback=self._reset_state),
            None,
        ]

        self._update_lang_label()

        # ── State ──────────────────────────────────────────────────────────────
        self._recording      = False
        self._processing     = False
        self._canceling      = False
        self._pasting        = False    # True while type_text is running — guard against re-entry
        self._cancel_cnt     = 0
        self._pending_clean  = None
        self._audio_data     = []
        self._stream         = None
        self._cmd_mode_active = False   # True when Option+Cmd triggered
        self._cgtap_fire_pending = False  # CGEventTap sets this; _drain_queue picks it up on main thread

        # Thread-safe title queue
        self._title_q: queue.Queue = queue.Queue()
        rumps.Timer(self._drain_queue, 0.1).start()

        # Recording duration timer — ticks every second while recording so the
        # menu bar shows elapsed time (e.g. "🔴 0:15") instead of a static label.
        self._recording_timer = rumps.Timer(self._update_recording_title, 1)

        # Cancel countdown (main thread)
        self._countdown = rumps.Timer(self._cancel_tick, 1)

        # Hot keys set
        self._hotkeys = KEY_MAP.get(self.cfg["trigger_key"], KEY_MAP["alt"])

        # Boot
        threading.Thread(target=self._boot, daemon=True).start()

        # Crash recovery: check for leftover audio from a previous crash
        self._crash_buf_file = None
        self._recover_crash_audio()

        # Hotkey monitors.
        #
        # On macOS, prefer AppKit's native NSEvent monitors. Running pynput's
        # keyboard listener at the same time is redundant and can hit macOS
        # input-source APIs from a background thread, which the OS kills with a
        # SIGTRAP. Keep pynput only as a non-AppKit fallback.
        self._kb = None
        self._mod_key_down = False
        self._ns_monitor = None
        self._ns_key_monitor = None
        if _HAS_NSEVENT:
            self._ns_mod_mask = _NS_MOD_FLAGS.get(self.cfg["trigger_key"], 0x80000)
            self._ns_mod_key  = _NS_MOD_KEYS.get(self.cfg["trigger_key"], keyboard.Key.alt)
            self._ns_monitor = _NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                1 << 12,  # NSEventMaskFlagsChanged
                self._ns_modifier_handler
            )
            self._ns_key_monitor = _NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                1 << 10,  # NSEventMaskKeyDown
                self._ns_key_handler
            )
        else:
            self._kb = keyboard.Listener(on_press=self._on_press,
                                         on_release=self._on_release)
            self._kb.start()

        # Watchdog: detect & recover from any stuck state.
        # 1) Fallback listener thread death → restart
        # 2) _processing flag stuck >30s → soft reset; >3 resets in 5 min → full process restart
        # 3) _recording flag stuck >90s → soft reset; >3 resets in 5 min → full process restart
        self._processing_started_at = 0
        self._recording_started_at  = 0
        self._pasting_started_at    = 0
        self._canceling_started_at  = 0
        self._mic_close_started_at  = 0
        self._mic_close_hung_logged = False
        self._idle_stream_seen_at   = 0
        self._audio_last_frame_count = 0
        self._audio_last_progress_at = 0
        self._watchdog_reset_times  = []   # rolling window of soft-reset timestamps

        def _self_restart(reason: str):
            """Full process restart via os.execv — releases mic, clears all state, reloads models.
            Called when repeated soft resets fail to clear a stuck state (main thread wedge).
            launchd KeepAlive treats execv transparently (same PID, new image).
            """
            self._hard_restart(reason)

        def _record_soft_reset() -> bool:
            """Track soft reset; returns True if full restart threshold hit (3 in 5 min)."""
            now_t = time.time()
            self._watchdog_reset_times.append(now_t)
            self._watchdog_reset_times = [t for t in self._watchdog_reset_times if now_t - t < 300]
            return len(self._watchdog_reset_times) >= 3

        def _listener_watchdog():
            while True:
                time.sleep(5)
                try:
                    if self._kb is not None and not self._kb.is_alive():
                        print("[Watchdog] Listener died — restarting…")
                        _log_crash_event("listener_death",
                                         was_recording=self._recording)
                        if self._recording:
                            try: self._stop_rec()
                            except Exception: pass
                            # CRITICAL: save audio BEFORE clearing state (CC stability fix #1)
                            frames = self._audio_data.copy()
                            self._recording = False
                            self._processing = False
                            self._mod_key_down = False
                            self._cmd_mode_active = False
                            self._audio_data = []
                            self._close_crash_buf(cleanup=True)
                            if frames:
                                self._processing = True
                                self._processing_started_at = time.time()
                                self._push("👨‍🍳 Cooking...")
                                threading.Thread(
                                    target=self._process, args=(frames,), daemon=True
                                ).start()
                            else:
                                self._push(self._idle)
                        self._kb = keyboard.Listener(on_press=self._on_press,
                                                     on_release=self._on_release)
                        self._kb.start()
                        print("[Watchdog] Listener restarted.")

                    now = time.time()
                    # 30s hard cap on processing. Skip during model load (first download takes minutes).
                    if self._processing and self._processing_started_at and not _loading_model and (now - self._processing_started_at) > 30:
                        stuck_for = int(now - self._processing_started_at)
                        print(f"[Watchdog] _processing stuck for {stuck_for}s — soft reset", flush=True)
                        _log_freeze("processing", stuck_for)
                        _log_crash_event("watchdog_stuck_processing", stuck_sec=stuck_for,
                                         model=self.cfg.get("whisper_model", "?"))
                        self._processing = False
                        self._processing_started_at = 0
                        # Clean up any orphaned stream (processing stuck can leave mic open)
                        if self._stream:
                            stream = self._stream
                            self._stream = None
                            threading.Thread(
                                target=lambda s=stream: (s.stop(), s.close()),
                                daemon=True,
                            ).start()
                        self._push(self._idle)
                        if _record_soft_reset():
                            _self_restart("processing_repeated")
                    # Periodic NSEvent monitor refresh during recording.
                    # macOS can silently invalidate global monitors after sleep/wake
                    # or accessibility changes; re-registering ensures the next
                    # key-down is delivered.  Runs every 15s of recording.
                    # CRITICAL: the re-registration MUST happen on the main thread
                    # (AppKit run loop) — NSEvent monitors registered from a background
                    # thread are silently dead.  We queue a sentinel and _drain_queue
                    # performs the actual re-registration.
                    if (self._recording and self._recording_started_at
                            and _HAS_NSEVENT and self._ns_monitor
                            and (now - self._recording_started_at) > 15):
                        elapsed = now - self._recording_started_at
                        last_refresh = getattr(self, '_ns_last_refresh', 0)
                        if elapsed - last_refresh >= 15:
                            self._ns_last_refresh = elapsed
                            self._title_q.put("__REFRESH_MONITOR__")

                    # Stuck recording recovery. A long toggle recording is valid;
                    # only auto-stop when the mic stream is gone or audio frames
                    # have stopped arriving for a while.
                    should_abort_oversize_recording = False
                    if self._recording and self._recording_started_at:
                        elapsed_recording = now - self._recording_started_at
                        frame_count = len(self._audio_data)
                        if frame_count > self._audio_last_frame_count:
                            self._audio_last_frame_count = frame_count
                            self._audio_last_progress_at = now
                        should_abort_oversize_recording = _recording_watchdog_should_abort_oversize(
                            elapsed_recording
                        )
                        should_recover_recording = _recording_watchdog_should_recover(
                            elapsed_sec=elapsed_recording,
                            frame_count=frame_count,
                            last_frame_count=self._audio_last_frame_count,
                            seconds_since_audio_progress=now - (self._audio_last_progress_at or self._recording_started_at),
                            has_stream=self._stream is not None,
                        )
                    else:
                        should_recover_recording = False

                    if should_abort_oversize_recording:
                        stuck_for = int(now - self._recording_started_at)
                        _log_freeze("recording_oversize_abort", stuck_for)
                        _log_crash_event("recording_oversize_abort", stuck_sec=stuck_for,
                                         audio_frames=len(self._audio_data))
                        print(f"[Watchdog] recording exceeded {_MAX_RECORDING_SEC}s — aborting stale buffer", flush=True)
                        stream = self._stream
                        self._stream = None
                        self._recording = False
                        self._recording_started_at = 0
                        self._mod_key_down = False
                        self._cmd_mode_active = False
                        self._audio_data = []
                        self._close_crash_buf(cleanup=True)
                        if stream:
                            self._close_stream_async(stream, "oversize_abort")
                        self._push("⚠️ Recording too long")
                        rumps.notification("LocalFlow", "Recording reset",
                                           "Recording ran too long and was reset to avoid a stuck cook.")
                        threading.Timer(2.0, lambda: self._push(self._idle)).start()
                        if _record_soft_reset():
                            _self_restart("recording_oversize_repeated")
                        continue

                    if should_recover_recording:
                        stuck_for = int(now - self._recording_started_at)
                        _log_freeze("recording", stuck_for)
                        _log_crash_event("watchdog_stuck_recording", stuck_sec=stuck_for,
                                         audio_frames=len(self._audio_data))
                        print(f"[Watchdog] _recording stuck for {stuck_for}s — no audio progress, saving audio + hard mic release", flush=True)
                        # Snapshot audio BEFORE clearing anything
                        frames = self._audio_data.copy()
                        # Grab and null the stream reference immediately
                        stream = self._stream
                        self._stream = None
                        self._recording = False
                        self._recording_started_at = 0
                        self._mod_key_down = False
                        self._cmd_mode_active = False
                        self._audio_data = []
                        self._close_crash_buf(cleanup=True)  # audio is saved in frames
                        # Process the saved audio — same path as a normal stop
                        if frames:
                            self._processing = True
                            self._processing_started_at = time.time()
                            self._push("👨‍🍳 Cooking...")
                            threading.Thread(
                                target=self._process, args=(frames,), daemon=True
                            ).start()
                        else:
                            self._push(self._idle)
                        # Try closing the stream with a 2s timeout.
                        # If stream.stop()/close() hangs (PortAudio driver glitch),
                        # fall through to sd._terminate() which force-releases the mic.
                        if stream:
                            import queue as _wdq
                            close_q = _wdq.Queue()
                            def _wd_close(s=stream):
                                try:
                                    s.stop(); s.close()
                                    close_q.put(True)
                                except Exception:
                                    close_q.put(False)
                            threading.Thread(target=_wd_close, daemon=True).start()
                            try:
                                close_q.get(timeout=2)
                            except _wdq.Empty:
                                print("[Watchdog] stream.close() hung for 2s", flush=True)
                        # Nuclear mic release: force-terminate ALL PortAudio resources.
                        # This guarantees the yellow mic indicator clears even if
                        # stream.close() hung or a leaked stream exists.
                        try:
                            sd._terminate()
                            sd._initialize()
                            print("[Watchdog] PortAudio force-reinitialized — mic released", flush=True)
                        except Exception as e:
                            print(f"[Watchdog] PortAudio reinit failed: {e}", flush=True)
                        if _record_soft_reset():
                            _self_restart("recording_repeated")
                    # Stuck pasting >5s = osascript hung; clear guard so user can record
                    if self._pasting and self._pasting_started_at and (now - self._pasting_started_at) > 5:
                        print(f"[Watchdog] _pasting stuck for {int(now - self._pasting_started_at)}s — soft reset", flush=True)
                        _log_freeze("pasting", int(now - self._pasting_started_at))
                        self._pasting = False
                        self._pasting_started_at = 0
                    # Stuck canceling >10s = ESC rescue countdown wedged
                    if self._canceling and self._canceling_started_at and (now - self._canceling_started_at) > 10:
                        print(f"[Watchdog] _canceling stuck for {int(now - self._canceling_started_at)}s — soft reset", flush=True)
                        _log_freeze("canceling", int(now - self._canceling_started_at))
                        self._canceling = False
                        self._canceling_started_at = 0
                        self._push(self._idle)
                    # PortAudio/CoreAudio stream.stop()/close() can hang forever
                    # inside AudioOutputUnitStop. If that happens, future mic
                    # opens can wedge even though the app and hotkey thread look
                    # alive. A clean process restart is the only reliable release.
                    if self._mic_close_started_at:
                        close_stuck_for = now - self._mic_close_started_at
                        if close_stuck_for > 3 and not self._mic_close_hung_logged:
                            self._mic_close_hung_logged = True
                            print(f"[Watchdog] mic close stuck for {int(close_stuck_for)}s", flush=True)
                            _log_crash_event("mic_close_stuck",
                                             stuck_sec=int(close_stuck_for))
                        if close_stuck_for > 8 and not (
                            self._recording or self._processing or self._pasting or self._canceling
                        ):
                            _log_freeze("mic_close", int(close_stuck_for))
                            _self_restart("mic_close_hung")
                    # A non-None stream while idle means the orange macOS mic
                    # indicator can stay on even though LocalFlow says it is not
                    # recording. Treat it as a leaked mic handle and restart
                    # after one watchdog tick if async close cannot clear it.
                    if (self._stream is not None and not (
                        self._recording or self._processing or self._pasting or self._canceling
                    )):
                        if not self._idle_stream_seen_at:
                            self._idle_stream_seen_at = now
                            print("[Watchdog] idle mic stream detected — closing", flush=True)
                            stream = self._stream
                            self._stream = None
                            self._close_stream_async(stream, "idle_stream_leak")
                        elif now - self._idle_stream_seen_at > 8:
                            _log_freeze("idle_mic_stream", int(now - self._idle_stream_seen_at))
                            _log_crash_event("idle_mic_stream",
                                             stuck_sec=int(now - self._idle_stream_seen_at))
                            _self_restart("idle_mic_stream")
                    else:
                        self._idle_stream_seen_at = 0
                    # CGTap thread died (macOS revoked accessibility, etc.) → restart
                    cgtap_t = getattr(self, '_cgtap_thread', None)
                    if _HAS_CGTAP and (cgtap_t is None or not cgtap_t.is_alive()):
                        print("[Watchdog] CGTap thread dead — restarting", flush=True)
                        self._start_persistent_cgtap()
                except Exception as e:
                    print(f"[Watchdog] error: {e}", flush=True)
        threading.Thread(target=_listener_watchdog, daemon=True).start()

        # CRITICAL: keep strong references to the NSEvent monitor tokens. PyObjC's
        # autorelease pool can collect them otherwise, silently killing event
        # delivery after macOS sleep/wake cycles or memory pressure events.

    # ── Queue / title draining ─────────────────────────────────────────────────
    def _push(self, text: str):
        self._title_q.put(text)

    # ── Boot ───────────────────────────────────────────────────────────────────
    def _boot(self):
        # Start the local web dashboard for configuration
        threading.Thread(target=self._start_dashboard_server, daemon=True).start()

        _request_mic_permission()
        _check_accessibility_and_prompt()
        # Load models in background so the menu bar icon appears immediately.
        # First download of large-v3-turbo can take 10+ minutes — don't freeze the UI.
        self._push("⏳ Loading models...")
        def _boot_load():
            load_models(self.cfg["whisper_model"])
            # Start persistent CGEventTap AFTER models load — this is the PRIMARY
            # keypress detector. NSEvent is a bonus. CGTap runs forever.
            self._start_persistent_cgtap()
            self._push(self._idle)
        threading.Thread(target=_boot_load, daemon=True).start()

    def _start_dashboard_server(self):
        import socket
        try:
            # Prevent double-bind or orphaned process from previous crash
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            in_use = (s.connect_ex(('127.0.0.1', 5050)) == 0)
            s.close()
            if in_use:
                subprocess.run("lsof -t -i:5050 | xargs kill -9", shell=True, check=False)
                time.sleep(0.5)

            # Import and run dashboard inline to inherit LSUIElement (hides dock icon)
            sys.path.insert(0, str(APP_DIR))
            import dashboard
            import logging
            log = logging.getLogger('werkzeug')
            log.setLevel(logging.ERROR)
            dashboard.app.run(host='127.0.0.1', port=5050, debug=False, use_reloader=False)
        except Exception as e:
            print(f"[Dashboard] failed to start: {e}")

    # ── Menu label updaters ────────────────────────────────────────────────────
    def _update_lang_label(self):
        lang  = self.cfg.get("language", "en")
        label = {
            "en": "English", "auto": "Auto-Detect", "zh": "Chinese",
            "es": "Spanish", "fr": "French",        "de": "German",
            "ja": "Japanese","ko": "Korean",         "pt": "Portuguese",
            "hi": "Hindi",   "ar": "Arabic",         "ru": "Russian",
        }.get(lang, lang)
        self._lang_menu.title = f"Language: {label}"
        for code, item in self._lang_items.items():
            item.state = 1 if code == lang else 0

    def _make_lang_cb(self, code: str):
        def _cb(_):
            self.cfg["language"] = code
            # If Speed engine and non-English, auto-switch to multilingual model
            if code not in ("en", "auto") and self.cfg.get("whisper_model") == "small.en":
                self.cfg["whisper_model"] = "small"
                threading.Thread(target=load_models, args=("small",), daemon=True).start()
                rumps.notification("LocalFlow", "Switched to multilingual model",
                                   "small.en only supports English.")
            save_config(self.cfg)
            self._update_lang_label()
        return _cb

    # ── Menu callbacks ─────────────────────────────────────────────────────────
    def _change_hotkey(self, _):
        w = rumps.Window(title="Change Trigger Key",
                         message="Enter trigger key: alt, cmd, or ctrl",
                         default_text=self.cfg.get("trigger_key", "alt"),
                         ok="Save", cancel="Cancel", dimensions=(200, 24))
        r = w.run()
        if r.clicked:
            key = r.text.strip().lower()
            if key in KEY_MAP:
                self.cfg["trigger_key"] = key
                self._hotkeys = KEY_MAP[key]
                if _HAS_NSEVENT:
                    self._ns_mod_mask = _NS_MOD_FLAGS.get(key, 0x80000)
                    self._ns_mod_key  = _NS_MOD_KEYS.get(key, keyboard.Key.alt)
                save_config(self.cfg)
                rumps.notification("LocalFlow", f"Trigger Key: {key.upper()}", "")
            else:
                rumps.alert("Invalid Key", "Must be: alt, cmd, or ctrl")

    def _toggle_speed_accuracy(self, _):
        """Toggle between small.en (fast) and medium.en (accurate).

        Guards against the v0.2.0/0.2.1 crash loop: previously this saved the
        config to medium.en unconditionally, and if the model wasn't cached
        and HF download failed, the app crash-looped trying to load a model
        it couldn't get.  Now we PREFLIGHT — if switching TO medium.en, we
        first check the model is cached locally.  If not, we tell the user
        to run restore-models.sh and we DON'T save the config.
        """
        current = self.cfg.get("whisper_model", "small.en")
        new_model = "medium.en" if current == "small.en" else "small.en"

        # Preflight: when switching to medium.en, verify it's cached on disk.
        # The local cache is set via HF_HOME at the top of this file.
        if new_model == "medium.en":
            hub_dir = os.path.join(_LOCALFLOW_MODELS_DIR, "hub")
            medium_dir = os.path.join(hub_dir, "models--Systran--faster-whisper-medium.en")
            snapshots = os.path.join(medium_dir, "snapshots")
            model_present = False
            try:
                if os.path.isdir(snapshots):
                    revs = [r for r in os.scandir(snapshots) if r.is_dir()]
                    # Check the largest file in any revision is >1 GB
                    # (medium.en weights are ~1.5 GB; refuse partial downloads)
                    for r in revs:
                        for entry in os.scandir(r.path):
                            try:
                                # Follow symlinks to blob and stat the real file
                                size = os.stat(entry.path).st_size
                                if size > 1_000_000_000:
                                    model_present = True
                                    break
                            except Exception:
                                continue
                        if model_present:
                            break
            except Exception:
                model_present = False

            if not model_present:
                rumps.notification(
                    "LocalFlow",
                    "Accurate mode unavailable",
                    "medium.en (~1.5 GB) not downloaded yet. "
                    "Run ~/localflow/restore-models.sh to fetch it."
                )
                print("[ModelSwitch] Refused — medium.en not cached locally", flush=True)
                return  # Do NOT save config — stays on small.en

        # Apply switch
        self.cfg["whisper_model"] = new_model
        if new_model == "medium.en":
            self._speed_item.title = "🐢 Accurate mode ON"
            rumps.notification("LocalFlow", "Switched to Accurate mode",
                               "medium.en will load on next recording — slightly slower, better accuracy.")
        else:
            self._speed_item.title = "⚡ Fast mode ON"
            rumps.notification("LocalFlow", "Switched to Fast mode",
                               "small.en — faster, slightly less accurate.")
        save_config(self.cfg)
        # Hot-reload happens automatically on next recording via load_config() in __process_inner

    def _open_dashboard(self, _):
        import webbrowser
        webbrowser.open("http://localhost:5050")

    def _update_recording_title(self, _):
        """Tick once per second while recording — show elapsed time in menu bar.
        Goes from "🔴 0:01" to "🔴 0:15" to "🔴 1:30" as user speaks.
        Skips updates during cancel/rescue so we don't fight that flow."""
        if not self._recording or not self._recording_started_at:
            return
        if self._canceling:
            return  # let the rescue countdown own the title
        elapsed = int(time.time() - self._recording_started_at)
        mm, ss = divmod(elapsed, 60)
        self.title = f"🔴 {mm}:{ss:02d}"

    def _reset_state(self, _):
        """Emergency manual reset — nukes all state without restarting the app.
        Use when the app appears stuck or unresponsive to the hotkey.
        Also reloads config from disk so any manual edits take effect immediately.
        """
        print("[Reset] User invoked manual reset", flush=True)
        # Stop any active stream
        if self._stream:
            try:
                stream = self._stream
                self._stream = None
                self._close_stream_async(stream, "manual_reset")
            except Exception as e:
                print(f"[Reset] stream cleanup error (ignored): {e}", flush=True)
        # Clear all state flags
        self._recording = False
        self._processing = False
        self._canceling = False
        self._pasting = False
        self._mod_key_down = False
        self._cmd_mode_active = False
        self._audio_data = []
        self._pending_clean = None
        self._cancel_cnt = 0
        self._recording_started_at = 0
        self._processing_started_at = 0
        self._pasting_started_at = 0
        self._canceling_started_at = 0
        self._audio_last_frame_count = 0
        self._audio_last_progress_at = 0
        # Reload config from disk so any external edits take effect immediately
        self.cfg = load_config()
        # Reset UI
        self._push(self._idle)
        rumps.notification("LocalFlow", "Reset Complete",
                           "All state cleared. You can record again.")

    def _hard_restart(self, reason: str):
        """Restart the app process without blocking on CoreAudio cleanup."""
        print(f"[Watchdog] FULL RESTART triggered ({reason})", flush=True)
        # Do not synchronously stop/close PortAudio here. This restart path is
        # often entered precisely because CoreAudio cleanup is stuck. execv
        # replaces the process and releases all audio resources at the OS level.
        try:
            self._close_crash_buf(cleanup=False)
        except Exception:
            pass
        time.sleep(0.2)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _close_stream_async(self, stream, reason: str):
        """Close a PortAudio stream off-thread and let watchdog recover hangs."""
        close_id = time.time()
        self._mic_close_started_at = close_id
        self._mic_close_hung_logged = False
        self._mic_close_reason = reason

        def _close_async():
            try:
                stream.stop()
                stream.close()
            except Exception as e:
                print(f"[Mic] async close error ({reason}, ignored): {e}", flush=True)
            finally:
                if getattr(self, '_mic_close_started_at', 0) == close_id:
                    self._mic_close_started_at = 0
                    self._mic_close_hung_logged = False
                    self._mic_close_reason = None

        threading.Thread(target=_close_async, daemon=True).start()

    def _view_history(self, _):
        history_path = APP_DIR / "history.md"
        if not history_path.exists():
            history_path.write_text("# Dictation History\n\n")
        subprocess.run(["open", str(history_path)])

    def _add_custom_word(self, _):
        w = rumps.Window(title="Add Custom Word",
                         message="Enter a word/phrase Whisper should always recognize correctly:",
                         default_text="",
                         ok="Add", cancel="Cancel", dimensions=(300, 24))
        r = w.run()
        if r.clicked and r.text.strip():
            word = r.text.strip()
            words = self.cfg.get("custom_words", [])
            if word not in words:
                words.append(word)
                self.cfg["custom_words"] = words
                save_config(self.cfg)
                rumps.notification("LocalFlow", "Custom Word Added", f'"{word}" will now be recognized.')
            else:
                rumps.alert("Already exists", f'"{word}" is already in your dictionary.')

    def _add_snippet(self, _):
        w1 = rumps.Window(title="Add Voice Snippet - Step 1",
                          message="Enter the trigger phrase you'll say to expand the snippet:",
                          default_text="",
                          ok="Next", cancel="Cancel", dimensions=(300, 24))
        r1 = w1.run()
        if not r1.clicked or not r1.text.strip():
            return
        trigger = r1.text.strip()
        w2 = rumps.Window(title=f'Add Voice Snippet - Step 2 (trigger: "{trigger}")',
                          message="Enter the full text this phrase should expand to:",
                          default_text="",
                          ok="Save", cancel="Cancel", dimensions=(400, 80))
        r2 = w2.run()
        if r2.clicked and r2.text.strip():
            snippets = self.cfg.get("snippets", {})
            snippets[trigger] = r2.text.strip()
            self.cfg["snippets"] = snippets
            save_config(self.cfg)
            rumps.notification("LocalFlow", "Snippet Saved", f'Say "{trigger}" to expand.')

    # ── Audio ──────────────────────────────────────────────────────────────────
    def _audio_cb(self, indata, frames, t, status):
        self._audio_data.append(indata.copy())
        # Crash-safe buffer: write every chunk to disk immediately so even a
        # process crash preserves the audio.  The file is a raw PCM append
        # (fast, no seeking) — converted to WAV on recovery.
        buf = getattr(self, '_crash_buf_file', None)
        if buf and not buf.closed:
            try:
                buf.write(indata.copy().tobytes())
            except Exception:
                pass  # never let buffer I/O kill the audio callback

    _CRASH_BUF_PATH = Path(tempfile.gettempdir()) / "localflow_crash_audio.pcm"
    _CRASH_BUF_WAV  = Path(tempfile.gettempdir()) / "localflow_crash_audio.wav"

    def _open_crash_buf(self):
        """Open the crash-safe PCM buffer file for writing."""
        try:
            self._crash_buf_file = open(self._CRASH_BUF_PATH, 'wb')
        except Exception as e:
            print(f"[CrashBuf] failed to open: {e}", flush=True)
            self._crash_buf_file = None

    def _close_crash_buf(self, cleanup=True):
        """Close the crash buffer.  If cleanup=True, delete the file (recording
        was processed normally).  If False, leave it for crash recovery."""
        buf = getattr(self, '_crash_buf_file', None)
        if buf and not buf.closed:
            try:
                buf.close()
            except Exception:
                pass
        self._crash_buf_file = None
        if cleanup and self._CRASH_BUF_PATH.exists():
            try:
                self._CRASH_BUF_PATH.unlink()
            except Exception:
                pass

    def _recover_crash_audio(self):
        """On startup, check for leftover crash buffer from a process crash.
        If found, convert to WAV, transcribe, and save to history."""
        if not self._CRASH_BUF_PATH.exists():
            return
        size = self._CRASH_BUF_PATH.stat().st_size
        if size < SAMPLE_RATE * 2:  # less than ~0.5s of audio
            self._CRASH_BUF_PATH.unlink(missing_ok=True)
            return
        print(f"[CrashBuf] Found {size} bytes of crash audio — recovering", flush=True)
        try:
            raw_pcm = np.frombuffer(self._CRASH_BUF_PATH.read_bytes(), dtype=np.float32)
            dur = len(raw_pcm) / SAMPLE_RATE
            print(f"[CrashBuf] Recovered {dur:.1f}s of audio", flush=True)
            sf.write(str(self._CRASH_BUF_WAV), raw_pcm, SAMPLE_RATE)
            # Process on a background thread once models are loaded
            def _do_recover():
                _recovery_succeeded = False
                # Wait for models to be ready
                for _ in range(60):
                    if _whisper_model is not None:
                        break
                    time.sleep(1)
                if _whisper_model is None:
                    # CC stability fix #4: DON'T delete crash audio if we can't transcribe it.
                    # Leave it for next startup attempt.
                    print("[CrashBuf] Models not ready — keeping crash audio for next startup", flush=True)
                    rumps.notification("LocalFlow", "Crash Audio Found",
                                       "Previous recording preserved. Will retry on next restart.")
                    return  # don't clean up — leave files for next startup
                try:
                    segments, _ = _whisper_model.transcribe(
                        str(self._CRASH_BUF_WAV),
                        beam_size=3, language="en",
                        condition_on_previous_text=False,
                        vad_filter=True,
                        initial_prompt=_build_whisper_prompt(self.cfg),
                    )
                    raw_text = " ".join(s.text.strip() for s in segments).strip()
                    if raw_text and len(raw_text) > 5:
                        # Save to history
                        history_path = APP_DIR / "history.md"
                        now_str = time.strftime("%Y-%m-%d %I:%M:%S %p")
                        entry = f"### {now_str} (⚠️ RECOVERED from crash)\n**Raw:** {raw_text}\n\n**Output:** {raw_text}\n\n---\n\n"
                        with open(history_path, "a", encoding="utf-8") as f:
                            f.write(entry)
                        print(f"[CrashBuf] Recovered and saved to history: '{raw_text[:60]}...'", flush=True)
                        rumps.notification("LocalFlow", "Recording Recovered",
                                           f"Recovered {dur:.0f}s from last crash. Check history.")
                    _recovery_succeeded = True
                except Exception as e:
                    print(f"[CrashBuf] Recovery transcription failed: {e}", flush=True)
                    _recovery_succeeded = True  # transcription ran — even if it failed, don't retry
                finally:
                    # CC stability fix #4: Only delete crash audio after successful processing
                    if _recovery_succeeded:
                        self._CRASH_BUF_PATH.unlink(missing_ok=True)
                        self._CRASH_BUF_WAV.unlink(missing_ok=True)
            threading.Thread(target=_do_recover, daemon=True).start()
        except Exception as e:
            # CC stability fix #4: DON'T delete crash audio on recovery failure
            print(f"[CrashBuf] Recovery failed (keeping files for retry): {e}", flush=True)

    def _start_rec(self):
        # Guard 1: Toggle stop-press arrived before the OS scheduled this thread.
        # If _recording was already cleared, bail out immediately — nothing to open.
        if not self._recording:
            return
        self._audio_data = []
        self._audio_last_frame_count = 0
        self._audio_last_progress_at = time.time()
        self._open_crash_buf()
        # Do NOT pre-emptively call sd._terminate()/_initialize() here.
        # Pa_Terminate() blocks the calling thread until PortAudio fully winds down,
        # which can take several seconds when called immediately after a previous session.
        # This froze the hotkey callback thread, preventing key-release events from
        # being processed and causing the stuck-processing watchdog to fire.
        # Fix: try opening the stream directly first; only fall back to terminate/initialize
        # if the stream actually fails (device-switch error path).
        for attempt in range(2):
            try:
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE, channels=CHANNELS, callback=self._audio_cb)
                stream.start()
                # Guard 2: Toggle stop-press arrived while PortAudio was opening.
                # _stop_rec() intentionally sees _stream=None until stream.start()
                # succeeds, so it cannot race CoreAudio by closing a half-started
                # stream. Close the local stream here instead.
                if not self._recording:
                    self._close_stream_async(stream, "late_stop_guard")
                    return
                self._stream = stream
                if attempt > 0:
                    print(f"[Mic] Recovered on retry", flush=True)
                return
            except Exception as e:
                print(f"[Mic] Attempt {attempt+1} failed: {e}", flush=True)
                if attempt == 0:
                    # Stream open failed (e.g. device switched) — refresh PortAudio and retry
                    try:
                        sd._terminate()
                        sd._initialize()
                    except Exception:
                        pass
                    time.sleep(0.3)
        # Both attempts failed
        self._recording = False
        self._stream    = None
        self._push(self._idle)
        rumps.notification("LocalFlow", "Mic Error",
                           "Could not access microphone. Check audio input device.")

    def _stop_rec(self):
        # Detach the stream reference IMMEDIATELY so the calling thread doesn't block.
        # PortAudio stream.stop()/close() can hang for several seconds on device disconnect
        # or driver glitches. Running synchronously on the NSEvent main thread froze the
        # entire UI and triggered the _processing watchdog (drain queue couldn't run).
        # Fix: hand the stream off to a daemon thread for cleanup.
        if self._stream:
            stream = self._stream
            self._stream = None
            self._close_stream_async(stream, "normal_stop")

    # ── Keyboard handler ───────────────────────────────────────────────────────
    def _on_press(self, key, _via_nsevent=False):
        try:
            return self._on_press_inner(key, _via_nsevent)
        except Exception as e:
            print(f"[Listener] _on_press exception (suppressed): {e}")
            import traceback; traceback.print_exc()

    def _on_press_inner(self, key, _via_nsevent=False):
        if _HAS_NSEVENT and key in self._hotkeys and not _via_nsevent:
            return
        if key in self._hotkeys:
            # Debounce: NSEvent + CGTap may BOTH fire on the same physical keypress.
            # 400ms cooldown prevents double-fire (start→immediate-stop). The fastest
            # physical key re-press is ~100ms, so 400ms is safe.
            _now = time.time()
            if _now - getattr(self, '_last_hotkey_time', 0) < 0.4:
                return
            self._last_hotkey_time = _now
            # Log state for debugging dead-hotkey issues
            print(f"[Hotkey] rec={self._recording} proc={self._processing} "
                  f"cancel={self._canceling} paste={self._pasting}", flush=True)
            if _loading_model:
                self._push("⏳ Model loading...")
                return
            if self._pasting:
                # Previous paste still in flight — block new recording to avoid PortAudio race
                self._push("⌛ Pasting...")
                threading.Timer(0.6, lambda: self._push(self._idle)).start()
                return
            if not self._recording and not self._processing and not self._canceling:
                # Kill clipboard watcher + do final clipboard check
                self._stop_watcher_with_final_check()
                self._recording = True
                self._recording_started_at = time.time()
                self._ns_last_refresh = 0       # reset for periodic monitor refresh
                self._push("🔴 Recording...")
                # Start the 1-second elapsed-time tick so the menu bar shows
                # "🔴 0:15" etc instead of a static label.
                self._recording_timer.start()
                # NOTE: Auto-snapshot disabled 2026-05-20 — too invasive. It was
                # firing Cmd+A/Cmd+C/Right arrow on every recording start, which
                # selected/deselected text in whatever field the user was focused
                # on. Confusing UX and only worked if user stayed in same field.
                # Falls back to clipboard-watcher learning (user copies corrected
                # word -> we detect it) which is opt-in and non-invasive.
                # threading.Thread(target=self._snapshot_and_learn, daemon=True).start()
                # _start_rec() opens the PortAudio stream — that C call can stall.
                # Run it on a daemon thread so the main run loop is never blocked.
                threading.Thread(target=self._start_rec, daemon=True).start()
                # CGEventTap is now persistent (started at boot) — no per-recording tap needed.
                return
            elif self._processing:
                # CC stability fix #5: User pressed trigger while still processing.
                # Show feedback instead of silently ignoring the press.
                self._push("⏳ Still processing...")
                threading.Timer(1.0, lambda: self._push("👨‍🍳 Cooking...") if self._processing else None).start()
                return
            elif self._recording:
                self._recording  = False
                self._recording_timer.stop()  # halt duration tick before transition
                self._processing = True
                self._processing_started_at = time.time()
                self._stop_rec()
                self._close_crash_buf(cleanup=True)  # processed normally — delete buffer
                self._push("👨‍🍳 Cooking...")
                frames = self._audio_data.copy()
                threading.Thread(target=self._process, args=(frames,), daemon=True).start()
                return
        if key == keyboard.Key.esc:
            if self._recording:
                self._recording = False
                self._stop_rec()
                self._close_crash_buf(cleanup=True)  # user cancelled — delete buffer
                self._audio_data = []
                self._push(self._idle)
            elif self._processing and not self._canceling:
                self._canceling  = True
                self._canceling_started_at = time.time()
                self._cancel_cnt = 5
                self._push(f"⏳ Rescue? Press ESC ({self._cancel_cnt})...")
            elif self._canceling:
                self._push("__RESCUE__")

    def _on_release(self, key, _via_nsevent=False):
        try:
            return self._on_release_inner(key, _via_nsevent)
        except Exception as e:
            print(f"[Listener] _on_release exception (suppressed): {e}")
            import traceback; traceback.print_exc()

    def _on_release_inner(self, key, _via_nsevent=False):
        if _HAS_NSEVENT and key in self._hotkeys and not _via_nsevent:
            return
        # Hold mode removed — Toggle is the only mode. Release does nothing.

    def _arm_toggle_latch_clear(self):
        """Clear the modifier-down latch after a toggle press.

        Toggle mode only needs the press edge. If macOS drops the matching
        FlagsChanged release event, _mod_key_down can stay True and the next
        Option press gets ignored, leaving the menu icon orange/stuck. Command
        mode still depends on release, so this watchdog is only armed for plain
        toggle recording.
        """
        def _clear():
            if self._mod_key_down and not self._cmd_mode_active:
                self._mod_key_down = False
                print("[Hotkey] toggle latch auto-cleared", flush=True)
        t = threading.Timer(0.35, _clear)
        t.daemon = True
        t.start()

    def _start_persistent_cgtap(self):
        """Start a persistent CGEventTap that runs for the lifetime of the app.

        CGEventTap is lower-level than NSEvent — it taps directly into the
        macOS event stream before AppKit sees it.  This catches modifier
        key presses even when NSEvent silently drops FlagsChanged events.

        ARCHITECTURE (2026-05-11): CGTap is now the PRIMARY always-on detector,
        not a per-recording backup.  NSEvent is the bonus.  A 400ms debounce
        in _on_press_inner prevents double-fire when both detect the same press.

        The tap runs forever on its own CFRunLoop daemon thread.  If the thread
        dies (macOS revokes accessibility), the watchdog restarts it.
        """
        if not _HAS_CGTAP:
            return

        # Kill any existing tap before creating a new one
        self._kill_cgtap()

        mask = self._ns_mod_mask
        app_ref = self

        state = {"last_down": False, "tap": None, "loop_ref": None}
        self._cgtap_state = state

        def _callback(proxy, event_type, event, refcon):
            flags = _CGGetFlags(event)
            down_now = bool(flags & mask)
            if down_now and not state["last_down"]:
                action = "stop" if app_ref._recording else "start"
                print(f"[CGTap] modifier press → {action} recording", flush=True)
                app_ref._on_press_inner(app_ref._ns_mod_key, _via_nsevent=True)
            state["last_down"] = down_now
            return event

        def _run_tap():
            tap = _CGTapCreate(
                kCGSessionEventTap,
                kCGHeadInsertEventTap,
                kCGEventTapOptionListenOnly,
                _CGMaskBit(kCGEventFlagsChanged),
                _callback,
                None,
            )
            if tap is None:
                print("[CGTap] failed to create event tap (accessibility?)", flush=True)
                return
            state["tap"] = tap
            source = _CFMachPortSource(None, tap, 0)
            loop = _CFRunLoopCurrent()
            state["loop_ref"] = loop
            _CFRunLoopAdd(loop, source, kCFRunLoopCommonModes)
            _CGTapEnable(tap, True)
            print("[CGTap] persistent tap running", flush=True)
            _CFRunLoopRun()  # runs forever — only exits if tap is invalidated
            # Cleanup if RunLoop exits unexpectedly
            print("[CGTap] RunLoop exited — tap will be restarted by watchdog", flush=True)
            try:
                _CGTapEnable(tap, False)
                _CFMachPortInvalidate(tap)
            except Exception:
                pass

        t = threading.Thread(target=_run_tap, daemon=True)
        t.start()
        self._cgtap_thread = t

    def _kill_cgtap(self):
        """Stop and clean up any existing CGEventTap from a previous recording.

        CRITICAL: This may be called from inside the CGTap thread itself
        (when CGTap fires _on_press_inner which then calls _kill_cgtap).
        Joining the current thread raises RuntimeError, so we detect that
        case and skip the join — the RunLoopStop signal will cause the
        thread to exit on its own.
        """
        old_state = getattr(self, '_cgtap_state', None)
        if old_state:
            old_state["fired"] = True  # prevent any more callbacks from firing
            if old_state.get("loop_ref"):
                try:
                    _CFRunLoopStop(old_state["loop_ref"])
                except Exception:
                    pass
            if old_state.get("tap"):
                try:
                    _CGTapEnable(old_state["tap"], False)
                    _CFMachPortInvalidate(old_state["tap"])
                except Exception:
                    pass
        old_thread = getattr(self, '_cgtap_thread', None)
        # Only join if we're NOT on the CGTap thread itself
        if (old_thread and old_thread.is_alive()
                and old_thread is not threading.current_thread()):
            try:
                old_thread.join(timeout=0.5)
            except RuntimeError:
                pass  # belt-and-suspenders against same-thread-join
        self._cgtap_state = None
        self._cgtap_thread = None

    def _ns_key_handler(self, event):
        """Handle non-modifier rescue keys through the native macOS monitor."""
        try:
            if int(event.keyCode()) == 53:  # Escape
                self._on_press(keyboard.Key.esc, _via_nsevent=True)
        except Exception as e:
            print(f"[NSEvent] key handler exception (suppressed): {e}", flush=True)

    def _ns_modifier_handler(self, event):
        """NSEvent modifier handler — also detects Option+Cmd for Command Mode.

        CRITICAL: This callback fires on the AppKit main thread's run loop.

        Architecture: press/release are processed SYNCHRONOUSLY here to preserve
        strict event ordering — if both were dispatched to background threads, the
        OS scheduler could run the release thread before the press thread, causing
        _recording to be False when release fires so it gets silently ignored, then
        True after press runs with no release handler coming → stuck recording.

        The only PortAudio call that can block is stream.start() inside _start_rec().
        That is dispatched to a background thread from _on_press_inner / _start_command_mode.
        Everything else (flag ops, _stop_rec async close, thread spawning) is instant.
        """
        flags        = event.modifierFlags()
        trigger_down = bool(flags & self._ns_mod_mask)
        cmd_down     = bool(flags & _NS_CMD_MASK)
        # Command mode = trigger + Cmd, only when trigger_key is not cmd itself
        is_cmd_mode  = cmd_down and self.cfg.get("trigger_key") != "cmd"

        if trigger_down and not self._mod_key_down:
            self._mod_key_down    = True
            self._cmd_mode_active = is_cmd_mode
            if is_cmd_mode:
                self._start_command_mode()       # fast: flag + async _start_rec thread
            else:
                self._on_press(self._ns_mod_key, _via_nsevent=True)   # fast: flag + async _start_rec thread
                self._arm_toggle_latch_clear()
        elif not trigger_down and self._mod_key_down:
            self._mod_key_down = False
            if self._cmd_mode_active:
                self._cmd_mode_active = False
                self._stop_command_mode()        # fast: flag + async _stop_rec + process thread
            else:
                self._on_release(self._ns_mod_key, _via_nsevent=True) # fast: flag + async _stop_rec + process thread

    # ── Command Mode ───────────────────────────────────────────────────────────
    def _start_command_mode(self):
        if self._recording or self._processing or self._canceling:
            return
        self._recording = True
        self._push("🔧 Command...")
        threading.Thread(target=self._start_rec, daemon=True).start()

    def _stop_command_mode(self):
        if not self._recording:
            return
        self._recording  = False
        self._processing = True
        self._stop_rec()
        self._push("⚙️ Transforming...")
        frames = self._audio_data.copy()
        threading.Thread(target=self._run_command, args=(frames,), daemon=True).start()

    def _run_command(self, audio_frames):
        import traceback
        try:
            self.__run_command_inner(audio_frames)
        except Exception:
            print(f"[Command] CRASH:\n{traceback.format_exc()}", flush=True)
            self._processing = False
            self._push(self._idle)

    def __run_command_inner(self, audio_frames):
        if not audio_frames or not _whisper_model:
            self._processing = False
            self._push(self._idle)
            return

        # 1. Transcribe the instruction
        audio_np = np.concatenate(audio_frames, axis=0)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_path = tf.name
        sf.write(wav_path, audio_np, SAMPLE_RATE)
        try:
            segments, _ = _whisper_model.transcribe(
                wav_path, beam_size=3, vad_filter=True,
                initial_prompt="Voice editing instruction.",
            )
            instruction = " ".join(s.text.strip() for s in segments).strip()
            instruction = re.sub(r"\[\d+:\d+\.\d+ --> \d+:\d+\.\d+\]", "", instruction).strip()
        finally:
            if os.path.exists(wav_path):
                os.remove(wav_path)

        if not instruction:
            self._processing = False
            self._push(self._idle)
            return

        # 2. Save original clipboard
        original_cb = _clipboard_read()

        # 3. Grab selected text via Cmd+C
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "c" using command down'],
            check=False, timeout=3
        )
        time.sleep(0.15)
        selection = _clipboard_read()

        # If clipboard didn't change, nothing was selected
        if selection == original_cb:
            selection = ""

        if not selection:
            # Nothing selected — just notify and bail
            _clipboard_write(original_cb)
            self._processing = False
            self._push(self._idle)
            rumps.notification("LocalFlow", "Command Mode",
                               "No text selected. Highlight text first, then use Option+Cmd.")
            return

        # 4. Transform via LLM
        result = _command_transform(selection, instruction)

        if not result:
            _clipboard_write(original_cb)
            self._processing = False
            self._push(self._idle)
            return

        # 5. Paste result
        _clipboard_write(result)
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" using command down'],
            check=False, timeout=3
        )
        time.sleep(0.2)

        # 6. Restore original clipboard
        _clipboard_write(original_cb)

        self._processing = False
        self._push(self._idle)

    # ── Cancel/Rescue ──────────────────────────────────────────────────────────
    def _drain_queue(self, _):
        while not self._title_q.empty():
            msg = self._title_q.get_nowait()
            if isinstance(msg, str) and msg == "__REFRESH_MONITOR__":
                # Re-register NSEvent modifier monitor ON THE MAIN THREAD.
                # This is critical — monitors registered from background threads
                # are silently dead because they miss the AppKit run loop.
                if _HAS_NSEVENT and self._ns_monitor:
                    try:
                        _NSEvent.removeMonitor_(self._ns_monitor)
                        self._ns_monitor = _NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                            1 << 12, self._ns_modifier_handler)
                        self._mod_key_down = False  # clear stale latch
                        print("[Monitor] re-registered on main thread", flush=True)
                    except Exception as e:
                        print(f"[Monitor] re-register failed: {e}", flush=True)
                continue
            if isinstance(msg, str) and msg == "__RESCUE__":
                self._countdown.stop()
                self._canceling = False
                self._canceling_started_at = 0
                self.title = "👨‍🍳 Cooking..."
                if self._pending_clean and self._processing:
                    t = self._pending_clean
                    self._pending_clean = None
                    self._processing    = False
                    self.title          = self._idle
                    self._pasting = True
                    def _rescue_paste(t=t):
                        try:
                            type_text(t)
                        finally:
                            self._pasting = False
                    threading.Thread(target=_rescue_paste, daemon=True).start()
            elif isinstance(msg, str) and msg.startswith("__PASTE__"):
                text = msg[len("__PASTE__"):]
                self._processing = False
                self.title = self._idle

                # Auto-space between consecutive recordings: if the previous paste
                # happened within 30s and didn't end with whitespace, prepend a space
                # so consecutive dictations don't run together (e.g. "hello.world" → "hello. world").
                now = time.time()
                last_t = getattr(self, "_last_paste_time", 0)
                last_end = getattr(self, "_last_paste_end_char", "")
                gap = now - last_t
                will_prepend = (gap < 30 and last_end and not last_end.isspace()
                                and text and not text[0].isspace())
                _new_first = text[0] if text else ''
                print(f"[AutoSpace] gap={gap:.1f}s last_end={last_end!r} new_first={_new_first!r} prepend={will_prepend}", flush=True)
                if will_prepend:
                    text = " " + text
                self._last_paste_time = now
                self._last_paste_end_char = text[-1] if text else ""

                self._pasting = True
                self._pasting_started_at = time.time()
                def _paste_and_clear(t):
                    try:
                        type_text(t)
                    finally:
                        self._pasting = False
                        self._pasting_started_at = 0
                threading.Thread(target=_paste_and_clear, args=(text,), daemon=True).start()
                # Store for auto-learning: snapshot compares field at next rec start
                self._last_pasted_text = text
                self._last_pasted_text_time = time.time()
                # Clipboard watcher as secondary learning mechanism
                self._start_correction_watcher(text)
            else:
                self.title = msg
                if "⏳ Rescue" in msg and not self._countdown.is_alive():
                    self._countdown.start()

    def _cancel_tick(self, _):
        if not self._canceling:
            self._countdown.stop()
            return
        self._cancel_cnt -= 1
        if self._cancel_cnt > 0:
            self.title = f"⏳ Rescue? Press ESC ({self._cancel_cnt})..."
        else:
            self._countdown.stop()
            self._canceling     = False
            self._canceling_started_at = 0
            self._processing    = False
            self._pending_clean = None
            self.title          = self._idle

    # ── Auto-learning: field snapshot at recording start ─────────────────────
    def _snapshot_and_learn(self):
        """Silently read the focused text field and learn from user edits.

        Called in a background thread at the start of each new recording.
        Compares the current field content with what we pasted last time.
        Any word substitutions the user made are saved as learned corrections.

        Method: Cmd+A → Cmd+C → read clipboard → Right arrow (deselect) →
        restore clipboard.  Total time ~300ms, runs in parallel with mic init
        so the user sees zero delay.
        """
        pasted = getattr(self, '_last_pasted_text', None)
        paste_age = time.time() - getattr(self, '_last_pasted_text_time', 0)
        if not pasted or len(pasted.split()) < 2 or paste_age > 300:
            # No recent paste, or too old (>5min) — skip
            return

        try:
            # Save current clipboard
            original_cb = _clipboard_read()

            # Select all + copy in the focused field
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to keystroke "a" using command down'],
                check=False, timeout=2, capture_output=True
            )
            time.sleep(0.1)
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to keystroke "c" using command down'],
                check=False, timeout=2, capture_output=True
            )
            time.sleep(0.15)

            field_text = _clipboard_read().strip()

            # Deselect: right arrow moves cursor to end without altering content
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to key code 124'],  # right arrow
                check=False, timeout=2, capture_output=True
            )

            # Restore the user's original clipboard
            _clipboard_write(original_cb)

            if not field_text or field_text == original_cb:
                print("[Learning] Snapshot: clipboard unchanged — no text field focused", flush=True)
                return

            print(f"[Learning] Snapshot: got {len(field_text)} chars from field", flush=True)
            self._learn_from_field_snapshot(field_text, pasted)

        except Exception as e:
            print(f"[Learning] Snapshot failed: {e}", flush=True)
        finally:
            self._last_pasted_text = None

    def _learn_from_field_snapshot(self, field_text: str, pasted_text: str):
        """Compare field content with what we pasted to find user corrections."""
        import difflib
        pasted_words = pasted_text.lower().split()
        field_words = field_text.lower().split()

        # Case 1: field text is about the same length as pasted text (±3 words).
        # This is the common case — user dictated into an empty/near-empty field.
        if abs(len(field_words) - len(pasted_words)) <= 3:
            ratio = difflib.SequenceMatcher(
                None, pasted_text.lower(), field_text.lower()).ratio()
            if 0.75 <= ratio <= 0.99:
                print(f"[Learning] Direct match — ratio={ratio:.2f}, running correction diff", flush=True)
                self._check_for_corrections(field_text, pasted_text, pasted_words)
                return
            elif ratio > 0.99:
                print("[Learning] Field unchanged from paste — no corrections", flush=True)
                return

        # Case 2: field has more text — search for our pasted region with a
        # sliding window.  Only needed when the user was composing a longer
        # document and our paste is embedded somewhere inside it.
        if len(field_words) > len(pasted_words) + 3:
            window = len(pasted_words)
            best_ratio = 0.0
            best_region_words = None

            for start in range(len(field_words) - window + 3):
                # Try window sizes ±2 words to handle insertions/deletions
                for extra in range(-2, 3):
                    end = start + window + extra
                    if end <= start or end > len(field_words):
                        continue
                    region = " ".join(field_words[start:end])
                    ratio = difflib.SequenceMatcher(
                        None, pasted_text.lower(), region).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_region_words = field_words[start:end]

            if best_region_words and 0.75 <= best_ratio <= 0.99:
                region_text = " ".join(best_region_words)
                print(f"[Learning] Window match — ratio={best_ratio:.2f}, running correction diff", flush=True)
                self._check_for_corrections(region_text, pasted_text, pasted_words)
            elif best_ratio > 0.99:
                print("[Learning] Window match — no corrections (unchanged)", flush=True)
            else:
                print(f"[Learning] No matching region found (best={best_ratio:.2f})", flush=True)

    # ── Hybrid learning: clipboard correction watcher (secondary) ─────────────
    def _start_correction_watcher(self, pasted_text: str):
        """Watch clipboard for user corrections until the next recording starts.

        Two detection modes:
        1. FULL-TEXT: user copies the entire corrected paragraph (75-98% similar).
        2. SINGLE-WORD: user selects a corrected word and Cmd+C's it. We match
           it against the words we pasted using text similarity + consonant-
           skeleton phonetic matching.

        The watcher runs continuously until the next recording starts (not a
        fixed timeout), because users may take their time editing.
        """
        import difflib
        pasted_words = pasted_text.lower().split()
        if len(pasted_words) < 2:
            return  # too short to detect a meaningful correction

        # Cancel any existing watcher from a previous paste
        old_stop = getattr(self, '_watcher_stop', None)
        if old_stop:
            old_stop.set()

        stop_event = threading.Event()
        self._watcher_stop = stop_event
        self._watcher_pasted_text = pasted_text
        self._watcher_pasted_words = pasted_words

        threading.Thread(target=self._correction_watcher_loop,
                         args=(pasted_text, pasted_words, stop_event),
                         daemon=True).start()

    def _stop_watcher_with_final_check(self):
        """Stop the correction watcher (called when a new recording starts).
        Does one last clipboard read before shutting down."""
        stop_event = getattr(self, '_watcher_stop', None)
        pasted = getattr(self, '_watcher_pasted_text', None)
        pasted_words = getattr(self, '_watcher_pasted_words', None)
        if stop_event and pasted and pasted_words:
            # One final clipboard check before killing the watcher
            try:
                current = _clipboard_read().strip()
                if current and current != pasted:
                    self._check_for_corrections(current, pasted, pasted_words)
            except Exception:
                pass
            stop_event.set()
        self._watcher_stop = None
        self._watcher_pasted_text = None
        self._watcher_pasted_words = None

    def _correction_watcher_loop(self, pasted_text: str, pasted_words: list,
                                  stop_event: threading.Event):
        import difflib
        last_seen = pasted_text
        while not stop_event.is_set():
            stop_event.wait(2)  # sleep 2s, or wake immediately if stopped
            if stop_event.is_set():
                break
            try:
                current = _clipboard_read().strip()
            except Exception:
                continue
            if current == last_seen or not current:
                continue
            last_seen = current
            if current == pasted_text:
                continue  # clipboard unchanged from what we pasted

            self._check_for_corrections(current, pasted_text, pasted_words)

    def _check_for_corrections(self, current: str, pasted_text: str,
                                pasted_words: list):
        """Detect corrections by comparing clipboard content to what we pasted.
        Handles both full-text and single-word clipboard content."""
        import difflib
        current_words = current.lower().split()

        # ── MODE 1: Single-word or short phrase (1-3 words) ──────────────
        # The user selected just the corrected word(s) and copied them.
        if 1 <= len(current_words) <= 3:
            self._detect_single_word_correction(current_words, pasted_words)
            return

        # ── MODE 2: Full text (user copied the whole corrected paragraph) ─
        if abs(len(pasted_words) - len(current_words)) > 2:
            return

        # Similarity check: 75-98%
        ratio = difflib.SequenceMatcher(None, pasted_text.lower(),
                                        current.lower()).ratio()
        if ratio < 0.75 or ratio > 0.99:
            return

        # Find substituted word pairs
        matcher = difflib.SequenceMatcher(None, pasted_words, current_words)
        corrections = []
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == 'replace' and (i2 - i1) <= 2 and (j2 - j1) <= 2:
                old_phrase = " ".join(pasted_words[i1:i2])
                new_phrase = " ".join(current_words[j1:j2])
                if old_phrase != new_phrase and len(old_phrase) >= 3 and len(new_phrase) >= 3:
                    corrections.append((old_phrase, new_phrase))

        if not corrections or len(corrections) > 3:
            return  # too many changes = probably unrelated copy

        # Filter out pure punctuation differences (formatting noise)
        corrections = [
            (o, n) for o, n in corrections
            if o.strip(".,!?;:'\"") != n.strip(".,!?;:'\"")
        ]
        if not corrections:
            return

        saved = []
        for old_word, new_word in corrections:
            if self._save_learned_correction(old_word, new_word):
                saved.append((old_word, new_word))

        if not saved:
            return

        if len(saved) == 1:
            o, n = saved[0]
            rumps.notification("LocalFlow", "📚 Learned correction",
                               f'"{o}" → "{n}" saved automatically')
        else:
            rumps.notification("LocalFlow",
                               f"📚 Learned {len(saved)} corrections",
                               " | ".join(f'"{o}"→"{n}"' for o, n in saved))

    def _detect_single_word_correction(self, clipboard_words: list,
                                        pasted_words: list):
        """Match a copied word against words we pasted to find corrections.

        Uses two matching strategies:
        1. Text similarity (SequenceMatcher ratio >= 0.6)
        2. Consonant-skeleton phonetic matching (ratio >= 0.85 with first-2 guard)

        Both strategies require the words to differ (otherwise it's just a copy,
        not a correction) and be at least 4 chars each (to avoid matching
        tiny common words like "the" -> "they").
        """
        import difflib
        cb_str = " ".join(clipboard_words).strip(".,!?;:'\"()[]{}").lower()
        if len(cb_str) < 4:
            return

        best_match = None
        best_score = 0

        for pw in pasted_words:
            pw_clean = pw.strip(".,!?;:'\"()[]{}").lower()
            if len(pw_clean) < 4:
                continue
            if pw_clean == cb_str:
                continue  # same word — not a correction
            if abs(len(pw_clean) - len(cb_str)) > 4:
                continue  # length too different

            # Strategy 1: text similarity
            text_ratio = difflib.SequenceMatcher(None, pw_clean, cb_str).ratio()

            # Strategy 2: consonant skeleton (with first-2 guard)
            skel_pw = _consonant_skeleton(pw_clean)
            skel_cb = _consonant_skeleton(cb_str)
            skel_ratio = 0.0
            first2_match = (skel_pw and skel_cb and len(skel_pw) >= 2
                            and len(skel_cb) >= 2 and skel_pw[:2] == skel_cb[:2])
            if first2_match:
                skel_ratio = difflib.SequenceMatcher(None, skel_pw, skel_cb).ratio()

            # Accept criteria — two tiers:
            # Tier 1: first-2 consonants match → text ≥ 0.6 OR skeleton ≥ 0.85
            # Tier 2: no phonetic match → only accept if text ≥ 0.85 (near-typo)
            # This prevents false positives like "hockey"→"hotkey" (text=0.83 but
            # different starting consonants hk≠ht) while still catching real
            # corrections like "recieve"→"receive" (text=0.93).
            is_match = False
            if first2_match and (text_ratio >= 0.6 or skel_ratio >= 0.85):
                is_match = True
            elif text_ratio >= 0.85:
                is_match = True

            score = max(text_ratio, skel_ratio)
            if is_match and score > best_score:
                best_score = score
                best_match = pw_clean

        if best_match:
            # Use the original case from the clipboard for the correction value
            new_word = " ".join(clipboard_words).strip(".,!?;:'\"()[]{}")
            if self._save_learned_correction(best_match, new_word):
                rumps.notification("LocalFlow", "📚 Learned correction",
                                   f'"{best_match}" → "{new_word}" saved')

    def _save_learned_correction(self, old_word: str, new_word: str) -> bool:
        """Persist a learned word correction to learned_corrections.json.

        Only deterministic domain-token corrections are stored for blind regex
        replacement.  Common words and phrase edits are too contextual and are
        rejected here so they cannot corrupt future dictation.
        """
        old_lc = _normalize_learned_key(old_word)
        new_lc = _normalize_learned_key(new_word)

        if not _is_safe_learned_correction(old_lc, new_lc):
            print(f"[Learning] BLOCKED contextual/common correction: "
                  f"{old_word!r} → {new_word!r}", flush=True)
            return False

        try:
            path = APP_DIR / "learned_corrections.json"
            corrections = {}
            if path.exists():
                corrections = json.loads(path.read_text())
            corrections[old_lc] = new_lc if new_lc != new_word else new_word
            path.write_text(json.dumps(corrections, indent=2))
            print(f"[Learning] Saved: {old_word!r} → {new_word!r}", flush=True)
            return True
        except Exception as e:
            print(f"[Learning] Save failed: {e}", flush=True)
            return False

    # ── Processing (background) ────────────────────────────────────────────────
    def _process(self, audio_frames):
        import traceback
        try:
            self.__process_inner(audio_frames)
        except Exception as _exc:
            tb = traceback.format_exc()
            print(f"[Process] CRASH:\n{tb}", flush=True)
            # Log to crash forensics
            _log_crash_event("processing_exception",
                             error=str(_exc)[:200],
                             audio_frames=len(audio_frames) if audio_frames else 0)
            # CC stability fix #2b: Save audio as crash buffer so it's not lost forever
            try:
                if audio_frames:
                    audio_np = np.concatenate(audio_frames, axis=0)
                    crash_wav = APP_DIR / "recovered_crash.wav"
                    sf.write(str(crash_wav), audio_np, SAMPLE_RATE)
                    print(f"[Process] Crash audio saved to {crash_wav}", flush=True)
                    rumps.notification("LocalFlow", "Processing Error",
                                       f"Audio saved to recovered_crash.wav")
            except Exception:
                pass
            self._processing = False
            self._push(self._idle)

    def __process_inner(self, audio_frames):
        self.cfg = load_config()  # Hot-reload config before starting
        result_text = None

        if _whisper_model is None or _current_model_name != self.cfg["whisper_model"]:
            self._push("⏳ Loading Whisper...")
            load_models(self.cfg["whisper_model"])
            if _whisper_model is None:
                self._processing = False
                self._push(self._idle)
                rumps.notification("LocalFlow", "Whisper Not Ready",
                                   "Model not cached. Connect to internet once to download.")
                return

        if audio_frames and _whisper_model:
            audio_np = np.concatenate(audio_frames, axis=0)
            audio_np, was_trimmed, original_audio_dur = _trim_oversize_audio_for_processing(audio_np)
            if was_trimmed:
                print(
                    f"[Audio] Oversize stale buffer: {original_audio_dur:.2f}s → "
                    f"{len(audio_np) / SAMPLE_RATE:.2f}s tail recovery",
                    flush=True,
                )
                _log_crash_event(
                    "processing_oversize_audio_trimmed",
                    original_audio_dur=round(original_audio_dur, 1),
                    kept_sec=_OVERSIZE_AUDIO_RECOVERY_SEC,
                    original_frames=len(audio_frames),
                )
            # Normalize amplitude before Whisper — improves accuracy on quiet/loud recordings
            audio_np = _normalize_audio(audio_np)
            audio_dur = len(audio_np) / SAMPLE_RATE
            print(f"[Audio] {len(audio_frames)} frames, {audio_dur:.2f}s duration, peak={float(audio_np.max()):.4f}", flush=True)
            if audio_dur < 0.3:
                print(f"[Audio] Too short ({audio_dur:.2f}s) — skipping", flush=True)
                self._processing = False
                self._push(self._idle)
                return
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                wav_path = tf.name
            sf.write(wav_path, audio_np, SAMPLE_RATE)

            # Language param: None = auto-detect, else specific code
            lang_cfg = self.cfg.get("language", "en")
            lang_param = None if lang_cfg == "auto" else lang_cfg

            try:
                import queue as _q
                t0 = time.time()
                whisper_q = _q.Queue()
                def _do_transcribe():
                    try:
                        segments, _ = _whisper_model.transcribe(
                            wav_path,
                            beam_size=3,
                            language=lang_param,
                            condition_on_previous_text=False,
                            vad_filter=True,
                            initial_prompt=_build_whisper_prompt(self.cfg),
                        )
                        whisper_q.put(("ok", " ".join(s.text.strip() for s in segments).strip()))
                    except Exception as e:
                        whisper_q.put(("err", str(e)))

                # CC stability fix #3: Scale Whisper timeout with audio length.
                # medium.en processes ~3-4x realtime; add generous headroom.
                whisper_timeout = max(30, int(audio_dur * 5))
                threading.Thread(target=_do_transcribe, daemon=True).start()
                try:
                    w_status, w_val = whisper_q.get(timeout=whisper_timeout)
                except _q.Empty:
                    print(f"[Whisper] TIMEOUT after {whisper_timeout}s — saving audio to history", flush=True)
                    _log_crash_event("whisper_timeout",
                                     audio_dur=round(audio_dur, 1),
                                     timeout_sec=whisper_timeout,
                                     model=self.cfg.get("whisper_model", "?"))
                    # CC stability fix #3: Save the WAV so user can recover audio
                    try:
                        timeout_wav = APP_DIR / "recovered_timeout.wav"
                        import shutil
                        shutil.copy2(wav_path, str(timeout_wav))
                        # Log to history so it's not completely lost
                        history_path = APP_DIR / "history.md"
                        now_str = time.strftime("%Y-%m-%d %I:%M:%S %p")
                        entry = f"### {now_str} (⚠️ Whisper timeout — {audio_dur:.0f}s audio saved to recovered_timeout.wav)\n**Raw:** [transcription timed out]\n\n**Output:** [transcription timed out]\n\n---\n\n"
                        with open(history_path, "a", encoding="utf-8") as f:
                            f.write(entry)
                        print(f"[Whisper] Audio saved to {timeout_wav}", flush=True)
                    except Exception as save_err:
                        print(f"[Whisper] Failed to save timeout audio: {save_err}", flush=True)
                    rumps.notification("LocalFlow", "Recording too long",
                                       f"Transcription timed out ({audio_dur:.0f}s). Audio saved for recovery.")
                    self._processing = False
                    self._push(self._idle)
                    return
                if w_status != "ok":
                    print(f"[Whisper] error: {w_val}", flush=True)
                    self._processing = False
                    self._push(self._idle)
                    return
                raw = w_val
                print(f"[Whisper] transcribe took {time.time()-t0:.1f}s → raw='{raw[:80] if raw else ''}'", flush=True)
                raw = re.sub(r"\[\d+:\d+\.\d+ --> \d+:\d+\.\d+\]", "", raw)
                raw = re.sub(r"^\[.*?\]\s*", "", raw).strip()

                if not raw:
                    print(f"[Whisper] Empty transcript — nothing to paste", flush=True)
                    self._processing = False
                    self._push("❌ No speech")
                    time.sleep(1.2)
                    self._push(self._idle)
                    return

                # Garbage detector: bypass MLX when Whisper hallucinated on noisy audio.
                # Polishing junk into a real-sounding sentence is more dangerous than failing visibly.
                is_garbage, gb_reason = _is_likely_garbage(raw, audio_dur)
                if is_garbage:
                    print(f"[GarbageDetector] flagged ({gb_reason}) — raw='{raw}' — bypassing MLX", flush=True)
                    self._processing = False
                    self._push("❌ Couldn't hear clearly")
                    time.sleep(1.5)
                    self._push(self._idle)
                    return

                if raw:
                    # App context — always on, zero-config
                    app_ctx = get_frontmost_app_context()
                    cwords = self.cfg.get("custom_words", [])

                    # Custom words: deterministic fuzzy correction BEFORE the LLM
                    raw = _apply_custom_words(raw, cwords)
                    raw = _apply_domain_phrase_corrections(raw)
                    # Learned corrections — two-tier approach:
                    # Tier 1 (safe regex): uncommon words like "enos"→"n8n" are applied
                    #   deterministically — they're never real English words.
                    # Tier 2 (LLM hints): common words like "still"→"stealth" are passed
                    #   to the LLM as context hints — it reads the full sentence and
                    #   decides whether the correction applies.
                    raw = _apply_learned_corrections_safe(raw)
                    lcorr = _load_learned_corrections()
                    # Voice quote markers: "quote-unquote X" → "X"
                    raw = _apply_voice_quote_marks(raw)
                    # Self-corrections: drop "wait no / actually / scratch that" mistakes
                    raw = _apply_self_corrections(raw)

                    # CC stability fix #2: Save raw to history BEFORE post-processing.
                    # If MLX/formatting crashes, the raw transcript is still preserved.
                    now_str = time.strftime("%Y-%m-%d %I:%M:%S %p")
                    _history_entry_time = now_str  # stash for update after clean

                    try:
                        clean = clean_with_mlx(raw, app_ctx, cwords, lcorr)

                        # Track whether MLX actually rewrote the text, or fell back to raw.
                        # _split_paragraphs must ONLY run on MLX output — running it on raw
                        # adds artificial paragraph breaks inside continuous speech (bug: 2026-05-09).
                        _mlx_changed = (clean != raw)

                        # Flatten any LLM-created numbered/bulleted lists FIRST so smart_bullets
                        # is the single source of truth on list formatting.
                        clean = _flatten_llm_lists(clean)
                        # Collapse random single line breaks from the local LLM, then repair
                        # only obvious long run-ons that survived the punctuation pass.
                        clean = _normalize_prose_line_breaks(clean)
                        clean = _repair_long_runons(clean)
                        # Remove obvious filler even when MLX safely fell back to raw.
                        clean = _remove_filler_words(clean)
                        # Number normalization + acronym uppercase + formatting cues + snippets + bullets
                        clean = _normalize_numbers(clean)
                        clean = _apply_acronyms(clean)
                        clean = _apply_formatting_cues(clean)
                        clean = _apply_snippets(clean, self.cfg.get("snippets", {}))
                        clean = _format_spoken_question_list(clean)
                        clean = _smart_bullets(clean)  # auto-detect lists, always on
                        # LAST: enforce paragraph breaks deterministically (1B LLM unreliable here)
                        # Skip if MLX fell back to raw — raw speech should never be auto-split.
                        if _mlx_changed:
                            clean = _split_paragraphs(clean)
                    except Exception as post_err:
                        # Post-processing crashed — fall back to raw (CC stability fix #2)
                        print(f"[Process] Post-processing crashed, falling back to raw: {post_err}", flush=True)
                        clean = raw

                    result_text = clean

                    # History logging (capped to 200 entries)
                    try:
                        history_path = APP_DIR / "history.md"
                        entry = f"### {now_str}\n**Raw:** {raw}\n\n**Output:** {clean}\n\n---\n\n"

                        content = ""
                        if history_path.exists():
                            with open(history_path, "r", encoding="utf-8") as f:
                                content = f.read()

                        blocks = content.split("---")
                        blocks = [b.strip() for b in blocks if b.strip()]
                        blocks.append(entry.strip())

                        if len(blocks) > 200:
                            blocks = blocks[-200:]

                        kept_content = "\n\n---\n\n".join(blocks) + "\n\n---\n\n"

                        with open(history_path, "w", encoding="utf-8") as f:
                            f.write(kept_content)
                    except Exception as e:
                        print(f"Error logging history: {e}")

            except Exception as e:
                print(f"Processing error: {e}", flush=True)
            finally:
                if os.path.exists(wav_path):
                    os.remove(wav_path)

        while self._canceling:
            self._pending_clean = result_text
            time.sleep(0.05)
            if not self._canceling and not self._processing:
                return

        if self._processing and result_text:
            self._push(f"__PASTE__{result_text}")
        else:
            self._processing = False
            self._push(self._idle)


if __name__ == "__main__":
    try:
        from AppKit import NSApplication
        NSApp = NSApplication.sharedApplication()
        # NSApplicationActivationPolicyAccessory = 1 (menu bar only, no Dock icon)
        NSApp.setActivationPolicy_(1)
    except Exception as e:
        print(f"[Dock] Failed to hide Dock icon: {e}")
        
    LocalFlowApp().run()
