"""Fresh duplicate check of the client media library (CLIENT_LIBRARY in config.py). Read-only.

Same size -> same sampled hash (first/middle/last 1 MiB) -> same full hash = duplicate.
Writes dupes_review.json in the dashboard's pair format (the copy with the shorter path is "kept";
the Duplicates tab lets you swap). The previous review is archived first.

  python dashboard/check_library_dupes.py
"""
import hashlib, json, os, shutil, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import config

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = config.CLIENT_LIBRARY
OUT = os.path.join(HERE, "dupes_review.json")
SKIP = {"desktop.ini", "thumbs.db", ".ds_store"}
CHUNK = 1 << 20


def sampled(p, size):
    h = hashlib.blake2b(digest_size=16)
    with open(p, "rb") as f:
        for off in sorted({0, max(0, size // 2 - CHUNK // 2), max(0, size - CHUNK)}):
            f.seek(off)
            h.update(f.read(CHUNK))
    return h.hexdigest()


def full(p):
    h = hashlib.blake2b(digest_size=16)
    with open(p, "rb") as f:
        while b := f.read(8 * CHUNK):
            h.update(b)
    return h.hexdigest()


def refine(groups, fn):
    out = []
    with ThreadPoolExecutor(2) as ex:  # light, so the NAS stays responsive for other jobs
        for g in groups:
            by = defaultdict(list)
            for p, hsh in zip(g, ex.map(fn, g)):
                if hsh:
                    by[hsh].append(p)
            out += [v for v in by.values() if len(v) > 1]
    return out


def main():
    t0 = time.time()
    sizes = defaultdict(list)
    n = 0
    for dp, dns, fns in os.walk(LIB):
        for f in fns:
            if f.lower() in SKIP or f.startswith("._"):
                continue
            p = os.path.join(dp, f)
            try:
                s = os.path.getsize(p)
            except OSError:
                continue
            if s:
                sizes[s].append(p); n += 1
    cand = [v for v in sizes.values() if len(v) > 1]
    print(f"{n} files; {len(cand)} same-size groups ({sum(map(len, cand))} files) after {time.time() - t0:.0f}s")

    def safe(fn):
        def w(p):
            try:
                return fn(p)
            except OSError:
                return None
        return w
    cand = refine(cand, safe(lambda p: sampled(p, os.path.getsize(p))))
    print(f"{len(cand)} groups match on sampled hash after {time.time() - t0:.0f}s")
    cand = refine(cand, safe(full))
    print(f"{len(cand)} groups are exact duplicates after {time.time() - t0:.0f}s")

    if os.path.exists(OUT):
        prev = json.load(open(OUT, encoding="utf-8"))
        if not prev.get("fresh_scan"):
            shutil.copy(OUT, os.path.join(HERE, f"dupes_review_{time.strftime('%Y-%m-%d')}_done.json"))
    pairs = []
    for g in cand:
        g.sort(key=lambda p: (len(p), p.lower()))
        for d in g[1:]:
            pairs.append({"dup": d, "kept": g[0], "bytes": os.path.getsize(d), "dup_exists": True,
                          "kept_exists": True, "note": "found inside the library"})
    groups = defaultdict(list)
    for i, p in enumerate(pairs):
        groups[(os.path.dirname(p["dup"]), os.path.dirname(p["kept"]))].append(i)
    glist = sorted(({"dup_dir": k[0], "kept_dir": k[1], "pairs": v, "bytes": sum(pairs[i]["bytes"] for i in v)}
                    for k, v in groups.items()), key=lambda g: -g["bytes"])
    json.dump({"built": time.strftime("%Y-%m-%d %H:%M"), "fresh_scan": True, "files_scanned": n,
               "pairs": pairs, "groups": glist}, open(OUT, "w", encoding="utf-8"))
    print(f"wrote {len(pairs)} pairs")


if __name__ == "__main__":
    main()
