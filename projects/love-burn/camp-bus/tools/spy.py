#!/usr/bin/env python3
"""Bus monitor: prints every message (or a topic filter) with a timestamp.

    python tools/spy.py --host localhost               # everything
    python tools/spy.py --host localhost -t 'audio/#'  # one subtree
    python tools/spy.py --host localhost --no-energy   # everything except the 20 Hz firehose
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients", "python"))
from campbus import Node  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("CAMPBUS_HOST", "campbus.local"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("CAMPBUS_PORT", "1883")))
    ap.add_argument("-t", "--topic", default="#", help="MQTT topic filter (default: everything)")
    ap.add_argument("--no-energy", action="store_true", help="hide audio/energy")
    a = ap.parse_args()

    node = Node("spy_" + str(os.getpid()), host=a.host, port=a.port, fw="spy")

    @node.on(a.topic)
    def show(topic, msg):
        if a.no_energy and topic == "audio/energy":
            return
        print(f"{time.strftime('%H:%M:%S')}  {topic:<32} {json.dumps(msg, separators=(',', ':'))}")

    node.connect()
    print(f"spy -> {a.host}:{a.port}  filter={a.topic!r}  (Ctrl-C to stop)")
    node.wait()


if __name__ == "__main__":
    main()
