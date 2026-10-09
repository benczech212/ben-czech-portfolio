"""Put every kept Plex title at its final name under MEDIA_ROOT (config.py) before any re-encoding.

  python dashboard/stage_media.py plan    build stage_manifest.json and print the totals (changes nothing)
  python dashboard/stage_media.py run     copy/move everything in the manifest (resumable; run in its own window)

- Files from the PC's drives are COPIED (originals stay until everything is checked).
- Files already on the NAS (e.g. rescued from the failing drive into RESCUE_ROOT) are MOVED: a rename on the NAS,
  instant, no extra space.
- Names follow the standard (docs/media_pipeline.md) but keep the source's file type until the re-encode makes it .mkv.
- Each copy is written as "<name>.partial", size-checked, then renamed. Existing complete files are skipped.
- Titles without a proper name yet (unidentified movies, episodes whose numbers disagree with the file name) are
  listed and skipped.
"""
import json, os, shutil, sys, time
import server  # plan helpers: library choice, TV/movie targets, decisions
import config

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "stage_manifest.json")
STATUS = os.path.join(HERE, "stage_status.json")
SUBS = (".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt")


from reencode_common import staged_names  # shared with the re-encode queue


def same_volume(a, b):
    """Both paths on the same drive letter or network share (a rename there is instant)."""
    return os.path.normcase(os.path.splitdrive(a)[0]) == os.path.normcase(os.path.splitdrive(b)[0])


def plan():
    data = server.plex_review(); F = data["files"]
    dec = server.load_decisions().get("plex", {})
    entries, skipped = [], {"no proper name yet (fix the match in Plex first)": [],
                            "episode number in Plex disagrees with the file name": []}
    for m in data["movies"]:
        key = "m:%d" % m["id"]
        if dec.get(key) != "keep":
            continue
        if server.library_of(key, dec) == "tv":
            show = (dec.get("showname:" + key) or m.get("show_name") or m["title"]).translate(server.SAFE_NAME).strip()
            for v, dest in server.tv_targets(m, show):
                srcs = [F[i] for i in v["src"]]
                entries += [{"key": key, "title": f"{show}: {os.path.basename(dest)}", "src": s, "dest": d}
                            for s, d in zip(srcs, staged_names(dest, srcs))]
            continue
        if not m.get("dest"):
            skipped["no proper name yet (fix the match in Plex first)"].append(m["title"]); continue
        pick = dec.get("pick:" + key)
        v = next((x for x in m["versions"] if str(x["media"]) == pick), None) if pick and pick != "all" else None
        v = v or m["versions"][0]
        srcs = [F[i] for i in v["src"]]
        entries += [{"key": key, "title": f"{m['title']} ({m['year']})", "src": s, "dest": d}
                    for s, d in zip(srcs, staged_names(m["dest"], srcs))]
    for sh in data["shows"]:
        key = "s:%d" % sh["id"]
        if dec.get(key) != "keep":
            continue
        if server.library_of(key, dec) == "movies":
            for row, dest in server.movie_targets(sh):
                srcs = [F[i] for i in row[7]]
                entries += [{"key": key, "title": os.path.basename(dest), "src": s, "dest": d}
                            for s, d in zip(srcs, staged_names(dest, srcs))]
            continue
        root = sh["dest_root"]; base = os.path.basename(root)
        for label, title, fi, size, nv, ekind, est, src, season, ep, agree in sh["files"]:
            if not agree:
                skipped["episode number in Plex disagrees with the file name"].append(f"{sh['title']} {label}"); continue
            et = (title or "").translate(server.SAFE_NAME).strip()
            dest = os.path.join(root, f"Season {season:02d}", f"{base} - s{season:02d}e{ep:02d}" + (f" - {et}" if et else "") + ".mkv")
            srcs = [F[i] for i in src]
            entries += [{"key": key, "title": f"{sh['title']} {label}", "src": s, "dest": d}
                        for s, d in zip(srcs, staged_names(dest, srcs))]
    # one destination per entry; two sources for one name would overwrite each other
    seen, clash = {}, []
    for e in entries:
        k = e["dest"].lower()
        if k in seen:
            clash.append(e); continue
        seen[k] = e
    entries = list(seen.values())
    for e in entries:
        try:
            e["size"] = os.path.getsize(e["src"])
        except OSError:
            e["size"] = 0; e["missing"] = True
        e["mode"] = "move" if same_volume(e["src"], config.MEDIA_ROOT) else "copy"
    json.dump({"built": time.strftime("%Y-%m-%d %H:%M"), "entries": entries, "skipped": skipped,
               "clashes": [c["src"] for c in clash]}, open(MANIFEST, "w", encoding="utf-8"), indent=0)
    cp = [e for e in entries if e["mode"] == "copy"]; mv = [e for e in entries if e["mode"] == "move"]
    print(f"{len(entries)} files: copy {len(cp)} ({sum(e['size'] for e in cp) / 1e12:.2f} TB from the PC), "
          f"move {len(mv)} ({sum(e['size'] for e in mv) / 1e9:.0f} GB already on the NAS); "
          f"missing sources: {sum(1 for e in entries if e.get('missing'))}; name clashes: {len(clash)}")
    for why, lst in skipped.items():
        print(f"skipped, {why}: {len(lst)}", ("e.g. " + "; ".join(lst[:5])) if lst else "")


def write_status(**kv):
    """Progress for the dashboard. The dashboard may be reading the file at that moment (Windows then refuses the
    replace), so retry briefly and otherwise skip this update; progress must never stop the copy."""
    tmp = STATUS + ".tmp"
    for _ in range(10):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(kv, f)
            os.replace(tmp, STATUS)
            return
        except OSError:
            time.sleep(0.2)


def sidecars(src, dest):
    """External subtitle files next to the source, renamed to match (Movie.en.srt -> Title (Year).en.srt)."""
    d, stem = os.path.dirname(src), os.path.splitext(os.path.basename(src))[0]
    nstem = os.path.splitext(dest)[0]
    try:
        names = os.listdir(d)
    except OSError:
        return []
    return [(os.path.join(d, n), nstem + n[len(stem):]) for n in names
            if n.lower().startswith(stem.lower()) and os.path.splitext(n)[1].lower() in SUBS]


def encoded_already():
    """Staged copies the re-encode worker has used (or is using). After a successful encode it removes the staged copy,
    so without this a second run would copy the original again - and for a single .mkv, whose staged name is the final
    name, would replace the finished encode with it."""
    import reencode_common as rq
    return {x.lower() for j in rq.load()["jobs"] if j["status"] in ("done", "running", "verifying")
            for x in j.get("staged") or []}


def wait_for_drives(src, dest, n, of, done_b, total, t0, failed):
    """The NAS share sometimes drops off the network for a moment; wait for both ends instead of failing."""
    roots = {os.path.splitdrive(src)[0] + "\\", os.path.splitdrive(dest)[0] + "\\"}
    while not all(os.path.isdir(r) for r in roots):
        write_status(state="waiting", n=n, of=of, done_bytes=done_b, total_bytes=total,
                     current="waiting for " + ", ".join(r for r in roots if not os.path.isdir(r)) + " to come back",
                     started=t0, updated=time.time(), failed=len(failed))
        time.sleep(30)


def run():
    man = json.load(open(MANIFEST, encoding="utf-8"))
    entries = [e for e in man["entries"] if not e.get("missing")]
    total = sum(e["size"] for e in entries)
    done_b, t0, moved, copied, failed = 0, time.time(), 0, 0, []
    finished, fin_at = encoded_already(), time.time()
    for n, e in enumerate(entries, 1):
        src, dest, size = e["src"], e["dest"], e["size"]
        wait_for_drives(src, dest, n, len(entries), done_b, total, t0, failed)
        if os.path.exists(dest) and os.path.getsize(dest) == size:
            done_b += size; continue
        if time.time() - fin_at > 60:
            finished, fin_at = encoded_already(), time.time()
        if dest.lower() in finished:  # the worker already turned this copy into the final file and removed it
            done_b += size; continue
        write_status(state="running", n=n, of=len(entries), done_bytes=done_b, total_bytes=total, current=e["title"],
                     started=t0, updated=time.time(), failed=len(failed))
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if e["mode"] == "move":
                os.replace(src, dest); moved += 1
            else:
                part = dest + ".partial"
                with open(src, "rb") as fi, open(part, "wb") as fo:
                    last = time.time()
                    while True:
                        b = fi.read(16 << 20)
                        if not b:
                            break
                        fo.write(b); done_b += len(b)
                        if time.time() - last > 3:
                            last = time.time()
                            write_status(state="running", n=n, of=len(entries), done_bytes=done_b, total_bytes=total,
                                         current=e["title"], started=t0, updated=last, failed=len(failed))
                done_b -= size  # counted again below once the file is confirmed
                if os.path.getsize(part) != size:
                    raise OSError(f"size mismatch {os.path.getsize(part)} vs {size}")
                os.replace(part, dest); copied += 1
            done_b += size
            for s, d in sidecars(src if e["mode"] == "copy" else dest, dest):
                if not os.path.exists(d) and os.path.exists(s):
                    shutil.copyfile(s, d)
        except OSError as ex:
            failed.append((src, str(ex)))
            try:
                if os.path.exists(dest + ".partial"):
                    os.remove(dest + ".partial")
            except OSError:
                pass
    write_status(state="finished", n=len(entries), of=len(entries), done_bytes=done_b, total_bytes=total, current="",
                 started=t0, updated=time.time(), failed=len(failed), failures=failed[:50], copied=copied, moved=moved)
    print(f"done: copied {copied}, moved {moved}, failed {len(failed)}")


if __name__ == "__main__":
    {"plan": plan, "run": run}[sys.argv[1] if len(sys.argv) > 1 else "plan"]()
