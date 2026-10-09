#!/usr/bin/env python3
"""Mock receiving devices - a virtual camp that prints what it would do.

Each mock is a real bus node (status, last-will, counters) that subscribes to
exactly what the real device will, and prints one line per effect instead of
driving LEDs/screens/Resolume. Use them to see a workflow land, and in tests
via `build_devices()` which also records everything received.

    python sim/devices.py --host localhost            # run the whole virtual camp
    python sim/devices.py --host localhost --beats    # also print every beat (noisy)

Each mock also serves its own console (campbus.console) on 8101+, the way a real device
is expected to: health, subscriptions, live traffic and a couple of debug controls. The
port is published as `ui` in the node's retained status, so the Devices page links to it
without being told. `--no-consoles` turns them off; they are off by default when tests
call build_devices(), so a test run never binds a port.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "clients", "python"))
from campbus import Node, console  # noqa: E402

BAR = "▁▂▃▄▅▆▇█"
CONSOLE_PORT_BASE = 8101      # one console per mock, in build_devices() order


def _bar(x: float, width: int = 8) -> str:
    x = max(0.0, min(1.0, float(x or 0)))
    full = int(x * width)
    return "█" * full + "░" * (width - full)


class MockDevice:
    """A bus node that logs effects. Subclasses declare `subs` = [(topic_filter, handler_name)]."""

    icon = "?"
    role = "responder"
    subs: List[Tuple[str, str]] = []

    def __init__(self, node_id: str, host: Optional[str], port: Optional[int], fw: str,
                 beats: bool = False, quiet: bool = False, console_port: Optional[int] = None):
        kw: Dict[str, Any] = {}
        if host:
            kw["host"] = host
        if port:
            kw["port"] = port
        self.node = Node(node_id, fw=fw, role=self.role, **kw)
        self.console_port = console_port
        self.last_effect: Optional[str] = None
        self.beats = beats
        self.quiet = quiet
        self.received: List[Tuple[float, str, Dict[str, Any]]] = []   # (perf_counter, topic, msg) for tests
        self._lock = threading.Lock()
        for topic, handler in self.subs:
            self.node.subscribe(topic, self._wrap(getattr(self, handler)))

    def _wrap(self, fn: Callable[[str, Dict[str, Any]], None]):
        def h(topic: str, msg: Dict[str, Any]) -> None:
            with self._lock:
                self.received.append((time.perf_counter(), topic, msg))
                if len(self.received) > 2000:
                    del self.received[:1000]
            fn(topic, msg)
        return h

    def effect(self, text: str, interaction: bool = True) -> None:
        self.last_effect = text
        if interaction:
            self.node.count()
        if not self.quiet:
            print(f"{time.strftime('%H:%M:%S')} {self.icon} {self.node.node_id:<18} {text}", flush=True)

    def start(self) -> "MockDevice":
        if self.console_port:
            console.serve(self.node, port=self.console_port, title=f"{self.node.node_id} console",
                          actions={"Identify": self._identify, "Self-test": self._self_test})
        self.node.connect()
        return self

    # ---- console controls. A real device would light an LED or ring a bell; a mock says so
    # on the devices log, which is exactly what the virtual camp is for.
    def _identify(self) -> str:
        self.effect("IDENTIFY - a real device would light up now", interaction=False)
        return f"{self.node.node_id} identified on the devices log"

    def _self_test(self) -> str:
        self.effect("SELF-TEST - local white flash, nothing published", interaction=False)
        return "ran a local self-test; nothing was published to the bus"

    def stop(self) -> None:
        self.node.close()

    def seen(self, topic_filter: str, since: float = 0.0) -> List[Dict[str, Any]]:
        import paho.mqtt.client as mqtt
        with self._lock:
            return [m for (t, top, m) in self.received if t >= since and mqtt.topic_matches_sub(topic_filter, top)]


class MockCrystal(MockDevice):
    """ESP32 crystal: flashes on beats, answers cmd/crystals/* and cmd/all/*."""
    icon = "\U0001F48E"
    subs = [("audio/beat", "on_beat"), ("cmd/crystals/#", "on_cmd"), ("cmd/all/#", "on_cmd")]

    def on_beat(self, topic, msg):
        n = msg.get("n", 0)
        if self.beats or n % 16 == 0:
            self.effect(f"beat {n} {'DOWNBEAT' if n % 4 == 0 else ''} bpm={msg.get('bpm')}", interaction=False)

    def on_cmd(self, topic, msg):
        effect = topic.rsplit("/", 1)[-1]
        self.effect(f"{effect.upper()} {msg}")


class MockHallway(MockDevice):
    """Space hallway bridge Pi: bass meter from audio/energy, answers cmd/hallway/* and cmd/all/*."""
    icon = "\U0001F30C"
    subs = [("audio/energy", "on_energy"), ("cmd/hallway/#", "on_cmd"), ("cmd/all/#", "on_cmd")]
    _last_meter = 0.0

    def on_energy(self, topic, msg):
        now = time.monotonic()
        if now - self._last_meter >= 2.0:
            self._last_meter = now
            self.effect(f"bass {_bar(msg.get('bass'))} mid {_bar(msg.get('mid'))} high {_bar(msg.get('high'))}",
                        interaction=False)

    def on_cmd(self, topic, msg):
        effect = topic.rsplit("/", 1)[-1]
        self.effect(f"{effect.upper()} {msg}")


class MockMycelium(MockDevice):
    """Sonic Mycelium Cluster bridge: the UV beds under the mushrooms answer cmd/mycelium/* and cmd/all/*."""
    icon = "\U0001F344"
    role = "instrument"
    subs = [("cmd/mycelium/#", "on_cmd"), ("cmd/all/#", "on_cmd")]

    def on_cmd(self, topic, msg):
        effect = topic.rsplit("/", 1)[-1]
        self.effect(f"{effect.upper()} {msg}")


class MockGeode(MockDevice):
    """Crystal Geode Synth bridge: the COB RGBW inside each geode answers cmd/geode/* and cmd/all/*."""
    icon = "\U0001F48E"
    role = "instrument"
    subs = [("cmd/geode/#", "on_cmd"), ("cmd/all/#", "on_cmd")]

    def on_cmd(self, topic, msg):
        effect = topic.rsplit("/", 1)[-1]
        self.effect(f"{effect.upper()} {msg}")


class MockPlant(MockDevice):
    """Monstera Piezo Plant bridge: the LED filaments in the leaves answer cmd/plant/* and cmd/all/*."""
    icon = "\U0001F33F"
    role = "instrument"
    subs = [("cmd/plant/#", "on_cmd"), ("cmd/all/#", "on_cmd")]

    def on_cmd(self, topic, msg):
        effect = topic.rsplit("/", 1)[-1]
        self.effect(f"{effect.upper()} {msg}")


class MockTubes(MockDevice):
    """Water Chord Tubes bridge: the RGB puck under each tube answers cmd/tubes/* and cmd/all/*."""
    icon = "\U0001F4A7"
    role = "instrument"
    subs = [("cmd/tubes/#", "on_cmd"), ("cmd/all/#", "on_cmd")]

    def on_cmd(self, topic, msg):
        effect = topic.rsplit("/", 1)[-1]
        self.effect(f"{effect.upper()} {msg}")


class MockDisplay(MockDevice):
    """Entrance now-playing screen: renders booth/dj + display/* from retained state."""
    icon = "\U0001F4FA"
    subs = [("booth/dj", "on_dj"), ("display/#", "on_display")]

    def on_dj(self, topic, msg):
        artist = msg.get("artist")
        self.effect(f"NOW PLAYING: {artist}" if artist else "NOW PLAYING: (booth empty)", interaction=False)

    def on_display(self, topic, msg):
        what = topic.split("/", 1)[1]
        if what == "banner":
            text = msg.get("text")
            self.effect(f"BANNER [{msg.get('level')}] {text}" if text else "BANNER cleared", interaction=False)
        else:
            self.effect(f"{what.upper()} -> {msg}", interaction=False)


class MockResolume(MockDevice):
    """Resolume adapter: would fire the named clip via Arena REST/OSC."""
    icon = "\U0001F3AC"
    role = "bridge"
    subs = [("cmd/resolume/#", "on_cmd")]

    def on_cmd(self, topic, msg):
        effect = topic.rsplit("/", 1)[-1]
        if effect == "clip":
            name = msg.get("name") or msg.get("fallback")
            self.effect(f"trigger clip '{name}'" + ("" if msg.get("name") else " (fallback)"))
        else:
            self.effect(f"{effect.upper()} {msg}")


class MockKitchenBell(MockDevice):
    """Kitchen Pi: rings the solenoid bell."""
    icon = "\U0001F514"
    subs = [("cmd/kitchen/#", "on_cmd")]

    def on_cmd(self, topic, msg):
        effect = topic.rsplit("/", 1)[-1]
        if effect == "bell":
            self.effect("DING (soft)" if msg.get("soft") else "DING DING DING")
        else:
            self.effect(f"{effect.upper()} {msg}")


def build_devices(host: Optional[str] = None, port: Optional[int] = None, beats: bool = False,
                  quiet: bool = False, suffix: str = "", consoles: bool = False) -> Dict[str, MockDevice]:
    """The standard virtual camp, keyed by logical name (crystal_01, hallway, ...). Call start_all().
    `suffix` is appended to every MQTT client id so a test run can coexist with a live sim.
    `consoles` gives each mock a web console on 8101+ (off by default: a test run must not bind ports)."""
    specs = [
        (MockCrystal, "crystal_01", "mock-crystal"),
        (MockCrystal, "crystal_02", "mock-crystal"),
        (MockHallway, "hallway", "mock-hallway"),
        (MockMycelium, "mycelium_01", "mock-mycelium"),
        (MockGeode, "geode_01", "mock-geode"),
        (MockPlant, "plant_01", "mock-plant"),
        (MockTubes, "tubes_01", "mock-tubes"),
        (MockDisplay, "display_entrance", "mock-display"),
        (MockResolume, "resolume", "mock-resolume"),
        (MockKitchenBell, "kitchen_bell", "mock-kitchen"),
    ]
    return {name: cls(name + suffix, host, port, fw, beats, quiet,
                      console_port=(CONSOLE_PORT_BASE + i) if consoles else None)
            for i, (cls, name, fw) in enumerate(specs)}


def start_all(devs: Dict[str, MockDevice]) -> Dict[str, MockDevice]:
    for d in devs.values():
        d.start()
    return devs


def stop_all(devs: Dict[str, MockDevice]) -> None:
    for d in devs.values():
        d.stop()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=None, help="broker (default: $CAMPBUS_HOST or campbus.local)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--beats", action="store_true", help="print every beat on the crystals")
    ap.add_argument("--no-consoles", action="store_true", help="don't serve a web console per device")
    a = ap.parse_args()
    devs = start_all(build_devices(a.host, a.port, beats=a.beats, consoles=not a.no_consoles))
    first = next(iter(devs.values())).node
    print(f"virtual camp -> {first.host}:{first.port}: {', '.join(devs)}  (Ctrl-C to stop)", flush=True)
    if not a.no_consoles:
        lo = CONSOLE_PORT_BASE
        print(f"device consoles on {lo}-{lo + len(devs) - 1}; each node publishes its own as `ui`", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_all(devs)


if __name__ == "__main__":
    main()
