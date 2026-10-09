#!/usr/bin/env python3
"""Simulated inputs + scenario runner.

Fire the exact messages real devices would send, from the command line:

    python sim/inputs.py --host localhost press big_red_button
    python sim/inputs.py --host localhost hold big_red_button 2.5
    python sim/inputs.py --host localhost crank 3.0
    python sim/inputs.py --host localhost level fader_wall 0 0.8
    python sim/inputs.py --host localhost mode dial_a_vibe chaos
    python sim/inputs.py --host localhost staff staff_01 slam 0.9 "#ff4000"
    python sim/inputs.py --host localhost wand wand_01 flick 0.7 "#00c8ff" 25.7368 -80.1547 90
    python sim/inputs.py --host localhost drum drum_01 hit 1.0 "#ffd400"
    python sim/inputs.py --host localhost dip tube_2 0.8
    python sim/inputs.py --host localhost checkin "SideQuest"
    python sim/inputs.py --host localhost checkout
    python sim/inputs.py --host localhost foodcall dinner
    python sim/inputs.py --host localhost campmode idle        # no music, no BPM; show / quiet
    python sim/inputs.py --host localhost raw toy/whatever '{"toy":"whatever","event":"press","value":1}'

Or run a scenario that fires inputs and asserts what must appear on the bus:

    python sim/inputs.py --host localhost run sim/scenarios/smoke.yaml

Scenario format (yaml):
    name: friday night
    steps:
      - do: { press: big_red_button }              # any input above; args as scalar or list
        expect:
          - { topic: cmd/hallway/sweep, within_ms: 500 }
          - { topic: cmd/crystals/flash, payload: { color: "#ffffff" } }   # payload = subset match
      - do: { topic: toy/x, payload: { ... }, retain: false }              # or a raw publish
      - wait_ms: 300
      - do: { checkin: SideQuest }
        expect: [{ topic: cmd/resolume/clip, payload: { name: SideQuest } }]
        expect_not: [{ topic: cmd/kitchen/bell }]                          # must NOT appear in the window
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "clients", "python"))
import paho.mqtt.client as mqtt  # noqa: E402

from campbus import Node, now_ms  # noqa: E402

Msg = Tuple[str, Dict[str, Any], bool]   # topic, payload, retain


# ---------------------------------------------------------------- input catalogue
def _toy(toy: str, event: str, value: Any, **extra: Any) -> Msg:
    return (f"toy/{toy}", {"toy": toy, "event": event, "value": value, **extra}, False)


def press(toy: str) -> Msg:
    return _toy(toy, "press", 1)


def release(toy: str) -> Msg:
    return _toy(toy, "release", 1)


def hold(toy: str, seconds: float) -> Msg:
    return _toy(toy, "hold", float(seconds))


def pull(toy: str) -> Msg:
    return _toy(toy, "pull", 1)


def crank(rate: float, toy: str = "crank") -> Msg:
    return _toy(toy, "rate", float(rate))


def level(toy: str, i: int, value: float) -> Msg:
    return _toy(toy, "level", float(value), i=int(i))


def mode(toy: str, label: str) -> Msg:
    return _toy(toy, "mode", str(label))


def note(source: str, note_: int, vel: int = 100, ch: int = 1, on: bool = True) -> Msg:
    return (f"midi/{source}", {"note": int(note_), "vel": int(vel), "ch": int(ch), "on": bool(on)}, False)


def prop(prop: str, prop_id: str, gesture: str, intensity: float = 1.0, color: str = "#ffffff",
         lat: Optional[float] = None, lon: Optional[float] = None, heading: Optional[float] = None,
         spin_dir: Optional[int] = None, via: str = "sim", rssi: int = -50) -> Msg:
    """Any handheld prop action = prop type + id + gesture + intensity 0..1 + colour + heading (deg, 0 = N, cw)
    (+ GPS lat/lon when it has a fix; spin_dir +-1 for spins). Published on prop/<prop_id>."""
    return (f"prop/{prop_id}", {"prop": prop, "prop_id": prop_id, "gesture": gesture, "intensity": float(intensity),
                                "color": str(color), "heading": None if heading is None else float(heading) % 360,
                                "spin_dir": None if spin_dir is None else int(spin_dir),
                                "lat": None if lat is None else float(lat),
                                "lon": None if lon is None else float(lon), "via": via, "rssi": int(rssi)}, False)


def staff(staff_id: str, gesture: str, intensity: float = 1.0, color: str = "#ffffff", lat: Optional[float] = None,
          lon: Optional[float] = None, heading: Optional[float] = None, spin_dir: Optional[int] = None, **kw: Any) -> Msg:
    """Staff gestures: slam (circle), spin (spiral), swing (cone)."""
    return prop("staff", staff_id, gesture, intensity, color, lat, lon, heading, spin_dir, **kw)


def wand(wand_id: str, gesture: str, intensity: float = 1.0, color: str = "#ffffff", lat: Optional[float] = None,
         lon: Optional[float] = None, heading: Optional[float] = None, **kw: Any) -> Msg:
    """Wand gestures: flick (narrow fast bolt), circle (orb close around the caster)."""
    return prop("wand", wand_id, gesture, intensity, color, lat, lon, heading, None, **kw)


def drum(drum_id: str, gesture: str, intensity: float = 1.0, color: str = "#ffffff", lat: Optional[float] = None,
         lon: Optional[float] = None, **kw: Any) -> Msg:
    """Drum gestures: hit (slow heavy wave, far), roll (steady charge nearby)."""
    return prop("drum", drum_id, gesture, intensity, color, lat, lon, None, None, **kw)


def whip(whip_id: str, gesture: str, intensity: float = 1.0, color: str = "#ffffff", lat: Optional[float] = None,
         lon: Optional[float] = None, heading: Optional[float] = None, **kw: Any) -> Msg:
    """Whip gestures: crack (12 degree needle at 30 m/s), lash (140 degree arc within 25 m)."""
    return prop("whip", whip_id, gesture, intensity, color, lat, lon, heading, None, **kw)


# The Sonic Mycelium Cluster's mushrooms: pad -> (MIDI note, colour). Keep in sync with sim/instruments.html.
MYCELIUM_PADS = {"amanita": (36, "#ff3b3b"), "chanterelle": (38, "#ffb300"), "indigo_milk": (42, "#3d7bff"),
                 "ghost": (45, "#4dffa6"), "violet_coral": (39, "#b04dff"), "pink_oyster": (51, "#ff4da6")}


def hit(pad: str = "amanita", velocity: float = 0.8, instrument_id: str = "mycelium_01", instrument: str = "mycelium",
        lat: Optional[float] = None, lon: Optional[float] = None, **kw: Any) -> Msg:
    """A whack on a touch instrument pad (a mushroom of the Sonic Mycelium Cluster): published on
    instrument/<instrument_id> with the pad's MIDI note and colour, velocity 0..1, and the cluster's position."""
    note, color = MYCELIUM_PADS.get(pad, (60, "#ffffff"))
    pads = list(MYCELIUM_PADS)
    return (f"instrument/{instrument_id}", {"instrument": instrument, "instrument_id": instrument_id, "pad": pad,
                                            "pad_i": pads.index(pad) if pad in pads else -1, "event": "hit",
                                            "velocity": float(velocity), "note": note, "color": color,
                                            "lat": lat, "lon": lon, **kw}, False)


# The Crystal Geode Synth's capacitive pads: pad -> (MIDI note, colour). Keep in sync with sim/instruments.html.
GEODE_PADS = {"amethyst": (60, "#b04dff"), "citrine": (64, "#ffc83d"), "aquamarine": (67, "#4de0ff"), "rose_quartz": (71, "#ff7ab8")}


def touch(pad: str = "amethyst", velocity: float = 0.6, event: str = "touch", value: Optional[float] = None,
          instrument_id: str = "geode_01", instrument: str = "geode", lat: Optional[float] = None,
          lon: Optional[float] = None, **kw: Any) -> Msg:
    """A capacitive touch on a geode: `touch` (velocity = how strongly this person couples to the frame, 0..1),
    `hold` (value = the live normalised reading) or `release`. Published on instrument/<instrument_id>."""
    note, color = GEODE_PADS.get(pad, (60, "#ffffff"))
    pads = list(GEODE_PADS)
    if value is None:
        value = 0.0 if event == "release" else float(velocity)
    return (f"instrument/{instrument_id}", {"instrument": instrument, "instrument_id": instrument_id, "pad": pad,
                                            "pad_i": pads.index(pad) if pad in pads else -1, "event": event,
                                            "velocity": 0.0 if event == "release" else float(velocity), "value": float(value),
                                            "raw": int(1240 + float(value) * 900), "baseline": 1240, "note": note,
                                            "color": color, "lat": lat, "lon": lon, **kw}, False)


# The Monstera Piezo Plant's leaves: pad -> (MIDI note, colour). Keep in sync with sim/instruments.html.
PLANT_PADS = {"leaf_1": (36, "#3ddc84"), "leaf_2": (40, "#8fff5a"), "leaf_3": (43, "#1fb6a6"), "leaf_4": (47, "#b8ff8a"),
              "leaf_5": (50, "#d8ff3d")}


def bend(pad: str = "leaf_1", velocity: float = 0.6, event: str = "release", value: Optional[float] = None,
         instrument_id: str = "plant_01", instrument: str = "plant", lat: Optional[float] = None,
         lon: Optional[float] = None, **kw: Any) -> Msg:
    """Working a piezo-tape leaf: `bend` (~8 Hz while flexed, value = the flex 0..1), `release` (the snap back;
    velocity = the spike) or a light `hit`. Published on instrument/<instrument_id>."""
    note, color = PLANT_PADS.get(pad, (36, "#ffffff"))
    pads = list(PLANT_PADS)
    if value is None:
        value = 0.0 if event == "release" else float(velocity)
    return (f"instrument/{instrument_id}", {"instrument": instrument, "instrument_id": instrument_id, "pad": pad,
                                            "pad_i": pads.index(pad) if pad in pads else -1, "event": event,
                                            "velocity": float(velocity), "value": float(value), "note": note, "ch": 3,
                                            "color": color, "lat": lat, "lon": lon, **kw}, False)


# The Water Chord Tubes: pad -> (MIDI note, colour), three tubes tuned as one chord. Keep in sync with sim/instruments.html.
TUBE_PADS = {"tube_1": (48, "#4de0ff"), "tube_2": (52, "#3d9bff"), "tube_3": (55, "#7cffd4")}
TUBE_BASELINE, TUBE_SPAN = 980, 1500   # the water is a bigger electrode than the geodes' brass frame


def dip(pad: str = "tube_1", velocity: float = 0.7, event: str = "touch", value: Optional[float] = None,
        instrument_id: str = "tubes_01", instrument: str = "tubes", lat: Optional[float] = None,
        lon: Optional[float] = None, **kw: Any) -> Msg:
    """A hand in a water chord tube: `touch` (velocity = how strongly this person couples through the water),
    `hold` (value = the live reading) or `release`. Published on instrument/<instrument_id>."""
    note, color = TUBE_PADS.get(pad, (48, "#ffffff"))
    pads = list(TUBE_PADS)
    if value is None:
        value = 0.0 if event == "release" else float(velocity)
    return (f"instrument/{instrument_id}", {"instrument": instrument, "instrument_id": instrument_id, "pad": pad,
                                            "pad_i": pads.index(pad) if pad in pads else -1, "event": event,
                                            "velocity": 0.0 if event == "release" else float(velocity), "value": float(value),
                                            "raw": int(TUBE_BASELINE + float(value) * TUBE_SPAN), "baseline": TUBE_BASELINE,
                                            "note": note, "ch": 4, "color": color, "lat": lat, "lon": lon, **kw}, False)


def checkin(artist: str, slot: Optional[str] = None, minutes: Optional[float] = None, logo: Optional[str] = None) -> Msg:
    t = now_ms()
    return ("booth/dj", {"artist": artist, "slot": slot, "since": t,
                         "timer_end": (t + int(float(minutes) * 60000)) if minutes else None, "logo": logo}, True)


def checkout() -> Msg:
    return ("booth/dj", {"artist": None, "since": now_ms()}, True)


def foodcall(meal: str = "dinner", soft: bool = False) -> Msg:
    return ("kitchen/foodcall", {"meal": meal, "soft": bool(soft) and str(soft).lower() != "false"}, False)


def campmode(mode: str = "show") -> Msg:
    """The booth sets the camp mode (retained): idle = no music/no BPM, show = normal, quiet = quiet hours."""
    return ("camp/mode", {"mode": mode, "since": now_ms()}, True)


def raw(topic: str, payload: Any = None, retain: bool = False) -> Msg:
    if isinstance(payload, str):
        payload = json.loads(payload)
    return (topic, dict(payload or {}), bool(retain))


INPUTS = {f.__name__: f for f in (press, release, hold, pull, crank, level, mode, note, prop, staff, wand, drum,
                                  whip, hit, touch, bend, dip, checkin, checkout, foodcall, campmode, raw)}


def build(kind: str, args: Any) -> Msg:
    """Build a message from an input name and scalar/list/dict args."""
    fn = INPUTS[kind]
    if args is None:
        return fn()
    if isinstance(args, dict):
        return fn(**args)
    if isinstance(args, (list, tuple)):
        return fn(*args)
    return fn(args)


# ---------------------------------------------------------------- scenario runner
class Recorder:
    """Subscribes to # and remembers everything with a perf_counter timestamp."""

    def __init__(self, node: Node):
        self.node = node
        self.log: List[Tuple[float, str, Dict[str, Any]]] = []
        self._lock = threading.Lock()
        self._event = threading.Event()
        node.subscribe("#", self._on)

    def _on(self, topic: str, msg: Dict[str, Any]) -> None:
        with self._lock:
            self.log.append((time.perf_counter(), topic, msg))
        self._event.set()

    def find(self, since: float, topic: str, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        with self._lock:
            items = list(self.log)
        for t, top, m in items:
            if t < since or not mqtt.topic_matches_sub(topic, top):
                continue
            if payload and any(m.get(k) != v for k, v in payload.items()):
                continue
            return m
        return None

    def wait_for(self, since: float, expects: List[Dict[str, Any]], default_ms: int) -> List[Optional[Dict[str, Any]]]:
        deadline = since + max([e.get("within_ms", default_ms) for e in expects] or [default_ms]) / 1000
        found: List[Optional[Dict[str, Any]]] = [None] * len(expects)
        while True:
            for i, e in enumerate(expects):
                if found[i] is None:
                    found[i] = self.find(since, e["topic"], e.get("payload"))
            if all(f is not None for f in found) or time.perf_counter() >= deadline:
                return found
            self._event.clear()
            self._event.wait(min(0.05, max(0.0, deadline - time.perf_counter())))


def run_scenario(path: str, node: Node, recorder: Recorder, default_within_ms: int = 1000,
                 verbose: bool = True) -> Tuple[int, int, List[str]]:
    """Run one scenario file. Returns (passed, failed, failure_messages)."""
    import yaml
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    name = doc.get("name") or os.path.basename(path)
    steps = doc.get("steps") or []
    passed = failed = 0
    failures: List[str] = []
    say = (lambda s: print(s, flush=True)) if verbose else (lambda s: None)
    say(f"== scenario: {name} ({len(steps)} steps)")
    for i, step in enumerate(steps, 1):
        if "wait_ms" in step:
            time.sleep(step["wait_ms"] / 1000)
            continue
        do = step.get("do") or {}
        if "topic" in do:
            topic, payload, retain = raw(do["topic"], do.get("payload"), do.get("retain", False))
            label = f"raw {topic}"
        else:
            (kind, args), = do.items()
            topic, payload, retain = build(kind, args)
            label = f"{kind} {args if args is not None else ''}".strip()
        since = time.perf_counter()   # perf_counter: monotonic() is 15.6 ms coarse on Windows
        node.publish(topic, payload, retain=retain)
        expects = step.get("expect") or []
        nots = step.get("expect_not") or []
        found = recorder.wait_for(since, expects, default_within_ms) if expects else []
        # expect_not needs the window to elapse before we can say it never appeared
        if nots:
            window = max([e.get("within_ms", default_within_ms) for e in nots]) / 1000
            time.sleep(max(0.0, since + window - time.perf_counter()))
        step_ok = True
        for e, f in zip(expects, found):
            if f is None:
                step_ok = False
                failures.append(f"step {i} ({label}): expected {e['topic']} {e.get('payload') or ''} - not seen")
                say(f"   FAIL  {e['topic']} {e.get('payload') or ''}")
            else:
                say(f"   ok    {e['topic']} {json.dumps(f, separators=(',', ':'))}")
        for e in nots:
            hit = recorder.find(since, e["topic"], e.get("payload"))
            if hit is not None:
                step_ok = False
                failures.append(f"step {i} ({label}): {e['topic']} must not appear but got {hit}")
                say(f"   FAIL  {e['topic']} appeared: {hit}")
            else:
                say(f"   ok    (no {e['topic']})")
        if step_ok:
            passed += 1
            say(f"  [{i}] {label}: PASS")
        else:
            failed += 1
            say(f"  [{i}] {label}: FAIL")
    say(f"== {name}: {passed} passed, {failed} failed")
    return passed, failed, failures


# ---------------------------------------------------------------- cli
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=None, help="broker (default: $CAMPBUS_HOST or campbus.local)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--within", type=int, default=1000, help="default expect window in ms (run only)")
    ap.add_argument("what", nargs="+", help="input name + args, or: run <scenario.yaml> [...]")
    a = ap.parse_args()
    kw: Dict[str, Any] = {}
    if a.host:
        kw["host"] = a.host
    if a.port:
        kw["port"] = a.port
    node = Node(f"sim_input_{os.getpid()}", fw="sim-input", **kw)

    if a.what[0] == "run":
        rec = Recorder(node)
        node.connect()
        _wait_connected(node)
        node.wait_ready(5)
        time.sleep(0.3)   # let retained state settle before we start timing
        total_fail = 0
        for path in a.what[1:]:
            _, failed, _ = run_scenario(path, node, rec, a.within)
            total_fail += failed
        node.close()
        sys.exit(1 if total_fail else 0)

    kind, args = a.what[0], a.what[1:]
    if kind not in INPUTS:
        ap.error(f"unknown input {kind!r}; choose from {', '.join(INPUTS)} or run")
    topic, payload, retain = build(kind, args if args else None)
    node.connect()
    _wait_connected(node)
    node.publish(topic, payload, retain=retain)
    time.sleep(0.2)
    print(f"published {topic} {json.dumps(payload, separators=(',', ':'))}{' (retained)' if retain else ''}")
    node.close()


def _wait_connected(node: Node, timeout: float = 5.0) -> None:
    t = time.monotonic()
    while not node.connected and time.monotonic() - t < timeout:
        time.sleep(0.05)
    if not node.connected:
        print(f"could not reach broker at {node.host}:{node.port}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
