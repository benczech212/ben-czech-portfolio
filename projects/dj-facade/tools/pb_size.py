"""Compile the Facade Bus pattern OFFLINE and print its bytecode size - without touching the Pixelblaze.

  python tools/pb_size.py                 # patterns/facade_bus.js with the zone + mirror tables baked in
  python tools/pb_size.py some_pattern.js # any pattern file as is

Why: the Pixelblaze loads the compiled pattern into a fixed buffer. Measured 2026-10-07 on fw 3.51:
37.9 KB loaded, 40.8 KB made it fall back to another pattern (and reboot once), so keep Facade Bus
under ~38 KB. `mem` in the stats is a different budget (free runtime array memory, in words).
Uses the cached web-UI compiler (`.cache/pixelblaze-ui-<ver>.html.gz`, filled by tools/pb_compile.py
at the first deploy) through pixelblaze-client's compiler path, with the device calls stubbed out.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from pixelblaze import Pixelblaze  # noqa: E402

LIMIT_KB = 38.0


def cached_ui():
    files = sorted((ROOT / ".cache").glob("pixelblaze-ui-*.html.gz"))
    if not files:
        raise SystemExit("no cached compiler in .cache/ - deploy once (tools/pb_compile.py fills it)")
    return files[-1]


def compile_offline(src: str) -> bytes:
    ui = cached_ui()
    version = ui.stem.split("-")[-1].replace(".html", "")
    pb = Pixelblaze.__new__(Pixelblaze)            # no connection: just the compiler helpers
    pb.getVersion = lambda: float(version)
    pb.getFile = lambda name: ui.read_bytes() if name == "/index.html.gz" else b""
    return pb.compilePattern(src)


def main():
    if len(sys.argv) > 1:
        src = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
        label = sys.argv[1]
    else:
        import facade_bridge
        src = facade_bridge.pattern_source()
        label = "patterns/facade_bus.js (tables baked)"
    try:
        blob = compile_offline(src)
    except ValueError as e:                        # pixelblaze-client raises ValueError on a compile error
        print(f"{label}: {e}")
        return 1
    kb = len(blob) / 1024
    verdict = "ok" if kb < LIMIT_KB - 0.5 else "TIGHT" if kb < LIMIT_KB else "TOO BIG - will not load"
    print(f"{label}: {len(blob)} bytes ({kb:.1f} KB) - {verdict} (limit ~{LIMIT_KB:.0f} KB)")
    return 0 if kb < LIMIT_KB else 1


if __name__ == "__main__":
    sys.exit(main())
