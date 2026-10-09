"""One-take showreel: every vibe, then every effect, with the music and on-screen names.

1. Captures the Elgato Virtual Camera + Voicemeeter B1 (the music) at high quality while it steps
   the facade through the running order via the bridge's controller API (bridge must be running).
2. Logs when each change actually happened, then adds a fading title for each segment (cross-fading
   at every change) into a full-quality 1080p master (<name>_master.mp4), and re-encodes that into a
   Facebook-ready copy under the size limit.

  python tools/record_showreel.py                        # ~2 min -> recordings/<timestamp>_showreel.mp4
  python tools/record_showreel.py --vibe-seconds 6 --effect-seconds 3

Optional opening-credit extras, from the environment:
  SHOWREEL_LOGOS    logo image paths separated by os.pathsep (";" on Windows, ":" elsewhere),
                    shown side by side; missing files are skipped, none = text-only credits
  SHOWREEL_TAGLINE  a line under "Created by Ben Czech"; empty = no line
"""

import argparse
import datetime
import os
import pathlib
import re
import subprocess
import sys
import threading
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from now_playing import now_playing  # noqa: E402
from spotify_control import start_fresh_song  # noqa: E402
from record_clip import AUDIO, VIDEO, encoder  # noqa: E402

API = "http://127.0.0.1:8121"
PIECE_NAME = "Etherical Lotus Flower"            # the facade's name, in the opening credits
INTRO = 7.0                                     # opening credits over the facade, before the first change
LOGOS = [pathlib.Path(p.strip()) for p in os.environ.get("SHOWREEL_LOGOS", "").split(os.pathsep) if p.strip()]
TAGLINE = os.environ.get("SHOWREEL_TAGLINE", "").strip()
VERSION_RE = re.compile(r"[A-Za-z0-9._-]{0,16}")   # the version reaches a file name and a drawtext
FONT = "C\\:/Windows/Fonts/segoeuib.ttf"      # Segoe UI Bold (colon escaped for ffmpeg filters)
FONT_REG = FONT.replace("segoeuib", "segoeui")   # Segoe UI regular
VIBES = ["cozy", "party", "chaos", "wave", "jumping", "ethereal", "snake", "twinkle", "heartbeat",
         "aurora", "meteor", "bloom", "kaleido",
         "lotus", "spectrum", "fireflies", "cascade", "mycelium", "butterfly",
         "mosaic", "wings", "vortex"]
EFFECTS = [  # effect, colour, re-fire every N s so it stays visible for its whole segment
    ("flash", "#ffffff", 2.0), ("pulse", "#ff4fd8", 2.5), ("color", "#00ffcc", 3.0), ("charge", "#ffaa00", 1.5),
    ("sweep", "#ff4000", 2.5), ("shockwave", "#3fa9ff", 2.0), ("rings", "#ffee00", 3.0), ("spiral", "#ff00aa", 3.3),
    ("wipe", "#40ff00", 3.5), ("rain", "#8a2be2", 4.0), ("comet", "#ffffff", 2.5),
    ("halo", "#ffd27f", 2.0), ("iris", "#000000", 2.0), ("petalfall", "#000000", 2.0), ("sunburst", "#ffe066", 3.0),
    ("sporeburst", "#9bff6a", 2.5)]
EFFECT_BASE = "twinkle"


def post(path, body):
    requests.post(API + path, json=body, timeout=3).raise_for_status()


def plan(args):
    """[(label, action, seconds, repeat_every)] - action runs at the segment's start (and repeats)."""
    steps = []
    for v in VIBES:
        from facade_zones import VIBE_LABELS
        steps.append((f"Vibe · {VIBE_LABELS.get(v, v).title()}", lambda v=v: post("/api/vibe", {"vibe": v}),
                      args.vibe_seconds, None))
    steps.append((f"Effects on {EFFECT_BASE.title()}", lambda: post("/api/vibe", {"vibe": EFFECT_BASE}), 3, None))
    for fx, color, every in EFFECTS:
        steps.append((f"Effect · {fx.title()}",
                      lambda fx=fx, c=color: post("/api/effect", {"effect": fx, "color": c, "intensity": 1, "zone": "all"}),
                      args.effect_seconds, every))
    return steps


def health(label, at):
    """A second after each change: is the Pixelblaze rendering, and on the vibe we asked for?"""
    time.sleep(1.2)
    try:
        live = requests.get(API + "/api/live", timeout=3).json()
        want = label.split("·")[-1].strip().lower() if label.startswith("Vibe") else None
        from facade_zones import VIBE_LABELS
        got = VIBE_LABELS.get(live.get("vibe"), live.get("vibe"))
        flag = "" if live.get("fps") else "  << NO FRAMES (Pixelblaze not reporting)"
        if want and got != want:
            flag += f"  << showing {got}, expected {want}"
        print(f"  health {at:6.1f} s  fps {live.get('fps')}  vibe {got}{flag}", flush=True)
    except Exception as e:
        print(f"  health {at:6.1f} s  bridge unreachable: {e.__class__.__name__}", flush=True)


def capture(raw: pathlib.Path, seconds: float):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-progress", "pipe:1", "-nostats",
           "-f", "dshow", "-rtbufsize", "1500M", "-thread_queue_size", "4096", "-use_wallclock_as_timestamps", "1",
           "-pixel_format", "nv12", "-video_size", "1920x1080", "-framerate", "30", "-i", f"video={VIDEO}",
           "-f", "dshow", "-thread_queue_size", "4096", "-use_wallclock_as_timestamps", "1",
           "-audio_buffer_size", "50", "-i", f"audio={AUDIO}",
           "-t", f"{seconds:.1f}", "-map", "0:v", "-map", "1:a",
           "-c:v", encoder(), "-cq", "17", "-b:v", "25M", "-pix_fmt", "yuv420p", "-fps_mode", "cfr", "-r", "30",
           "-af", "aresample=async=1000", "-c:a", "aac", "-b:a", "256k", "-ar", "48000", str(raw)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)


def watch_progress(proc):
    """Drain ffmpeg's progress output for the whole recording (an unread pipe fills up and stalls
    the capture - that froze the end of the first showreel) and return the wall-clock time of
    frame 0 as soon as it's known."""
    first = threading.Event()
    t0 = [None]

    def reader():
        for line in proc.stdout:
            if t0[0] is None and line.startswith("out_time_us="):
                v = line.split("=")[1].strip()
                if v.isdigit() and int(v) > 0:
                    t0[0] = time.time() - int(v) / 1e6
                    first.set()
        first.set()

    threading.Thread(target=reader, daemon=True).start()
    first.wait(30)
    if t0[0] is None:
        raise RuntimeError("ffmpeg stopped before recording anything")
    return t0[0]


def track_spans(changes, total):
    """[(t, (title, artist) or None)] -> [(start, end, title, artist)] for the stretches music played."""
    spans = []
    for i, (t, track) in enumerate(changes):
        end = changes[i + 1][0] if i + 1 < len(changes) else total
        if track and end - t > 1.5:
            spans.append((max(0.0, t), end, track[0], track[1]))
    return spans


def _dt_text(s):
    """Text for a quoted drawtext value: escape backslashes and colons, swap ' for a typographic one."""
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")


def credits_graph(width, height, scale, intro=INTRO, fade=0.6, version="", n_logos=0, tagline=TAGLINE):
    """Opening credits: darken the facade, the logos side by side (if any), then the names - all fading.
    Returns filter-graph text taking [v0] (the video) and, when n_logos > 0, the logo images as
    ffmpeg inputs [1:v] .. [n_logos:v] (input 0 is the recording), and producing [vc]."""
    m = lambda v: int(v * scale)

    def alpha(t1, t2):
        return (f"if(lt(t,{t1:.2f}),0,if(lt(t,{t1 + fade:.2f}),(t-{t1:.2f})/{fade},"
                f"if(lt(t,{t2 - fade:.2f}),1,if(lt(t,{t2:.2f}),({t2:.2f}-t)/{fade},0))))")

    g = [f"color=c=black:s={width}x{height}:r=30:d={intro},format=rgba,colorchannelmixer=aa=0.62,"
         f"fade=t=in:st=0:d=0.5:alpha=1,fade=t=out:st={intro - 1:.2f}:d=1:alpha=1[scrim]",
         "[v0][scrim]overlay=0:0:eof_action=pass[c1]"]
    last = "c1"
    if n_logos > 0:
        logo_h, gap = m(180), m(90)
        cy = int(height * 0.33)
        for i in range(1, n_logos + 1):           # same height each; a transparent gap after all but the last
            pad = f",pad=iw+{gap}:ih:0:0:color=black@0" if i < n_logos else ""
            g.append(f"[{i}:v]scale=-1:{logo_h},format=rgba{pad}[lg{i}]")
        row = "".join(f"[lg{i}]" for i in range(1, n_logos + 1))
        g.append(f"{row}hstack=inputs={n_logos}[logos]" if n_logos > 1 else "[lg1]null[logos]")
        g.append(f"[logos]fade=t=in:st=0.4:d=0.8:alpha=1,fade=t=out:st={intro - 1.3:.2f}:d=0.9:alpha=1[lrow]")
        g.append(f"[c1][lrow]overlay=x=(W-w)/2:y={cy}-h/2:eof_action=pass[c2]")
        last = "c2"
    top = 0.52 if n_logos > 0 else 0.42                # centre the names when there are no logos
    names = [(PIECE_NAME + (f"  {version}" if version else ""), FONT, 88, "white", top, 0.5),
             ("Created by Ben Czech", FONT_REG, 44, "white", top + 115 / 1080, 0.9)]
    if tagline:
        names.append((tagline, FONT, 36, "0xffd166", top + 175 / 1080, 1.2))
    texts = ",".join(
        f"drawtext=fontfile='{font}':text='{_dt_text(t)}':expansion=none:fontsize={m(size)}:fontcolor={color}:"
        f"shadowcolor=black@0.6:shadowx={m(3)}:shadowy={m(3)}:x=(w-text_w)/2:y={int(height * y)}:"
        f"alpha='{alpha(start, intro - 0.3)}'"
        for t, font, size, color, y, start in names)
    g.append(f"[{last}]{texts}[vc]")
    return ";".join(g)


def overlay_filter(marks, total, intro=4.0, fade=0.6, scale=1.0, tracks=()):
    """drawtext filters: an intro title, each segment's label (top left) and the playing track
    (top right), each fading in/out at its edges."""
    def alpha(t1, t2):
        return (f"if(lt(t,{t1:.2f}),0,if(lt(t,{t1 + fade:.2f}),(t-{t1:.2f})/{fade},"
                f"if(lt(t,{t2 - fade:.2f}),1,if(lt(t,{t2:.2f}),({t2:.2f}-t)/{fade},0))))")

    def text(s):
        return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")

    parts = []
    for i, (label, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else total
        parts.append(f"drawtext=fontfile='{FONT}':text='{text(label)}':expansion=none:fontsize={int(64 * scale)}:fontcolor=white:"
                     f"box=1:boxcolor=black@0.35:boxborderw={int(18 * scale)}:x={int(50 * scale)}:y={int(50 * scale)}:alpha='{alpha(start, end)}'")
    m = lambda v: int(v * scale)
    for start, end, title, artist in tracks:
        a = alpha(start, end)
        parts.append(f"drawtext=fontfile='{FONT}':text='MUSIC':expansion=none:fontsize={m(26)}:"
                     f"fontcolor=0xffd166:box=1:boxcolor=black@0.35:boxborderw={m(10)}:"
                     f"x=w-text_w-{m(50)}:y={m(50)}:alpha='{a}'")
        parts.append(f"drawtext=fontfile='{FONT}':text='{text(title)}':expansion=none:fontsize={m(44)}:"
                     f"fontcolor=white:box=1:boxcolor=black@0.35:boxborderw={m(14)}:"
                     f"x=w-text_w-{m(50)}:y={m(50 + 52)}:alpha='{a}'")
        parts.append(f"drawtext=fontfile='{FONT_REG}':text='{text(artist)}':expansion=none:fontsize={m(34)}:"
                     f"fontcolor=white@0.85:box=1:boxcolor=black@0.35:boxborderw={m(12)}:"
                     f"x=w-text_w-{m(50)}:y={m(50 + 52 + 76)}:alpha='{a}'")
    return ",".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vibe-seconds", type=float, default=10)
    ap.add_argument("--effect-seconds", type=float, default=10)
    ap.add_argument("--max-mb", type=float, default=95)
    ap.add_argument("--version", default="", help="shown after the name in the credits and in the file name, e.g. V2.2")
    ap.add_argument("--spotify", choices=["next", "none"], default="next",
                    help="next: skip to the next song as the recording starts, so it plays from its beginning")
    args = ap.parse_args()
    if VERSION_RE.fullmatch(args.version) is None:
        sys.exit("--version must be up to 16 characters of A-Z a-z 0-9 . _ -")
    logo_files = [f for f in LOGOS if f.is_file()]
    for f in LOGOS:
        if not f.is_file():
            print(f"logo not found, skipping: {f}", flush=True)

    try:
        requests.get(API + "/api/state", timeout=3)
    except requests.RequestException:
        sys.exit("start the bridge first: python tools/facade_bridge.py --host localhost")
    steps = plan(args)
    total = INTRO + sum(s for _, _, s, _ in steps) + 1.5
    out_dir = ROOT / "recordings"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    raw = out_dir / f".{stamp}_raw.mp4"
    out = out_dir / f"{stamp}_showreel{'_' + args.version if args.version else ''}.mp4"

    post("/api/followall", {})
    post("/api/set", {"auto": False})
    post("/api/vibe", {"vibe": "ethereal"})
    print(f"recording {total:.0f} s: {len(steps)} segments")
    proc = capture(raw, total)
    t0 = watch_progress(proc)
    # what's playing, checked every second on the video's clock
    tracks, done = [], threading.Event()

    def poll_tracks():
        last = "unset"
        while not done.is_set():
            np_ = now_playing()
            if np_ != last:
                tracks.append((time.time() - t0, np_))
                last = np_
                if np_:
                    print(f"  {tracks[-1][0]:6.1f} s  now playing: {np_[0]} - {np_[1]}", flush=True)
            done.wait(1.0)

    threading.Thread(target=poll_tracks, daemon=True).start()
    if args.spotify == "next":
        def fresh_song():
            try:
                song = start_fresh_song()
                print(f"  spotify: started {song[0]} - {song[1]}" if song else "  spotify: nothing playing", flush=True)
            except Exception as e:
                print(f"  spotify: {e}", flush=True)
        threading.Thread(target=fresh_song, daemon=True).start()
    marks = []
    at = INTRO                                     # credits play over the current look, then the tour
    for label, action, seconds, every in steps:
        time.sleep(max(0.0, t0 + at - time.time()))
        action()
        marks.append((label, time.time() - t0))    # when it actually happened, on the video's clock
        print(f"  {marks[-1][1]:6.1f} s  {label}", flush=True)
        threading.Thread(target=health, args=(label, marks[-1][1]), daemon=True).start()
        if every:                                  # keep a short effect going for the whole segment
            k = 1
            while at + k * every < at + seconds - 0.8:
                time.sleep(max(0.0, t0 + at + k * every - time.time()))
                action()
                k += 1
        at += seconds
    proc.wait()                                    # let the capture finish
    done.set()
    post("/api/vibe", {"vibe": "ethereal"})

    # 1) master: titles added at full 1080p, high quality (keep this one for editing / big screens)
    master = out.with_name(out.stem + "_master.mp4")
    labels = overlay_filter(marks, total, scale=1.0, tracks=track_spans(tracks, total))
    filt = ";".join(["[0:v]scale=1920:1080:flags=lanczos[v0]",
                     credits_graph(1920, 1080, 1.0, version=args.version, n_logos=len(logo_files)),
                     f"[vc]{labels}[vout]" if labels else "[vc]null[vout]"])
    script = out_dir / f".{stamp}_overlay.txt"
    script.write_text(filt, encoding="utf-8")
    enc = encoder()
    hq = (["-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "18", "-b:v", "0", "-maxrate", "40M", "-bufsize", "80M",
           "-profile:v", "high"] if enc == "h264_nvenc" else ["-preset", "slow", "-crf", "16"])
    print("adding titles: master 1080p (high quality) ...", flush=True)
    # inputs 1..N are the logos, in the same order credits_graph numbers them; none = text-only credits
    logos = [a for f in logo_files for a in ("-loop", "1", "-framerate", "30", "-t", str(INTRO), "-i", str(f))]
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw), *logos,
                    "-filter_complex_script", str(script), "-map", "[vout]", "-map", "0:a",
                    "-c:v", enc, *hq, "-pix_fmt", "yuv420p", "-g", "60",
                    "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart", str(master)], check=True)
    print(f"master: {master} ({master.stat().st_size / 1e6:.1f} MB)", flush=True)

    # 2) share copy: the master re-encoded to fit the size limit (titles scale with the picture)
    audio_kbps = 192
    video_kbps = int(min(8000, args.max_mb * 8000 / total * 0.95 - audio_kbps))
    # under ~4.5 Mb/s, 1080p turns blocky on busy patterns: give the bits to 720p instead
    height = 1080 if video_kbps >= 4500 else 720
    vopts = ["-preset", "p6", "-rc", "vbr", "-cq", "21"] if enc == "h264_nvenc" else ["-preset", "medium", "-crf", "20"]
    print(f"share copy: {height}p at {video_kbps} kb/s ...", flush=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(master),
                    *([] if height == 1080 else ["-vf", f"scale=-2:{height}:flags=lanczos"]),
                    "-c:v", enc, *vopts, "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k",
                    "-bufsize", f"{video_kbps * 2}k", "-pix_fmt", "yuv420p", "-g", "60",
                    "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-movflags", "+faststart", str(out)], check=True)
    for f in (raw, script):                        # Defender may still be scanning the raw file
        for _ in range(10):
            try:
                f.unlink()
                break
            except PermissionError:
                time.sleep(1)
    print(f"done: {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
