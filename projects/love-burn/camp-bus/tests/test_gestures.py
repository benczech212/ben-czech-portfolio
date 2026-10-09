"""The staff's cast detector, without a staff.

`gestures.py` is deliberately a pure function of a sample stream so it can be tested
here rather than by waving a Raspberry Pi around. What is being checked is the part
that is easy to get quietly wrong: the sign of the rotation (a spiral that sweeps the
wrong way is a bug nobody reports, they just say it "feels off"), and the rejections,
because a detector that fires on walking is worse than one that never fires.
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "nodes", "staff_pi"))

from gestures import ARMED, ARMING, ASLEEP, CastDetector, yaw_rate_cw  # noqa: E402

UP = (0.0, 0.0, 9.81)          # sensor flat and level: gravity vector points up (+z)
HZ = 50.0


class Rig:
    """Feeds a detector synthetic samples on a synthetic clock."""

    def __init__(self, **kw):
        self.det = CastDetector(**kw)
        self.t = 100.0
        self.casts = []

    def feed(self, seconds, gyro=(0.0, 0.0, 0.0), gravity=UP, accel=0.0, heading=90.0):
        n = max(1, int(seconds * HZ))
        for _ in range(n):
            self.t += 1.0 / HZ
            mag = math.sqrt(sum(v * v for v in gyro))
            cast = self.det.feed(self.t, gyro, gravity, accel, mag, heading)
            if cast:
                self.casts.append(cast)
        return self

    def arm(self):
        self.feed(2.2)
        assert self.det.state == ARMED, "should have armed after 2.2 s of stillness"
        return self

    def sweep(self, dps, seconds=0.6, gravity=UP):
        """dps positive = clockwise seen from above. Gravity is +z, the gyro is
        right-handed about it, so clockwise is -z."""
        return self.feed(seconds, gyro=(0.0, 0.0, -dps), gravity=gravity).quiet()

    def quiet(self):
        return self.feed(0.4)

    @property
    def last(self):
        return self.det.attempts[0]


# ---- the sign, which is the thing worth being sure about ----------------------

def test_clockwise_is_positive():
    """Right-hand rule about an up-pointing gravity vector makes counterclockwise
    positive, so `yaw_rate_cw` negates it. Get this backwards and every spiral in
    camp turns the wrong way."""
    assert yaw_rate_cw((0.0, 0.0, -100.0), UP) == pytest.approx(100.0)   # cw
    assert yaw_rate_cw((0.0, 0.0, 100.0), UP) == pytest.approx(-100.0)   # ccw


def test_yaw_is_the_same_however_the_staff_is_held():
    """The whole reason we project onto gravity: a staff held on its side turning the
    same way in the world must read the same. Here the sensor is rolled 90 degrees, so
    world-vertical is the sensor's -y."""
    tilted_up = (0.0, -9.81, 0.0)
    assert yaw_rate_cw((0.0, 120.0, 0.0), tilted_up) == pytest.approx(120.0)


def test_freefall_says_nothing_rather_than_guessing():
    assert yaw_rate_cw((0.0, 0.0, -100.0), (0.0, 0.0, 0.0)) == 0.0


# ---- arming -------------------------------------------------------------------

def test_arming_needs_unbroken_stillness():
    r = Rig()
    r.feed(1.5)
    assert r.det.state == ARMING
    r.feed(0.1, gyro=(0.0, 0.0, 40.0))       # a nudge part way through
    r.feed(1.5)
    # the clock restarted, so at 1.5 s it is still arming, not armed
    assert r.det.state == ARMING
    r.feed(0.8)
    assert r.det.state == ARMED


def test_a_sweep_without_arming_does_nothing():
    """This is the point of the two-part gesture: walking across camp swinging a staff
    must not cast."""
    r = Rig()
    r.feed(0.5).sweep(200)
    assert r.casts == []
    assert r.det.attempts == []              # never even captured


def test_armed_expires_if_unused():
    r = Rig(arm_timeout=3.0).arm()
    r.feed(3.2)
    assert r.det.state != ARMED


def test_a_staff_left_alone_goes_to_sleep():
    """A prop on the ground must not sit armed all night: it would hold the fast sample
    rate open and be one knock away from casting."""
    r = Rig(sleep_after=5.0).arm()
    r.feed(6.0)
    assert r.det.state == ASLEEP
    r.sweep(300)
    assert r.casts == []                     # asleep means asleep


def test_moving_it_wakes_it_up():
    r = Rig(sleep_after=5.0)
    r.feed(6.0)
    assert r.det.state == ASLEEP
    r.feed(0.2, gyro=(0.0, 0.0, 60.0))       # picked up
    assert r.det.state != ASLEEP
    r.arm().sweep(250)
    assert len(r.casts) == 1


# ---- the sweep ----------------------------------------------------------------

def test_clockwise_sweep_casts_spin_dir_plus_one():
    r = Rig().arm().sweep(200, seconds=0.6)
    assert len(r.casts) == 1
    cast = r.casts[0]
    assert cast["gesture"] == "spin"         # SCHEMA's directional rotation
    assert cast["spin_dir"] == 1
    assert cast["sweep_deg"] == pytest.approx(120, abs=10)
    assert cast["heading"] == 90.0           # where the sweep began


def test_counterclockwise_sweep_casts_spin_dir_minus_one():
    r = Rig().arm().sweep(-200, seconds=0.6)
    assert len(r.casts) == 1
    assert r.casts[0]["spin_dir"] == -1


def test_faster_sweeps_hit_harder():
    gentle = Rig().arm().sweep(80, seconds=0.8).casts[0]["intensity"]
    hard = Rig().arm().sweep(400, seconds=0.8).casts[0]["intensity"]
    assert 0.3 <= gentle < hard <= 1.0


def test_each_cast_needs_its_own_stillness():
    r = Rig().arm().sweep(200)
    r.sweep(200)                             # straight into a second one
    assert len(r.casts) == 1
    r.arm().sweep(200)
    assert len(r.casts) == 2


# ---- rejections, with a reason ------------------------------------------------

def test_a_small_turn_is_not_a_sweep():
    r = Rig().arm().feed(0.2, gyro=(0.0, 0.0, -90.0)).quiet()
    assert r.casts == []
    assert "not enough turn" in r.last.reason


def test_a_sweep_that_comes_back_is_not_a_sweep():
    """Out 180 degrees and most of the way back. It has the path length and it has a
    net direction - it is just not one committed turn."""
    r = Rig().arm()
    r.feed(0.6, gyro=(0.0, 0.0, -300.0))
    r.feed(0.45, gyro=(0.0, 0.0, 300.0))
    r.quiet()
    assert r.casts == []
    assert "changed direction" in r.last.reason


def test_an_even_shake_is_rejected_too():
    """A symmetric shake nets nothing, and the size check catches it first. Either
    reason is correct; what matters is that waving the staff about never casts."""
    r = Rig().arm()
    for _ in range(4):
        r.feed(0.12, gyro=(0.0, 0.0, -220.0))
        r.feed(0.12, gyro=(0.0, 0.0, 220.0))
    r.quiet()
    assert r.casts == []
    assert not r.last.ok


def test_a_swing_is_not_a_sweep():
    """Mostly rotation about a horizontal axis - the staff arcing overhead rather than
    turning flat. It has the degrees, it just is not the same gesture."""
    r = Rig().arm().feed(0.6, gyro=(200.0, 0.0, -100.0)).quiet()
    assert r.casts == []
    assert "not a flat turn" in r.last.reason


def test_every_attempt_is_recorded_for_the_page():
    r = Rig().arm().sweep(200)
    a = r.last
    assert a.ok and a.reason == "cw"
    assert a.metrics["samples"] > 5 and a.trace
    assert r.det.snapshot()["casts"] == 1


def test_a_stalled_bus_does_not_become_a_rotation():
    """If the reader hangs for a second, the last known rate must not be integrated
    across the gap - that would invent hundreds of degrees of turn out of a dropout."""
    from gestures import integrate

    yaw, _, _, _ = integrate([(0.0, 300.0, 300.0), (2.0, 300.0, 300.0)])
    assert yaw == 0.0


# ---- the node plumbing, still without hardware --------------------------------

class FakeIMU:
    """Just enough BNO055 for wire_casting: the fields it reads and the hook it sets."""

    def __init__(self, mag_calib=3):
        self.accel_mag = 0.0
        self.gyro_mag = 0.0
        self.gyro_vec = (0.0, 0.0, 0.0)     # filtered, as the driver hands them over
        self.gravity = UP
        self.calib = (3, 3, 3, mag_calib)
        self.sample_hook = None
        self.hz_requests = []

    def set_hz(self, hz):
        self.hz_requests.append(hz)


def _node_args(**over):
    """The real parser's defaults, so these tests cannot drift from the program."""
    import staff_node

    ns = staff_node.build_parser().parse_args(["--cast", "--prop-id", "staff_test"])
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


class FakeGate:
    active = False
    idle = False
    transitions = 0

    def fix(self):
        return None                      # no GPS on a desk; the cast must still publish


def _run_gesture(imu, args, det, dps=250.0, heading=137.0):
    """Drive the IMU hook the way the poll loop would."""
    import staff_node

    last = {}
    staff_node.wire_casting(None, args, imu, FakeGate(), det, last)
    def sample(t, gyro, accel):
        """What BNO055.poll() does before it calls the hook."""
        imu.gyro_vec = gyro
        imu.accel_mag = accel
        imu.gyro_mag = math.sqrt(sum(v * v for v in gyro))
        imu.sample_hook(t, {"gyro": gyro, "gravity": UP, "heading": heading})

    t = 500.0
    for _ in range(int(2.2 * HZ)):       # stillness to arm
        t += 1.0 / HZ
        sample(t, (0.0, 0.0, 0.0), 0.0)
    for _ in range(int(0.6 * HZ)):       # the sweep
        t += 1.0 / HZ
        sample(t, (0.0, 0.0, -dps), 0.4)
    for _ in range(int(0.4 * HZ)):       # let go
        t += 1.0 / HZ
        sample(t, (0.0, 0.0, 0.0), 0.0)
    return last


def test_a_sweep_publishes_a_schema_shaped_cast():
    import staff_node

    args = _node_args()
    det = staff_node.open_detector(args)
    imu = FakeIMU()
    payload = _run_gesture(imu, args, det)["payload"]

    # exactly the keys docs/SCHEMA.md lists for prop/<prop_id>
    assert set(payload) == {"prop", "prop_id", "gesture", "intensity", "color",
                            "heading", "spin_dir", "lat", "lon", "acc_m", "via"}
    assert payload["prop"] == "staff" and payload["gesture"] == "spin"
    assert payload["spin_dir"] == 1                     # clockwise
    assert payload["heading"] == 137.0
    assert payload["lat"] is None                       # no fix, and that is allowed


def test_heading_is_withheld_while_the_magnetometer_is_uncalibrated():
    """A confidently wrong heading points the spiral the wrong way; null is a value the
    contract already allows and every consumer already handles."""
    import staff_node

    args = _node_args()
    det = staff_node.open_detector(args)
    payload = _run_gesture(FakeIMU(mag_calib=1), args, det)["payload"]
    assert payload["heading"] is None
    assert payload["spin_dir"] == 1                     # direction is not the compass


def test_the_sample_rate_only_goes_up_while_it_matters():
    """Fast sampling all the time would starve the GPS that shares this bus."""
    import staff_node

    args = _node_args()
    det = staff_node.open_detector(args)
    imu = FakeIMU()
    _run_gesture(imu, args, det)
    assert 50.0 in imu.hz_requests, "never wound up for the gesture"
    assert imu.hz_requests[0] == 10.0 and imu.hz_requests[-1] == 10.0, "never wound back down"


def test_the_payload_fits_the_budget():
    """SCHEMA.md budgets ~320 bytes a message."""
    import json as _json

    import staff_node

    args = _node_args()
    det = staff_node.open_detector(args)
    payload = _run_gesture(FakeIMU(), args, det)["payload"]
    assert len(_json.dumps(payload, separators=(",", ":"))) < 320


# ---- the register window, without a bus ---------------------------------------

def _frame(gyro=(0, 0, 0), euler=(90.0, 0, 0), quat=(1.0, 0, 0, 0),
           accel=(0, 0, 0), gravity=(0.0, 0.0, 9.81)) -> bytes:
    """Build the 32 bytes the BNO055 hands back for 0x14-0x33."""
    import struct

    raw = bytearray(32)
    struct.pack_into("<hhh", raw, 0, *[round(v * 16) for v in gyro])
    struct.pack_into("<hhh", raw, 6, *[round(v * 16) for v in euler])
    struct.pack_into("<hhhh", raw, 12, *[round(v * 16384) for v in quat])
    struct.pack_into("<hhh", raw, 20, *[round(v * 100) for v in accel])
    struct.pack_into("<hhh", raw, 26, *[round(v * 100) for v in gravity])
    return bytes(raw)


def test_every_reading_comes_out_of_its_own_slot():
    """A wrong offset reads as plausible numbers from the wrong sensor, which is a bug
    that survives a long time. Every field gets a value nothing else has."""
    from bno055 import decode_frame

    f = decode_frame(_frame(gyro=(11.0, -22.0, 33.0), euler=(123.0, -45.0, 67.0),
                            quat=(0.5, 0.5, -0.5, 0.5), accel=(1.5, -2.5, 3.5),
                            gravity=(0.0, 3.0, 9.34)))
    assert f["gyro"] == pytest.approx((11.0, -22.0, 33.0), abs=0.1)
    assert f["heading"] == pytest.approx(123.0, abs=0.1)
    assert (f["roll"], f["pitch"]) == pytest.approx((-45.0, 67.0), abs=0.1)
    assert f["quat"] == pytest.approx((0.5, 0.5, -0.5, 0.5), abs=1e-4)
    assert f["accel"] == pytest.approx((1.5, -2.5, 3.5), abs=0.01)
    assert f["gravity"] == pytest.approx((0.0, 3.0, 9.34), abs=0.01)


def test_gravity_that_is_not_gravity_means_the_bus_lied():
    """The strongest corruption check available: gravity is 9.81, always."""
    from bno055 import decode_frame

    assert decode_frame(_frame(gravity=(0.0, 0.0, 9.81))) is not None
    assert decode_frame(_frame(gravity=(0.0, 0.0, 1.28))) is None     # the 128-count artifact
    assert decode_frame(_frame(gravity=(9.8, 9.8, 9.8))) is None      # too long


def test_an_impossible_heading_is_rejected():
    from bno055 import decode_frame

    assert decode_frame(_frame(euler=(367.9, 0, 0))) is None


def test_a_settling_quaternion_does_not_throw_the_frame_away():
    """Fusion takes a moment to converge. Its gyro and gravity are fine meanwhile, and
    dropping those would stall the motion gate at startup for no reason."""
    from bno055 import decode_frame

    f = decode_frame(_frame(quat=(0.0, 0.0, 0.0, 0.0)))
    assert f is not None and f["quat"] == (0.0, 0.0, 0.0, 0.0)


# ---- input filters -------------------------------------------------------------

def test_a_median_removes_a_spike_and_a_mean_only_spreads_it():
    """The reason median is the default. Both have the same window, the same lag and
    the same cost; only one of them actually gets rid of the outlier."""
    from filters import Filter

    spike = [1.0, 1.0, 9.0, 1.0, 1.0]
    median = Filter(3, "median")
    assert [median.push(v) for v in spike] == [1.0] * 5

    mean = Filter(3, "mean")
    out = [round(mean.push(v), 2) for v in spike]
    assert max(out) > 3.0, "a mean cannot remove an outlier, it averages it in"


def test_a_window_of_one_is_a_straight_wire():
    from filters import Filter

    for mode in ("off", "mean", "median"):
        f = Filter(1, mode)
        assert [f.push(v) for v in (3.0, -1.0, 7.5)] == [3.0, -1.0, 7.5]


def test_off_passes_everything_through_whatever_the_window():
    from filters import Filter

    f = Filter(9, "off")
    assert [f.push(v) for v in (1.0, 50.0, 1.0)] == [1.0, 50.0, 1.0]


def test_it_warms_up_on_real_readings_not_on_zeros():
    """The first sample after a restart must be the first sample, not a fade in from
    nowhere - the motion gate would read that ramp as movement."""
    from filters import Filter

    f = Filter(5, "median")
    assert f.push(4.0) == 4.0
    assert f.push(4.0) == 4.0


def test_widening_the_window_does_not_look_like_a_spike():
    """Changing a setting must not itself disturb the reading, or every adjustment
    while tuning would trip the thing you are tuning."""
    from filters import Filter

    f = Filter(3, "median")
    for _ in range(5):
        f.push(2.0)
    f.window = 9
    assert f.push(2.0) == 2.0
    f.window = 2
    assert f.push(2.0) == 2.0


def test_the_window_is_clamped_to_something_sane():
    from filters import MAX_WINDOW, Filter

    f = Filter(3)
    f.window = 0
    assert f.window == 1
    f.window = 10 ** 6
    assert f.window == MAX_WINDOW
    f.window = 4.7                      # arrives from the page as a float
    assert f.window == 5


def test_a_nonsense_mode_is_ignored_rather_than_obeyed():
    from filters import Filter

    f = Filter(3, "median")
    f.mode = "banana"
    assert f.mode == "median"


def test_a_vector_filter_keeps_the_axes_apart():
    from filters import VectorFilter

    vf = VectorFilter(3, 3, "median")
    for _ in range(3):
        vf.push((1.0, 2.0, 3.0))
    assert vf.push((1.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)
    assert vf.push((90.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)      # spike on x only, dropped


def _hold_through_a_spike(window):
    """Hold still for 3 s with one bad frame 1.5 s in - half a second before the hold
    would have completed - and see whether it still armed."""
    import math as _math

    from filters import VectorFilter

    det = CastDetector()
    vf = VectorFilter(3, window, "median")
    t = 100.0
    for i in range(int(3.0 * HZ)):
        t += 1.0 / HZ
        raw = (0.0, 0.0, -140.0) if i == int(1.5 * HZ) else (0.0, 0.0, 0.0)
        gyro = vf.push(raw)
        det.feed(t, gyro, UP, 0.0, _math.sqrt(sum(v * v for v in gyro)), 90.0)
    return det.state


def test_one_bad_frame_no_longer_throws_away_a_nearly_finished_hold():
    """The whole point of the filter. Arming is a comparison against a single
    instantaneous reading, so before this a lone spike could restart a hold you had
    almost completed - the most annoying failure the page ever showed."""
    assert _hold_through_a_spike(1) == ARMING, "unfiltered, the spike should reset it"
    assert _hold_through_a_spike(3) == ARMED, "filtered, it should survive"


# ---- the arm cues: charge while holding still, ready when armed, then the cast ----

class RecordingSound:
    """Stands in for sound.SoundPlayer: records what the IMU thread asked for."""

    available = True

    def __init__(self):
        self.log = []

    def later(self, fn, *args):          # the real one queues to a worker; order is what matters
        fn(*args)

    def play(self, rel):
        self.log.append(("play", rel))
        return True

    def stop_if(self, rel):
        self.log.append(("stop", rel))
        return True

    def on_cast(self, payload):
        self.log.append(("cast", payload["gesture"]))


def _cue_rig(monkeypatch):
    import staff_node

    snd = RecordingSound()
    monkeypatch.setattr(staff_node, "SOUND", snd)
    monkeypatch.setattr(staff_node, "KNOB", None)
    args = _node_args()
    det = staff_node.open_detector(args)
    imu = FakeIMU()
    staff_node.wire_casting(None, args, imu, FakeGate(), det, {})
    clock = {"t": 900.0}

    def run(seconds, gyro=(0.0, 0.0, 0.0), accel=0.0):
        for _ in range(max(1, int(seconds * HZ))):
            clock["t"] += 1.0 / HZ
            imu.gyro_vec, imu.accel_mag = gyro, accel
            imu.gyro_mag = math.sqrt(sum(v * v for v in gyro))
            imu.sample_hook(clock["t"], {"gyro": gyro, "gravity": UP, "heading": 90.0})

    return args, snd, run


def test_charge_then_ready_then_cast(monkeypatch):
    args, snd, run = _cue_rig(monkeypatch)
    run(0.3, accel=1.0)                  # picked up, moving
    run(2.3)                             # held still past arm-seconds
    assert snd.log == [("play", args.charge_sound), ("play", args.ready_sound)]
    run(0.6, gyro=(0.0, 0.0, -250.0), accel=0.4)    # the sweep
    run(0.4)
    assert snd.log[-1] == ("cast", "spin")
    assert ("stop", args.charge_sound) not in snd.log


def test_moving_too_soon_stops_the_charge_and_it_starts_over(monkeypatch):
    args, snd, run = _cue_rig(monkeypatch)
    run(0.3, accel=1.0)
    run(1.0)                             # half a hold
    run(0.1, accel=1.0)                  # moved: interrupted
    assert snd.log == [("play", args.charge_sound), ("stop", args.charge_sound)]
    run(0.5, accel=1.0)
    run(2.3)                             # holds still again: charges again, then arms
    assert snd.log[2:] == [("play", args.charge_sound), ("play", args.ready_sound)]


def test_jitter_at_the_threshold_never_starts_the_charge(monkeypatch):
    args, snd, run = _cue_rig(monkeypatch)
    run(0.3, accel=1.0)
    for _ in range(40):                  # steady 0.1 s, a twitch, steady 0.1 s, ...
        run(0.1)
        run(0.02, accel=1.0)
    assert snd.log == []
