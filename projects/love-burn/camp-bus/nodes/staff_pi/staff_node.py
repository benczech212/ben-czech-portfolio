#!/usr/bin/env python3
"""staff_pi - the staff prop, on real hardware, for the first time.

A Raspberry Pi Zero W standing in for the eventual ESP32 staff. It publishes the
exact `prop/<prop_id>` payload from docs/SCHEMA.md, so the virtual camp cannot tell
it from the simulator - which is the whole point: the sim is the acceptance test.

What is real here and what is not (handbook/devices/props.md, step order):
  real    - the bus contract, node lifecycle, and lat/lon/acc_m from a PA1010D
  real    - with `--cast`, `gesture`/`intensity`/`spin_dir`/`heading` come off the
            BNO055: hold the staff still for 2 s to arm, then sweep it. See gestures.py
  faked   - without `--cast`, `gesture` and `intensity` still come from a keypress
  absent  - nothing classifies slam or swing yet; a sweep is the one real gesture
  extra   - a WM8960 HAT plays .wav files from ~/sounds, from the page (--web) and,
            only if --sound-map is given, on a cast. See sound.py
  extra   - a seesaw rotary encoder at 0x36 picks the spell colour (`color`), glows
            dim in it, and flashes it on every cast. See knob.py

Usage on the Pi:

    ./staff_node.py --host campbus.local                 # interactive: 1/2/3 cast
    ./staff_node.py --host campbus.local --fire slam     # one-shot, good over ssh
    ./staff_node.py --host campbus.local --track         # publish live position for the GPS page
    ./staff_node.py --host campbus.local --track --motion-gate   # ... and idle the GPS when still
    ./staff_node.py --imu-test 60                       # IMU only: tune the motion thresholds
    ./staff_node.py --web --lan --motion-gate --track   # live sensor page on http://<pi>:8081/
    ./staff_node.py --host campbus.local --cast --web --lan  # cast by gesture: hold still 2s, then sweep
    ./staff_node.py --gps-test 120                      # GPS only, no broker needed
    ./staff_node.py --dry-run --fire slam               # print the payload, publish nothing
    ./staff_node.py --web --sound-map spin=magic/08-teleport.wav   # a sweep also plays a sound

Never publishes to cmd/. Survives a dead broker (campbus retries forever) and a
missing or unfixed GPS (lat/lon go out as null).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from typing import Optional

try:
    from campbus import Node
except ImportError:                                   # --dry-run must work anywhere
    Node = None

def _my_ip() -> str:
    """Best-guess LAN address, only so the log line is something you can actually type."""
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "0.0.0.0"


GESTURES = ("slam", "spin", "swing")
COLORS = ["#ff4000", "#00c8ff", "#8b5cf6", "#22c55e", "#facc15", "#ec4899"]
FW = "staff-pi-0.2"
# Direct WiFi, so there is no ESP-NOW bridge to stamp `via`/`rssi`. Omitting rssi
# keeps the payload small; the bridge adds both later. Budget is ~320 bytes.
VIA = "wifi"


# The sound player and the spell knob, if there are any. Module-level only so that
# emit() - which every cast goes through, keypress or gesture - can hand a cast to
# them without threading new parameters through every caller. None is always safe.
SOUND = None
KNOB = None
KNOB_WHY = "not started"
STARTUP_SOUND_WINDOW = 120.0      # s after start in which a late card still gets the startup sound
RETRY_STOP = threading.Event()   # set on exit, ends any keep_trying() loops


def keep_trying(name: str, attempt, fast: float = 5.0, fast_for: float = 120.0,
                slow: float = 30.0) -> threading.Thread:
    """Call attempt() until it returns True: every `fast` s for the first `fast_for`
    s, then every `slow` s. For parts that turn up late (a driver that probes after
    we start) or get plugged in later (the knob)."""
    born = time.monotonic()

    def run():
        while True:
            age = time.monotonic() - born
            if RETRY_STOP.wait(fast if age < fast_for else slow):
                return
            try:
                if attempt():
                    return
            except Exception as exc:          # a bug here must not kill the node
                print("[%s] retry failed: %s" % (name, exc), file=sys.stderr)

    t = threading.Thread(target=run, daemon=True, name=name)
    t.start()
    return t


def open_knob(args, verbose: bool = True):
    """The spell knob, or None. Never fatal: without it the staff casts in --color."""
    global KNOB_WHY
    if args.no_knob:
        KNOB_WHY = "off (--no-knob)"
        return None
    if args.fire:
        KNOB_WHY = "not used for a one-shot --fire"
        return None                       # its reset alone takes 0.5 s; a one-shot has no use for it
    from knob import SpellKnob, hex_to_hue

    def recolour(color):
        args.color = color                # the knob's hue is the colour every cast carries

    def preview():
        if SOUND is not None:             # the flash itself is drawn by the knob
            SOUND.on_cast({"gesture": "preview", "spin_dir": None})

    def attempt():
        return SpellKnob.open(addr=args.knob_addr, hue=hex_to_hue(args.color),
                              step=args.knob_step, rest=args.knob_rest,
                              reverse=args.knob_reverse, hz=args.knob_hz,
                              on_change=recolour, on_press=preview,
                              state_file=args.knob_state or None, intro=args.knob_intro)

    def found(knob, late: float = 0.0):
        global KNOB_WHY
        KNOB_WHY = ""
        knob.start()                      # the rainbow doubles as "found it"
        if verbose:
            print("[knob] %sseesaw %s at 0x%02x - spell colour %s (hue %.0f), %.0f Hz"
                  % ("found after %.0f s - " % late if late else "", knob.product,
                     args.knob_addr, knob.color, knob.hue, args.knob_hz))
        return knob

    knob, why = attempt()
    if knob is not None:
        return found(knob)

    import importlib.util

    # Unplugged, or plugged in later: keep looking, the same way as for a late sound
    # card. A failed look is one NACKed address on the bus - cheap. But a machine with
    # no I2C at all (the dev PC) will never grow one, so do not bother there.
    retry = os.path.exists("/dev/i2c-1") and importlib.util.find_spec("smbus2") is not None
    KNOB_WHY = why + (" - looking again every 5 s" if retry else "")
    if verbose:
        print("[knob] unavailable: %s - casting in %s" % (KNOB_WHY, args.color), file=sys.stderr)
    if retry:
        born = time.monotonic()

        def again():
            global KNOB, KNOB_WHY
            k, w = attempt()
            if k is None:
                late = time.monotonic() - born
                KNOB_WHY = w + " - looking again every %d s" % (5 if late < 120 else 30)
                return False
            KNOB = found(k, time.monotonic() - born)
            return True

        keep_trying("knob-wait", again)
    return None


def open_sound(args, verbose: bool = True):
    """The WM8960 player, or None. Like every other peripheral here, never fatal: no
    card means no sound, and the page says why."""
    if args.no_sound or not (args.web or args.sound_map or args.startup_sound):
        return None                       # nothing would ever ask it to play
    from sound import SoundPlayer, parse_map

    def ready(player, late: float) -> None:
        """The card is usable - at start, or when the driver finally probes. Volume
        first, then the startup sound, so the first thing heard is at the set level."""
        if args.sound_volume is not None:
            player.set_volume(args.sound_volume)
        if verbose:
            print("[sound] %s%d sounds in %s -> %s, volume %s%%%s"
                  % ("card appeared after %.0f s - " % late if late else "",
                     len(player.files), player.dir, player.device,
                     player.volume if player.volume is not None else "?",
                     ", following casts %s" % player.cast_map if player.cast_map else ""))
        # A power-up sound ten minutes after power-up is not a startup sound, it is a
        # surprise - so a card that turns up very late stays quiet until asked.
        if args.startup_sound and not args.fire and late <= STARTUP_SOUND_WINDOW:
            if not player.play(args.startup_sound):
                print("[sound] startup sound %s: %s" % (args.startup_sound, player.last_error),
                      file=sys.stderr)

    player = SoundPlayer(args.sounds, args.sound_device, args.sound_mixer,
                         cast_map=parse_map(args.sound_map), on_ready=ready,
                         pan_reverse=args.pan_reverse)
    if player.available:
        ready(player, 0.0)
    elif verbose:
        print("[sound] unavailable: %s" % player.why, file=sys.stderr)
    return player


def cast_payload(prop_id: str, gesture: str, intensity: float, color: str,
                 fix=None, heading: Optional[float] = None,
                 spin_dir: Optional[int] = None) -> dict:
    """The one payload shape every prop publishes. Mirrors sim/inputs.py prop()."""
    return {
        "prop": "staff",
        "prop_id": prop_id,
        "gesture": gesture,
        "intensity": round(float(intensity), 2),
        "color": str(color),
        "heading": None if heading is None else float(heading) % 360,
        "spin_dir": None if spin_dir is None else int(spin_dir),
        "lat": None if fix is None else fix.lat,
        "lon": None if fix is None else fix.lon,
        "acc_m": None if fix is None else fix.acc_m,
        "via": VIA,
    }


def open_gps(enabled: bool, configure: bool, verbose: bool = True):
    """Start the GPS reader, or return None and say why. Never fatal: the staff
    works without a position, it just loses propagation."""
    if not enabled:
        return None
    try:
        from pa1010d import PA1010D

        gps = PA1010D()
        if configure:
            gps.configure()
        return gps.start()
    except Exception as exc:                          # no smbus2, no bus, no module
        if verbose:
            print("[gps] unavailable (%s: %s) - casting without a position"
                  % (exc.__class__.__name__, exc), file=sys.stderr)
        return None


def open_imu(enabled: bool, args, verbose: bool = True):
    """Start the IMU, or return None and say why. Like the GPS, never fatal - without
    it the staff simply keeps the GPS running all the time."""
    if not enabled:
        return None
    try:
        from bno055 import BNO055

        return BNO055(accel_thresh=args.accel_thresh, gyro_thresh=args.gyro_thresh,
                      samples=args.motion_samples,
                      accel_window=args.accel_filter, gyro_window=args.gyro_filter,
                      filter_mode=args.filter_mode).start(getattr(args, "imu_hz", 10.0))
    except Exception as exc:
        if verbose:
            print("[imu] unavailable (%s: %s) - no motion gating"
                  % (exc.__class__.__name__, exc), file=sys.stderr)
        return None


class MotionGate:
    """Run the GPS only while the staff is moving.

    A stationary GPS is the worst of both worlds: it burns power and battery to
    re-measure a position that is not changing, and its drift shows up as movement
    that never happened. So: while moving, track normally and keep the recent fixes.
    Once the IMU has seen nothing for `still_seconds`, average those fixes, publish
    that average once, and stop reading the module until something moves again.

    The held average is still the staff's position - `fix()` returns it while idle,
    so a cast thrown while standing still carries `lat`/`lon` exactly as before.
    Without that, standing still would silently strip the position off every cast.
    """

    def __init__(self, gps, imu, args):
        self.gps, self.imu, self.args = gps, imu, args
        self.recent = []              # fixes seen during the current moving stretch
        self.idle = False
        self.held = None              # averaged Fix, valid while idle
        self.changed = False          # set on a transition, cleared by the caller
        self.transitions = 0

    @property
    def active(self) -> bool:
        return self.gps is not None and self.imu is not None and self.args.motion_gate

    def fix(self):
        """The staff's position: live while moving, the held average while idle."""
        if not self.active:
            return usable_fix(self.gps, self.args.fix_max_age)
        if self.idle:
            return self.held
        return usable_fix(self.gps, self.args.fix_max_age)

    def tick(self) -> None:
        if not self.active:
            return
        if self.idle:
            if self.imu.moving:
                self.gps.resume()
                self.recent = []
                self.idle = False
                self.held = None
                self.changed = True
                self.transitions += 1
                print("[gate] moving - GPS back on")
            return

        f = usable_fix(self.gps, self.args.fix_max_age)
        if f is not None and (not self.recent or self.recent[-1].at != f.at):
            self.recent.append(f)
            if len(self.recent) > 120:            # ~2 min of 1 Hz fixes is plenty to average
                self.recent = self.recent[-120:]

        if self.imu.still_for >= self.args.still_seconds:
            from pa1010d import average_fixes

            # Average only the fixes taken while actually still, not the whole walk.
            window = [x for x in self.recent if x.at >= time.monotonic() - self.imu.still_for]
            self.held = average_fixes(window) or f
            if self.held is None:
                return                            # no fix at all: nothing to hold, keep looking
            self.gps.pause()
            self.idle = True
            self.changed = True
            self.transitions += 1
            print("[gate] still for %.0fs - holding %.6f,%.6f +-%sm from %d fixes, GPS idle"
                  % (self.imu.still_for, self.held.lat, self.held.lon, self.held.acc_m, len(window)))


def usable_fix(gps, max_age_s: float):
    """The latest fix, or None if it is stale/absent - so a cast never lies about
    where it was thrown from."""
    if gps is None or gps.fix is None:
        return None
    return gps.fix if gps.fix.age_s <= max_age_s else None


def describe_fix(gps, max_age_s: float, gate=None) -> str:
    if gate is not None and gate.active and gate.idle and gate.held is not None:
        return ("HOLDING %.6f,%.6f +-%sm (still %.0fs, GPS idle)"
                % (gate.held.lat, gate.held.lon, gate.held.acc_m, gate.imu.still_for))
    if gps is None:
        return "no gps"
    fix = usable_fix(gps, max_age_s)
    if fix is None:
        # Nothing on the bus at all is a wiring/power fault, not a cold sky. Say so,
        # or an unplugged module looks exactly like standing indoors.
        if gps.sentences == 0 and gps.io_errors:
            return ("NO DATA on i2c-%d 0x%02x (%d read errors) - check wiring/power, not the sky"
                    % (gps.bus_no, gps.addr, gps.io_errors))
        seen = ", %d sats in view" % gps.sats_in_view if gps.sats_in_view else ""
        return "no fix (%d sentences%s)" % (gps.sentences, seen)
    return "%.6f,%.6f +-%sm (hdop %s, %d sats, %.1fs old)" % (
        fix.lat, fix.lon, fix.acc_m, fix.hdop, fix.sats, fix.age_s)


# ---- modes -------------------------------------------------------------------

def run_gps_test(seconds: float, configure: bool) -> int:
    """Answer the open question in handbook/03-state-of-play.md: is this module good
    enough, and how long must the calibration average be? Needs no broker."""
    from statistics import mean, pstdev

    started = time.monotonic()
    gps = open_gps(True, configure)
    if gps is None:
        return 1

    print("PA1010D on i2c-%d 0x%02x - %.0fs test, Ctrl-C to stop early"
          % (gps.bus_no, gps.addr, seconds))
    ttff: Optional[float] = None
    samples = []
    try:
        while time.monotonic() - started < seconds:
            time.sleep(1.0)
            fix = gps.fix
            if fix is None:
                print("  %5.0fs  no fix   sentences=%d bad_ck=%d io_err=%d sats_in_view=%d"
                      % (time.monotonic() - started, gps.sentences, gps.bad_checksums,
                         gps.io_errors, gps.sats_in_view))
                continue
            if ttff is None:
                ttff = time.monotonic() - started
                print("  *** first fix after %.1fs ***" % ttff)
            if not samples or fix.at != samples[-1].at:
                samples.append(fix)
            print("  %5.0fs  %.6f,%.6f  hdop=%-5s sats=%-3d acc_m=%s"
                  % (time.monotonic() - started, fix.lat, fix.lon, fix.hdop, fix.sats, fix.acc_m))
    except KeyboardInterrupt:
        print()

    print("\nsentences=%d bad_checksums=%d io_errors=%d fixes=%d"
          % (gps.sentences, gps.bad_checksums, gps.io_errors, len(samples)))
    if gps.sentences == 0 and gps.io_errors:
        # Every read failed, so the module never spoke. A cold sky still produces
        # sentences (with an empty fix), so this is electrical, not astronomical.
        print("NO DATA: nothing answered at 0x%02x on i2c-%d - %d failed reads."
              % (gps.addr, gps.bus_no, gps.io_errors))
        print("Check power and SDA/SCL, then: sudo i2cdetect -y %d  (expect 0x10)" % gps.bus_no)
        print("If that scan is empty while raspi-gpio get 2,3 shows func=SDA1/SCL1 at level=1,")
        print("the bus is fine and the module is not connected to it.")
        gps.stop()
        return 3
    if ttff is None:
        print("NO FIX. Indoors, or the antenna is blocked - retry with a clear view of the sky.")
        print("(The module is talking - %d sentences - it just cannot see satellites.)" % gps.sentences)
        gps.stop()
        return 2
    print("TTFF: %.1fs" % ttff)
    if len(samples) >= 2:
        lats = [f.lat for f in samples]
        lons = [f.lon for f in samples]
        m_per_deg_lat = 111320.0
        m_per_deg_lon = 111320.0 * max(0.01, abs(math.cos(math.radians(mean(lats)))))
        spread_n = pstdev(lats) * m_per_deg_lat
        spread_e = pstdev(lons) * m_per_deg_lon
        print("scatter over %d fixes: %.1fm N/S, %.1fm E/W (1 sigma); mean hdop %.1f"
              % (len(samples), spread_n, spread_e, mean(f.hdop for f in samples)))
        print("averaged position: %.6f,%.6f" % (mean(lats), mean(lons)))
        print("\nRule of thumb: the calibration average must run long enough that this")
        print("scatter falls below the spacing between camp nodes. Record it in the handbook.")
    gps.stop()
    return 0


def start_tracker(node, args, gps, gate):
    """Publish where the staff is, ~1/s, on its own retained `map/<prop_id>`.

    Reuses the existing `map/<node_id>` shape rather than inventing a topic: it is
    already "where this node is", already retained, and nothing routes it (the engine
    does not subscribe to map/), so a moving prop publishing its own position cannot
    loop or trigger anything. Retained means a page opened later still knows where the
    staff was. `sats`/`hdop` are optional additions for the GPS page - useful for
    judging a fix, ignored by everything else.
    """
    stop = threading.Event()

    def run():
        while not stop.wait(1.0 / max(0.1, args.track_hz)):
            gate.tick()
            # While idle there is nothing new to say: the retained message already holds
            # the averaged position. Publish once on the transition, then go quiet - that
            # is the whole point of the gate. `moving` tells readers which it is, so a
            # held position is never mistaken for a stale one.
            if gate.active and gate.idle and not gate.changed:
                continue
            fix = gate.fix()
            if fix is None:
                continue
            payload = {"lat": fix.lat, "lon": fix.lon, "acc_m": fix.acc_m,
                       "sats": fix.sats, "hdop": fix.hdop}
            if gate.active:
                payload["moving"] = not gate.idle
            gate.changed = False
            node.publish("map/%s" % args.prop_id, payload, retain=True)

    threading.Thread(target=run, daemon=True).start()
    return stop


def run_tracker_forever(args, gps, gate) -> None:
    """Headless: no tty to read keypresses from. Position and gestures both come from
    hardware, so there is nothing to do here but stay alive and narrate."""
    if args.track:
        print("[track] publishing map/%s at %.1f Hz - Ctrl-C to stop" % (args.prop_id, args.track_hz))
    else:
        print("[cast] waiting for gestures - Ctrl-C to stop")
    last = ""
    try:
        while True:
            time.sleep(2.0)
            if not args.track:
                continue                         # casting only: the cast log is the narration
            line = describe_fix(gps, args.fix_max_age, gate)
            if line != last:                     # only speak when something changed
                print("[track] %s" % line)
                last = line
    except KeyboardInterrupt:
        pass


def run_imu_test(seconds: float, args) -> int:
    """Watch the IMU and the motion decision, so the thresholds can be set from what
    the hardware actually reads rather than from the datasheet. Needs no broker."""
    imu = open_imu(True, args)
    if imu is None:
        return 1
    print("BNO055 on i2c-%d 0x%02x - %.0fs. Thresholds: accel > %.2f m/s^2 or gyro > %.1f deg/s"
          % (imu.bus_no, imu.addr, seconds, imu.accel_thresh, imu.gyro_thresh))
    print("Leave it still, then pick it up. Calib is sys/gyro/accel/mag, each 0-3;")
    print("heading needs mag 3, motion detection needs none of it.\n")
    started = time.monotonic()
    peak_still = 0.0
    try:
        while time.monotonic() - started < seconds:
            time.sleep(0.5)
            try:
                cal = imu.calibration()
            except OSError:
                cal = (0, 0, 0, 0)
            state = "MOVING" if imu.moving else "still %4.1fs" % imu.still_for
            if not imu.moving:
                peak_still = max(peak_still, imu.accel_mag)
            print("  %5.0fs  %-12s accel %5.2f m/s^2   gyro %6.1f deg/s   heading %5.1f   calib %d/%d/%d/%d"
                  % (time.monotonic() - started, state, imu.accel_mag, imu.gyro_mag,
                     imu.heading if imu.heading is not None else -1, *cal))
    except KeyboardInterrupt:
        print()
    print("\nreads=%d io_errors=%d bad_frames=%d" % (imu.reads, imu.io_errors, imu.bad_frames))
    total = imu.reads + imu.bad_frames
    if total and imu.bad_frames / total > 0.02:
        print("%.0f%% of frames were CORRUPT - the Pi I2C controller mishandles this"
              % (100.0 * imu.bad_frames / total))
        print("chip's clock stretching. They are dropped, but fix the bus properly:")
        print("  dtparam=i2c_arm_baudrate=10000  in /boot/firmware/config.txt, reboot")
    if imu.reads == 0:
        print("The chip never answered - check the wiring, not the thresholds.")
        imu.stop()
        return 2
    print("Highest acceleration seen while judged still: %.2f m/s^2 (threshold %.2f)."
          % (peak_still, imu.accel_thresh))
    if peak_still > imu.accel_thresh * 0.8:
        print("That is close to the threshold - raise --accel-thresh or it will twitch awake.")
    imu.stop()
    return 0


def open_detector(args):
    """The gesture detector, or None. Pure software, so the only reason it is absent
    is that nobody asked for it."""
    if not args.cast:
        return None
    from gestures import CastDetector

    return CastDetector(arm_seconds=args.arm_seconds, arm_accel=args.arm_accel,
                        arm_gyro=args.arm_gyro, arm_timeout=args.arm_timeout,
                        trigger_dps=args.trigger_dps, min_deg=args.min_deg,
                        dir_purity=args.dir_purity, axis_purity=args.axis_purity,
                        full_dps=args.full_dps, cooldown_s=args.cast_cooldown,
                        sleep_after=args.cast_sleep_after)


def wire_casting(node, args, imu, gate, det, last_cast: dict):
    """Hang the detector off the IMU's sample loop and publish what it finds.

    The hook runs on the IMU thread, which is the point: a gesture is decided from
    the same samples the motion gate uses, with nothing in between to add latency or
    to disagree about what the staff did.
    """
    if det is None or imu is None:
        return

    base_hz, cast_hz = args.imu_hz, args.cast_hz
    cue = {"charging": False, "glow": False, "state": det.state}

    def cues(t: float, cast) -> None:
        """Three sounds follow the arm (Ben, 2026-10-04):

          CHARGING  the staff starts holding still -> --charge-sound, building toward
                    the arm. It moves before arm-seconds -> stopped at once, and it
                    starts over the next time the staff holds still.
          READY     the hold completes and it ARMS -> --ready-sound, once. Starting it
                    stops the charge sound (a new play always stops the old one).
          CAST      a cast -> --sound-map, from emit(). Replaces whatever is playing.

        Hysteresis: the detector drops out of ARMING on a single unsteady sample, so a
        staff hovering near the threshold flickers in and out every frame. The charge
        sound waits until ARMING has lasted --charge-debounce, so jitter never gets far
        enough to start it, let alone restart it over and over. Stopping is immediate.

        Runs on the IMU thread: everything below only queues (SOUND.later) or sets a
        timestamp (KNOB) - aplay is started and stopped on the sound worker."""
        state, was = det.state, cue["state"]
        cue["state"] = state
        snd = SOUND if (SOUND is not None and SOUND.available) else None

        holding = (state == "arming" and det.still_since is not None
                   and t - det.still_since >= args.charge_debounce)
        if holding and not cue["charging"]:
            cue["charging"] = True
            if snd and args.charge_sound:
                snd.later(snd.play, args.charge_sound)
        elif cue["charging"] and state != "arming":
            cue["charging"] = False
            if state == "armed" and snd and args.ready_sound:
                snd.later(snd.play, args.ready_sound)        # replaces the charge sound
            elif snd and args.charge_sound:
                snd.later(snd.stop_if, args.charge_sound)    # moved too soon: silence
        elif state == "armed" and was == "arming" and snd and args.ready_sound:
            snd.later(snd.play, args.ready_sound)            # armed inside the debounce

        # The knob glow climbs while charging and stays up while armed. A cast hands
        # over to the knob's flash (emit() calls KNOB.cast()).
        glow = holding or state in ("armed", "capture")
        if glow != cue["glow"] and cast is None:
            cue["glow"] = glow
            if KNOB is not None:
                KNOB.charge_ramp = max(0.5, args.arm_seconds - args.charge_debounce)
                KNOB.charging(glow)
        elif cast is not None:
            cue["glow"] = False

    def on_sample(t, frame):
        # Filtered vectors, not the raw frame: the turn rate is a projection of the
        # gyro onto gravity, so if the detector read raw gyro while the gate read
        # filtered, the two would disagree about what the staff just did.
        cast = det.feed(t, imu.gyro_vec, imu.gravity,
                        imu.accel_mag, imu.gyro_mag, frame["heading"])
        # Resolution only where it is needed: wind the sample rate up the moment the
        # staff is armed, back down as soon as the moment has passed. The GPS shares
        # this bus and is the thing that suffers if we sample fast all the time.
        # Armed and capturing are both "charged": the sweep that becomes the cast is
        # happening inside the charge, so the sound carries on through it.
        imu.set_hz(cast_hz if det.state in ("armed", "capture") else base_hz)
        cues(t, cast)
        if cast is None:
            return

        # An uncalibrated magnetometer gives a heading that is confidently wrong, and
        # the contract already allows null. A spiral pointing nowhere beats one
        # pointing the wrong way, so we say nothing rather than something false.
        heading = cast["heading"]
        if args.heading_min_cal and imu.calib[3] < args.heading_min_cal:
            heading = None

        payload = cast_payload(args.prop_id, cast["gesture"], cast["intensity"],
                               args.color, gate.fix(), heading=heading,
                               spin_dir=cast["spin_dir"])
        last_cast["payload"] = payload
        last_cast["sweep_deg"] = cast["sweep_deg"]
        last_cast["at"] = time.time()
        print("[cast] sweep %+.0f deg -> spin_dir %+d  intensity %.2f  heading %s"
              % (cast["sweep_deg"], cast["spin_dir"], cast["intensity"],
                 "%.0f" % heading if heading is not None else "null"))
        emit(node, args, payload)

    imu.sample_hook = on_sample


def sensor_state(args, gps, imu, gate, node, det=None, last_cast=None, sound=None) -> dict:
    """Everything the web page shows, in one snapshot.

    Read straight off the shared sensor objects - the page is a *view* of the node
    that is already running, never a second reader. Two processes on this I2C bus is
    how a marginal bus becomes a broken one.
    """
    out = {"node": {"id": args.prop_id, "connected": bool(node and node.connected)}}

    if imu is None:
        out["imu"] = {"present": False,
                      "why": "no BNO055 (run with --motion-gate, and check 0x28 is on the bus)"}
    else:
        total = imu.reads + imu.bad_frames
        out["imu"] = {
            "present": True, "moving": imu.moving, "still_for": round(imu.still_for, 2),
            "accel": round(imu.accel_mag, 3), "gyro": round(imu.gyro_mag, 2),
            "heading": None if imu.heading is None else round(imu.heading, 1),
            "accel_thresh": imu.accel_thresh, "gyro_thresh": imu.gyro_thresh,
            "accel_raw": round(imu.accel_raw, 3), "gyro_raw": round(imu.gyro_raw, 2),
            "accel_filter": imu.accel_filter.window,
            "gyro_filter": imu.gyro_filter.window,
            "filter_mode": imu.filter_mode,
            "reads": imu.reads, "bad_frames": imu.bad_frames, "io_errors": imu.io_errors,
            "bad_pct": round(100.0 * imu.bad_frames / total, 2) if total else 0.0,
            # Read from the poll loop's cache, never from the bus: this is asked for
            # ten times a second and an I2C round trip per web request would compete
            # with the sensor it is reporting on.
            "calib": list(imu.calib),
            "hz": round(imu.hz, 1),
            "gravity": [round(v, 2) for v in imu.gravity],
            # Full attitude, for the orientation view. Euler angles are here too because
            # they are what a person reads; the quaternion is what a renderer wants.
            "quat": [round(v, 4) for v in imu.quat],
            "roll": None if imu.roll is None else round(imu.roll, 1),
            "pitch": None if imu.pitch is None else round(imu.pitch, 1),
        }

    if gps is None:
        out["gps"] = {"present": False}
    else:
        f = gate.fix()
        out["gps"] = {
            "present": True, "paused": gps.paused, "sentences": gps.sentences,
            "bad_checksums": gps.bad_checksums, "io_errors": gps.io_errors,
            "fix": None if f is None else {"lat": f.lat, "lon": f.lon, "acc_m": f.acc_m,
                                           "sats": f.sats, "hdop": f.hdop},
        }

    out["gate"] = {"active": gate.active, "idle": gate.idle,
                   "still_seconds": args.still_seconds, "transitions": gate.transitions}

    if det is None:
        out["cast"] = {"present": False,
                       "why": "gesture casting is off - start with --cast"}
    else:
        out["cast"] = dict(det.snapshot(), present=True)
        if last_cast and last_cast.get("payload"):
            out["cast"]["last"] = {"payload": last_cast["payload"],
                                   "sweep_deg": last_cast.get("sweep_deg"),
                                   "ago": round(time.time() - last_cast["at"], 1)}

    out["sound"] = sound.state() if sound is not None else {
        "present": False, "why": "sound is off (--no-sound)" if args.no_sound else "no player"}
    out["knob"] = KNOB.state() if KNOB is not None else {
        "present": False, "why": KNOB_WHY, "color": args.color}
    return out


# Which knob on the page moves which number. Everything here is a float and none of
# it is persisted: this is for *finding* the numbers, and the ones you settle on
# belong in the systemd unit where they survive a reboot.
CONFIG_KEYS = {
    "accel":       ("imu", "accel_thresh"),
    "gyro":        ("imu", "gyro_thresh"),
    "still":       ("args", "still_seconds"),
    "arm_seconds": ("det", "arm_seconds"),
    "arm_accel":   ("det", "arm_accel"),
    "arm_gyro":    ("det", "arm_gyro"),
    "arm_timeout": ("det", "arm_timeout"),
    "trigger_dps": ("det", "trigger_dps"),
    "min_deg":     ("det", "min_deg"),
    "dir_purity":  ("det", "dir_purity"),
    "axis_purity": ("det", "axis_purity"),
    "sleep_after": ("det", "sleep_after"),
    "accel_filter": ("imu", "accel_filter.window"),
    "gyro_filter":  ("imu", "gyro_filter.window"),
    "filter_mode":  ("imu", "filter_mode"),
}


def apply_config(args, imu, det, patch: dict) -> None:
    """Live tuning from the page."""
    targets = {"imu": imu, "args": args, "det": det}
    applied = []
    for key, value in patch.items():
        where, attr = CONFIG_KEYS.get(key, (None, None))
        obj = targets.get(where)
        if obj is None:
            continue
        *path, leaf = attr.split(".")        # "accel_filter.window" reaches inside
        for step in path:
            obj = getattr(obj, step, None)
            if obj is None:
                break
        if obj is None:
            continue
        setattr(obj, leaf, value)
        applied.append("%s=%s" % (key, value))
    if applied:
        print("[web] " + "  ".join(applied))


def emit(node, args, payload: dict) -> None:
    topic = "prop/%s" % args.prop_id
    blob = json.dumps(payload, separators=(",", ":"))
    print("%s  %s  (%d bytes)" % (topic, blob, len(blob)))
    if node is not None:
        node.publish(topic, payload)
        node.count()
    # After the publish, never before: the camp hears about a cast first. on_cast
    # returns at once and does nothing at all unless --sound-map was given.
    if SOUND is not None:
        SOUND.on_cast(payload)
    if KNOB is not None:
        KNOB.cast()                       # sets a timestamp; the knob's own thread draws


def run_once(node, args, gate) -> None:
    payload = cast_payload(args.prop_id, args.fire, args.intensity, args.color,
                           gate.fix(),
                           spin_dir=args.spin_dir if args.fire == "spin" else None)
    emit(node, args, payload)


def run_interactive(node, args, gps, gate) -> None:
    color_i = COLORS.index(args.color) if args.color in COLORS else 0
    intensity = args.intensity
    spin_dir = args.spin_dir

    def banner():
        print("staff=%s intensity=%.2f color=%s spin_dir=%+d | %s"
              % (args.prop_id, intensity, COLORS[color_i], spin_dir,
                 describe_fix(gps, args.fix_max_age, gate)))

    print("\n  1 slam   2 spin   3 swing   +/- intensity   c colour   d spin dir   g gps   q quit\n")
    banner()

    for key in keypresses():
        if key in ("q", "\x03", "\x04"):
            break
        if key in "123":
            gesture = GESTURES[int(key) - 1]
            emit(node, args, cast_payload(args.prop_id, gesture, intensity, COLORS[color_i],
                                          gate.fix(),
                                          spin_dir=spin_dir if gesture == "spin" else None))
        elif key in "+=":
            intensity = min(1.0, round(intensity + 0.05, 2))
            banner()
        elif key in "-_":
            intensity = max(0.0, round(intensity - 0.05, 2))
            banner()
        elif key == "c":
            color_i = (color_i + 1) % len(COLORS)
            banner()
        elif key == "d":
            spin_dir = -spin_dir
            banner()
        elif key == "g":
            banner()


def keypresses():
    """Yield single keystrokes on a tty; fall back to whole lines when piped."""
    if not sys.stdin.isatty():
        for line in sys.stdin:
            for ch in line.strip():
                yield ch
        return
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ch = sys.stdin.read(1)
            if not ch:
                return
            yield ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def build_parser() -> argparse.ArgumentParser:
    """Separate from main() so the tests can get at the real defaults. A test that
    keeps its own copy of them stops testing this program the first time one changes."""
    p = argparse.ArgumentParser(description="Camp Bus staff prop on a Raspberry Pi")
    p.add_argument("--host", default=None, help="broker (default: $CAMPBUS_HOST or campbus.local)")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--prop-id", default="staff_01")
    p.add_argument("--fire", choices=GESTURES, help="publish one cast and exit")
    p.add_argument("--intensity", type=float, default=0.9, help="0..1; 1.0 is a perfect cast")
    p.add_argument("--color", default=COLORS[0])
    p.add_argument("--spin-dir", type=int, default=1, choices=(1, -1))
    p.add_argument("--no-gps", action="store_true", help="skip the PA1010D entirely")
    p.add_argument("--configure-gps", action="store_true", help="send PMTK: GGA+RMC only, 1 Hz")
    p.add_argument("--fix-max-age", type=float, default=5.0, help="seconds before a fix is stale")
    p.add_argument("--gps-wait", type=float, default=0.0, help="wait up to N s for a first fix on start")
    p.add_argument("--track", action="store_true",
                   help="also publish live position on retained map/<prop_id> (for the GPS page)")
    p.add_argument("--track-hz", type=float, default=1.0, help="position publish rate (default 1/s)")
    p.add_argument("--web", nargs="?", type=int, const=8081, metavar="PORT",
                   help="serve the live sensor page on this port (default 8081; localhost only unless --lan)")
    p.add_argument("--lan", action="store_true",
                   help="bind the sensor page on all interfaces so a phone on the camp Wi-Fi can reach it "
                        "(no login: use only on a closed network)")
    p.add_argument("--motion-gate", action="store_true",
                   help="idle the GPS while the IMU says the staff is not moving")
    p.add_argument("--still-seconds", type=float, default=8.0,
                   help="how long with no motion before the GPS idles (default 8)")
    p.add_argument("--accel-thresh", type=float, default=0.35,
                   help="m/s^2 of linear acceleration that counts as moving")
    p.add_argument("--gyro-thresh", type=float, default=15.0,
                   help="deg/s of rotation that counts as moving")
    p.add_argument("--motion-samples", type=int, default=3,
                   help="consecutive moving reads before it counts (rejects corrupt I2C frames)")
    p.add_argument("--imu-hz", type=float, default=10.0,
                   help="IMU sample rate at rest (default 10; the GPS shares this bus)")
    p.add_argument("--accel-filter", type=int, default=3, metavar="FRAMES",
                   help="rolling window on linear acceleration, 1 = off (default 3)")
    p.add_argument("--gyro-filter", type=int, default=3, metavar="FRAMES",
                   help="rolling window on rotation, also filters the turn rate (default 3)")
    p.add_argument("--filter-mode", default="median", choices=("off", "mean", "median"),
                   help="median rejects spikes outright, mean only spreads them (default median)")
    p.add_argument("--no-imu", action="store_true", help="skip the BNO055 entirely")

    cast = p.add_argument_group(
        "gesture casting",
        "hold the staff still to arm, then sweep it - see gestures.py. Tune these on "
        "the sensor page (--web), then put what you settle on back here.")
    cast.add_argument("--cast", action="store_true",
                      help="cast from real gestures instead of keypresses")
    cast.add_argument("--cast-hz", type=float, default=50.0,
                      help="IMU sample rate while armed/capturing (default 50)")
    cast.add_argument("--arm-seconds", type=float, default=2.0,
                      help="stillness needed before the staff will listen (default 2)")
    cast.add_argument("--arm-accel", type=float, default=0.30,
                      help="m/s^2 that still enough to arm means")
    cast.add_argument("--arm-gyro", type=float, default=10.0,
                      help="deg/s that still enough to arm means")
    cast.add_argument("--arm-timeout", type=float, default=8.0,
                      help="armed and unused for this long goes back to sleep (default 8)")
    cast.add_argument("--trigger-dps", type=float, default=60.0,
                      help="rotation that starts a gesture capture (default 60)")
    cast.add_argument("--min-deg", type=float, default=45.0,
                      help="degrees of turn a sweep must cover to count (default 45)")
    cast.add_argument("--full-dps", type=float, default=400.0,
                      help="sweep speed that reads as intensity 1.0 (default 400)")
    cast.add_argument("--dir-purity", type=float, default=0.65,
                      help="how one-way the turn must be, 0..1 (rejects a shake)")
    cast.add_argument("--axis-purity", type=float, default=0.5,
                      help="how flat the turn must be, 0..1 (rejects a swing)")
    cast.add_argument("--cast-sleep-after", type=float, default=25.0,
                      help="untouched for this long and it stops arming until moved (default 25)")
    cast.add_argument("--cast-cooldown", type=float, default=0.8,
                      help="quiet time after a cast before it can re-arm")
    cast.add_argument("--heading-min-cal", type=int, default=3,
                      help="magnetometer calibration needed to publish heading (0 = always)")
    snd = p.add_argument_group(
        "sound",
        "a WM8960 Audio HAT playing .wav files - from the page's Sound panel, and on a "
        "cast only if --sound-map says which. No card is fine: the panel says so.")
    snd.add_argument("--sounds", default="~/sounds", metavar="DIR",
                     help="folder of .wav files, scanned recursively (default ~/sounds)")
    snd.add_argument("--sound-device", default="plughw:wm8960soundcard", metavar="ALSA",
                     help="aplay -D device (default plughw:wm8960soundcard)")
    snd.add_argument("--sound-mixer", default="Speaker", metavar="CONTROL",
                     help="mixer control the volume slider drives; '' for none (default Speaker)")
    snd.add_argument("--sound-volume", type=float, default=None, metavar="PCT",
                     help="set the volume at start, 0-100 (default: leave the mixer as stored)")
    snd.add_argument("--sound-map", default="", metavar="SPEC",
                     help="play a sound on a cast, e.g. 'spin=magic/08-teleport.wav' or "
                          "'spin_cw=a.wav,spin_ccw=b.wav' (default: none - casts are silent)")
    snd.add_argument("--startup-sound", default="magic/09-power-up.wav", metavar="FILE",
                     help="played once when the node starts, relative to --sounds; '' for "
                          "none (default magic/09-power-up.wav). Not for --fire")
    snd.add_argument("--charge-sound", default="magic/01-crystal-glow-a.wav", metavar="FILE",
                     help="plays while the staff is being held still to arm; stopped the "
                          "moment it moves. '' for none (default magic/01-crystal-glow-a.wav)")
    snd.add_argument("--ready-sound", default="magic/06-bell-chime-a.wav", metavar="FILE",
                     help="played once when it arms; replaces the charge sound. '' for none "
                          "(default magic/06-bell-chime-a.wav)")
    snd.add_argument("--pan-reverse", action="store_true",
                     help="swap the <gesture>_cw / _ccw cast sounds: by default a clockwise "
                          "cast plays the left-to-right sweep; this makes it right-to-left")
    snd.add_argument("--charge-debounce", type=float, default=0.25, metavar="SECONDS",
                     help="stillness needed before the charge sound starts, so jitter at the "
                          "threshold cannot restart it over and over (default 0.25)")
    snd.add_argument("--no-sound", action="store_true", help="no sound player at all")

    kn = p.add_argument_group(
        "spell knob",
        "an Adafruit I2C QT Rotary Encoder (seesaw): turn it to pick the spell colour, "
        "which every cast carries as `color`. Absent is fine - casts use --color.")
    kn.add_argument("--knob-addr", type=lambda v: int(v, 0), default=0x36, metavar="ADDR",
                    help="seesaw I2C address on bus 1 (default 0x36)")
    kn.add_argument("--knob-hz", type=float, default=25.0,
                    help="poll rate (default 25; it shares the bus with the IMU and GPS)")
    kn.add_argument("--knob-step", type=float, default=15.0,
                    help="degrees of hue per click (default 15: 24 clicks round the wheel)")
    kn.add_argument("--knob-rest", type=float, default=0.3,
                    help="resting glow, perceived brightness 0-1 (default 0.3)")
    kn.add_argument("--knob-reverse", action="store_true", help="turn the hue the other way")
    kn.add_argument("--knob-intro", type=float, default=1.0, metavar="SECONDS",
                    help="rainbow on start, then the saved hue (default 1.0; 0 = none)")
    kn.add_argument("--knob-state", default="~/.config/campbus/staff-knob.json", metavar="FILE",
                    help="where the chosen hue is remembered across restarts; '' = do not")
    kn.add_argument("--no-knob", action="store_true", help="do not look for the knob")
    p.add_argument("--imu-test", type=float, metavar="SECONDS", help="IMU diagnostics only, no broker")
    p.add_argument("--gps-test", type=float, metavar="SECONDS", help="GPS diagnostics only, no broker")
    p.add_argument("--dry-run", action="store_true", help="print payloads, connect to nothing")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.gps_test:
        return run_gps_test(args.gps_test, args.configure_gps)
    if args.imu_test:
        return run_imu_test(args.imu_test, args)

    # First, before the GPS wait (up to --gps-wait seconds): the staff says it is awake
    # with a sound and a rainbow the moment the service starts, not a minute later.
    global SOUND, KNOB
    SOUND = sound = open_sound(args)      # plays the startup sound now, or when the card appears
    KNOB = open_knob(args)                # starts with the rainbow, then the saved hue

    gps = open_gps(not args.no_gps, args.configure_gps)
    if gps is not None and args.gps_wait:
        print("[gps] waiting up to %.0fs for a fix..." % args.gps_wait)
        gps.wait_for_fix(args.gps_wait)
    print("[gps] %s" % describe_fix(gps, args.fix_max_age))

    imu = open_imu((args.motion_gate or args.cast or bool(args.web)) and not args.no_imu, args)
    gate = MotionGate(gps, imu, args)
    if args.motion_gate:
        print("[gate] %s" % ("armed: GPS idles after %.0fs still (accel > %.2f m/s^2 or gyro > %.1f deg/s)"
                             % (args.still_seconds, args.accel_thresh, args.gyro_thresh)
                             if gate.active else "requested but unavailable - GPS stays on"))

    node = None
    if not args.dry_run:
        if Node is None:
            print("campbus is not installed: pip install -e <repo>/clients/python", file=sys.stderr)
            return 1
        kwargs = {"fw": FW, "port": args.port}
        if args.host:
            kwargs["host"] = args.host
        node = Node(args.prop_id, **kwargs).connect()
        if not node.wait_ready(5.0):
            print("[bus] no broker yet at %s:%d - publishing anyway, it reconnects"
                  % (node.host, node.port), file=sys.stderr)
        else:
            print("[bus] connected to %s:%d as %s" % (node.host, node.port, args.prop_id))

    det = open_detector(args)
    last_cast: dict = {}
    if args.cast:
        if imu is None:
            print("[cast] no IMU - gesture casting is off", file=sys.stderr)
            det = None
        else:
            wire_casting(node, args, imu, gate, det, last_cast)
            print("[cast] armed by %.1fs still (accel <= %.2f, gyro <= %.0f); "
                  "sweep >= %.0f deg at >= %.0f deg/s"
                  % (args.arm_seconds, args.arm_accel, args.arm_gyro,
                     args.min_deg, args.trigger_dps))


    web = None
    if args.web:
        try:
            from webui import start_web

            web = start_web(args.web,
                            lambda: sensor_state(args, gps, imu, gate, node, det, last_cast, sound),
                            lambda patch: apply_config(args, imu, det, patch),
                            sound=sound, host="0.0.0.0" if args.lan else "127.0.0.1")
            print("[web] sensor page on http://%s:%d/" % (_my_ip() if args.lan else "127.0.0.1", args.web))
        except OSError as exc:
            print("[web] could not start on port %d: %s" % (args.web, exc), file=sys.stderr)

    tracker = start_tracker(node, args, gps, gate) if (args.track and node and gps) else None
    if args.track and gps is None:
        print("[track] no GPS - nothing to publish", file=sys.stderr)

    try:
        if not args.fire and (args.cast or args.track) and not sys.stdin.isatty():
            run_tracker_forever(args, gps, gate)       # headless: gestures and position only
        elif args.fire:
            run_once(node, args, gate)
            # campbus rate-limits node/<id>/count to 1/s and leaves the flush to its
            # heartbeat thread, so a one-shot must outlive that or the counter is lost.
            time.sleep(1.3)
        else:
            run_interactive(node, args, gps, gate)
    finally:
        if tracker is not None:
            tracker.set()
        if node is not None:
            node.close()
        if gps is not None:
            gps.stop()
        if imu is not None:
            imu.stop()
        if web is not None:
            web._campbus_stop.set()
            web.shutdown()
        RETRY_STOP.set()
        if KNOB is not None:
            KNOB.stop()
        if sound is not None:
            sound.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
