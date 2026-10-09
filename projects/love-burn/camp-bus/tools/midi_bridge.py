#!/usr/bin/env python3
"""Bus -> MIDI bridge: turns instrument/# events into MIDI for Ableton (or any DAW).

Runs on the Ableton laptop (or the booth Pi). Subscribes to every instrument on the bus and plays
them into one MIDI output port - on Windows a loopMIDI virtual port, on macOS the IAC bus, on Linux
a virtual port it creates itself. Real hardware and the browser simulator look identical here,
because both publish the same instrument/<id> messages.

    python tools/midi_bridge.py --list                     # show MIDI output ports
    python tools/midi_bridge.py --host localhost           # first port named like "Camp Bus" / loopMIDI / IAC
    python tools/midi_bridge.py --host localhost --port "IAC Driver Bus 1"
    python tools/midi_bridge.py --host localhost --dry     # no port: print what would be sent

How events become MIDI (same table as the Web MIDI output in sim/instruments.html; see docs/SCHEMA.md):

    event        note on              note off             control change
    (every note on is preceded by CC <--vel-cc, default 11> = velocity: map it to a gain in the DAW)
    hit          yes (velocity)       after --gate ms      -
    touch        yes (velocity)       on release           CC <cc> = value
    hold         -                    -                    CC <cc> = value (rate-limited per pad)
    bend         -                    -                    CC <cc> = value (rate-limited per pad)
    release      plant: yes, spike    geodes/tubes: yes    CC <cc> = 0
                 as velocity, then
                 off after --gate

Note and channel come from the message (`note`, `ch`); a retained config/midi/<instrument_id>
overrides them per pad, so the Instruments page's MIDI table is the source of truth for both the
bridge and the simulator. Requires: pip install mido python-rtmidi
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "clients", "python"))
from campbus import Node  # noqa: E402

DEFAULT_CH = {"mycelium": 1, "geode": 2, "plant": 3, "tubes": 4}   # sequential (Ben, 2026-09-21); only used when the message has no `ch`
GATED = {"geode", "tubes"}           # touch = note on, release = note off (held instruments)
PLUCKED = {"plant"}                  # release = the note (the snap), bend = CC while worked

MidiEvent = Tuple[str, int, int, int]   # (kind, channel 1-16, data1, data2): ("note_on", 10, 36, 100) / ("cc", 1, 74, 90)


def _v127(x: Any) -> int:
    try:
        return max(0, min(127, int(round(float(x) * 127))))
    except (TypeError, ValueError):
        return 0


def translate(msg: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None, cc: int = 74, vel_cc: int = 11) -> List[Tuple[MidiEvent, float]]:
    """Pure: an instrument message (+ optional config/midi pads dict for that instrument) -> [(midi event, delay s)].
    A delay > 0 means "send this much later" (the note off that closes a one-shot hit).
    `vel_cc` (default 11, expression; 0 = off): sent with the note's velocity right before every note on, so a
    DAW can make loudness follow velocity on any patch by mapping that CC to a gain (patches differ in whether
    velocity reaches volume at all)."""
    kind = str(msg.get("instrument") or "")
    event = str(msg.get("event") or "")
    pad = str(msg.get("pad") or "")
    over = (cfg or {}).get(pad) if isinstance(cfg, dict) else None
    note = int((over or {}).get("note", msg.get("note", 60)) or 0)
    ch = int((over or {}).get("ch", msg.get("ch") or DEFAULT_CH.get(kind, 1)) or 1)
    ch = max(1, min(16, ch))
    note = max(0, min(127, note))
    vel = _v127(msg.get("velocity"))
    val = _v127(msg.get("value", msg.get("velocity")))
    # chord mode: `notes` lists every note of the chord (the config may override the pad's chord too)
    raw_notes = (over or {}).get("notes") if isinstance(over, dict) and over.get("notes") else msg.get("notes")
    notes = [max(0, min(127, int(n))) for n in raw_notes] if isinstance(raw_notes, list) and raw_notes else [note]
    out: List[Tuple[MidiEvent, float]] = []
    expr = [(("cc", ch, vel_cc, max(1, vel)), 0.0)] if vel_cc else []
    if event == "hit":
        out.extend(expr)
        for n in notes:
            out.append((("note_on", ch, n, max(1, vel)), 0.0))
            out.append((("note_off", ch, n, 0), -1.0))                     # -1 = after the gate time
    elif event == "touch":
        out.append((("cc", ch, cc, val), 0.0))
        out.extend(expr)
        for n in notes:
            out.append((("note_on", ch, n, max(1, vel)), 0.0))
    elif event in ("hold", "bend"):
        out.append((("cc", ch, cc, val), 0.0))
    elif event == "release":
        out.append((("cc", ch, cc, 0), 0.0))
        if kind in PLUCKED and vel > 0:
            out.extend(expr)
        for n in notes:
            if kind in PLUCKED and vel > 0:
                out.append((("note_on", ch, n, vel), 0.0))
                out.append((("note_off", ch, n, 0), -1.0))
            else:
                out.append((("note_off", ch, n, 0), 0.0))
    return out


class Bridge:
    def __init__(self, port_name: Optional[str], host: Optional[str], port: Optional[int], cc: int = 74,
                 gate_ms: int = 120, cc_hz: float = 30.0, dry: bool = False, verbose: bool = True, vel_cc: int = 11):
        kw: Dict[str, Any] = {}
        if host:
            kw["host"] = host
        if port:
            kw["port"] = port
        self.node = Node("midi_bridge", fw="midi-bridge-0.1", role="bridge", **kw)
        self.cc, self.gate, self.cc_min_dt, self.verbose, self.vel_cc = cc, gate_ms / 1000.0, 1.0 / max(1.0, cc_hz), verbose, vel_cc
        self.cfg: Dict[str, Dict[str, Any]] = {}        # instrument_id -> pads
        self._last_cc: Dict[Tuple[str, str], Tuple[float, int]] = {}
        self._lock = threading.Lock()
        self.out = None
        self.sent = 0
        if not dry:
            import mido  # noqa: F401  (import error = tell the user how to install)
            self.out = open_port(port_name)
        self.node.subscribe("instrument/#", self._on_instrument)
        self.node.subscribe("config/midi/+", self._on_config)

    def start(self) -> "Bridge":
        self.node.connect()
        return self

    def stop(self) -> None:
        self.node.close()
        if self.out is not None:
            try:
                self.out.reset()
                self.out.close()
            except Exception:
                pass

    def _on_config(self, topic: str, msg: Dict[str, Any]) -> None:
        if isinstance(msg, dict) and isinstance(msg.get("pads"), dict):
            iid = topic.rsplit("/", 1)[-1]
            self.cfg[iid] = msg["pads"]
            self._log(f"config for {iid}: " + ", ".join(f"{p}={v.get('note')}/ch{v.get('ch')}" for p, v in msg["pads"].items()))
            for clash in channel_clashes(self.cfg):
                self._log("WARNING " + clash)

    def _on_instrument(self, topic: str, msg: Dict[str, Any]) -> None:
        if not isinstance(msg, dict):
            return
        iid = str(msg.get("instrument_id") or topic.rsplit("/", 1)[-1])
        for ev, delay in translate(msg, self.cfg.get(iid), self.cc, self.vel_cc):
            if ev[0] == "cc" and ev[2] != self.vel_cc:          # rate-limit continuous readings per pad, always pass 0 (release)
                key = (iid, str(msg.get("pad")))
                now = time.monotonic()
                last = self._last_cc.get(key)
                if ev[3] != 0 and last and now - last[0] < self.cc_min_dt:
                    continue
                self._last_cc[key] = (now, ev[3])
            if delay < 0:
                threading.Timer(self.gate, self.send, args=(ev,)).start()
            else:
                self.send(ev)
        self.node.count()

    def send(self, ev: MidiEvent) -> None:
        kind, ch, d1, d2 = ev
        with self._lock:
            self.sent += 1
            if self.out is not None:
                import mido
                if kind == "cc":
                    m = mido.Message("control_change", channel=ch - 1, control=d1, value=d2)
                else:
                    m = mido.Message(kind, channel=ch - 1, note=d1, velocity=d2)
                self.out.send(m)
        if self.verbose:
            self._log(f"{kind:<8} ch{ch:<3} {'cc' if kind == 'cc' else 'note'} {d1:<4} {'val' if kind == 'cc' else 'vel'} {d2}")

    def _log(self, s: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} [midi] {s}", flush=True)


def channel_clashes(cfg: Dict[str, Dict[str, Any]]) -> List[str]:
    """Instruments are told apart in the DAW by MIDI channel, so two instruments sharing one is a conflict."""
    chans: Dict[int, set] = {}
    for iid, pads in cfg.items():
        for v in (pads or {}).values():
            try:
                chans.setdefault(int(v.get("ch")), set()).add(iid)
            except (TypeError, ValueError, AttributeError):
                pass
    return [f"channel {ch} is used by {', '.join(sorted(ids))}: the DAW cannot tell them apart" for ch, ids in sorted(chans.items()) if len(ids) > 1]


def list_ports() -> List[str]:
    import mido
    return list(mido.get_output_names())


def open_port(name: Optional[str]):
    """Open the first output port whose name contains `name` (default: loopMIDI / IAC / first port);
    on Linux with no ports, create a virtual one."""
    import mido
    names = list_ports()
    want = name or next((n for n in names if any(k in n.lower() for k in ("camp bus", "campbus", "loopmidi", "iac"))), None)
    if want is None:
        if sys.platform.startswith("linux"):
            return mido.open_output("campbus", virtual=True)
        raise SystemExit("no obvious MIDI output port. Create one named 'Camp Bus' in loopMIDI (Windows) or enable the IAC Driver "
                         f"(macOS), or pick one with --port. Ports now: {names or '(none)'}")
    for n in names:
        if want.lower() in n.lower():
            return mido.open_output(n)
    raise SystemExit(f"no MIDI output port matching {want!r}; have: {names}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=None, help="broker (default: $CAMPBUS_HOST or campbus.local)")
    ap.add_argument("--bus-port", type=int, default=None, help="broker port (default 1883)")
    ap.add_argument("--port", default=None, help="MIDI output port name (substring; default: loopMIDI / IAC / first)")
    ap.add_argument("--list", action="store_true", help="list MIDI output ports and exit")
    ap.add_argument("--cc", type=int, default=74, help="controller number for hold/bend readings (default 74, 'brightness')")
    ap.add_argument("--gate", type=int, default=120, help="ms a one-shot hit stays on before note off (default 120)")
    ap.add_argument("--cc-hz", type=float, default=30, help="max CC rate per pad (default 30/s)")
    ap.add_argument("--vel-cc", type=int, default=11, help="CC sent = velocity before every note on, so loudness can follow velocity on any patch (default 11 expression; 0 = off)")
    ap.add_argument("--dry", action="store_true", help="don't open a port; print what would be sent")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.list:
        try:
            ports = list_ports()
        except ImportError:
            raise SystemExit("pip install mido python-rtmidi")
        print("\n".join(ports) if ports else "(no MIDI output ports - install loopMIDI and create one)")
        return
    try:
        b = Bridge(a.port, a.host, a.bus_port, a.cc, a.gate, a.cc_hz, a.dry, not a.quiet, a.vel_cc).start()
    except ImportError:
        raise SystemExit("pip install mido python-rtmidi")
    where = "dry run (no port)" if b.out is None else f"MIDI out '{b.out.name}'"
    print(f"midi bridge -> bus {b.node.host}:{b.node.port}, {where}, CC {a.cc} for hold/bend, CC {a.vel_cc or 'off'} = velocity before notes, gate {a.gate} ms (Ctrl-C to stop)", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        b.stop()


if __name__ == "__main__":
    main()
