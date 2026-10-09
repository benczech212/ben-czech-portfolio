#!/usr/bin/env python3
"""DEV-ONLY tiny upload endpoint so the simulator's "Record clip" can save straight to disk.

    PUT /clips/<name>.webm   -> writes CLIPS_DIR/<name>.webm (name sanitised)
    GET /clips/               -> JSON list of clips

Development tool for the docker-compose "virtual camp" only: it has no authentication, so it
must never be exposed on a shared network. It binds 127.0.0.1 by default. In docker-compose it
runs in the `clips` container with CLIPS_BIND=0.0.0.0 (reachable only on the compose network;
its port is not published) and nginx proxies /clips/ to it, so the browser talks to it
same-origin and no CORS headers are sent.
"""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLIPS_DIR = os.environ.get("CLIPS_DIR", "/clips")
BIND = os.environ.get("CLIPS_BIND", "127.0.0.1")
PORT = int(os.environ.get("CLIPS_PORT", "8090"))
MAX_BYTES = 500 * 1024 * 1024          # matches nginx client_max_body_size
SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        files = sorted(f for f in os.listdir(CLIPS_DIR) if not f.startswith("."))
        self._json(200, [{"name": f, "bytes": os.path.getsize(os.path.join(CLIPS_DIR, f))} for f in files])

    def do_PUT(self):
        name = SAFE.sub("-", os.path.basename(self.path.split("?")[0])) or "clip.webm"
        if name.startswith(".") or not name.endswith(".webm"):
            return self._json(400, {"error": "name must be <name>.webm"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BYTES:
            return self._json(413, {"error": "bad or oversized body"})
        path = os.path.join(CLIPS_DIR, name)
        with open(path, "wb") as f:
            left = n
            while left > 0:
                chunk = self.rfile.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk); left -= len(chunk)
        self.log_message("saved %s (%d bytes)", path, n)
        self._json(200, {"saved": name, "bytes": n})


if __name__ == "__main__":
    os.makedirs(CLIPS_DIR, exist_ok=True)
    print(f"clip server (dev only) on {BIND}:{PORT} -> {CLIPS_DIR}", flush=True)
    ThreadingHTTPServer((BIND, PORT), H).serve_forever()
