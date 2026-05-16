"""
Google App Password Auto-Generator - Flask Server
Provides a web interface and API for automated Google App Password generation.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

import csv
import io
import json
import os
import queue
import threading
from flask import Flask, request, jsonify, Response, send_from_directory
from automation import process_accounts

AAPW_API_KEY = os.environ.get("AAPW_API_KEY", "")

app = Flask(__name__, static_folder="public", static_url_path="")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RESULTS_FILE = os.path.join(DATA_DIR, "results.json")
RUNS_DIR = os.path.join(DATA_DIR, "runs")


@app.route("/")
def index():
    """Serve the frontend."""
    return send_from_directory("public", "index.html")


@app.route("/api/generate", methods=["POST"])
def generate():
    """
    Start app password generation process.
    Uses Server-Sent Events (SSE) to stream progress and results.
    
    Request body: { "accounts": "email|pass|recovery|secret\\nemail2|..." }
    """
    data = request.get_json()
    if not data or "accounts" not in data:
        return jsonify({"error": "Missing 'accounts' field"}), 400

    accounts_text = data["accounts"].strip()
    if not accounts_text:
        return jsonify({"error": "No accounts provided"}), 400

    # Optional overrides from the request body (null/missing = use env defaults)
    use_proxy = data.get("useProxy")   # True / False / None
    headless  = data.get("headless")   # True / False / None

    # Use a queue to communicate between threads
    msg_queue = queue.Queue()

    def on_log(msg):
        msg_queue.put({"type": "log", "message": msg})

    def on_result(result):
        msg_queue.put({"type": "result", "data": result})

    def run_automation():
        try:
            results = process_accounts(accounts_text, on_log=on_log, on_result=on_result, use_proxy=use_proxy, headless=headless)
            msg_queue.put({"type": "done", "data": results})
        except Exception as e:
            msg_queue.put({"type": "error", "message": str(e)})

    # Start automation in a background thread
    thread = threading.Thread(target=run_automation, daemon=True)
    thread.start()

    def event_stream():
        while True:
            try:
                msg = msg_queue.get(timeout=120)  # 2 minute timeout
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                if msg["type"] in ("done", "error"):
                    break
            except queue.Empty:
                # Send keepalive
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.route("/api/results")
def get_results():
    """Return all saved results from data/results.json."""
    if not os.path.exists(RESULTS_FILE):
        return jsonify([])
    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            results = json.load(f)
        return jsonify(results)
    except (json.JSONDecodeError, IOError):
        return jsonify([])


@app.route("/api/results/export")
def export_results():
    """Export all results as CSV download."""
    results = []
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                results = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["email", "app_password", "status", "timestamp", "error_detail"])
    for r in results:
        writer.writerow([
            r.get("email", ""),
            r.get("app_password", ""),
            r.get("status", ""),
            r.get("timestamp", ""),
            r.get("error_detail", ""),
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=aapw_results.csv"}
    )


@app.route("/api/runs")
def get_runs():
    """Return list of all run summaries (without full results for brevity)."""
    if not os.path.exists(RUNS_DIR):
        return jsonify([])

    runs = []
    for filename in sorted(os.listdir(RUNS_DIR), reverse=True):
        if filename.endswith(".json"):
            filepath = os.path.join(RUNS_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    run_data = json.load(f)
                # Return summary without full results array
                runs.append({
                    "run_id": run_data.get("run_id"),
                    "timestamp": run_data.get("timestamp"),
                    "total": run_data.get("total"),
                    "success": run_data.get("success"),
                    "failed": run_data.get("failed"),
                })
            except (json.JSONDecodeError, IOError):
                continue
    return jsonify(runs)


@app.route("/api/runs/<run_id>")
def get_run(run_id):
    """Return full details of a specific run."""
    run_file = os.path.join(RUNS_DIR, f"{run_id}.json")
    if not os.path.exists(run_file):
        return jsonify({"error": "Run not found"}), 404
    try:
        with open(run_file, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except (json.JSONDecodeError, IOError):
        return jsonify({"error": "Could not read run file"}), 500


@app.route("/api/results/clear", methods=["POST"])
def clear_results():
    """Clear all saved results."""
    try:
        if os.path.exists(RESULTS_FILE):
            os.remove(RESULTS_FILE)
        return jsonify({"success": True})
    except IOError as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trigger", methods=["POST"])
def trigger():
    """
    Trigger an immediate AAPW poll cycle.
    Called by nixus-labs when the manager clicks 'Run AAPW Now'.
    Requires x-aapw-api-key header.
    """
    key = request.headers.get("x-aapw-api-key", "")
    if not AAPW_API_KEY or key != AAPW_API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from poller import run_poll
        thread = threading.Thread(target=run_poll, daemon=True)
        thread.start()
        return jsonify({"status": "triggered", "message": "Poll cycle started in background"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/status")
def status():
    """Basic health/status endpoint."""
    return jsonify({"status": "ok", "service": "AAPW"})


if __name__ == "__main__":
    # Start the background poller if env vars are set
    nixus_url = os.environ.get("NIXUS_API_URL", "")
    api_key = os.environ.get("AAPW_API_KEY", "")
    if nixus_url and api_key:
        try:
            from poller import start_scheduler
            start_scheduler()
            print(f"🤖 AAPW poller started — connected to {nixus_url}")
        except Exception as e:
            print(f"⚠️  Poller failed to start: {e}")
    else:
        print("ℹ️  NIXUS_API_URL / AAPW_API_KEY not set — poller disabled (web UI only mode)")

    print("🚀 Server starting on http://localhost:3000")
    print("📋 Open your browser and paste your accounts to generate app passwords")
    app.run(host="0.0.0.0", port=3000, debug=False, threaded=True)
