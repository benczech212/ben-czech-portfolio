"""Build dxv_review.json and dupes_review.json (the inputs of delete_approved.py). Read-only: only lists folders on
the NAS and reads three CSVs in WORK_DIR written by earlier steps that are not part of this copy:
  move_plan.csv         the library rebuild: source, destination, action (keep/duplicate), duplicate_of, bytes
  dxv_queue.csv         the DXV conversion queue: path, queue, status, codec, width, height, duration, size
  deleted_damaged.csv   files removed as damaged (truncated), one path per row

  python dashboard/build_review_data.py
"""
import csv, json, os, time
from collections import defaultdict
import config

HERE = os.path.dirname(os.path.abspath(__file__))
SCR = config.WORK_DIR
LIB = config.CLIENT_LIBRARY
DUPDIR = config.DUPES_DIR
PARKED = os.path.join(DUPDIR, "_replaced by DXV")
SRC_ROOT = config.DUPES_SOURCE_ROOT


def walk(root, skip=()):
    out = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if os.path.join(dp, d) not in skip]
        for f in fns:
            p = os.path.join(dp, f)
            try:
                out[p] = os.path.getsize(p)
            except OSError:
                pass
    return out


def main():
    t0 = time.time()
    lib = walk(LIB)
    parked = walk(PARKED)
    dups_on_disk = walk(DUPDIR, skip={PARKED, os.path.join(DUPDIR, "_junk")})
    print(f"listed {len(lib)} library files, {len(parked)} parked originals, {len(dups_on_disk)} duplicates "
          f"in {time.time() - t0:.0f}s")

    by_name_size = defaultdict(list)
    for p, s in lib.items():
        by_name_size[(os.path.basename(p).lower(), s)].append(p)

    # --- DXV conversions: parked original <-> new DXV file -------------------------------------
    parked_by_stem = {}
    for p in parked:
        rel = os.path.relpath(p, PARKED)
        parked_by_stem[os.path.splitext(rel)[0].lower()] = p
    queue = list(csv.DictReader(open(config.DXV_QUEUE, encoding="utf-8")))
    dxv, converted_from = [], {}  # converted_from: original library path (lower) -> DXV path
    for r in queue:
        if r["queue"] != "ffmpeg_dxt1":
            continue
        st = r["status"].split(":")[0]
        if st == "replaced":
            new = r["path"]
            stem = os.path.splitext(os.path.relpath(new, LIB))[0]
            orig = parked_by_stem.get(stem.lower()) or parked_by_stem.get(stem.removesuffix(" (DXV)").lower())
            if orig:
                converted_from[os.path.join(LIB, os.path.relpath(orig, PARKED)).lower()] = new
            dxv.append({"kind": "converted", "dxv": new, "orig": orig, "codec": r["codec"],
                        "res": f"{r['width']}x{r['height']}", "dur": float(r["duration"] or 0),
                        "orig_bytes": parked.get(orig, int(r["size"] or 0)), "dxv_bytes": lib.get(new)})
        elif st in ("verify_failed", "encode_failed"):
            dxv.append({"kind": "kept", "orig": r["path"], "dxv": None, "codec": r["codec"],
                        "res": f"{r['width']}x{r['height']}", "dur": float(r["duration"] or 0),
                        "orig_bytes": lib.get(r["path"], int(r["size"] or 0)),
                        "reason": r["status"].split(":", 1)[1].strip()})
    dxv.sort(key=lambda x: (x["kind"], (x["dxv"] or x["orig"] or "").lower()))

    # --- Duplicates: parked duplicate <-> the copy that was kept in the library -----------------
    plan = list(csv.DictReader(open(os.path.join(SCR, "move_plan.csv"), encoding="utf-8")))
    keep_dest = {os.path.relpath(r["source"], SRC_ROOT).lower(): r["destination"]
                 for r in plan if r["action"] == "keep"}
    damaged = {row[0].lower() for row in csv.reader(open(os.path.join(SCR, "deleted_damaged.csv"), encoding="utf-8")) if row}
    pairs, unresolved = [], 0
    for r in plan:
        if r["action"] != "duplicate":
            continue
        dup, size = r["destination"], int(r["bytes"])
        kept, note = keep_dest.get(r["duplicate_of"].lower()), ""
        if kept and kept not in lib:
            conv = converted_from.get(kept.lower())
            if kept.lower() in damaged:
                note = "kept copy was deleted as damaged (truncated); this duplicate is the same broken file"
            elif conv:
                note = "kept copy was since converted to DXV (its pre-DXV original is parked in _replaced by DXV)"
                kept = conv
            else:
                hits = by_name_size.get((os.path.basename(kept).lower(), size), [])
                if len(hits) == 1:
                    kept, note = hits[0], "kept copy was renamed/moved since"
                else:
                    note, kept = "kept copy not found - do not delete without checking", kept
                    unresolved += 1
        pairs.append({"dup": dup, "kept": kept, "bytes": size, "dup_exists": dup in dups_on_disk,
                      "kept_exists": kept in lib, "note": note,
                      "was": os.path.join(SRC_ROOT, r["source"][len(SRC_ROOT) + 1:]),
                      "kept_was": os.path.join(SRC_ROOT, r["duplicate_of"])})
    groups = defaultdict(list)
    for i, p in enumerate(pairs):
        groups[(os.path.dirname(p["dup"]), os.path.dirname(p["kept"] or ""))].append(i)
    glist = sorted(({"dup_dir": k[0], "kept_dir": k[1], "pairs": v, "bytes": sum(pairs[i]["bytes"] for i in v)}
                    for k, v in groups.items()), key=lambda g: -g["bytes"])

    stamp = time.strftime("%Y-%m-%d %H:%M")
    json.dump({"built": stamp, "items": dxv}, open(os.path.join(HERE, "dxv_review.json"), "w", encoding="utf-8"))
    json.dump({"built": stamp, "pairs": pairs, "groups": glist},
              open(os.path.join(HERE, "dupes_review.json"), "w", encoding="utf-8"))
    print(f"dxv: {sum(x['kind'] == 'converted' for x in dxv)} converted "
          f"({sum(1 for x in dxv if x['kind'] == 'converted' and not x['orig'])} without a parked original), "
          f"{sum(x['kind'] == 'kept' for x in dxv)} kept")
    print(f"dupes: {len(pairs)} pairs in {len(glist)} folder groups, "
          f"{sum(not p['dup_exists'] for p in pairs)} duplicates no longer on disk, {unresolved} unresolved kept copies")


if __name__ == "__main__":
    main()
