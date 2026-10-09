"""Matrix Portal M4 as a camp bus output device.

Drop this on CIRCUITPY as `code.py`, next to `effects.py`, `campbus_mini.py` and a
`settings.toml`. It subscribes to `cmd/matrix/#` and `cmd/all/#` and paints an RGB
matrix panel: a colour, or a fill arriving from any of eight directions.

Everything that decides *what colour* lives in `effects.py`, which has no
CircuitPython in it and is unit-tested on a laptop. This file is the wiring: panel,
WiFi, MQTT socket, and a loop that copies one 32-entry palette onto the hardware.

WHY A PALETTE AND NOT PIXELS
----------------------------
A 64x32 panel is 2048 pixels and CircuitPython cannot write all of them every frame
and still answer MQTT. So each pixel is assigned, once, **two** buckets packed into
one byte - how far along the fill it sits, and which band of the Perlin texture it is
in - and the animation is done by rewriting the 256-colour **palette**:

    index = position_bucket * LEVELS + texture_bucket

256 is the most an 8-bit bitmap index can address, so carrying the texture costs
nothing extra. The eight position maps are built one at a time while the panel is
idle; the noise field is built once at boot and never again, because it does not
depend on direction - the texture *moves* by re-tinting bands, not by moving pixels.

TARGET NAME: `matrix` is **not yet in docs/SCHEMA.md**. The topic shape is the
contract's own (`cmd/<target>/<effect>`) and the effects are all existing ones, but
the target is Ben's to approve - see the README.
"""
import gc
import time

import board
import displayio
from digitalio import DigitalInOut

import effects
from campbus_mini import Node

# ---- settings ----------------------------------------------------------------
try:
    from os import getenv
except ImportError:                                   # very old CircuitPython
    getenv = lambda k, d=None: d                      # noqa: E731


def setting(key, default):
    value = getenv(key)
    if value is None:
        return default
    if isinstance(default, bool):
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return value


NODE_ID = setting("NODE_ID", "matrix_01")
TARGET = setting("TARGET", "matrix")
HOST = setting("CAMPBUS_HOST", "campbus.local")
PORT = setting("CAMPBUS_PORT", 1883)
WIDTH = setting("PANEL_WIDTH", 64)
HEIGHT = setting("PANEL_HEIGHT", 32)
# Higher looks better and costs RAM plus refresh rate. Drop to 4 on a MemoryError.
BIT_DEPTH = setting("PANEL_BIT_DEPTH", 5)
# Compass bearing of the panel's own top edge. 0 means headings are read in panel
# space, which is what you want on a bench; set it once the panel is mounted.
NORTH = setting("PANEL_NORTH", 0.0)
# How curved the fill front is, in panel radii. 0 makes it an expanding ring with no
# direction at all; large flattens it back into a straight wipe.
CURVE = setting("PANEL_CURVE", effects.CURVE)
TEXTURE = setting("PANEL_TEXTURE", effects.TEXTURE)      # 0 switches the noise off
HUE_SPREAD = setting("PANEL_HUE_SPREAD", effects.HUE_SPREAD)
BRIGHTNESS = setting("BRIGHTNESS", 100) / 100.0
SSID = setting("WIFI_SSID", "")
PASSWORD = setting("WIFI_PASSWORD", "")
if not SSID:
    # Every older Adafruit project on this board already keeps its WiFi details in
    # secrets.py. Reading them from there means the password does not have to be
    # copied into a second file to get this running.
    try:
        from secrets import secrets as _secrets

        SSID = _secrets.get("ssid", "")
        PASSWORD = _secrets.get("password", "")
        print("[wifi] using the network from secrets.py")
    except (ImportError, AttributeError, KeyError):
        pass
FW = "matrix-portal-0.1"

STEPS = effects.STEPS
LEVELS = effects.LEVELS
PALETTE = STEPS * LEVELS          # 256: the most an 8-bit bitmap index can reach
BUCKETS = 8

print("[matrix] %s -> %s:%s, %dx%d, target %s" % (NODE_ID, HOST, PORT, WIDTH, HEIGHT, TARGET))


# ---- the panel ----------------------------------------------------------------
# Driven through the core `rgbmatrix` module rather than the adafruit_matrixportal
# helper: the helper adds a library to keep in step with the bundle and gives nothing
# back here, because all it does for a bare panel is pick these same pins.
import framebufferio  # noqa: E402
import rgbmatrix  # noqa: E402

displayio.release_displays()

# One address line per doubling of the scan rate: 16 rows needs A-C, 32 needs A-D,
# 64 needs A-E. Getting this wrong shows up as the panel drawing half an image twice.
_ADDR = [board.MTX_ADDRA, board.MTX_ADDRB, board.MTX_ADDRC,
         board.MTX_ADDRD, board.MTX_ADDRE]
_lines = 3
while (1 << _lines) * 2 < HEIGHT:
    _lines += 1

matrix = rgbmatrix.RGBMatrix(
    width=WIDTH, height=HEIGHT, bit_depth=BIT_DEPTH,
    rgb_pins=[board.MTX_R1, board.MTX_G1, board.MTX_B1,
              board.MTX_R2, board.MTX_G2, board.MTX_B2],
    addr_pins=_ADDR[:_lines],
    clock_pin=board.MTX_CLK, latch_pin=board.MTX_LAT,
    output_enable_pin=board.MTX_OE)
# auto_refresh off, and refreshed once at the end of a frame instead. With it on,
# each of the 256 palette writes can drag a refresh behind it, and a palette write
# measured ~0.9 ms - most of a 230 ms frame. The matrix hardware scans continuously
# either way; this only controls when displayio pushes new data at it.
display = framebufferio.FramebufferDisplay(matrix, auto_refresh=False)

palette = displayio.Palette(PALETTE)
for i in range(PALETTE):
    palette[i] = 0x000000

# The texture, built once. It is the slowest thing at boot (two passes of Perlin over
# every pixel, to equalise the bands), so it happens before the panel has anything to
# show and never again.
print("[matrix] building the texture...")
NOISE = effects.noise_field(WIDTH, HEIGHT, LEVELS)
print("[matrix] texture ready")

bitmaps = {}


def build_bitmap(bucket):
    """One pixel map: position bucket and texture band packed into each pixel."""
    prj = effects.projection(WIDTH, HEIGHT, bucket * (360.0 / BUCKETS), STEPS, CURVE)
    bmp = displayio.Bitmap(WIDTH, HEIGHT, PALETTE)
    noise = NOISE
    for i in range(len(prj)):
        bmp[i] = prj[i] * LEVELS + noise[i]
    return bmp


bitmaps[0] = build_bitmap(0)
tile = displayio.TileGrid(bitmaps[0], pixel_shader=palette)
group = displayio.Group()
group.append(tile)
try:
    display.root_group = group          # CircuitPython 9
except AttributeError:
    display.show(group)                 # CircuitPython 8
current_bucket = 0


def build_zone_bitmap():
    """The zone map, packed the same way a direction is.

    A zone map makes the position bucket mean "which rock" instead of "how far along
    the fill". Everything downstream - the palette, the texture, the frame cost - is
    identical, which is the whole reason the bucket indirection was worth having.
    """
    z = engine.zones
    bmp = displayio.Bitmap(WIDTH, HEIGHT, PALETTE)
    noise = NOISE
    for i in range(len(z)):
        bmp[i] = z[i] * LEVELS + noise[i]
    return bmp


def use_bucket(bucket):
    """Swap in the pixel map for a direction (or for the zones), building it if this
    is its first use."""
    global current_bucket
    if bucket == current_bucket:
        return
    if bucket == "zones":
        bmp = bitmaps.get("zones")
        if bmp is None:
            if engine.zones is None:
                return
            try:
                bmp = build_zone_bitmap()
                bitmaps["zones"] = bmp
            except MemoryError:
                gc.collect()
                return
        tile.bitmap = bmp
        current_bucket = bucket
        return
    bmp = bitmaps.get(bucket)
    if bmp is None:
        try:
            bmp = build_bitmap(bucket)
            bitmaps[bucket] = bmp
        except MemoryError:
            # Out of room for eight maps: reuse one and pay the rebuild each time.
            gc.collect()
            print("[matrix] low memory - keeping one pixel map instead of eight")
            for key in list(bitmaps):
                if key not in (bucket, current_bucket):
                    del bitmaps[key]
            gc.collect()
            try:
                bmp = build_bitmap(bucket)
                bitmaps[bucket] = bmp
            except MemoryError:
                return                  # keep the old direction rather than crash
    tile.bitmap = bmp
    current_bucket = bucket


# ---- the bus ------------------------------------------------------------------
def connect_wifi():
    """Matrix Portal talks to WiFi through its ESP32 co-processor, so this is not the
    native `wifi` module. Returns a socket pool for MQTT to use."""
    from adafruit_esp32spi import adafruit_esp32spi

    esp = adafruit_esp32spi.ESP_SPIcontrol(
        board.SPI(), DigitalInOut(board.ESP_CS),
        DigitalInOut(board.ESP_BUSY), DigitalInOut(board.ESP_RESET))

    tries = 0
    while not esp.is_connected:
        try:
            print("[wifi] joining %s" % SSID)
            esp.connect_AP(SSID, PASSWORD)
        except (RuntimeError, ConnectionError) as exc:
            tries += 1
            print("[wifi] %s (try %d)" % (exc, tries))
            time.sleep(min(10, 1 + tries))
    # The ESP32SPI API has moved around: `ipv4_address` and `ap_info` are the current
    # spellings (CircuitPython 10), `ip_address` bytes and `esp.rssi` the older ones.
    # Ask for what exists rather than assume a bundle version.
    ip = getattr(esp, "ipv4_address", None)
    if ip is None:
        ip = esp.ip_address
    ip = esp.pretty_ip(ip) if isinstance(ip, (bytes, bytearray)) else str(ip)
    try:
        rssi = esp.ap_info.rssi
    except (AttributeError, RuntimeError, OSError):
        rssi = getattr(esp, "rssi", "?")
    print("[wifi] %s, rssi %s" % (ip, rssi))

    try:
        import adafruit_connection_manager
        pool = adafruit_connection_manager.get_radio_socketpool(esp)
    except ImportError:                              # older bundle
        import adafruit_esp32spi.adafruit_esp32spi_socket as pool
        pool.set_interface(esp)
    return pool, ip


engine = effects.Engine(steps=STEPS, north=NORTH, brightness=BRIGHTNESS)
engine.curve = CURVE
engine.texture = TEXTURE
engine.hue_spread = HUE_SPREAD
mode_scale = 1.0


def on_message(topic, msg):
    """Everything the panel listens to. Unknown effects fall through and are ignored,
    which is what the contract asks of a responder."""
    global mode_scale, current_bucket
    if topic.startswith("cmd/"):
        effect = topic.rsplit("/", 1)[-1]
        if engine.submit(effect, msg, time.monotonic()):
            node.count()
        return
    if topic == "config/zones/" + NODE_ID:
        zones, count = effects.decode_zones(msg, WIDTH, HEIGHT)
        engine.set_zones(zones, count)
        bitmaps.pop("zones", None)               # rebuild it on next use
        if current_bucket == "zones":
            current_bucket = None
        gc.collect()
        print("[bus] zone map: %s" % ("%d zones" % count if zones else "cleared"))
        return
    if topic == "map/" + NODE_ID:
        lat, lon = msg.get("lat"), msg.get("lon")
        if lat is not None and lon is not None:
            engine.set_position(lat, lon)
            print("[bus] my position: %.6f, %.6f" % (lat, lon))
        return
    if topic == "camp/mode":
        mode = msg.get("mode")
        mode_scale = 0.3 if mode == "quiet" else 1.0
        print("[bus] camp mode %s" % mode)


pool, ip = connect_wifi()
node = Node(NODE_ID, HOST, PORT, fw=FW, socket_pool=pool, on_message=on_message)
node.ip = ip
node.subscribe("cmd/%s/#" % TARGET)
node.subscribe("cmd/all/#")
node.subscribe("map/%s" % NODE_ID)       # retained: where this panel stands in camp
node.subscribe("config/zones/%s" % NODE_ID)   # retained: which pixels light which rock
node.subscribe("camp/mode")

# ---- the loop -----------------------------------------------------------------
last_ramp = [None] * STEPS
last_bands = [None] * LEVELS
last_val = [None] * PALETTE
next_prebuild = 1
ALL_LEVELS = tuple(range(LEVELS))
frames = 0
fps_at = time.monotonic()


def _ch(v, b):
    v = int(v * b * 255.0 + 0.5)
    return 0 if v < 0 else (255 if v > 255 else v)


def paint(now, connected):
    """Rebuild only the palette entries that actually changed.

    Recomputing all 256 every frame measured **5 fps** on this board - the arithmetic,
    not the writes. But the palette is a product: an entry is `ramp[position]` combined
    with `band[texture]`. During a fill the front crosses only two or three of the 32
    positions per frame, and the texture bands hold still unless something is
    twinkling, so nearly every entry is already correct. Recomputing the handful that
    are not is the difference between a slideshow and an animation.
    """
    b = BRIGHTNESS * mode_scale
    engine.brightness = b
    ramp = engine.palette(now)
    bands = engine.bands(now)
    if not connected:
        # A panel that is not on the bus should say so from across camp, without
        # throwing away whatever it was last told to show.
        breath = 0.12 + 0.10 * (1.0 + effects.cos(now * 2.0)) * 0.5
        amber = (1.0, 0.55, 0.12)
        ramp = [effects.mix(c, amber, breath) for c in ramp]

    moved = []
    for i in range(LEVELS):
        if bands[i] != last_bands[i]:
            last_bands[i] = bands[i]
            moved.append(i)

    for pos in range(STEPS):
        c = ramp[pos]
        if c != last_ramp[pos]:
            last_ramp[pos] = c
            which = ALL_LEVELS                 # this position moved: all of its bands
        elif moved:
            which = moved                      # only the bands that moved
        else:
            continue
        r, g, bl = c
        base = pos * LEVELS
        for l in which:
            mul, add = bands[l]
            # Inlined rather than through to_rgb888(), which would allocate a tuple per
            # entry and hand the collector 256 of them a frame.
            v = ((_ch(r * mul[0] + add[0], b) << 16)
                 | (_ch(g * mul[1] + add[1], b) << 8)
                 | _ch(bl * mul[2] + add[2], b))
            if v != last_val[base + l]:
                palette[base + l] = v
                last_val[base + l] = v
    display.refresh(minimum_frames_per_second=0)


print("[matrix] running")
t_bus = t_paint = 0.0
ticks = 0
POLL_EVERY = 4
connected = False
while True:
    _t0 = time.monotonic()
    # The socket poll is the single most expensive thing in the loop, and it cannot be
    # shortened: too short and large messages die mid-read (see campbus_mini). So it is
    # amortised instead - poll every POLL_EVERY-th frame and spend the rest drawing.
    # Worst-case message latency is a few frames, which nobody can see on a light.
    ticks += 1
    if ticks % POLL_EVERY == 0 or not connected:
        connected = node.loop()
    _t1 = now = time.monotonic()
    use_bucket("zones" if engine.wants_zones else engine.bucket)
    paint(now, connected)

    frames += 1
    _t2 = time.monotonic()
    t_paint += _t2 - _t1
    t_bus += _t1 - _t0
    if now - fps_at >= 5.0:
        # Measured, not hoped for: a 256-entry palette is the whole risk of carrying
        # the texture, so the panel reports what it is actually managing.
        span = now - fps_at
        print("[matrix] %.1f fps  bus %.0f%%  paint %.0f%%  rest %.0f%%  mem %d%s"
              % (frames / span, 100 * t_bus / span, 100 * t_paint / span,
                 100 * (span - t_bus - t_paint) / span, gc.mem_free(),
                 "" if connected else "  (off the bus)"))
        frames = 0
        t_bus = t_paint = 0.0
        fps_at = now

    if engine.busy:
        time.sleep(0.005)                # as fast as it will go while something moves
    else:
        # Idle: build the next direction's pixel map so the first fill from it is
        # instant, then take it easy on the CPU.
        if engine.zones is not None and "zones" not in bitmaps:
            try:
                bitmaps["zones"] = build_zone_bitmap()
            except MemoryError:
                gc.collect()
        elif next_prebuild < BUCKETS:
            if next_prebuild not in bitmaps:
                try:
                    bitmaps[next_prebuild] = build_bitmap(next_prebuild)
                except MemoryError:
                    next_prebuild = BUCKETS     # stop trying; use_bucket() copes
                    gc.collect()
            next_prebuild += 1
        else:
            time.sleep(0.05)
