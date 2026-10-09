"""Delete the files of titles marked Drop in the Plex review.

  python dashboard/delete_plex_drops.py          dry run: lists what would go, deletes nothing
  python dashboard/delete_plex_drops.py --go     delete, logging every file to deleted_log.csv

Safety rules:
  - a file that any kept title also uses is never deleted
  - files that look like a *different* film filed under a dropped title (relation "wrong"/"other") are left alone
  - nothing on the failing drive (FAILING_DRIVE in config.py) is touched; its rescued copies under RESCUE_ROOT are
    deleted instead
  - every path must sit inside SAFE_ROOTS: the Plex library folders the review was built from (from
    plex_review.json) and their rescued copies. Anything else stops the run before a single file is removed.
"""
import csv, json, os, sys, time
from collections import defaultdict
import config

HERE = os.path.dirname(os.path.abspath(__file__))
RESCUE = os.path.normpath(config.RESCUE_ROOT)
FAILING = config.FAILING_DRIVE.rstrip("\\/").upper()     # e.g. "E:"
LOG = os.path.join(HERE, "deleted_log.csv")


def on_failing(p):
    return p[:len(FAILING)].upper() == FAILING


def rescued(p):
    """Where the rescue put a file from the failing drive: E:\\x\\y -> RESCUE_ROOT\\x\\y."""
    return RESCUE + p[len(FAILING):]


def safe_roots(review):
    """The library folders the review covers (Plex's own section roots); failing-drive roots map to the rescue copy."""
    roots = []
    for r in review.get("roots") or []:
        r = os.path.normpath(r)
        roots.append(rescued(r) if on_failing(r) else r)
    return roots


def item_files(it, F):
    """(path, deletable) for every file of a movie/show."""
    out = []
    if it["kind"] == "movie":
        for v in it["versions"]:
            ok = v["relation"] not in ("wrong", "other")
            for cp in v["copies"]:
                out += [(F[i], ok) for i in cp]
    else:
        seen = set()
        for r in it["files"]:
            seen.add(r[2]); out.append((F[r[2]], True))
        for e in it["dup_eps"]:
            for v in e["versions"]:
                for cp in v["copies"]:
                    out += [(F[i], True) for i in cp if i not in seen]
    return out


def main(go):
    d = json.load(open(os.path.join(HERE, "plex_review.json"), encoding="utf-8"))
    dec = json.load(open(os.path.join(HERE, "decisions.json"), encoding="utf-8"))["plex"]
    F = d["files"]
    roots = safe_roots(d)
    SAFE_ROOTS = tuple(os.path.normcase(os.path.join(r, "")) for r in roots)
    assert SAFE_ROOTS, "refusing: plex_review.json lists no library roots"
    items = [("m:%d" % m["id"], m) for m in d["movies"]] + [("s:%d" % s["id"], s) for s in d["shows"]]
    kept_files = set()
    for k, it in items:
        if dec.get(k) != "drop":
            kept_files.update(p.lower() for p, _ in item_files(it, F))
    todo, skipped = [], defaultdict(list)
    for k, it in items:
        if dec.get(k) != "drop":
            continue
        for p, ok in item_files(it, F):
            if not ok:
                skipped["looks like a different film; left for you to check"].append(p); continue
            if p.lower() in kept_files:
                skipped["also used by a kept title"].append(p); continue
            t = rescued(p) if on_failing(p) else p
            if os.path.exists(t):
                todo.append((t, os.path.getsize(t), it["title"]))
            elif not on_failing(p):
                skipped["already gone"].append(t)
    todo = list({t[0].lower(): t for t in todo}.values())

    bad = [t for t, _, _ in todo if on_failing(t) or not os.path.normcase(os.path.normpath(t)).startswith(SAFE_ROOTS)]
    assert not bad, f"refusing: {len(bad)} path(s) outside {roots} or on {FAILING}, e.g. {bad[:3]}"

    by_drive = defaultdict(int)
    for t, s, _ in todo:
        by_drive[os.path.splitdrive(t)[0].upper()] += s
    print(f"to delete: {len(todo)} files, {sum(s for _, s, _ in todo) / 1e9:.0f} GB "
          + "(" + ", ".join(f"{k} {v / 1e9:.0f} GB" for k, v in sorted(by_drive.items())) + ")")
    for why, lst in skipped.items():
        print(f"skipped ({why}): {len(lst)}")
        for p in lst[:6]:
            print("    ", p)
    if not go:
        print("dry run - nothing deleted"); return
    n = 0
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for t, s, title in todo:
            try:
                os.remove(t)
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), t, s, f"Plex review: dropped '{title}'"]); n += 1
            except FileNotFoundError:
                pass
            except OSError as e:
                print("FAILED", t, e)
    # remove folders left empty inside the library roots and their rescued copies (never on the failing drive)
    pruned = 0
    for root in roots:
        for dp, dns, fns in os.walk(root, topdown=False):
            if dp != root and not os.listdir(dp):
                try:
                    os.rmdir(dp); pruned += 1
                except OSError:
                    pass
    print(f"deleted {n} files; removed {pruned} empty folders; log: {LOG}")


if __name__ == "__main__":
    main("--go" in sys.argv)
