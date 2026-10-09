"""Casting: hold the staff still, then sweep it.

The gesture Ben asked for (2026-09-23) is deliberately two-part:

    hold as still as you can for ~2 s   ->  ARMED
    then sweep, clockwise or counter    ->  CAST

The stillness is not ceremony, it is what makes the rest possible. A staff being
carried across camp is *constantly* producing accelerations and rotations that look
like gestures; without a deliberate quiet moment in front of every cast, the only
way to tell "I meant that" from "I walked" is a classifier good enough to read
intent, which we do not have and do not need. Two seconds of stillness is a cheap,
unambiguous, physical "listen to me now" - and it reads as ritual rather than as a
user interface, which is the right feel for a prop people hand to strangers at 2am.

A staff that has not been touched for `sleep_after` goes to sleep and stops arming
until something moves it. A prop lying on the ground must not be one knock away from
casting, and the fast sampling an arm turns on has no business running all night.

It also makes the debug loop honest: an attempt that never armed and an attempt that
armed and was rejected are different failures with different fixes, and this module
records which one happened every time.

WHAT A SWEEP IS, IN PHYSICS
---------------------------
Rotation about the **world vertical** - the staff turning the way a radar dish turns,
whatever angle it is being held at. That last part is why we do not just read the gyro
or the compass:

  * raw gyro axes are the chip's, so what "turning" reads as depends entirely on how
    the staff is held, and people do not hold a staff the same way twice
  * fused `heading` is yaw only near level, and wraps at 360

So we take the BNO055's gravity vector - which it computes for us - and project the
gyro onto it. The component of rotation about the gravity axis *is* the turn rate
about the world vertical, at any tilt, with no wrap. One dot product.

    cw_dps = -dot(gyro, gravity) / |gravity|

The minus sign: the BNO055's gravity vector points **up** in the sensor frame (it is
the gravity part of the accelerometer reading, and an accelerometer at rest reads
+9.8 along whichever axis points up). The gyro is right-handed about the same axes,
so a positive rotation about "up" is counterclockwise seen from above. Negate it and
positive means clockwise, which is what `spin_dir: 1` means in SCHEMA.md.

WHAT IT PUBLISHES
-----------------
`gesture: "spin"` with `spin_dir` +1 cw / -1 ccw - the existing contract slot for a
directional rotational gesture, already routed to the spiral cast. Nothing new is
invented here; "sweep" is the name of the *motion*, `spin` is the name of the cast.
If Ben wants `sweep` as a gesture of its own that is a contract decision, not a code
change (see the node README).

TUNING
------
Every threshold below is a default, not a measurement. Find the real ones on the
sensor page - it plots the yaw rate live and logs why each attempt was rejected -
then put what you settle on in the systemd unit.
"""
from __future__ import annotations

import math
import time
from typing import List, Optional, Sequence, Tuple

IDLE = "idle"
ASLEEP = "asleep"
ARMING = "arming"
ARMED = "armed"
CAPTURE = "capture"
COOLDOWN = "cooldown"

# A gap this long between samples means the reader stalled (an I2C retry, a dropped
# frame), not that the staff rotated at the last known rate for all that time.
MAX_SAMPLE_GAP_S = 0.5


def yaw_rate_cw(gyro: Sequence[float], gravity: Sequence[float]) -> float:
    """Turn rate about the world vertical in deg/s, positive = clockwise from above.

    Works at any tilt: see the module docstring. Returns 0 if gravity is unreadable,
    which in practice means freefall or a corrupt frame - either way we know nothing
    about which way is up, so we say nothing about which way it turned.
    """
    gx, gy, gz = gyro
    ux, uy, uz = gravity
    mag = math.sqrt(ux * ux + uy * uy + uz * uz)
    if mag < 1.0:
        return 0.0
    return -(gx * ux + gy * uy + gz * uz) / mag


def integrate(samples: Sequence[Tuple[float, float, float]]) -> Tuple[float, float, float, float]:
    """(signed degrees, absolute degrees, absolute total rotation, peak |rate|).

    Trapezoid over real timestamps rather than an assumed rate, because the sample
    rate changes on purpose (see `--cast-hz`) and the bus occasionally stalls.
    """
    yaw = abs_yaw = abs_gyro = peak = 0.0
    for (t0, y0, g0), (t1, y1, g1) in zip(samples, samples[1:]):
        dt = t1 - t0
        peak = max(peak, abs(y0), abs(y1))
        if dt <= 0.0 or dt > MAX_SAMPLE_GAP_S:
            continue
        yaw += 0.5 * (y0 + y1) * dt
        abs_yaw += 0.5 * (abs(y0) + abs(y1)) * dt
        abs_gyro += 0.5 * (g0 + g1) * dt
    return yaw, abs_yaw, abs_gyro, peak


class Attempt:
    """One capture window and what we decided about it. Kept whether it cast or not -
    the rejects are the useful ones while tuning."""

    def __init__(self, at: float, ok: bool, reason: str, metrics: dict,
                 trace: List[Tuple[float, float]]):
        self.at, self.ok, self.reason = at, ok, reason
        self.metrics, self.trace = metrics, trace

    def as_dict(self, now: float) -> dict:
        return {"ago": round(now - self.at, 1), "ok": self.ok, "reason": self.reason,
                "trace": [[round(t, 3), round(v, 1)] for t, v in self.trace],
                **self.metrics}


class CastDetector:
    """Feed it IMU samples; it hands back a cast when one happens.

    Stateless between casts on purpose: every cast needs its own two seconds of
    stillness, so there is no way to fire two by accident on one arm.
    """

    def __init__(self, arm_seconds: float = 2.0, arm_accel: float = 0.30,
                 arm_gyro: float = 10.0, arm_timeout: float = 8.0,
                 trigger_dps: float = 60.0, release_dps: float = 25.0,
                 gap_s: float = 0.25, max_s: float = 1.5, min_s: float = 0.15,
                 min_deg: float = 45.0, full_deg: float = 360.0,
                 dir_purity: float = 0.65, axis_purity: float = 0.5,
                 full_dps: float = 400.0, cooldown_s: float = 0.8,
                 sleep_after: float = 25.0, keep_attempts: int = 8):
        self.arm_seconds, self.arm_accel, self.arm_gyro = arm_seconds, arm_accel, arm_gyro
        self.arm_timeout = arm_timeout
        self.trigger_dps, self.release_dps = trigger_dps, release_dps
        self.gap_s, self.max_s, self.min_s = gap_s, max_s, min_s
        self.min_deg, self.full_deg = min_deg, full_deg
        self.dir_purity, self.axis_purity = dir_purity, axis_purity
        self.full_dps, self.cooldown_s = full_dps, cooldown_s
        self.sleep_after, self.keep_attempts = sleep_after, keep_attempts

        self.state = IDLE
        self.since = 0.0             # when the current state started
        self.still_since: Optional[float] = None
        self.yaw = 0.0               # latest turn rate, for the live meter
        self.samples: List[Tuple[float, float, float]] = []
        self.attempts: List[Attempt] = []
        self.casts = 0
        self.rejects = 0
        self.quiet_since: Optional[float] = None
        self.start_heading: Optional[float] = None
        self._last_unsteady: Optional[float] = None

    # ---- live readouts -------------------------------------------------------
    @property
    def arm_progress(self) -> float:
        """0..1 along the stillness the arm needs. The thing to watch on the page."""
        if self.state == ARMING and self.still_since is not None:
            return min(1.0, (self._now - self.still_since) / max(0.05, self.arm_seconds))
        return 1.0 if self.state in (ARMED, CAPTURE) else 0.0

    @property
    def asleep(self) -> bool:
        return self.state == ASLEEP

    def steady(self, accel_mag: float, gyro_mag: float) -> bool:
        return accel_mag <= self.arm_accel and gyro_mag <= self.arm_gyro

    # ---- the machine ---------------------------------------------------------
    def feed(self, t: float, gyro: Sequence[float], gravity: Sequence[float],
             accel_mag: float, gyro_mag: float,
             heading: Optional[float] = None) -> Optional[dict]:
        """One IMU sample in, at most one cast out."""
        self._now = t
        self.yaw = yaw_rate_cw(gyro, gravity)
        steady = self.steady(accel_mag, gyro_mag)
        if not steady or self._last_unsteady is None:
            self._last_unsteady = t

        # A staff put down is not a staff waiting to cast. Without this it sits armed
        # for ever - which burns the fast sample rate the arm turns on, keeps the bus
        # busy for nothing, and leaves a prop on the ground one knock away from casting.
        # Anything that moves it wakes it, so picking it up is the whole ritual.
        if self.state == ASLEEP:
            if not steady:
                self._to(IDLE, t)
            return None
        if self.state in (IDLE, ARMING, ARMED) and t - self._last_unsteady >= self.sleep_after:
            self.still_since = None
            self._to(ASLEEP, t)
            return None

        if self.state == COOLDOWN:
            if t - self.since >= self.cooldown_s:
                self._to(IDLE, t)
            return None

        if self.state == IDLE:
            if steady:
                self.still_since = t
                self._to(ARMING, t)
            return None

        if self.state == ARMING:
            if not steady:
                self.still_since = None
                self._to(IDLE, t)          # the clock restarts; stillness must be unbroken
            elif t - (self.still_since or t) >= self.arm_seconds:
                self._to(ARMED, t)
            return None

        if self.state == ARMED:
            # Any rotation big enough to be meant starts a capture - including one that
            # turns out to be a slam or a swing. Capturing and then rejecting it with a
            # reason beats silently ignoring it: on the page you can see what you did.
            if gyro_mag >= self.trigger_dps:
                self.samples = [(t, self.yaw, gyro_mag)]
                self.quiet_since = None
                self.start_heading = heading
                self._to(CAPTURE, t)
            elif t - self.since >= self.arm_timeout:
                self._to(IDLE, t)          # armed and never used: go back to sleep
            return None

        # CAPTURE
        self.samples.append((t, self.yaw, gyro_mag))
        if abs(self.yaw) < self.release_dps:
            if self.quiet_since is None:
                self.quiet_since = t
        else:
            self.quiet_since = None

        ended = (self.quiet_since is not None and t - self.quiet_since >= self.gap_s) \
            or (t - self.since >= self.max_s)
        if not ended:
            return None
        return self._classify(t)

    def _to(self, state: str, t: float) -> None:
        self.state, self.since = state, t

    # ---- classification ------------------------------------------------------
    def _classify(self, t: float) -> Optional[dict]:
        samples = self.samples
        self.samples = []
        dur = samples[-1][0] - samples[0][0] if len(samples) > 1 else 0.0
        yaw_deg, abs_yaw, abs_gyro, peak = integrate(samples)
        dirp = abs(yaw_deg) / abs_yaw if abs_yaw > 1e-6 else 0.0
        axisp = abs_yaw / abs_gyro if abs_gyro > 1e-6 else 0.0

        metrics = {"deg": round(yaw_deg, 1), "abs_deg": round(abs_yaw, 1),
                   "peak_dps": round(peak, 1), "dur": round(dur, 2),
                   "dir_purity": round(dirp, 2), "axis_purity": round(axisp, 2),
                   "samples": len(samples), "hz": round((len(samples) - 1) / dur, 1) if dur > 0 else 0.0}

        reason = self._reject_reason(samples, dur, yaw_deg, abs_yaw, dirp, axisp)
        if reason:
            self.rejects += 1
            self._record(t, False, reason, metrics, samples)
            self._to(COOLDOWN, t)
            return None

        cast = {
            "gesture": "spin",                       # SCHEMA's directional rotation
            "spin_dir": 1 if yaw_deg > 0 else -1,    # +1 clockwise seen from above
            "intensity": self._intensity(peak, abs(yaw_deg)),
            "heading": self.start_heading,           # where the sweep began
            "sweep_deg": round(yaw_deg, 1),
        }
        self.casts += 1
        self._record(t, True, "cw" if cast["spin_dir"] > 0 else "ccw",
                     dict(metrics, intensity=cast["intensity"], spin_dir=cast["spin_dir"]),
                     samples)
        self._to(COOLDOWN, t)
        return cast

    def _reject_reason(self, samples, dur, yaw_deg, abs_yaw, dirp, axisp) -> Optional[str]:
        """First failure wins, so the order decides what the page tells you.

        It is deliberately *not* the order the numbers come in. A shake back and forth
        nets nearly zero degrees, so testing the net turn first would report it as "not
        enough turn" - true, and useless, because the person did move the staff a long
        way. Measuring the total path first separates "you barely moved it" from "you
        moved it and changed your mind", which are different mistakes with different
        fixes.
        """
        if len(samples) < 3 or dur < self.min_s:
            return "too short - %d samples over %.2fs" % (len(samples), dur)
        if abs_yaw < self.min_deg:
            return "not enough turn - %.0f deg, needs %.0f" % (abs_yaw, self.min_deg)
        if dirp < self.dir_purity:
            return "changed direction - %.0f%% one way, needs %.0f%% (a shake, not a sweep?)" % (
                100 * dirp, 100 * self.dir_purity)
        if abs(yaw_deg) < self.min_deg:
            return "did not get far enough - %.0f deg net, needs %.0f" % (
                abs(yaw_deg), self.min_deg)
        if axisp < self.axis_purity:
            return "not a flat turn - %.0f%% about vertical, needs %.0f%% (a swing, not a sweep?)" % (
                100 * axisp, 100 * self.axis_purity)
        return None

    def _intensity(self, peak: float, deg: float) -> float:
        """How hard was it. Speed and size both count, speed more: a fast short flick
        should read stronger than a slow full turn."""
        span = max(1.0, self.full_dps - self.trigger_dps)
        speed_k = min(1.0, max(0.0, (peak - self.trigger_dps) / span))
        size_k = min(1.0, max(0.0, (deg - self.min_deg) / max(1.0, self.full_deg - self.min_deg)))
        return round(min(1.0, 0.35 + 0.65 * (0.6 * speed_k + 0.4 * size_k)), 2)

    def _record(self, t, ok, reason, metrics, samples) -> None:
        t0 = samples[0][0]
        trace = [(s[0] - t0, s[1]) for s in samples]
        if len(trace) > 120:                       # keep the JSON small on a Zero
            step = len(trace) / 120.0
            trace = [trace[int(i * step)] for i in range(120)]
        self.attempts.insert(0, Attempt(t, ok, reason, metrics, trace))
        del self.attempts[self.keep_attempts:]

    # ---- for the page --------------------------------------------------------
    def snapshot(self, now: Optional[float] = None) -> dict:
        now = time.monotonic() if now is None else now
        return {
            "state": self.state,
            "yaw": round(self.yaw, 1),
            "arm_progress": round(self.arm_progress, 3),
            "held_for": round(now - self.still_since, 2) if self.still_since else 0.0,
            "in_state": round(now - self.since, 2),
            "casts": self.casts, "rejects": self.rejects,
            "attempts": [a.as_dict(now) for a in self.attempts],
            "asleep": self.state == ASLEEP,
            "cfg": {"arm_seconds": self.arm_seconds, "arm_accel": self.arm_accel,
                    "arm_gyro": self.arm_gyro, "trigger_dps": self.trigger_dps,
                    "min_deg": self.min_deg, "dir_purity": self.dir_purity,
                    "axis_purity": self.axis_purity, "arm_timeout": self.arm_timeout,
                    "sleep_after": self.sleep_after},
        }

    _now = 0.0
