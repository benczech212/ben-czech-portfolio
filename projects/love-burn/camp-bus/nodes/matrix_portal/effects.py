"""The matrix panel's effect engine: pure maths, no hardware and no network.

Everything that decides *what colour the panel should be* lives here, so it can be
tested on a laptop (`tests/test_matrix_effects.py`) and previewed in a terminal
(`preview.py`) without flashing a board. `code.py` is then only wiring: it owns the
WiFi, the MQTT socket and the framebuffer, and asks this file what to draw.

HOW IT DRAWS WITHOUT MELTING A SAMD51
-------------------------------------
A 64x32 panel is 2048 pixels. Touching all of them every frame from CircuitPython is
far too slow. So every pixel is assigned, once, **two** bucket indices packed into one
byte, and the animation is done entirely by rewriting the palette:

    index = position_bucket * LEVELS + texture_bucket

  * `position_bucket` (`projection()`) is how far along the fill the pixel sits
  * `texture_bucket` (`noise_field()`) is which band of the Perlin texture it is in

That is `STEPS * LEVELS` = 256 palette writes a frame instead of 2048 pixel writes,
and it is the largest palette an 8-bit bitmap can address, so the packing is free.
The position buckets are rebuilt only when the *direction* changes - there are eight -
and the noise field never changes at all, because it does not depend on direction.

The decomposition is also what keeps the colour maths affordable. Hue is rotated
properly, but only across the fill axis, which is `STEPS` rotations a frame. The
texture's colour wander is a per-channel tint - three multiplies - because that one
runs `STEPS * LEVELS` times.

ZONES: THE PANEL IS NOT ALWAYS A SCREEN
---------------------------------------
Behind a pile of rocks, a panel is a set of light sources, not a picture. A **zone
map** says which pixels light which rock, and once there is one, the position bucket
stops meaning "how far along the fill" and starts meaning "which rock" - the same 32
buckets, the same 256-entry palette, the same per-frame cost. Nothing else changes,
which is the whole reason the bucket indirection was worth having.

`rainbow` is the effect built on that: every zone takes a different hue and the whole
set walks around the wheel. With `seconds: 0` it holds still, and a static rainbow is
exactly "draw each zone in its own colour" - so the editor's debug view and the show
effect are the same code, and what you see while authoring is what the rocks do.

THE TEXTURE IS ALSO A MAP OF PLACES
-----------------------------------
The noise bands are not only decoration. An iso-band of Perlin noise is a scattered
set of organic patches, and the high bands are the small isolated peaks - so "light up
some small areas" is just "raise the gain on the top bands", which is a palette write
rather than a search through 2048 pixels.

That is what `twinkle` and a regional `charge` are built on. `twinkle` sparkles the
small high bands, each on its own phase so they do not blink in unison. `charge` grows
its colour outward from those same peaks as the intensity climbs, so a light charge
freckles the panel and a heavy one floods it. Neither needs a fill to be running, and
neither costs more than the fill already did.

THE FRONT IS AN ARC, NOT A STRAIGHT EDGE
----------------------------------------
A fill still comes *from* a direction, but it arrives as an expanding circular
wavefront, the way everything else on this bus models a cast. The wave's centre sits
off the panel, opposite `heading`, `curve` panel-radii out: at `curve = 0` the centre
is the panel itself and it becomes a ring with no direction at all, and as `curve`
grows the arc flattens back into a straight wipe.

WHAT HEADING MEANS
------------------
`heading` is already in SCHEMA.md - "degrees, 0 = north, clockwise" - and it already
means *the direction a cast is pointing*. A fill reuses it unchanged: **`heading` is
the direction the fill travels**, so `heading: 0` moves toward north, entering from
the south edge. The eight directions people ask for are simply 0, 45, 90 ... 315.

Nothing new is invented, and because the staff publishes a real fused `heading` on
every cast, one mapping rule is enough to make the panel fill the way the staff points.

`north` is the compass bearing of the panel's own top edge. Leave it 0 and headings
are read in panel space (0 = toward the top of the panel), which is what you want on a
bench. Once the panel is bolted to something, set it and bus headings become true
compass directions.
"""
from math import cos, floor, pi, radians, sin

STEPS = 32               # buckets along the fill
LEVELS = 8               # bands of Perlin texture. STEPS * LEVELS must stay <= 256,
                         # which is the most an 8-bit bitmap index can address.
EDGE = 0.14              # softness of a fill's leading edge, as a fraction of the panel
BAND = 0.22              # width of a `sweep` band, same units
GLOW_HALFLIFE_S = 5.0    # how fast `charge` bleeds away

CURVE = 1.4              # wavefront centre distance, in panel radii. 0 = a ring.
HUE_SPREAD = 26.0        # degrees of hue rotation across the fill axis
TEXTURE = 0.30           # how hard the Perlin texture bites, 0..1
TEXTURE_HUE = 18.0       # degrees the texture wanders around the colour wheel
TEXTURE_DRIFT = 0.0      # turns/s the texture breathes. Off by default because a
                         # drifting texture re-dirties all 256 palette entries every
                         # frame, which is most of the board's frame budget.

NOISE_SCALE = 4.5        # Perlin feature size, in cells across the panel's long edge
NOISE_OCTAVES = 3
NOISE_SEED = 1013

MAX_ZONES = STEPS        # zone * LEVELS + texture still has to fit in one byte
# The top zone id is reserved and painted white: it is the calibration probe. A
# reserved id rather than a new field, because the map already carries zone ids and
# this is a statement about one block of pixels, which is exactly what the map is for.
# It also means the probe travels *with* the rest of the map, so the rocks already
# worked out stay lit in their own colours while the next one is being asked about -
# which is the whole point, since what you are checking is this rock against those.
PROBE_ZONE = MAX_ZONES - 1
MAX_ROCKS = PROBE_ZONE - 1          # ids 1..30, since 0 is "no rock" and 31 is the probe
# The golden angle. Zone hues step by this rather than dividing the wheel evenly, for
# two reasons, neither of which is covering the wheel - an even spread does that better.
#   - Adding a rock must not recolour the ones already mapped. An even spread repaints
#     every zone each time the count changes, which is unusable while you are
#     calibrating seventeen of them one at a time.
#   - Calibration walks the panel in a serpentine, so rocks next to each other get
#     consecutive ids. Under an even spread the two rocks most easily confused are also
#     the two closest on the wheel; here consecutive ids are always 137 degrees apart.
GOLDEN_ANGLE = 137.50776
RAINBOW_SECONDS = 12.0   # default time for the hue to walk all the way round

TWINKLE_BANDS = 3        # how many of the top (smallest) bands sparkle
TWINKLE_RATE = 1.7       # sparkles per second per band
TWINKLE_GAIN = 1.8       # how much brighter a sparking band gets
REGION_SPREAD = 0.15     # softness of the edge as `charge` grows down the bands

# Effects that look the same everywhere, effects that have a front, and effects that
# pick out places using the noise bands.
UNIFORM = ("flash", "pulse", "color")
DIRECTIONAL = ("wipe", "sweep")
REGIONAL = ("charge", "twinkle")
ZONAL = ("rainbow",)
KNOWN = UNIFORM + DIRECTIONAL + REGIONAL + ZONAL

WHITE = (1.0, 1.0, 1.0)


# ---- colour ------------------------------------------------------------------

def parse_color(value, default=WHITE):
    """`#rrggbb` or `#rgb` to floats. Anything unreadable falls back rather than raising:
    a malformed colour on the bus must not take a light off the air."""
    if not isinstance(value, str):
        return default
    s = value.strip().lstrip("#")
    try:
        if len(s) == 3:
            return tuple(int(c * 2, 16) / 255.0 for c in s)
        if len(s) == 6:
            return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        pass
    return default


def mix(a, b, k):
    """Move colour `a` toward `b` by `k`. This is the contract's colour-mixing rule:
    every effect payload may carry `color` and `intensity`, and the responder mixes
    that colour into whatever it is already showing with weight = intensity."""
    if k <= 0.0:
        return a
    if k >= 1.0:
        return b
    return (a[0] + (b[0] - a[0]) * k,
            a[1] + (b[1] - a[1]) * k,
            a[2] + (b[2] - a[2]) * k)


def to_rgb888(c, brightness=1.0):
    def ch(v):
        v = int(v * brightness * 255.0 + 0.5)
        return 0 if v < 0 else (255 if v > 255 else v)
    return (ch(c[0]) << 16) | (ch(c[1]) << 8) | ch(c[2])


def clamp01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def rotate_hue(c, degrees):
    """Turn a colour around the wheel, keeping its brightness.

    The standard luminance-preserving matrix. It is nine multiplies, so it is used
    across the fill axis (`STEPS` times a frame) and never per texture band.
    """
    if not degrees:
        return c
    a = radians(degrees)
    cs, sn = cos(a), sin(a)
    r, g, b = c
    return (clamp01(r * (0.299 + 0.701 * cs + 0.168 * sn)
                    + g * (0.587 - 0.587 * cs + 0.330 * sn)
                    + b * (0.114 - 0.114 * cs - 0.497 * sn)),
            clamp01(r * (0.299 - 0.299 * cs - 0.328 * sn)
                    + g * (0.587 + 0.413 * cs + 0.035 * sn)
                    + b * (0.114 - 0.114 * cs + 0.292 * sn)),
            clamp01(r * (0.299 - 0.300 * cs + 1.250 * sn)
                    + g * (0.587 - 0.588 * cs - 1.050 * sn)
                    + b * (0.114 + 0.886 * cs - 0.203 * sn)))


def rgb_to_hsv(c):
    r, g, b = c
    hi, lo = max(r, g, b), min(r, g, b)
    span = hi - lo
    if span <= 0.0:
        h = 0.0
    elif hi == r:
        h = 60.0 * (((g - b) / span) % 6.0)
    elif hi == g:
        h = 60.0 * ((b - r) / span + 2.0)
    else:
        h = 60.0 * ((r - g) / span + 4.0)
    return h, (span / hi if hi > 0 else 0.0), hi


def hsv_to_rgb(h, s, v):
    """The piecewise conversion, deliberately not a hue-rotation matrix.

    A rainbow changes its angle every frame, so the cached rotation coefficients that
    make the fill's hue spread cheap are no use here - and `rotate_hue` costs a pair of
    trig calls each time, which measured 7.5 ms for 32 of them on this board. This is
    a handful of multiplies and no trig at all.
    """
    h = h % 360.0
    c = v * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = v - c
    i = int(h / 60.0)
    if i == 0:
        r, g, b = c, x, 0.0
    elif i == 1:
        r, g, b = x, c, 0.0
    elif i == 2:
        r, g, b = 0.0, c, x
    elif i == 3:
        r, g, b = 0.0, x, c
    elif i == 4:
        r, g, b = x, 0.0, c
    else:
        r, g, b = c, 0.0, x
    return (r + m, g + m, b + m)


def decode_zones(msg, width, height, max_zones=MAX_ZONES):
    """A retained zone map to a per-pixel bytearray, plus how many zones it holds.

    Run-length encoded, because zones are blobs: a hand-drawn map of a dozen rocks is
    a few hundred bytes this way against 2048 raw, and it stays readable on the wire.
    Returns `(None, 0)` for anything that does not fit this panel rather than raising -
    a malformed retained config must not stop the panel booting.
    """
    try:
        if int(msg.get("w", 0)) != width or int(msg.get("h", 0)) != height:
            return None, 0
        runs = msg.get("rle") or []
        out = bytearray(width * height)
        i = 0
        top = 0
        for run in runs:
            zone, count = int(run[0]), int(run[1])
            if zone < 0 or zone >= max_zones:
                zone = 0
            if zone > top:
                top = zone
            end = min(len(out), i + count)
            while i < end:
                out[i] = zone
                i += 1
            if i >= len(out):
                break
        if i < len(out):
            return None, 0                       # short map: do not half-light a rock
        return out, top + 1
    except (TypeError, ValueError, IndexError, AttributeError):
        return None, 0


# ---- Perlin noise ---------------------------------------------------------------
# Proper gradient noise, not value noise: the difference is visible as soon as you
# fill a panel with it, because value noise leaves a grid of bright cell centres.

_GRAD2 = ((1.0, 1.0), (-1.0, 1.0), (1.0, -1.0), (-1.0, -1.0))
_TAU3 = 2.0943951023931953          # 120 degrees, for the three-channel tint


def _perm_table(seed=NOISE_SEED):
    """A deterministic shuffle. `random` is not worth importing on a microcontroller,
    and a fixed table means the panel wears the same texture every boot."""
    p = list(range(256))
    state = (seed & 0xFFFF) or 1
    for i in range(255, 0, -1):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        j = state % (i + 1)
        p[i], p[j] = p[j], p[i]
    return p + p                    # doubled, so perm[perm[x] + y] never overruns


def _fade(t):
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def perlin2(x, y, perm):
    """One octave of 2D Perlin noise, roughly -0.7 .. 0.7."""
    x0, y0 = floor(x), floor(y)
    xf, yf = x - x0, y - y0
    xi, yi = int(x0) & 255, int(y0) & 255
    u, v = _fade(xf), _fade(yf)

    def dot(ix, iy, dx, dy):
        gx, gy = _GRAD2[perm[perm[ix] + iy] & 3]
        return gx * dx + gy * dy

    n00 = dot(xi, yi, xf, yf)
    n10 = dot(xi + 1, yi, xf - 1.0, yf)
    n01 = dot(xi, yi + 1, xf, yf - 1.0)
    n11 = dot(xi + 1, yi + 1, xf - 1.0, yf - 1.0)
    a = n00 + u * (n10 - n00)
    b = n01 + u * (n11 - n01)
    return a + v * (b - a)


def fbm(x, y, perm, octaves=NOISE_OCTAVES):
    """Stacked octaves, so the texture has both large blotches and fine grain."""
    total = norm = 0.0
    amp, freq = 1.0, 1.0
    for _ in range(octaves):
        total += perlin2(x * freq, y * freq, perm) * amp
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / (norm or 1.0)


_EQ_BINS = 96


def noise_field(width, height, levels=LEVELS, scale=NOISE_SCALE,
                seed=NOISE_SEED, octaves=NOISE_OCTAVES):
    """Which texture band each pixel belongs to, row-major.

    Built once and never rebuilt: it does not depend on the fill direction, and the
    texture *moves* by re-tinting the bands rather than by re-assigning pixels.

    The bands are **equalised** - each holds about the same number of pixels - which
    matters more than it sounds. Stacked octaves of Perlin are roughly Gaussian, so a
    straight linear mapping bunches almost everything into the middle bands: measured
    on a 64x32 panel it put 1 pixel in the top band and none in the bottom, which
    wastes two of the 256 palette entries and leaves `twinkle` sparkling a single LED.
    Equalising makes "the top band" mean "the smallest eighth of the panel", whatever
    the seed and scale happen to be.

    It costs a second pass over the noise rather than a sorted copy of the field,
    because 2048 floats is a lot of memory on a SAMD51 and this only runs at boot.
    """
    perm = _perm_table(seed)
    step = scale / float(max(1, width))     # same on both axes, or the grain stretches
    lo, hi = -0.75, 0.75
    span = hi - lo

    hist = [0] * _EQ_BINS
    for y in range(height):
        ny = y * step
        for x in range(width):
            u = (fbm(x * step, ny, perm, octaves) - lo) / span
            b = int(clamp01(u) * (_EQ_BINS - 1) + 0.5)
            hist[b] += 1

    # Walk the histogram and mark where each band should end.
    total = width * height
    edges, seen, band = [], 0, 1
    for b in range(_EQ_BINS):
        seen += hist[b]
        while band < levels and seen >= total * band / float(levels):
            edges.append(b)
            band += 1
    while len(edges) < levels - 1:
        edges.append(_EQ_BINS - 1)

    out = bytearray(width * height)
    i = 0
    for y in range(height):
        ny = y * step
        for x in range(width):
            u = (fbm(x * step, ny, perm, octaves) - lo) / span
            b = int(clamp01(u) * (_EQ_BINS - 1) + 0.5)
            level = 0
            while level < levels - 1 and b > edges[level]:
                level += 1
            out[i] = level
            i += 1
    return out


def band_tint(level, levels=LEVELS, texture=TEXTURE, hue=TEXTURE_HUE, phase=0.0,
              gain=0.0):
    """The brightness-and-colour nudge for one texture band.

    Three multiplies per palette entry, which is what a `STEPS * LEVELS` palette can
    afford - a real hue rotation here would be nine, on 256 entries, every frame.
    `phase` walks the bands so the texture breathes without touching a single pixel,
    and `gain` is what `twinkle` adds on top.
    """
    t = level / float(max(1, levels - 1))             # 0 = broad dim areas, 1 = peaks
    ang = 2.0 * pi * (t + phase)
    bright = 1.0 + texture * (2.0 * t - 1.0) + gain
    k = (hue / 360.0) * 2.0
    return (max(0.0, bright * (1.0 + k * cos(ang))),
            max(0.0, bright * (1.0 + k * cos(ang - _TAU3))),
            max(0.0, bright * (1.0 + k * cos(ang + _TAU3))))


# ---- geometry ----------------------------------------------------------------

def snap_heading(heading, buckets=8):
    """The eight directions, as a bucket index and the angle it stands for.

    Snapping is a *rendering* decision, not a contract one: headings stay continuous on
    the bus, and the panel rounds to the nearest of the directions it has a pixel map
    cached for. Eight is what the panel is asked for; raise it and the only cost is
    more cached maps.
    """
    step = 360.0 / buckets
    idx = int(round((heading % 360.0) / step)) % buckets
    return idx, idx * step


def projection(width, height, heading_deg, steps=STEPS, curve=CURVE):
    """Per-pixel bucket along the fill, row-major, length width*height.

    The fill is an **expanding circle**, not a straight edge. Its centre sits off the
    panel, opposite `heading`, `curve` panel-radii from the middle - so the front
    arrives as an arc from that direction, the way a shockwave would. `curve = 0` puts
    the centre on the panel and it becomes a plain expanding ring, with `heading` no
    longer meaning anything; large `curve` flattens the arc back into a straight wipe.

    Panel space: x runs right, y runs down, 0 degrees is toward the *top*, so a heading
    points along (sin, -cos).
    """
    th = radians(heading_deg)
    dx, dy = sin(th), -cos(th)
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    radius = (cx * cx + cy * cy) ** 0.5 or 1.0
    ox, oy = cx - dx * curve * radius, cy - dy * curve * radius

    # Closed form, so this stays one pass: the farthest point of a rectangle from any
    # centre is always a corner, and the nearest is the centre clamped onto it.
    hi = 0.0
    for ex, ey in ((0.0, 0.0), (width - 1.0, 0.0), (0.0, height - 1.0),
                   (width - 1.0, height - 1.0)):
        d = ((ex - ox) ** 2 + (ey - oy) ** 2) ** 0.5
        if d > hi:
            hi = d
    nx = 0.0 if ox < 0.0 else (width - 1.0 if ox > width - 1.0 else ox)
    ny = 0.0 if oy < 0.0 else (height - 1.0 if oy > height - 1.0 else oy)
    lo = ((nx - ox) ** 2 + (ny - oy) ** 2) ** 0.5
    span = (hi - lo) or 1.0
    last = steps - 1

    out = bytearray(width * height)
    i = 0
    for y in range(height):
        ry = y - oy
        ry2 = ry * ry
        for x in range(width):
            rx = x - ox
            u = (((rx * rx + ry2) ** 0.5) - lo) / span
            out[i] = int(clamp01(u) * last + 0.5)
            i += 1
    return out


# ---- propagation --------------------------------------------------------------

EARTH_M_PER_DEG = 111320.0


def distance_m(a, b):
    """Equirectangular metres. Plenty at camp scale, and cheap on a microcontroller."""
    dlat = (b[0] - a[0]) * EARTH_M_PER_DEG
    dlon = (b[1] - a[1]) * EARTH_M_PER_DEG * cos(radians((a[0] + b[0]) * 0.5))
    return (dlat * dlat + dlon * dlon) ** 0.5


def bearing_deg(a, b):
    """Compass bearing from a to b, 0 = north, clockwise."""
    dlat = (b[0] - a[0]) * EARTH_M_PER_DEG
    dlon = (b[1] - a[1]) * EARTH_M_PER_DEG * cos(radians((a[0] + b[0]) * 0.5))
    from math import atan2, degrees
    return degrees(atan2(dlon, dlat)) % 360.0


def wrap180(deg):
    return (deg + 180.0) % 360.0 - 180.0


def propagate(msg, my_pos):
    """The contract's propagation rule, for one message: `(delay_s, intensity)`, or
    `None` if this node should ignore the message entirely.

    Implemented here rather than skipped because a responder that ignores `origin` is
    not on the same bus as everything else - a cast would light the panel before it
    lights the crystal standing next to it.
    """
    intensity = msg.get("intensity", 1.0)
    try:
        intensity = float(intensity)
    except (TypeError, ValueError):
        intensity = 1.0
    intensity = clamp01(intensity)

    origin = msg.get("origin") or {}
    lat, lon = origin.get("lat"), origin.get("lon")
    if my_pos is None or lat is None or lon is None:
        return 0.0, intensity              # no origin, or I do not know where I am

    here = (float(lat), float(lon))
    dist = distance_m(here, my_pos)

    cutoff = msg.get("cutoff_m")
    if cutoff is not None and dist > float(cutoff):
        return None                        # a hard edge, for deliberately local effects

    shape = msg.get("shape", "circle")
    heading = msg.get("heading")
    angle = None
    if heading is not None and dist > 0.5:
        angle = wrap180(bearing_deg(here, my_pos) - float(heading))
    if shape == "cone" and angle is not None:
        half = float(msg.get("cone_deg", 70)) / 2.0
        if abs(angle) > half:
            return None                    # outside the cone: this one is not for me

    speed = float(msg.get("speed_mps", 6) or 6)
    if msg.get("instant"):
        delay = 0.0
    elif shape == "spiral":
        # Rotation only: the sweep reaches near and far nodes at the same angle.
        spin = float(msg.get("spin_dir", 1) or 1)
        turn_s = float(msg.get("turn_s", 2) or 2)
        delay = (((spin * (angle or 0.0)) % 360.0) / 360.0) * turn_s
    else:
        delay = dist / speed if speed > 0 else 0.0

    # A perfect cast reaches everything at full strength; so does `falloff: none`.
    if intensity < 1.0 and msg.get("falloff") != "none":
        rng = float(msg.get("range_m", 60) or 60)
        intensity *= 1.0 - 0.6 * min(1.0, dist / rng if rng else 0.0)

    return delay, clamp01(intensity)


# ---- effects ------------------------------------------------------------------

class Fx:
    """One running effect."""

    def __init__(self, kind, color, intensity, start, duration, heading=0.0):
        self.kind = kind
        self.color = color
        self.intensity = intensity
        self.start = start
        self.duration = duration if duration > 0 else 0.0
        self.heading = heading

    def progress(self, now):
        if self.duration <= 0.0:
            return 1.0
        return clamp01((now - self.start) / self.duration)

    def done(self, now):
        return self.progress(now) >= 1.0


class Engine:
    """What the panel is showing, and what it should show next.

    Two layers sit under the running effects:

      `fill`  - the standing colour a completed wipe leaves behind. It does not decay,
                because a fill that quietly faded would be a light that turns itself
                off in the middle of the night.
      `glow`  - what `charge` accumulates, which does decay, because the contract says
                charge "bleeds off".
    """

    def __init__(self, steps=STEPS, north=0.0, brightness=1.0):
        self.steps = steps
        self.north = float(north)
        self.brightness = brightness
        self.fill = (0.0, 0.0, 0.0)
        self.glow = (0.0, 0.0, 0.0)
        self.glow_alpha = 0.0
        self._glow_at = 0.0
        self.curve = CURVE
        self.hue_spread = HUE_SPREAD
        self.texture = TEXTURE
        self.texture_hue = TEXTURE_HUE
        # Per texture band: how much of `band_color` it carries, which is how a
        # regional effect says "these patches, not those".
        self.band_mix = [0.0] * LEVELS
        self.band_color = WHITE
        self._band_at = 0.0
        self._hue_tab = None       # per-position rotation coefficients, see _hue_coeffs
        self._hue_for = None
        # A zone map turns the position bucket from "how far along the fill" into
        # "which rock". Absent, `rainbow` simply runs along the fill axis instead.
        self.zones = None
        self.zone_count = 0
        self.rainbow = None        # a standing mode, not a timed effect
        self.pending = []          # (due_time, Fx) still travelling across camp
        self.active = []
        self.pos = None            # (lat, lon) from retained map/<node_id>
        self.bucket = 0            # which of the eight pixel maps is in use
        self.heading = 0.0         # panel-space heading of that map
        self.received = 0
        self.ignored = 0

    # ---- input ---------------------------------------------------------------
    def set_position(self, lat, lon):
        self.pos = (float(lat), float(lon))

    def set_zones(self, zones, count):
        """Install a zone map, or clear it with `(None, 0)`."""
        self.zones = zones
        self.zone_count = int(count)

    @property
    def wants_zones(self):
        """Whether the board should be showing the zone pixel map rather than a
        direction's. Only a standing rainbow needs it."""
        return self.rainbow is not None and self.zones is not None

    def submit(self, effect, msg, now):
        """Take one `cmd/<target>/<effect>` message. Unknown effects are ignored, which
        is what the contract asks of every responder."""
        if effect not in KNOWN:
            self.ignored += 1
            return False
        result = propagate(msg, self.pos)
        if result is None:
            self.ignored += 1
            return False
        delay, intensity = result
        self.received += 1

        color = parse_color(msg.get("color"), WHITE)
        duration = self._duration(effect, msg)
        heading = 0.0
        if effect in DIRECTIONAL:
            # Panel space: the bus speaks compass degrees, the panel knows which way
            # its own top edge faces.
            heading = (float(msg.get("heading", 0) or 0) - self.north) % 360.0
        fx = Fx(effect, color, intensity, now + delay, duration, heading)
        if delay > 0.0:
            self.pending.append((now + delay, fx))
        else:
            self._begin(fx)
        return True

    def _duration(self, effect, msg):
        if effect == "flash":
            return float(msg.get("ms", 2500) or 0) / 1000.0
        if effect == "pulse":
            return float(msg.get("seconds", 3) or 0)
        if effect == "color":
            return float(msg.get("seconds", 2.5) or 0)
        if effect in DIRECTIONAL:
            # `seconds: 0` is a legitimate instant fill - it is how you say "just be
            # this colour" without a second effect name for it.
            return float(msg.get("seconds", 1.2) or 0)
        if effect == "twinkle":
            return float(msg.get("seconds", 4) or 0)
        if effect == "rainbow":
            # Here `seconds` is the time for one full turn of the wheel, not a
            # lifetime. 0 holds it still, which is the editor's debug view.
            return float(msg.get("seconds", RAINBOW_SECONDS) or 0)
        return 0.0                                  # charge lands at once

    def _begin(self, fx):
        if fx.kind == "rainbow":
            # A standing mode rather than an effect with an end: a rock lit by a
            # rainbow should stay lit until it is told otherwise. `intensity: 0`
            # turns it off, and any fill takes the panel back.
            self.rainbow = None if fx.intensity <= 0.0 else fx
            return
        if fx.kind == "charge":
            self._decay_glow(fx.start)
            # A little of it floods the whole panel...
            self.glow = mix(self.glow, fx.color, 1.0 if self.glow_alpha <= 0 else 0.5)
            self.glow_alpha = clamp01(self.glow_alpha + fx.intensity * 0.45)
            self._glow_at = fx.start
            # ...and the rest grows out of the noise peaks. The top bands are the
            # smallest, most isolated patches, so a light charge freckles the panel and
            # a heavy one floods outward from those same specks.
            self._decay_bands(fx.start)
            self.band_color = mix(self.band_color, fx.color,
                                  1.0 if max(self.band_mix) <= 0.0 else 0.5)
            reach = fx.intensity
            last = float(max(1, LEVELS - 1))
            for i in range(LEVELS):
                t = i / last
                # `reach` slides a soft threshold down the bands: a quarter-charge
                # only catches the peaks, a full one reaches the broad areas too.
                w = clamp01((t - (1.0 - reach)) / REGION_SPREAD + 1.0)
                self.band_mix[i] = clamp01(self.band_mix[i] + w * fx.intensity * 0.85)
            self._band_at = fx.start
            return
        if fx.kind in DIRECTIONAL:
            # A fill and a rainbow cannot both own what the buckets mean.
            self.rainbow = None
            # One pixel map at a time, so a new fill from a different direction
            # finishes the old one rather than fighting it for the ramp.
            for other in self.active:
                if other.kind == "wipe":
                    self._commit(other)
            self.active = [f for f in self.active if f.kind not in DIRECTIONAL]
            self.bucket, self.heading = snap_heading(fx.heading)
            fx.heading = self.heading
        self.active.append(fx)

    def _commit(self, fx):
        """A finished wipe is a fill: it stays."""
        if fx.kind == "wipe":
            self.fill = mix(self.fill, fx.color, fx.intensity)

    def _decay_bands(self, now):
        if max(self.band_mix) <= 0.0:
            self._band_at = now
            return
        dt = now - self._band_at
        if dt > 0:
            k = 0.5 ** (dt / GLOW_HALFLIFE_S)
            self.band_mix = [0.0 if v * k < 0.004 else v * k for v in self.band_mix]
        self._band_at = now

    def _twinkle_gain(self, level, now):
        """How much brighter this band is right now because something is twinkling.

        Only the top bands sparkle - they are the small isolated peaks - and each gets
        its own phase, or the whole panel would blink in unison like a fault light.
        """
        if level < LEVELS - TWINKLE_BANDS:
            return 0.0
        # The highest band is the smallest, most isolated set of patches, so it carries
        # the sparkle; the ones below it only glint.
        weight = (level - (LEVELS - TWINKLE_BANDS) + 1) / float(TWINKLE_BANDS)
        gain = 0.0
        for fx in self.active:
            if fx.kind != "twinkle":
                continue
            envelope = sin(pi * fx.progress(now))          # fade in, fade out
            phase = (now * TWINKLE_RATE + level * 0.37) % 1.0
            spark = 1.0 - abs(phase - 0.5) * 6.0           # a short spike, not a square
            if spark > 0.0:
                gain += envelope * spark * fx.intensity * TWINKLE_GAIN * weight
        return gain

    def _decay_glow(self, now):
        if self.glow_alpha <= 0.0:
            return
        dt = now - self._glow_at
        if dt > 0:
            self.glow_alpha *= 0.5 ** (dt / GLOW_HALFLIFE_S)
            if self.glow_alpha < 0.004:
                self.glow_alpha = 0.0
        self._glow_at = now

    # ---- output --------------------------------------------------------------
    def base_color(self, now):
        self._decay_glow(now)
        return mix(self.fill, self.glow, self.glow_alpha)

    def _uniform_layer(self, now):
        """Fold every uniform effect into one (colour, alpha).

        Mixing is associative in this form, so N layers collapse to one and the
        per-step loop below stays the same length whatever is running.
        """
        alpha = 0.0
        color = WHITE
        for fx in self.active:
            if fx.kind in DIRECTIONAL:
                continue
            p = fx.progress(now)
            if fx.kind == "flash":
                a = fx.intensity * (1.0 - p)                       # hit, then decay
            elif fx.kind == "pulse":
                a = fx.intensity * (0.5 - 0.5 * cos(2.0 * pi * p * 2.0))
            elif fx.kind == "color":
                a = fx.intensity * (1.0 - p)                       # tint, fading out
            else:
                continue
            if a <= 0.0:
                continue
            total = 1.0 - (1.0 - alpha) * (1.0 - a)
            if total > 0:
                color = ((color[0] * alpha * (1.0 - a) + fx.color[0] * a) / total,
                         (color[1] * alpha * (1.0 - a) + fx.color[1] * a) / total,
                         (color[2] * alpha * (1.0 - a) + fx.color[2] * a) / total)
            alpha = total
        return color, clamp01(alpha)

    def palette(self, now):
        """The `steps` colours the panel's palette should hold, nearest end of the fill
        axis first. This is the whole render: everything else is one bitmap write per
        direction change."""
        # promote anything whose propagation delay has elapsed
        if self.pending:
            still = []
            for due, fx in self.pending:
                if due <= now:
                    self._begin(fx)
                else:
                    still.append((due, fx))
            self.pending = still

        # retire what has finished, committing fills on the way out
        if self.active:
            keep = []
            for fx in self.active:
                if fx.done(now):
                    self._commit(fx)
                else:
                    keep.append(fx)
            self.active = keep

        base = self.base_color(now)
        ucolor, ualpha = self._uniform_layer(now)

        if self.rainbow is not None:
            return self._rainbow_ramp(now, base, ucolor, ualpha)

        directional = [f for f in self.active if f.kind in DIRECTIONAL]

        last = self.steps - 1
        hue_tab = self._hue_coeffs() if self.hue_spread else None
        out = []
        for i in range(self.steps):
            t = i / last if last else 0.0
            c = base
            for fx in directional:
                c = mix(c, fx.color, self._coverage(fx, t, now) * fx.intensity)
            if ualpha > 0.0:
                c = mix(c, ucolor, ualpha)
            # Hue wanders across the fill, so the front is not one flat colour. The
            # coefficients are cached per position (see _hue_coeffs), so this is nine
            # multiplies rather than a pair of trig calls.
            if self.hue_spread:
                m = hue_tab[i]
                r, g, bl = c
                c = (clamp01(r * m[0] + g * m[1] + bl * m[2]),
                     clamp01(r * m[3] + g * m[4] + bl * m[5]),
                     clamp01(r * m[6] + g * m[7] + bl * m[8]))
            out.append(c)
        return out

    def _hue_coeffs(self):
        """The hue rotation matrix for every position, worked out once.

        The angle at each position depends only on `hue_spread`, so recomputing a
        `radians`/`cos`/`sin` for all 32 of them every frame was 64 trig calls a frame
        for an answer that never changed - 7.5 ms of a 16 ms paint, measured on the
        board.
        """
        if self._hue_tab is not None and self._hue_for == (self.hue_spread, self.steps):
            return self._hue_tab
        last = self.steps - 1
        tab = []
        for i in range(self.steps):
            t = i / last if last else 0.0
            a = radians(self.hue_spread * (t - 0.5))
            cs, sn = cos(a), sin(a)
            tab.append((0.299 + 0.701 * cs + 0.168 * sn,
                        0.587 - 0.587 * cs + 0.330 * sn,
                        0.114 - 0.114 * cs - 0.497 * sn,
                        0.299 - 0.299 * cs - 0.328 * sn,
                        0.587 + 0.413 * cs + 0.035 * sn,
                        0.114 - 0.114 * cs + 0.292 * sn,
                        0.299 - 0.300 * cs + 1.250 * sn,
                        0.587 - 0.588 * cs - 1.050 * sn,
                        0.114 + 0.886 * cs - 0.203 * sn))
        self._hue_tab = tab
        self._hue_for = (self.hue_spread, self.steps)
        return tab

    def _rainbow_ramp(self, now, base, ucolor, ualpha):
        """One colour per zone, the set walking around the wheel together.

        Without a zone map the buckets still mean distance along the fill, so this
        lays the rainbow along that axis instead - which is a reasonable thing to look
        at, and means the effect works before anyone has drawn the rocks.
        """
        fx = self.rainbow
        zoned = self.zones is not None
        # With a map, bucket 0 is "no rock here" and stays dark - that is what lets the
        # editor treat unpainted pixels as unassigned. Without one, every bucket is
        # fair game and the rainbow simply lies along the fill axis.
        first = 1 if zoned else 0
        count = max(1, (self.zone_count - first) if zoned else self.steps)
        hue0, sat, val = rgb_to_hsv(fx.color)
        sat = sat if sat > 0.05 else 1.0          # white means "give me the whole wheel"
        turn = 360.0 * ((now - fx.start) / fx.duration) if fx.duration > 0 else 0.0
        out = []
        for i in range(self.steps):
            if zoned and i == PROBE_ZONE and i < self.zone_count:
                # The probe: white, so it is the one thing on the panel that is not a
                # colour belonging to a rock.
                c = mix(base, (1.0, 1.0, 1.0), fx.intensity)
            elif zoned and (i < first or i >= self.zone_count):
                c = base
            else:
                # Along a fill axis, bucket order is distance, so a smooth gradient
                # means something. Across zones it is just the order they were drawn
                # in, so a gradient says nothing and only costs you the ability to
                # tell two rocks apart - hence the golden angle instead.
                hue = (hue0 + GOLDEN_ANGLE * (i - first) + turn) if zoned else (
                    hue0 + 360.0 * ((i % count) / count) + turn)
                c = mix(base, hsv_to_rgb(hue, sat, val), fx.intensity)
            if ualpha > 0.0:
                c = mix(c, ucolor, ualpha)
            out.append(c)
        return out

    def bands(self, now):
        """Per texture band, the `(multiply, add)` that turns a ramp colour into the
        final one. Folded into six operations so the 256-entry palette stays cheap:

            mix(c, band_color, m) * tint  ==  c * ((1 - m) * tint) + band_color * m * tint
        """
        self._decay_bands(now)
        phase = now * TEXTURE_DRIFT
        col = self.band_color
        out = []
        for i in range(LEVELS):
            tint = band_tint(i, LEVELS, self.texture, self.texture_hue, phase,
                             self._twinkle_gain(i, now))
            m = self.band_mix[i]
            keep = 1.0 - m
            out.append(((tint[0] * keep, tint[1] * keep, tint[2] * keep),
                        (tint[0] * m * col[0], tint[1] * m * col[1], tint[2] * m * col[2])))
        return out

    def full_palette(self, now):
        """Every `position x texture` combination - exactly what the board's palette
        holds, indexed `position * LEVELS + texture`."""
        ramp = self.palette(now)
        bands = self.bands(now)
        out = []
        for c in ramp:
            r, g, b = c
            for mul, add in bands:
                out.append((r * mul[0] + add[0], g * mul[1] + add[1], b * mul[2] + add[2]))
        return out

    def _coverage(self, fx, t, now):
        p = fx.progress(now)
        if fx.kind == "wipe":
            # The front starts just off one edge and ends just off the other, so the
            # soft edge has somewhere to be at both ends of the travel.
            front = -EDGE + p * (1.0 + 2.0 * EDGE)
            return clamp01((front - t) / EDGE)
        # sweep: a band of colour crossing the panel and leaving nothing behind
        front = -BAND + p * (1.0 + 2.0 * BAND)
        return clamp01(1.0 - abs(t - front) / BAND)

    def rgb888(self, now):
        """The board's whole palette, ready to write."""
        return [to_rgb888(c, self.brightness) for c in self.full_palette(now)]

    @property
    def busy(self):
        if self.rainbow is not None and self.rainbow.duration > 0:
            return True                          # a turning wheel needs frames
        return bool(self.active or self.pending or max(self.band_mix) > 0.0)
