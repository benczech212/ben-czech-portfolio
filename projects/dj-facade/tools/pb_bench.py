"""Frame-rate bench for the Facade Bus pattern on the live Pixelblaze.

Runs every vibe (steady), every vibe change (the 2 s crossfade), every effect, zone mixes and auto
mode, and records the MINIMUM fps of each - the dips are what people notice. The Pixelblaze reports
fps once a second (a 1 s average), so a "min" is the worst whole second.

  python tools/pb_bench.py                 # everything (~4 min), writes bench/<timestamp>.json
  python tools/pb_bench.py vibes effects   # just some groups: vibes transitions effects zones auto

Leaves the facade on ethereal with auto mode off.
"""

import datetime
import json
import os
import pathlib
import sys
import threading
import time

import websocket
from pixelblaze import Pixelblaze

IP = os.environ.get("PIXELBLAZE_IP")   # required; there is no default address
ROOT = pathlib.Path(__file__).resolve().parent.parent
VIBES = ["cozy", "party", "chaos", "wave", "jumping", "ethereal", "snake", "twinkle", "heartbeat",
         "aurora", "meteor", "bloom", "kaleido",
         "lotus", "spectrum", "fireflies", "cascade", "mycelium", "butterfly",
         "mosaic", "wings", "vortex"]
EFFECTS = {   # effect: vars that start it and hold it on while measuring
    "flash": {"flashT": 0, "flashDur": 99, "flashI": 1, "flashZ": 255},
    "pulse": {"pulseT": 0.5, "pulseDur": 99, "pulseI": 1, "pulseZ": 255},
    "color": {"tintT": 0, "tintDur": 99, "tintI": 1, "tintZ": 255},
    "charge": {"chargeAmt": 0.8, "chargeZ": 255},
    "sweep": {"sweepT": 0, "sweepSpeed": 0.02, "sweepI": 1, "sweepZ": 255},
    "shockwave": {"waveT": 0.3, "waveI": 1, "waveZ": 255},
    "rings": {"ringsT": 0, "ringsDur": 99, "ringsI": 1, "ringsZ": 255},
    "spiral": {"spiralT": 0, "spiralDur": 99, "spiralI": 1, "spiralZ": 255},
    "wipe": {"wipeT": 0.5, "wipeDur": 99, "wipeI": 1, "wipeZ": 255},
    "rain": {"rainT": 0, "rainDur": 99, "rainI": 1, "rainZ": 255},
    "comet": {"cometT": 0, "cometSpeed": 0.01, "cometI": 1, "cometZ": 255},
    "halo": {"waveT": 0.3, "waveDir": -1, "waveI": 1, "waveZ": 255},
    "iris": {"waveT": 0.3, "waveDir": -1, "waveS": 0, "waveV": 0, "waveI": 1, "waveZ": 255},
    "petalfall": {"waveT": 0.3, "waveDir": 1, "waveS": 0, "waveV": 0, "waveI": 1, "waveZ": 255},
    "sunburst": {"spiralT": 0, "spiralDur": 99, "spiralDir": 0, "spiralArms": 12, "spiralI": 1, "spiralZ": 255},
    "sporeburst": {"rainT": 0, "rainDur": 99, "rainI": 1, "rainZ": 140},
}
EFFECT_OFF = {"flashT": 99, "pulseT": 99, "tintT": 99, "chargeAmt": 0, "sweepT": 99, "waveT": 99, "ringsT": 99,
              "spiralT": 99, "wipeT": 99, "rainT": 99, "cometT": 99,
              "flashDur": 0.6, "pulseDur": 2, "tintDur": 2.5, "ringsDur": 2.5, "spiralDur": 3, "wipeDur": 3,
              "rainDur": 4, "sweepSpeed": 1, "cometSpeed": 1,
              "waveDir": 1, "waveS": 0, "waveV": 1, "spiralArms": 3, "rainZ": 255}
NO_ZONES = {f"zv{k}": -1 for k in range(7)}


class Stats:
    """Collects the Pixelblaze's once-a-second stats on its own websocket."""

    def __init__(self):
        self.samples = []
        self.ws = websocket.create_connection(f"ws://{IP}:81", timeout=2)
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.running:
            try:
                m = self.ws.recv()
            except Exception:
                continue
            if isinstance(m, str) and '"fps"' in m:
                d = json.loads(m)
                self.samples.append((time.time(), d["fps"], d.get("vmerr", 0)))

    def window(self, start, end):
        return [(f, e) for t, f, e in self.samples if start <= t <= end]

    def close(self):
        self.running = False
        self.ws.close()


def measure(stats, seconds, skip=1.2):
    """fps samples for the next `seconds`, ignoring the first `skip` s (a 1 s average straddling the change)."""
    t0 = time.time()
    time.sleep(seconds)
    w = stats.window(t0 + skip, time.time())
    if not w:
        return {"min": None, "avg": None, "vmerr": None}
    fps = [f for f, _ in w]
    return {"min": round(min(fps), 1), "avg": round(sum(fps) / len(fps), 1), "vmerr": max(e for _, e in w)}


def run(groups):
    if not IP:
        sys.exit("Set PIXELBLAZE_IP to the Pixelblaze's address first.")
    results = {}
    stats = Stats()
    with Pixelblaze(IP) as pb:
        pid = next(k for k, v in pb.getPatternList(forceRefresh=True).items() if v == "Facade Bus")
        pb.pauseSequencer()                 # the playlist would swap patterns mid-bench (not saved)
        if pb.getActivePattern() != pid:
            pb.setActivePattern(pid)
            time.sleep(2)
        base = {"autoMode": 0, "speed": 1, "split": 1, **NO_ZONES, **EFFECT_OFF}

        def setv(**v):
            pb.setActiveVariables(v)

        setv(**base, vibe=5)
        time.sleep(3)
        try:
            if "vibes" in groups:
                for i, name in enumerate(VIBES):
                    setv(vibe=i)
                    time.sleep(2.3)                        # let the crossfade finish
                    results[f"vibe:{name}"] = r = measure(stats, 3.5, skip=0.5)
                    print(f"vibe {name:10s} min {r['min']:>5} avg {r['avg']:>5}", flush=True)
            if "transitions" in groups:
                for i, name in enumerate(VIBES):
                    prev = (i + 5) % len(VIBES)            # from a different vibe each time
                    setv(vibe=prev)
                    time.sleep(3)
                    setv(vibe=i)                           # the 2 s crossfade starts now
                    results[f"transition:{VIBES[prev]}->{name}"] = r = measure(stats, 2.6, skip=0.3)
                    print(f"change {VIBES[prev]:>9} -> {name:10s} min {r['min']:>5} avg {r['avg']:>5}", flush=True)
            if "effects" in groups:
                for host in ("ethereal", "party"):
                    setv(vibe=VIBES.index(host))
                    time.sleep(2.5)
                    for fx, on in EFFECTS.items():
                        setv(**on)
                        results[f"effect:{fx}@{host}"] = r = measure(stats, 3.2)
                        setv(**EFFECT_OFF)
                        time.sleep(0.6)
                        print(f"effect {fx:9s} on {host:8s} min {r['min']:>5} avg {r['avg']:>5}", flush=True)
            if "zones" in groups:
                mixes = {
                    "circle kaleido / mushrooms cozy / leaves aurora": dict(vibe=9, zv0=12, zv1=12, zv2=0, zv3=0),
                    "circle bloom / mushrooms meteor / leaves ethereal": dict(vibe=5, zv0=11, zv1=11, zv2=10, zv3=10),
                    "every zone different": dict(vibe=1, zv0=12, zv1=11, zv2=0, zv3=10, zv4=9, zv5=5, zv6=7),
                }
                for name, v in mixes.items():
                    setv(**{**NO_ZONES, **v})
                    time.sleep(2.3)
                    results[f"zones:{name}"] = r = measure(stats, 3.5, skip=0.5)
                    print(f"zones {name:48s} min {r['min']:>5} avg {r['avg']:>5}", flush=True)
                setv(**NO_ZONES)
            if "auto" in groups:
                setv(vibe=5, autoSeconds=6, autoLeft=6, autoMode=1)
                results["auto:6 s changes for 36 s"] = r = measure(stats, 36, skip=1)
                print(f"auto mode (6 s)  min {r['min']:>5} avg {r['avg']:>5}", flush=True)
        finally:
            setv(**base, vibe=5, autoSeconds=30)
            stats.close()
    return results


def main():
    groups = sys.argv[1:] or ["vibes", "transitions", "effects", "zones", "auto"]
    results = run(groups)
    out = ROOT / "bench" / (datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    worst = sorted((v["min"] or 0, k) for k, v in results.items())[:8]
    print("\nworst dips:", ", ".join(f"{k} {m}" for m, k in worst))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
