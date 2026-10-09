"""Bosch BNO055 over I2C - enough of it to answer "is this thing moving?".

The BNO055 runs its own sensor fusion, so we do not integrate anything here. Two
readings do the work:

  linear acceleration - acceleration with gravity already removed, so it sits near
                        zero when still *whatever* the staff is leaning against
  gyroscope           - rotation rate, which catches a slow turn on the spot that
                        barely registers as acceleration

Either one over threshold means moving. That is all the motion gate needs.

`euler_heading`, the **quaternion** and the **gravity vector** come along for free:
0x14-0x33 is exactly 32 bytes, which is the largest block read SMBus allows, so one
transaction yields gyro + heading + attitude quaternion + linear acceleration + gravity.

Gravity is what lets `gestures.py` work out which way the staff turned no matter how it
is being held, and its magnitude must be 9.8, which makes it a *better* corruption check
than anything else we have.

The quaternion is the full attitude - which way the staff is pointing and how it is
rolled, with no gimbal lock and no wrap at 360, unlike the Euler angles beside it. The
sensor page uses it to draw the staff in space. It is deliberately *not* part of the
validity check: a frame taken while fusion is still settling has a meaningless
quaternion but perfectly good gyro and gravity, and rejecting those would stall the
motion gate at startup for no reason. Consumers check its length themselves.

READ THIS BEFORE TRUSTING A NUMBER OUT OF THIS CHIP ON A PI
-----------------------------------------------------------
The BNO055 stretches the I2C clock and the Pi's BCM2835 controller mishandles
that. It does not fail loudly: it hands back *plausible-looking corrupted frames*.
On the prototype (2026-09-22, 400 kHz default) roughly a third of reads came back
with acceleration pinned at exactly 128 counts (1.28 m/s^2), gyro near 130 counts
and a heading of 367.9 degrees - which is not a heading any compass can produce.

Three things here defend against it, in order of how much they buy:

  1. ONE block read per sample (0x14-0x2D) instead of three. Fewer transactions is
     fewer chances to be corrupted, and every value in a sample is consistent.
  2. A validity check with physics behind it: heading must be 0-360. A frame that
     fails is dropped whole rather than half-believed. `bad_frames` counts them.
  3. Motion needs `samples` consecutive moving reads. Corruption is isolated, real
     movement is not, so 3 samples at 20 Hz (150 ms) rejects the former and is far
     shorter than any real gesture.

The proper fix is to slow the bus down, in /boot/firmware/config.txt, then reboot:

    dtparam=i2c_arm_baudrate=50000

Do that as well - the defences above keep the gate honest, they do not make the bus
good. Do NOT drop to the 10 kHz you will see recommended online: the PA1010D shares
this bus and needs a few hundred bytes a second, and 10 kHz tops out around 1 kB/s
for both devices together. That trades a broken IMU for a starved GPS.

    imu = BNO055()           # raises OSError if the bus or the chip is absent
    imu.start()
    imu.moving               # bool, updated in the background
    imu.still_for            # seconds since the last motion
    imu.accel_mag            # filtered; imu.accel_raw is the same sample unfiltered

Readings are passed through a rolling filter before anything compares them to a
threshold, because every decision here is made from one instantaneous number and a
single spike is enough to restart a hold or start a capture. See filters.py.
"""
from __future__ import annotations

import struct
import threading
import time
from typing import Optional, Tuple

from filters import MEDIAN, VectorFilter

I2C_ADDR = 0x28              # 0x29 with ADR high
I2C_BUS = 1
CHIP_ID = 0xA0

REG_CHIP_ID = 0x00
REG_PAGE_ID = 0x07
REG_GYRO = 0x14              # X/Y/Z, 16 LSB per deg/s
REG_EULER = 0x1A             # heading/roll/pitch, 16 LSB per degree
REG_QUAT = 0x20              # w/x/y/z, 16384 LSB per unit - fused absolute attitude
REG_LIN_ACCEL = 0x28         # X/Y/Z, 100 LSB per m/s^2 (gravity removed)
REG_GRAVITY = 0x2E           # X/Y/Z, 100 LSB per m/s^2 - which way is down, fused
REG_CALIB_STAT = 0x35
REG_UNIT_SEL = 0x3B
REG_OPR_MODE = 0x3D
REG_PWR_MODE = 0x3E
REG_SYS_TRIGGER = 0x3F

MODE_CONFIG = 0x00
MODE_NDOF = 0x0C             # full fusion, absolute heading off the magnetometer
POWER_NORMAL = 0x00

# Thresholds a still staff sits under and a carried one does not. Measured on the
# prototype with --imu-test; retune there rather than guessing.
MOVE_ACCEL_MS2 = 0.35        # m/s^2 of linear acceleration
# 15, not 8: the residual I2C corruption artifact is exactly 128 counts, which is
# 8.0 deg/s, so a threshold of 8 sits right on top of it and reads a desk as moving.
# Real motion is nowhere near this fine - walking rotates a carried staff 30-90 deg/s
# and a swing is in the hundreds - so there is nothing to lose by clearing the artifact.
MOVE_GYRO_DPS = 15.0         # deg/s of rotation

# Gravity is 9.81 m/s^2 and does not negotiate. A frame whose gravity vector is not
# that length was corrupted on the bus, whatever else it looks like.
G_MIN, G_MAX = 8.0, 11.5


def decode_frame(raw: bytes) -> Optional[dict]:
    """Turn the 32-byte register window at 0x14 into readings, or None if it is junk.

    Module level and free of any bus so the offsets can be tested directly - a wrong
    offset here reads as plausible numbers from the wrong sensor, which is the kind of
    bug that survives a long time.

      offset  0  gyro x/y/z          16 LSB per deg/s
              6  euler h/r/p         16 LSB per degree
             12  quaternion w/x/y/z  16384 LSB per unit
             20  linear accel x/y/z  100 LSB per m/s^2
             26  gravity x/y/z       100 LSB per m/s^2
    """
    gx, gy, gz = (v / 16.0 for v in struct.unpack_from("<hhh", raw, 0))
    heading, roll, pitch = (v / 16.0 for v in struct.unpack_from("<hhh", raw, 6))
    qw, qx, qy, qz = (v / 16384.0 for v in struct.unpack_from("<hhhh", raw, 12))
    ax, ay, az = (v / 100.0 for v in struct.unpack_from("<hhh", raw, 20))
    vx, vy, vz = (v / 100.0 for v in struct.unpack_from("<hhh", raw, 26))

    # Physics, not magic numbers: a fused heading is 0-360, a hand-held prop does not
    # see 16 g, and gravity is 9.81 whatever the staff is doing. Any of the three
    # failing means the bus lied to us, so the frame is dropped whole. The quaternion
    # is deliberately not part of this - see the module docstring.
    gmag = (vx * vx + vy * vy + vz * vz) ** 0.5
    if (not (0.0 <= heading <= 360.0)
            or max(abs(ax), abs(ay), abs(az)) > 156.0
            or not (G_MIN <= gmag <= G_MAX)):
        return None
    return {"gyro": (gx, gy, gz), "heading": heading, "roll": roll, "pitch": pitch,
            "quat": (qw, qx, qy, qz),
            "accel": (ax, ay, az), "gravity": (vx, vy, vz)}


class BNO055:
    def __init__(self, bus: int = I2C_BUS, addr: int = I2C_ADDR,
                 accel_thresh: float = MOVE_ACCEL_MS2, gyro_thresh: float = MOVE_GYRO_DPS,
                 samples: int = 3, accel_window: int = 3, gyro_window: int = 3,
                 filter_mode: str = MEDIAN):
        from smbus2 import SMBus

        self.bus_no, self.addr = bus, addr
        self.accel_thresh, self.gyro_thresh = accel_thresh, gyro_thresh
        self.samples = max(1, samples)
        self._bus = SMBus(bus)

        chip = self._bus.read_byte_data(addr, REG_CHIP_ID)
        if chip != CHIP_ID:
            raise OSError("not a BNO055 at 0x%02x (chip id 0x%02x, expected 0x%02x)"
                          % (addr, chip, CHIP_ID))
        self._configure()

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.moving = True            # assume moving until proven otherwise: never
        self._last_motion = time.monotonic()   # park the GPS on a bad first reading
        self.accel_mag = 0.0
        self.gyro_mag = 0.0
        self.heading: Optional[float] = None
        self.io_errors = 0
        self.reads = 0
        self.bad_frames = 0           # frames rejected as corrupt (see the module docstring)
        self._moving_run = 0          # consecutive moving samples
        # Filtered readings are what everything downstream compares against; the raw
        # magnitudes are kept beside them so the page can show what the filter removed.
        # See filters.py for why the default is a median and not a mean.
        self.accel_filter = VectorFilter(3, accel_window, filter_mode)
        self.gyro_filter = VectorFilter(3, gyro_window, filter_mode)
        self.accel_raw = 0.0
        self.gyro_raw = 0.0
        self.accel_vec = (0.0, 0.0, 0.0)
        self.gyro_vec = (0.0, 0.0, 0.0)
        self.gravity = (0.0, 0.0, 0.0)
        self.quat = (1.0, 0.0, 0.0, 0.0)   # w, x, y, z - identity until the chip speaks
        self.roll: Optional[float] = None
        self.pitch: Optional[float] = None
        self.calib = (0, 0, 0, 0)     # refreshed in the poll loop, not per reader:
                                      # the page asks 10x a second and this is an I2C round trip
        self.hz = 0.0                 # measured, not requested - the ARMv6 does not always keep up
        self._period = 0.1
        self._last_read = 0.0
        self._calib_countdown = 0
        self.sample_hook = None       # called with the frame; gestures.py hangs off this
        self.hook_errors = 0

    @property
    def filter_mode(self) -> str:
        return self.gyro_filter.mode

    @filter_mode.setter
    def filter_mode(self, value) -> None:
        self.accel_filter.mode = value
        self.gyro_filter.mode = value

    # ---- setup ---------------------------------------------------------------
    def _configure(self) -> None:
        """CONFIG -> normal power -> NDOF. Mode changes need a moment to settle;
        skipping the waits gives you a chip that reads zeros forever."""
        self._bus.write_byte_data(self.addr, REG_OPR_MODE, MODE_CONFIG)
        time.sleep(0.03)
        self._bus.write_byte_data(self.addr, REG_PAGE_ID, 0)
        self._bus.write_byte_data(self.addr, REG_PWR_MODE, POWER_NORMAL)
        time.sleep(0.02)
        self._bus.write_byte_data(self.addr, REG_SYS_TRIGGER, 0x00)
        self._bus.write_byte_data(self.addr, REG_UNIT_SEL, 0x00)   # m/s^2, dps, degrees
        time.sleep(0.02)
        self._bus.write_byte_data(self.addr, REG_OPR_MODE, MODE_NDOF)
        time.sleep(0.03)

    # ---- reads ---------------------------------------------------------------
    def read_frame(self) -> Optional[dict]:
        """One block read covering gyro, euler, attitude, linear acceleration, gravity.

        0x14-0x33 is 32 bytes and SMBus caps a block read at exactly 32, so all five
        fit in a single transaction - which is the whole corruption defence: fewer
        transactions, and every value in a sample taken at the same instant.
        Returns None if the frame fails its sanity check.
        """
        raw = bytes(self._bus.read_i2c_block_data(self.addr, REG_GYRO, 32))
        frame = decode_frame(raw)
        if frame is None:
            self.bad_frames += 1
        return frame

    def linear_accel(self) -> Tuple[float, float, float]:
        f = self.read_frame()
        return f["accel"] if f else (0.0, 0.0, 0.0)

    def gyro(self) -> Tuple[float, float, float]:
        f = self.read_frame()
        return f["gyro"] if f else (0.0, 0.0, 0.0)

    def euler_heading(self) -> Optional[float]:
        """Absolute heading, 0 = north, clockwise - the fused value, no maths here."""
        f = self.read_frame()
        return f["heading"] if f else None

    def calibration(self) -> Tuple[int, int, int, int]:
        """(sys, gyro, accel, mag), each 0-3. Heading is not trustworthy until mag is 3;
        motion detection does not care."""
        v = self._bus.read_byte_data(self.addr, REG_CALIB_STAT)
        return ((v >> 6) & 3, (v >> 4) & 3, (v >> 2) & 3, v & 3)

    # ---- motion --------------------------------------------------------------
    @property
    def still_for(self) -> float:
        return time.monotonic() - self._last_motion

    def poll(self) -> None:
        frame = self.read_frame()
        if frame is None:
            return                                  # corrupt: say nothing, do not guess
        ax, ay, az = frame["accel"]
        gx, gy, gz = frame["gyro"]
        self.accel_raw = (ax * ax + ay * ay + az * az) ** 0.5
        self.gyro_raw = (gx * gx + gy * gy + gz * gz) ** 0.5
        self.accel_vec = self.accel_filter.push(frame["accel"])
        self.gyro_vec = self.gyro_filter.push(frame["gyro"])
        self.accel_mag = sum(v * v for v in self.accel_vec) ** 0.5
        self.gyro_mag = sum(v * v for v in self.gyro_vec) ** 0.5
        self.gravity = frame["gravity"]
        self.quat = frame["quat"]
        self.heading = frame["heading"]
        self.roll, self.pitch = frame["roll"], frame["pitch"]
        self.reads += 1

        now = time.monotonic()
        if self._last_read:
            dt = now - self._last_read
            if 0.0 < dt < 1.0:                      # smooth, and ignore stalls
                self.hz = 0.8 * self.hz + 0.2 / dt if self.hz else 1.0 / dt
        self._last_read = now

        # One extra I2C round trip a second, instead of one per web request.
        self._calib_countdown -= 1
        if self._calib_countdown <= 0:
            self._calib_countdown = max(1, int(1.0 / max(0.001, self._period)))
            try:
                self.calib = self.calibration()
            except OSError:
                pass

        if self.sample_hook is not None:
            try:
                self.sample_hook(now, frame)
            except Exception:                        # a gesture bug must not stop the gate
                self.hook_errors += 1

        over = self.accel_mag > self.accel_thresh or self.gyro_mag > self.gyro_thresh
        # Motion has to persist; stillness does not. A corrupt frame that slips the
        # validity check is isolated, so it never survives `samples` in a row - but we
        # must not need three samples to *stop*, or the still timer never starts.
        self._moving_run = self._moving_run + 1 if over else 0
        if self._moving_run >= self.samples:
            self._last_motion = time.monotonic()
            self.moving = True
        elif not over:
            self.moving = False

    def set_hz(self, hz: float) -> None:
        """Change the sample rate while running.

        Gesture capture wants far more resolution than motion gating does, but only
        for the second or so it lasts - and the GPS shares this bus, so paying for
        that resolution all the time would starve it. The detector turns the rate up
        when it arms and back down when it is done. See `--cast-hz`.
        """
        self._period = 1.0 / max(1.0, hz)

    def _run(self, hz: float) -> None:
        self.set_hz(hz)
        while not self._stop.is_set():
            try:
                self.poll()
            except OSError:
                self.io_errors += 1
                time.sleep(0.2)
            time.sleep(self._period)

    def start(self, hz: float = 10.0) -> "BNO055":
        # 10 Hz, not 20: the GPS shares this bus and the bus is slowed down to cope
        # with this chip's clock stretching. Motion detection has cycles to spare -
        # gesture capture does not, which is what set_hz() is for.
        self._thread = threading.Thread(target=self._run, args=(hz,), daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self._bus.close()
        except Exception:
            pass
