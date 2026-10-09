#!/usr/bin/env python3
"""The matrix panel, in a terminal, on a real bus.

Same `effects.py` the board runs, same subscriptions, rendered with half-block
characters instead of LEDs. Two reasons it exists:

  * **Test the wiring before flashing anything.** Mapping rules, colours and fill
    directions can all be got right here, and the board then only has to be plugged in.
  * **Debug from the PC when the panel misbehaves.** If the terminal shows the fill
    and the panel does not, the problem is the board; if neither does, the problem is
    a mapping rule. That is a much shorter argument than staring at LEDs.

    python nodes/matrix_portal/preview.py --host campbus.local
    python nodes/matrix_portal/preview.py --host campbus.local --demo
    python nodes/matrix_portal/preview.py --host campbus.local --send wipe color=#00c8ff heading=45

It joins the bus under its own node id, so it can run next to the real panel without
either of them kicking the other off the broker.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "clients", "python"))

import effects  # noqa: E402
from campbus import Node  # noqa: E402

RESET = "\x1b[0m"
HIDE, SHOW = "\x1b[?25l", "\x1b[?25h"
HOME = "\x1b[H"
CLEAR = "\x1b[2J"


def enable_ansi() -> None:
    """Windows consoles need to be told they can do this."""
    if os.name != "nt":
        return
    try:
        import ctypes

        k = ctypes.windll.kernel32
        k.SetConsoleMode(k.GetStdHandle(-11), 7)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def render(engine, width, height, now) -> str:
    """Two panel rows per terminal row: foreground is the top pixel, background the
    bottom, and the half block sits between them."""
    pal = [tuple(int(c * engine.brightness * 255) for c in rgb)
           for rgb in engine.palette(now)]
    proj = engine.projection_cache(width, height)
    out = []
    for y in range(0, height - 1, 2):
        row = []
        prev = None
        for x in range(width):
            top = pal[proj[y * width + x]]
            bot = pal[proj[(y + 1) * width + x]]
            pair = (top, bot)
            if pair != prev:
                row.append("\x1b[38;2;%d;%d;%dm\x1b[48;2;%d;%d;%dm" % (top + bot))
                prev = pair
            row.append("▀")
        out.append("".join(row) + RESET)
    return "\n".join(out)


def demo(node, target, delay=1.6):
    """Every direction, then the effects that are not directional. Enough to see that
    the panel is a real responder and not just a light."""
    colors = ["#ff4000", "#00c8ff", "#8b5cf6", "#22c55e", "#facc15", "#ec4899",
              "#ff8a3d", "#5fb8ff"]
    print("demo: eight fills, then a sweep, a flash and a charge")
    for i in range(8):
        heading = i * 45
        node.publish("cmd/%s/wipe" % target,
                     {"color": colors[i], "intensity": 1.0, "heading": heading,
                      "seconds": 1.0, "from": "preview"})
        print("  wipe heading %3d  %s" % (heading, colors[i]))
        time.sleep(delay)
    node.publish("cmd/%s/sweep" % target,
                 {"color": "#ffffff", "heading": 90, "seconds": 1.2, "from": "preview"})
    time.sleep(delay)
    node.publish("cmd/%s/flash" % target,
                 {"color": "#ffffff", "ms": 1200, "from": "preview"})
    time.sleep(delay)
    node.publish("cmd/%s/charge" % target,
                 {"color": "#22c55e", "intensity": 0.9, "from": "preview"})
    print("demo done")


def parse_kv(pairs):
    msg = {}
    for pair in pairs:
        if "=" not in pair:
            continue
        key, _, raw = pair.partition("=")
        try:
            msg[key] = float(raw) if "." in raw else int(raw)
        except ValueError:
            msg[key] = raw
    return msg


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Terminal preview of the matrix panel")
    p.add_argument("--host", default=None, help="broker (default $CAMPBUS_HOST)")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--node-id", default="matrix_preview")
    p.add_argument("--target", default="matrix", help="the cmd/<target>/ it answers")
    p.add_argument("--width", type=int, default=64)
    p.add_argument("--height", type=int, default=32)
    p.add_argument("--north", type=float, default=0.0,
                   help="compass bearing of the panel's top edge")
    p.add_argument("--fps", type=float, default=25.0)
    p.add_argument("--demo", action="store_true", help="publish a tour of every effect")
    p.add_argument("--send", nargs="+", metavar="EFFECT|K=V",
                   help="publish one command and exit, e.g. --send wipe color=#00c8ff heading=45")
    args = p.parse_args(argv)

    kwargs = {"fw": "matrix-preview-0.1", "port": args.port}
    if args.host:
        kwargs["host"] = args.host
    node = Node(args.node_id, **kwargs).connect()

    if args.send:
        effect, rest = args.send[0], args.send[1:]
        msg = parse_kv(rest)
        msg.setdefault("from", "preview")
        node.wait_ready(5.0)
        node.publish("cmd/%s/%s" % (args.target, effect), msg)
        print("cmd/%s/%s  %s" % (args.target, effect, msg))
        time.sleep(0.5)
        node.close()
        return 0

    engine = effects.Engine(north=args.north)
    _attach_projection_cache(engine)

    node.subscribe("cmd/%s/#" % args.target,
                   lambda topic, msg: engine.submit(topic.rsplit("/", 1)[-1], msg, time.monotonic()))
    node.subscribe("cmd/all/#",
                   lambda topic, msg: engine.submit(topic.rsplit("/", 1)[-1], msg, time.monotonic()))

    def on_map(topic, msg):
        if msg.get("lat") is not None and msg.get("lon") is not None:
            engine.set_position(msg["lat"], msg["lon"])
    node.subscribe("map/%s" % args.node_id, on_map)

    if not node.wait_ready(5.0):
        print("[bus] no broker yet at %s:%d - waiting" % (node.host, node.port), file=sys.stderr)

    enable_ansi()
    if args.demo:
        import threading
        threading.Thread(target=demo, args=(node, args.target), daemon=True).start()

    sys.stdout.write(HIDE + CLEAR)
    period = 1.0 / max(1.0, args.fps)
    try:
        while True:
            now = time.monotonic()
            frame = render(engine, args.width, args.height, now)
            sys.stdout.write(HOME + frame + "\n" +
                             "  %s  %s  received %d  ignored %d   ctrl-c to stop%s\n"
                             % (args.target,
                                "on the bus" if node.connected else "broker offline",
                                engine.received, engine.ignored, RESET))
            sys.stdout.flush()
            time.sleep(period)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW + RESET + "\n")
        node.close()
    return 0


def _attach_projection_cache(engine):
    """The board keeps its pixel maps in displayio bitmaps; here they are plain bytes.
    Same `effects.projection`, so the preview and the panel cannot disagree."""
    cache = {}

    def projection_cache(width, height):
        key = (width, height, engine.bucket)
        if key not in cache:
            cache[key] = effects.projection(width, height, engine.bucket * 45.0)
        return cache[key]

    engine.projection_cache = projection_cache


if __name__ == "__main__":
    sys.exit(main())
