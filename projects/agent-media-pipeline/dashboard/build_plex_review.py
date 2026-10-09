"""Build plex_review.json for the dashboard's Plex review tab. Read-only: works on a backup copy of the Plex database.

Each movie, TV show and movie collection (a folder holding several movies) gets a suggestion - keep, drop or
undecided - with the reason. Suggestions are only suggestions; your marks in the tab are what count.

Duplicates are judged the way Plex matched things, never by folder or size alone:
  - movie: two or more *different* media versions attached to the same Plex movie
  - episode: two or more different files for the same show + season + episode number
  - the same file in two places (copies on two drives) is one version with two locations, not a duplicate
  - CD1/CD2 parts are one version; a file holding two episodes is flagged, not counted twice
  - plus likely duplicates Plex didn't merge: an unidentified movie whose name matches an identified one,
    or the exact same file listed under two titles

  python dashboard/build_plex_review.py
"""
import json, os, re, sqlite3, time
from collections import defaultdict
import config

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = config.PLEX_DB                 # the PC's Plex database; only a backup copy of it is read
SCR = config.WORK_DIR
COPY = os.path.join(SCR, "plex", "library_review.db")
# optional: {folder: [file names]} of files that exist only on the failing drive (written by the rescue step, not included)
E_NEEDED = os.path.join(SCR, "rescue", "failing_only.json")
OUT = os.path.join(HERE, "plex_review.json")
FAILING = config.FAILING_DRIVE.rstrip("\\/").upper()   # e.g. "E:"

KEEP_TITLES = tuple(t.lower() for t in config.KEEP_TITLES)           # your keep list (config.py), empty by default
TASTE_KEEP = {k.lower(): v for k, v in config.TASTE_KEEP.items()}    # {"title substring": "reason"} (config.py)
CULT_STUDIOS = ("williams street", "comedy central", "adult swim")
MAINSTREAM = {"Action", "Thriller", "Crime", "Drama", "Romance", "Comedy", "Adventure", "Mystery", "Family",
              "Fantasy", "Horror", "War", "Western"}
YEAR = 365 * 86400
RES_RANK = {"SD": 0, "720p": 1, "1080p": 2, "4K": 3}
# Re-encode standard (see docs/media_pipeline.md): HEVC 10-bit in MKV at the source resolution, E-AC3 instead of DTS/TrueHD.
# A copy is queued for re-encoding when it is bigger than about 2x the target, or uses a codec/container most players can't
# play directly. Copies that only have a bad audio track or container get the video copied untouched ("audio"/"remux").
# Kept titles: at most 1080p and at most 2.5 GB per hour of running time (movies and TV alike).
CAP_GBH = 2.5
RESULT_GBH = {"SD": 0.7, "720p": 1.3, "1080p": 2.3, "4K": 2.3}  # typical size after re-encoding (4K is scaled to 1080p)
GOOD_VIDEO = ("h264", "hevc", "av1")
BAD_AUDIO = ("dca", "dts", "truehd", "mlp", "wmav2", "wmapro", "mp2", "flac", "pcm_s16le", "pcm_s24le", "pcm_bluray")
BAD_CONTAINER = ("avi", "wmv", "asf", "mpeg", "mpg", "flv", "divx", "ogm", "rmvb", "rm")
MEDIA_ROOT = config.MEDIA_ROOT
RESCUE_ROOT = config.RESCUE_ROOT
SAFE = str.maketrans({c: "" for c in '<>:"/\\|?*'})


def assess(v, path):
    """Does this copy need work to meet the standard (<= 1080p, <= 2.5 GB/hour, plays directly)?
    Returns {kind, reasons, est_bytes}, or None when it already meets it."""
    reasons, kind = [], None
    hours = v["dur"] / 3600 if v["dur"] else 0
    gbh = v["size"] / 1e9 / hours if hours else 0
    if v["vcodec"] not in GOOD_VIDEO:
        kind = "video"; reasons.append(f"old codec ({v['vcodec']}) that many players can't play directly")
    if v["res"] == "4K":
        kind = "video"; reasons.append("4K: scaled to 1080p")
    if gbh > CAP_GBH * 1.05:
        kind = "video"; reasons.append(f"{gbh:.1f} GB per hour; the cap is {CAP_GBH} GB per hour")
    if v["acodec"] in BAD_AUDIO:
        kind = kind or "audio"; reasons.append(f"{v['acodec'].upper()} audio: large and/or forces Plex to convert it while playing")
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in BAD_CONTAINER:
        kind = kind or "remux"; reasons.append(f".{ext} file; MKV plays more widely")
    if not kind:
        return None
    if kind == "video":
        est = int(RESULT_GBH[v["res"]] * 1e9 * hours) if hours else v["size"] // 2
    else:
        est = v["size"]
    return {"kind": kind, "reasons": reasons, "est_bytes": min(est, v["size"])}


def read_path(path):
    """Where to read a file from: the rescued copy on the NAS instead of the failing drive."""
    if path[:len(FAILING)].upper() == FAILING:
        alt = RESCUE_ROOT + path[len(FAILING):]
        if os.path.exists(alt):
            return alt
    return path


def ext_id(ids, kind):
    return next((i.split("://", 1)[1] for i in ids if i.startswith(kind + "://")), None)


def movie_dest(m):
    """Movies sit directly in Movies/ as 'Title (Year).mkv' (no per-movie folder, no database IDs in names)."""
    if not m["matched"] or not m["year"]:
        return None
    t = m["title"].translate(SAFE).strip().rstrip(".")
    ed = f" - {m['edition']}" if m.get("edition") else ""
    return os.path.join(MEDIA_ROOT, "Movies", f"{t} ({m['year']}){ed}.mkv")


def episode_dest(show, season, ep, title):
    """TV: Show (Year)/Season 01/Show (Year) - s01e01 - Episode Title.mkv"""
    t = show["title"].translate(SAFE).strip().rstrip(".")
    yr = f" ({show['year']})" if show["year"] and f"({show['year']})" not in t else ""
    et = (title or "").translate(SAFE).strip()
    fn = f"{t}{yr} - s{season:02d}e{ep:02d}" + (f" - {et}" if et else "") + ".mkv"
    return os.path.join(MEDIA_ROOT, "TV Shows", f"{t}{yr}", f"Season {season:02d}", fn)


CODEC_RANK = {"hevc": 3, "av1": 3, "h264": 2}


def snapshot():
    os.makedirs(os.path.dirname(COPY), exist_ok=True)
    src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(COPY)
    src.backup(dst)
    src.close()
    return dst


def res(w):
    return "4K" if w > 2000 else "1080p" if w > 1300 else "720p" if w > 900 else "SD"


def version_score(v):
    """Best first: resolution, then a modern codec, then surround audio, then a copy that isn't only on the failing
    drive, then bitrate."""
    return (RES_RANK[v["res"]], CODEC_RANK.get(v["vcodec"], 0), v["ch"] >= 6, not v["e_only"], v["kbps"])


def pick_reason(vs):
    a, b = vs[0], vs[1]
    if a["res"] != b["res"]:
        return f"suggested: the {a['res']} version (highest resolution)"
    if CODEC_RANK.get(a["vcodec"], 0) != CODEC_RANK.get(b["vcodec"], 0):
        return f"suggested: the {a['vcodec'].upper()} version (same resolution, more modern codec)"
    if (a["ch"] >= 6) != (b["ch"] >= 6):
        return "suggested: the version with surround sound"
    if a["e_only"] != b["e_only"]:
        return "suggested: the copy that isn't only on the failing drive"
    return "suggested: the higher-bitrate version (same resolution and codec)"


EXTRA_WORDS = ("commentary", "featurette", "behind the scenes", "deleted scene", "bonus", "trailer", "sample", "interview",
               "making of", "gag reel", "bloopers")
EP_PATTERNS = [
    (re.compile(r"[sS](\d{1,2})[\s._-]*[eE](\d{1,3})(?:[-_ ]?[eE]?(\d{1,3}))?"), "se"),
    (re.compile(r"\b(\d{1,2})x(\d{2,3})\b"), "se"),
    (re.compile(r"season\s*(\d{1,2})\D{1,12}episode\s*(\d{1,3})", re.I), "se"),
    (re.compile(r"(?:^|[\s._-])(\d{3})(?:[\s._-]|$)"), "abs"),
]


# anime-style numbering at the very end of the name: "Show Name 01", "Show - 12v2"
ANIME_EP = re.compile(r"[\s._-](\d{1,3})(?:v\d)?\s*$")


def parse_episode(path):
    """Season/episode numbers and the episode title, read from the file name itself (not from Plex)."""
    name = os.path.splitext(os.path.basename(path))[0]
    for rx, kind in EP_PATTERNS + [(ANIME_EP, "abs")]:
        mm = rx.search(name)
        if mm and kind == "abs" and rx is ANIME_EP and re.search(r"(part|pt|vol|volume|chapter|cd|disc|disk)[\s._-]*$",
                                                                 name[:mm.start(1)], re.I):
            continue  # "Part 2", "Vol 3": a sequel or a split film, not an episode number
        if mm:
            if kind == "se":
                season, ep = int(mm.group(1)), int(mm.group(2))
                end = int(mm.group(3)) if mm.lastindex and mm.lastindex >= 3 and mm.group(3) and int(mm.group(3)) > ep else ep
            else:
                season, ep, end = None, int(mm.group(1)), int(mm.group(1))
            rest = name[mm.end():]
            rest = re.split(r"\b(1080p|720p|480p|2160p|web|webdl|web-dl|hdtv|bluray|dvdrip|x264|x265|hevc|xvid|aac|webrip|amzn|nf)\b", rest, flags=re.I)[0]
            rest = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", rest)
            title = re.sub(r"[._]+", " ", rest).strip(" -")
            return season, ep, end, title
    return None


def words(t):
    return {w for w in re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) > 2 and w not in ("the", "and", "episode", "part")}


def episode_status(r, vs):
    """Is this Plex episode group really the same episode more than once?
    duplicate: file names agree on the episode (and titles/lengths don't contradict it)
    overlap:   one file holds two episodes and also covers this one
    extra:     one file is a commentary, featurette or special, not a copy of the episode
    misfiled:  the files are different episodes that Plex put under the same number (fix the names; not a duplicate)"""
    names = [v["name"] for v in vs]
    if any(any(w in n.lower() for w in EXTRA_WORDS + ("special",)) for n in names):
        return "extra", "one file is a commentary, special or extra, not another copy of the episode"
    parsed = [parse_episode(n) for n in names]
    if all(parsed):
        keys = {(p[0], p[1]) if p[0] is not None else (None, p[1]) for p in parsed}
        ranged = [p for p in parsed if p[2] > p[1]]
        if ranged and any(p[1] <= q_[1] <= p[2] for p in ranged for q_ in parsed if q_ is not p):
            return "overlap", "a double-episode file also contains this episode"
        if len(keys) > 1:
            nums = ", ".join(sorted({(f"S{p[0]:02d}E{p[1]:02d}" if p[0] is not None else f"#{p[1]}") for p in parsed}))
            return "misfiled", f"the file names say different episodes ({nums}); Plex filed them under one number"
        titles = [words(p[3]) for p in parsed]
        if all(titles) and not set.intersection(*titles):
            return "misfiled", "same number but different episode titles in the file names: probably different episodes"
    durs = [v["dur"] for v in vs if v["dur"]]
    if len(durs) > 1 and max(durs) > 1.25 * min(durs):
        return "misfiled", "running times differ by more than 25%: probably different episodes"
    if not all(parsed):
        return "check", "the file names don't give an episode number; running times are similar. Check before deleting"
    return "duplicate", ""


PART_RX = re.compile(r"[\s._\-\[(]*(?<![a-z])(?:pt|part|cd|disc|disk)[\s._-]*(\d)(?!\d)", re.I)
TITLE_STOP = {"the", "and", "part", "movie", "film", "of", "a", "an", "in", "on", "to"}
EXTRA_TAGS = ("trailer", "[extra", "extras", "featurette", "sample", "behind the scenes", "deleted scene", "making of", "interview")
CAM_TAGS = ("cam", "screener", "dvdscr", "telesync", ".ts.", " ts ", "hdts", "hdcam")


def title_matches(title, fname):
    """Does the file (name plus its folder name) mention the title? The part after a colon counts on its own, so
    'The Second Chapter.avi' matches 'Some Saga: The Second Chapter'."""
    fw = set(re.findall(r"[a-z0-9]+", fname.lower()))
    for t in [title] + title.split(":")[1:]:
        tw = [w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in TITLE_STOP]
        if not tw or sum(w in fw for w in tw) / len(tw) >= 0.6:
            return True
    return False


def classify_movie_versions(title, vs):
    """Label each version of a movie:
    main  - the copy we'd keep by default (best feature-length copy whose file name matches the title)
    same  - another copy of the same cut (duplicate), including cam/screener copies
    cut   - same film, different length (extended / theatrical / director's cut)
    extra - trailer, sample, featurette or extras disc
    part  - a CD1/CD2 half or an episode, not a copy of the whole film
    wrong - the file name doesn't match this title: Plex probably filed a different film here
    """
    for v in vs:
        n = v["name"].lower()
        v["title_ok"] = title_matches(title, v["name"] + " " + v.get("folder", ""))
        v["relation"] = "extra" if any(t in n for t in EXTRA_TAGS) or (v["dur"] and v["dur"] < 15 * 60) else None
    longest = max((v["dur"] for v in vs if v["relation"] is None), default=0)
    feature = [v for v in vs if v["relation"] is None and (not longest or v["dur"] >= 0.6 * longest)]
    pool = [v for v in feature if v["title_ok"]] or feature or vs
    main = max(pool, key=version_score)
    main["relation"] = "main"
    main_words = set(re.findall(r"[a-z]{3,}", main["name"].lower())) - TITLE_STOP
    for v in vs:
        if v["relation"]:
            continue
        if not main["title_ok"]:
            # no file name mentions the title: only call another file a copy if its name looks like the main one's
            vw = set(re.findall(r"[a-z]{3,}", v["name"].lower())) - TITLE_STOP
            if main_words and len(vw & main_words) / max(1, len(main_words | vw)) < 0.5:
                v["relation"] = "other"
                continue
        if not v["title_ok"] and main["title_ok"]:
            v["relation"] = "wrong"
            continue
        if not main["dur"] or not v["dur"]:
            v["relation"] = "same"
            continue
        ratio = max(main["dur"], v["dur"]) / max(1, min(main["dur"], v["dur"]))
        if v["dur"] < 0.6 * main["dur"]:
            v["relation"] = "part"
        elif ratio <= 1.05 or (ratio <= 1.4 and any(t in v["name"].lower() for t in CAM_TAGS)):
            v["relation"] = "same"
        elif ratio <= 1.4:
            v["relation"] = "cut"
        else:
            v["relation"] = "other"
    order = {"main": 0, "same": 1, "cut": 2, "other": 3, "part": 4, "wrong": 5, "extra": 6}
    vs.sort(key=lambda v: (order[v["relation"]], [-x for x in version_score(v)[:4]], -v["kbps"]))


def movie_relation(best, v):
    """How another version of a movie relates to the main one, judged by running time and file name."""
    if v["size"] < 400e6 and "sample" in v["name"].lower():
        return "sample"
    if not best["dur"] or not v["dur"]:
        return "same"
    ratio = max(best["dur"], v["dur"]) / max(1, min(best["dur"], v["dur"]))
    if ratio <= 1.05:
        return "same"
    if ratio <= 1.40:
        return "cut"  # e.g. extended or director's cut
    return "wrong"


def franchise(title):
    """Series key: the part before a colon, without numbers ('Some Saga and the...' -> 'some saga')."""
    t = title.lower().split(":")[0]
    t = re.sub(r"\b(part|chapter|vol|volume|episode)\b.*$", "", t)
    t = re.sub(r"[^a-z ]", " ", t)
    words = [w for w in t.split() if w not in ("the", "a", "an", "and", "of")]
    return " ".join(words[:2]) if len(words) >= 2 else (words[0] if words else "")


def norm(t):
    t = re.sub(r"[^a-z0-9]", "", t.lower())
    return t[3:] if t.startswith("the") and len(t) > 8 else t


def strip_file_noise(t):
    t = re.sub(r"\b(19|20)\d{2}\b", " ", t)
    t = re.sub(r"\b(cd|disc|part|pt)\s*\d+\b", " ", t, flags=re.I)
    t = re.sub(r"\b(1080p|720p|480p|2160p|bluray|brrip|dvdrip|webrip|web-dl|x264|x265|hevc|xvid)\b.*", " ", t, flags=re.I)
    return re.sub(r"^[\d\s._-]+", "", t)


def main():
    c = snapshot()
    q = lambda s, *a: c.execute(s, a).fetchall()
    now = time.time()
    accounts = dict(q("select id, name from accounts"))
    hist_start = q("select min(viewed_at) from metadata_item_views")[0][0] or now
    watch = defaultdict(lambda: {"views": 0, "last": 0, "who": set()})
    for guid, acc, vc, last in q("select guid, account_id, view_count, last_viewed_at from metadata_item_settings where view_count > 0"):
        w = watch[guid]; w["views"] += vc; w["last"] = max(w["last"], last or 0); w["who"].add(accounts.get(acc) or "you")
    ext_ids = defaultdict(list)
    for mid, tag in q("select tg.metadata_item_id, t.tag from taggings tg join tags t on t.id = tg.tag_id where t.tag_type = 314"):
        ext_ids[mid].append(tag)
    e_only = set()
    if os.path.exists(E_NEEDED):
        for d, names in json.load(open(E_NEEDED, encoding="utf-8")).items():
            e_only.update(os.path.join(d, n).lower() for n in names)

    # ---- files and versions ----
    FILES, fidx = [], {}  # every file path, referenced by index so the dashboard never sends paths back to the server

    def fid(path):
        if path not in fidx:
            fidx[path] = len(FILES); FILES.append(path)
        return fidx[path]

    items_per_file = defaultdict(set)
    media = {}
    for mid, miid, w, vc, ac, ch, br, trc, dur, path, size in q(
            """select mi.metadata_item_id, mi.id, mi.width, mi.video_codec, mi.audio_codec, mi.audio_channels,
                      mi.bitrate, mi.color_trc, mi.duration, mp.file, mp.size from media_items mi join media_parts mp on mp.media_item_id = mi.id
               where mp.deleted_at is null order by mp."index" """):
        items_per_file[path.lower()].add(mid)
        v = media.setdefault(miid, {"item": mid, "media": miid, "res": res(w or 0), "vcodec": vc or "?", "acodec": ac or "?",
                                    "ch": ch or 0, "kbps": round((br or 0) / 1000),
                                    "hdr": (trc or "") in ("smpte2084", "arib-std-b67"), "dur": round((dur or 0) / 1000),
                                    "parts": [], "size": 0})
        v["parts"].append(path); v["size"] += size or 0
    by_item = defaultdict(list)
    for v in media.values():
        by_item[v["item"]].append(v)

    def versions_of(item_id):
        """Distinct versions of one movie/episode. Identical copies (same total size, part count and file type) are
        folded into one version with several locations; CD1/CD2 parts stay together as one version."""
        out = {}
        for v in by_item.get(item_id, []):
            sig = (v["size"], len(v["parts"]), os.path.splitext(v["parts"][0])[1].lower())
            if sig in out:
                out[sig]["copies"].append([fid(p) for p in v["parts"]])
                continue
            out[sig] = {"media": v["media"], "res": v["res"], "vcodec": v["vcodec"], "acodec": v["acodec"], "ch": v["ch"],
                        "dur": v["dur"], "name": os.path.basename(v["parts"][0]),
                        "folder": os.path.basename(os.path.dirname(v["parts"][0])), "media_ids": [v["media"]], "split": 1,
                        "kbps": v["kbps"], "hdr": v["hdr"], "size": v["size"], "copies": [[fid(p) for p in v["parts"]]],
                        "multi_episode": any(len(items_per_file[p.lower()]) > 1 for p in v["parts"])}
        vs = list(out.values())
        # one copy split into files that Plex saw as separate versions (PT_1 / PT_2, CD1 / CD2, Disc 1 / Disc 2)
        split = defaultdict(list)
        for x in vs:
            mm = PART_RX.search(x["name"])
            if mm:
                split[(PART_RX.sub("", x["name"]).lower(), x["folder"].lower())].append((int(mm.group(1)), x))
        for lst in split.values():
            if len(lst) > 1 and len({n for n, _ in lst}) == len(lst):
                lst.sort(key=lambda t: t[0])
                first = lst[0][1]
                for _, x in lst[1:]:
                    first["copies"][0] += x["copies"][0]
                    first["size"] += x["size"]; first["dur"] += x["dur"]
                    first["media_ids"].append(x["media"]); first["split"] += 1
                    vs.remove(x)
        for x in vs:
            x["copies"].sort(key=lambda cp: FILES[cp[0]][:2].upper() == FAILING)  # main location: not the failing drive
            paths = [FILES[i] for cp in x["copies"] for i in cp]
            x["drives"] = sorted({p[:2].upper() for p in paths})
            x["e_only"] = all(p[:2].upper() == FAILING for p in paths) and all(FILES[i].lower() in e_only for i in x["copies"][0])
        vs.sort(key=version_score, reverse=True)
        return vs

    def base(m):
        genres = [g for g in (m["tags_genre"] or "").split("|") if g]
        return {"id": m["id"], "title": m["title"], "year": m["year"], "studio": m["studio"] or "", "genres": genres,
                "rating": m["audience_rating"], "content_rating": m["content_rating"] or "",
                "country": (m["tags_country"] or "").split("|")[0], "added": m["added_at"],
                "ids": ext_ids.get(m["id"], []), "matched": not (m["guid"] or "").startswith("local://"),
                "edition": (m["edition_title"] or "").strip()}

    c.row_factory = sqlite3.Row
    # ---------------- movies ----------------
    movies = []
    for m in c.execute("select * from metadata_items where metadata_type = 1"):
        it = base(m)
        vs = versions_of(m["id"])
        if not vs:
            continue
        w = watch.get(m["guid"], {"views": 0, "last": 0, "who": set()})
        classify_movie_versions(m["title"], vs)
        if not any(v["title_ok"] for v in vs):
            it["name_warning"] = "none of the file names mention this title: Plex may have filed the wrong film(s) here"
        it.update(kind="movie", versions=vs, size=sum(v["size"] for v in vs), best=f"{vs[0]['res']} {vs[0]['vcodec']}",
                  extra_bytes=sum(v["size"] for v in vs if v["relation"] in ("same", "cut")), views=w["views"], last_viewed=w["last"], watched_by=sorted(w["who"]),
                  drives=sorted({d for v in vs for d in v["drives"]}), e_only=all(v["e_only"] for v in vs),
                  pick_reason=pick_reason([v for v in vs if v["relation"] in ("main", "same", "cut")])
                  if sum(v["relation"] in ("main", "same", "cut") for v in vs) > 1 else "",
                  has_dupes=sum(v["relation"] in ("main", "same", "cut") for v in vs) > 1,
                  problems=sorted({v["relation"] for v in vs if v["relation"] in ("wrong", "extra", "part", "other")}))
        it["size"] = sum(v["size"] for v in vs)
        it["suggest"], it["reason"] = suggest_movie(it, now, hist_start)
        it["strength"] = strength(it["suggest"], it["reason"])
        for v in vs:
            v["src"] = [fid(read_path(FILES[i])) for i in v["copies"][0]]  # all parts, in order (split copies get joined)
            v["encode"] = assess(v, FILES[v["copies"][0][0]])
            pe = parse_episode(v["name"])  # season/episode/title from the file name, for "Keep in TV"
            v["ep"] = [pe[0], pe[1], pe[3]] if pe else None
        it["looks_like_tv"] = sum(1 for v in vs if v["ep"]) if sum(1 for v in vs if v["ep"]) >= 3 else 0
        it["show_name"] = re.sub(r"\s*(:.*|the movie.*)$", "", it["title"], flags=re.I).strip() or it["title"]
        it["dest"] = movie_dest(it)
        it["franchise"] = franchise(it["title"])
        movies.append(it)

    # ---------------- shows: duplicates judged per episode (same show, season and episode number) ----------------
    shows = []
    eps = defaultdict(list)
    for r in c.execute("""select s.id sid, e.id eid, e.guid, e.title, e."index" ep, se."index" season from metadata_items s
                          join metadata_items se on se.parent_id = s.id and se.metadata_type = 3
                          join metadata_items e on e.parent_id = se.id and e.metadata_type = 4
                          where s.metadata_type = 2 order by se."index", e."index" """):
        eps[r["sid"]].append(r)
    for m in c.execute("select * from metadata_items where metadata_type = 2").fetchall():
        it = base(m)
        rows, dup_eps, size, e_bytes, drives, best = [], [], 0, 0, set(), "SD"
        for r in eps[m["id"]]:
            vs = versions_of(r["eid"])
            if not vs:
                continue
            label = f"S{(r['season'] or 0):02d}E{(r['ep'] or 0):02d}"
            size += sum(v["size"] for v in vs)
            e_bytes += sum(v["size"] for v in vs if v["e_only"])
            drives.update(d for v in vs for d in v["drives"])
            best = max([best] + [v["res"] for v in vs], key=RES_RANK.get)
            bv = vs[0]
            enc = assess(bv, FILES[bv["copies"][0][0]])
            parsed = parse_episode(bv["name"])
            # only name an episode from Plex's numbers when the file name agrees (or has no numbers at all)
            agree = parsed is None or ((parsed[0] in (None, r["season"])) and parsed[1] == r["ep"]) or parsed[0] is None
            rows.append([label, r["title"], bv["copies"][0][0], bv["size"], len(vs),
                         enc["kind"] if enc else "", enc["est_bytes"] if enc else 0,
                         [fid(read_path(FILES[i])) for i in bv["copies"][0]],
                         r["season"] or 0, r["ep"] or 0, 1 if agree else 0])
            if len(vs) > 1:
                status, note = episode_status(r, vs)
                dup_eps.append({"ep": r["eid"], "label": label, "title": r["title"], "versions": vs, "pick_reason": pick_reason(vs),
                                "status": status, "note": note})
        if not rows:
            continue
        ws = [watch[r["guid"]] for r in eps[m["id"]] if r["guid"] in watch]
        it.update(kind="show", episodes=len(rows), seasons=len({r["season"] for r in eps[m["id"]]}), size=size,
                  watched_eps=len(ws), last_viewed=max((w["last"] for w in ws), default=0),
                  watched_by=sorted({p for w in ws for p in w["who"]}), drives=sorted(drives), e_only_bytes=e_bytes, best=best,
                  files=rows, dup_eps=dup_eps,
                  dup_bytes=sum(sum(v["size"] for v in d["versions"][1:]) for d in dup_eps if d["status"] in ("duplicate", "overlap", "check")))
        it["suggest"], it["reason"] = suggest_show(it, now)
        it["strength"] = strength(it["suggest"], it["reason"])
        it["dest_root"] = episode_dest(it, 1, 1, "").rsplit("\\Season", 1)[0]
        it["looks_like_movie"] = len(rows) == 1
        it["encode_eps"] = sum(1 for x in rows if x[5])
        it["encode_bytes"] = sum(x[3] for x in rows if x[5])
        it["encode_est"] = sum(x[6] for x in rows if x[5])
        it["franchise"] = franchise(it["title"])
        shows.append(it)

    # ---------------- collections: a top-level folder holding 2+ movies ----------------
    roots = [r[0].rstrip("\\") + "\\" for r in q("select root_path from section_locations")]
    groups = defaultdict(set)
    for it in movies:
        for v in it["versions"]:
            for cp in v["copies"]:
                path = FILES[cp[0]]
                root = next((r for r in roots if path.lower().startswith(r.lower())), None)
                if root:
                    rel = path[len(root):].split("\\")
                    if len(rel) > 2:  # the movie sits inside a sub-folder of a top-level folder
                        groups[rel[0].strip()].add(it["id"])
    by_id = {m_["id"]: m_ for m_ in movies}
    collections = []
    for name, ids in groups.items():
        if len(ids) < 2:
            continue
        mem = [by_id[i] for i in ids]
        votes = defaultdict(int)
        for m_ in mem:
            votes[m_["suggest"]] += 1
        sug = max(votes, key=lambda k: (votes[k], k == "keep"))
        collections.append({"kind": "collection", "id": "c:" + name, "title": name, "members": sorted(ids),
                            "size": sum(m_["size"] for m_ in mem), "suggest": sug,
                            "reason": f"{votes['keep']} keep, {votes['drop']} drop, {votes['undecided']} undecided among its movies"})

    # ---------------- likely duplicates Plex didn't merge ----------------
    names = defaultdict(set)
    for m_ in movies:
        if m_["matched"]:
            for part in [m_["title"]] + m_["title"].split(":"):
                n = norm(part)
                if len(n) >= 5:
                    names[n].add(m_["id"])
    maybe = []
    for m_ in movies:
        if not m_["matched"]:
            full = norm(m_["title"])
            hits = names.get(norm(strip_file_noise(m_["title"])), set())
            if not hits:  # the longest known title contained in the name ("…the second chapter ext pt 1" -> The Second Chapter)
                inside = [n for n in names if len(n) >= 10 and n in full]
                if inside:
                    hits = names[max(inside, key=len)]
            if hits:
                maybe.append({"kind": "name", "ids": sorted(hits) + [m_["id"]],
                              "why": f"Plex couldn't identify \"{m_['title']}\", but its name matches "
                                     + ", ".join(f"{by_id[h]['title']} ({by_id[h]['year']})" for h in sorted(hits))})
    shadows = {}
    by_sig = defaultdict(set)
    for m_ in movies:
        for v in m_["versions"]:
            if v["size"] > 200e6 and v["dur"]:
                by_sig[(v["size"], v["dur"] // 10, v["name"].lower())].add(m_["id"])
    for (sz, _, fname), ids in by_sig.items():
        if len(ids) > 1:
            ms = [by_id[i] for i in ids]
            right = [m_ for m_ in ms if title_matches(m_["title"], fname)]
            keep_row = next((m_ for m_ in ms if any(d != FAILING for d in m_["drives"])), ms[0])
            for m_ in ms:
                if m_ is not keep_row and all(d == FAILING for d in m_["drives"]):
                    shadows[m_["id"]] = keep_row["id"]
            if len(right) == 1 and len(ms) > 1:
                for m_ in ms:
                    if m_ is not right[0]:
                        m_["wrong_match"] = f"this file looks like \"{right[0]['title']}\" ({fname})"
            maybe.append({"kind": "samefile", "ids": sorted(ids),
                          "why": "the same file (same name, size and running time) is matched to "
                                 + " and to ".join(f"\"{by_id[i]['title']}\"" for i in sorted(ids))
                                 + ", usually two copies on different drives matched differently. One of these matches is wrong."})

    for col in collections:  # point collections at the visible row instead of a hidden failing-drive twin
        col["members"] = sorted({shadows.get(i, i) for i in col["members"]})
        col["size"] = sum(by_id[i]["size"] for i in col["members"])
    collections = [col for col in collections if len(col["members"]) > 1]
    for lst in (movies, shows, collections):
        lst.sort(key=lambda x: -x["size"])
    data = {"built": time.strftime("%Y-%m-%d %H:%M"), "history_since": time.strftime("%Y-%m-%d", time.localtime(hist_start)),
            "movies": [m_ for m_ in movies if m_["id"] not in shadows], "shadows": shadows, "shows": shows, "collections": collections,
            "maybe_dupes": [x for x in maybe if not (x["kind"] == "name" and x["ids"][-1] in shadows)], "files": FILES,
            "asked_keep": list(KEEP_TITLES), "roots": roots,
            "missing_favorites": [f for f in KEEP_TITLES if not any(f in x["title"].lower() for x in movies + shows)]}
    json.dump(data, open(OUT, "w", encoding="utf-8"))
    for name, lst in (("movies", movies), ("shows", shows), ("collections", collections)):
        cnt = defaultdict(lambda: [0, 0])
        for x in lst:
            cnt[x["suggest"]][0] += 1; cnt[x["suggest"]][1] += x["size"]
        print(f"{name:12} {len(lst):4}  " + "  ".join(f"{k} {v[0]} ({v[1] / 1e12:.2f} TB)" for k, v in sorted(cnt.items())))
    mv = [m_ for m_ in movies if len(m_["versions"]) > 1]
    print(f"movies with 2+ different versions: {len(mv)} ({sum(m_['extra_bytes'] for m_ in mv) / 1e9:.0f} GB in the extra versions)")
    de = [d for s_ in shows for d in s_["dup_eps"]]
    from collections import Counter
    print("episode groups by status:", dict(Counter(d["status"] for d in de)),
          f"-> real duplicates {sum(s_['dup_bytes'] for s_ in shows) / 1e9:.0f} GB extra")
    print("hidden failing-drive copies listed as separate movies:", len(shadows), "| wrong matches found:",
          sum(1 for m_ in movies if m_.get("wrong_match")))
    print("movie version labels:", dict(Counter(v["relation"] for m_ in movies for v in m_["versions"])),
          "| movies with real duplicate versions:", sum(m_["has_dupes"] for m_ in movies))
    print(f"likely duplicates Plex didn't merge: {sum(x['kind'] == 'name' for x in maybe)} by name, "
          f"{sum(x['kind'] == 'samefile' for x in maybe)} same file under two titles")
    print("keep-list titles not in Plex:", data["missing_favorites"])


# How strongly each first-guess rule points its way (the dashboard turns this into a % confidence)
STRENGTH = [("never watched since Plex history began", 0.8), ("mainstream series", 0.8),  # before "watched "
            ("on your keep list", 3.0), ("your taste list", 1.2), ("watched ", 2.0), ("episode(s) watched", 2.0),
            ("reality TV", 1.5), ("poorly rated", 1.3), ("documentary", 1.2),
            ("science / nature", 1.2), ("adult animation", 1.0), ("anime", 1.0), ("cult", 1.0), ("sci-fi", 1.0),
            ("low-quality (SD)", 1.0), ("pre-1980", 0.9), ("classic horror", 0.9)]


def strength(suggest, reason):
    if suggest == "undecided":
        return 0.0
    return next((w for k, w in STRENGTH if k in (reason or "")), 0.8)


def has(it, *genres):
    return any(g in it["genres"] for g in genres)


def suggest_movie(it, now, hist_start):
    t = it["title"].lower()
    if any(f in t for f in KEEP_TITLES):
        return "keep", "on your keep list"
    if it["views"]:
        who = ", ".join(it["watched_by"])
        return "keep", f"watched {it['views']}x (by {who}), last {time.strftime('%b %Y', time.localtime(it['last_viewed']))}"
    for k, why in TASTE_KEEP.items():
        if k in t:
            return "keep", f"your taste list: {why}"
    if not it["matched"]:
        return "undecided", "Plex couldn't identify it: check what it is (often a normal movie with an odd file name)"
    if has(it, "Documentary", "Music"):
        return "keep", "documentary / music film: harder to find again"
    if has(it, "Animation") and (it["content_rating"] in ("R", "NC-17", "TV-MA") or it["country"] == "Japan"):
        return "keep", "adult animation / anime"
    if has(it, "Science Fiction") and ((it["year"] or 9999) < 2000 or (it["rating"] or 0) >= 7.5):
        return "keep", "sci-fi classic or highly rated sci-fi"
    if (it["year"] or 9999) < 1980:
        return "keep", "pre-1980 film: harder to find"
    if has(it, "Horror") and (it["year"] or 9999) < 1995:
        return "keep", "classic horror"
    if (it["rating"] or 10) < 5.5:
        return "drop", f"poorly rated ({it['rating']}/10) and never watched"
    if it["best"].startswith("SD") and (it["year"] or 0) >= 2000:
        return "drop", "low-quality (SD) copy of a modern film, never watched"
    old_enough = (it["added"] or now) < max(hist_start, now - 2 * YEAR)
    if old_enough and (it["year"] or 0) >= 1995 and it["genres"] and set(it["genres"]) <= MAINSTREAM:
        return "drop", "never watched since Plex history began; mainstream and easy to stream or re-download"
    return "undecided", f"rated {it['rating']}/10" if it["rating"] else ""


def suggest_show(it, now):
    t = it["title"].lower()
    stu = it["studio"].lower()
    if any(f in t for f in KEEP_TITLES):
        return "keep", "on your keep list"
    if it["watched_eps"]:
        return "keep", f"{it['watched_eps']} episode(s) watched (by {', '.join(it['watched_by'])})"
    for k, why in TASTE_KEEP.items():
        if k in t:
            return "keep", f"your taste list: {why}"
    if has(it, "Anime") or (has(it, "Animation") and it["country"] == "Japan"):
        return "keep", "anime"
    if has(it, "Animation") and (any(s in stu for s in CULT_STUDIOS) or it["content_rating"] in ("TV-MA", "TV-14")):
        return "keep", "adult animation / cult comedy"
    if has(it, "Reality"):
        return "drop", "reality TV, never watched"
    if has(it, "Documentary") and not has(it, "Crime"):
        return "keep", "science / nature / how-things-work documentary: hard to find again, good visual reference"
    if not it["matched"] or not it["genres"]:
        return "undecided", "Plex couldn't identify this show: check what it is"
    if has(it, "Science Fiction"):
        return "keep", "sci-fi"
    if has(it, "Comedy") and any(s in stu for s in CULT_STUDIOS):
        return "keep", "cult comedy"
    if has(it, "Family") or it["content_rating"] in ("TV-Y", "TV-Y7", "TV-G"):
        return "undecided", "kids' show: check with the family"
    if (it["year"] or 0) >= 1995 and set(it["genres"]) <= MAINSTREAM | {"War & Politics", "Sci-Fi & Fantasy"}:
        return "drop", "never watched; mainstream series that's easy to stream or re-download"
    return "undecided", ""


if __name__ == "__main__":
    main()
