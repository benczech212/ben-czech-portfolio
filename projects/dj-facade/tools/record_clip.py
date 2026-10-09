"""Record the facade with the Facecam plus the music that's driving it, ready for Facebook.

Video: the Elgato Virtual Camera at 1080p30 (Camera Hub's output, so its exposure settings apply
and Camera Hub can stay open - the raw Facecam can only be opened by one app). Audio: Voicemeeter's B2 tap - the same app audio that goes
to the Pixelblaze's line-in, without the microphones. Output: H.264 + AAC MP4 (Facebook's preferred format), with the
bitrate sized from the length so the file stays under the size limit (default 95 MB).

  python tools/record_clip.py                      # 30 s -> recordings/<timestamp>.mp4
  python tools/record_clip.py --seconds 60 --name bloom-demo
  python tools/record_clip.py --seconds 45 --vibe kaleido --auto   # set the look first

For the best picture, lock exposure low in Elgato Camera Hub (auto exposure blows the LEDs out).
"""

import argparse
import datetime
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
VIDEO = "EƖgato Virtual Camera"    # sic: Windows lists it with a Latin "Ɩ", not an "l"
# Voicemeeter B2 = Windows/app audio only (vm_audio.py routes it there). B1 also carries the
# microphones, and Banana's "Out A2" tap is silent.
AUDIO = "Voicemeeter Out B2 (VB-Audio Voicemeeter VAIO)"
VIBES = ["cozy", "party", "chaos", "wave", "jumping", "ethereal", "snake", "twinkle", "heartbeat",
         "aurora", "meteor", "bloom", "kaleido"]


def encoder():
    """Hardware H.264 (NVIDIA) if ffmpeg has it - keeps the PC free - else x264."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return "h264_nvenc" if "h264_nvenc" in out else "libx264"


def set_look(args):
    if args.vibe is None and not args.auto:
        return
    from pixelblaze import Pixelblaze
    with Pixelblaze(args.ip) as pb:
        v = {}
        if args.vibe:
            v.update(vibe=VIBES.index(args.vibe), **{f"zv{k}": -1 for k in range(7)})
        if args.auto:
            v.update(autoMode=1, autoSeconds=args.auto_seconds, autoLeft=args.auto_seconds)
        pb.setActiveVariables(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--name", default=None)
    ap.add_argument("--max-mb", type=float, default=95)
    ap.add_argument("--vibe", choices=VIBES)
    ap.add_argument("--auto", action="store_true", help="turn on full auto for the recording")
    ap.add_argument("--auto-seconds", type=float, default=10)
    ap.add_argument("--ip", default=os.environ.get("PIXELBLAZE_IP"), required=not os.environ.get("PIXELBLAZE_IP"),
                    help="Pixelblaze address (or set PIXELBLAZE_IP)")
    args = ap.parse_args()
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found")

    set_look(args)
    audio_kbps = 192
    budget_kbps = args.max_mb * 8000 / args.seconds * 0.95      # 5% for MP4 overhead
    video_kbps = int(min(8000, budget_kbps - audio_kbps))
    if video_kbps < 1500:
        sys.exit(f"{args.seconds:.0f} s won't fit in {args.max_mb} MB at a watchable quality; record shorter clips")

    out_dir = ROOT / "recordings"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = out_dir / f"{stamp}{'_' + args.name if args.name else ''}.mp4"
    enc = encoder()
    vopts = (["-preset", "p5", "-rc", "vbr", "-cq", "21"] if enc == "h264_nvenc" else ["-preset", "medium", "-crf", "20"])
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
           # both inputs stamped from the wall clock so picture and sound stay in sync
           "-f", "dshow", "-rtbufsize", "1500M", "-thread_queue_size", "4096", "-use_wallclock_as_timestamps", "1",
           "-pixel_format", "nv12", "-video_size", "1920x1080", "-framerate", "30", "-i", f"video={VIDEO}",
           "-f", "dshow", "-thread_queue_size", "4096", "-use_wallclock_as_timestamps", "1",
           "-audio_buffer_size", "50", "-i", f"audio={AUDIO}",
           "-t", str(args.seconds), "-map", "0:v", "-map", "1:a",
           "-c:v", enc, *vopts, "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k",
           "-pix_fmt", "yuv420p", "-fps_mode", "cfr", "-r", "30", "-g", "60",
           "-af", "aresample=async=1000",       # smooth the audio clock's jitter against the video
           "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ar", "48000", "-ac", "2",
           "-movflags", "+faststart", str(out)]
    print(f"recording {args.seconds:.0f} s ({enc}, video {video_kbps} kb/s) -> {out}")
    subprocess.run(cmd, check=True)
    mb = out.stat().st_size / 1e6
    print(f"done: {mb:.1f} MB")


if __name__ == "__main__":
    main()
