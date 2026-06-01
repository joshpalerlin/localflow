import os
import json
import time
from flask import Flask, jsonify, request, render_template, send_from_directory
from pathlib import Path

app = Flask(__name__, template_folder='templates')
APP_DIR = Path(__file__).parent
CONFIG_FILE           = APP_DIR / "config.json"
HISTORY_FILE          = APP_DIR / "history.md"
CRASH_FORENSICS_FILE  = APP_DIR / "crash_forensics.jsonl"
LEARNED_CORRECTIONS   = APP_DIR / "learned_corrections.json"

def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except:
            pass
    return {}

def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/config', methods=['GET'])
def get_config():
    cfg = load_config()
    return jsonify({
        "custom_words": cfg.get("custom_words", []),
        "snippets": cfg.get("snippets", {})
    })

@app.route('/api/config', methods=['POST'])
def update_config():
    cfg = load_config()
    data = request.json
    if "custom_words" in data:
        cfg["custom_words"] = data["custom_words"]
    if "snippets" in data:
        cfg["snippets"] = data["snippets"]
    save_config(cfg)
    return jsonify({"status": "success"})

@app.route('/api/history', methods=['GET'])
def get_history():
    if not HISTORY_FILE.exists():
        return jsonify({"history": []})
    
    try:
        content = HISTORY_FILE.read_text()
        entries = []
        # Parse history file blocks: ### Timestamp \n**Raw:** ... \n\n**Output:** ... \n\n---
        blocks = content.split('---')
        for block in reversed(blocks):
            block = block.strip()
            if not block:
                continue
            
            lines = block.split('\n')
            timestamp = ""
            raw = ""
            output = ""
            
            in_output = False
            output_lines = []
            for line in lines:
                if line.startswith('### '):
                    timestamp = line[4:].strip()
                    in_output = False
                elif line.startswith('**Raw:** '):
                    raw = line[9:].strip()
                    in_output = False
                elif line.startswith('**Output:** '):
                    output_lines = [line[12:].strip()]
                    in_output = True
                elif in_output and line.strip():
                    output_lines.append(line.strip())
            output = '\n'.join(output_lines)
            
            if timestamp:
                entries.append({
                    "timestamp": timestamp,
                    "raw": raw,
                    "output": output
                })
        
        # Return top 100 (frontend paginates at 20/page)
        return jsonify({"history": entries[:100]})
    except Exception as e:
        return jsonify({"history": [], "error": str(e)})

@app.route('/api/health', methods=['GET'])
def get_health():
    """Return crash forensics summary for the System Health dashboard tab."""
    events = []
    if CRASH_FORENSICS_FILE.exists():
        try:
            for line in CRASH_FORENSICS_FILE.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            pass

    # Recent events (last 50)
    recent = list(reversed(events[-50:]))

    # Summary counts by type
    type_counts = {}
    for e in events:
        t = e.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    # Stability score: last 24 hours of crashes
    now = time.time()
    recent_crashes = [
        e for e in events
        if e.get("type") not in ("stress_test",)
        and _ts_to_epoch(e.get("ts", "")) > now - 86400
    ]

    return jsonify({
        "total_events": len(events),
        "recent_24h": len(recent_crashes),
        "type_counts": type_counts,
        "recent": recent,
    })

def _ts_to_epoch(ts_str: str) -> float:
    try:
        import datetime
        dt = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        return dt.timestamp()
    except Exception:
        return 0.0

@app.route('/api/learned_corrections', methods=['GET'])
def get_learned_corrections():
    """Return auto-learned word corrections."""
    if not LEARNED_CORRECTIONS.exists():
        return jsonify({"corrections": {}})
    try:
        return jsonify({"corrections": json.loads(LEARNED_CORRECTIONS.read_text())})
    except Exception:
        return jsonify({"corrections": {}})

@app.route('/api/learned_corrections', methods=['DELETE'])
def delete_learned_correction():
    """Remove a specific learned correction."""
    data = request.json or {}
    word = data.get("word", "").lower().strip()
    if not word or not LEARNED_CORRECTIONS.exists():
        return jsonify({"status": "not_found"})
    try:
        corrections = json.loads(LEARNED_CORRECTIONS.read_text())
        corrections.pop(word, None)
        LEARNED_CORRECTIONS.write_text(json.dumps(corrections, indent=2))
        return jsonify({"status": "deleted"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})

if __name__ == '__main__':
    # Silence werkzeug logging
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    app.run(host='127.0.0.1', port=5050, debug=False)
