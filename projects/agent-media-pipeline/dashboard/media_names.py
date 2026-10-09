"""Naming check for the NAS Plex libraries (MEDIA_ROOT\\Movies and MEDIA_ROOT\\TV Shows; MEDIA_ROOT is set in config.py).

Finds video files whose names don't follow the library standard (docs/media_pipeline.md) and works out the right
name from the NAS Plex's own match:
  Movies:  Movies\\Title (Year).ext                                edition " - Extended", split copies " - pt1"
  TV:      TV Shows\\Show (Year)\\Season 01\\Show (Year) - s01e01 - Episode Title.ext

A file is renamed automatically only when automatic fixing is on, Plex has matched the file, and the file name agrees
with that match (year and title for a film, season and episode number for an episode). Everything else is listed in
the dashboard's Library names tab for you to decide. Subtitles and other files with the same name move along. Every
rename is logged in rename_log.csv and can be undone from the tab. Files the re-encode queue still needs are left
alone. Nothing is deleted here except a duplicate you delete yourself from the tab (logged in deleted_log.csv).

  python media_names.py        scan and print what it found (renames nothing)
"""
import csv, hashlib, json, os, re, threading, time
import config

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "names_state.json")
LOG = os.path.join(HERE, "rename_log.csv")
DELETED_LOG = os.path.join(HERE, "deleted_log.csv")
ROOT = config.MEDIA_ROOT
LIBS = {"Movies": "/media/Movies", "TV Shows": "/media/TV Shows"}   # folder under ROOT -> path inside the Plex container
PLEX_URL = config.PLEX_NAS_URL
SCAN_EVERY = 6 * 3600
VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts", ".flv", ".webm", ".divx", ".ogm"}
SAFE = str.maketrans({c: "" for c in '<>:"/\\|?*'})
MOVIE_RX = re.compile(r"^(?P<t>.+) \((?P<y>\d{4})\)(?: - (?P<ed>.+?))?(?: - pt(?P<pt>\d+))?$")
EP_RX = re.compile(r"^(?P<show>.+?) - s(?P<s>\d{2,})e(?P<e>\d{2,})(?:-e\d{2,})?(?: - .+?)?(?: - pt\d+)?$")
SXE = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})[ ._-]*e(\d{1,3})|(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?!\d)")
YEAR = re.compile(r"(?<!\d)(19[2-9]\d|20[0-4]\d)(?!\d)")
JUNK = re.compile(r"(?i)[ ._\[(-]+(?:2160p|1080p|720p|480p|4k|uhd|bluray|blu-ray|brrip|bdrip|bluraywrip|web-?dl|webrip|web|hdtv|"
                  r"dvdrip|dvd|xvid|divx|x264|x265|h\.?264|h\.?265|hevc|aac|ac3|dts|remux|proper|repack|extended|unrated)\b.*$")
TITLE_STOP = {"the", "and", "part", "movie", "film", "of", "a", "an", "in", "on", "to"}
_lock = threading.RLock()


# ---------------- small helpers ----------------
def clean(s):
    return re.sub(r"\s+", " ", (s or "").translate(SAFE)).strip().rstrip(".").strip()


def iid(path):
    return hashlib.sha1(os.path.normcase(path).encode()).hexdigest()[:12]


def to_win(p):
    for lib, nas in LIBS.items():
        if p.startswith(nas + "/"):
            return os.path.join(ROOT, lib, *p[len(nas) + 1:].split("/"))
    return None


def to_nas(p):
    rel = os.path.relpath(p, ROOT).split(os.sep)
    return "/".join([LIBS[rel[0]]] + rel[1:]) if rel and rel[0] in LIBS else None


def lib_of(p):
    rel = os.path.relpath(p, ROOT).split(os.sep)
    return rel[0] if rel and rel[0] in LIBS else None


def title_matches(title, text):
    """Does the file name (and folder) mention the title? The part after a colon counts on its own."""
    fw = set(re.findall(r"[a-z0-9]+", text.lower()))
    for t in [title] + title.split(":")[1:]:
        tw = [w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in TITLE_STOP]
        if not tw or sum(w in fw for w in tw) / len(tw) >= 0.6:
            return True
    return False


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"auto": True, "seen": {}, "ignored": {}, "issues": []}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    for _ in range(50):
        try:
            os.replace(tmp, STATE); return
        except PermissionError:
            time.sleep(0.1)


def _write_log(path, header, rows):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new and header:
            w.writerow(header)
        w.writerows(rows)


# ---------------- Plex on the NAS ----------------
def plex():
    from plexapi.server import PlexServer
    tok = config.plex_token()   # used here only; never shown or stored
    if not tok:
        raise RuntimeError("no Plex token (set PLEX_TOKEN or sign in to the local Plex server)")
    return PlexServer(PLEX_URL, tok, timeout=60)


def plex_index(px):
    """normcase(path under ROOT) -> what Plex matched it to. Episodes: a list (one file can hold two episodes)."""
    idx = {}
    for sec in px.library.sections():
        if sec.type == "movie":
            for m in sec.all():
                ok = (m.guid or "").startswith("plex://")
                for media in m.media:
                    for i, part in enumerate(media.parts):
                        p = to_win(part.file)
                        if p:
                            idx[os.path.normcase(p)] = {"kind": "movie", "title": m.title, "year": m.year, "matched": ok,
                                                        "edition": getattr(m, "editionTitle", None),
                                                        "part": i + 1 if len(media.parts) > 1 else None}
        elif sec.type == "show":
            shows = {s.ratingKey: s for s in sec.all()}
            for ep in sec.search(libtype="episode"):
                sh = shows.get(ep.grandparentRatingKey)
                for media in ep.media:
                    for part in media.parts:
                        p = to_win(part.file)
                        if p and sh is not None:
                            idx.setdefault(os.path.normcase(p), []).append({
                                "kind": "episode", "show": sh.title, "show_year": sh.year,
                                "matched": (sh.guid or "").startswith("plex://"),
                                "season": ep.parentIndex, "ep": ep.index, "title": ep.title})
    return idx


def plex_rescan(paths):
    """Ask Plex to look at these folders again (after a rename or when it hasn't seen a new file yet)."""
    dirs = {to_nas(p) for p in paths if p and to_nas(p)}
    if not dirs:
        return
    try:
        px = plex()
        secs = {s.locations[0]: s for s in px.library.sections() if s.locations}
        for d in sorted(dirs):
            for loc, s in secs.items():
                if d == loc or d.startswith(loc + "/"):
                    s.update(path=d)
    except Exception:
        pass   # Plex also finds changes on its own schedule


# ---------------- what's wrong with a name, and the right one ----------------
def problem_of(path):
    lib = lib_of(path)
    rel = os.path.relpath(path, os.path.join(ROOT, lib)).split(os.sep)
    stem = os.path.splitext(rel[-1])[0]
    if lib == "Movies":
        if len(rel) != 1:
            return "in a subfolder (movies sit directly in Movies)"
        if not MOVIE_RX.match(stem):
            return "not named 'Title (Year)'"
        return None
    if len(rel) != 3:
        return "not in a 'Show (Year)\\Season NN' folder"
    show, season, _ = rel
    m = EP_RX.match(stem)
    if not re.fullmatch(r"Season \d{2,}", season):
        return f"season folder '{season}' isn't named 'Season NN'"
    if not m:
        return "not named 'Show (Year) - sNNeNN - Title'"
    if m["show"] != show:
        return "file name doesn't start with its show folder's name"
    if int(m["s"]) != int(season.split()[1]):
        return f"season s{m['s']} filed in '{season}'"
    if not re.search(r"\(\d{4}\)$", show):
        return "show folder has no year"
    return None


def guess_movie(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.basename(os.path.dirname(path))
    y = YEAR.findall(stem) or YEAR.findall(folder)
    t = JUNK.sub("", stem.replace(".", " ").replace("_", " "))
    if y:
        t = t.split(y[-1])[0]
    t = clean(re.sub(r"[\[(]\s*$", "", t).strip(" -"))
    return (f"{t} ({y[-1]})" if t and y else None), "guessed from the file name"


def target_for(path, match, busy):
    """(target path or None, confidence, reason, plex summary). Confidence: auto | review | waiting."""
    lib, ext = lib_of(path), os.path.splitext(path)[1].lower()
    name_text = os.path.relpath(path, os.path.join(ROOT, lib))
    if lib == "Movies":
        if match and match["matched"] and match["year"]:
            ed = f" - {clean(match['edition'])}" if match.get("edition") else ""
            pt = f" - pt{match['part']}" if match.get("part") else ""
            tgt = os.path.join(ROOT, "Movies", f"{clean(match['title'])} ({match['year']}){ed}{pt}{ext}")
            years = [int(y) for y in YEAR.findall(name_text)]
            year_ok = not years or any(abs(y - match["year"]) <= 1 for y in years)
            title_ok = title_matches(match["title"], name_text)
            summary = f"Plex: {match['title']} ({match['year']})"
            if year_ok and title_ok:
                return tgt, "auto", "Plex's match agrees with the file name", summary
            why = [] if year_ok else [f"the file name says {', '.join(map(str, years))}"]
            if not title_ok:
                why.append("the file name doesn't mention that title")
            return tgt, "review", f"check Plex's match: {' and '.join(why)}", summary
        g, why = guess_movie(path)
        tgt = os.path.join(ROOT, "Movies", g + ext) if g else None
        if match:
            return tgt, "review", "Plex couldn't identify this film" + (f"; {why}" if g else ""), f"Plex: unmatched ({match['title']})"
        return tgt, "waiting", "Plex hasn't scanned this file yet" + (f"; {why}" if g else ""), "not in Plex yet"
    # TV
    rel = os.path.relpath(path, os.path.join(ROOT, "TV Shows")).split(os.sep)
    num = SXE.search(os.path.basename(path))
    fs, fe = ((int(num[1]), int(num[2])) if num[1] else (int(num[3]), int(num[4]))) if num else (None, None)
    if match and all(e["matched"] for e in match):
        eps = sorted(match, key=lambda e: (e["season"] or 0, e["ep"] or 0))
        e0, e9 = eps[0], eps[-1]
        if e0["season"] is None or e0["ep"] is None:
            return None, "review", "Plex has no episode number for it", f"Plex: {e0['show']}"
        show = clean(e0["show"])
        if e0["show_year"] and f"({e0['show_year']})" not in show:
            show += f" ({e0['show_year']})"
        code = f"s{e0['season']:02d}e{e0['ep']:02d}" + (f"-e{e9['ep']:02d}" if len(eps) > 1 and e9["ep"] != e0["ep"] else "")
        et = clean(e0["title"])
        tgt = os.path.join(ROOT, "TV Shows", show, f"Season {e0['season']:02d}", f"{show} - {code}" + (f" - {et}" if et else "") + ext)
        summary = f"Plex: {e0['show']} {code}" + (f" - {e0['title']}" if e0["title"] else "")
        if (fs, fe) == (e0["season"], e0["ep"]):
            return tgt, "auto", "Plex's match agrees with the episode number in the file name", summary
        why = f"the file name says s{fs:02d}e{fe:02d}" if fs is not None else "the file name has no episode number"
        return tgt, "review", f"check Plex's match: {why}", summary
    show = clean(rel[0]) if len(rel) >= 2 else None
    if not show and num:  # loose file in TV Shows: the show is the name before the episode number
        raw = clean(os.path.basename(path)[:num.start()].replace(".", " ").replace("_", " ").strip(" -"))
        key = lambda n: re.sub(r"[^a-z0-9]", "", re.sub(r"\s*\(\d{4}\)$", "", n.lower()))
        try:
            show = next((d for d in os.listdir(os.path.join(ROOT, "TV Shows")) if key(d) == key(raw)), raw) or None
        except OSError:
            show = raw or None
    tgt = None
    if show and fs is not None:
        tgt = os.path.join(ROOT, "TV Shows", show, f"Season {fs:02d}", f"{show} - s{fs:02d}e{fe:02d}{ext}")
        if os.path.normcase(tgt) == os.path.normcase(path):
            tgt = None
    if match:
        return tgt, "review", "Plex couldn't identify the show", f"Plex: unmatched ({match[0]['show']})"
    return tgt, "waiting", "Plex hasn't scanned this file yet", "not in Plex yet"


def busy_paths():
    """Files the re-encode queue still reads or writes (anything not finished)."""
    import reencode_common as rq
    out = set()
    for j in rq.load().get("jobs", []):
        if j.get("status") != "done":
            for p in list(j.get("src") or []) + list(j.get("staged") or []) + [j["dest"], j["dest"][:-4] + ".partial.mkv"]:
                out.add(os.path.normcase(p))
    return out


# ---------------- scan ----------------
def ensure_plexignore():
    """Plex shouldn't pick up half-written re-encodes ('….partial.mkv')."""
    for lib in LIBS:
        p = os.path.join(ROOT, lib, ".plexignore")
        try:
            if os.path.isdir(os.path.dirname(p)) and not os.path.exists(p):
                with open(p, "w", encoding="utf-8") as f:
                    f.write("# written by the dashboard's naming check: files still being re-encoded\n*.partial.mkv\n")
        except OSError:
            pass


def scan(dry=False):
    """Check every video file; fix the confident ones when automatic fixing is on (never on a dry run).
    Returns the saved state."""
    with _lock:
        st = load_state()
        auto = st.get("auto", True) and not dry
        if not os.path.isdir(ROOT):
            st.update(error=f"{ROOT} isn't reachable (NAS share offline?)", scanned=time.time()); save_state(st); return st
        if not dry:
            ensure_plexignore()
        try:
            idx, plex_err = plex_index(plex()), None
        except Exception as e:  # Plex down: names can still be checked, just not fixed from Plex's match
            idx, plex_err = {}, f"Plex on the NAS not reachable ({type(e).__name__})"
        busy = busy_paths()
        first = not st.get("seen")
        seen, ignored, now = st.setdefault("seen", {}), st.setdefault("ignored", {}), time.time()
        issues, checked, ok, fixed, waiting, errors = [], 0, 0, [], [], []
        present = set()
        for lib in LIBS:
            for dp, _, fns in os.walk(os.path.join(ROOT, lib)):
                for fn in fns:
                    path = os.path.join(dp, fn)
                    stem, ext = os.path.splitext(fn)
                    if ext.lower() not in VIDEO or stem.lower().endswith(".partial"):
                        continue
                    key = os.path.normcase(path)
                    present.add(key)
                    checked += 1
                    is_new = key not in seen
                    if is_new:
                        seen[key] = now
                    if key in busy:
                        ok += 1; continue
                    prob = problem_of(path)
                    if not prob:
                        ok += 1; continue
                    if key in ignored:
                        continue
                    match = idx.get(key)
                    tgt, conf, why, summary = target_for(path, match, busy)
                    taken = tgt and os.path.exists(tgt) and os.path.normcase(tgt) != key
                    if taken and os.path.getsize(tgt) == os.path.getsize(path):
                        conf, why = "duplicate", "the same file (same size) is already there under the proper name"
                    elif tgt and os.path.normcase(tgt) in busy:
                        conf, why = "review", "the re-encode queue is still writing a file with this name"
                    elif taken:
                        conf, why = "conflict", "another file already has that name (different size): pick one"
                    it = {"id": iid(path), "path": path, "lib": lib, "problem": prob, "target": tgt, "confidence": conf,
                          "reason": why, "plex": summary, "size": os.path.getsize(path),
                          "new": (not first) and is_new, "found": seen[key]}
                    if conf == "auto" and auto:
                        try:
                            _rename(path, tgt, "automatic", f"{prob}; {summary}")
                            fixed.append(it); continue
                        except OSError as e:
                            it.update(confidence="review", reason=f"rename failed ({e.strerror or e}); try again")
                            errors.append(path)
                    if conf == "waiting":
                        waiting.append(path)
                    issues.append(it)
        for k in [k for k in seen if k not in present]:   # forget files that are gone
            del seen[k]
        if (fixed or waiting) and not dry:
            plex_rescan([os.path.dirname(x["path"]) for x in fixed] + [os.path.dirname(x["target"]) for x in fixed]
                        + [os.path.dirname(p) for p in waiting])
        order = {"conflict": 0, "review": 1, "duplicate": 2, "auto": 3, "waiting": 4}
        issues.sort(key=lambda x: (order.get(x["confidence"], 9), x["path"].lower()))
        st.update(issues=issues, scanned=now, checked=checked, ok=ok, error=plex_err,
                  fixed_last_scan=len(fixed), plex_files=len(idx), waiting=len(waiting),
                  new_last_scan=0 if first else sum(1 for k, v in seen.items() if v == now))
        save_state(st)
        return st


# ---------------- changes (each one logged) ----------------
def _prune(d):
    """Remove empty folders from d upward, never the library folders themselves."""
    roots = {os.path.normcase(os.path.join(ROOT, lib)) for lib in LIBS}
    while os.path.normcase(d) not in roots and os.path.normcase(d).startswith(os.path.normcase(ROOT) + os.sep):
        try:
            if os.listdir(d):
                return
            os.rmdir(d)
        except OSError:
            return
        d = os.path.dirname(d)


def _rename(src, dst, how, reason):
    """Rename a video plus its same-named sidecar files (subtitles, .nfo, artwork). Logged as one batch."""
    if os.path.exists(dst):
        raise FileExistsError(17, "the new name is already taken")
    batch = f"{time.strftime('%Y%m%d%H%M%S')}-{iid(src)}"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.rename(src, dst)
    moves = [(src, dst)]
    sd, stem = os.path.dirname(src), os.path.splitext(os.path.basename(src))[0]
    dstem = os.path.splitext(os.path.basename(dst))[0]
    try:
        for n in os.listdir(sd):
            if n.lower().startswith(stem.lower() + ".") and os.path.splitext(n)[1].lower() not in VIDEO:
                new = os.path.join(os.path.dirname(dst), dstem + n[len(stem):])
                if not os.path.exists(new):
                    os.rename(os.path.join(sd, n), new); moves.append((os.path.join(sd, n), new))
    except OSError:
        pass
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_log(LOG, ["time", "batch", "from", "to", "how", "reason"], [[t, batch, a, b, how, reason] for a, b in moves])
    _prune(sd)
    return batch


def _issue(st, issue_id):
    it = next((x for x in st.get("issues", []) if x["id"] == issue_id), None)
    if not it:
        raise ValueError("that item isn't in the list any more; scan again")
    if not os.path.exists(it["path"]):
        raise ValueError("the file isn't there any more; scan again")
    return it


def fix(issue_id, target=None):
    """Rename one listed file, to the proposed name or to a name you typed (same library, same file type)."""
    with _lock:
        st = load_state()
        it = _issue(st, issue_id)
        tgt = target.strip() if target else it["target"]
        if not tgt:
            raise ValueError("no name to rename to; type one")
        if not os.path.isabs(tgt):
            tgt = os.path.join(ROOT, it["lib"], tgt)
        tgt = os.path.normpath(tgt)
        libroot = os.path.join(ROOT, it["lib"])
        if not os.path.normcase(tgt).startswith(os.path.normcase(libroot) + os.sep):
            raise ValueError(f"the new name must stay inside {libroot}")
        if os.path.splitext(tgt)[1].lower() != os.path.splitext(it["path"])[1].lower():
            raise ValueError("keep the file type (extension) the same")
        if any(c in part for part in os.path.relpath(tgt, libroot).split(os.sep) for c in '<>:"/|?*'):
            raise ValueError('names can\'t contain < > : " / | ? *')
        if os.path.normcase(tgt) in busy_paths() or os.path.normcase(it["path"]) in busy_paths():
            raise ValueError("the re-encode queue still needs this file or name")
        _rename(it["path"], tgt, "you", f"{it['problem']}; {it['plex']}")
        st["issues"] = [x for x in st["issues"] if x["id"] != issue_id]
        save_state(st)
    plex_rescan([os.path.dirname(it["path"]), os.path.dirname(tgt)])
    return tgt


def ignore(issue_id):
    """Leave this file's name as it is; it won't be listed again."""
    with _lock:
        st = load_state()
        it = next((x for x in st.get("issues", []) if x["id"] == issue_id), None)
        if it:
            st.setdefault("ignored", {})[os.path.normcase(it["path"])] = time.time()
            st["issues"] = [x for x in st["issues"] if x["id"] != issue_id]
            save_state(st)


def delete_duplicate(issue_id):
    """Delete a misnamed copy whose identical twin (same size) already sits at the proper name. Logged."""
    with _lock:
        st = load_state()
        it = _issue(st, issue_id)
        if it["confidence"] != "duplicate" or not it["target"] or not os.path.exists(it["target"]):
            raise ValueError("only a copy whose twin is already at the proper name can be deleted here")
        size = os.path.getsize(it["path"])
        if os.path.getsize(it["target"]) != size:
            raise ValueError("the sizes differ now; scan again")
        os.remove(it["path"])
        _write_log(DELETED_LOG, None, [[time.strftime("%Y-%m-%d %H:%M:%S"), it["path"], size,
                                        f"duplicate of {it['target']} (Library names tab, your click)"]])
        _prune(os.path.dirname(it["path"]))
        st["issues"] = [x for x in st["issues"] if x["id"] != issue_id]
        save_state(st)
    plex_rescan([os.path.dirname(it["path"])])


def recent(n=40):
    """The last n rename batches, newest first, each with whether it was undone."""
    try:
        rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    except FileNotFoundError:
        return []
    batches, undone = {}, set()
    for r in rows:
        if r["how"] == "undo":
            undone.add(r["reason"]); continue
        b = batches.setdefault(r["batch"], {"batch": r["batch"], "time": r["time"], "how": r["how"], "reason": r["reason"], "moves": []})
        b["moves"].append([r["from"], r["to"]])
    out = sorted(batches.values(), key=lambda b: b["batch"], reverse=True)[:n]
    for b in out:
        b["undone"] = b["batch"] in undone
    return out


def undo(batch):
    """Put one rename batch back the way it was."""
    with _lock:
        b = next((x for x in recent(10_000) if x["batch"] == batch), None)
        if not b or b["undone"]:
            raise ValueError("nothing to undo")
        back = []
        for old, new in reversed(b["moves"]):
            if os.path.exists(new) and not os.path.exists(old):
                os.makedirs(os.path.dirname(old), exist_ok=True)
                os.rename(new, old); back.append((new, old))
        if not back:
            raise ValueError("the renamed file isn't there any more (or the old name is taken)")
        _write_log(LOG, None, [[time.strftime("%Y-%m-%d %H:%M:%S"), f"undo-{batch}", a, c, "undo", batch] for a, c in back])
        _prune(os.path.dirname(b["moves"][0][1]))
        st = load_state()
        st.setdefault("ignored", {})[os.path.normcase(b["moves"][0][0])] = time.time()  # don't rename it straight back
        save_state(st)
    plex_rescan([os.path.dirname(b["moves"][0][0]), os.path.dirname(b["moves"][0][1])])


def set_auto(on):
    with _lock:
        st = load_state(); st["auto"] = bool(on); save_state(st)


def path_of(issue_id):
    it = next((x for x in load_state().get("issues", []) if x["id"] == issue_id), None)
    return it["path"] if it and os.path.exists(it["path"]) else None


def background():
    """Scan two minutes after the dashboard starts, then every 6 hours; sooner (15 min) while Plex still has
    new files to look at."""
    time.sleep(120)
    while True:
        try:
            st = scan()
            wait = 900 if st.get("waiting") else SCAN_EVERY
        except Exception as e:
            st = load_state(); st.update(error=f"scan failed: {e!r}"[:300], scanned=time.time()); save_state(st)
            wait = 3600
        time.sleep(wait)


if __name__ == "__main__":
    st = scan(dry=True)
    print(f"checked {st.get('checked')} files, {st.get('ok')} fine, {len(st['issues'])} listed; Plex knows {st.get('plex_files')}"
          + (f"; {st['error']}" if st.get("error") else ""))
    for it in st["issues"]:
        print(f"[{it['confidence']}] {it['path']}\n    {it['problem']} | {it['plex']} | {it['reason']}\n    -> {it['target']}")
