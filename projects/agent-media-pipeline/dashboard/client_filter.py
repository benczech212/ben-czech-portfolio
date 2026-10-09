"""Write the NAS uploader's rclone filter (UPLOAD_FILTER in config.py, mounted as /filter.txt by
nas/docker/rclone-uploader/compose.yaml).

Skips temporary and system files, plus only the clips the DXV conversion hasn't finished yet (their original and
their future DXV name), so a clip goes to Google Drive once, already in its final DXV form. Converted clips and
originals that are kept upload normally. The dashboard server rewrites it whenever the number of clips still
waiting changes. The conversion queue (DXV_QUEUE) comes from the DXV conversion step, which is not part of this copy.
"""
import csv, os, re
import config

QUEUE = config.DXV_QUEUE
LIB = os.path.join(config.CLIENT_LIBRARY, "")   # with a trailing separator
OUT = config.UPLOAD_FILTER


def esc(s):
    return re.sub(r"([*?\[\]{}\\])", r"\\\1", s)


def write():
    rows = list(csv.DictReader(open(QUEUE, encoding="utf-8")))
    out = ["# written by client_filter.py; clips still waiting for DXV conversion are skipped until converted",
           "- *.dxv-tmp.mov", "- ._*", "- .DS_Store", "- Thumbs.db"]
    waiting = 0
    for r in rows:
        if r["queue"] != "ffmpeg_dxt1" or r["status"] not in ("pending", "anyway", "encoded"):
            continue
        if not os.path.normcase(r["path"]).startswith(os.path.normcase(LIB)):
            continue   # not in the uploaded library
        waiting += 1
        rel = r["path"][len(LIB):].replace("\\", "/")
        base = os.path.splitext(rel)[0]
        for p in sorted({rel, base + ".mov", base + " (DXV).mov"}):
            out.append("- /" + esc(p))
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, OUT)
    return waiting


if __name__ == "__main__":
    print("clips held back until converted:", write())
