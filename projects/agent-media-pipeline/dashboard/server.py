"""Local dashboard: media library re-encoding and naming, Google Drive uploads, NAS space, backup plan.

Run:  python dashboard/server.py      then open http://localhost:8765 (port: DASHBOARD_PORT in config.py)
Mostly read-only: it reads logs/state and runs status queries (rclone size, NAS share reads, tasklist). Changes happen
only from your clicks (keep/drop marks, re-encode queue controls, library renames) and from the naming check's
automatic fixes (media_names.py: confident renames only, logged and undoable).

Security: listens on 127.0.0.1 only; every request must carry a localhost Host header (blocks DNS rebinding), a
cross-origin Origin is refused, and every POST needs Content-Type: application/json plus the X-Dashboard: 1 header
(a custom header forces a CORS preflight, which this server never grants, so other sites can't post here).
Files are only ever chosen by index/id from server-side lists, never by a path from the browser.
"""
import ctypes, json, os, re, shutil, subprocess, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import config

HERE = os.path.dirname(os.path.abspath(__file__))
RCLONE = config.RCLONE
PLAN = os.path.join(HERE, "plan.json")              # optional: {"updated", "alerts": [{level, text}], "stages": [{name, status, note}]}
DECISIONS = os.path.join(HERE, "decisions.json")  # your marks from the Plex tab
WORKER_LOG = os.path.join(HERE, "reencode_worker.log")
NAS_DATA = config.NAS_DATA
NAS_RECYCLE = config.NAS_RECYCLE
CLIENT_LIB = config.CLIENT_LIBRARY
DXV_QUEUE, DXV_LOG = config.DXV_QUEUE, config.DXV_LOG   # written by the DXV conversion step (not part of this copy)
# logs of the two upload containers on the NAS (nas/docker/*/compose.yaml), read over the network share
UPLOAD_LOGS = {"library": config.UPLOAD_LOG, "kopia": config.OFFSITE_LOG}
PORT = config.PORT
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
MAX_BODY = 1 << 20

state = {"started": time.time()}
lock = threading.Lock()


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=0x08000000)
        return r.returncode, r.stdout
    except Exception as e:
        return -1, str(e)


def ram():
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS(); m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return {"total_gb": m.ullTotalPhys / 2**30, "avail_gb": m.ullAvailPhys / 2**30, "load": m.dwMemoryLoad}


def upload_log(path):
    """Last lines of an rclone loop's log on the NAS and whether its most recent attempt failed."""
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - 20000))
            lines = f.read().decode("utf-8", errors="replace").splitlines()[1:]
    except OSError as e:
        return {"error": f"log not readable ({e.strerror or e})"}
    lines = [l for l in lines if l.strip()]
    last_ok = next((l for l in reversed(lines) if "PASS COMPLETE" in l or "rc=0" in l or "Transferred:" in l), None)
    last_bad = next((l for l in reversed(lines) if "CRITICAL" in l or "exited" in l or re.search(r"rc=[1-9]", l)), None)
    failing = bool(last_bad) and (not last_ok or lines.index(last_bad) > lines.index(last_ok))
    # two uploaders can share this log (the stuck container and the stand-in on the NAS host): recent upload
    # activity means it's working, whatever the other one logs. NAS clocks may be an hour off, hence 2 h.
    if failing and time.time() - os.path.getmtime(path) < 7200 and any(
            ("Copied" in l or "ETA" in l or "Set directory modification time" in l) for l in lines[-40:]):
        failing = False
    no_net = any("i/o timeout" in l or "no such host" in l for l in lines[-6:])
    return {"tail": [l[:200] for l in lines[-4:]], "failing": failing, "no_internet": failing and no_net,
            "updated": os.path.getmtime(path)}


def folder_bytes(root):
    total = 0
    for dp, _, fns in os.walk(root):
        for f in fns:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def dxv_status():
    """Client library -> DXV conversion (separate step, not included): counts by result, the last log line, and whether
    it's running."""
    import csv
    try:
        rows = [r for r in csv.DictReader(open(DXV_QUEUE, encoding="utf-8")) if r["queue"] == "ffmpeg_dxt1"]
    except OSError:
        return None
    c = {}
    for r in rows:
        k = r["status"].split(":")[0]
        c[k] = c.get(k, 0) + 1
    last = ""
    try:
        with open(DXV_LOG, "rb") as f:
            f.seek(max(0, os.path.getsize(DXV_LOG) - 4000))
            last = [l for l in f.read().decode("utf-8", errors="replace").splitlines() if l.startswith("[")][-1][:200]
        fresh = time.time() - os.path.getmtime(DXV_LOG) < 900
    except (OSError, IndexError):
        fresh = False
    todo = c.get("pending", 0) + c.get("anyway", 0) + c.get("encoded", 0)
    return {"counts": c, "total": len(rows), "todo": todo, "last": last,
            "running": bool(todo) and fresh and "ALL DONE" not in last}


def slow_refresh():
    """Checks that take a while, every few minutes: NAS space and recycle bin (over the share), the upload
    containers' logs, and (every 30 min) how much of the client library is on Google Drive."""
    last_drive = 0
    while True:
        out = {}
        try:
            du = shutil.disk_usage(NAS_DATA)
            out.update(nas_used_bytes=du.used, nas_free_bytes=du.free, nas_recycle_bytes=folder_bytes(NAS_RECYCLE),
                       nas_checked=time.time())
        except OSError:
            pass
        out["uploads"] = {k: upload_log(p) for k, p in UPLOAD_LOGS.items()}
        x = dxv_status()
        if x and x["todo"] != state.get("filter_todo"):   # a clip finished converting: let the uploader have it
            try:
                import client_filter
                client_filter.write(); out["filter_todo"] = x["todo"]
            except OSError:
                pass
        if time.time() - last_drive > 1800:
            n = b = 0
            for dp, _, fns in os.walk(CLIENT_LIB):
                for f in fns:
                    try:
                        b += os.path.getsize(os.path.join(dp, f)); n += 1
                    except OSError:
                        pass
            out.update(lib_files=n, lib_bytes=b)
            rc, txt = run([RCLONE, "size", config.CLIENT_LIBRARY_REMOTE, "--json"], 600)
            if rc == 0:
                try:
                    j = json.loads(txt)
                    out.update(drive_bytes=j.get("bytes", 0), drive_count=j.get("count", 0), drive_checked=time.time())
                    last_drive = time.time()
                except ValueError:
                    pass
        with lock:
            state.update(out)
        time.sleep(300)


def snapshot():
    with lock:
        s = dict(state)
    plan = json.load(open(PLAN, encoding="utf-8")) if os.path.exists(PLAN) else {}
    gb = lambda k: s[k] / 1e9 if k in s else None
    return {
        "now": time.time(), "ram": ram(), "plan": plan,
        "uploads": {**s.get("uploads", {}), "drive_gb": gb("drive_bytes"), "drive_files": s.get("drive_count"),
                    "drive_checked": s.get("drive_checked"),
                    "lib_gb": gb("lib_bytes"), "lib_files": s.get("lib_files")},
        "dxv": dxv_status(),
        "disk": {"nas_used_gb": gb("nas_used_bytes"), "nas_free_gb": gb("nas_free_bytes"),
                 "nas_recycle_gb": gb("nas_recycle_bytes"), "nas_checked": s.get("nas_checked")},
    }


POSTERS = os.path.join(HERE, "cache", "posters")


def poster(item_id):
    """Small poster JPEG for a Plex movie/show id, from the local Plex server, cached on disk. The token
    (config.plex_token) is used only server-side; it is never sent to the browser or written anywhere."""
    os.makedirs(POSTERS, exist_ok=True)
    path = os.path.join(POSTERS, f"{item_id}.jpg")
    if not os.path.exists(path):
        tok = config.plex_token()
        if not tok:
            return None
        import urllib.parse, urllib.request
        url = (config.PLEX_LOCAL_URL.rstrip("/") + "/photo/:/transcode?width=160&height=240&minSize=1&upscale=1&url="
               + urllib.parse.quote(f"/library/metadata/{item_id}/thumb") + "&X-Plex-Token=" + urllib.parse.quote(tok))
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                data = r.read()
        except OSError:
            return None
        with open(path, "wb") as f:
            f.write(data)
    return open(path, "rb").read()


_review_cache = {"mtime": 0, "data": None}


def plex_review():
    p = os.path.join(HERE, "plex_review.json")
    mt = os.path.getmtime(p)
    if mt != _review_cache["mtime"]:
        _review_cache.update(mtime=mt, data=json.load(open(p, encoding="utf-8")))
    return _review_cache["data"]


SAFE_NAME = str.maketrans({c: "" for c in '<>:"/\\|?*'})


def library_of(key, dec):
    """Which library a kept item goes to: 'movies' or 'tv' (your "Keep in TV / Keep in Movies" choice, else its own)."""
    return dec.get("lib:" + key) or ("movies" if key.startswith("m:") else "tv")


def tv_targets(m, show):
    """Movie item kept as TV: each real file (the film and any episode files attached to it) -> its TV name.
    Episode numbers come from the file names; files without one become Season 00 specials."""
    root = os.path.join(MEDIA_ROOT, "TV Shows", show)
    out, special = [], 0
    for v in m["versions"]:
        if v["relation"] in ("extra", "wrong", "same", "cut", "other"):
            continue  # trailers, other films, and extra copies of the film aren't kept
        ep = v.get("ep")
        if ep and ep[1]:
            season, e, title = ep[0] or 1, ep[1], ep[2]
        else:
            special += 1
            season, e, title = 0, special, m["title"]
        t = (title or "").translate(SAFE_NAME).strip()
        out.append((v, os.path.join(root, f"Season {season:02d}", f"{show} - s{season:02d}e{e:02d}" + (f" - {t}" if t else "") + ".mkv")))
    return out


def movie_targets(s):
    """Show item kept as Movies: a one-episode 'show' becomes one film; otherwise each episode becomes its own film."""
    t = s["title"].translate(SAFE_NAME).strip()
    yr = f" ({s['year']})" if s["year"] else ""
    out = []
    for row in s["files"]:
        label, title = row[0], (row[1] or "").translate(SAFE_NAME).strip()
        name = f"{t}{yr}" if len(s["files"]) == 1 else f"{t} - {title or label}{yr}"
        out.append((row, os.path.join(MEDIA_ROOT, "Movies", name + ".mkv")))
    return out


MEDIA_ROOT = config.MEDIA_ROOT


def plan_jobs(key, dec):
    """Re-encode jobs for one kept item ('m:<id>' movie, 's:<id>' show). Returns (jobs, message)."""
    import reencode_common as rq
    data = plex_review()
    F, now = data["files"], time.time()
    kind, iid = key.split(":", 1)
    lib = library_of(key, dec)
    if kind == "m" and lib == "tv":
        m = next((x for x in data["movies"] if str(x["id"]) == iid), None)
        if not m:
            return [], "not found"
        show = (dec.get("showname:" + key) or m.get("show_name") or m["title"]).translate(SAFE_NAME).strip()
        jobs = []
        for v, dest in tv_targets(m, show):
            if v.get("encode"):
                jobs.append({"id": rq.job_id(dest), "key": key, "imdb": None, "title": f"{show}: {os.path.basename(dest)[:-4]}",
                             "kind": v["encode"]["kind"], "reasons": v["encode"]["reasons"], "src": [F[i] for i in v["src"]],
                             "dest": dest, "src_bytes": v["size"], "est_bytes": v["encode"]["est_bytes"], "status": "queued", "added": now})
        return jobs, "" if jobs else "moved to TV as-is (nothing needs re-encoding)"
    if kind == "s" and lib == "movies":
        s = next((x for x in data["shows"] if str(x["id"]) == iid), None)
        if not s:
            return [], "not found"
        jobs = []
        for row, dest in movie_targets(s):
            label, title, fi, size, nv, ekind, est, src = row[:8]
            if ekind:
                jobs.append({"id": rq.job_id(dest), "key": key, "imdb": None, "title": os.path.basename(dest)[:-4], "kind": ekind,
                             "reasons": [], "src": [F[i] for i in src], "dest": dest, "src_bytes": size, "est_bytes": est,
                             "status": "queued", "added": now})
        return jobs, "" if jobs else "moved to Movies as-is (nothing needs re-encoding)"
    if kind == "m":
        m = next((x for x in data["movies"] if str(x["id"]) == iid), None)
        if not m:
            return [], "not found"
        pick = dec.get("pick:" + key)
        v = next((x for x in m["versions"] if str(x["media"]) == pick), None) if pick and pick != "all" else None
        v = v or m["versions"][0]
        if not v.get("encode"):
            return [], "already in a good format; it will be moved as-is"
        if not m.get("dest"):
            return [], "Plex couldn't identify it, so there's no proper name yet (fix the match in Plex first)"
        imdb = next((i[7:] for i in m.get("ids", []) if i.startswith("imdb://")), None)
        return [{"id": rq.job_id(m["dest"]), "key": key, "imdb": imdb, "title": f"{m['title']} ({m['year']})", "kind": v["encode"]["kind"],
                 "reasons": v["encode"]["reasons"], "src": [F[i] for i in v["src"]], "dest": m["dest"],
                 "src_bytes": v["size"], "est_bytes": v["encode"]["est_bytes"], "status": "queued", "added": now}], ""
    s = next((x for x in data["shows"] if str(x["id"]) == iid), None)
    if not s:
        return [], "not found"
    root = s["dest_root"]
    base = re.sub(r" \{tvdb-[^}]+\}$", "", os.path.basename(root))
    jobs, unsure = [], 0
    for label, title, fi, size, nv, ekind, est, src, season, ep, agree in s["files"]:
        if not ekind:
            continue
        if not agree:
            unsure += 1; continue
        et = (title or "").translate(str.maketrans({c: "" for c in '<>:"/\\|?*'})).strip()
        dest = os.path.join(root, f"Season {season:02d}", f"{base} - s{season:02d}e{ep:02d}" + (f" - {et}" if et else "") + ".mkv")
        jobs.append({"id": rq.job_id(dest), "key": key, "imdb": next((i[7:] for i in s.get("ids", []) if i.startswith("imdb://")), None),
                     "title": f"{s['title']} {label}", "kind": ekind, "reasons": [],
                     "src": [F[i] for i in src], "dest": dest, "src_bytes": size, "est_bytes": est, "status": "queued", "added": now})
    msg = f"{unsure} episode(s) skipped: Plex's episode number doesn't match the file name, check them first" if unsure else ""
    return jobs, msg


def sync_queue(keys, dec):
    """Queue kept items that need work (unless the re-encode box is unticked); take queued jobs off for anything else.
    Never adds a destination twice and never touches a job that is already running or finished."""
    import reencode_common as rq
    msgs = []
    with rq.queue_lock():
        q = rq.load()
        for key in keys:
            want = dec.get(key) == "keep" and dec.get("enc:" + key, "yes") != "no"
            # drop queued (not started) jobs of this item; they're re-added below if still wanted
            q["jobs"] = [j for j in q["jobs"] if not (j["key"] == key and j["status"] in ("queued", "cancelled"))]
            if not want:
                continue
            jobs, msg = plan_jobs(key, dec)
            if msg:
                msgs.append(msg)
            by_id = {j["id"]: j for j in q["jobs"]}
            active_src = {tuple(j["src"]) for j in q["jobs"] if j["status"] in rq.ACTIVE}
            for j in jobs:
                j["staged"] = rq.staged_names(j["dest"], j["src"])  # the copy placed under MEDIA_ROOT first
                old = by_id.get(j["id"])
                if old and old["status"] in ("running", "verifying", "done"):
                    continue  # already being made or made
                if tuple(j["src"]) in active_src:
                    continue  # the same source file is already on its way somewhere
                if old:
                    q["jobs"].remove(old)
                q["jobs"].append(j)
        rq.save(q)
    return msgs


_qframe = {"at": 0, "job": None}


def queue_frame():
    """JPEG of what the encoder is working on: a frame from its output a few seconds behind where it has got to
    (falls back to the source at the same point). Refreshed at most every 20 s."""
    import reencode_common as rq
    q = rq.load()
    job = next((j for j in q["jobs"] if j["status"] == "running"), None)
    out = os.path.join(HERE, "cache", "queue_frame.jpg")
    if not job:
        return None
    if time.time() - _qframe["at"] < 20 and _qframe["job"] == job["id"] and os.path.exists(out):
        return open(out, "rb").read()
    t = max(0.0, (job.get("progress") or 0) * (job.get("src_duration") or 0) - 4)
    partial = job["dest"][:-4] + ".partial.mkv"
    staged = [x for x in job.get("staged") or [] if os.path.exists(x)]
    tonemap = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
               "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    for path in [partial] + (staged[:1] or job["src"][:1]):
        if not os.path.exists(path):
            continue
        rc, info = run([rq.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_transfer",
                        "-of", "csv=p=0", path], 15)
        hdr = "smpte2084" in info or "arib-std-b67" in info
        rc, _ = run([rq.FFMPEG, "-v", "error", "-y", "-ss", f"{t:.1f}", "-i", path, "-frames:v", "1",
                     "-vf", (tonemap if hdr else "") + "scale=640:-2", "-q:v", "3", out], 25)
        if rc == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            _qframe.update(at=time.time(), job=job["id"])
            return open(out, "rb").read()
    return None


def queue_status():
    import reencode_common as rq
    q = rq.load()
    w = q.get("worker", {})
    alive = False
    if w.get("pid"):
        rc, out = run(["tasklist", "/FI", f"PID eq {w['pid']}", "/NH"], 10)
        alive = str(w["pid"]) in out
    q["worker"] = {**w, "alive": alive}
    try:
        q["stage"] = json.load(open(os.path.join(HERE, "stage_status.json"), encoding="utf-8"))
    except (OSError, ValueError):
        q["stage"] = None
    q.setdefault("settings", {"mode": "idle", "idle_minutes": 10})
    q["overall"] = queue_overview(q)
    return q


def load_decisions():
    try:
        return json.load(open(DECISIONS, encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"dxv": {}, "dupes": {}}


def review_file(lst, i):
    """Path of a file to play or show, chosen by index/id only (never a path from the browser)."""
    if lst == "plex":  # index into the file list built by build_plex_review.py
        if int(i) < 0:
            raise IndexError
        return plex_review()["files"][int(i)]
    if lst == "names":
        import media_names
        return media_names.path_of(i)
    raise KeyError(lst)


_pace = {"mtime": 0, "per_title": {}}


def encode_hours_per_title():
    """Hours the encoder actually spent on each title (start/resumed until paused/done/error), from its log."""
    try:
        mt = os.path.getmtime(WORKER_LOG)
    except OSError:
        return {}
    if mt == _pace["mtime"]:
        return _pace["per_title"]
    per, cur, since = {}, None, None
    stamp = lambda l: time.mktime(time.strptime(l[:19], "%Y-%m-%d %H:%M:%S"))
    for line in open(WORKER_LOG, encoding="utf-8", errors="replace"):
        if len(line) < 21 or not line[:4].isdigit():
            continue
        t, msg = stamp(line), line[20:].rstrip()
        if since is not None and cur and (msg.startswith(("paused", "done ", "error on", "worker started", "worker stopped", "start "))):
            per[cur] = per.get(cur, 0) + (t - since) / 3600
            since = None
        if msg.startswith("start "):
            cur, since = msg[6:].split(" [", 1)[0], t
        elif msg.startswith("resumed "):
            cur, since = msg[8:], t
        elif msg.startswith(("done ", "error on", "worker started", "worker stopped")):
            cur = None
    _pace.update(mtime=mt, per_title=per)
    return per


def queue_overview(q):
    """Whole-queue progress for the dashboard: titles and data done, what's saved, and how long is left at the
    pace measured so far (hours of actual encoding, which only happen while the PC is idle)."""
    jobs, now = q.get("jobs", []), time.time()
    by = lambda *s: [j for j in jobs if j["status"] in s]
    done, failed, queued, running = by("done"), by("failed"), by("queued", "cancelled"), by("running", "verifying")
    total = sum(j["src_bytes"] for j in jobs) or 1
    cur = sum(j["src_bytes"] * (j.get("progress") or 0) for j in running)
    done_b = sum(j["src_bytes"] for j in done)
    per = encode_hours_per_title()
    timed = [j for j in done if per.get(j["title"])]
    hours = sum(per[j["title"]] for j in timed)
    pace = sum(j["src_bytes"] for j in timed) / hours if hours > 0.2 else None   # source bytes per encoding hour
    left = total - done_b - sum(j["src_bytes"] for j in failed) - cur
    recent = [j for j in done if now - (j.get("finished") or 0) < 86400]
    return {"titles": len(jobs), "done": len(done), "failed": len(failed), "queued": len(queued), "running": len(running),
            "total_bytes": total, "done_bytes": done_b, "failed_bytes": sum(j["src_bytes"] for j in failed),
            "current_bytes": cur, "progress": (done_b + cur) / total,
            "saved_bytes": sum(j["src_bytes"] - (j.get("out_bytes") or j["src_bytes"]) for j in done),
            "final_bytes": sum(j.get("out_bytes") or j["src_bytes"] for j in done) + sum(j["est_bytes"] for j in jobs if j["status"] != "done"),
            "pace_bytes_h": pace, "eta_hours": left / pace if pace else None, "encode_hours": hours,
            "last24_titles": len(recent), "last24_bytes": sum(j["src_bytes"] for j in recent)}


def start_worker():
    """Open the re-encode worker in its own window (it only encodes while the PC is idle, unless set to Run anytime)."""
    import sys
    subprocess.Popen([sys.executable, os.path.join(HERE, "reencode_worker.py")], cwd=HERE, close_fds=True,
                     stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NEW_CONSOLE)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        # no framing by other sites (clickjacking), no MIME sniffing, no referrer leaking local paths
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def reply(self, body, ctype="application/json", cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def ok(self, **kv):
        self.reply(json.dumps({"ok": True, **kv}).encode())

    def fail(self, msg):
        self.reply(json.dumps({"ok": False, "error": str(msg)}).encode())

    def body(self):
        n = int(self.headers.get("Content-Length", 0))
        if not 0 <= n <= MAX_BODY:
            raise ValueError("request body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def allowed(self, post):
        """Localhost Host header, no foreign Origin, and for POSTs a JSON body plus the dashboard's own header.
        Sends 403 and returns False otherwise."""
        origin = self.headers.get("Origin")
        ok = self.headers.get("Host", "") in ALLOWED_HOSTS and (origin is None or origin in ALLOWED_ORIGINS)
        if ok and post:
            ok = (self.headers.get("Content-Type", "").split(";")[0].strip().lower() == "application/json"
                  and self.headers.get("X-Dashboard") == "1")
        if not ok:
            self.send_error(403)
        return ok

    def do_POST(self):
        if not self.allowed(post=True):
            return
        p = self.path
        if p == "/api/file":
            # {"list": "plex"|"names", "i": index or id, "act": "play"|"show"}: play opens the video in its default app;
            # show opens Explorer with it selected. The path comes from a server-side list, never from the browser.
            try:
                req = self.body()
                path = review_file(req["list"], req.get("i") if req.get("i") is not None else req.get("id"))
                if not path or not os.path.isfile(path):
                    return self.send_error(404)
                import media_names
                if os.path.splitext(path)[1].lower() not in media_names.VIDEO:
                    return self.send_error(400)   # only ever open video files
                if req.get("act") == "play":
                    os.startfile(path)
                else:
                    subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])
            except (ValueError, IndexError, KeyError, TypeError, OSError):
                return self.send_error(400)
            return self.ok()
        if p.startswith("/api/names/"):  # Library names tab; see media_names.py
            import media_names as mn
            try:
                req, act = self.body(), p.rsplit("/", 1)[1]
                if act == "scan":
                    st = mn.scan()
                    return self.ok(fixed=st.get("fixed_last_scan", 0), listed=len(st.get("issues", [])))
                if act == "fix":
                    return self.ok(to=mn.fix(req["id"], req.get("target")))
                if act == "ignore":
                    mn.ignore(req["id"]); return self.ok()
                if act == "delete":
                    mn.delete_duplicate(req["id"]); return self.ok()
                if act == "undo":
                    mn.undo(req["batch"]); return self.ok()
                if act == "settings":
                    mn.set_auto(bool(req["auto"])); return self.ok()
            except (ValueError, KeyError, TypeError) as e:
                return self.fail(e)
            except OSError as e:
                return self.fail(f"{e.strerror or e} ({getattr(e, 'filename', '') or ''})")
            return self.send_error(404)
        if p == "/api/queue/start":
            if queue_status()["worker"]["alive"]:
                return self.fail("the worker is already running")
            start_worker(); return self.ok()
        if p == "/api/queue/control":  # {"action": "pause"} or {"action": "resume"}; the worker acts within ~2 s
            import reencode_common as rq
            try:
                act = self.body()["action"]
                if act not in ("pause", "resume"):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                return self.send_error(400)
            with rq.queue_lock():
                qq = rq.load(); qq["control"] = {act: True, "at": time.time()}; rq.save(qq)
            return self.ok()
        if p == "/api/queue/settings":  # {"mode": "idle"|"always", "idle_minutes": n}
            import reencode_common as rq
            try:
                req = self.body()
                mode = req.get("mode"); mins = float(req.get("idle_minutes", 10))
                if mode not in ("idle", "always") or not 1 <= mins <= 240:
                    raise ValueError
            except (ValueError, TypeError):
                return self.send_error(400)
            with rq.queue_lock():
                qq = rq.load(); qq["settings"] = {"mode": mode, "idle_minutes": mins}; rq.save(qq)
            return self.ok()
        if p in ("/api/queue/retry", "/api/queue/remove"):
            # retry: a failed job goes back in line; remove: take a job that hasn't started off the queue
            import reencode_common as rq
            try:
                jid = self.body()["id"]
            except (ValueError, KeyError, TypeError):
                return self.send_error(400)
            with rq.queue_lock():
                qq = rq.load()
                for j in list(qq["jobs"]):
                    if j["id"] != jid:
                        continue
                    if p.endswith("retry") and j["status"] == "failed":
                        j.update(status="queued", error=None, progress=0)
                    elif p.endswith("remove") and j["status"] in ("queued", "failed", "cancelled"):
                        qq["jobs"].remove(j)
                rq.save(qq)
            return self.ok()
        if p != "/api/mark":  # {"list": "plex", "keys": [...], "decision": "..." or null to clear}
            return self.send_error(404)
        try:
            req = self.body()
            with lock:
                d = load_decisions()
                bucket = d.setdefault(req["list"], {})
                for k in req["keys"]:
                    if req.get("decision"):
                        bucket[k] = req["decision"]
                    else:
                        bucket.pop(k, None)
                tmp = DECISIONS + ".tmp"
                json.dump(d, open(tmp, "w", encoding="utf-8"), indent=1)
                os.replace(tmp, DECISIONS)
            msgs = []
            if req["list"] == "plex":  # keep the re-encode queue in step with keep/drop, version picks and the checkbox
                items = {re.sub(r"^(pick|enc|lib|showname):", "", k) for k in req["keys"]}
                msgs = sync_queue({k for k in items if k[:2] in ("m:", "s:")}, d.get("plex", {}))
        except (ValueError, KeyError, TypeError):
            return self.send_error(400)
        self.ok(queue=msgs)

    def do_GET(self):
        if not self.allowed(post=False):
            return
        p = self.path
        q = dict(x.split("=", 1) for x in p.split("?", 1)[-1].split("&") if "=" in x)
        if p.startswith("/api/queue/frame"):
            img = queue_frame()
            return self.reply(img, "image/jpeg") if img else self.send_error(404)
        if p.startswith("/api/queue"):
            return self.reply(json.dumps(queue_status()).encode())
        if p.startswith("/api/names"):
            import media_names as mn
            st = mn.load_state()
            st.pop("seen", None)
            st["ignored"] = len(st.get("ignored", {}))
            st["recent"] = mn.recent()
            st["root"] = mn.ROOT
            return self.reply(json.dumps(st).encode())
        if p.startswith("/api/poster"):
            try:
                img = poster(int(q.get("id", "")))
            except ValueError:
                return self.send_error(400)
            return self.reply(img, "image/jpeg", "max-age=86400") if img else self.send_error(404)
        if p.startswith("/api/plexlist"):
            data = json.load(open(os.path.join(HERE, "plex_review.json"), encoding="utf-8"))
            data["decisions"] = load_decisions().get("plex", {})
            data["media_root"] = MEDIA_ROOT
            return self.reply(json.dumps(data).encode())
        if p.startswith("/api/status"):
            return self.reply(json.dumps(snapshot()).encode())
        if p in ("/", "/index.html") or p.startswith("/#"):
            return self.reply(open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")
        self.send_error(404)


if __name__ == "__main__":
    import media_names
    threading.Thread(target=slow_refresh, daemon=True).start()
    threading.Thread(target=media_names.background, daemon=True).start()   # naming check: every 6 h
    print(f"Dashboard on http://localhost:{PORT} (local only)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
