"""Music cues as a bridge job: start/stop tools/music_cues.py from the test bench page and keep
its cue log for the dashboard."""

import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from collections import deque

ROOT = pathlib.Path(__file__).resolve().parent.parent


class CuesJob:
    def __init__(self):
        self.proc = None
        self.log = deque(maxlen=30)
        self.counts = {}
        self.started = None
        self.lock = threading.Lock()

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.running():
            return False
        with self.lock:
            self.log.clear()
            self.counts = {}
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        self.proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "tools" / "music_cues.py")], cwd=ROOT,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace", env=env)
        self.started = time.time()
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        return True

    def stop(self):
        if self.running():
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
        return True

    def _read(self, proc):
        for line in proc.stdout:
            line = line.rstrip()
            if not line or "warn" in line.lower():
                continue
            m = re.match(r"CUE (\w+)\s+(\S+) (.*)", line)
            with self.lock:
                if m:
                    self.counts[m.group(1)] = self.counts.get(m.group(1), 0) + 1
                    self.log.appendleft({"kind": m.group(1), "at": m.group(2), "text": m.group(3)})
                elif not line.startswith("  t="):
                    self.log.appendleft({"kind": "info", "at": time.strftime("%H:%M:%S"), "text": line})

    def status(self):
        with self.lock:
            return {"running": self.running(), "since": self.started, "counts": dict(self.counts),
                    "log": list(self.log)[:20]}


JOB = CuesJob()


def handle_get(path):
    if path == "/api/cues":
        return JOB.status()
    return None


def handle_post(path, body):
    if path == "/api/cues/start":
        return {"ok": JOB.start()}
    if path == "/api/cues/stop":
        return {"ok": JOB.stop()}
    return None
