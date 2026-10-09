"""Before/after preview of the re-encode settings: each test clip encoded at the 2.5 GB/h cap and at a higher 4 GB/h
option, with frames from the original and both encodes (full frame and a 1:1 crop) plus sizes and VMAF.
Writes cache/preview/*.jpg and cache/preview/preview.json.
Test clips: short excerpts you cut yourself, placed in the preview_clips folder inside WORK_DIR under the names in TESTS below.

  python dashboard/build_encode_preview.py
"""
import json, os, re, sys, time
import reencode_common as rq
import reencode_worker as w
import config

HERE = os.path.dirname(os.path.abspath(__file__))
PREV = os.path.join(HERE, "cache", "preview")
CLIPS = os.path.join(config.WORK_DIR, "preview_clips")
os.makedirs(PREV, exist_ok=True)
TESTS = [
    ("film", "1080p film (grainy Blu-ray, H.264)", "1080p_film_H_264_src.mkv"),
    ("hdr", "4K HDR film scaled to 1080p", "4K_HDR_film_1080p_src.mkv"),
    ("tv", "1080p TV episode (web)", "1080p_TV_episode_src.mkv"),
]
VARIANTS = [("cap25", "2.5 GB/h (your cap)", 2.5), ("cap40", "4 GB/h (higher option)", 4.0)]
TIMES = (8, 28, 48)
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def encode(src, out, gbh):
    info = w.probe(src)
    old = rq.CAP_GBH
    w.CAP_GBH = gbh  # the worker reads its cap from this module-level name
    try:
        r = w.run(w.build_cmd({"kind": "video", "src_duration": float(info["format"]["duration"])}, info, ["-i", src], out))
    finally:
        w.CAP_GBH = old
    return r.returncode == 0


def frame(path, t, name, hdr, full_scale_1080):
    """Two JPEGs: the whole frame (960 wide) and a 1:1 crop from the middle of the 1080p picture."""
    pre = "scale=1920:-2:flags=lanczos," if full_scale_1080 else ""
    tm = TONEMAP + "," if hdr else ""
    for kind, vf in (("full", f"{pre}{tm}scale=960:-2"), ("crop", f"{pre}{tm}crop=640:360:(iw-640)/2:(ih-360)/2")):
        w.run([w.FFMPEG, "-v", "error", "-y", "-ss", str(t), "-i", path, "-frames:v", "1", "-vf", vf, "-q:v", "2",
               os.path.join(PREV, f"{name}_{kind}_{t}.jpg")])


def whole_vmaf(out, src, width):
    scale = "scale=1920:-2:flags=bicubic," if width > 1920 else ""
    r = w.run([w.FFMPEG, "-v", "info", "-nostats", "-i", out, "-i", src, "-lavfi",
               f"[0:v]format=yuv420p10le,setpts=PTS-STARTPTS[d];[1:v]{scale}format=yuv420p10le,setpts=PTS-STARTPTS[r];"
               "[d][r]libvmaf=n_threads=8:n_subsample=2", "-f", "null", "-"])
    m = re.search(r"VMAF score: ([\d.]+)", r.stderr)
    return float(m.group(1)) if m else None


items = []
for key, label, fname in TESTS:
    src = os.path.join(CLIPS, fname)
    info = w.probe(src)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    hdr = v.get("color_transfer") in ("smpte2084", "arib-std-b67")
    big = (v.get("width") or 0) > 1920
    dur = float(info["format"]["duration"])
    item = {"key": key, "label": label, "src": src, "src_res": f"{v['width']}x{v['height']}", "hdr": hdr,
            "src_gbh": os.path.getsize(src) / 1e9 / (dur / 3600), "times": list(TIMES), "variants": []}
    for t in TIMES:
        frame(src, t, f"{key}_orig", hdr, big)
    for vk, vlabel, gbh in VARIANTS:
        out = os.path.join(CLIPS, f"{key}_{vk}.mkv")
        t0 = time.time()
        ok = encode(src, out, gbh)
        if not ok:
            print(key, vk, "encode failed"); continue
        secs = time.time() - t0
        for t in TIMES:
            frame(out, t, f"{key}_{vk}", hdr, False)
        o = w.probe(out)
        item["variants"].append({"key": vk, "label": vlabel, "path": out,
                                 "gbh": os.path.getsize(out) / 1e9 / (float(o["format"]["duration"]) / 3600),
                                 "vmaf": whole_vmaf(out, src, v.get("width") or 0), "speed": dur / secs})
        print(f"{label} @ {vlabel}: {item['variants'][-1]['gbh']:.2f} GB/h, VMAF {item['variants'][-1]['vmaf']}")
    items.append(item)
json.dump({"built": time.strftime("%Y-%m-%d %H:%M"), "items": items}, open(os.path.join(PREV, "preview.json"), "w"), indent=1)
print("preview ready")
