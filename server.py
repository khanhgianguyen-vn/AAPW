"""
Google App Password Auto-Generator - Flask Server
Provides a web interface and API for automated Google App Password generation.
"""

import json
import os
import queue
import threading
import requests as http
from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response, send_from_directory
from automation import process_accounts

load_dotenv()

app = Flask(__name__, static_folder="public", static_url_path="")

_captcha_event = threading.Event()
_captcha_event.set()


@app.route("/")
def index():
    """Serve the frontend."""
    return send_from_directory("public", "index.html")


@app.route("/api/captcha-resolved", methods=["POST"])
def captcha_resolved():
    """Signal that the user has solved the CAPTCHA."""
    _captcha_event.set()
    return jsonify({"ok": True})


@app.route("/api/send-to-nixus", methods=["POST"])
def send_to_nixus():
    """POST successful app passwords to the nixuslabs update endpoint."""
    nixus_url = os.environ.get("NIXUS_API_URL", "").rstrip("/")
    api_key = os.environ.get("AAPW_API_KEY", "")
    if not nixus_url or not api_key:
        return jsonify({"error": "NIXUS_API_URL or AAPW_API_KEY not set in .env"}), 400

    data = request.get_json()
    results = data.get("results", []) if data else []

    headers = {"x-aapw-api-key": api_key, "Content-Type": "application/json"}
    sent, failed, errors = 0, 0, []

    for r in results:
        if r.get("status") != "success" or not r.get("app_password"):
            continue
        payload = {
            "email": r["email"],
            "newAppPassword": r["app_password"],
            "status": "success",
        }
        try:
            resp = http.post(
                f"{nixus_url}/api/aapw/update",
                headers=headers,
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            sent += 1
        except Exception as e:
            failed += 1
            errors.append(f"{r['email']}: {e}")

    return jsonify({"sent": sent, "failed": failed, "errors": errors})


@app.route("/api/generate", methods=["POST"])
def generate():
    """
    Start app password generation process.
    Uses Server-Sent Events (SSE) to stream progress and results.
    
    Request body: { "accounts": "email|pass|recovery\\nemail2|pass2|recovery2" }
    """
    data = request.get_json()
    if not data or "accounts" not in data:
        return jsonify({"error": "Missing 'accounts' field"}), 400

    accounts_text = data["accounts"].strip()
    if not accounts_text:
        return jsonify({"error": "No accounts provided"}), 400

    # Use a queue to communicate between threads
    msg_queue = queue.Queue()
    _captcha_event.set()  # Reset in case a previous run left it cleared

    def on_log(msg):
        msg_queue.put({"type": "log", "message": msg})

    def on_result(result):
        msg_queue.put({"type": "result", "data": result})

    def on_captcha():
        msg_queue.put({"type": "captcha", "message": "CAPTCHA detected! Solve it and click Continue."})
        _captcha_event.clear()
        _captcha_event.wait()
        msg_queue.put({"type": "captcha_resolved", "message": "CAPTCHA resolved, continuing..."})

    def run_automation():
        try:
            results = process_accounts(accounts_text, on_log=on_log, on_result=on_result, on_captcha=on_captcha)
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


if __name__ == "__main__":
    print("Server starting on http://localhost:3000")
    app.run(host="0.0.0.0", port=3000, debug=False, threaded=True)
