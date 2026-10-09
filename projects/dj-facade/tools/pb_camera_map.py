"""Camera-based LED position mapper for a Pixelblaze.

Lights one LED at a time, photographs it, and records the centre of its glow.
Works for diffused / back-lit LEDs (where binary-coded structured light fails
because neighbouring glows overlap).

Subcommands:
  preview   save one camera frame (check framing/focus) to maps/preview.jpg
  scan      run the mapping pass; writes maps/<timestamp>/{scan.json,map.json,overlay.jpg}
  center    pick the point that should be the map's middle (0.5, 0.5); click it on the scan photo
  apply     write a map.json to the Pixelblaze (saves to flash; archive first!). Uses the run's
            center.json if present, so patterns centre on that point instead of the LED bounding box

Examples:
  python tools/pb_camera_map.py preview
  python tools/pb_camera_map.py scan
  python tools/pb_camera_map.py scan --start 0 --count 30          # quick test
  python tools/pb_camera_map.py scan --redo maps/<run>/scan.json   # retry only missed LEDs
  python tools/pb_camera_map.py center maps/<run>                  # click the centre, Enter to save
  python tools/pb_camera_map.py center maps/<run> --at 944,572      # or give it in camera pixels
  python tools/pb_camera_map.py apply maps/<run>/map.json

The Pixelblaze's pattern and brightness are restored afterwards; nothing is saved
to flash except by `apply`.
"""

import argparse
import datetime
import json
import os
import pathlib
import sys
import threading
import time

import cv2
import numpy as np
from pixelblaze import Pixelblaze

DEFAULT_IP = os.environ.get("PIXELBLAZE_IP")   # no default: set it or pass --ip
DEFAULT_CAMERA = 2  # DirectShow index of the Elgato Facecam 4K on this PC
ROOT = pathlib.Path(__file__).resolve().parent.parent
MAPS = ROOT / "maps"

PROBE_PATTERN = """
export var probeIndex = -1
export var probeAll = 0
export var probeLevel = 1

export function render(index) {
  v = 0
  if (probeAll || index == probeIndex) v = probeLevel
  rgb(v, v, v)
}
"""


class Camera:
    """Grabs frames continuously on a thread so reads are never stale."""

    def __init__(self, index: int, width: int, height: int, exposure: float | None):
        self.cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            raise RuntimeError(f"camera {index} did not open")
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if exposure is not None:
            # Manual exposure keeps the dark reference valid for the whole scan.
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            self.cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
        self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        self._lock = threading.Lock()
        self._frame = None
        self._count = 0
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        deadline = time.time() + 5
        while self._count < 10:  # let auto-white-balance etc. settle
            if time.time() > deadline:
                raise RuntimeError("camera produced no frames")
            time.sleep(0.05)

    def _loop(self):
        while self._running:
            ok, frame = self.cap.read()
            if ok:
                with self._lock:
                    self._frame = frame
                    self._count += 1

    def fresh(self, n: int = 1) -> np.ndarray:
        """Average of the next n frames captured after this call."""
        frames = []
        with self._lock:
            seen = self._count
        while len(frames) < n:
            with self._lock:
                if self._count > seen:
                    seen = self._count
                    frames.append(self._frame.astype(np.float32))
            time.sleep(0.003)
        return np.mean(frames, axis=0)

    def close(self):
        self._running = False
        self._thread.join(timeout=2)
        self.cap.release()


def gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img.astype(np.float32), cv2.COLOR_BGR2GRAY)


def locate(diff: np.ndarray, roi: np.ndarray, noise: float, min_snr: float):
    """Brightness-weighted centroid of the blob around the brightest spot in diff."""
    d = cv2.GaussianBlur(diff, (0, 0), 3) * roi
    peak = float(d.max())
    snr = peak / max(noise, 1e-3)
    if snr < min_snr:
        return None, snr
    py, px = np.unravel_index(int(d.argmax()), d.shape)
    mask = (d >= peak * 0.5).astype(np.uint8)
    _, labels = cv2.connectedComponents(mask)
    blob = labels == labels[py, px]
    w = d * blob
    ys, xs = np.nonzero(blob)
    weights = w[ys, xs]
    return (float((xs * weights).sum() / weights.sum()), float((ys * weights).sum() / weights.sum())), snr


def fill_gaps(points: list, max_gap: int) -> list:
    """Interpolate missed LEDs along the strip from their nearest detected neighbours."""
    filled = list(points)
    found = [i for i, p in enumerate(points) if p is not None]
    if not found:
        return filled
    for i, p in enumerate(points):
        if p is not None:
            continue
        lo = max((j for j in found if j < i), default=None)
        hi = min((j for j in found if j > i), default=None)
        if lo is not None and hi is not None and hi - lo - 1 <= max_gap:
            t = (i - lo) / (hi - lo)
            filled[i] = [points[lo][0] + t * (points[hi][0] - points[lo][0]),
                         points[lo][1] + t * (points[hi][1] - points[lo][1])]
        else:
            nearest = lo if hi is None or (lo is not None and i - lo <= hi - i) else hi
            filled[i] = list(points[nearest])
    return filled


def draw_overlay(base: np.ndarray, points: list, detected: list, path: pathlib.Path):
    img = np.clip(base * 0.6, 0, 255).astype(np.uint8)
    n = len(points)
    for i, p in enumerate(points):
        if p is None:
            continue
        hue = int(170 * i / max(n - 1, 1))
        color = cv2.cvtColor(np.uint8([[[hue, 255, 255]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
        c = (int(round(p[0])), int(round(p[1])))
        if detected[i]:
            cv2.circle(img, c, 4, color, -1)
        else:
            cv2.circle(img, c, 7, (0, 0, 255), 2)  # interpolated
        if i % 50 == 0:
            cv2.putText(img, str(i), (c[0] + 6, c[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.imwrite(str(path), img)


def cmd_preview(args):
    cam = Camera(args.camera, args.width, args.height, args.exposure)
    try:
        MAPS.mkdir(exist_ok=True)
        out = MAPS / "preview.jpg"
        cv2.imwrite(str(out), cam.fresh(3).astype(np.uint8))
        print(f"saved {out}  exposure={cam.cap.get(cv2.CAP_PROP_EXPOSURE)}")
    finally:
        cam.close()


def cmd_scan(args):
    prior = json.loads(pathlib.Path(args.redo).read_text()) if args.redo else None

    with Pixelblaze(args.ip) as pb:
        pixel_count = pb.getPixelCount()
        restore_pattern = pb.getActivePattern()
        restore_brightness = pb.getBrightnessSlider()
        restore_sequencer = pb.getSequencerState()

        if prior:
            indices = [i for i, d in enumerate(prior["detected"]) if not d]
        else:
            end = pixel_count if args.count is None else min(pixel_count, args.start + args.count)
            indices = list(range(args.start, end))
        print(f"{pb.getDeviceName()}: {pixel_count} LEDs, scanning {len(indices)}")

        cam = Camera(args.camera, args.width, args.height, args.exposure)
        try:
            pb.pauseSequencer()
            pb.sendPatternToRenderer(pb.compilePattern(PROBE_PATTERN))
            pb.setBrightnessSlider(args.brightness)

            def show(**vars_):
                pb.setActiveVariables(vars_)
                time.sleep(args.settle)

            show(probeIndex=-1, probeAll=0, probeLevel=1)
            dark_bgr = cam.fresh(8)
            dark = gray(dark_bgr)
            # Frame-to-frame noise (measured the same way locate() sees it) sets the detection floor.
            noise = float(np.percentile(
                cv2.GaussianBlur(np.clip(gray(cam.fresh(args.frames)) - dark, 0, None), (0, 0), 3), 99.99)) + 1.0

            show(probeAll=1)
            lit_bgr = cam.fresh(4)
            lit = np.clip(gray(lit_bgr) - dark, 0, None)
            roi = cv2.dilate((cv2.GaussianBlur(lit, (0, 0), 5) > max(noise, lit.max() * 0.03)).astype(np.uint8),
                             np.ones((41, 41), np.uint8)).astype(np.float32)
            if args.no_roi:
                roi = np.ones_like(roi)
            show(probeAll=0)
            print(f"noise floor {noise:.1f}, region of interest {roi.mean() * 100:.0f}% of frame")

            points = prior["points_raw"] if prior else [None] * pixel_count
            snrs = prior["snr"] if prior else [0.0] * pixel_count
            t0 = time.time()
            for k, i in enumerate(indices):
                show(probeIndex=i)
                p, snr = locate(np.clip(gray(cam.fresh(args.frames)) - dark, 0, None), roi, noise, args.min_snr)
                if p is None and args.settle < 1:  # slow frame / dropped message: one retry
                    time.sleep(args.settle)
                    p, snr = locate(np.clip(gray(cam.fresh(args.frames)) - dark, 0, None), roi, noise, args.min_snr)
                points[i], snrs[i] = (list(p) if p else None), round(snr, 2)
                if args.verbose or k % 25 == 0 or k == len(indices) - 1:
                    rate = (k + 1) / (time.time() - t0)
                    print(f"  {k + 1}/{len(indices)}  led {i}: {'%.0f,%.0f' % tuple(p) if p else 'MISSED'}"
                          f"  snr {snr:.1f}  ~{(len(indices) - k - 1) / rate:.0f}s left", flush=True)
        finally:
            cam.close()
            pb.setActivePattern(restore_pattern)
            pb.setBrightnessSlider(restore_brightness)
            if restore_sequencer:
                pb.playSequencer()

    detected = [p is not None for p in points]
    filled = fill_gaps(points, args.max_gap)
    run = MAPS / datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run.mkdir(parents=True, exist_ok=True)
    h, w = dark.shape
    (run / "scan.json").write_text(json.dumps({
        "ip": args.ip, "camera": args.camera, "frame": [w, h], "pixel_count": pixel_count,
        "points_raw": points, "detected": detected, "snr": snrs}, indent=1))
    # Pixelblaze rescales each axis to 0..1 itself; keep image orientation (y down).
    (run / "map.json").write_text(json.dumps([[round(x, 2), round(y, 2)] for x, y in filled]))
    draw_overlay(lit_bgr, filled, detected, run / "overlay.jpg")
    cv2.imwrite(str(run / "lit.jpg"), lit_bgr.astype(np.uint8))

    missed = [i for i in indices if not detected[i]]
    print(f"\n{len(indices) - len(missed)}/{len(indices)} scanned LEDs detected; "
          f"{detected.count(False)}/{pixel_count} positions interpolated -> {run}")
    if missed:
        print(f"missed: {missed[:60]}{' ...' if len(missed) > 60 else ''}")


def estimate_center(lit_gray: np.ndarray):
    """Centre of the largest clear circle in the photo (the flower of life), in camera pixels."""
    h, w = lit_gray.shape
    scale = 960 / w
    small = cv2.GaussianBlur(cv2.resize(lit_gray, (960, int(h * scale))), (0, 0), 2)
    found = cv2.HoughCircles(small, cv2.HOUGH_GRADIENT, dp=1.5, minDist=200, param1=80, param2=60,
                             minRadius=int(small.shape[0] * 0.33), maxRadius=int(small.shape[0] * 0.52))
    if found is None:
        return None
    x, y, r = found[0][0]
    return [float(x / scale), float(y / scale)], float(r / scale)


def cmd_center(args):
    run = pathlib.Path(args.run)
    photo = cv2.imread(str(run / "lit.jpg"))
    if args.at:
        center = [float(v) for v in args.at.split(",")]
    else:
        guess = estimate_center(cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY))
        center = guess[0] if guess else [photo.shape[1] / 2, photo.shape[0] / 2]
        radius = guess[1] if guess else 0
        pts = np.array(json.loads((run / "map.json").read_text()))
        box_mid = (pts.min(0) + pts.max(0)) / 2
        view_scale = min(1.0, 1600 / photo.shape[1])
        state = {"c": center}

        def draw():
            img = photo.copy()
            cx, cy = int(state["c"][0]), int(state["c"][1])
            if radius:
                cv2.circle(img, (int(guess[0][0]), int(guess[0][1])), int(radius), (0, 200, 255), 1)
            cv2.drawMarker(img, (int(box_mid[0]), int(box_mid[1])), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 30, 2)
            cv2.drawMarker(img, (cx, cy), (0, 255, 255), cv2.MARKER_CROSS, 60, 2)
            cv2.putText(img, "click the centre | Enter/Space = save | Esc = cancel | red X = current (box) centre",
                        (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            cv2.imshow("pick centre", cv2.resize(img, None, fx=view_scale, fy=view_scale))

        def on_mouse(event, x, y, flags, _):
            if event == cv2.EVENT_LBUTTONDOWN:
                state["c"] = [x / view_scale, y / view_scale]
                draw()

        cv2.namedWindow("pick centre")
        cv2.setMouseCallback("pick centre", on_mouse)
        draw()
        while True:
            key = cv2.waitKey(50) & 0xFF
            if key in (13, 32):
                break
            if key == 27 or cv2.getWindowProperty("pick centre", cv2.WND_PROP_VISIBLE) < 1:
                cv2.destroyAllWindows()
                sys.exit("cancelled; nothing saved")
        cv2.destroyAllWindows()
        center = state["c"]
    (run / "center.json").write_text(json.dumps({"center": [round(center[0], 1), round(center[1], 1)]}))
    print(f"centre {center[0]:.0f},{center[1]:.0f} saved to {run / 'center.json'}")


def centered_unit_coords(coords: list, center: list) -> list:
    """Scale each axis to 0..1 symmetrically about `center`, so it lands at exactly (0.5, 0.5).
    The LEDs then span a slightly smaller range on the axis where they reach less far."""
    pts = np.array(coords, float)
    c = np.array(center, float)
    half = np.maximum(c - pts.min(0), pts.max(0) - c)
    return np.clip((pts - (c - half)) / (2 * half), 0, 1).tolist()


def cmd_apply(args):
    path = pathlib.Path(args.map)
    coords = json.loads(path.read_text())
    center_file = path.parent / "center.json"
    with Pixelblaze(args.ip) as pb:
        n = pb.getPixelCount()
        if len(coords) != n:
            sys.exit(f"map has {len(coords)} points but the Pixelblaze has {n} LEDs")
        if center_file.exists() and not args.no_center:
            center = json.loads(center_file.read_text())["center"]
            unit = centered_unit_coords(coords, center)
            # Write the map data directly: the library's setMapCoordinates would re-normalize to the
            # LED bounding box and undo the centring. Format: v3 map = uint16 per axis, 0..65535 = 0..1.
            data = (2).to_bytes(4, "little") + (2).to_bytes(4, "little") + (n * 2 * 2).to_bytes(4, "little")
            data += b"".join(int(round(v * 65535)).to_bytes(2, "little") for p in unit for v in p)
            pb.putFile("/pixelmap.txt", json.dumps([[round(x, 5), round(y, 5)] for x, y in unit]))
            pb.putFile("/pixelmap.dat", data)
            pb.setMapData(data)
            print(f"wrote {n}-point map centred on {center} to {pb.getDeviceName()} (saved to flash)")
        else:
            pb.setMapCoordinates(coords)
            print(f"wrote {n}-point map to {pb.getDeviceName()} (saved to flash)")


def main():
    ap = argparse.ArgumentParser(description="Camera-based LED mapper for Pixelblaze")
    ap.add_argument("--ip", default=DEFAULT_IP, help="Pixelblaze address (or set PIXELBLAZE_IP); only device commands need it")
    ap.add_argument("--camera", type=int, default=DEFAULT_CAMERA)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--exposure", type=float, default=None,
                    help="manual exposure (DirectShow log2 seconds, e.g. -6); default leaves auto")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preview")

    s = sub.add_parser("scan")
    s.add_argument("--start", type=int, default=0)
    s.add_argument("--count", type=int, default=None)
    s.add_argument("--redo", help="scan.json from a previous run; rescans only its missed LEDs")
    s.add_argument("--brightness", type=float, default=0.6, help="temporary brightness while scanning")
    s.add_argument("--settle", type=float, default=0.12, help="seconds to wait after each LED change")
    s.add_argument("--frames", type=int, default=2, help="frames averaged per LED")
    s.add_argument("--min-snr", type=float, default=5.0, help="peak/noise ratio needed to count as detected")
    s.add_argument("--no-roi", action="store_true", help="search the whole frame, not just where LEDs glowed")
    s.add_argument("--verbose", action="store_true", help="print every LED")
    s.add_argument("--max-gap", type=int, default=12, help="longest run of misses to interpolate along the strip")

    c = sub.add_parser("center")
    c.add_argument("run", help="maps/<run> folder (uses its lit.jpg and map.json)")
    c.add_argument("--at", help="centre in camera pixels, e.g. 944,572 (skips the click window)")

    a = sub.add_parser("apply")
    a.add_argument("map")
    a.add_argument("--no-center", action="store_true", help="ignore center.json; centre on the LED bounding box")

    args = ap.parse_args()
    if args.cmd in ("scan", "apply") and not args.ip:
        ap.error(f"{args.cmd} talks to the Pixelblaze: set PIXELBLAZE_IP or pass --ip")
    {"preview": cmd_preview, "scan": cmd_scan, "center": cmd_center, "apply": cmd_apply}[args.cmd](args)


if __name__ == "__main__":
    main()
