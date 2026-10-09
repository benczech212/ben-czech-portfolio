"""Bench-test the facade bridge by publishing camp-bus messages to a local broker.

On the real camp bus only the mapping engine publishes to cmd/; this is for the
home broker only.

Examples:
  python tools/bus_test.py flash --color "#ff4000"
  python tools/bus_test.py mode party
  python tools/bus_test.py beat --bpm 124 --seconds 20      # fake beats + energy
  python tools/bus_test.py tour                             # every vibe + effect, in order
"""

import argparse
import json
import math
import random
import time

import paho.mqtt.client as mqtt

VIBES = ["cozy", "party", "chaos", "wave", "jumping", "ethereal",
         "snake", "twinkle", "heartbeat", "aurora", "meteor"]


def connect(host, port):
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"facade_bench_{random.randint(0, 9999)}")
    c.connect(host, port)
    c.loop_start()
    return c


def pub(c, topic, payload, retain=False):
    payload = {**payload, "ts": int(time.time() * 1000)}
    c.publish(topic, json.dumps(payload, separators=(",", ":")), retain=retain)
    print(topic, payload)


def beats(c, bpm, seconds):
    period = 60 / bpm
    n, start, next_beat = 0, time.time(), time.time()
    while time.time() - start < seconds:
        now = time.time()
        if now >= next_beat:
            pub(c, "audio/beat", {"n": n, "bpm": bpm, "next_ms": int(period * 1000)})
            n += 1
            next_beat += period
        ph = ((now - start) / period) % 1
        c.publish("audio/energy", json.dumps({
            "bass": round(max(0.0, 1 - ph * 2.5), 2), "mid": round(0.4 + 0.2 * math.sin(now), 2),
            "high": round(random.random() * 0.4, 2), "loud": 0.6, "ts": 0}))
        time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", help="flash|pulse|color|charge|sweep|shockwave|speed|mode|beat|camp|tour")
    ap.add_argument("value", nargs="?")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--target", default="facade")
    ap.add_argument("--color", default="#ff00ff")
    ap.add_argument("--intensity", type=float, default=1.0)
    ap.add_argument("--bpm", type=float, default=124)
    ap.add_argument("--seconds", type=float, default=15)
    a = ap.parse_args()
    c = connect(a.host, a.port)
    cmd = f"cmd/{a.target}"
    base = {"color": a.color, "intensity": a.intensity}

    if a.what == "mode":
        pub(c, f"{cmd}/mode", {"mode": a.value or "party"})
    elif a.what == "speed":
        pub(c, f"{cmd}/speed", {"value": float(a.value or 1)})
    elif a.what == "camp":
        pub(c, "camp/mode", {"mode": a.value or "show", "since": int(time.time() * 1000)}, retain=True)
    elif a.what == "beat":
        pub(c, "audio/bpm", {"bpm": a.bpm, "conf": 0.9}, retain=True)
        beats(c, a.bpm, a.seconds)
        pub(c, "audio/bpm", {"bpm": 0, "conf": 0}, retain=True)
    elif a.what == "tour":
        for v in VIBES:
            pub(c, f"{cmd}/mode", {"mode": v})
            time.sleep(6)
        for fx, extra in [("flash", {"ms": 1200}), ("pulse", {"seconds": 3}), ("color", {"seconds": 3}),
                          ("sweep", {"speed": 1}), ("shockwave", {}), ("charge", {}), ("charge", {})]:
            pub(c, f"{cmd}/{fx}", {**base, "color": random.choice(["#ff4000", "#00ffcc", "#ff00ff", "#3060ff"]), **extra})
            time.sleep(3)
    else:
        extra = {"flash": {"ms": 800}, "pulse": {"seconds": 2}, "color": {"seconds": 3}, "sweep": {"speed": 1.5}}
        pub(c, f"{cmd}/{a.what}", {**base, **extra.get(a.what, {})})
    time.sleep(0.3)
    c.loop_stop()


if __name__ == "__main__":
    main()
