"""Adafruit Mini GPS PA1010D over I2C - just enough NMEA for the camp bus.

The module streams NMEA sentences at 0x10 and pads with 0x0a whenever it has nothing
to say - including mid-sentence - so a read is "drain the buffer, strip the padding,
and pull whole `$...*XX` sentences out of what is left".

    gps = PA1010D()          # raises OSError if the bus or the module is absent
    gps.start()              # background reader thread
    fix = gps.fix            # None until the first valid GGA/RMC, then a Fix
    fix.lat, fix.lon, fix.acc_m, fix.age_s

Nothing here talks to MQTT; `staff_node.py` decides what to do with a fix.
"""
from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

I2C_ADDR = 0x10
I2C_BUS = 1
READ_CHUNK = 32

# A whole NMEA sentence: $ ... *XX, stopping at the next $ so run-together
# sentences (padding stripped) still separate cleanly.
_SENTENCE_RE = re.compile(r"\$[^$]{1,200}?\*[0-9A-Fa-f]{2}")

# Horizontal error ~= HDOP * UERE. 2.5 m is the usual consumer-GPS figure and is
# what `acc_m` in SCHEMA.md means. Measure it before trusting it (see README).
UERE_M = 2.5

# Output GGA + RMC only, once a second: less noise on a shared I2C bus.
PMTK_SET_NMEA_OUTPUT_RMC_GGA = "$PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0*28"
PMTK_SET_NMEA_UPDATE_1HZ = "$PMTK220,1000*1F"


@dataclass
class Fix:
    lat: float
    lon: float
    acc_m: float
    quality: int          # GGA fix quality: 0 none, 1 GPS, 2 DGPS
    sats: int
    hdop: float
    alt_m: Optional[float]
    at: float             # time.monotonic() when parsed

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.at


def nmea_ok(line: str) -> bool:
    """True if the sentence carries a checksum and it matches."""
    if not line.startswith("$") or "*" not in line:
        return False
    body, _, given = line[1:].partition("*")
    ck = 0
    for ch in body:
        ck ^= ord(ch)
    try:
        return ck == int(given[:2], 16)
    except ValueError:
        return False


def _dm_to_deg(raw: str, hemi: str) -> Optional[float]:
    """NMEA ddmm.mmmm + hemisphere -> signed decimal degrees."""
    if not raw or not hemi:
        return None
    try:
        dot = raw.index(".")
    except ValueError:
        return None
    deg, minutes = raw[: dot - 2], raw[dot - 2 :]
    if not deg:
        return None
    try:
        val = float(deg) + float(minutes) / 60.0
    except ValueError:
        return None
    return -val if hemi in ("S", "W") else val


def parse_gga(fields: List[str], uere_m: float = UERE_M) -> Optional[Fix]:
    """$xxGGA -> Fix, or None when the sentence carries no usable fix."""
    if len(fields) < 10:
        return None
    try:
        quality = int(fields[6] or 0)
    except ValueError:
        return None
    if quality == 0:
        return None
    lat = _dm_to_deg(fields[2], fields[3])
    lon = _dm_to_deg(fields[4], fields[5])
    if lat is None or lon is None:
        return None
    try:
        sats = int(fields[7] or 0)
    except ValueError:
        sats = 0
    try:
        hdop = float(fields[8] or 0) or 99.0
    except ValueError:
        hdop = 99.0
    try:
        alt = float(fields[9]) if fields[9] else None
    except ValueError:
        alt = None
    return Fix(lat=round(lat, 6), lon=round(lon, 6), acc_m=round(hdop * uere_m, 1),
               quality=quality, sats=sats, hdop=hdop, alt_m=alt, at=time.monotonic())


def average_fixes(fixes: List[Fix]) -> Optional[Fix]:
    """Mean of several fixes, as one Fix - what a stationary node should publish.

    Averaging only means anything while standing still, which is exactly when the
    motion gate calls this. `acc_m` is taken as the *worse* of the mean reported
    accuracy and the observed spread, so a tight-looking average over a drifting
    minute does not claim more precision than it has.
    """
    if not fixes:
        return None
    n = len(fixes)
    lat = sum(f.lat for f in fixes) / n
    lon = sum(f.lon for f in fixes) / n
    last = fixes[-1]
    mean_acc = sum(f.acc_m for f in fixes) / n
    m_per_deg_lon = 111320.0 * max(0.01, abs(math.cos(math.radians(lat))))
    spread = max((math.hypot((f.lon - lon) * m_per_deg_lon, (f.lat - lat) * 111320.0)
                  for f in fixes), default=0.0)
    return Fix(lat=round(lat, 6), lon=round(lon, 6), acc_m=round(max(mean_acc, spread), 1),
               quality=last.quality, sats=last.sats, hdop=last.hdop, alt_m=last.alt_m,
               at=last.at)


class PA1010D:
    """Background NMEA reader. Every attribute read is cheap and lock-free enough."""

    def __init__(self, bus: int = I2C_BUS, addr: int = I2C_ADDR, uere_m: float = UERE_M):
        from smbus2 import SMBus, i2c_msg  # imported here so the module imports on a dev PC

        self._SMBus, self._i2c_msg = SMBus, i2c_msg
        self.bus_no, self.addr, self.uere_m = bus, addr, uere_m
        self._bus = SMBus(bus)
        self._buf = ""
        self._stop = threading.Event()
        self._paused = threading.Event()      # set = reader idle (motion gate)
        self._thread: Optional[threading.Thread] = None

        self.fix: Optional[Fix] = None
        self.sentences = 0          # valid NMEA sentences seen
        self.bad_checksums = 0
        self.io_errors = 0          # bus reads that failed: wiring/power, not sky
        self.last_gga: Optional[float] = None   # monotonic of the last GGA, fix or not
        self.sats_in_view = 0       # from GGA even without a fix

    # ---- raw io --------------------------------------------------------------
    def _read_block(self) -> str:
        read = self._i2c_msg.read(self.addr, READ_CHUNK)
        self._bus.i2c_rdwr(read)
        return bytes(read).decode("ascii", "ignore")

    def send_command(self, cmd: str) -> None:
        """Write a PMTK sentence (checksum already in it)."""
        payload = (cmd + "\r\n").encode("ascii")
        write = self._i2c_msg.write(self.addr, payload)
        self._bus.i2c_rdwr(write)

    def configure(self) -> None:
        self.send_command(PMTK_SET_NMEA_OUTPUT_RMC_GGA)
        time.sleep(0.25)
        self.send_command(PMTK_SET_NMEA_UPDATE_1HZ)

    # ---- parsing -------------------------------------------------------------
    def _feed(self, chunk: str) -> List[str]:
        """Accumulate and return the complete sentences.

        Splitting on newlines does not work here. The module pads with 0x0a whenever
        it has nothing to say, *including in the middle of a sentence*, so a line
        split shreds good sentences into fragments that then fail their checksum
        (~20% of them, which reads like bad wiring and is not). Strip the padding
        instead and match `$...*XX` outright; a sentence with genuinely missing bytes
        still fails its checksum in _handle, which is the honest signal.
        """
        self._buf += chunk.replace("\n", "").replace("\r", "").replace("\x00", "")
        if len(self._buf) > 4096:                  # a stuck bus must not eat RAM
            self._buf = self._buf[-1024:]
        out, end = [], 0
        for m in _SENTENCE_RE.finditer(self._buf):
            out.append(m.group(0))
            end = m.end()
        if end:
            self._buf = self._buf[end:]
        else:
            start = self._buf.rfind("$")           # keep only a sentence in progress
            if start > 0:
                self._buf = self._buf[start:]
        return out

    def _handle(self, line: str) -> None:
        if not nmea_ok(line):
            self.bad_checksums += 1
            return
        self.sentences += 1
        fields = line[1:].split("*")[0].split(",")
        kind = fields[0][2:]                       # GP/GN/GL prefix varies
        if kind == "GGA":
            self.last_gga = time.monotonic()
            try:
                self.sats_in_view = int(fields[7] or 0)
            except (ValueError, IndexError):
                pass
            fix = parse_gga(fields, self.uere_m)
            if fix:
                self.fix = fix

    def poll(self, max_reads: int = 24) -> None:
        """Drain the module's buffer and parse what came out.

        One 32-byte read per cycle is not enough: the PA1010D emits GGA/RMC/GSA/GSV
        faster than that, its FIFO wraps, and sentences arrive with holes in them -
        which shows up as a high `bad_checksums` rather than as an error. So keep
        reading until a chunk is nothing but the 0x0a idle padding.
        """
        for _ in range(max_reads):
            chunk = self._read_block()
            for line in self._feed(chunk):
                self._handle(line)
            if not chunk.strip("\n\r\x00 "):
                return                             # only padding left: buffer is drained

    # ---- lifecycle -----------------------------------------------------------
    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self) -> None:
        """Stop reading the module. `fix` keeps its last value - it is simply no longer
        refreshed, so callers must decide whether a frozen fix is still usable."""
        self._paused.set()

    def resume(self) -> None:
        self._buf = ""                        # whatever queued while idle is half a sentence
        self._paused.clear()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.2)
                continue
            try:
                self.poll()
            except OSError:
                self.io_errors += 1
                time.sleep(0.5)                    # unplugged module must not kill the node
            time.sleep(0.05)

    def start(self) -> "PA1010D":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self._bus.close()
        except Exception:
            pass

    def wait_for_fix(self, timeout: float = 120.0) -> Optional[Fix]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.fix is not None:
                return self.fix
            time.sleep(0.5)
        return self.fix
