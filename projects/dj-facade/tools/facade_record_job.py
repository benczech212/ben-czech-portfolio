"""Showreel recording as a bridge job: a 10 s countdown, then tools/record_showreel.py, with
progress for the dashboard. The countdown runs in the bridge, so it carries on if the page that
started it is closed (start it, walk away)."""

import os
import pathlib
import re
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
# The version tag ends up in a file name and an ffmpeg drawtext, so keep it to a safe alphabet.
VERSION_RE = re.compile(r"[A-Za-z0-9._-]{0,16}")


def valid_version(version) -> bool:
    return isinstance(version, str) and VERSION_RE.fullmatch(version) is not None


class RecordJob:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"phase": "idle"}
        self.proc = None
        self.cancelled = False
        self.running = False            # True for the whole run: countdown + every take

    def status(self):
        with self.lock:
            s = dict(self.state)
        if s.get("phase") == "countdown":
            s["remaining"] = max(0, round(s["starts_at"] - time.time(), 1))
        if s.get("phase") == "recording" and s.get("t0"):
            s["elapsed"] = round(time.time() - s["t0"], 1)
        return s

    def busy(self):
        return self.running

    def start(self, countdown=10, vibe_seconds=10, effect_seconds=10, spotify="next", takes=1, pause_after=True,
              version=""):
        if not valid_version(version):
            raise ValueError("version must be up to 16 characters of A-Z a-z 0-9 . _ -")
        if self.busy():
            return False
        self.cancelled = False
        self.running = True
        takes = max(1, min(10, int(takes)))
        with self.lock:
            self.state = {"phase": "countdown", "starts_at": time.time() + countdown, "takes": takes, "take": 0,
                          "files": []}
        args = [sys.executable, "-u", str(ROOT / "tools" / "record_showreel.py"),
                "--vibe-seconds", str(vibe_seconds), "--effect-seconds", str(effect_seconds), "--spotify", spotify,
                f"--version={version}"]
        threading.Thread(target=self._run, args=(countdown, args, takes, pause_after), daemon=True).start()
        return True

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            # the recorder's ffmpeg is a child process: kill the whole tree, or the capture keeps going
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
            threading.Thread(target=self._clean_temp, daemon=True).start()
        with self.lock:
            self.state = {"phase": "cancelled"}
        return True

    @staticmethod
    def _clean_temp():
        """Remove a cancelled recording's temp files (retrying while Windows still holds them)."""
        for _ in range(15):
            left = list((ROOT / "recordings").glob(".*_raw.mp4")) + list((ROOT / "recordings").glob(".*_overlay.txt"))
            for f in left:
                try:
                    f.unlink()
                except OSError:
                    pass
            if not left:
                return
            time.sleep(1)

    def _set(self, **kv):
        with self.lock:
            self.state.update(kv)

    def _run(self, countdown, args, takes=1, pause_after=True):
        try:
            end = time.time() + countdown
            while time.time() < end:
                if self.cancelled:
                    return
                time.sleep(0.1)
            for take in range(1, takes + 1):     # back to back; each take starts with a fresh song
                if self.cancelled:
                    return
                self._set(take=take)
                if not self._take(args) or self.cancelled:
                    return
                with self.lock:
                    self.state["files"].append({"file": self.state.get("file"), "mb": self.state.get("mb"),
                                                "master": self.state.get("master"), "master_mb": self.state.get("master_mb")})
                time.sleep(2)
            if pause_after:
                try:
                    import spotify_control
                    spotify_control.send("pause")
                    self._set(spotify="paused")
                except Exception as e:
                    self._set(spotify=f"couldn't pause: {e}")
            self._set(phase="all done")
        finally:
            self.running = False

    def _take(self, args):
        """One showreel; True when it produced a file."""
        self._set(phase="recording", t0=time.time(), segment="starting", song=None, log=[], total=None)
        try:
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}   # names contain "·"
            self.proc = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace", env=env)
            for line in self.proc.stdout:
                line = line.rstrip()
                if not line or "too full" in line or "Last message repeated" in line:
                    continue
                with self.lock:
                    self.state.setdefault("log", []).append(line)
                    self.state["log"] = self.state["log"][-40:]
                m = re.match(r"recording (\d+) s", line)
                if m:
                    self._set(total=int(m.group(1)), t0=time.time())
                m = re.match(r"\s+[\d.]+ s\s+(Vibe|Effect|Effects)(.*)", line)
                if m:
                    self._set(segment=(m.group(1) + m.group(2)).strip())
                m = re.search(r"(?:now playing|spotify: started): (.+)", line)
                if m:
                    self._set(song=m.group(1))
                if line.startswith("adding titles"):
                    self._set(phase="encoding")
                m = re.match(r"master: (.+) \(([\d.]+) MB\)", line)
                if m:
                    self._set(master=m.group(1), master_mb=float(m.group(2)))
                m = re.match(r"done: (.+) \(([\d.]+) MB\)", line)
                if m:
                    self._set(phase="done", file=m.group(1), mb=float(m.group(2)))
            self.proc.wait()
            if not self.cancelled and self.state.get("phase") != "done":
                self._set(phase="error", error=f"recorder exited with code {self.proc.returncode}")
                return False
            return not self.cancelled
        except Exception as e:
            self._set(phase="error", error=str(e))
            return False


JOB = RecordJob()


def handle_get(path):
    if path == "/api/record":
        return JOB.status()
    return None


def handle_post(path, body):
    if path == "/api/record/start":
        version = body.get("version", "")
        if not valid_version(version):
            return {"ok": False, "error": "version must be up to 16 characters of A-Z a-z 0-9 . _ -"}
        ok = JOB.start(countdown=float(body.get("countdown", 10)),
                       vibe_seconds=float(body.get("vibe_seconds", 10)),
                       effect_seconds=float(body.get("effect_seconds", 10)),
                       spotify=body.get("spotify", "next"),
                       takes=int(body.get("takes", 1)),
                       version=version)
        return {"ok": ok, "error": None if ok else "a recording is already running"}
    if path == "/api/record/cancel":
        return {"ok": JOB.cancel()}
    return None
