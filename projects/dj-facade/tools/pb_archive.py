"""Snapshot everything on a Pixelblaze into a timestamped archive folder.

Produces:
  backup.pbb            full binary backup, restorable via the Pixelblaze UI or pb.restoreFromBackup()
  config.json           device settings (pixel count, LED type, brightness, ...)
  sequencer.json        active pattern / sequencer state
  playlist.json         default playlist
  map/pixelmap.txt      mapper source as shown in the Mapper tab
  map/coordinates.json  compiled map coordinates (normalized 0..1)
  patterns/*.epe        each pattern as an importable .epe
  patterns/*.js         each pattern's source code, for reading and diffing
  manifest.json         index of what was saved

Usage:
  python tools/pb_archive.py [--ip <address>] [--out archive]
"""

import argparse
import base64
import datetime
import json
import os
import pathlib
import re
import sys

from pixelblaze import Pixelblaze

DEFAULT_IP = os.environ.get("PIXELBLAZE_IP")   # no default: set it or pass --ip


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "unnamed"


def write_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def archive(ip: str, out_root: pathlib.Path) -> pathlib.Path:
    with Pixelblaze(ip) as pb:
        config = pb.getConfigSettings()
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out = out_root / f"{stamp}_{safe_name(config.get('name', ip))}"
        out.mkdir(parents=True, exist_ok=True)
        print(f"Archiving {config.get('name')} ({ip}) -> {out}")

        pb.saveBackup(str(out / "backup.pbb"))
        print("  backup.pbb")

        write_json(out / "config.json", config)
        write_json(out / "sequencer.json", pb.getConfigSequencer())
        try:
            write_json(out / "playlist.json", pb.getSequencerPlaylist())
        except Exception as e:  # older firmware has no playlist endpoint
            print(f"  playlist skipped: {e}")

        map_fn = pb.getMapFunction()
        if map_fn:
            (out / "map").mkdir(exist_ok=True)
            (out / "map" / "pixelmap.txt").write_text(map_fn, encoding="utf-8")
        try:
            write_json(out / "map" / "coordinates.json", pb.getMapCoordinates())
        except Exception as e:
            print(f"  map coordinates skipped: {e}")
        print("  config, sequencer, playlist, map")

        patterns = pb.getPatternList(forceRefresh=True)
        manifest = {"device": config.get("name"), "ip": ip, "firmware": config.get("ver"),
                    "archived": stamp, "patterns": []}
        pdir = out / "patterns"
        pdir.mkdir(exist_ok=True)
        used = set()
        for pid, name in sorted(patterns.items(), key=lambda kv: kv[1].lower()):
            base = safe_name(name)
            if base.lower() in used:  # duplicate names exist on-device
                base = f"{base}__{pid}"
            used.add(base.lower())
            sources = json.loads(pb.getPatternSourceCode(pid))
            epe = {"name": name, "id": pid, "sources": sources,
                   "preview": base64.b64encode(pb.getPreviewImage(pid) or b"").decode("ascii")}
            write_json(pdir / f"{base}.epe", epe)
            (pdir / f"{base}.js").write_text(sources.get("main", ""), encoding="utf-8")
            manifest["patterns"].append({"id": pid, "name": name, "file": f"patterns/{base}.epe"})
            print(f"  pattern: {name}")

        write_json(out / "manifest.json", manifest)
        print(f"Done: {len(patterns)} patterns.")
        return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ip", default=DEFAULT_IP, required=not DEFAULT_IP, help="Pixelblaze address (or set PIXELBLAZE_IP)")
    ap.add_argument("--out", default=str(pathlib.Path(__file__).resolve().parent.parent / "archive"))
    args = ap.parse_args()
    archive(args.ip, pathlib.Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
