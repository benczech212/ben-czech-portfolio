"""Re-encode worker: takes jobs from reencode_queue.json one at a time and writes each title to its final,
properly named place under MEDIA_ROOT (config.py). Original sources are only read, never changed or deleted; the
staged copy placed by stage_media.py is replaced only after the new file has passed every check.

  python dashboard/reencode_worker.py        (run it in its own window; only one copy can run)

Per job: encode to "<dest>.partial.mkv" -> check it (length, streams, full decode, VMAF on samples for re-encoded
video) -> rename to the final name. A job is never started twice: the queue holds each destination once, a job's
state is saved before and after every step, and a single-instance lock stops a second worker.
To stop after the current job, create an empty file named reencode_worker.stop next to this script.
"""
import ctypes, json, msvcrt, os, re, subprocess, sys, tempfile, threading, time, webbrowser
from reencode_common import (ACTIVE, BAD_AUDIO, CAP_GBH, FFMPEG, FFPROBE, HERE, MAX_AUDIO_TRACKS, VIDEO_CQ, load,
                             queue_lock, res_tier, save)
import config

PIDLOCK = os.path.join(HERE, "reencode_worker.pid")
STOP = os.path.join(HERE, "reencode_worker.stop")
VMAF_MIN = 70.0          # catches broken encodes; grain smoothing on film scores ~75-80 and looks fine (preview 2026-09-30)
STAGE_STATUS = os.path.join(HERE, "stage_status.json")
DASHBOARD_QUEUE = f"http://localhost:{config.PORT}/#queue"


# ---------------- idle scheduling ----------------
class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds():
    """Seconds since the last keyboard/mouse input anywhere on this PC."""
    li = _LASTINPUTINFO(); li.cbSize = ctypes.sizeof(li)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0


def settings():
    st = load().get("settings", {})
    return st.get("mode", "idle"), float(st.get("idle_minutes", 10))


def countdown(seconds=60):
    """A small always-on-top window counting down, beeping every 10 s. Any mouse/keyboard input cancels it.
    Returns True to go ahead, False if you came back."""
    import tkinter as tk
    import winsound
    root = tk.Tk()
    root.title("Re-encoding")
    root.attributes("-topmost", True)
    root.geometry("+60+60")
    msg = tk.Label(root, font=("Segoe UI", 14), padx=24, pady=16, justify="left")
    msg.pack()
    btns = tk.Frame(root); btns.pack(pady=(0, 14))
    result = {"go": False}
    t0 = time.time()

    def finish(go):
        result["go"] = go
        root.destroy()
    tk.Button(btns, text="Start now", command=lambda: finish(True)).pack(side="left", padx=6)
    tk.Button(btns, text="Not now", command=lambda: finish(False)).pack(side="left", padx=6)
    last_beep = [-10]

    def tick():
        el = time.time() - t0
        if el > 1.5 and idle_seconds() < 1.0:  # you touched the mouse or keyboard
            return finish(False)
        left = int(seconds - el + 0.999)
        if left <= 0:
            return finish(True)
        msg.config(text=f"The PC has been idle, so video re-encoding starts in {left} s.\n"
                        "Move the mouse or press a key to cancel.")
        if el - last_beep[0] >= 10:
            last_beep[0] = el
            winsound.Beep(880, 250)
        root.after(250, tick)
    root.after(0, tick)
    root.mainloop()
    return result["go"]


def _suspend(pid, on):
    """Pause or resume a process (the running ffmpeg) without losing its progress."""
    h = ctypes.windll.kernel32.OpenProcess(0x0800, False, pid)  # PROCESS_SUSPEND_RESUME
    if h:
        (ctypes.windll.ntdll.NtSuspendProcess if on else ctypes.windll.ntdll.NtResumeProcess)(h)
        ctypes.windll.kernel32.CloseHandle(h)


def wait_until_idle_and_confirm(reason):
    """Block until the PC has been idle long enough and the countdown ran out. Returns False if asked to stop."""
    while not os.path.exists(STOP):
        mode, mins = settings()
        if mode == "always":
            return True
        if idle_seconds() >= mins * 60:
            set_worker(state="countdown", reason="counting down (the PC is idle)")
            if countdown():
                return True
        set_worker(state="waiting", reason=f"{reason}: waiting until the PC has been idle for {mins:.0f} min")
        time.sleep(15)
    return False


# ---------------- which copy to read ----------------
def staging_finished():
    try:
        return json.load(open(STAGE_STATUS, encoding="utf-8")).get("state") == "finished"
    except (OSError, ValueError):
        return False


_placed = {"at": 0, "set": set()}


def placed_copies():
    """Destinations the copy step has finished, read from its manifest and progress (no NAS lookups), refreshed each minute."""
    if time.time() - _placed["at"] > 60:
        try:
            man = json.load(open(os.path.join(HERE, "stage_manifest.json"), encoding="utf-8"))["entries"]
            st = json.load(open(STAGE_STATUS, encoding="utf-8"))
            n = len(man) if st.get("state") == "finished" else max(0, int(st.get("n", 1)) - 1)
            _placed["set"] = {os.path.normcase(e["dest"]) for e in man[:n]}
        except (OSError, ValueError, KeyError):
            _placed["set"] = set()
        _placed["at"] = time.time()
    return _placed["set"]


def sources_for(job):
    """Read the copy already placed under MEDIA_ROOT when it is complete; before that, wait for the copy (unless the
    copy step is finished or doesn't include this file, then use the original). Returns a list, or None = not yet."""
    staged = job.get("staged") or []
    placed = placed_copies()
    if staged and not all(os.path.normcase(x) in placed for x in staged):
        return job["src"] if staging_finished() else None  # copy not there yet
    if staged and all(os.path.exists(x) for x in staged):
        sizes_ok = all(not os.path.exists(o) or os.path.getsize(o) == os.path.getsize(x) for o, x in zip(job["src"], staged))
        if sizes_ok:
            return staged
    if not staged or staging_finished():
        return job["src"]
    return None
LOG = os.path.join(HERE, "reencode_worker.log")


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def set_worker(**kv):
    with queue_lock():
        q = load(); q.setdefault("worker", {}).update(kv, pid=os.getpid(), seen=time.time()); save(q)


def update_job(jid, **kv):
    with queue_lock():
        q = load()
        for j in q["jobs"]:
            if j["id"] == jid:
                j.update(kv)
        q.setdefault("worker", {}).update(pid=os.getpid(), seen=time.time())
        save(q)


def nvenc_ok():
    r = run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x144:d=0.1", "-c:v", "hevc_nvenc", "-f", "null", "-"])
    return r.returncode == 0, (r.stderr.strip().splitlines() or [""])[0][:200]


def probe(path):
    out = run([FFPROBE, "-v", "error", "-show_entries",
               "stream=index,codec_type,codec_name,channels,width,height,color_transfer,bit_rate:stream_tags=language"
               ":format=duration",
               "-of", "json", path]).stdout
    return json.loads(out or "{}")


def pick_audio(streams):
    """Keep the main (first) audio track plus English tracks, at most MAX_AUDIO_TRACKS; returns the streams kept."""
    auds = [x for x in streams if x["codec_type"] == "audio"]
    keep = auds[:1] + [x for x in auds[1:] if (x.get("tags", {}).get("language") or "").lower() in ("eng", "en")]
    return keep[:MAX_AUDIO_TRACKS]


def audio_kbps(a):
    """Bitrate this track will have in the output."""
    ch = a.get("channels") or 2
    if a.get("codec_name") in BAD_AUDIO:
        return 640 if ch > 2 else 224
    br = int(a.get("bit_rate") or 0) // 1000
    return br or (640 if ch > 2 else 192)


def build_cmd(job, info, inputs, partial, gpu=True):
    streams = info.get("streams", [])
    v = next((x for x in streams if x["codec_type"] == "video"), {})
    audio = pick_audio(streams)
    dur = job.get("src_duration") or float(info.get("format", {}).get("duration") or 0)
    cmd = [FFMPEG, "-v", "error", "-y", "-nostats", "-progress", "pipe:1"]
    tier = res_tier(v.get("width") or 0)
    big = tier in ("1080p", "4K")
    if job["kind"] == "video" and gpu:
        cmd += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    cmd += inputs
    cmd += ["-map", "0:v:0"] + [x for a in audio for x in ("-map", f"0:{a['index']}")] +            ["-map", "0:s?", "-map", "0:t?", "-map_metadata", "0", "-map_chapters", "0", "-max_muxing_queue_size", "4096"]
    if job["kind"] == "video":
        # never above 1080p: 1920 wide or 1080 tall, whichever limits first, keeping the shape
        down = (v.get("width") or 0) > 1920 or (v.get("height") or 0) > 1080
        if gpu:
            cmd += ["-vf", ("scale_cuda=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:format=p010le"
                            if down else "scale_cuda=format=p010le")]
        else:
            if down:
                cmd += ["-vf", "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:flags=lanczos"]
            cmd += ["-pix_fmt", "p010le"]
        common = ["-c:v", "hevc_nvenc", "-preset", "p7", "-tune", "hq", "-spatial-aq", "1", "-temporal-aq", "1",
                  "-rc-lookahead", "32", "-bf", "4", "-profile:v", "main10"]
        if big:
            # fixed average so the whole file stays under the cap: 2.5 GB/h = ~5,556 kbps for everything
            total = int(CAP_GBH * 1e9 * 8 / 3600 / 1000 * 0.97)
            vk = max(1500, total - sum(audio_kbps(a) for a in audio) - 30)
            cmd += common + ["-rc", "vbr", "-multipass", "fullres", "-b:v", f"{vk}k", "-maxrate", f"{vk * 2}k",
                             "-bufsize", f"{vk * 4}k"]
        else:
            cq, maxrate = VIDEO_CQ[tier]
            cmd += common + ["-rc", "vbr", "-cq", str(cq), "-b:v", "0", "-maxrate", maxrate,
                             "-bufsize", str(int(maxrate[:-1]) * 2) + "M"]
        if v.get("color_transfer") in ("smpte2084", "arib-std-b67"):  # keep HDR information
            cmd += ["-color_primaries", "bt2020", "-color_trc", v["color_transfer"], "-colorspace", "bt2020nc"]
    else:
        cmd += ["-c:v", "copy"]
    for i, a in enumerate(audio):
        if a.get("codec_name") in BAD_AUDIO:
            ch = a.get("channels") or 2
            cmd += [f"-c:a:{i}", "eac3", f"-b:a:{i}", "640k" if ch > 2 else "224k", f"-ac:a:{i}", "6" if ch > 2 else "2"]
        else:
            cmd += [f"-c:a:{i}", "copy"]
    for i, x in enumerate(x for x in streams if x["codec_type"] == "subtitle"):
        cmd += [f"-c:s:{i}", "srt" if x.get("codec_name") == "mov_text" else "copy"]
    return cmd + ["-f", "matroska", partial]


def vmaf_at(out, inputs, t, secs, scale, shift):
    """VMAF of `secs` seconds from time t; shift > 0 skips frames of the original, < 0 of the new file (seeks in the
    two files can land a few frames apart, which would make a good encode score badly)."""
    d_trim = f"trim=start_frame={-shift}," if shift < 0 else ""
    r_trim = f"trim=start_frame={shift}," if shift > 0 else ""
    # "-v info": ffmpeg prints the VMAF score at info level
    r = run([FFMPEG, "-v", "info", "-nostats", "-ss", f"{t:.1f}", "-t", str(secs), "-i", out,
             "-ss", f"{t:.1f}", "-t", str(secs)] + inputs +
            ["-lavfi", f"[0:v]{d_trim}{scale}format=yuv420p10le,setpts=PTS-STARTPTS[d];"
                       f"[1:v]{r_trim}{scale}format=yuv420p10le,setpts=PTS-STARTPTS[r];[d][r]libvmaf=n_threads=8:n_subsample=2",
             "-f", "null", "-"])
    m = re.search(r"VMAF score: ([\d.]+)", r.stderr)
    return float(m.group(1)) if m else None


def vmaf_samples(out, inputs, dur, width, secs=10):
    """Lowest VMAF over three samples (20/50/80% in); each sample uses the best of a few frame alignments."""
    scale = "scale=1920:-2:flags=bicubic," if width > 1920 else ""
    scores = []
    for frac in (0.2, 0.5, 0.8):
        t = max(0, dur * frac)
        best = None
        for shift in (0, 1, -1, 2, -2, 3, -3):
            v = vmaf_at(out, inputs, t, secs, scale, shift)
            if v is not None and (best is None or v > best):
                best = v
            if best is not None and best >= 93:  # clearly aligned and good; no need to try more offsets
                break
        if best is not None:
            scores.append(best)
    return min(scores) if scores else None


def process(job, src):
    jid, dest = job["id"], job["dest"]
    staged = {os.path.normcase(x) for x in job.get("staged") or []}
    missing = [p for p in src if not os.path.exists(p)]
    if missing:
        return update_job(jid, status="failed", error=f"source missing: {missing[0]}", finished=time.time())
    if os.path.exists(dest) and os.path.normcase(dest) not in staged:
        return update_job(jid, status="done", note="already at the destination; nothing to do", finished=time.time(),
                          out_bytes=os.path.getsize(dest))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    partial = dest[:-4] + ".partial.mkv"
    listfile = None
    if len(src) > 1:  # a copy split into parts: join them in order
        fd, listfile = tempfile.mkstemp(suffix=".txt", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for p in src:
                f.write("file '" + p.replace("'", "'\\''") + "'\n")
        inputs = ["-f", "concat", "-safe", "0", "-i", listfile]
    else:
        inputs = ["-i", src[0]]
    info = probe(src[0])
    total = sum(float(probe(p).get("format", {}).get("duration") or 0) for p in src) if len(src) > 1 else \
        float(info.get("format", {}).get("duration") or 0)
    width = next((s.get("width") or 0 for s in info.get("streams", []) if s["codec_type"] == "video"), 0)
    n_audio_src = len(pick_audio(info.get("streams", [])))
    update_job(jid, status="running", started=time.time(), progress=0.0, error=None, src_duration=total)
    log(f"start {job['title']} [{job['kind']}] from {src[0]} -> {dest}")

    def encode(cmd):
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        last, t0 = 0, time.time()
        stop_watch = threading.Event()

        def control():
            return load().get("control", {})

        def clear_control():
            with queue_lock():
                qq = load(); qq.pop("control", None); save(qq)

        def watch():
            """Pause the running ffmpeg when you use the PC (idle mode) or press Pause on the dashboard.
            It resumes after the next idle stretch + countdown, or at once with the dashboard's "Resume now"."""
            while not stop_watch.wait(2):
                mode, mins = settings()
                asked = control().get("pause")
                if p.poll() is None and (asked or (mode == "idle" and idle_seconds() < 3)):
                    why = "paused from the dashboard" if asked else "paused because you're using the PC"
                    _suspend(p.pid, True)
                    update_job(jid, paused=True)
                    set_worker(state="paused", reason=f"{why}; resumes after the next idle countdown")
                    log(f"{why}: {job['title']}")
                    if asked:
                        clear_control()
                    while not stop_watch.is_set():
                        mode, mins = settings()
                        if control().get("resume"):
                            clear_control(); break
                        # after a dashboard pause, even "Run anytime" waits for the idle countdown (your normal schedule)
                        if (mode == "always" and not asked) or (idle_seconds() >= mins * 60 and countdown()):
                            break
                        time.sleep(5)
                    _suspend(p.pid, False)
                    update_job(jid, paused=False)
                    set_worker(state="working", reason="")
                    log(f"resumed {job['title']}")
        threading.Thread(target=watch, daemon=True).start()
        for line in p.stdout:
            if line.startswith("out_time_us=") and total:
                try:
                    done = int(line.split("=")[1]) / 1e6
                except ValueError:
                    continue
                if time.time() - last > 5:
                    last = time.time()
                    update_job(jid, progress=min(done / total, 0.999), speed=done / max(1, time.time() - t0))
            if os.path.exists(STOP + ".now"):
                p.kill()
        err = p.stderr.read()
        p.wait()
        stop_watch.set()
        return p.returncode, err

    job["src_duration"] = total
    rc, err = encode(build_cmd(job, info, inputs, partial, gpu=True))
    if rc and job["kind"] == "video":  # GPU decode/scale problem: retry with CPU decoding and scaling
        log(f"GPU pipeline failed ({err.strip()[-120:]}); retrying on the CPU")
        rc, err = encode(build_cmd(job, info, inputs, partial, gpu=False))
    if rc:
        _rm(partial); _rm(listfile)
        return update_job(jid, status="failed", error=(err.strip()[-300:] or f"ffmpeg exit {rc}"), finished=time.time())

    update_job(jid, status="verifying", progress=1.0)
    o = probe(partial)
    odur = float(o.get("format", {}).get("duration") or 0)
    problems = []
    if total and abs(odur - total) > max(2.0, total * 0.005):
        problems.append(f"length {odur:.0f}s vs {total:.0f}s")
    if not any(s["codec_type"] == "video" for s in o.get("streams", [])):
        problems.append("no video")
    if sum(1 for s in o.get("streams", []) if s["codec_type"] == "audio") != n_audio_src:
        problems.append("audio track count changed")
    ov = next((x for x in o.get("streams", []) if x["codec_type"] == "video"), {})
    if job["kind"] == "video" and ((ov.get("width") or 0) > 1920 or (ov.get("height") or 0) > 1080):
        problems.append(f"still {ov.get('width')}x{ov.get('height')}")
    gbh = os.path.getsize(partial) / 1e9 / (odur / 3600) if odur else 0
    if job["kind"] == "video" and gbh > CAP_GBH * 1.03:
        problems.append(f"{gbh:.2f} GB per hour, over the {CAP_GBH} cap")
    dec = run([FFMPEG, "-v", "error", "-hwaccel", "cuda", "-i", partial, "-map", "0:v:0", "-f", "null", "-"])
    if dec.returncode or dec.stderr.strip():
        problems.append("decode errors: " + dec.stderr.strip()[:150])
    vmaf = None
    if job["kind"] == "video" and not problems:
        vmaf = vmaf_samples(partial, inputs, total, width)
        if vmaf is None:
            problems.append("quality check (VMAF) couldn't be measured")
        elif vmaf < VMAF_MIN:
            problems.append(f"VMAF {vmaf:.1f} below {VMAF_MIN}")
    _rm(listfile)
    if problems:
        _rm(partial)
        return update_job(jid, status="failed", error="; ".join(problems), finished=time.time(), vmaf=vmaf)
    os.replace(partial, dest)
    for x in job.get("staged") or []:  # the placed copy it replaced (the original source is untouched)
        if os.path.normcase(x) != os.path.normcase(dest):
            _rm(x)
    update_job(jid, status="done", finished=time.time(), out_bytes=os.path.getsize(dest), vmaf=vmaf, progress=1.0)
    log(f"done  {job['title']}: {job['src_bytes'] / 1e9:.1f} GB -> {os.path.getsize(dest) / 1e9:.1f} GB"
        + (f", VMAF {vmaf:.1f}" if vmaf else ""))


def _rm(p):
    try:
        if p and os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


def main():
    fd = os.open(PIDLOCK, os.O_RDWR | os.O_CREAT)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        print("another worker is already running"); sys.exit(0)
    for f in (STOP, STOP + ".now"):
        _rm(f)
    # jobs left "running" by a worker that died: back to the queue, partial output removed
    with queue_lock():
        q = load()
        for j in q["jobs"]:
            if j["status"] in ("running", "verifying"):
                _rm(j["dest"][:-4] + ".partial.mkv"); j.update(status="queued", progress=0)
        save(q)
    log("worker started")
    opened = False
    while not os.path.exists(STOP):
        ok, why = nvenc_ok()
        if not ok:
            set_worker(state="blocked", reason="GPU encoder unavailable: " + why)
            time.sleep(600); continue
        q = load()
        pick = next(((j, sources_for(j)) for j in q["jobs"] if j["status"] == "queued" and sources_for(j)), None)
        if not pick:
            waiting = any(j["status"] == "queued" for j in q["jobs"])
            set_worker(state="idle", reason="waiting for titles to be copied into the media library" if waiting else "")
            opened = False
            time.sleep(60); continue
        if not wait_until_idle_and_confirm("jobs ready"):
            break
        if not opened:  # show progress once per session
            webbrowser.open(DASHBOARD_QUEUE); opened = True
        with queue_lock():  # claim the job (another worker can't exist, but the queue may have changed)
            q = load()
            job = next((j for j in q["jobs"] if j["id"] == pick[0]["id"] and j["status"] == "queued"), None)
            if job:
                job["status"] = "running"; save(q)
        if not job:
            continue
        set_worker(state="working", reason="", current=job["id"])
        try:
            process(job, sources_for(job) or job["src"])
        except Exception as e:  # keep the worker alive; the job is marked failed with the reason
            log(f"error on {job['title']}: {e!r}")
            update_job(job["id"], status="failed", error=repr(e)[:300], finished=time.time())
        mode, mins = settings()
        if mode == "idle" and idle_seconds() < 60:
            opened = False  # you're back: the next job waits for the next idle stretch and countdown
    set_worker(state="stopped", reason="stopped on request")
    log("worker stopped")


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:  # leave the reason in the log (the window may be gone); the next start resets the job
        if not isinstance(e, SystemExit):
            import traceback
            log("worker crashed: " + traceback.format_exc().strip().replace("\n", " | ")[-600:])
        raise
