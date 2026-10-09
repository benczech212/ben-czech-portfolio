"""Cast the live facade camera feed to a Chromecast (e.g. "Living Room TV").

ffmpeg turns the Elgato Virtual Camera + the music (Voicemeeter B2) into a live HLS stream with
live captions (vibe / latest effect top-left, the Spotify track top-right, like the showreels), a
tiny web server (with the CORS headers the Chromecast's player needs) serves it on the LAN, and
pychromecast tells the TV to play it. Buffered for smoothness: ~10 s behind the room.

  python tools/cast_feed.py                         # cast to Living Room TV until Ctrl-C
  python tools/cast_feed.py --tv "Kitchen display" --no-audio --no-captions
  python tools/cast_feed.py --stop                  # stop whatever is casting on the TV
"""

import argparse
import functools
import http.server
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pychromecast

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from record_clip import AUDIO, VIDEO, encoder  # noqa: E402

PORT = 8130
# Chromecast addresses to try directly, in case mDNS discovery is flaky: CAST_HOSTS="a.b.c.d,e.f.g.h"
KNOWN_HOSTS = [h.strip() for h in os.environ.get("CAST_HOSTS", "").split(",") if h.strip()]


def lan_ip():
    """This PC's LAN address: the source address of the default route. A UDP connect() sends nothing."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
    s.close()
    return ip


class CORSHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def guess_type(self, path):
        if str(path).endswith(".m3u8"):
            return "application/vnd.apple.mpegurl"
        if str(path).endswith(".ts"):
            return "video/mp2t"
        return super().guess_type(path)

    def log_message(self, *a):
        pass


FONT = "C\\:/Windows/Fonts/segoeuib.ttf"
FONT_REG = FONT.replace("segoeuib", "segoeui")
API = "http://127.0.0.1:8121"
CAPTIONS = ("vibe", "fx", "mlabel", "mtitle", "martist")
ZMQ_ADDR = "tcp://127.0.0.1:5556"


def _esc(text):
    """Text for a drawtext option value inside a zmq 'reinit' command."""
    text = (text or " ").replace("\\", "\\\\").replace("'", "’").replace(":", "\\:")
    return f"'{text}'"


def caption_writer(stop):
    """Keep the captions current: vibe + latest effect from the bridge, the track from Spotify.
    Changes go to ffmpeg over its zmq command channel (no files: on Windows, ffmpeg re-reading a caption
    file while it was being replaced hit 'permission denied' and killed the stream)."""
    import requests
    import zmq
    from facade_zones import VIBE_LABELS
    from now_playing import now_playing
    sock = zmq.Context.instance().socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 2000)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(ZMQ_ADDR)
    shown = {}

    def show(name, text):
        nonlocal sock
        if shown.get(name) == text:
            return
        try:
            sock.send_string(f"drawtext@{name} reinit text={_esc(text)}")
            sock.recv_string()
            shown[name] = text
        except zmq.ZMQError:                   # ffmpeg busy/restarting: new socket, retry next round
            sock.close()
            sock = zmq.Context.instance().socket(zmq.REQ)
            sock.setsockopt(zmq.RCVTIMEO, 2000)
            sock.setsockopt(zmq.LINGER, 0)
            sock.connect(ZMQ_ADDR)

    song_at = 0.0
    while not stop.is_set():
        try:
            st = requests.get(API + "/api/state", timeout=2).json()
            vibe = VIBE_LABELS.get(st.get("vibe"), st.get("vibe") or "")
            show("vibe", f"Vibe · {vibe.title()}" if vibe else " ")
            fx = " "
            last = (st.get("last") or [None])[0]
            if last:
                h, m, sec = (int(x) for x in last["at"].split(":"))
                now = time.localtime()
                age = (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec) - (h * 3600 + m * 60 + sec)
                if 0 <= age <= 3:
                    fx = f"Effect · {last['effect'].title()}"
            show("fx", fx)
        except Exception:
            pass
        if time.time() - song_at > 2:
            song_at = time.time()
            try:
                song = now_playing()
            except Exception:
                song = None
            show("mlabel", "MUSIC" if song else " ")
            show("mtitle", song[0] if song else " ")
            show("martist", song[1] if song else " ")
        stop.wait(0.5)


def captions_filter(k=1.0):
    box = "box=1:boxcolor=black@0.35:boxborderw={b}:expansion=none:text=' '"
    # ':' inside an option value is escaped twice (option level, then filtergraph level) -> three backslashes
    zmq_in = "zmq=bind_address=" + ZMQ_ADDR.replace(":", r"\\\:")
    return ",".join([
        zmq_in,
        f"drawtext@vibe=fontfile='{FONT}':fontsize={int(43 * k)}:fontcolor=white:{box.format(b=int(12 * k))}:x={int(33 * k)}:y={int(33 * k)}",
        f"drawtext@fx=fontfile='{FONT}':fontsize={int(30 * k)}:fontcolor=0xffd166:{box.format(b=int(9 * k))}:x={int(33 * k)}:y={int(95 * k)}",
        f"drawtext@mlabel=fontfile='{FONT}':fontsize={int(17 * k)}:fontcolor=0xffd166:{box.format(b=int(7 * k))}:x=w-text_w-{int(33 * k)}:y={int(33 * k)}",
        f"drawtext@mtitle=fontfile='{FONT}':fontsize={int(29 * k)}:fontcolor=white:{box.format(b=int(9 * k))}:x=w-text_w-{int(33 * k)}:y={int(68 * k)}",
        f"drawtext@martist=fontfile='{FONT_REG}':fontsize={int(23 * k)}:fontcolor=white@0.85:{box.format(b=int(8 * k))}:x=w-text_w-{int(33 * k)}:y={int(119 * k)}",
    ])


def find_tv(name):
    norm = lambda s: s.lower().replace("’", "'").strip()
    casts, browser = pychromecast.get_chromecasts(timeout=15, known_hosts=KNOWN_HOSTS)
    match = [c for c in casts if norm(c.cast_info.friendly_name) == norm(name)]
    if not match:
        pychromecast.discovery.stop_discovery(browser)
        sys.exit(f"no Chromecast called {name!r} found (saw: {[c.cast_info.friendly_name for c in casts]})")
    cast = match[0]
    cast.wait()                                      # connect while discovery is still running
    pychromecast.discovery.stop_discovery(browser)
    return cast


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tv", default="Living Room TV", help="the Chromecast's friendly name")
    ap.add_argument("--no-audio", action="store_true", help="video only")
    ap.add_argument("--height", type=int, default=540, help="stream height (540 = 960x540, 720 = 1280x720)")
    ap.add_argument("--kbps", type=int, default=2500, help="video bitrate; lower if the TV stutters")
    ap.add_argument("--no-captions", action="store_true", help="no vibe/effect/track text")
    ap.add_argument("--stop", action="store_true")
    args = ap.parse_args()

    cast = find_tv(args.tv)
    if args.stop:
        cast.quit_app()
        print(f"stopped casting on {args.tv}")
        return

    out = pathlib.Path(tempfile.mkdtemp(prefix="facade_cast_"))
    enc = encoder()
    audio = not args.no_audio
    audio_in = ["-f", "dshow", "-thread_queue_size", "4096", "-use_wallclock_as_timestamps", "1",
                "-audio_buffer_size", "50", "-i", f"audio={AUDIO}"] if audio else []
    audio_out = ["-map", "1:a", "-af", "aresample=async=1000", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"] if audio else []
    vf = f"scale=-2:{args.height}:flags=lanczos"
    if not args.no_captions:
        vf += "," + captions_filter(args.height / 720)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "dshow", "-rtbufsize", "1024M", "-thread_queue_size", "4096", "-use_wallclock_as_timestamps", "1",
           "-pixel_format", "nv12", "-video_size", "1920x1080", "-framerate", "30",
           "-i", f"video={VIDEO}", *audio_in,
           "-map", "0:v", "-vf", vf, "-fps_mode", "cfr", "-r", "30", "-c:v", enc,
           *(["-preset", "p4", "-tune", "ll", "-rc", "cbr"] if enc == "h264_nvenc" else ["-preset", "veryfast", "-tune", "zerolatency"]),
           "-b:v", f"{args.kbps}k", "-maxrate", f"{args.kbps}k", "-bufsize", f"{args.kbps * 2}k", "-g", "60", "-pix_fmt", "yuv420p", *audio_out,
           "-f", "hls", "-hls_time", "3", "-hls_list_size", "12",
           "-hls_flags", "delete_segments+independent_segments+omit_endlist", str(out / "live.m3u8")]
    ff = subprocess.Popen(cmd)
    handler = functools.partial(CORSHandler, directory=str(out))
    # NOTE: binds 0.0.0.0 on purpose - the Chromecast fetches the stream over the LAN, so the HLS files
    # (live camera + music) are readable by anyone on the network while this runs. No auth.
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for _ in range(60):                              # wait for the first segments (a deeper buffer = smoother)
        if (out / "live.m3u8").exists() and len(list(out.glob("*.ts"))) >= 4:
            break
        time.sleep(0.5)
    stop = threading.Event()
    if not args.no_captions:
        threading.Thread(target=caption_writer, args=(stop,), daemon=True).start()
    url = f"http://{lan_ip()}:{PORT}/live.m3u8"
    print(f"stream ready: {url}", flush=True)
    mc = cast.media_controller
    # The default receiver assumes packed-AAC audio in HLS; ours is MPEG-TS with audio+video muxed, so
    # say so or it buffers forever (video-only streams play without this).
    mc.play_media(url, "application/x-mpegURL", title="Etherical Lotus Flower - live", stream_type="LIVE",
                  media_info={"hlsSegmentFormat": "ts", "hlsVideoSegmentFormat": "mpeg2_ts"})
    mc.block_until_active(timeout=20)
    print(f"casting to {args.tv}; Ctrl-C to stop", flush=True)
    try:
        while ff.poll() is None:
            time.sleep(5)
            mc.update_status()
            print(f"  tv: {mc.status.player_state}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            cast.quit_app()
        except Exception:
            pass
        stop.set()
        ff.terminate()
        server.shutdown()
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    main()
