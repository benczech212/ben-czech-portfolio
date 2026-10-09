"""Render a who's-talking table visualization for a Craig multi-track recording.

    python make_speaker_video.py RECORDING_DIR --part part1 --mixdown RECORDING_DIR/part1-mixdown.m4a
    python make_speaker_video.py RECORDING_DIR --part part1 --title "Camp Meeting #2 · Sept 14" --out part1.mp4
    python make_speaker_video.py RECORDING_DIR --part part1 --preview 1200   # one frame at t=1200 s -> PNG

Reads RECORDING_DIR/<part>/<n>-<speaker>.<audio> stems + their .tsv transcripts and renders a
1280x720 video: an ATTENDING table where each speaker row has a text box to the right showing
their own words (colour-tinted), fading out ~2 s after they go silent. Long utterances scroll
inside the box (newest words pinned to the bottom, faded gradient + arrow hint when text has
scrolled off the top). Bottom strip: chapter seek bar (segments from
RECORDING_DIR/<part>-chapters.tsv, "start_ms<TAB>title" with a header row, filled to the
playhead; the active chapter's label lights up). Muxed with the mixdown audio via ffmpeg.

Speaker names and colours come from the stem filenames (speakers.py).
"""
import argparse
import glob
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from speakers import palette, speaker_name

AUDIO_EXTS = ("aac", "flac", "ogg", "m4a", "wav", "mp3")

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("recording_dir", help="folder holding the part folders (part1/, part2/, ...)")
ap.add_argument("--part", required=True, help="part folder name, e.g. part1")
ap.add_argument("--mixdown", help="mixed-down audio to mux (default: first RECORDING_DIR/*<part>-mixdown.*)")
ap.add_argument("--out", help="output .mp4 (default: RECORDING_DIR/<part>-table.mp4)")
ap.add_argument("--title", default="Camp Meeting", help="title line at the top of the frame")
ap.add_argument("--preview", type=float, metavar="SECONDS", help="render one frame at this time to a PNG and exit")
args = ap.parse_args()

PART = args.part
HERE = os.path.abspath(args.recording_dir)
PART_DIR = os.path.join(HERE, PART)
MIXDOWN = args.mixdown or next(iter(sorted(glob.glob(os.path.join(HERE, f"*{PART}-mixdown.*")))), None)
OUT = args.out or os.path.join(HERE, f"{PART}-table.mp4")
CHAPTERS_TSV = os.path.join(HERE, f"{PART}-chapters.tsv")
if args.preview is None and not (MIXDOWN and os.path.exists(MIXDOWN)):
    ap.error("no mixdown audio found; pass --mixdown")

W, H, FPS, SR = 1280, 720, 12, 16000
BG = (13, 11, 20)
DIM = (90, 84, 110)
PANEL = (26, 22, 38)
LINE = (56, 48, 76)
TITLE = args.title
FADE_S = 2.0          # text fade time-constant after an utterance ends
UTT_GAP_MS = 1500     # segments closer than this merge into one utterance

FONT_DIRS = [r"C:\Windows\Fonts", "/usr/share/fonts/truetype/dejavu", "/Library/Fonts"]


def font(path_opts, size):
    for p in path_opts + ["DejaVuSans.ttf"]:
        for d in FONT_DIRS:
            fp = os.path.join(d, p)
            if os.path.exists(fp):
                return ImageFont.truetype(fp, size)
    return ImageFont.load_default()

F_TITLE = font(["bahnschrift.ttf", "arialbd.ttf"], 26)
F_NAME  = font(["bahnschrift.ttf", "arial.ttf"], 20)
F_SUB   = font(["bahnschrift.ttf", "arial.ttf"], 17)
F_TIME  = font(["consola.ttf", "arial.ttf"], 22)
F_HDR   = font(["bahnschrift.ttf", "arial.ttf"], 18)
F_CHAP  = font(["bahnschrift.ttf", "arial.ttf"], 15)

def envelope(aac_path):
    """Per-video-frame speech envelope 0..1 with fast attack, ~2s release."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", aac_path, "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    spf = SR // FPS
    n = len(x) // spf
    rms = np.sqrt(np.mean(x[: n * spf].reshape(n, spf) ** 2, axis=1))
    floor = np.percentile(rms[rms > 0], 20) if np.any(rms > 0) else 0.0
    thresh = max(0.015, floor * 4)
    active = rms > thresh
    env = np.zeros(n, np.float32)
    e, rel = 0.0, np.exp(-1.0 / (2.0 * FPS))
    for i in range(n):
        e = min(1.0, e + 0.45) if active[i] else e * rel
        env[i] = e
    return env

def utterances(tsv_path):
    """[(start_ms, end_ms, [(seg_start_ms, text), ...]), ...] merged by gap."""
    utts = []
    if not os.path.exists(tsv_path):
        return utts
    with open(tsv_path, encoding="utf-8") as f:
        next(f)  # header
        for ln in f:
            parts = ln.rstrip("\n").split("\t", 2)
            if len(parts) != 3 or not parts[2].strip():
                continue
            s, e, txt = int(parts[0]), int(parts[1]), parts[2].strip()
            e = min(e, s + 15000)  # clamp hallucinated hour-long segments
            if utts and s - utts[-1][1] <= UTT_GAP_MS:
                utts[-1][2].append((s, txt))
                utts[-1][1] = max(utts[-1][1], e)
            else:
                utts.append([s, e, [(s, txt)]])
    return utts

def lerp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

def wrap(text, fnt, maxw):
    lines, cur = [], ""
    for w in text.split():
        cand = (cur + " " + w).strip()
        if fnt.getlength(cand) <= maxw:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

# ── analyze stems ──────────────────────────────────────────────────────────
stem_files = sorted(f for ext in AUDIO_EXTS for f in glob.glob(os.path.join(PART_DIR, "*." + ext)))
if not stem_files:
    sys.exit(f"no audio stems in {PART_DIR}")
PALETTE = palette(speaker_name(f) for f in stem_files)
speakers = []  # (speaker, color, env, utts)
for f in stem_files:
    name = speaker_name(f)
    print("analyzing", name, flush=True)
    speakers.append((name, PALETTE.get(name, (200, 200, 200)), envelope(f),
                     utterances(f.rsplit(".", 1)[0] + ".tsv")))

total = max(len(s[2]) for s in speakers)
for i, (n, c, e, u) in enumerate(speakers):
    if len(e) < total:
        speakers[i] = (n, c, np.pad(e, (0, total - len(e))), u)

# ── layout ─────────────────────────────────────────────────────────────────
ROWS = len(speakers)
TAB_Y0 = 128
ROW_H = min(62, (H - TAB_Y0 - 12) // ROWS)
NAME_X, BOX_X, BOX_R = 64, 380, W - 40
TEXT_W = BOX_R - BOX_X - 28
LINE_H = 21
MAX_LINES = max(1, (ROW_H - 20) // LINE_H)

# ── static base frame ──────────────────────────────────────────────────────
base = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(base)
d.text((40, 24), TITLE, font=F_TITLE, fill=(242, 237, 228))
d.text((40, 56), PART.upper(), font=F_TITLE, fill=(255, 79, 163))
d.line([(40, 96), (W - 40, 96)], fill=LINE, width=2)
d.text((NAME_X, TAB_Y0 - 26), "ATTENDING", font=F_HDR, fill=DIM)
for i in range(ROWS):
    y = TAB_Y0 + i * ROW_H
    d.rounded_rectangle([BOX_X, y + 4, BOX_R, y + ROW_H - 6], radius=9,
                        fill=PANEL, outline=LINE, width=1)

# ── chapters + seek bar geometry ───────────────────────────────────────────
TOTAL_MS = total * 1000 // FPS
chapters = []  # (start_ms, end_ms, title)
if os.path.exists(CHAPTERS_TSV):
    with open(CHAPTERS_TSV, encoding="utf-8") as f:
        next(f)
        rows = [ln.rstrip("\n").split("\t", 1) for ln in f if ln.strip()]
    for j, (s, title) in enumerate(rows):
        end = int(rows[j + 1][0]) if j + 1 < len(rows) else TOTAL_MS
        chapters.append((int(s), end, title))
BAR_X0, BAR_X1 = 40, W - 40
BAR_Y0, BAR_Y1 = 648, 664
LBL_Y = (672, 694)   # staggered label rows (odd chapters lower)

def chap_x(ms):
    return BAR_X0 + (BAR_X1 - BAR_X0) * min(ms, TOTAL_MS) / max(TOTAL_MS, 1)

def draw_seekbar(d, t_ms):
    for j, (s, e, title) in enumerate(chapters):
        x0, x1 = chap_x(s), chap_x(e) - (3 if j < len(chapters) - 1 else 0)
        active = s <= t_ms < e
        past = t_ms >= e
        d.rounded_rectangle([x0, BAR_Y0, x1, BAR_Y1], radius=5, fill=PANEL,
                            outline=lerp(LINE, (255, 79, 163), 0.8 if active else 0.0), width=1)
        if past:
            d.rounded_rectangle([x0, BAR_Y0, x1, BAR_Y1], radius=5,
                                fill=lerp(PANEL, (255, 79, 163), 0.30))
        elif active and t_ms > s:
            d.rounded_rectangle([x0, BAR_Y0, max(x0 + 4, chap_x(t_ms)), BAR_Y1], radius=5,
                                fill=lerp((255, 79, 163), (79, 216, 224), (t_ms - s) / max(e - s, 1)))
        ly = LBL_Y[j % 2]
        lc = (255, 235, 245) if active else (lerp(DIM, (255, 79, 163), 0.35) if past else lerp(BG, DIM, 0.75))
        d.text((x0 + 2, ly), title, font=F_CHAP, fill=lc)
    # playhead notch
    px = chap_x(t_ms)
    d.polygon([(px - 5, BAR_Y0 - 7), (px + 5, BAR_Y0 - 7), (px, BAR_Y0 - 1)], fill=(245, 245, 245))
    d.line([(px, BAR_Y0), (px, BAR_Y1)], fill=(245, 245, 245), width=2)

# ── frame drawing ──────────────────────────────────────────────────────────
utt_idx = [0] * ROWS               # per-speaker pointer into utts (monotonic)
wrap_cache = [None] * ROWS         # (utt_i, n_segs) -> lines

def draw_frame(fr):
    img = base.copy()
    d = ImageDraw.Draw(img)
    t = fr // FPS
    t_ms = fr * 1000 // FPS
    d.text((W - 150, 56), f"{t // 60:02d}:{t % 60:02d}", font=F_TIME, fill=DIM)
    draw_seekbar(d, t_ms)
    for i, (name, color, env, utts) in enumerate(speakers):
        a_dot = float(env[fr])
        y = TAB_Y0 + i * ROW_H
        cy = y + ROW_H // 2 - 6
        # dot + name (audio-driven glow, as before)
        r = 9 + int(4 * a_dot)
        d.ellipse([NAME_X, cy - r, NAME_X + 2 * r, cy + r],
                  fill=lerp((50, 45, 65), color, max(a_dot, 0.12)))
        d.text((NAME_X + 34, cy - 12), name, font=F_NAME,
               fill=lerp(DIM, color, max(a_dot, 0.25)))
        # advance to the current utterance
        while utt_idx[i] + 1 < len(utts) and utts[utt_idx[i] + 1][0] <= t_ms:
            utt_idx[i] += 1
        if not utts or utts[utt_idx[i]][0] > t_ms:
            continue
        start, end, segs = utts[utt_idx[i]]
        a = 1.0 if t_ms <= end else math.exp(-((t_ms - end) / 1000.0) / FADE_S)
        # gate on real audio: text only lives while their track is (recently) active,
        # so hallucinated transcript timings can't pin stale text in the box
        a = min(a, min(1.0, a_dot * 2.5))
        if a < 0.03:
            continue
        # words revealed so far (whole segments whose start has passed)
        n_vis = sum(1 for s, _ in segs if s <= t_ms)
        key = (utt_idx[i], n_vis)
        if wrap_cache[i] is None or wrap_cache[i][0] != key:
            text = " ".join(txt for _, txt in segs[:n_vis])
            wrap_cache[i] = (key, wrap(text, F_SUB, TEXT_W))
        lines = wrap_cache[i][1]
        scrolled = len(lines) > MAX_LINES
        show = lines[-MAX_LINES:]
        d.rounded_rectangle([BOX_X, y + 4, BOX_R, y + ROW_H - 6], radius=9,
                            outline=lerp(LINE, color, a * 0.9), width=2)
        txt_c = lerp(PANEL, lerp((235, 230, 224), color, 0.25), a)
        for li, ln in enumerate(show):
            d.text((BOX_X + 14, y + 9 + li * LINE_H), ln, font=F_SUB, fill=txt_c)
        if scrolled:
            for gy in range(6):
                d.line([(BOX_X + 2, y + 5 + gy), (BOX_R - 2, y + 5 + gy)],
                       fill=lerp(PANEL, BG, 1 - gy / 6))
            ax = BOX_R - 22
            d.polygon([(ax, y + 13), (ax + 10, y + 13), (ax + 5, y + 7)],
                      fill=lerp(PANEL, color, 0.5 * a))
    return img

# \─\─ preview mode: one frame to PNG \─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─
if args.preview is not None:
    t_prev = args.preview
    fr = min(total - 1, int(t_prev * FPS))
    # fast-forward utterance pointers (draw_frame only advances, never rewinds)
    for i, (_, _, _, utts) in enumerate(speakers):
        while utt_idx[i] + 1 < len(utts) and utts[utt_idx[i] + 1][0] <= fr * 1000 // FPS:
            utt_idx[i] += 1
    out_png = os.path.splitext(OUT)[0] + "-preview.png"
    draw_frame(fr).save(out_png)
    print("preview:", out_png)
    sys.exit(0)

# \─\─ render \─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─\─
ff = subprocess.Popen(
    ["ffmpeg", "-y", "-v", "error",
     "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
     "-i", MIXDOWN, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
     "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", "-movflags", "+faststart", OUT],
    stdin=subprocess.PIPE)

for fr in range(total):
    ff.stdin.write(draw_frame(fr).tobytes())
    if fr % (FPS * 300) == 0:
        print(f"frame {fr}/{total} ({fr / total:.0%})", flush=True)

ff.stdin.close()
ff.wait()
print("done:", OUT, os.path.getsize(OUT), "bytes")
