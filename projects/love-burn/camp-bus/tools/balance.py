#!/usr/bin/env python3
"""Paint the Camp balance: Monte Carlo coverage + energy per cast for every prop gesture.

Reads the prop rules straight from booth/mappings.yaml and applies the responder rules from
docs/SCHEMA.md (distance falloff, cone/spiral shapes, `falloff: none`, `paint` weight) to a camp of
N crystals on the default spiral layout, with the prop dropped at random positions and headings.

    python tools/balance.py                # report coverage + energy with the current weights
    python tools/balance.py --apply        # also rewrite `paint` so every gesture has equal expected energy
    python tools/balance.py --trials 5000 --crystals 20 --intensity 0.8

"Energy per cast" = sum over crystals of intensity x falloff x shape factor x paint, exactly what the
game credits. Equal expected energy per cast makes props fair on average while leaving the skill in
aiming and positioning (cone casts have high variance: aim well and you beat the average).
"""
from __future__ import annotations

import argparse
import math
import os
import random
import re
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
MAPPINGS = os.path.join(HERE, "..", "booth", "mappings.yaml")

DEFAULT_RANGE, DEFAULT_CONE, DEFAULT_SPEED = 60.0, 70.0, 6.0
CAMP = (-30.0, 30.0, -6.0, 38.0)          # metres east/north the demo props wander over (same as camp.html)


def spiral_layout(n: int) -> list[tuple[float, float]]:
    """Same golden-angle spiral as the simulator's default crystal positions."""
    out = []
    for i in range(n):
        a, r = i * 2.39996, 7 + 5.2 * math.sqrt(i)
        out.append((round(r * math.cos(a) * 2) / 2, round((14 + r * math.sin(a)) * 2) / 2))
    return out


def wrap180(d: float) -> float:
    return ((d + 540) % 360) - 180


def factor(dx: float, dy: float, m: dict, intensity: float) -> float:
    """Responder rules: falloff x shape, for a crystal at (dx east, dy north) from the prop."""
    d = math.hypot(dx, dy)
    if m.get("cutoff_m") and d > float(m["cutoff_m"]):
        return 0.0
    if intensity >= 1 or m.get("falloff") == "none":
        f = 1.0
    else:
        f = 1 - 0.6 * min(1.0, d / float(m.get("range_m") or DEFAULT_RANGE))
    shape = m.get("shape", "circle")
    if shape == "cone":
        half = float(m.get("cone_deg") or DEFAULT_CONE) / 2
        bearing = math.degrees(math.atan2(dx, dy)) % 360
        a = wrap180(bearing - m["_heading"])
        f *= 0 if abs(a) > half else 0.3 + 0.7 * math.cos(math.pi / 2 * a / half)
    return 0.0 if f <= 0.02 else f


def load_gestures(path: str) -> list[dict]:
    doc = yaml.safe_load(open(path, encoding="utf-8"))
    out = []
    for rule in doc["rules"]:
        w = rule.get("when") or {}
        if w.get("topic") != "prop/+":
            continue
        for o in rule["then"]:
            if o["topic"].startswith("cmd/crystals/") or o["topic"].startswith("cmd/all/"):
                out.append({"prop": w["prop"], "gesture": w["gesture"], "topic": o["topic"], "payload": o["payload"], "rule": rule["name"]})
    return out


def simulate(gestures: list[dict], crystals: list[tuple[float, float]], trials: int, intensity: float, seed: int = 1):
    rng = random.Random(seed)
    stats = {}
    for g in gestures:
        reach = energy_raw = 0.0
        for _ in range(trials):
            px, py = rng.uniform(CAMP[0], CAMP[1]), rng.uniform(CAMP[2], CAMP[3])
            m = dict(g["payload"]); m["_heading"] = rng.uniform(0, 360)
            hit = tot = 0.0
            for cx, cy in crystals:
                f = factor(cx - px, cy - py, m, intensity)
                if f > 0:
                    hit += 1; tot += intensity * f
            reach += hit; energy_raw += tot
        paint = float(g["payload"].get("paint", 1.0))
        stats[(g["prop"], g["gesture"])] = {"reach": reach / trials, "raw": energy_raw / trials, "paint": paint,
                                            "energy": energy_raw / trials * paint, "g": g}
    return stats


def report(stats: dict, n: int, title: str) -> None:
    print(f"\n{title}  ({n} crystals; energy per cast = what the game credits, summed over crystals)")
    print(f"{'prop':6} {'gesture':8} {'reach':>7} {'raw/cast':>9} {'paint':>6} {'energy':>8}")
    by_prop = {}
    for (p, gname), s in stats.items():
        print(f"{p:6} {gname:8} {s['reach']:7.1f} {s['raw']:9.2f} {s['paint']:6.2f} {s['energy']:8.2f}")
        by_prop.setdefault(p, []).append(s["energy"])
    print("per prop (gestures used equally):", "  ".join(f"{p}={sum(v)/len(v):.2f}" for p, v in by_prop.items()))


def apply_weights(path: str, stats: dict, target: float) -> None:
    """Rewrite `paint: X` inside each gesture's output line so energy per cast == target."""
    text = open(path, encoding="utf-8").read()
    for (p, gname), s in stats.items():
        new_w = round(max(0.05, min(8.0, target / s["raw"])) if s["raw"] > 0 else 1.0, 2)
        topic = s["g"]["topic"]
        # find the output line for this rule (by rule name block, then the topic) and replace its paint value
        rule_pat = re.compile(r'(- name: "?' + re.escape(s["g"]["rule"]) + r'"?\n(?:.*\n)*?\s+- \{ topic: "' + re.escape(topic) + r'".*?)paint: [0-9.]+', re.M)
        text, n = rule_pat.subn(lambda mm: mm.group(1) + f"paint: {new_w}", text, count=1)
        if n != 1:
            print(f"  ! could not set paint for {p} {gname} ({topic}); add `paint:` to that output first")
        else:
            s["paint"] = new_w; s["energy"] = s["raw"] * new_w
    open(path, "w", encoding="utf-8", newline="\n").write(text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=3000)
    ap.add_argument("--crystals", type=int, default=20)
    ap.add_argument("--intensity", type=float, default=0.8)
    ap.add_argument("--apply", action="store_true", help="rewrite paint weights in mappings.yaml for equal energy per cast")
    ap.add_argument("--target", type=float, default=None, help="energy per cast to aim for (default 2.5)")
    a = ap.parse_args()
    crystals = spiral_layout(a.crystals)
    gestures = load_gestures(MAPPINGS)
    stats = simulate(gestures, crystals, a.trials, a.intensity)
    report(stats, a.crystals, "CURRENT weights")
    if a.apply:
        target = a.target or 2.5                                        # energy per cast; weak casts get paint > 1, strong ones < 1
        apply_weights(MAPPINGS, stats, target)
        report(stats, a.crystals, f"AFTER --apply (target {target:.2f} energy per cast)")


if __name__ == "__main__":
    main()
