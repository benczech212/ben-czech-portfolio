"""Music cues: listen to the music and fire facade effects on its transition points.

Runs outside the Pixelblaze, on the PC: it analyses the same audio the facade hears (Voicemeeter
B2 = app audio, no mics) at full quality and drives the facade through the bridge's controller API
(the test bench's API), like a lighting operator would.

  python tools/music_cues.py              # run until Ctrl-C
  python tools/music_cues.py --dry-run    # print the cues, send nothing
  python tools/music_cues.py --verbose    # also print levels/tempo every few seconds

Cues
  breakdown  the bass drops away for a while        -> calm vibe + slow pulse
  build      energy/hits rise during a breakdown    -> charge on the beat, growing
  drop       the bass slams back after a breakdown  -> white flash + shockwave + rings + a big vibe
  phrase     every 8 bars while grooving            -> sweep or wipe in a fresh colour
  hit        a kick far above the rest              -> flash on the mushrooms (rate-limited)
  song       Spotify changes track                  -> wipe + new vibe
"""

import argparse
import collections
import queue
import random
import sys
import time

import numpy as np
import requests
import sounddevice as sd

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
from now_playing import now_playing  # noqa: E402

API = "http://127.0.0.1:8121"
SOURCE = "Voicemeeter Out B2"
SR = 48000
BLOCK = 1024                                    # ~21 ms
FPS = SR / BLOCK
CALM = ["ethereal", "aurora", "cozy", "twinkle", "bloom", "lotus", "fireflies", "mycelium", "butterfly"]
BIG = ["party", "jumping", "kaleido", "chaos", "bloom", "snake", "spectrum", "cascade", "mosaic", "wings", "vortex"]
COLORS = ["#ff4fd8", "#00ffcc", "#ffaa00", "#3fa9ff", "#ff4000", "#8a2be2", "#40ff00", "#ffee00"]


def find_input():
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and SOURCE in d["name"] and \
                sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI":
            return i, int(d["default_samplerate"])
    sys.exit(f"no '{SOURCE}' input found (is Voicemeeter running?)")


class Cues:
    def __init__(self, dry_run=False, verbose=False):
        self.dry, self.verbose = dry_run, verbose
        freqs = np.fft.rfftfreq(2048, 1 / SR)
        self.bands = {"bass": (freqs >= 35) & (freqs < 150), "mid": (freqs >= 150) & (freqs < 2000),
                      "high": (freqs >= 2000) & (freqs < 12000)}
        self.window = np.hanning(2048)
        self.buf = np.zeros(2048)
        self.prev_spec = None
        n = int(FPS * 16)
        self.hist = {k: collections.deque(maxlen=n) for k in ("bass", "loud", "high", "flux")}
        self.flux_env = collections.deque(maxlen=int(FPS * 6))
        self.t = 0.0
        # tempo / beat
        self.period = 0.5
        self.next_beat = None
        self.beats = 0
        self.last_tempo_at = 0.0
        # sections
        self.state = "groove"        # groove | breakdown
        self.low_since = None
        self.breakdown_at = None
        self.breakdown_bass = 1.0
        self.build_level = 0.0
        self.last_cue = collections.defaultdict(lambda: -99.0)
        self.last_hit = -99.0
        self.song = None
        self.song_checked = 0.0
        self.color = random.choice(COLORS)

    # ---- output -------------------------------------------------------------------------
    def send(self, kind, why, effects=(), vibe=None):
        self.last_cue[kind] = self.t
        acts = []
        if vibe:
            acts.append(f"vibe {vibe}")
        acts += [f"{e['effect']}{'@' + e['zone'] if e.get('zone', 'all') != 'all' else ''}" for e in effects]
        print(f"CUE {kind:9s} {time.strftime('%H:%M:%S')} {why} -> {', '.join(acts) or '-'}", flush=True)
        if self.dry:
            return
        try:
            if vibe:
                requests.post(API + "/api/vibe", json={"vibe": vibe}, timeout=2)
            for e in effects:
                requests.post(API + "/api/effect", json={"intensity": 1, "zone": "all", "from": f"cues:{kind}", **e},
                              timeout=2)
        except requests.RequestException as ex:
            print(f"   (bridge not reachable: {ex.__class__.__name__})", flush=True)

    # ---- analysis -----------------------------------------------------------------------
    def feed(self, block):
        self.t += len(block) / SR
        self.buf = np.concatenate([self.buf[len(block):], block])
        spec = np.abs(np.fft.rfft(self.buf * self.window))
        lv = {k: float(np.sqrt((spec[m] ** 2).mean())) for k, m in self.bands.items()}
        loud = float(np.sqrt((block ** 2).mean()))
        flux = 0.0
        if self.prev_spec is not None:      # onset strength: positive change, weighted to the low end
            d = np.clip(np.log1p(spec) - np.log1p(self.prev_spec), 0, None)
            flux = float(d[self.bands["bass"]].sum() * 2 + d[self.bands["mid"]].sum())
        self.prev_spec = spec
        for k, v in (("bass", lv["bass"]), ("loud", loud), ("high", lv["high"]), ("flux", flux)):
            self.hist[k].append(v)
        self.flux_env.append(flux)
        self.tempo()
        self.beat(flux)
        self.sections(lv, loud, flux)
        self.check_song()

    def avg(self, k, seconds):
        h = self.hist[k]
        n = min(len(h), int(seconds * FPS))
        return float(np.mean(list(h)[-n:])) if n else 0.0

    def tempo(self):
        if self.t - self.last_tempo_at < 1.0 or len(self.flux_env) < FPS * 4:
            return
        self.last_tempo_at = self.t
        e = np.array(self.flux_env)
        e = e - e.mean()
        ac = np.correlate(e, e, "full")[len(e) - 1:]
        lo, hi = int(FPS * 60 / 170), int(FPS * 60 / 80) + 1
        lag = lo + int(np.argmax(ac[lo:hi]))
        if 1 <= lag < len(ac) - 1:          # parabolic refinement of the peak
            a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
            lag = lag + 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else lag
        self.period = 0.85 * self.period + 0.15 * (lag / FPS)

    def beat(self, flux):
        if self.next_beat is None:
            self.next_beat = self.t + self.period
        # nudge the beat clock toward strong onsets that land near where a beat is expected
        med = np.median(self.flux_env) if self.flux_env else 0
        if flux > 2.2 * med + 1e-6:
            err = self.t - (self.next_beat - self.period)
            if abs(err) < 0.25 * self.period:
                self.next_beat += 0.2 * err
        if self.t >= self.next_beat:
            self.next_beat += self.period
            self.beats += 1
            self.on_beat()

    def on_beat(self):
        if self.state == "breakdown" and self.build_level > 0 and self.beats % 2 == 0:
            self.build_level = min(1.0, self.build_level + 0.08)
            self.send("build", f"rising ({self.build_level:.2f})",
                      [{"effect": "charge", "color": self.color, "intensity": round(0.3 + 0.6 * self.build_level, 2)}])
        if self.state == "groove" and self.beats % 32 == 0 and self.t - self.last_cue["drop"] > 8:
            self.color = random.choice(COLORS)
            fx = random.choice(["sweep", "wipe", "rings", "halo", "sunburst"])
            self.send("phrase", f"8 bars @ {60 / self.period:.0f} bpm", [{"effect": fx, "color": self.color}])

    def sections(self, lv, loud, flux):
        if self.t < 6:
            return
        bass_s, bass_l = self.avg("bass", 1.0), self.avg("bass", 12)
        loud_s = self.avg("loud", 1.0)
        if loud_s < 1e-4:                    # silence: nothing to cue
            return
        if self.state == "groove":
            if bass_s < 0.35 * bass_l:
                self.low_since = self.low_since or self.t
                if self.t - self.low_since > 1.5 and self.t - self.last_cue["drop"] > 6:
                    self.state, self.breakdown_at, self.build_level = "breakdown", self.t, 0.0
                    self.breakdown_bass = bass_l
                    vibe = random.choice(CALM)
                    self.send("breakdown", f"bass {bass_s / bass_l:.0%} of normal",
                              [{"effect": "pulse", "color": self.color}, {"effect": "iris"}],   # the circle closes for a moment
                              vibe=vibe)
            else:
                self.low_since = None
            # a single kick far above the rest: flash the mushrooms
            med = np.median(self.flux_env) if self.flux_env else 0
            if flux > 6 * med + 1e-6 and lv["bass"] > 1.8 * bass_l and self.t - self.last_hit > 4.0:
                self.last_hit = self.t
                self.send("hit", f"kick x{flux / max(med, 1e-6):.1f}",
                          [{"effect": "sporeburst", "color": self.color}])        # the mushrooms puff spores
        else:  # breakdown
            rising = self.avg("high", 2) > 1.3 * self.avg("high", 8) or self.avg("loud", 2) > 1.25 * self.avg("loud", 8)
            if rising and self.build_level == 0 and self.t - self.breakdown_at > 2:
                self.build_level = 0.05
            if bass_s > 0.8 * self.breakdown_bass and self.t - self.breakdown_at > 2:
                self.state, self.low_since = "groove", None
                self.beats = 0                   # phrases count from the drop
                self.next_beat = self.t + self.period
                self.color = random.choice(COLORS)
                self.send("drop", f"bass back after {self.t - self.breakdown_at:.0f} s",
                          [{"effect": "flash", "color": "#ffffff", "ms": 700},
                           {"effect": "shockwave", "color": self.color},
                           random.choice([{"effect": "rings", "color": "#ffffff", "count": 3},
                                          {"effect": "sunburst", "color": "#ffffff"}])],
                          vibe=random.choice(BIG))
        if self.verbose and self.t - getattr(self, "last_report", -9) >= 5:
            self.last_report = self.t
            print(f"  t={self.t:6.1f} {60 / self.period:5.1f} bpm  {self.state:9s} bass {bass_s / max(bass_l, 1e-9):4.0%}"
                  f"  build {self.build_level:.2f}", flush=True)

    def check_song(self):
        if self.t - self.song_checked < 2:
            return
        self.song_checked = self.t
        try:
            song = now_playing()
        except Exception:
            return
        if song and song != self.song:
            if self.song is not None:
                self.color = random.choice(COLORS)
                self.send("song", f"{song[0]} - {song[1]}", [{"effect": "wipe", "color": self.color}],
                          vibe=random.choice(BIG + CALM))
            self.song = song


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long (0 = until Ctrl-C)")
    args = ap.parse_args()
    dev, dev_sr = find_input()
    if dev_sr != SR:
        sys.exit(f"'{SOURCE}' runs at {dev_sr} Hz; set it to {SR} Hz")
    q = queue.Queue(maxsize=200)
    cues = Cues(args.dry_run, args.verbose)

    def callback(indata, frames, t, status):
        try:
            q.put_nowait(indata.mean(axis=1).copy())
        except queue.Full:
            pass

    print(f"listening to {sd.query_devices(dev)['name']} ({'dry run' if args.dry_run else 'sending cues to ' + API})",
          flush=True)
    start = time.time()
    with sd.InputStream(device=dev, channels=2, samplerate=SR, blocksize=BLOCK, callback=callback):
        while not args.seconds or time.time() - start < args.seconds:
            try:
                cues.feed(q.get(timeout=1))
            except queue.Empty:
                continue


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
