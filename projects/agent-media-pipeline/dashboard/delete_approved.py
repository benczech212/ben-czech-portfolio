"""Delete what was approved in the dashboard's review: every parked duplicate (its kept copy stays in the library)
and every original parked by the DXV conversion (its verified DXV stays in the library).

  python dashboard/delete_approved.py          dry run: checks everything, deletes nothing
  python dashboard/delete_approved.py --go     delete, logging each file to deleted_log.csv

A file is only deleted if its kept counterpart is still present, and a pair marked "keep both" / "swap" /
"restore original" in decisions.json is always left alone. Only files inside DUPES_DIR (config.py), where the
earlier steps parked them, can be deleted: anything else stops the run before a single file is removed.
Inputs: dupes_review.json and, if present, dxv_review.json, both from build_review_data.py. (A fresh scan by
check_library_dupes.py lists duplicates still inside the library; this script refuses those until they are parked.)
"""
import csv, json, os, sys, time
import config

HERE = os.path.dirname(os.path.abspath(__file__))
DUPDIR = os.path.normpath(config.DUPES_DIR)
SAFE_ROOTS = (os.path.normcase(os.path.join(DUPDIR, "")),)
LOG = os.path.join(HERE, "deleted_log.csv")


def size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return None


def load(name, key):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return []
    return json.load(open(p, encoding="utf-8"))[key]


def main(go):
    dec = json.load(open(os.path.join(HERE, "decisions.json"), encoding="utf-8"))
    dx = load("dxv_review.json", "items")
    dp = load("dupes_review.json", "pairs")
    # kept copies later converted in place (same name): DXV path -> size of the original it was made from
    dxv_src_size = {it["dxv"].lower(): size(it["orig"]) for it in dx if it["kind"] == "converted" and it["orig"]}

    todo, skipped = [], []
    for p in dp:
        d = dec.get("dupes", {}).get(p["dup"])
        if d and d != "remove":
            skipped.append((p["dup"], f"marked {d}")); continue
        s = size(p["dup"])
        if s is None:
            skipped.append((p["dup"], "already gone")); continue
        if s != p["bytes"]:
            skipped.append((p["dup"], f"size changed {p['bytes']} -> {s}")); continue
        if "deleted as damaged" in p["note"]:
            todo.append((p["dup"], s, "duplicate of a damaged (truncated) file")); continue
        ks = size(p["kept"])
        if ks is None:
            skipped.append((p["dup"], "kept copy missing")); continue
        if ks != s and "converted to DXV" not in p["note"] and dxv_src_size.get(p["kept"].lower()) != s:
            skipped.append((p["dup"], f"kept copy size differs ({ks})")); continue
        todo.append((p["dup"], s, "duplicate of " + p["kept"]))
    for it in dx:
        if it["kind"] != "converted":
            continue
        d = dec.get("dxv", {}).get(it["dxv"])
        if d and d != "ok":
            skipped.append((it["orig"], f"marked {d}")); continue
        s, ds = size(it["orig"]), size(it["dxv"])
        if s is None:
            skipped.append((it["orig"], "already gone")); continue
        if not ds:
            skipped.append((it["orig"], "DXV file missing")); continue
        todo.append((it["orig"], s, "replaced by DXV " + it["dxv"]))

    bad = [t for t in todo if not os.path.normcase(os.path.normpath(t[0])).startswith(SAFE_ROOTS)]
    if bad:   # not an assert: python -O would skip it
        sys.exit(f"refusing: paths outside {DUPDIR}: {bad[:3]}")
    print(f"to delete: {len(todo)} files, {sum(t[1] for t in todo) / 1e9:.1f} GB; skipped {len(skipped)}")
    for p, why in skipped[:20]:
        print(f"  skip: {why}: {p}")
    if not go:
        print("dry run - nothing deleted"); return

    n = 0
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for p, s, why in todo:
            try:
                os.remove(p)
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), p, s, why]); n += 1
            except OSError as e:
                print(f"  FAILED {p}: {e}")
            # AppleDouble sidecar left by Macs, if any
            side = os.path.join(os.path.dirname(p), "._" + os.path.basename(p))
            if os.path.exists(side):
                try:
                    os.remove(side)
                except OSError:
                    pass
    # prune now-empty folders (not _junk, which was not part of this approval)
    pruned = 0
    for dp_, dns, fns in os.walk(DUPDIR, topdown=False):
        if dp_ == DUPDIR or dp_.startswith(os.path.join(DUPDIR, "_junk")):
            continue
        try:
            os.rmdir(dp_); pruned += 1
        except OSError:
            pass
    print(f"deleted {n} files, removed {pruned} empty folders; log: {LOG}")


if __name__ == "__main__":
    main("--go" in sys.argv)
