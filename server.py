"""
Google App Password Auto-Generator - Flask Server
Provides a web interface and API for automated Google App Password generation.
"""

import json
import queue
import threading
from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response, send_from_directory
from automation import process_accounts

load_dotenv()

app = Flask(__name__, static_folder="public", static_url_path="")


@app.route("/")
def index():
    """Serve the frontend."""
    return send_from_directory("public", "index.html")


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

    def on_log(msg):
        msg_queue.put({"type": "log", "message": msg})

    def on_result(result):
        msg_queue.put({"type": "result", "data": result})

    def run_automation():
        try:
            results = process_accounts(accounts_text, on_log=on_log, on_result=on_result)
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
    import os
    nixus_url = os.environ.get("NIXUS_API_URL", "")
    api_key = os.environ.get("AAPW_API_KEY", "")
    if nixus_url and api_key:
        try:
            from poller import start_scheduler
            start_scheduler()
            print(f"Poller started — connected to {nixus_url}")
        except Exception as e:
            print(f"Poller failed to start: {e}")
    else:
        print("NIXUS_API_URL / AAPW_API_KEY not set — poller disabled")

    print("Server starting on http://localhost:3000")
    app.run(host="0.0.0.0", port=3000, debug=False, threaded=True)
