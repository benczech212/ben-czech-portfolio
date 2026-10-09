"""The spell knob: an Adafruit I2C QT Rotary Encoder as the staff's colour picker.

Fitted 2026-10-04. A seesaw board (an ATtiny817 running Adafruit's seesaw firmware)
at 0x36 on i2c-1, the same bus as the BNO055 and the PA1010D, with a push button and
one NeoPixel on it.

  * Turning the knob turns the hue. That hue *is* the spell colour: it is what goes
    out as `color` in every cast, which is the contract's existing "the prop's current
    colour" field, so nothing new rides on the bus.
  * At rest the pixel glows dim in that hue - the indicator of what you are holding.
  * A cast fades the pixel up to full in that hue and back down to the glow.
  * While the staff is armed (held still, ready to cast) the glow slowly brightens -
    charging - and drops back if the arm is lost without a cast.
  * The button previews the spell: the same flash and sound, published nowhere.
  * On start it runs once round the rainbow (~1 s), beginning and ending at the
    selected hue, then settles to the glow - "I am awake, and this is my colour".
    The hue is saved to a small file, so it is the colour Ben last chose, not a default.

The rule, again, is that the knob must never cost the sensors anything. This bus is
the one the BNO055's clock stretching already makes fragile, it runs at 50 kHz, and
the CPU is one ARMv6 core. So:

  * One thread, polling at `hz` (default 25). Each poll is one 4-byte position read
    and one 4-byte button read, about 2-3 ms of bus time at 50 kHz.
  * The pixel is written only when its colour actually changes - every frame of a
    fade, otherwise only when the knob turns. A staff at rest writes nothing.
  * Position, not delta: the seesaw keeps an absolute count, so a missed or failed
    poll loses no clicks, it just reports them on the next one.
  * Failures are counted, never fatal. A knob that stops answering backs off to one
    try a second, and the page says so.

No knob (unplugged, the dev PC, the sim) is a normal state: `SpellKnob.open()` returns
None with a reason, and the staff casts in its configured colour exactly as before.

seesaw register map from Adafruit's seesaw firmware/library (Adafruit_seesaw.h).
"""
from __future__ import annotations

import colorsys
import json
import os
import struct
import threading
import time
from typing import Callable, Optional

# ---- seesaw ----------------------------------------------------------------------
STATUS, GPIO, NEOPIXEL, ENCODER = 0x00, 0x01, 0x0E, 0x11
STATUS_HW_ID, STATUS_VERSION, STATUS_SWRST = 0x01, 0x02, 0x7F
GPIO_DIRCLR_BULK, GPIO_BULK, GPIO_BULK_SET, GPIO_PULLENSET = 0x03, 0x04, 0x05, 0x0B
NEO_PIN, NEO_SPEED, NEO_BUF_LENGTH, NEO_BUF, NEO_SHOW = 0x01, 0x02, 0x03, 0x04, 0x05
ENC_POSITION = 0x30

BUTTON_PIN = 24           # on the QT Rotary Encoder (Adafruit 4991 / 5880)
PIXEL_PIN = 6
DEFAULT_ADDR = 0x36


class Seesaw:
    """Just the seesaw calls the knob needs, over smbus2's raw i2c_rdwr.

    A seesaw read is two transactions with a pause between: write the register,
    give the ATtiny a moment to fetch it, then read. Another device's transfer may
    land in that pause - harmless, the seesaw keeps its register pointer."""

    def __init__(self, bus: int = 1, addr: int = DEFAULT_ADDR, read_delay: float = 0.002):
        from smbus2 import SMBus, i2c_msg  # here, so this module imports on a dev PC

        self._msg = i2c_msg
        self._bus = SMBus(bus)
        self.bus_no, self.addr, self.read_delay = bus, addr, read_delay

    def write(self, base: int, fn: int, data: bytes = b"") -> None:
        self._bus.i2c_rdwr(self._msg.write(self.addr, bytes([base, fn]) + bytes(data)))

    def read(self, base: int, fn: int, n: int) -> bytes:
        self._bus.i2c_rdwr(self._msg.write(self.addr, [base, fn]))
        time.sleep(self.read_delay)
        r = self._msg.read(self.addr, n)
        self._bus.i2c_rdwr(r)
        return bytes(r)

    def close(self) -> None:
        try:
            self._bus.close()
        except OSError:
            pass

    # -- the encoder board's parts --
    def setup(self) -> int:
        """Reset, configure the button and pixel, return the product id (4991/5880)."""
        hw = self.read(STATUS, STATUS_HW_ID, 1)[0]
        if hw in (0x00, 0xFF):
            raise OSError("no seesaw at 0x%02x (hw id 0x%02x)" % (self.addr, hw))
        self.write(STATUS, STATUS_SWRST, b"\xff")
        time.sleep(0.5)                                    # the ATtiny reboots
        product = struct.unpack(">I", self.read(STATUS, STATUS_VERSION, 4))[0] >> 16
        mask = struct.pack(">I", 1 << BUTTON_PIN)
        self.write(GPIO, GPIO_DIRCLR_BULK, mask)           # input
        self.write(GPIO, GPIO_PULLENSET, mask)             # with pull-up
        self.write(GPIO, GPIO_BULK_SET, mask)              # pull *up*, not down
        self.write(NEOPIXEL, NEO_PIN, bytes([PIXEL_PIN]))
        self.write(NEOPIXEL, NEO_SPEED, b"\x01")           # 800 kHz
        self.write(NEOPIXEL, NEO_BUF_LENGTH, struct.pack(">H", 3))
        return product

    def position(self) -> int:
        return struct.unpack(">i", self.read(ENCODER, ENC_POSITION, 4))[0]

    def pressed(self) -> bool:
        bits = struct.unpack(">I", self.read(GPIO, GPIO_BULK, 4))[0]
        return not (bits >> BUTTON_PIN) & 1                # pulled up: pressed reads low

    def pixel(self, r: int, g: int, b: int) -> None:
        self.write(NEOPIXEL, NEO_BUF, bytes([0, 0, g, r, b]))   # offset 0, GRB order
        self.write(NEOPIXEL, NEO_SHOW)


# ---- colour ----------------------------------------------------------------------
def hue_to_hex(hue: float) -> str:
    r, g, b = colorsys.hsv_to_rgb((hue % 360) / 360.0, 1.0, 1.0)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def hex_to_hue(color: str) -> float:
    try:
        c = color.lstrip("#")
        r, g, b = (int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return 0.0
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360.0


def pixel_rgb(hue: float, level: float) -> tuple:
    """`level` is perceived brightness 0..1; LEDs are linear, so square-ish it (gamma
    2.2) or a fade spends all its time looking full and then drops off a cliff."""
    k = max(0.0, min(1.0, level)) ** 2.2
    r, g, b = colorsys.hsv_to_rgb((hue % 360) / 360.0, 1.0, 1.0)
    return (round(r * 255 * k), round(g * 255 * k), round(b * 255 * k))


class Flash:
    """The cast envelope: rise to full, hold, fall back to the resting glow."""

    def __init__(self, rise: float = 0.15, hold: float = 0.25, fall: float = 1.1):
        self.rise, self.hold, self.fall = rise, hold, fall
        self.at: Optional[float] = None

    @property
    def length(self) -> float:
        return self.rise + self.hold + self.fall

    def level(self, now: float, rest: float) -> float:
        if self.at is None:
            return rest
        t = now - self.at
        if t < 0 or t >= self.length:
            self.at = None
            return rest
        if t < self.rise:
            return rest + (1.0 - rest) * (t / self.rise)
        t -= self.rise
        if t < self.hold:
            return 1.0
        t = (t - self.hold) / self.fall
        return rest + (1.0 - rest) * (1.0 - t) ** 2       # ease out: quick, then a long tail


# ---- the knob --------------------------------------------------------------------
class SpellKnob:
    def __init__(self, dev, hue: float = 15.0, step: float = 15.0, rest: float = 0.3,
                 reverse: bool = False, hz: float = 25.0,
                 on_change: Optional[Callable[[str], None]] = None,
                 on_press: Optional[Callable[[], None]] = None,
                 state_file: Optional[str] = None, product: Optional[int] = None,
                 intro: float = 1.0):
        self.dev = dev
        self.hue = hue % 360
        self.step = -step if reverse else step
        self.rest = rest
        self.hz = hz
        self.on_change, self.on_press = on_change, on_press
        self.state_file = state_file
        self.product = product
        self.flash = Flash()
        self.intro = intro                # seconds of startup rainbow; 0 = none
        self.charge_peak, self.charge_ramp = 0.7, 3.0
        self._charge_at: Optional[float] = None
        self._intro_at: Optional[float] = None
        self.level = rest
        self.pressed = False
        self.presses = 0
        self.turns = 0
        self.io_errors = 0
        self.ok = True                    # False while it has stopped answering
        self._pos: Optional[int] = None
        self._shown = None
        self._dirty_at: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @classmethod
    def open(cls, bus: int = 1, addr: int = DEFAULT_ADDR, **kw):
        """(knob, None) or (None, why). Never raises: no knob is not an error."""
        try:
            dev = Seesaw(bus, addr)
        except Exception as exc:                          # no smbus2, no /dev/i2c-1
            return None, "%s: %s" % (exc.__class__.__name__, exc)
        try:
            product = dev.setup()
        except OSError as exc:
            dev.close()
            return None, "nothing answered at 0x%02x on i2c-%d (%s)" % (addr, bus, exc)
        state_file = kw.get("state_file")
        if state_file:
            kw["hue"] = load_hue(state_file, kw.get("hue", 15.0))
        return cls(dev, product=product, **kw), None

    @property
    def color(self) -> str:
        return hue_to_hex(self.hue)

    def cast(self) -> None:
        """A spell went out: light up. Called from the IMU thread - it only sets a
        timestamp; the knob thread does the drawing."""
        self._charge_at = None
        self.flash.at = time.monotonic()

    def charging(self, on: bool) -> None:
        """Armed (True) or not any more (False). Also just a timestamp."""
        if on and self._charge_at is None:
            self._charge_at = time.monotonic()
        elif not on:
            self._charge_at = None

    def _base(self, now: float) -> float:
        """The resting glow, or the charge glow climbing toward charge_peak."""
        if self._charge_at is None:
            return self.rest
        k = min(1.0, max(0.0, (now - self._charge_at) / self.charge_ramp))
        return self.rest + (self.charge_peak - self.rest) * k

    # -- one poll: read, decide, draw --
    def tick(self, now: float) -> None:
        try:
            pos = self.dev.position()
            pressed = self.dev.pressed()
        except OSError:
            self.io_errors += 1
            self.ok = False
            return
        if not self.ok:
            # Back after dropping out. If it lost power, the seesaw has forgotten its
            # pull-up and pixel setup and restarted its count: set it up again and
            # re-learn where the knob is, rather than jumping the hue.
            setup = getattr(self.dev, "setup", None)
            if setup is not None:
                try:
                    setup()
                    pos = self.dev.position()      # a reset zeroes the count
                except OSError:
                    self.io_errors += 1
                    return
            self._pos, self._shown = pos, None    # new baseline; redraw the pixel
        self.ok = True
        if self._pos is None:
            self._pos = pos
        elif pos != self._pos:
            self.hue = (self.hue + (pos - self._pos) * self.step) % 360
            self.turns += abs(pos - self._pos)
            self._pos = pos
            self._dirty_at = now
            if self.on_change:
                self.on_change(self.color)
        if pressed and not self.pressed:
            self.presses += 1
            self.flash.at = now           # a preview flashes exactly like a cast
            if self.on_press:
                self.on_press()
        self.pressed = pressed

        shown_hue = self.hue
        if self._intro_at is not None and now - self._intro_at < self.intro:
            # once round the wheel from the selected hue back to it, so it lands home
            shown_hue = self.hue + 360.0 * (now - self._intro_at) / self.intro
            self.level = 0.8
        else:
            self._intro_at = None
            self.level = self.flash.level(now, self._base(now))
        rgb = pixel_rgb(shown_hue, self.level)
        if rgb != self._shown:
            try:
                self.dev.pixel(*rgb)
                self._shown = rgb
            except OSError:
                self.io_errors += 1

        # Remember the colour across restarts, but do not write the SD card on every
        # click: once, a few seconds after the knob stops moving.
        if self._dirty_at is not None and now - self._dirty_at > 3.0:
            self._dirty_at = None
            if self.state_file:
                save_hue(self.state_file, self.hue)

    def _run(self) -> None:
        period = 1.0 / max(1.0, self.hz)
        while not self._stop.is_set():
            self.tick(time.monotonic())
            # A knob that stopped answering is retried once a second, not 25 times:
            # hammering a dead address is bus time the IMU and GPS could have had.
            if self._stop.wait(period if self.ok else 1.0):
                return

    def start(self) -> "SpellKnob":
        if self.on_change:
            self.on_change(self.color)
        if self.intro > 0:
            self._intro_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True, name="knob")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(1.0)
        try:
            self.dev.pixel(0, 0, 0)                       # a stopped staff goes dark
        except OSError:
            pass
        if self.state_file and self._dirty_at is not None:
            save_hue(self.state_file, self.hue)
        close = getattr(self.dev, "close", None)
        if close:
            close()

    def state(self) -> dict:
        return {"present": True, "ok": self.ok, "hue": round(self.hue, 1), "color": self.color,
                "level": round(self.level, 2), "rest": self.rest, "pressed": self.pressed,
                "presses": self.presses, "turns": self.turns, "io_errors": self.io_errors,
                "intro": self._intro_at is not None, "charging": self._charge_at is not None,
                "product": self.product}


def load_hue(path: str, default: float) -> float:
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            return float(json.load(f)["hue"]) % 360
    except (OSError, ValueError, KeyError, TypeError):
        return default


def save_hue(path: str, hue: float) -> None:
    path = os.path.expanduser(path)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"hue": round(hue, 1)}, f)
        os.replace(tmp, path)                             # a power cut mid-write leaves the old one
    except OSError:
        pass
