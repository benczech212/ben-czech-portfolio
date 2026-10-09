# DJ-Facade

_Part of Ben Czech's portfolio. Built with Claude Code._

Tools for the **Etherical Lotus Flower**, a lotus / flower-of-life DJ facade: a single Pixelblaze v3
driving 1,215 LEDs on a 2D map, connected to the Love Burn camp event bus.

At a glance:

- **22 vibes** (11 shared with the camp + 11 facade-only) and **16 zone-targetable effects**, across **8 zones**
  (ring, grid, two mushrooms, two leaf sides, crown, and a cloned mushroom data line).
- **OpenCV camera mapper** (`tools/pb_camera_map.py`): lights one LED at a time and records where its glow
  lands, producing the Pixelblaze's 2D pixel map. A sample run is in `maps/2026-10-06_213307/`.
- **On-device audio analysis** in the pattern: the Pixelblaze sensor board's frequency bins are split into
  bass / mids / highs / loudness, auto-gained, and used for beat detection with a tempo flywheel.
- **Measured performance work**: frame-rate benchmarks per vibe, effect and transition (`tools/pb_bench.py`,
  results in `bench/`), a refactor into small per-vibe functions, and an offline bytecode-size check
  (`tools/pb_size.py`) against the device's ~38 KB pattern limit.

Showreel videos are not included because they contain copyrighted music.

## The lesson

**The problem.** You have 1,215 LEDs behind a hand-cut wooden sculpture and one small controller (a Pixelblaze)
with a tight budget for code and frame time. A pattern only looks right if it knows where each LED physically
sits, and you want lights, sound and other camp devices to drive it live without anyone touching the device.

**The idea.** Measure instead of guessing, and keep the device's interface narrow. A camera measures where every
LED really is, so the map is data rather than hand-entered coordinates. Benchmarks and an offline size check
measure what each change costs before it ships. At runtime the PC talks to the pattern only through its
exported variables (`setVars`, never written to flash), batched over one socket, so a burst of bus messages
can't flood the device.

### Walkthrough

1. **Map with a camera, one LED at a time:** [`tools/pb_camera_map.py`](tools/pb_camera_map.py). Each LED is lit
   alone, the frame is diffed against a dark one, and the centre of the glow becomes that LED's position.
   Lighting one LED at a time costs scan time (about 8 minutes for this piece), but it works where back-lit
   glows overlap. `locate()` takes the brightness-weighted centroid of the blob around the brightest spot, and
   returns nothing when the signal is too weak:
   ```python
   d = cv2.GaussianBlur(diff, (0, 0), 3) * roi
   peak = float(d.max())
   snr = peak / max(noise, 1e-3)
   if snr < min_snr:
       return None, snr
   ```
   LEDs it can't see are filled in by `fill_gaps()`, which interpolates along the strip from the nearest found
   neighbours. Those are the red circles in [`overlay.jpg`](maps/2026-10-06_213307/overlay.jpg).
2. **Put the centre where the art's centre is.** `centered_unit_coords()` scales each axis symmetrically about a
   point you click (`center.json`), so the flower of life's centre is exactly (0.5, 0.5) and radial effects
   start from the right place.
3. **Turn wiring order into named zones:** [`tools/facade_zones.py`](tools/facade_zones.py). Each art element is
   one stretch of strip, so `auto` assigns zones from LED index ranges (`AUTO_RANGES`). `edit` lets you
   click a stretch on the scan photo to reassign it; stretches are found where consecutive LEDs jump apart
   (`strip_segments()`). Zones get names (`ring`,
   `mushroom_left`, ...) and groups (`circle`, `leaves`, ...), and that's what the bus and controller use. The
   pattern only ever sees ids.
4. **One socket, latest value wins:** [`tools/facade_bridge.py`](tools/facade_bridge.py). Callers just record
   what they want, and a single loop sends the merged batch:
   ```python
   with self.lock:
       batch, self.pending = self.pending, {}
   if batch:
       self._send({"setVars": batch})
   ```
   The class's docstring explains why: extra sockets piled up on the device and the frame rate crashed.
5. **Measure the cost before you ship.** [`tools/pb_bench.py`](tools/pb_bench.py) records the worst second of
   frame rate per vibe, effect and transition into [`bench/`](bench/). [`tools/pb_size.py`](tools/pb_size.py)
   compiles the pattern offline and fails if it's over the ~38 KB the device will load. The "Pattern
   performance notes" below are what those measurements taught.

### Try it

You can run the mapping and zoning steps on the included sample run without a Pixelblaze or camera. Work on a
copy so the tracked sample files aren't overwritten:

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cp -r maps/2026-10-06_213307 maps/try
python tools/facade_zones.py preview maps/try                     # writes maps/try/zones.jpg
python tools/pb_camera_map.py center maps/try --at 944,572        # writes maps/try/center.json
python tools/pb_camera_map.py --help                              # preview / scan / center / apply
```

Open `maps/try/zones.jpg` next to `lit.jpg`: every dot is a measured LED, coloured by zone. `scan`, `apply`,
`pb_bench.py` and the bridge need the real device. `pb_size.py` needs the compiler cached by a first deploy.

### Take it to your own project

- If a physical layout matters, measure it (camera, sensor, survey) and store it as data. Don't hand-type it.
- Give each device one narrow control surface (here, exported variables) and route every client through it.
- Coalesce bursty inputs into "latest value wins" batches over one connection to small devices.
- Benchmark the expensive paths and check hard limits (code size, memory) offline, before every deploy.
- Use names at the edges and ids inside, so the protocol and the device can change independently.

## Reference

Setup: `python -m venv .venv && .venv/Scripts/pip install -r requirements.txt` (Windows; ffmpeg on PATH for
recording and casting). The bridge also needs the `campbus` MQTT client, a separate package from the camp
event-bus project that is not included here.

| Path | What |
|---|---|
| `tools/pb_archive.py` | Snapshot the Pixelblaze: `.pbb` backup, config, playlist, map, every pattern as `.epe` + `.js` → `archive/<timestamp>_<name>/` |
| `tools/pb_camera_map.py` | Camera mapper: lights one LED at a time, records the centre of its glow → `maps/<timestamp>/` (`map.json`, `overlay.jpg`). `center` picks the map's middle, `apply` writes the map to the Pixelblaze |
| `tools/facade_zones.py` | Zones: `auto` (from wiring order), `edit` (click to fix), `preview` (`zones.jpg`), `show` (light each zone on the facade) → `maps/<run>/zones.json` |
| `patterns/facade_bus.js` | "Facade Bus" v2: 11 camp vibes + 11 facade-only vibes (`bloom`, `kaleido`, `lotus`, `spectrum`, `fireflies`, `cascade`, `mycelium`, `butterfly`, `mosaic`, `wings`, `vortex` ), per-zone vibes, audio split, 16 zone-targetable effects (11 + `halo`, `iris`, `petalfall`, `sunburst`, `sporeburst`), all driven by exported vars. Computes its mirror table and the cut-out panes itself at start-up (~3 s) |
| `tools/facade_bridge.py` | Camp-bus responder (`node_id` `facade`, target `cmd/facade/#`): MQTT → Pixelblaze `setVars`. Console :8120, controller :8121. `--deploy` installs the pattern with the zone table baked in |
| `tools/facade_control.py` | The controller page (served by the bridge on :8121): layout preview, zone picker, colours, effects, vibes, brightness/speed/split |
| `tools/facade_notes.py` | Vibes & notes page (`:8121/vibes`): play vibes/effects, comment on the current settings or any vibe/effect; comments + settings snapshots → `notes/comments.json` |
| `tools/vm_audio.py` | Voicemeeter Banana routing for the sensor board's line-in: `status`, `route` (A1 = USB speakers, A2 = rear 3.5 mm jack → Pixelblaze, app audio → A1+A2, mics kept off A2) |
| `tools/set_default_audio.py` | List / set the Windows default playback device (should be **Voicemeeter Input**) |
| `tools/pb_bench.py` | Frame-rate bench: every vibe, vibe change, effect, zone mix and auto mode; records the worst second → `bench/<timestamp>.json` |
| `tools/facade_bench_page.py` | Test bench page (`:8121/bench`): try each scenario, measured fps (first vs latest run) + live fps, notes |
| `tools/record_clip.py` | Record the facade (Elgato Virtual Camera) with the music (Voicemeeter B2: app audio, no mics) → Facebook-ready MP4 under 95 MB in `recordings/` |
| `tools/record_showreel.py` | One-take showreel: opening credits (optional logos from `SHOWREEL_LOGOS`, the piece's name, Created by Ben Czech, optional `SHOWREEL_TAGLINE`), then every vibe and every effect (~10 s each) with the music, vibe/effect name top-left and Spotify's track/artist top-right, fading at each change → MP4 under 95 MB (720p when the budget is tight). Needs the bridge running |
| `tools/spotify_control.py` | Play / pause / next for the Spotify desktop app (media commands to its window; no login) |
| `tools/facade_record_job.py` | The dashboard's **Record showreel** button: 10 s countdown, N takes back to back (fresh song each), Spotify paused after |
| `tools/now_playing.py` | Current Spotify track and artist, from its window title |
| `tools/music_cues.py` | Listens to the music (Voicemeeter B2) and fires effects on its transition points via the dashboard API: breakdown, build, drop, 8-bar phrase, standout kick, song change. Start/stop from the test bench's **Music cues** panel (`tools/facade_cues_job.py`) or run it directly (`--dry-run`, `--verbose`) |
| `tools/cast_feed.py` | Casts the live camera + music to a Chromecast (default name "Living Room TV", `--tv` to change) as HLS with live captions (vibe / latest effect / Spotify track); ~10 s behind, buffered for smooth playback. `--stop` ends it. The HLS server is LAN-visible while it runs (see Security notes) |
| `tools/pb_compile.py` | Compile with a cached copy of the Pixelblaze's compiler (fetching it every time overloaded the Pixelblaze) |
| `tools/pb_size.py` | Compile the pattern offline (no device) and print its bytecode size: it must stay under ~38 KB or the Pixelblaze won't load it. Run before every deploy |
| `tools/bus_test.py` | Publishes test events to a **home** broker (on the camp bus only the mapping engine writes `cmd/`) |

## Camera mapping

Room dark, camera fixed, whole facade in frame (Elgato Facecam = DirectShow index 2).

```
python tools/pb_camera_map.py --exposure -5 preview          # check framing
python tools/pb_camera_map.py --exposure -5 scan             # ~8 min for 1215 LEDs
python tools/pb_camera_map.py --exposure -5 scan --redo maps/<run>/scan.json   # retry misses only
python tools/pb_camera_map.py center maps/<run>              # click the flower-of-life centre
python tools/pb_camera_map.py apply maps/<run>/map.json      # saves to flash - archive first
```

Lock exposure (`--exposure`): auto-exposure in a dark room raises gain and the noise floor ~15x.
LEDs fully hidden by the wood are interpolated along the strip (red circles in `overlay.jpg`).
Map coordinates keep image orientation (y down). `apply` scales each axis symmetrically about the
point in `center.json`, so the flower-of-life centre is exactly (0.5, 0.5) - re-saving in the
Pixelblaze Mapper tab would undo that. The pattern scales x by `ASPECT` (2.0 with the centred map)
to keep rings round.

## Zones

| id | zone | LEDs |
|---|---|---|
| 0 | ring (outer band of the flower of life) | 856–1023 |
| 1 | grid (flower-of-life interior) | 744–855 |
| 2 | mushroom_left | 658–743 |
| 3 | mushroom_right | 572–657 |
| 4 | leaves_left | 0–285, 1137–1214 |
| 5 | leaves_right | 286–571, 1038–1115 |
| 6 | crown (top arch) | 1116–1136 |
| 7 | mushroom_both | 1024–1037: a **cloned data line**, each index lights a piece on both mushrooms, so it follows the left mushroom's vibe and counts as targeted when either mushroom is |

Groups: `circle` (ring+grid), `mushrooms`, `leaves` (+crown), `left`, `right`, `all`.
With audio split on (show mode): bass → mushrooms, mids → grid, beat → ring + crown, highs → leaves.

## Running on the bus

```
python tools/facade_bridge.py --host localhost --deploy      # home docker camp-bus
python tools/facade_bridge.py                                # camp: $CAMPBUS_HOST / campbus.local
python tools/facade_bridge.py --lan                          # controller reachable from phones on the LAN (no auth)
```

Controller: http://localhost:8121 · campbus console: http://localhost:8120

Routes live in the camp-bus project's `booth/mappings.yaml` (group `Facade`); the target and the facade's
`zone` field are documented in that project's `docs/SCHEMA.md` (not included here).

## Controls on the Pixelblaze itself

Facade Bus has its own controls on the Pixelblaze's pattern page (and in Firestorm), so it can be
run with no PC: **Auto Mode** + **Auto Seconds** (full auto: a new random vibe, sometimes a separate
vibe for the circle / mushrooms / leaves, a new speed and often a random effect every 5 s-2 min;
with music the change lands on the start of a bar), a **Vibe ...** button per vibe plus a **Vibe**
number read-out (controls can't show text: 0 cozy 1 party 2 chaos 3 wave 4 jumping 5 ethereal
6 snake 7 twinkle 8 heartbeat 9 aurora 10 meteor 11 bloom 12 kaleido 13 lotus 14 spectrum 15 fireflies
16 cascade 17 mycelium 18 butterfly 19 mosaic 20 wings 21 vortex), **Speed**, **Audio Split**,
**Audio Source** (left auto / middle sensor / right bus), **Sensitivity**, an **Effect Color** and
a button per effect, and read-outs for BPM, Hears Music and the bass/mids/highs/loudness gauges.
Brightness is the Pixelblaze's own slider. `--deploy` writes the controls' saved values (= the
pattern's defaults); send slider values as plain numbers (`[x]` gets saved as garbage). The bridge
re-sends only camp-bus state on reconnect, so choices made on the Pixelblaze stick, and the
controller reads settings back from the Pixelblaze.

## Audio

The Pixelblaze has a **sensor board**: the pattern splits its 32 frequency bins into bass / mids /
highs / loudness, auto-gains them (noise floor + decaying peak, like the camp analyser) and detects
beats from bass onsets. Tempo = median of the last 8 inter-beat intervals (each folded into
85-170 BPM), and the beat clock is a flywheel at that tempo nudged toward detected kicks, so
missed or extra onsets don't stutter the pulse (checked against Spotify via Voicemeeter: track
96.8 BPM by audio analysis, Pixelblaze 95.9). `audioSrc`: **auto** uses the sensor while it
hears music, else the camp bus (`audio/energy` + `audio/beat`); or force **sensor** / **bus**.
`sensorGain` (controller "Sensitivity") sets how loud the input must be to count as music.
Music drives the vibes, not just brightness: audio-time `tA` runs faster with louder music, beats
launch meteors and hop sparks, bass swells bloom/kaleido/sparks, mids light ethereal/aurora, highs
flare cozy coals and twinkles. The controller shows live meters.

## Pattern performance notes (Pixelblaze VM, 1215 px)

What costs frame rate here (measured): the VM sets up **all of a function's locals on every call**,
so one big vibe function cost ~30%; each per-pixel `if` / global write costs ~1-2 fps at 1215 px.
So: each vibe is a small function dispatched through `VIBE_FN` with the pixel passed as arguments;
`render2D` takes a per-zone fast path (`zPlain`, `zGain` decided once per frame) unless a zone is
crossfading or has effects; effect scratch values are globals. Colours stay in HSV and render with
the native `hsv()` (a scripted hsv->rgb costs ~2/3 of the frame rate). Uniform layers collapse to one
overlay per zone per frame; spatial effects mix only the strongest. Array memory is ~10k slots:
watch `mem` in the stats (out of memory shows as `vmerr` 6 and 0 fps).

Vibe changes are a bloom-out switch from the flower's centre (only pixels at the edge render both
vibes); render2D dispatches per zone mode (plain / overlay inline / change inline / one effect
inline / slow path); vibe and effect scratch values are globals; ethereal and cozy drop detail while
effects or changes run. See bench/ for the numbers - `tools/pb_bench.py` re-measures.

**Bytecode size is the other hard limit** (2026-10-07): the compiled pattern must stay under ~37.8 KB
(37,879 B loaded; 38,789 B did not - the Pixelblaze falls back to another pattern), so literal tables
are kept small (the mirror table is computed on the device over the first seconds) and
`tools/pb_size.py` is run before every deploy. Long start-up loops stop the pattern (`vmerr` 7); do
per-frame chunks from `beforeRender` instead.

## Configuration

Device addresses and personal branding come from environment variables, so nothing site-specific is
hard-coded:

| Variable | Used by | Default | Meaning |
|---|---|---|---|
| `PIXELBLAZE_IP` | `facade_bridge.py`, `facade_zones.py`, `pb_archive.py`, `pb_bench.py`, `pb_camera_map.py`, `record_clip.py` | none (required) | The Pixelblaze's address. Set it, or pass `--ip` where the tool has that flag; `pb_bench.py` needs the variable |
| `CAST_HOSTS` | `cast_feed.py` | empty | Comma-separated Chromecast IPs to try directly when mDNS discovery is flaky |
| `SHOWREEL_LOGOS` | `record_showreel.py` | empty | Logo image paths for the opening credits, separated by `os.pathsep` (`;` on Windows). Missing files are skipped; none gives text-only credits |
| `SHOWREEL_TAGLINE` | `record_showreel.py` | empty | An extra credits line under "Created by Ben Czech"; empty omits it |
| `CAMPBUS_HOST` | `facade_bridge.py` (via `campbus`) | `campbus.local` | MQTT broker; `--host` overrides it |

## Security notes

- The controller (`:8121`, `tools/facade_control.py`) binds to **127.0.0.1 by default**. `facade_bridge.py --lan`
  binds it to 0.0.0.0 so phones on the network can use it, and prints a warning: it has **no authentication**,
  so anyone on that network can drive the lights and start recordings. Use `--lan` only on a trusted network.
- The controller only accepts POSTs with `Content-Type: application/json` (other types get 415). A cross-site
  form can't send that without a CORS preflight, which the server never approves, so other web pages can't
  fire commands at it.
- The showreel `version` tag (CLI `--version` or the dashboard's record request) is checked against
  `^[A-Za-z0-9._-]{0,16}$` before use, since it reaches a file name and an ffmpeg `drawtext` filter.
- `tools/cast_feed.py` serves its HLS stream on **0.0.0.0:8130** while casting, because the Chromecast fetches
  it over the LAN. The live camera and music are readable by anyone on the network during that time.
- The campbus console (`:8120`) is served by the separate `campbus` package; its binding is set there.
