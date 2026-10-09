# staff_pi — the staff prop on a Raspberry Pi

The first Camp Bus node to run on real hardware. A Pi standing in for the eventual ESP32 staff,
publishing the exact `prop/<prop_id>` payload from [`../../docs/SCHEMA.md`](../../docs/SCHEMA.md) so the
virtual camp cannot tell it from the simulator.

It exists to do two things the simulator cannot:

1. **Prove the chain end to end** — a real device on real WiFi drives the virtual camp
   (`handbook/04-build-a-device.md` step 6, and `handbook/devices/props.md` prototyping step 1).
2. **Answer the open GPS question** in `handbook/03-state-of-play.md` — "GPS module for the props not
   chosen… the module, averaging window and antenna are not [settled]" — with measurements from a real
   PA1010D instead of a guess. That is what `--gps-test` is for.

## What is real here

| Field | Source | Status |
|---|---|---|
| the bus contract, node lifecycle, retained status + last will + counter | `campbus.Node` | ✅ real |
| `lat` / `lon` / `acc_m` | Adafruit Mini GPS PA1010D over I2C | ✅ real when it has a fix |
| `gesture`, `spin_dir`, `intensity` | BNO055 at `0x28`, via `gestures.py` | ✅ real **with `--cast`** — one gesture so far (the sweep); a keypress otherwise |
| `heading` | BNO055 fused Euler yaw | ✅ real **when the magnetometer is calibrated**, `null` otherwise |
| `via` / `rssi` | — | `via: "wifi"`; there is no ESP-NOW bridge yet, so `rssi` is omitted |

`heading: null` is still not a bug to paper over. A wrong heading is worse than none
(`handbook/devices/props.md` step 3), so a cast carries a heading only while mag calibration reads 3 and
publishes `null` the rest of the time (`--heading-min-cal`, 0 to always send it). Everything downstream
already handles `null`; nothing downstream can tell that a heading is a lie.

## What is on this Pi's I2C bus

Scanned 2026-09-22. **There is no BME680 or BME280** — nothing answers at `0x76`/`0x77`.

| Addr | Device | Identified by |
|---|---|---|
| `0x10` | Adafruit Mini GPS PA1010D | streams NMEA; answers `$PMTK011,MTKGPS` on boot |
| `0x28` | **Bosch BNO055 9-DOF IMU** | chip id `0xA0`, accel `0xFB`, mag `0x32`, gyro `0x0F` |
| `0x1a` | WM8960 codec on the Audio HAT (2026-10-04) | bound by the `wm8960` driver, so `i2cdetect` shows `UU` |
| `0x36` | Adafruit I2C QT Rotary Encoder, the spell knob (2026-10-04) | seesaw hw id; not yet seen answering, see below |

**The bus went silent for a while on 2026-10-04** (00:16 to 00:31: empty scan, codec failed to probe).
After Ben's reboot at 00:31 the GPS, IMU and codec all answer again. The knob was unplugged at the time,
so `0x36` has still not been seen. The episode and what to check if it recurs are in
`projects/magic-staff/HARDWARE.md` ("i2c-1 went silent on 2026-10-04"). The node runs without any of these
parts, says which ones are missing, and keeps looking for the sound card and the knob.

The BNO055 is the part that unblocked the staff: it runs its own sensor-fusion engine and reports
**absolute orientation** (Euler heading, 0-360 degrees) and a **gravity vector** without any maths on
the Pi. It now drives both the GPS motion gate and gesture casting.

## GPS: measured, not guessed (2026-09-22)

The numbers `handbook/03-state-of-play.md` was asking for, from `--gps-test`:

| | |
|---|---|
| TTFF | **1.3-6.5 s** warm (the module keeps its almanac) |
| Fix rate | **1 Hz**, 25 fixes in 25 s — every GGA caught |
| Best seen | 9 satellites, HDOP 0.89 -> `acc_m` 2.2 |
| Short-term scatter | **0.2 m N/S, 0.1 m E/W** (1 sigma, stationary, 25 fixes) |

**Read that scatter honestly.** It is short-term *precision* over 25 s, not absolute accuracy. Consumer
GPS is still 2-5 m absolute and drifts with multipath over minutes, which is exactly why the calibration
walk works: marks taken minutes apart share most of their error, so relative positions across camp are
better than the absolute numbers look. A 20-30 s average per mark (`handbook/devices/props.md` step 4)
is well clear of this scatter.

A cast carrying a live fix is **198 bytes** including `ts`. That was the measurement that got the payload
budget **raised from ~200 to ~320 bytes** (Ben, 2026-09-22) — at 198 there was no room left for `heading`,
and the ESP32 sketch's 512-byte buffer was never the binding constraint the old number implied.

## Layout

| File | What |
|---|---|
| `staff_node.py` | the node: gesture casting, one-shot `--fire`, an interactive caster, the diagnostics modes |
| `gestures.py` | **the cast detector** — hold still to arm, then sweep. No hardware in it, so it is unit-tested (`tests/test_gestures.py`) |
| `filters.py` | rolling median/mean on the sensor inputs, so one spike cannot make a decision |
| `bno055.py` | BNO055 I2C driver — one 32-byte block read per sample gives gyro, heading, linear acceleration and gravity |
| `pa1010d.py` | PA1010D I2C driver — enough NMEA (GGA) for `lat`/`lon`/`acc_m`, in a background thread |
| `webui.py` | the live sensor page served from the Pi (`--web`) |
| `sound.py` | plays `.wav` files through the WM8960 HAT with `aplay`, one at a time (`tests/test_sound.py`) |
| `knob.py` | the spell knob: a seesaw rotary encoder picks the cast colour and drives its NeoPixel (`tests/test_knob.py`) |

## Running it

The camp (broker, mapping engine, virtual camp) runs on the **Windows dev PC**; the Pi is only a prop.

```bash
# on the dev PC
cd apps/camp-bus && docker compose --profile sim up -d     # broker :1883, virtual camp :8080

# on the Pi
~/campbus/.venv/bin/python ~/campbus/nodes/staff_pi/staff_node.py --host <dev-pc-ip> --fire slam
~/campbus/.venv/bin/python ~/campbus/nodes/staff_pi/staff_node.py --host <dev-pc-ip>   # interactive
# cast for real: hold it still 2 s, then sweep. Watch it on http://<pi>:8081/
~/campbus/.venv/bin/python ~/campbus/nodes/staff_pi/staff_node.py --host <dev-pc-ip> --cast --web --lan
```

Interactive keys: `1` slam, `2` spin, `3` swing, `+`/`-` intensity, `c` colour, `d` spin direction,
`g` re-read the GPS line, `q` quit. Intensity `1.00` is a **perfect cast** — no distance falloff anywhere
in camp. Use `ssh -t` so keystrokes arrive one at a time; piped stdin falls back to line mode.

```bash
staff_node.py --host <pc> --track     # also publish live position on retained map/staff_01 (~1 Hz)
staff_node.py --gps-test 300          # GPS only, no broker: TTFF, scatter, averaged position
staff_node.py --dry-run --fire slam   # print the payload, publish nothing, needs nothing installed
staff_node.py --no-gps                # skip the PA1010D entirely (lat/lon go out as null)
```

### Watching the GPS on a map

`--track` publishes the live fix on retained `map/<prop_id>`, which the sim's **GPS** page
(<http://localhost:8080/gps.html>) plots: a metre grid with the trail, a 1σ scatter ellipse, the accuracy
circle, and a marker wherever a cast was thrown from. With no tty it just tracks; with one you can cast at
the same time.

```bash
ssh <user>@<pi> '~/campbus/.venv/bin/python ~/campbus/nodes/staff_pi/staff_node.py --host <pc> --track'
```

The page has two views:

- **Grid** (default) - a local metre grid around a reference fix. This is the instrument for judging the
  module: scatter in metres, drift over minutes, whether walking 10 m looks like 10 m. At camp scale a
  world map is one stationary dot, which is why this is the default rather than the map.
- **Map** - real coordinates on satellite or street tiles, for "is it actually where I am". Leaflet is
  vendored in `sim/vendor/leaflet/`, so the page still loads with no internet; only the tiles go missing.
  Zooms to **z23** (~2 m across the scale bar) with a metre scale bar, which is what a 40 m camp needs.

#### How deep the imagery really goes (probed 2026-09-22)

Esri answers every zoom with HTTP 200, so the status code tells you nothing - a ~2.5 kB tile is its
"no imagery here" placeholder, and a real one is 10-25 kB. Measured:

| Place | Real imagery to |
|---|---|
| **Virginia Key (camp)** | **z21** |
| Home test bench | z20 |

The layers set `maxNativeZoom: 20` (the lower of the two, so neither place shows grey squares) and
`maxZoom: 23`, so past z20 Leaflet **upscales** the last real tile rather than requesting one that does
not exist. Blurry beats blank when you are placing a tent within metres. The page says so in the header
(`upscaled past z20 - blur is the tiles, not the fix`) so nobody mistakes soft imagery for a bad fix.

**When the offline pack is built, cut it at z21 for camp** - that is what is actually there, and one level
better than the online layer is configured to fetch.

It also shows the distance to the camp origin, which is why bench-testing far from Virginia Key shows
nothing on the Virtual Camp map - that map is a 40 m grid around the site. Which brings us to:

### Motion gating: run the GPS only when it is moving

`--motion-gate` uses the BNO055 to decide whether the staff is moving. While it moves, the GPS tracks
normally and the fixes are kept. Once the IMU has seen nothing for `--still-seconds` (default 8), the node
**averages the fixes taken while actually still**, publishes that average once with `moving: false`, and
stops reading the GPS until something moves again.

Three reasons this is worth having, in order:

1. **It makes the position better, not just cheaper.** Averaging only means anything while stationary,
   which is exactly when this fires. A stationary GPS otherwise re-measures a position that is not
   changing and reports its own drift as movement that never happened.
2. Battery, on a prop that runs off one for four nights.
3. Less traffic on a bus that everything else shares.

**A cast thrown while standing still still carries its position.** The held average *is* the staff's
position while idle, so `lat`/`lon` never silently vanish from a cast because someone stopped walking.
That was the one trap worth designing around here.

`moving: false` on the retained `map/<id>` is what tells a consumer the difference between a held average
and a stale reading. The age of a held position is not staleness.

### The live sensor page (`--web`)

`--web 8081 --lan` serves a self-contained page from the Pi itself: <http://<pi-ip>:8081/>. (Without `--lan` it binds 127.0.0.1 only; there is no login, so use `--lan` only on the closed camp network. Settings and sound changes are POST-only.) Carry the staff,
watch it on a phone. Its **sliders take effect immediately**, which is how you find the right numbers
instead of guessing them; nothing is persisted, so what you settle on belongs in the systemd unit.

It is laid out as an instrument panel rather than a page you scroll (2026-09-23):

* **One hand is holding the staff**, so the cast loop - state, turn rate, steadiness, last verdict - is
  pinned to the top of a phone screen (about 390 px, above the fold on anything) and never scrolls away.
  Everything you only consult *between* attempts sits behind tabs underneath: Attempts, Tune, System, and Spell (the knob and sound, added 2026-10-04).
* **On anything wider than 720 px it becomes two columns, and three past 1180 px**, at full viewport
  height with nothing to scroll and the panel capped at 1400 px so readings do not end up a hand-span
  apart on a big monitor. At a workbench the whole instrument should be visible at once.
* **State is carried by brightness, not by more colours**, the way an annunciator panel does: the face
  fills with light as you hold still and goes solid when it arms. Amber means clockwise, blue means
  counter-clockwise, and red means one thing only - rejected. Nothing else gets a hue, so the hues mean
  something. It is also a dark panel because it gets read at night, outdoors, on a phone.
* **Every panel collapses** and remembers whether it was open, per browser. The tuning knobs start
  collapsed, because they are for setting up rather than for watching.
* **Every threshold can be typed as well as dragged.** The box beside each slider takes a number
  directly, clamps it to the legal range rather than silently ignoring it, and the server echo never
  overwrites a box you are typing in.

### Orientation: the staff in space

The BNO055's fused **quaternion** arrives inside the same 32-byte block read as the gyro and the gravity
vector, so full attitude costs nothing extra on a bus we are already careful with. The Orientation panel
draws the staff standing on a fixed ground grid and turns it as the real one turns.

Quaternion rather than the Euler angles sitting right next to it: no gimbal lock, no wrap at 360, and it
is what a renderer wants anyway. It is ~100 lines of canvas rather than a 3D library, because the page
has to work with no internet.

**Nobody knows which way the chip is glued inside the staff**, so the model has no way to know which of
its axes is "up" in your hand. **Zero this pose** takes the current attitude as upright: hold the staff
the way you carry it, press it once, and the model lines up with the real thing from then on (remembered
in the browser). **Back to absolute** returns to the raw sensor frame, where the grid is the world and
the staff turns against true north.

The quaternion is deliberately **not** part of the frame's corruption check. Fusion takes a moment to
converge after a restart, and a frame with a settling quaternion still has perfectly good gyro and
gravity - dropping those would stall the motion gate at startup for nothing. Consumers check its length
themselves; the page ignores a zero quaternion and keeps the last good one.

It deliberately shares the running node's sensor objects rather than opening its own. Two processes reading
this IMU over this bus is how a marginal bus becomes a broken one. Endpoints: `/` the page, `/stream` SSE at
10 Hz, `/state` one JSON snapshot (handy with `curl`), `/config?accel=&gyro=&still=` to retune live, and the
`/sound/*` endpoints below.

### Sound: the Sound panel (2026-10-04)

The staff has a **Waveshare WM8960 Audio HAT** with speakers on it. The hardware, overlay, mixer settings and
the power warning are in `projects/magic-staff/HARDWARE.md`. The page has a **Sound** panel: on a phone it
is on the fourth tab, **Spell**, under the spell-colour readout, and on a bench it is at the top of the right-hand column. It has:

* **one button per `.wav`** under `--sounds` (default `~/sounds`), found **recursively** and grouped by
  folder. To add sounds, copy files or a whole new subfolder to the Pi and press **Rescan folder**. A file
  added since the page loaded also plays without a rescan.
* **Now playing** with elapsed time, and a **Stop** button. The playing sound's button is lit.
* a **Volume** slider (and a box you can type in) driving the codec's `Speaker` control through
  `amixer -M`. That is the perceptual scale, so 50 % sounds like half rather than nearly silent. Unlike the
  threshold knobs, this one *does* persist: `alsa-restore` stores the mixer on a clean shutdown.

**Sound must never cost the sensors anything** on a single-core ARMv6, so `sound.py` keeps it out of the
way:

* Each sound is its own `aplay -q -D plughw:wm8960soundcard` process. Python never touches audio data. It
  starts the process, kills it, and checks with a non-blocking `poll()` whether it has finished.
* **A new sound stops the old one** (SIGTERM, which aplay obeys in milliseconds) before the new one starts.
  One device, one sound, and no queue to fall behind the gesture.
* Volume is hardware gain on the codec: no CPU, and no `asound.conf` softvol device to maintain. The level is
  read once at start and remembered after each change, so the 10 Hz stream never spawns a process. The
  stream carries only what is playing and the volume; the file list is fetched once.
* **No card is a state, not a crash.** On the dev PC, in the sim, or with the HAT unseated, the panel says
  why (`no ALSA card 'wm8960soundcard'`, `aplay is not installed`) and everything else carries on.
* **A late card is normal at boot.** The service starts ~60 s after power-on and the wm8960 driver probes
  ~15 s later (seen 2026-10-04: the first boot after the slim-down logged "no ALSA card" and never
  recovered). So a missing card is looked for again, every 5 s for the first 2 minutes and then every
  30 s, and when it appears the node sets the volume and plays the startup sound if it is still within
  2 minutes of start. The page fetches the sound list again when the card turns up. `After=sound.target`
  is in the unit too, but it cannot do this job alone: `After=` does not wait for a card that has not
  been found yet.
* **The volume is set on every start**: `--sound-volume 80` in the unit (Ben, 2026-10-04), on the same
  `amixer -M` scale as the slider. It is applied once the card is found, before the startup sound, so the
  first thing heard is at the set level. 80 reads back as 79 on the `-M` scale, which is rounding.

```
--sounds DIR            folder of .wav files, scanned recursively (default ~/sounds)
--sound-device ALSA     aplay -D device (default plughw:wm8960soundcard)
--sound-mixer CONTROL   what the volume slider moves; '' for none (default Speaker)
--sound-volume PCT      set the volume at start (default: leave the mixer as stored)
--sound-map SPEC        play a sound on a cast (default: none - casts are silent)
--no-sound              no player at all
```

> **Quiet overnight (2026-10-04, Ben's ask). Only the startup sound is on.** The hold-still-then-sweep
> gesture fires on its own when the staff lies on the bench, so the deployed unit runs with no cast,
> charge or ready sounds: `--sound-map` is left out (it defaults to empty) and the unit has
> `--charge-sound= --ready-sound=`. Everything below still works and is tested; it is just switched off.
> **To turn it back on**, replace those two flags in `deploy/systemd/campbus-staff.service` with the
> commented-out line kept there:
>
> ```
> --sound-map *=magic/08-teleport.wav,spin_cw=magic/panned/08-teleport_L2R.wav,spin_ccw=magic/panned/08-teleport_R2L.wav \
> --charge-sound magic/01-crystal-glow-a.wav --ready-sound magic/06-bell-chime-a.wav
> ```
>
> then `sudo cp` it to `/etc/systemd/system/`, `sudo systemctl daemon-reload`, and restart the service.
> The gesture itself is being replaced (see "Next: a twist, learned from a recorded demo"), so these
> cues will be re-hooked to the twist detector rather than polished here.

**Casts play a sound only when `--sound-map` says so.** It is empty (silent) by default, and which sound goes
with which gesture is Ben's call:

```
--sound-map 'spin=magic/08-teleport.wav'
--sound-map 'spin_cw=magic/01-crystal-glow-a.wav,spin_ccw=magic/02-crystal-glow-b.wav,slam=magic/09-power-up.wav'
--sound-map '*=magic/08-teleport.wav'            # any cast nothing more specific matched
```

`<gesture>_cw` / `_ccw` beats a plain `<gesture>` for that direction, and `*` catches the rest.

**The cast sound pans with the cast** (Ben, 2026-10-04). A clockwise cast sweeps the sound **left to right**,
a counter-clockwise one **right to left**, and a gesture with no direction plays the centred original:

```
--sound-map '*=magic/08-teleport.wav,spin_cw=magic/panned/08-teleport_L2R.wav,spin_ccw=magic/panned/08-teleport_R2L.wav'
--pan-reverse     # swaps every _cw/_ccw pair, if the sweep sounds backwards on the staff
```

The Pi Zero does not pan anything live. `tools/make_panned.sh <in.wav> [outdir]` renders both sweeps once
on a PC with ffmpeg: fold to mono, equal-power pan (`cos`/`sin` gains, so the level does not dip in the
middle), 48 kHz 16-bit stereo, and loudness matched to the input. The mono fold costs 3 dB, made back up,
so they sit at -16 LUFS like everything else, with a -1 dBFS peak ceiling. The files go to
`~/sounds/magic/panned/` on the Pi and never into git. They also appear on the page's sound list.

Teleport is short (0.8 s), but its level is nearly flat end to end (it falls only ~5 dB in the last
quarter), so the whole sweep is audible. A longer sound that decays would lose the second half of the sweep.
If it still reads as too quick on the staff, `05-crystal-glow-e` (1.8 s) is the next candidate: run the
script on it and change the map.

**The arm has three sounds of its own** (Ben, 2026-10-04):

| When | Sound | Flag |
|---|---|---|
| **Charging**: the staff starts being held still | `magic/01-crystal-glow-a.wav` (3.4 s, swells, then sustains) | `--charge-sound` |
| **Ready**: the hold completes and it arms | `magic/06-bell-chime-a.wav` (1.0 s bell) | `--ready-sound` |
| **Cast** | the panned teleport, above | `--sound-map` |

* The charge sound starts as soon as the staff is held still, after `--charge-debounce` (0.25 s). **Move it
  before `--arm-seconds` and the sound stops at once.** It starts over the next time the staff is held still.
* The debounce is the hysteresis. The detector drops out of arming on a single unsteady sample, so a staff
  hovering at the threshold flickers every frame. A flicker never lasts 0.25 s, so it never starts the
  sound, let alone restarts it over and over (`tests/test_gestures.py` checks exactly that).
* When it arms, the ready chime replaces the charge sound, since a new play stops the old one. The charge
  sound is longer than the hold on purpose: it carries through the whole 2 s and is cut by the chime
  rather than leaving a gap. `04-crystal-glow-d` rises more steeply but is only 1.4 s, so it would end
  0.35 s before the chime. Arm-seconds stays 2.0 and is not tied to the sound's length.
* The cast sound replaces whatever is playing. The charge stop is queued just ahead of it.
* All of it runs from the IMU thread without waiting. Plays and stops go onto one sound worker thread, in
  order, so "charge, then ready" can never land the other way round. `stop_if` only stops the charge
  sound if it is still the one playing, so it can never cut off a chime or a page play.
* While charging, the knob's pixel (if fitted) climbs from the resting glow to 70 % over the hold, stays
  up while armed, and hands over to the cast flash.
* **A staff lying on a table charges and chimes by itself.** It arms, times out after 8 s, re-arms, and
  goes to sleep after 25 s untouched (seen live after a restart). That is the detector working as
  designed, and it goes quiet once asleep.

**On start** the node plays `--startup-sound` (default `magic/09-power-up.wav`; `''` for none) before
anything else, before the GPS wait, so the staff announces itself as soon as the service starts.
`--fire` one-shots never play it. The sound starts *after* the cast is
published, on a throwaway thread, so the IMU loop that decided the cast never waits on aplay. It follows
every cast that goes through the node, keypress or gesture.

Endpoints, all GET and all answering with the player's state: `/sound/list`, `/sound/play?f=magic/08-teleport.wav`,
`/sound/stop`, `/sound/volume?v=0-100`. `f` must be a name the folder scan found, so a URL cannot reach a
file outside `--sounds`.

**Sounds stay on the Pi, never in git.** The repo is on GitHub and the effects are licensed to us, not ours to
redistribute.

### The spell knob (2026-10-04)

An **Adafruit I2C QT Rotary Encoder** (seesaw at `0x36`, push button, one NeoPixel) is the staff's colour
picker. Hardware notes are in `projects/magic-staff/HARDWARE.md`.

* **Turn it and the hue changes** (`--knob-step`, 15° per click). That hue is the spell colour, and **every
  cast carries it as `color`**. That is the contract's existing "the prop's current colour" field, so the bus
  sees nothing new and the virtual camp paints in whatever colour the knob shows. Without a knob, casts use
  `--color` exactly as before.
* **At rest the pixel glows dim** in the hue (`--knob-rest`, 0.3 perceived). **A cast flashes it** up to
  full and back down: 0.15 s rise, 0.25 s hold, 1.1 s fall, with gamma 2.2 so the fade looks even.
* **On start it goes once round the rainbow** (`--knob-intro`, 1 s), starting and ending at the saved hue,
  then settles to the glow. With the startup sound, that is the "I am awake" signal.
* **The hue survives restarts**, saved to `--knob-state` (`~/.config/campbus/staff-knob.json`) once the
  knob has been still for 3 s. Writing on every click would wear the SD card for nothing, and the write is
  atomic, so a power cut leaves the old file. With no file the hue starts from `--color` (`#ff4000`, 15°).
* **A knob plugged in later is found.** A missing knob is looked for again on the same schedule as a late
  sound card, and it runs the rainbow when it turns up. A knob that drops out and comes back is set up
  again (a seesaw that lost power forgets its pull-up and pixel setup) and re-learns its position rather
  than jumping the hue.
* **The button previews the spell**: the cast flash plus the cast sound, published nowhere. To undo it,
  delete the `on_press=preview` argument in `open_knob()`; nothing else depends on it. It is also the
  natural home for the calibration "mark" when that walk is built.

It costs the sensors as little as it can. One thread polls at `--knob-hz` (25), reading the absolute
position (so a missed poll loses no clicks) and the button, about 2–3 ms of bus time each. It writes the
pixel only when the colour changes. A cast from the IMU thread only sets a timestamp, and the knob's thread
does the drawing. A knob that stops answering is retried once a second, not 25 times, and counted on the
page. A missing knob is logged (`[knob] unavailable: nothing answered at 0x36 ...`) and the node carries on.

The page's **Spell** tab (top of the right-hand column on a bench) mirrors the pixel: hue, colour, current
brightness, a rainbow flag while the intro runs, button state, and clicks, presses and bus errors.

**Proposal, not done (for lb-integrator / Ben):** the hue is not published as its own number. `color`
already carries it, and a `hue` field would be a SCHEMA change. If something downstream wants the raw dial
position (say, a sim knob mirror), that would be a one-line addition to the prop payload, for Ben to decide.

No auth, like everything else on this LAN.

### Setting the camp origin

**Set camp origin here** publishes retained `config/origin` from the **averaged** position of the tracked
node (not the latest fix - averaging is the entire point). Both this page and the Virtual Camp map follow
it, so the camp's metre grid gets anchored wherever you are standing. Nodes keep their metres; only the
anchor moves, so the layout is unchanged and simply lands somewhere real.

It takes two clicks on purpose: this is retained state that re-anchors camp for every page, and a
`confirm()` dialog would block the whole tab. **Reset to default** publishes an empty retained payload,
which deletes the topic so a page booting later inherits nothing stale.

### Offline tiles (not built - read this before starting)

The map needs internet today. At camp there is none, so the page already looks for a locally served pack
**first**: `sim/tiles/manifest.json`, served by the same nginx that serves the page.

```json
{ "generated": "2026-10-01",
  "layers": [ { "name": "Satellite", "url": "tiles/sat/{z}/{x}/{y}.jpg",
                "minZoom": 15, "maxZoom": 21, "attribution": "..." } ] }
```

`maxZoom` there is the pack's **native** depth; the page still lets you zoom to z23 by upscaling.

Drop that file and the tiles next to it and the map works offline with no code change. **The hard part is
not the code, it is the licence.** Bulk-downloading tiles is prohibited by the OpenStreetMap tile usage
policy, and Esri, Google and Mapbox all restrict caching their imagery. Do not point a scraper at the
layers this page uses online. The legitimate routes, roughly in order of fit:

1. **USGS / NAIP aerial imagery** - public domain, US coverage, and Virginia Key is in the US. Downloadable
   from The National Map. This is almost certainly the right answer for a satellite layer at camp.
2. **A licensed provider** - MapTiler and Mapbox sell plans that explicitly permit offline packs.
3. **Render your own from OSM data** - the data is ODbL and free to use; it is their *tile servers* that
   may not be scraped. Heavier, but unquestionably allowed.

Whichever is chosen, keep the attribution the licence requires visible on the map.

Watch it land, from the dev PC:

```bash
docker exec campbus-mosquitto mosquitto_sub -t 'prop/#' -t 'cmd/#' -t 'node/staff_01/#' -v
```

A `slam` must produce `cmd/hallway/shockwave` **and** `cmd/crystals/flash` carrying your colour and
intensity. That is the whole contract working.

## Casting: hold still, then sweep (2026-09-23)

`--cast` turns real motion into real casts. The gesture is deliberately two-part:

    hold the staff as still as you can for ~2 s   ->  ARMED
    then sweep it, clockwise or counter-clockwise ->  CAST

**The stillness is what makes the rest possible.** A staff being carried across camp produces
accelerations and rotations all night that look like gestures; without a deliberate quiet moment in
front of every cast, telling "I meant that" from "I walked" needs a classifier that reads intent, which
we do not have and do not need. Two seconds of stillness is cheap, unambiguous and physical - and it
reads as ritual rather than as a user interface, which is the right feel for a prop handed to strangers
at 2 am.

A staff nobody has touched for `--cast-sleep-after` (25 s) goes **ASLEEP** and stops arming until
something moves it. A prop lying on the ground must not be one knock away from casting, and the faster
sampling that arming turns on has no business running all night.

### What a sweep is, in physics

Rotation about the **world vertical** - the staff turning the way a radar dish turns, at whatever angle
it happens to be held. That last part is why neither the raw gyro nor the compass will do: gyro axes are
the chip's, so "turning" reads differently depending on how someone holds the staff, and fused `heading`
is only yaw near level and wraps at 360.

So take the BNO055's gravity vector - which it computes for us - and project the gyro onto it. The
component of rotation about the gravity axis **is** the turn rate about the world vertical, at any tilt,
with no wrap. One dot product:

```
cw_dps = -dot(gyro, gravity) / |gravity|
```

The minus sign is the BNO055's gravity vector pointing *up* in the sensor frame (it is the gravity part
of the accelerometer reading, and an accelerometer at rest reads +9.8 along whichever axis points up),
and the gyro being right-handed about the same axes - so a positive rotation about "up" is
counterclockwise seen from above. Negating it makes positive mean clockwise, which is what `spin_dir: 1`
means in SCHEMA.md.

### What it publishes, and the one contract question

`gesture: "spin"` with `spin_dir` +1 cw / -1 ccw, plus `intensity` and `heading`. That is the existing
contract slot for a directional rotational gesture and it is **already routed** to the spiral cast
("crystals charge sweeping around the staff in `spin_dir`"), so a sweep lights the virtual camp today
with no mapping changes.

> **Ben's call, not a code change:** "sweep" here is the name of the *motion*; `spin` is the name of the
> *cast*. If a sweep should be its own gesture (`gesture: "sweep"`) with its own effect, distinct from a
> twirl, that is a SCHEMA decision plus a mapping rule. The detector does not care which name it sends.

Rejections carry a reason, and both the page and `tests/test_gestures.py` are built around them:

| Reason | What happened |
|---|---|
| `too short` | fewer than 3 samples, or under 0.15 s |
| `not enough turn` | the total path was under `--min-deg` - the staff barely moved |
| `changed direction` | it went one way and came back: a shake, not a sweep (`--dir-purity`) |
| `did not get far enough` | mostly one way, but the net turn still fell short |
| `not a flat turn` | the rotation was mostly about a horizontal axis - a swing, not a sweep (`--axis-purity`) |

### Input filters: one spike must not make a decision

Every threshold on this node is a comparison against a single instantaneous reading -
still enough to arm, turning fast enough to be a sweep, moved enough to wake the GPS.
That makes one bad sample expensive: it restarts a two-second hold you had nearly
finished, or starts a capture you never began. So the readings go through a rolling
window before anything compares them to anything.

**The default is a median, not a mean, and that is the point.** "Average the last few
frames" is the obvious fix and the weaker one: a mean *spreads* a spike instead of
removing it. One 9-unit blip in a window of three still lifts the output to 3.7, which
is plenty to cross a threshold. A median of three discards it completely, because an
isolated outlier can never be the middle value. Same lag, same cost, and it is
measured in `tests/test_gestures.py`:

| Input `1, 1, 9, 1, 1` | Output |
|---|---|
| median, window 3 | `1, 1, 1, 1, 1` |
| mean, window 3 | `1, 1, 3.7, 3.7, 3.7` |

`mean` is offered anyway, because it is the better choice if a reading is ever *noisy*
rather than *spiky* - sitting on a vibration instead of jumping. `off` is offered
because a filter you cannot switch off is a filter you cannot debug.

```
--filter-mode median      # off | mean | median
--accel-filter 3          # frames; 1 is off
--gyro-filter 3           # also filters the turn rate, which is derived from the gyro
```

Two things worth knowing:

* **The window counts frames, not time**, and the node deliberately samples faster
  while armed (`--cast-hz`) than at rest (`--imu-hz`). Three frames is about 300 ms of
  holding still but 100 ms of sweeping. That is the right way round - patient while
  waiting, quick while reading - but it explains a number that looks wrong.
* **A median trims the peak of a fast gesture, and `intensity` is scaled by the peak.**
  The *net* rotation a cast's direction and size come from survives a symmetric filter
  almost untouched; the peak does not. If casts start reading gentler than they feel,
  narrow the window - that is the filter working, not the detector drifting.

The gyro and accelerometer are filtered as **vectors**, not as magnitudes, because the
turn rate is a projection of the gyro onto gravity and needs the vector. Filtering once
in the driver means the motion gate, the cast detector and the page all read the same
smoothed sample instead of disagreeing about what the staff just did. The page shows
the raw value beside the filtered one and draws both on the chart, so you can watch a
spike get eaten rather than take it on trust.

### Sampling only as fast as the moment needs

Motion gating is happy at 10 Hz; gesture capture is not. So the IMU runs at `--imu-hz` (10) normally and
`--cast-hz` (50) **only while armed or capturing**, then drops straight back. The GPS shares this bus
and is what suffers if we sample fast all the time. Measured on the prototype: **8.9 Hz idle, 28.9 Hz
while armed** - the ARMv6 and the I2C bus do not deliver the full 50, which is exactly why the page
shows the *measured* rate and every attempt records the rate it was captured at.

### Tuning it

Everything is a first guess. Open the page, sweep, and read the attempt log:

* **it never arms, and the raw number is much larger than the filtered one** -> it is
  being knocked, not held wrong; widen the filter window before touching a threshold
* **it never arms** -> the steady readout is over one of the arm thresholds; raise `arm accel`/`arm gyro`
* **it arms but nothing captures** -> lower `sweep starts above` (`--trigger-dps`)
* **"not enough turn"** -> lower `sweep must cover`, or sweep further
* **"not a flat turn"** -> you are swinging, not turning; or lower `flatness`
* **it fires when you did not mean it** -> tighten the arm thresholds first, not the sweep ones

Then **write the numbers into `deploy/systemd/campbus-staff.service`**. Nothing the page changes
survives a restart, on purpose: the page is for finding numbers, the unit is for keeping them.

## Pi setup (done once)

Notes from the bring-up on 2026-09-22. The box is a **Pi Zero W Rev 1.1** (ARMv6, 427 MB) — *not* a
Zero 2 W, which matters if anything here ever needs real CPU.

```bash
sudo apt-get install -y python3-paho-mqtt i2c-tools     # i2c-tools for i2cdetect; smbus2 ships already
sudo raspi-config nonint do_i2c 0                       # I2C was OFF out of the box
python3 -m venv --system-site-packages ~/campbus/.venv  # --system-site-packages picks up smbus2
~/campbus/.venv/bin/pip install 'paho-mqtt>=2.0' -e ~/campbus/clients/python
```

**Bookworm ships paho-mqtt 1.6.1 and `campbus` needs ≥ 2.0** (it builds its client with
`CallbackAPIVersion.VERSION2`). The apt package is not enough; the venv is not optional. Installing
paho from pip on ARMv6 takes several minutes — it is not hung.

**Enable I2C and then reboot, even though it looks like you do not have to.**
`raspi-config nonint do_i2c 0` brings `/dev/i2c-1` up immediately and `raspi-gpio get 2,3` reports the
pins correctly muxed (`func=SDA1` / `func=SCL1`, both `level=1`) — and yet `i2cdetect` stayed **completely
empty**, at every address, on both buses. After a reboot the same scan found the GPS at `0x10` and the IMU
at `0x28` with no wiring touched. So on this Pi a live-enabled bus looks healthy and finds nothing; do not
spend an hour re-seating cables as we did.

Reading the pins is still worth it once you have rebooted: a scan that is empty while both lines idle high
means nothing is answering (wiring or power), and a line stuck at `level=0` is the opposite problem — a
hung device holding the bus down.

### Updating the code on the Pi

The Pi holds a plain copy of this folder in `~/campbus/nodes/staff_pi/` (no git there). Copy the files over and
restart the unit:

```bash
cd apps/camp-bus/nodes/staff_pi
scp *.py <user>@<pi-ip>:campbus/nodes/staff_pi/
ssh <user>@<pi-ip> 'sudo systemctl restart campbus-staff; journalctl -u campbus-staff -n 20 --no-pager'
```

Compare `md5sum *.py` on both sides first if anyone might have edited on the Pi. The unit file in
`deploy/systemd/` is installed separately (`sudo cp` + `daemon-reload`), so the flags in it change only when
you copy it.

## Next: a twist, learned from a recorded demo (planned 2026-10-04, not built)

**Why.** Ben's verdict after carrying it: hold-still-then-sweep is unreliable, does not feel right, and fires
on its own when the staff lies on the bench. He wants a **twist**: the staff rotated about its own long axis,
like turning a key. He will demonstrate it, so the detector should be **fitted to his recordings, not
designed from a guess**.

**1. A "Record demo" button on the page** (a new pane in the Tune tab):

* **Record** opens a capture window (default 3 s; it can be stopped early). A 3-2-1 countdown and a beep
  through the HAT mark the start, so the recording does not open with the hand reaching for the phone.
* While it records, the IMU runs at `--cast-hz` (50) whatever the detector state, then drops back, the same
  as arming does today. The GPS shares the bus, so nothing samples fast for long.
* Each take gets a **label** chosen before recording (`twist_cw`, `twist_ccw`, `not_a_twist`, or free text)
  and a short note. **Negatives matter as much as positives.** Walking, setting it down, tapping it on the
  ground and a sweep are all needed, or the detector learns "any motion".
* Takes are listed on the page with a mini plot of the twist-axis rate, and can be deleted. The page reads
  nothing from the bus. It is local, like the rest of the tuning page.

**2. What a take contains.** Every field the BNO055 block read already gives, per sample:

| Field | Why |
|---|---|
| `t` (s, monotonic, from the first sample) | timing; the rate is not constant on this Pi |
| `gyro` x,y,z (deg/s), **raw and filtered** | a twist is rotation about the staff's own axis, so this is the signal |
| `accel` x,y,z linear (m/s²) | rules out swings and knocks |
| `gravity` x,y,z | the staff's tilt, needed to tell "about its own axis" from "about vertical" |
| `quat` w,x,y,z | full attitude; the twist angle can be integrated cleanly from it |
| `heading`, calibration bytes | context only |

Plus a header: label, note, date, `--cast-hz`, the measured rate, the filter settings, node version, and
the **staff-axis calibration** (below).

**3. Format and storage.** One **JSON Lines** file per take: a header line, then one line per sample. That is
plain text, appendable while recording (so a crash loses only the tail), and readable by Python, a
notebook, or `jq`. At 50 Hz for 3 s that is about 150 lines and about 30 kB.
`~/staff-demos/YYYY-MM-DD/HHMMSS_<label>.jsonl` on the Pi, **not in git** by default (the data is
harmless, but it piles up). `GET /demos` lists the takes, and `GET /demos/<file>` downloads one, so they
can be pulled to the PC with `scp` or from the browser.

**4. The staff's long axis.** Which chip axis runs along the staff is not known (the README's "nobody knows
how the chip is glued in"). The first recording mode is **Calibrate axis**: hold the staff upright and
still for 2 s. The gravity vector is then the long axis in sensor coordinates, and it is saved to
`~/.config/campbus/staff-axis.json`. Every twist computation projects the gyro onto that axis, so it does
not matter how the chip sits.

**5. The detector, fitted from the takes.** Plan to start simple and only go further if the data says so:

1. **Features per take:** the rate about the staff axis `w_axis = gyro · axis`, and its integral (the twist
   angle); the peak `|w_axis|`; the share of rotation that is about the axis
   (`|w_axis| / |gyro|`, like `axis_purity` today); duration; and linear acceleration during it.
2. **Threshold fit first.** A twist is likely "more than ~X degrees about the axis, within ~Y s, with most of
   the rotation about the axis and little linear acceleration". Fit X, Y and the purity cut-off from the
   labelled takes (each positive above, each negative below, with margin), and report the margin. If the
   classes separate cleanly, stop there: it is cheap, explainable, and tunable from the page like the
   current knobs.
3. **Template match if they do not separate.** Resample each positive's `w_axis` trace to a fixed length
   and average them into a template. Match the live window by normalised correlation (or DTW if speed
   varies a lot), and fit the score threshold on the negatives. That is still a few hundred
   multiply-adds per sample, fine for the Zero.
4. **Direction** = the sign of the integrated twist, which maps onto `spin_dir` (and the panned cast sound)
   exactly as the sweep does now.
5. **Arming.** Whether a twist still needs the hold-still arm is a question the data can answer. If the
   twist alone separates from carrying, drop the arm, which also cures the bench self-casting. If not, keep
   a shorter arm.
6. **Tests:** the recorded takes, copied into the repo as small fixtures (they are our own sensor data,
   not licensed media), become the unit tests: every positive casts, no negative does, and the direction
   is right. That locks the detector to Ben's real motion.

What it publishes is unchanged unless Ben decides otherwise. A twist could stay `gesture: "spin"` with
`spin_dir`, or become a new gesture name. A new name is a SCHEMA change, so it is Ben's call (with
lb-integrator).

## Gotchas already paid for

- **The interaction counter needs a moment to leave.** `campbus` rate-limits `node/<id>/count` to 1/s and
  leaves the flush to its heartbeat thread, so a one-shot that exits immediately loses the bump. `--fire`
  sleeps 1.3 s for exactly this reason — do not "optimise" it away.
- **No RTC.** The Pi boots at epoch-ish until NTP catches up, so early `ts` values are nonsense (`who`
  showing 1970 logins is this). At camp there is no internet: the booth Pi is the chrony source for the
  LAN (`deploy/README.md`), which is what keeps `ts` honest.
- **Payload budget.** A cast with a live fix measures **198 bytes** including the `ts` `campbus` adds,
  against the ceiling that is now **~320 bytes** (raised 2026-09-22 on the strength of this measurement).
  `heading` costs ~14 more and now fits. `rssi` is still omitted because no bridge stamps it yet; when the
  ESP-NOW bridge lands, re-measure rather than assuming.
- **Never `modprobe -r i2c_bcm2835` on this Pi.** `dtoverlay=vc4-kms-v3d` (HDMI) holds the same I2C
  adapter, so the removal wedges in uninterruptible D state, the module refcount goes to `-1`, every
  `/dev/i2c-*` disappears and reloading fails with `Device or resource busy`. A D-state process cannot be
  killed — **only a reboot fixes it** (cost us one on 2026-09-22 while trying to rescan at a lower clock).
  To change the bus speed, set `dtparam=i2c_arm_baudrate=10000` in `/boot/firmware/config.txt` and reboot.
- **A stale fix is a lie.** `--fix-max-age` (default 5 s) drops a fix that has gone quiet rather than
  casting from where the staff used to be.
- **`acc_m` is derived, not measured**: `hdop * 2.5` (the usual consumer-GPS UERE). Treat it as a
  reasonable label, not truth.
- **The PA1010D pads with `0x0a` mid-sentence**, so splitting its stream on newlines shreds good
  sentences into fragments that fail their checksum — ~20% of them, which reads like bad wiring and is
  not. The driver strips the padding and matches whole `$...*XX` sentences instead (1 bad checksum in 182,
  down from 64). It also **drains** the buffer each poll rather than reading one 32-byte block: a single
  block per 100 ms is slower than the module emits, its FIFO wraps, and you silently lose half the fixes.

## Still open

- **Only one gesture is real.** A sweep casts; `slam` and `swing` are still keypresses. Classifying them
  from accel/gyro is the genuinely unbuilt part - a slam is a linear-acceleration spike, a swing is
  rotation about a horizontal axis, and both want the same arm-then-act structure the sweep now has.
- **Is a sweep its own gesture?** It currently publishes `spin`. See the casting section - Ben's call.
- **Which sound goes with which cast**, and whether a staff makes sound at all during quiet hours. Deployed
  2026-10-04: only the startup sound (`09-power-up`) is on. The cast, charge and ready picks are switched
  off in the unit (see "Quiet overnight") until the twist gesture replaces the sweep. The choice is Ben's.
- **The pan direction is a reading of Ben's words**: clockwise = left to right. `--pan-reverse` flips it.
- **The knob's direction is unverified.** Clockwise should step the hue forwards (red, then yellow, then
  green). If it runs backwards, add `--knob-reverse` to the unit.
- **The knob has not yet run on the real part.** It was unplugged during the 2026-10-04 work, and the bus
  outage that night is unexplained (see "What is on this Pi's I2C bus").
- **The CW/CCW sign has not been confirmed on the hardware.** The reasoning and the unit tests agree, and
  the live gravity vector reads +9.80 m/s^2 with a positive z on a desk, which supports it - but turning
  the staff clockwise and watching the page's yaw bar go right is a two-second check that settles it.
- **No BME680/BME280 on this Pi** despite the parts list saying so - so the environment-topic question is
  moot here until such a sensor actually appears on a bus.
- **Calibration walk** (`cal/start` -> `cal/target` -> `cal/mark`) is designed in SCHEMA.md and has never
  run. It wants a physical button on the staff.
- **The BME680 / BME280 on this Pi have no home on the bus.** There is no environment topic in SCHEMA.md
  and this project does not invent topics - it is Ben's contract. Proposal, not code, until he says so.

