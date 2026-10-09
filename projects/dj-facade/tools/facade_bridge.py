"""Camp-bus responder for the DJ facade: MQTT in, Pixelblaze variables out.

Subscribes (docs/SCHEMA.md in apps/camp-bus):
  cmd/facade/#, cmd/all/#        effects: flash pulse breathe color charge sweep shockwave rings
                                 spiral wipe rain comet speed mode
  audio/beat, audio/energy, audio/bpm
  camp/mode, camp/sync, map/facade

Facade extension: any effect may carry `zone` - a zone or group name, or a list of them
(ring grid mushroom_left mushroom_right leaves_left leaves_right crown | circle mushrooms leaves
left right all). Missing = the whole facade. Other responders ignore the field.

Drives the "Facade Bus" pattern (patterns/facade_bus.js) through exported variables
(setVars - never written to flash). Serves the campbus console (:8120) and the facade
controller page (:8121, tools/facade_control.py).

Usage:
  python tools/facade_bridge.py --host localhost           # home broker (docker camp-bus)
  python tools/facade_bridge.py --deploy                   # (re)install the pattern on the Pixelblaze first
"""

import argparse
import colorsys
from collections import deque
import json
import math
import os
import pathlib
import sys
import threading
import time

import cv2
import numpy as np
from campbus import Node, console
from facade_zones import GROUPS, ZONES, zone_mask
from pixelblaze import Pixelblaze

DEFAULT_IP = os.environ.get("PIXELBLAZE_IP")   # no default: set it or pass --ip
PATTERN_NAME = "Facade Bus"
ROOT = pathlib.Path(__file__).resolve().parent.parent
PATTERN_FILE = ROOT / "patterns" / "facade_bus.js"
MAP_RUN = ROOT / "maps" / "2026-10-06_213307"   # zones.json lives with the map it describes
NODE_ID = "facade"
TARGET = "facade"

VIBES = ["cozy", "party", "chaos", "wave", "jumping", "ethereal",
         "snake", "twinkle", "heartbeat", "aurora", "meteor",
         "bloom", "kaleido",                       # from here on: facade-only looks (not camp-bus modes)
         "lotus", "spectrum", "fireflies", "cascade", "mycelium", "butterfly",
         "mosaic", "wings", "vortex"]
EFFECTS = ["flash", "pulse", "color", "charge", "sweep", "shockwave", "rings", "spiral", "wipe", "rain", "comet",
           "halo", "iris", "petalfall", "sunburst", "sporeburst"]   # the last five (2026-10-07) ride on the machinery of the first eleven
ALIASES = {"breathe": "pulse"}
VIBE_ZONES = ZONES[:7]                             # zones with their own vibe slot (zv0..zv6)
QUIET_LEVEL = 0.35
SEND_HZ = 30
# Camp-bus state, re-sent whenever the Pixelblaze reconnects (only the bus knows it). Choices a
# person makes - vibe, speed, split, audio source - are NOT re-sent: the Pixelblaze's own UI
# controls save and restore those, and the last writer wins.
STICKY = {"master", "bpm", "bpmSync", "pulseSync"}
READBACK = ("vibe", "speed", "split", "audioSrc", "sensorGain", "autoMode", "autoSeconds", "autoLeft",
            *(f"zv{z}" for z in range(7)))
# Saved values for the pattern's own UI controls, written at every deploy. The Pixelblaze applies
# saved control values whenever the pattern is activated, so these ARE the pattern's defaults.
DEFAULT_CONTROLS = {
    "toggleAutoMode": 0, "sliderAutoSeconds": 0.47,      # off; 30 s
    "sliderSpeed": 0.2727,                               # 1x
    "toggleAudioSplit": 1, "sliderAudioSource": 0.0,     # split on; auto
    "sliderSensitivity": 0.5,                            # 1x
    "hsvPickerEffectColor": [0, 0, 1],                   # white
}
AUDIO_VARS = ("aBass", "aMid", "aHigh", "aLoud", "aLive", "aBpm")
AUDIO_SOURCES = ["auto", "sensor", "bus"]


def parse_color(value, default=(0.0, 0.0, 1.0)):
    """'#rrggbb' -> (hue, sat, val) 0..1, the form the pattern mixes in. Default white."""
    if not isinstance(value, str) or len(value.lstrip("#")) != 6:
        return default
    h = value.lstrip("#")
    try:
        return colorsys.rgb_to_hsv(*(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)))
    except ValueError:
        return default


def wrap180(a):
    return (a + 180) % 360 - 180


class PixelblazeLink:
    """The bridge's ONE websocket to the Pixelblaze: variable writes go out, and the Pixelblaze's
    stats pushes and our variable reads come back on the same socket.

    The Pixelblaze has only a few connection slots and pays for every socket it services. An
    earlier version used pixelblaze-client (which reopens a new socket after any hiccup) plus a
    second socket for stats; sockets piled up and the frame rate crashed. This owns exactly one
    socket, closes it before reconnecting, and reconnects only when it is really dead.
    """

    def __init__(self, ip: str):
        self.ip = ip
        self.ws = None
        self.send_lock = threading.Lock()
        self.pending = {}
        self.sticky = {}
        self.lock = threading.Lock()
        self.connected = False
        self.last_error = ""
        self.audio = {}            # live levels the pattern is using (from its exported vars)
        self.vars = {}             # settings as the pattern has them (it may change them via its own UI)
        self.reclaims = 0          # times Facade Bus had to be put back on (playlist took over)
        self.fps = None            # live frame rate (the Pixelblaze pushes stats once a second)
        self.vmerr = 0
        self.pid = None            # Facade Bus's pattern id, looked up once
        self.last_heard = 0.0
        self._vars_at = 0.0
        self.meters_until = 0.0    # fast variable polling only while meters are on screen
        threading.Thread(target=self._loop, daemon=True).start()

    # ---- public ------------------------------------------------------------------------
    def set(self, **vars_):
        with self.lock:
            self.pending.update(vars_)
            self.sticky.update({k: v for k, v in vars_.items() if k in STICKY})

    def want_meters(self):
        """A page is showing the live audio meters: read the pattern's variables quickly for a while."""
        self.meters_until = time.monotonic() + 3

    def close(self):
        """Close the socket cleanly so the Pixelblaze frees its slot right away."""
        self.connected = False
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                ws.close(timeout=1)
            except Exception:
                pass

    # ---- socket --------------------------------------------------------------------------
    def _send(self, obj):
        with self.send_lock:
            self.ws.send(json.dumps(obj, separators=(",", ":")))

    def _find_pattern(self):
        """Facade Bus's id: a short-lived pixelblaze-client session (opened and closed here)."""
        with Pixelblaze(self.ip) as pb:
            pid = next((k for k, v in pb.getPatternList(forceRefresh=True).items() if v == PATTERN_NAME), None)
        if pid is None:
            raise RuntimeError(f'pattern "{PATTERN_NAME}" not on the Pixelblaze; run with --deploy')
        return pid

    def _connect(self):
        import websocket
        self.close()
        if self.pid is None:
            self.pid = self._find_pattern()
        self.ws = websocket.create_connection(f"ws://{self.ip}:81", timeout=5)
        self.last_heard = time.monotonic()
        threading.Thread(target=self._reader, args=(self.ws,), daemon=True).start()
        self.connected = True
        self.last_error = ""
        # Don't re-activate on a reconnect (that resets the vibe): re-send the bus state and read the
        # variables - the reader claims the Pixelblaze only if another pattern is running.
        with self.lock:
            self.pending = {**self.sticky, **self.pending}
        self._vars_at = 0.0

    def _claim(self):
        """Pause the Pixelblaze's playlist (not saved) and run Facade Bus, with the bus state."""
        # Always (re)load: an updated pattern only takes effect when it is activated again.
        self._send({"runSequencer": False})
        self._send({"activeProgramId": self.pid, "save": False})
        with self.lock:
            self.pending = {**self.sticky, **self.pending}

    def _reader(self, ws):
        import websocket
        while self.ws is ws:
            try:
                m = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue                      # quiet for a moment; the writer decides if it's dead
            except Exception:
                break
            self.last_heard = time.monotonic()
            if not isinstance(m, str):
                continue                      # binary frames (previews, pattern lists): not ours
            try:
                d = json.loads(m)
                if not isinstance(d, dict):
                    continue
            except ValueError:
                continue
            if "fps" in d:
                self.fps, self.vmerr = round(float(d["fps"]), 1), d.get("vmerr", 0)
            if "vars" in d:
                v = d["vars"] or {}
                if "vibe" not in v:
                    # Something else is running (the Pixelblaze's playlist moves on by itself)
                    self.reclaims += 1
                    try:
                        self._claim()
                    except Exception:
                        pass
                    continue
                num = lambda x: round(float(x), 3) if isinstance(x, (int, float)) else 0.0   # never die on odd data
                self.audio = {k: num(v.get(k, 0)) for k in AUDIO_VARS}
                self.vars = {k: v[k] for k in READBACK if k in v and isinstance(v[k], (int, float))}
        if self.ws is ws:                     # died under us
            self.connected = False

    def _loop(self):
        while True:
            try:
                if not self.connected:
                    self._connect()
                if time.monotonic() - self.last_heard > 15:
                    raise RuntimeError("Pixelblaze silent for 15 s")   # stats arrive every second
                with self.lock:
                    batch, self.pending = self.pending, {}
                if batch:
                    self._send({"setVars": batch})
                # Reading every variable is a big message for the Pixelblaze to build: 5x a second
                # only while a page shows the audio meters, otherwise every 2 s (enough to notice the
                # playlist taking over or a change made on the Pixelblaze's own page).
                interval = 0.2 if time.monotonic() < self.meters_until else 2.0
                if time.monotonic() - self._vars_at > interval:
                    self._vars_at = time.monotonic()
                    self._send({"getVars": True})
                time.sleep(1 / SEND_HZ)
            except Exception as e:            # Pixelblaze rebooted / wifi blip: close, wait, retry
                self.last_error = str(e)
                self.fps = None
                self.close()
                time.sleep(3)


class Facade:
    def __init__(self, link: PixelblazeLink, node: Node):
        self.link = link
        self.node = node
        self.pos = None          # {"lat", "lon"} from map/facade
        self.charge = 0.0
        self.charge_at = time.monotonic()
        self.vibe = "ethereal"
        self.zone_vibes = {z: None for z in VIBE_ZONES}   # None = follow the global vibe
        self.mode = "show"
        self.bpm = 0.0
        self.fader = 1.0       # controller brightness, multiplied with camp quiet mode
        self.speed = 1.0
        self.split = True
        self.audio_src = "auto"
        self.sensor_gain = 1.0
        self.auto = {"on": False, "seconds": 30, "left": 0}
        self.last = deque(maxlen=12)   # recent effects, for the controller page

    # ---- propagation (SCHEMA.md "Positions, calibration and propagation") ----
    def arrival(self, msg):
        """Returns (delay_s, intensity) for this node, or None if the effect doesn't reach us."""
        intensity = float(msg.get("intensity", 1.0))
        if "nearest" in msg:      # only the N nearest crystals react
            return None
        origin = msg.get("origin")
        if not origin or not self.pos:
            return 0.0, intensity
        lat0, lon0 = float(origin["lat"]), float(origin["lon"])
        north = (self.pos["lat"] - lat0) * 111_320
        east = (self.pos["lon"] - lon0) * 111_320 * math.cos(math.radians(lat0))
        dist = math.hypot(north, east)
        if "cutoff_m" in msg and dist > float(msg["cutoff_m"]):
            return None
        delay = 0.0 if msg.get("instant") else dist / float(msg.get("speed_mps", 6))
        shape = msg.get("shape", "circle")
        a = wrap180(math.degrees(math.atan2(east, north)) - float(msg.get("heading", 0)))
        if shape == "cone":
            half = float(msg.get("cone_deg", 70)) / 2
            if abs(a) > half:
                return None
            intensity *= 0.3 + 0.7 * math.cos(math.radians(90 * a / half))
        elif shape == "spiral":
            spin = float(msg.get("spin_dir", 1))
            delay = ((spin * a) % 360) / 360 * float(msg.get("turn_s", 2))
        if intensity < 1.0 and msg.get("falloff") != "none":
            intensity *= 1 - 0.6 * min(1.0, dist / float(msg.get("range_m", 60)))
        return delay, intensity

    # ---- effects ----
    def effect(self, name, msg, local=False):
        """Run one effect. `local` = from the controller page: no propagation, always reaches us."""
        if name == "mode":
            return self.set_vibe(msg.get("mode"), msg.get("zone"))
        if name == "speed":
            return self.set_speed(float(msg.get("value", 1)))
        name = ALIASES.get(name, name)
        if name not in EFFECTS:
            return False
        hit = (0.0, float(msg.get("intensity", 1.0))) if local else self.arrival(msg)
        if hit is None:
            return False
        delay, intensity = hit
        h, sat, v = parse_color(msg.get("color"))
        mask = zone_mask(msg.get("zone"))
        fire = lambda: self._fire(name, msg, h, sat, v, intensity, mask)
        threading.Timer(delay, fire).start() if delay > 0.01 else fire()
        self.last.appendleft({"effect": name, "color": msg.get("color", "#ffffff"), "zone": msg.get("zone") or "all",
                              "intensity": round(intensity, 2), "from": msg.get("from") or ("controller" if local else "bus"),
                              "at": time.strftime("%H:%M:%S")})
        return True

    def _fire(self, name, msg, h, sat, v, i, mask):
        num = lambda key, default: float(msg.get(key, default))
        col = {f"{name}H": h, f"{name}S": sat, f"{name}V": v}
        if name == "flash":
            self.link.set(**col, flashI=i, flashDur=num("ms", 600) / 1000, flashZ=mask, flashT=0)
        elif name == "pulse":
            self.link.set(**col, pulseI=i, pulseDur=num("seconds", 2), pulseZ=mask, pulseT=0)
        elif name == "color":
            self.link.set(tintH=h, tintS=sat, tintV=v, tintI=i, tintDur=num("seconds", 2.5), tintZ=mask, tintT=0)
        elif name == "sweep":
            self.link.set(**col, sweepI=i, sweepSpeed=max(0.1, num("speed", 1)), sweepZ=mask, sweepT=0)
        elif name == "shockwave":
            self.link.set(waveH=h, waveS=sat, waveV=v, waveI=i, waveZ=mask, waveDir=1, waveT=0)
        elif name == "halo":          # the flower's ring flares and the glow closes in over the grid
            self.link.set(waveH=h, waveS=sat, waveV=v, waveI=i, waveZ=mask, waveDir=-1, waveT=0)
        elif name == "iris":          # the circle closes and reopens: a dark ring from the flower's ring to the centre
            self.link.set(waveH=0, waveS=0, waveV=0, waveI=i, waveZ=mask, waveDir=-1, waveT=0)
        elif name == "petalfall":     # a shadow passes out over the petals
            self.link.set(waveH=0, waveS=0, waveV=0, waveI=i, waveZ=mask, waveDir=1, waveT=0)
        elif name == "sunburst":      # twelve still rays from the heart, easing in and out
            self.link.set(spiralH=h, spiralS=sat, spiralV=v, spiralI=i, spiralDur=num("seconds", 2),
                          spiralDir=0, spiralArms=12, spiralZ=mask, spiralT=0)
        elif name == "sporeburst":    # a puff of spores on the mushrooms that drifts up into the leaves
            self.link.set(rainH=h, rainS=sat, rainV=v, rainI=i, rainDur=1.2, rainZ=zone_mask("mushrooms"), rainT=0)
            threading.Timer(0.7, lambda: self.link.set(rainH=h, rainS=sat, rainV=v, rainI=i * 0.6, rainDur=1.5,
                                                       rainZ=zone_mask(["leaves_left", "leaves_right"]), rainT=0)).start()
        elif name == "rings":
            self.link.set(**col, ringsI=i, ringsDur=num("seconds", 2.5),
                          ringsCount=max(1, min(6, int(num("count", 3)))), ringsZ=mask, ringsT=0)
        elif name == "spiral":
            self.link.set(**col, spiralI=i, spiralDur=num("seconds", 3),
                          spiralDir=1 if num("spin_dir", 1) >= 0 else -1, spiralArms=3, spiralZ=mask, spiralT=0)
        elif name == "wipe":
            self.link.set(**col, wipeI=i, wipeDur=num("seconds", 3), wipeZ=mask, wipeT=0)
        elif name == "rain":
            self.link.set(**col, rainI=i, rainDur=num("seconds", 4), rainZ=mask, rainT=0)
        elif name == "comet":
            self.link.set(**col, cometI=i, cometSpeed=max(0.1, num("speed_mps", 6) / 6), cometZ=mask, cometT=0)
        elif name == "charge":
            now = time.monotonic()  # mirror the pattern's 0.12/s bleed so accumulation is right
            self.charge = min(1.0, max(0.0, self.charge - (now - self.charge_at) * 0.12) + 0.35 * i)
            self.charge_at = now
            self.link.set(**col, chargeZ=mask, chargeAmt=self.charge)

    def set_vibe(self, name, zone=None):
        """Global vibe, or with `zone` an override for those zones ('follow' clears it)."""
        if zone in (None, "", "all", []):
            if name not in VIBES:
                return False
            self.vibe = name
            self.link.set(vibe=VIBES.index(name))
            return True
        if name not in VIBES and name != "follow":
            return False
        names = []
        for item in ([zone] if isinstance(zone, str) else zone):
            names += GROUPS.get(item, [item])
        for z in names:
            if z in VIBE_ZONES:
                self.zone_vibes[z] = None if name == "follow" else name
                self.link.set(**{f"zv{VIBE_ZONES.index(z)}": -1 if name == "follow" else VIBES.index(name)})
        return True

    def follow_all(self):
        for z in VIBE_ZONES:
            self.zone_vibes[z] = None
        self.link.set(**{f"zv{k}": -1 for k in range(len(VIBE_ZONES))})

    def set_speed(self, value):
        self.speed = max(0.05, min(4.0, value))
        self.link.set(speed=self.speed)
        return True

    def set_fader(self, value):
        self.fader = max(0.0, min(1.0, value))
        self._push_master()

    def set_split(self, on):
        self.split = bool(on)
        self.link.set(split=1 if self.split else 0)

    def set_audio(self, source=None, gain=None):
        if source in AUDIO_SOURCES:
            self.audio_src = source
            self.link.set(audioSrc=AUDIO_SOURCES.index(source))
        if gain is not None:
            self.sensor_gain = max(0.1, min(8.0, float(gain)))
            self.link.set(sensorGain=self.sensor_gain)

    def set_auto(self, on=None, seconds=None):
        if seconds is not None:
            secs = max(5.0, min(120.0, float(seconds)))
            self.auto["seconds"] = round(secs)
            self.link.set(autoSeconds=secs, autoLeft=secs)
        if on is not None:
            self.auto["on"] = bool(on)
            self.link.set(autoMode=1 if on else 0, autoLeft=self.auto["seconds"])

    def _push_master(self):
        self.link.set(master=self.fader * (QUIET_LEVEL if self.mode == "quiet" else 1.0))

    def _sync_from_pixelblaze(self):
        """Adopt settings changed on the Pixelblaze's own UI, so the controller shows the truth."""
        v = self.link.vars
        if not v:
            return
        if 0 <= int(v.get("vibe", -1)) < len(VIBES):
            self.vibe = VIBES[int(v["vibe"])]
        for k, z in enumerate(VIBE_ZONES):
            o = int(v.get(f"zv{k}", -1))
            self.zone_vibes[z] = VIBES[o] if 0 <= o < len(VIBES) else None
        self.speed = float(v.get("speed", self.speed))
        self.split = bool(v.get("split", self.split))
        if 0 <= int(v.get("audioSrc", -1)) < len(AUDIO_SOURCES):
            self.audio_src = AUDIO_SOURCES[int(v["audioSrc"])]
        self.sensor_gain = float(v.get("sensorGain", self.sensor_gain))
        self.auto = {"on": bool(v.get("autoMode", 0)), "seconds": round(float(v.get("autoSeconds", 30))),
                     "left": round(float(v.get("autoLeft", 0)))}

    def state(self):
        self._sync_from_pixelblaze()
        return {"vibe": self.vibe, "zone_vibes": self.zone_vibes, "mode": self.mode, "bpm": self.bpm,
                "fader": self.fader, "speed": self.speed, "split": self.split,
                "audio_src": self.audio_src, "sensor_gain": self.sensor_gain, "audio": self.link.audio,
                "auto": self.auto,
                "pixelblaze": self.link.connected, "error": self.link.last_error, "last": list(self.last)}

    # ---- bus handlers ----
    def on_cmd(self, topic, msg):
        if self.effect(topic.rsplit("/", 1)[-1], msg):
            self.node.count()

    def on_beat(self, topic, msg):
        if self.mode == "idle":
            return
        self.bpm = float(msg.get("bpm", self.bpm))
        self.link.set(bpm=self.bpm, beatT=0)

    def on_bpm(self, topic, msg):
        self.bpm = float(msg.get("bpm", 0))
        self.link.set(bpm=self.bpm if self.mode != "idle" else 0)

    def on_energy(self, topic, msg):
        self.link.set(**{k: float(msg.get(k, 0)) for k in ("bass", "mid", "high", "loud")})

    def on_mode(self, topic, msg):
        self.mode = msg.get("mode", "show")
        self._push_master()
        if self.mode == "idle":
            self.link.set(bpm=0, bass=0, mid=0, high=0, loud=0)

    def on_sync(self, topic, msg):
        self.link.set(bpmSync=1 if msg.get("bpm_sync", True) else 0, pulseSync=1 if msg.get("pulse", True) else 0)

    def on_map(self, topic, msg):
        if "lat" in msg and "lon" in msg:
            self.pos = {"lat": float(msg["lat"]), "lon": float(msg["lon"])}


def pattern_source() -> str:
    """The pattern with this facade's zone table baked in (see tools/facade_zones.py)."""
    from facade_zones import load_zones, zone_runs
    src = PATTERN_FILE.read_text(encoding="utf-8")
    # (the mirror table used to be baked here too; the pattern now computes it at start-up)
    for marker, value in [("var ZONE_RUNS = [0, 4]  // @zones", f"var ZONE_RUNS = {json.dumps(zone_runs(load_zones(MAP_RUN)))}")]:
        assert marker in src, f"marker missing from pattern: {marker}"
        src = src.replace(marker, value)
    return src


def deploy(ip: str):
    preview = cv2.imencode(".jpg", np.zeros((150, 100, 3), np.uint8))[1].tobytes()
    with Pixelblaze(ip) as pb:
        import pb_compile
        pb_compile.install(pb)          # compile with a cached copy of the web UI's compiler
        existing = next((k for k, v in pb.getPatternList(forceRefresh=True).items() if v == PATTERN_NAME), None)
        pid = pb.savePattern(previewImage=preview, sourceCode=pattern_source(),
                             name=PATTERN_NAME, id=existing)
        pb.pauseSequencer()             # temporary: keeps the playlist from swapping it out (not saved)
        pb.setActivePattern(pid)
        time.sleep(1)
        # plain numbers (a slider sent as [x] gets saved as garbage), saved so they survive reloads
        pb.wsSendJson({"setControls": DEFAULT_CONTROLS, "save": True})
        time.sleep(0.5)
        print(f'{"updated" if existing else "installed"} "{PATTERN_NAME}" ({pid}) with default control values')


def main():
    ap = argparse.ArgumentParser(description="Camp-bus responder for the DJ facade Pixelblaze")
    ap.add_argument("--ip", default=DEFAULT_IP, required=not DEFAULT_IP, help="Pixelblaze address (or set PIXELBLAZE_IP)")
    ap.add_argument("--host", default=None, help="MQTT broker (default $CAMPBUS_HOST or campbus.local)")
    ap.add_argument("--console-port", type=int, default=8120)
    ap.add_argument("--control-port", type=int, default=8121, help="facade controller page")
    ap.add_argument("--deploy", action="store_true", help="install/update the Facade Bus pattern, then run")
    ap.add_argument("--lan", action="store_true",
                    help="serve the controller on all interfaces (0.0.0.0) instead of localhost; it has no auth")
    args = ap.parse_args()

    if args.deploy:
        deploy(args.ip)

    node = Node(NODE_ID, **({"host": args.host} if args.host else {}), fw="facade-bridge-0.2", role="responder")
    link = PixelblazeLink(args.ip)
    facade = Facade(link, node)

    def identify():
        facade.effect("flash", {"color": "#ffffff", "ms": 1500}, local=True)
        return "flashed white"

    def next_vibe():
        facade.set_vibe(VIBES[(VIBES.index(facade.vibe) + 1) % len(VIBES)])
        return f"vibe: {facade.vibe}"

    def status():
        return (f"pixelblaze {'connected' if link.connected else 'DOWN ' + link.last_error}; "
                f"vibe {facade.vibe}; mode {facade.mode}; bpm {facade.bpm:.0f}")

    console.serve(node, port=args.console_port, title="DJ facade",
                  actions={"Identify": identify, "Next vibe": next_vibe, "Status": status})
    from facade_control import serve_controller
    control_host = "0.0.0.0" if args.lan else "127.0.0.1"
    if args.lan:
        print(f"WARNING: the facade controller on :{args.control_port} is reachable from the whole LAN "
              "and has no authentication - anyone on the network can drive the lights. Use only on a trusted network.")
    serve_controller(facade, port=args.control_port, host=control_host)

    node.subscribe(f"cmd/{TARGET}/#", facade.on_cmd)
    node.subscribe("cmd/all/#", facade.on_cmd)
    node.subscribe("audio/beat", facade.on_beat)
    node.subscribe("audio/bpm", facade.on_bpm)
    node.subscribe("audio/energy", facade.on_energy)
    node.subscribe("camp/mode", facade.on_mode)
    node.subscribe("camp/sync", facade.on_sync)
    node.subscribe(f"map/{NODE_ID}", facade.on_map)

    import atexit
    import signal
    atexit.register(link.close)
    signal.signal(signal.SIGTERM, lambda *_: (link.close(), sys.exit(0)))
    node.connect()
    print(f"facade bridge: broker {node.host}:{node.port}, pixelblaze {args.ip}, "
          f"console :{args.console_port}, controller :{args.control_port}")
    node.wait()


if __name__ == "__main__":
    main()
