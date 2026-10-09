"""Sound for the staff: play a .wav through the WM8960 HAT, one at a time.

Fitted 2026-10-04: a Waveshare WM8960 Audio HAT with speakers on its speaker
terminals (`dtoverlay=wm8960-soundcard`; the ALSA card is `wm8960soundcard`).

The rule that shapes all of this: **sound must never cost the sensors anything.**
The Pi Zero W has one ARMv6 core and the IMU thread decides casts in real time, so:

  * Playback is a separate `aplay` process, not audio decoded in Python. The kernel
    schedules it like anything else; this process only starts it, stops it, and asks
    whether it has finished. Nothing here blocks, reads audio, or holds a lock while
    anything slow happens.
  * One sound at a time. A new play stops the old one first - two aplays on one
    hardware device would fail anyway, and a staff that queues sounds behind each
    other is a staff that answers the gesture you made three seconds ago.
  * Volume is the codec's own `Speaker` control, set with `amixer`. Hardware gain
    costs no CPU, and unlike an ALSA softvol device it needs no asound.conf.
  * Nothing is read from the mixer on a timer. The level is read once at start and
    remembered after every set, so the 10 Hz page stream never spawns a process.

No card (the dev PC, the sim, a HAT that has not enumerated) is a normal state, not
an error: `available` is False, `why` says so, and every call is a no-op.

A card that is *late* is the normal state at boot. The staff service starts ~60 s
after power-on and the wm8960 driver probes ~15 s after that (seen 2026-10-04), so
a missing card is looked for again - every 5 s for the first 2 minutes, then every
30 s - and `on_ready` is called the moment it turns up, which is where the startup
sound gets played. Only a missing *card* is retried; a missing aplay will not fix
itself.

Sounds live on the Pi, never in git (the repo is on GitHub and the files are
licensed for our use, not for redistribution). Default folder `~/sounds`, scanned
recursively, so a new subfolder of sounds is just a copy.
"""
from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import threading
import time
from typing import Dict, List, Optional

DEFAULT_DIR = "~/sounds"
DEFAULT_DEVICE = "plughw:wm8960soundcard"
DEFAULT_MIXER = "Speaker"
# (fast interval, how long to stay fast, slow interval) for re-checking a missing card
RETRY = (5.0, 120.0, 30.0)


def card_of(device: str) -> Optional[str]:
    """The ALSA card a device string names, or None if it does not name one.

    `plughw:wm8960soundcard` -> `wm8960soundcard`; `hw:1,0` -> `1`;
    `plughw:CARD=wm8960soundcard,DEV=0` -> `wm8960soundcard`; `default` -> None.
    """
    if ":" not in device:
        return None
    rest = device.split(":", 1)[1]
    m = re.search(r"CARD=([^,]+)", rest)
    if m:
        return m.group(1)
    return rest.split(",", 1)[0] or None


def parse_map(spec: str) -> Dict[str, str]:
    """`spin=magic/08-teleport.wav,slam=magic/09-power-up.wav` -> a dict.

    Keys are gesture names; `spin_cw` / `spin_ccw` (any `<gesture>_cw|_ccw`) beat a
    plain `spin` for that direction, and `*` is any cast nothing more specific
    matched. Empty spec, empty map: the default is silence.
    """
    out = {}
    for part in (spec or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


class SoundPlayer:
    def __init__(self, sounds_dir: str = DEFAULT_DIR, device: str = DEFAULT_DEVICE,
                 mixer: str = DEFAULT_MIXER, cast_map: Optional[Dict[str, str]] = None,
                 player: Optional[List[str]] = None, on_ready=None, retry=RETRY,
                 pan_reverse: bool = False):
        self.dir = os.path.realpath(os.path.expanduser(sounds_dir))
        self.device = device
        self.card = card_of(device)
        self.mixer = mixer or None
        self.cast_map = dict(cast_map or {})
        # Swap which of <gesture>_cw / _ccw a direction plays: the panned cast sounds
        # sweep L->R for clockwise, and this flips that if it sounds backwards.
        self.pan_reverse = pan_reverse
        # The command, minus the file. Overridable so tests can play "sleep" instead.
        self.player = player or ["aplay", "-q", "-D", device]
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._playing: Optional[str] = None
        self._started = 0.0
        self.last_error: Optional[str] = None
        self.volume: Optional[int] = None
        self.plays = 0
        self.born = time.monotonic()
        self.on_ready = on_ready          # called as on_ready(player, seconds_since_start)
        self.ready_after: Optional[float] = None
        self._closed = threading.Event()
        self._q: "queue.Queue" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self.available, self.why = self._probe()
        self._files: List[str] = []
        self.rescan()
        if self.available and self.mixer:
            self.volume = self._read_volume()
        if not self.available and retry and not player_missing(self.player):
            fast, fast_for, slow = retry
            self.why += " - looking again every %.0f s" % fast
            threading.Thread(target=self._wait_for_card, args=(fast, fast_for, slow),
                             daemon=True, name="sound-card-wait").start()

    def _wait_for_card(self, fast: float, fast_for: float, slow: float) -> None:
        """Re-check for the card until it appears. Costs one stat() per try."""
        while True:
            age = time.monotonic() - self.born
            if self._closed.wait(fast if age < fast_for else slow):
                return
            ok, why = self._probe()
            if not ok:
                if time.monotonic() - self.born >= fast_for:
                    self.why = why + " - looking again every %.0f s" % slow
                continue
            if self.mixer:
                self.volume = self._read_volume()
            self.ready_after = time.monotonic() - self.born
            self.why = ""
            self.available = True           # last: nothing may play before the volume is known
            if self.on_ready is not None:
                try:
                    self.on_ready(self, self.ready_after)
                except Exception:           # a callback bug must not leave the card unused
                    pass
            return

    # ---- discovery -------------------------------------------------------------
    def _probe(self):
        if player_missing(self.player):
            return False, "%s is not installed" % self.player[0]
        # /proc/asound/<id> exists for every enumerated card, by name and by number.
        # Cheaper and quieter than running aplay -l, and it is what "no HAT" looks like.
        if self.card is not None and not os.path.exists("/proc/asound/" + self.card):
            return False, "no ALSA card '%s' (is the HAT on and its overlay enabled?)" % self.card
        return True, ""

    def rescan(self) -> List[str]:
        """Every .wav under the sounds folder, as sorted '/'-separated relative paths."""
        found = []
        if os.path.isdir(self.dir):
            for root, dirs, files in os.walk(self.dir, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for f in files:
                    if f.lower().endswith(".wav") and not f.startswith("."):
                        rel = os.path.relpath(os.path.join(root, f), self.dir)
                        found.append(rel.replace(os.sep, "/"))
        self._files = sorted(found)
        return list(self._files)

    @property
    def files(self) -> List[str]:
        return list(self._files)

    def resolve(self, rel: str) -> Optional[str]:
        """An absolute path for a listed sound, or None. Only names that the scan
        found are playable, so a URL can never reach outside the sounds folder."""
        rel = (rel or "").replace("\\", "/")
        if rel not in self._files:
            self.rescan()                       # a file dropped in since the page loaded
            if rel not in self._files:
                return None
        path = os.path.realpath(os.path.join(self.dir, rel))
        if not path.startswith(self.dir + os.sep):
            return None
        return path

    # ---- playback --------------------------------------------------------------
    def play(self, rel: str) -> bool:
        """Stop whatever is playing and start `rel`. Returns at once."""
        if not self.available:
            return False
        path = self.resolve(rel)
        if path is None:
            self.last_error = "no such sound: %s" % rel
            return False
        with self._lock:
            self._stop_locked()
            try:
                self._proc = subprocess.Popen(
                    self.player + [path], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, close_fds=True)
            except OSError as exc:
                self.last_error = "%s: %s" % (exc.__class__.__name__, exc)
                self._proc = None
                return False
            self._playing = rel
            self._started = time.monotonic()
            self.last_error = None
            self.plays += 1
        return True

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        p, self._proc, self._playing = self._proc, None, None
        if p is None or p.poll() is not None:
            self._close(p)
            return
        p.terminate()
        try:
            p.wait(0.5)                 # aplay exits on SIGTERM in milliseconds
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(0.5)
        self._close(p)

    @staticmethod
    def _close(p) -> None:
        if p is not None and p.stderr is not None:
            try:
                p.stderr.close()
            except OSError:
                pass

    def _reap(self) -> None:
        """Notice a sound that finished by itself. Cheap: one waitpid(WNOHANG)."""
        p = self._proc
        if p is None:
            return
        rc = p.poll()
        if rc is None:
            return
        with self._lock:
            if self._proc is not p:
                return
            if rc != 0:
                # aplay is quiet on success with -q; whatever it said is the reason.
                try:
                    msg = (p.stderr.read() or b"").decode("utf-8", "replace").strip()
                except (OSError, ValueError):
                    msg = ""
                self.last_error = (msg.splitlines() or ["aplay exited %d" % rc])[-1][:200]
            self._close(p)
            self._proc, self._playing = None, None

    @property
    def playing(self) -> Optional[str]:
        self._reap()
        return self._playing

    # ---- following a cast (off unless --sound-map is given) ----------------------
    def sound_for(self, payload: dict) -> Optional[str]:
        g = payload.get("gesture") or ""
        d = payload.get("spin_dir")
        cw = (d > 0) != self.pan_reverse if d else None
        keys = ([g + ("_cw" if cw else "_ccw")] if d else []) + [g, "*"]
        for k in keys:
            if k in self.cast_map:
                return self.cast_map[k]
        return None

    def on_cast(self, payload: dict) -> Optional[str]:
        """Called from the IMU thread the moment a cast is published. A cast also
        ends any charge sound - by replacing it, since a new play stops the old."""
        if not (self.cast_map and self.available):
            return None
        rel = self.sound_for(payload)
        if rel is not None:
            self.later(self.play, rel)
        return rel

    # ---- from the IMU thread: never wait, never reorder ---------------------------
    def later(self, fn, *args) -> None:
        """Run fn(*args) on the sound worker. Starting or stopping aplay can take a
        few ms, which the IMU thread must not spend; and one worker, unlike a thread
        per call, keeps "charge, then cast" from arriving the other way round."""
        if self._worker is None:
            self._worker = threading.Thread(target=self._work, daemon=True, name="sound")
            self._worker.start()
        self._q.put((fn, args))

    def _work(self) -> None:
        while True:
            fn, args = self._q.get()
            try:
                fn(*args)
            except Exception as exc:              # keep the worker alive whatever happens
                self.last_error = "%s: %s" % (exc.__class__.__name__, exc)

    def stop_if(self, rel: str) -> bool:
        """Stop the current sound only if it is `rel` - so ending a charge never cuts
        off a cast sound, or something someone pressed on the page."""
        with self._lock:
            if self._playing != rel:
                return False
            self._stop_locked()
            return True

    # ---- volume ----------------------------------------------------------------
    def _amixer(self, *args) -> Optional[str]:
        cmd = ["amixer", "-M"] + (["-c", self.card] if self.card else []) + list(args)
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                               timeout=3, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.last_error = "amixer: %s" % exc
            return None
        if r.returncode != 0:
            self.last_error = "amixer: " + r.stderr.decode("utf-8", "replace").strip()[:200]
            return None
        return r.stdout.decode("utf-8", "replace")

    def _read_volume(self) -> Optional[int]:
        out = self._amixer("sget", self.mixer)
        m = re.search(r"\[(\d+)%\]", out or "")
        return int(m.group(1)) if m else None

    def set_volume(self, pct: float) -> Optional[int]:
        """0-100 on the mixer's perceptual (-M) scale, so the slider's middle sounds
        like the middle rather than near-silent, as a raw dB-register scale would."""
        if not (self.available and self.mixer):
            return None
        pct = int(round(max(0.0, min(100.0, float(pct)))))
        if self._amixer("sset", self.mixer, "%d%%" % pct) is not None:
            self.volume = self._read_volume()
        return self.volume

    # ---- for the page ----------------------------------------------------------
    def state(self) -> dict:
        """Small on purpose: this rides the 10 Hz stream. The file list does not."""
        playing = self.playing
        return {
            "present": self.available, "why": self.why, "playing": playing,
            "elapsed": round(time.monotonic() - self._started, 1) if playing else None,
            "volume": self.volume, "mixer": self.mixer if self.available else None,
            "device": self.device, "error": self.last_error, "count": len(self._files),
            "follows_casts": bool(self.cast_map),
            "late": None if self.ready_after is None else round(self.ready_after, 1),
        }

    def listing(self) -> dict:
        return {"dir": self.dir, "files": self.rescan(), "cast_map": self.cast_map,
                **self.state()}

    def close(self) -> None:
        self._closed.set()
        self.stop()


def player_missing(cmd: List[str]) -> bool:
    return shutil.which(cmd[0]) is None
