#!/usr/bin/env python3
"""Synthetic audio/beat + audio/energy + audio/bpm publisher.

Stands in for the real aubio analysis service (M3) so responders can be built
and demoed now. Honours camp/mode: in "idle" there is no music, so nothing is
published on audio/beat or audio/energy and audio/bpm reports 0. Usage:

    python tools/fake_beat.py --host localhost --bpm 124
"""
import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients", "python"))
from campbus import Node, now_ms  # noqa: E402

ENERGY_HZ = 20


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("CAMPBUS_HOST", "campbus.local"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("CAMPBUS_PORT", "1883")))
    ap.add_argument("--bpm", type=float, default=124.0)
    ap.add_argument("--quiet", action="store_true", help="don't print each beat")
    a = ap.parse_args()

    node = Node("fake_beat", host=a.host, port=a.port, fw="fake-beat-0.2", role="booth")
    state = {"mode": "idle"}          # clean slate: silent until camp/mode says "show" (retained)

    @node.on("camp/mode")
    def on_mode(topic, msg):
        new = msg.get("mode") or "show"
        if new != state["mode"]:
            print(f"camp/mode -> {new}" + ("  (silence: no beats, no energy, bpm 0)" if new == "idle" else ""))
        state["mode"] = new

    node.connect()
    beat_s = 60.0 / a.bpm
    t0 = time.monotonic()
    n = 0
    next_beat = t0
    last_energy = t0
    last_bpm = 0.0
    print(f"fake_beat -> {a.host}:{a.port} at {a.bpm} BPM (Ctrl-C to stop)")
    try:
        while True:
            now = time.monotonic()
            if state["mode"] == "idle":                      # no music playing: silence on the bus
                if now - last_bpm >= 1.0:
                    last_bpm = now
                    node.publish("audio/bpm", {"bpm": 0, "conf": 0.0}, retain=True)
                next_beat = now                                  # resume cleanly when music comes back
                time.sleep(0.05)
                continue
            if now >= next_beat:
                n += 1
                next_beat += beat_s
                node.publish("audio/beat", {"n": n, "bpm": a.bpm, "next_ms": int(beat_s * 1000)})
                if not a.quiet:
                    print("beat", n, "*" if n % 4 == 0 else "")
            if now - last_energy >= 1.0 / ENERGY_HZ:
                last_energy = now
                phase = ((now - t0) % beat_s) / beat_s          # 0 at the beat, 1 just before the next
                bass = max(0.0, 1.0 - phase * 2.5)               # sharp kick decay
                mid = 0.35 + 0.15 * math.sin((now - t0) * 2.1)
                high = 0.1 + 0.3 * (1.0 if 0.45 < phase < 0.55 else 0.0)  # hat on the off-beat
                loud = min(1.0, 0.5 * bass + 0.3 * mid + 0.2 * high + 0.2)
                node.publish("audio/energy", {"bass": round(bass, 3), "mid": round(mid, 3),
                                              "high": round(high, 3), "loud": round(loud, 3)})
            if now - last_bpm >= 1.0:
                last_bpm = now
                node.publish("audio/bpm", {"bpm": a.bpm, "conf": 1.0}, retain=True)
            time.sleep(0.005)
    except KeyboardInterrupt:
        node.close()


if __name__ == "__main__":
    main()
