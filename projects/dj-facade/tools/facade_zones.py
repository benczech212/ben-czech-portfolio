"""Zones for the facade: which LEDs are the flower of life, the mushrooms, the leaves.

  auto     build zones.json from the wiring order (each art element is one stretch of strip)
  edit     click LEDs on the scan photo to reassign their strip segment; keys pick the zone
  preview  write zones.jpg: every LED coloured by zone
  show     light each zone on the facade in its colour for a few seconds (then restores)

zones.json lives next to the map (maps/<run>/zones.json) and is baked into the Facade Bus
pattern by `facade_bridge.py --deploy`.
"""

import argparse
import json
import os
import pathlib
import sys
import time

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_RUN = ROOT / "maps" / "2026-10-06_213307"
DEFAULT_IP = os.environ.get("PIXELBLAZE_IP")   # no default: set it or pass --ip

# Zone ids are what the pattern sees; names are what the bus / controller use.
ZONES = ["ring", "grid", "mushroom_left", "mushroom_right", "leaves_left", "leaves_right", "crown",
         "mushroom_both"]   # 7: the cloned run - each index lights a piece on BOTH mushrooms
COLORS_BGR = [(0, 215, 255), (255, 200, 0), (60, 60, 255), (200, 60, 255), (60, 220, 60), (160, 255, 120),
              (255, 255, 255), (130, 130, 255)]
# Display names where they differ from the camp-bus vibe name (the bus keeps saying "party").
VIBE_LABELS = {"party": "hue rotate"}

GROUPS = {
    "all": ZONES,
    "circle": ["ring", "grid"],
    "mushrooms": ["mushroom_left", "mushroom_right", "mushroom_both"],
    "leaves": ["leaves_left", "leaves_right", "crown"],
    "left": ["mushroom_left", "leaves_left", "mushroom_both"],
    "right": ["mushroom_right", "leaves_right", "mushroom_both"],
}

# Wiring order of the facade (from the 2026-10-06 camera scan).
AUTO_RANGES = [
    (0, 285, "leaves_left"),
    (286, 571, "leaves_right"),      # 544-571: the vertical strip between circle and right mushroom
    (572, 657, "mushroom_right"),
    (658, 743, "mushroom_left"),
    (744, 855, "grid"),
    (856, 1023, "ring"),
    (1024, 1037, "mushroom_both"),  # cloned data line: every index lights a piece on both mushrooms
    (1038, 1115, "leaves_right"),
    (1116, 1136, "crown"),
    (1137, 1214, "leaves_left"),
]


def zone_mask(spec) -> int:
    """'mushrooms' / 'ring' / ['ring', 'leaves_left'] / None -> bitmask over ZONES (None = all)."""
    if spec is None or spec == "" or spec == []:
        return (1 << len(ZONES)) - 1
    names = []
    for item in ([spec] if isinstance(spec, str) else spec):
        names += GROUPS.get(item, [item])
    mask = 0
    for n in names:
        if n in ZONES:
            mask |= 1 << ZONES.index(n)
    return mask or (1 << len(ZONES)) - 1


def load_map(run: pathlib.Path):
    return np.array(json.loads((run / "map.json").read_text()), float)


def load_zones(run: pathlib.Path) -> list:
    return json.loads((run / "zones.json").read_text())["zones"]


def save_zones(run: pathlib.Path, zones: list):
    (run / "zones.json").write_text(json.dumps({"names": ZONES, "zones": zones}))


def zone_runs(zones: list) -> list:
    """Run-length form [start0, zone0, start1, zone1, ...] - what the pattern unpacks."""
    out = []
    for i, z in enumerate(zones):
        if i == 0 or z != zones[i - 1]:
            out += [i, z]
    return out


def mirror_strip(run: pathlib.Path):
    """A strip order for mirrored animations: LEDs left of (or on) the centre keep their place in
    wiring order; each LED right of it takes the place of its nearest mirror-image LED on the left.
    Returns (rank per LED, number of distinct ranks). The Facade Bus pattern now computes the same
    thing on the Pixelblaze at start-up (per zone); this stays as the reference / for analysis."""
    pts = load_map(run)
    cx = json.loads((run / "center.json").read_text())["center"][0] if (run / "center.json").exists()         else (pts[:, 0].min() + pts[:, 0].max()) / 2
    left = [i for i in range(len(pts)) if pts[i, 0] <= cx + 2]
    rank_of = {i: r for r, i in enumerate(left)}
    lp = pts[left]
    ranks = []
    for i, p in enumerate(pts):
        if i in rank_of:
            ranks.append(rank_of[i])
        else:
            m = np.array([2 * cx - p[0], p[1]])
            ranks.append(int(np.linalg.norm(lp - m, axis=1).argmin()))
    return ranks, len(left)


def strip_segments(pts: np.ndarray, jump_px: float = 45) -> list:
    """Contiguous stretches of strip: split wherever consecutive LEDs are far apart."""
    step = np.r_[0, np.linalg.norm(np.diff(pts, axis=0), axis=1)]
    cuts = [0] + [i for i in range(1, len(pts)) if step[i] > jump_px] + [len(pts)]
    return [(cuts[k], cuts[k + 1]) for k in range(len(cuts) - 1)]


def cmd_auto(args):
    run = pathlib.Path(args.run)
    pts = load_map(run)
    center_x = json.loads((run / "center.json").read_text())["center"][0] if (run / "center.json").exists() \
        else (pts[:, 0].min() + pts[:, 0].max()) / 2
    zones = [ZONES.index("leaves_left")] * len(pts)
    for a, b, name in AUTO_RANGES:
        for i in range(a, min(b, len(pts) - 1) + 1):
            zones[i] = ZONES.index(name)
    save_zones(run, zones)
    print(f"wrote {run / 'zones.json'}")
    draw_preview(run, zones)


def draw_preview(run: pathlib.Path, zones: list, path: pathlib.Path = None, highlight=None):
    pts = load_map(run)
    img = (cv2.imread(str(run / "lit.jpg")) * 0.4).astype(np.uint8)
    for i, p in enumerate(pts):
        r = 7 if highlight is not None and i in highlight else 4
        cv2.circle(img, (int(p[0]), int(p[1])), r, COLORS_BGR[zones[i]], -1)
    for k, name in enumerate(ZONES):
        cv2.rectangle(img, (20, 20 + 34 * k), (44, 44 + 34 * k), COLORS_BGR[k], -1)
        counts = zones.count(k)
        cv2.putText(img, f"{k}  {name} ({counts})", (54, 40 + 34 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                    (255, 255, 255), 2)
    out = path or run / "zones.jpg"
    cv2.imwrite(str(out), img)
    return img


def cmd_preview(args):
    run = pathlib.Path(args.run)
    draw_preview(run, load_zones(run))
    print(f"wrote {run / 'zones.jpg'}")


def cmd_edit(args):
    run = pathlib.Path(args.run)
    pts = load_map(run)
    zones = load_zones(run)
    segments = strip_segments(pts)
    seg_of = np.zeros(len(pts), int)
    for k, (a, b) in enumerate(segments):
        seg_of[a:b] = k
    state = {"zone": 0, "sel": None}
    base = cv2.imread(str(run / "lit.jpg"))
    scale = min(1.0, 1600 / base.shape[1])

    def redraw():
        sel = None
        if state["sel"] is not None:
            a, b = segments[state["sel"]]
            sel = set(range(a, b))
        img = draw_preview(run, zones, path=run / "zones.jpg", highlight=sel)
        cv2.putText(img, f"click = assign segment to zone {state['zone']} ({ZONES[state['zone']]}) | "
                         f"keys 0-6 pick zone | s = save | Esc = quit",
                    (20, img.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.imshow("facade zones", cv2.resize(img, None, fx=scale, fy=scale))

    def on_mouse(event, x, y, flags, _):
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        d = np.linalg.norm(pts - [x / scale, y / scale], axis=1)
        i = int(d.argmin())
        if d[i] > 25:
            return
        k = seg_of[i]
        a, b = segments[k]
        for j in range(a, b):
            zones[j] = state["zone"]
        state["sel"] = k
        print(f"LEDs {a}-{b - 1} -> {ZONES[state['zone']]}")
        redraw()

    cv2.namedWindow("facade zones")
    cv2.setMouseCallback("facade zones", on_mouse)
    redraw()
    while True:
        key = cv2.waitKey(50) & 0xFF
        if ord("0") <= key < ord("0") + len(ZONES):
            state["zone"] = key - ord("0")
            redraw()
        elif key == ord("s"):
            save_zones(run, zones)
            print(f"saved {run / 'zones.json'}")
        elif key == 27 or cv2.getWindowProperty("facade zones", cv2.WND_PROP_VISIBLE) < 1:
            break
    cv2.destroyAllWindows()


def cmd_show(args):
    from pixelblaze import Pixelblaze
    run = pathlib.Path(args.run)
    zones = load_zones(run)
    hues = [0.13, 0.55, 0.0, 0.8, 0.33, 0.42, 0.0, 0.66]
    sats = [1, 1, 1, 1, 1, 1, 0, 1]
    runs = zone_runs(zones)
    code = (f"var R = {json.dumps(runs)}\nvar H = {json.dumps(hues)}\nvar S = {json.dumps(sats)}\n"
            "var z = array(pixelCount)\n"
            "for (k = 0; k < R.length; k += 2) { e = k + 2 < R.length ? R[k + 2] : pixelCount\n"
            "  for (i = R[k]; i < e; i++) z[i] = R[k + 1] }\n"
            "export var only = -1\n"
            "export function render(index) { q = z[index]\n"
            "  hsv(H[q], S[q], only < 0 || only == q ? 0.8 : 0) }\n")
    with Pixelblaze(args.ip) as pb:
        active, bright = pb.getActivePattern(), pb.getBrightnessSlider()
        try:
            pb.sendPatternToRenderer(pb.compilePattern(code))
            pb.setBrightnessSlider(0.5)
            print("all zones")
            time.sleep(args.seconds)
            for k, name in enumerate(ZONES):
                pb.setActiveVariables({"only": k})
                print(f"zone {k}: {name}")
                time.sleep(args.seconds / 2)
        finally:
            pb.setActivePattern(active)
            pb.setBrightnessSlider(bright)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["auto", "edit", "preview", "show"])
    ap.add_argument("run", nargs="?", default=str(DEFAULT_RUN))
    ap.add_argument("--ip", default=DEFAULT_IP, help="Pixelblaze address (or set PIXELBLAZE_IP); only device commands need it")
    ap.add_argument("--seconds", type=float, default=4)
    args = ap.parse_args()
    if args.cmd == "show" and not args.ip:
        ap.error("show talks to the Pixelblaze: set PIXELBLAZE_IP or pass --ip")
    {"auto": cmd_auto, "edit": cmd_edit, "preview": cmd_preview, "show": cmd_show}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
