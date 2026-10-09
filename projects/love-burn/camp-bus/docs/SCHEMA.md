# Camp Bus schema (v0.2 — 2026-09-20)

The contract every publisher and subscriber follows. Owner: Ben Czech. Propose changes in `#interactive-art`; this file is canonical.

## Transport

- **MQTT 3.1.1** on the booth Pi, host `campbus.local` (static IP on the camp LAN; documented in `deploy/README.md`).
- Port **1883** plain MQTT (ESP32, Pi, PCs). Port **9001** MQTT over WebSockets (browser pages, phones).
- Auth: the booth broker requires a username + password and enforces `bus/acl.example` (only the `engine` and `booth` accounts may publish `cmd/#` or `mapping/set`). The docker-compose dev profile (`bus/mosquitto.dev.conf`) is anonymous. The camp LAN has no internet route.
- **Payloads are JSON**, UTF-8, one object per message, no arrays at the top level. Keep event payloads **under ~320 bytes** so a $3 ESP32 parses them with a fixed buffer. (**Raised from ~200 on 2026-09-22, Ben**, when a staff cast carrying a real GPS fix measured 198 bytes and adding `heading` would not fit: the reference sketch sets `mqtt.setBufferSize(512)` and that buffer holds the **whole packet** - MQTT header + topic + payload - so ~320 leaves real headroom.)
- **QoS 0** everywhere unless stated. Retained topics are marked. A lost beat event doesn't matter; a lost check-in is corrected by the retained state.
- `ts` = sender's clock in **unix milliseconds** (int). Nodes without a real clock (ESP32 with no NTP) send `0`; consumers treat `0` as "now". The booth Pi runs an NTP server on the LAN for nodes that want real time.
- Client id = `node_id` (naming in `README.md`). Connect with `clean_session=true`; you re-receive retained state on every reconnect.

## Node lifecycle (every node)

| Topic | Payload | Retained |
|---|---|---|
| `node/<id>/status` | `{"online": true, "ip": "10.42.0.31", "fw": "crystal-0.3", "uptime": 1234, "role": "responder", "ui": 8081, "ts": 0}` | yes |
| `node/<id>/count` | `{"interactions": 152, "ts": 0}` | yes |

- Set the **last will** to `node/<id>/status` = `{"online": false}` retained, so the broker marks you offline if you vanish.
- Publish `status` on connect and every **30 s** (uptime in seconds). Publish `count` whenever your counter changes (rate-limit to at most 1/s). Counters are the grant-application analytics; keep them.
- **`role`** (optional, added 2026-09-24) says which layer you are, so a fleet view can group you without a hardcoded list: `input` (toys, props, instrument sensors), `instrument`, `responder` (lights, bell, screens), `bridge` (protocol adapters), `booth` (kiosk, engine, analysis). Unknown or missing = ungrouped.
- **`ui`** (optional, added 2026-09-24) is **your console**: the address of a small web page you serve yourself with health, live traffic and whatever debug controls you have. Either a port number (`8081`, meaning `http://<your ip>:8081/`) or a full URL when you know better than the reader does (a container publishing a mapped port, a page behind a path). Omit it if you serve nothing.

**Node consoles (Ben, 2026-09-24).** Every input and output device should serve one. It is the difference between "that crystal is dark" and "that crystal is online, hearing `cmd/all/pulse`, and its LED driver is the problem", at 2 a.m., from a phone, with no laptop and no ssh. The rule of thumb: identity and health, what you are subscribed to, the last messages in and out, and any control that helps someone standing in front of the device (identify, self-test, reconnect). `campbus.console` gives Pi-class nodes the whole thing in two lines; the ESP32 template serves a cut-down version. The Devices page (`sim/nodes.html`) lists the fleet and links to each `ui`.

## Booth

| Topic | Payload | Retained | Publisher |
|---|---|---|---|
| `booth/dj` | `{"artist": "SideQuest", "slot": "2027-02-13T22:00", "since": 1770000000000, "timer_end": 1770002700000, "logo": "sidequest.png"}` | yes | check-in kiosk |
| `booth/schedule` | `{"slots": [{"artist": "...", "start": "...", "end": "..."}], "ts": 0}` | yes | check-in kiosk |
| `booth/quiet` | `{"quiet": true, "until": "2027-02-14T09:00", "ts": 0}` | yes | booth (policy TBD) |
| `camp/mode` | `{"mode": "idle", "since": 1770000000000}` | yes | booth kiosk (manual) / analysis service (auto) |
| `camp/sync` | `{"bpm_sync": true, "pulse": true}` | yes | booth. `bpm_sync` = every cycle rate follows the BPM (hops, waves, thumps, snake steps) instead of the vibe's free-running period. `pulse` = flash on beats and follow the bass; when false brightness follows a smoothed loudness (no pulsing) even while rates stay synced. Independent toggles. |
| `vibe/params` | `{"params": {"wave": {"wavelength": 40, "period": 4, "bars": 1, "floor": 0.15}, "jumping": {"sparks": 3, "hop": 0.45, "beats": 1, "trail": 2.2, "collide": 6, "hall": 0.08}, "...": {}}}` | yes | booth / any UI - live parameters for every vibe; responders merge what they understand and ignore the rest. Defaults and ranges are in `sim/camp.html` (`VIBE_DEFS`). |

**Camp mode** (added 2026-09-20, Ben): the live operating state of the whole camp. `idle` = no music playing and no BPM: the audio source publishes nothing on `audio/beat` / `audio/energy` and `audio/bpm` reports `0`; responders run their own ambient animations. `show` = normal, audio-reactive. `quiet` = quiet hours: responders dim (the simulators use 35%), bells go soft, displays mute. The analysis service may switch `idle`/`show` automatically from signal presence (~60 s of silence = idle); `quiet` comes from the booth schedule. Responders that need the mode subscribe to `camp/mode` (retained, so late joiners get it).

Empty booth: `booth/dj` = `{"artist": null, "since": ...}`. `logo` is a filename served by the booth at `http://campbus.local/logos/<file>`. Resolume clip name = the `artist` string exactly.

## Audio analysis

| Topic | Payload | Rate |
|---|---|---|
| `audio/beat` | `{"n": 48211, "bpm": 124.0, "ts": 0, "next_ms": 484}` | one per detected beat |
| `audio/energy` | `{"bass": 0.82, "mid": 0.40, "high": 0.15, "loud": 0.61, "ts": 0}` | 20 Hz |
| `audio/bpm` | `{"bpm": 124.0, "conf": 0.9, "ts": 0}` | 1 Hz, **retained** |

- `n` = running beat counter (wraps at 2^31); `n % 4 == 0` is a best-effort downbeat.
- `next_ms` = predicted ms until the next beat, so a consumer can schedule its flash instead of reacting late.
- Energies are **0.0–1.0, auto-gain normalized** over ~10 s, so a quiet set still lights things up. `loud` is overall RMS on the same normalization.
- Consumers near the ~1 s delayed entrance speakers add a configurable delay; consumers near the stage react immediately.

## Inputs (things people touch)

| Topic | Payload | Notes |
|---|---|---|
| `toy/<toy_id>` | `{"toy": "big_red_button", "event": "press", "value": 1, "ts": 0}` | `event` is one of `press, release, hold, pull, rate, level, note, mode` |
| `prop/<prop_id>` | `{"prop": "staff", "prop_id": "staff_01", "gesture": "slam", "intensity": 0.9, "color": "#ff4000", "heading": 135, "spin_dir": null, "lat": 25.73671, "lon": -80.15479, "acc_m": 3, "via": "bridge_hallway", "rssi": -52, "ts": 0}` | **every handheld prop** (staff, wand, drum, ...) publishes the same action: `prop` type + `prop_id` + **`gesture` + `intensity` 0.0–1.0 + `color`** (the prop's current colour) + **`heading`** (degrees, 0 = north, clockwise, from the IMU; `null` if unknown) + `spin_dir` (`1` cw / `-1` ccw, spins only) + `lat`/`lon`/`acc_m` from the GPS when it has a fix. Published by the ESP-NOW-to-MQTT bridge; `via` + `rssi` give crude proximity. (v0.1 used `staff/<id>` with `staff_id`; the engine still listens there during the overlap.) |
| `instrument/<instrument_id>` | `{"instrument": "mycelium", "instrument_id": "mycelium_01", "pad": "amanita", "pad_i": 0, "event": "hit", "velocity": 0.8, "note": 36, "color": "#ff3b3b", "lat": 25.73666, "lon": -80.1546, "acc_m": 2, "ts": 0}` | **fixed touch instruments** (the Sonic Mycelium Cluster's mushroom drum pads, the crystal geode synth, the Monstera bass plant, the water chord tubes): `instrument` type + `instrument_id` + `pad` (which pad, plus its index) + `event` (`hit` for now; `hold`/`release` later for capacitive pieces) + **`velocity` 0.0-1.0** + the pad's MIDI `note` and `color`, and the instrument's calibrated position (from its retained `map/<instrument_id>`) so a whack ripples out from where it stands. Published by the instrument's bridge (the Pi that hosts the e-drum brain / Ableton), one message per hit. Mushrooms of the cluster (2026-09-21): `amanita` kick 36 `#ff3b3b`, `chanterelle` snare 38 `#ffb300`, `indigo_milk` closed hat 42 `#3d7bff`, `ghost` tom 45 `#4dffa6`, `violet_coral` clap 39 `#b04dff`, `pink_oyster` ride 51 `#ff4da6`. **Capacitive instruments** (the Crystal Geode Synth, `instrument: "geode"`, pads `amethyst` 60 `#b04dff`, `citrine` 64 `#ffc83d`, `aquamarine` 67 `#4de0ff`, `rose_quartz` 71 `#ff7ab8`) use three events: `touch` (once, on contact), `hold` (~8 Hz while touching, throttle it in the mapping) and `release` (once; `velocity`/`value` 0, plus `held_ms`). They add `value` (the live normalised reading 0.0-1.0), `raw` (sensor counts) and `baseline` (the pad's untouched counts). The **Water Chord Tubes** (`instrument: "tubes"`, pads `tube_1` 48 `#4de0ff`, `tube_2` 52 `#3d9bff`, `tube_3` 55 `#7cffd4` on channel 4) are capacitive in exactly the same way and use the same three events, but the electrode is the *water*: a wire in each sealed cylinder makes the whole column live, so the hand-to-water contact area is far larger than the geodes' brass frame - the reading is bigger and steadier (the simulator's baseline is 980 counts with a 1500-count span against the geodes' 1240/900) and it settles in about a quarter of a second. **Three tubes, tuned as one chord** (Ben, 2026-09-21; the original 2026-09-14 concept said about five), with the water level differing per tube. **Piezo-tape instruments** (the Monstera Piezo Plant, `instrument: "plant"`, pads `leaf_1`..`leaf_5`, notes 36/40/43/47/50 on channel 3; five leaves from 2026-09-21, was four) read strain *rate*, so: `bend` at ~8 Hz while a leaf is being worked with the flex as `value`, `release` once when it springs back with the spike as `velocity`, and a small `hit` for a tap. Every instrument event also carries `ch` (MIDI channel) alongside `note`. **Chord mode (Ben, 2026-09-21):** an instrument may add `notes: [60, 64, 67, 71]`, every note of a chord the pad plays; `note` stays the root. The Crystal Geode Synth has a single-note / chord mode switch with chord presets (one chord per geode: C major I vi IV V, A minor, sus, open fifths, Lydian); its retained `config/midi/geode_01` then carries `mode: "chord"`, `chord_preset`, and `notes` per pad, and the bridge plays every note of `notes` (from the message, or from the config) on note on/off. **Channels are how the DAW tells instruments apart on the one `Camp Bus` port**, so each instrument must own its channel (sequential defaults: mycelium 1, geode 2, plant 3, tubes 4; changed from 10/1/2/3 on 2026-09-21); the Instruments page's MIDI table flags a shared channel or a duplicate note in red, and the bridge logs a WARNING when retained configs clash. **MIDI mapping** lives on the bus: `config/midi/<instrument_id>` (retained) = `{"instrument": "mycelium", "instrument_id": "mycelium_01", "pads": {"amanita": {"note": 36, "ch": 10}, ...}}`, written by the Instruments page's MIDI table (or by hand; it also carries `preset`, the name of the note preset the numbers came from, or `custom`); a bridge subscribes to its own and stamps those numbers on every event, and the Ableton rack follows the same table. **Bus to MIDI** (`tools/midi_bridge.py`, and the Instruments page's Web MIDI output, same table): every note on is preceded by **CC 11 (expression) = the same velocity** (`--vel-cc`, 0 disables), because whether velocity reaches volume is up to each patch; mapping CC 11 to a gain in the DAW makes "harder is louder" true for every instrument. `hit` = note on at `velocity`, note off after a 120 ms gate; `touch` = CC 74 at `value` then note on at `velocity`; `hold` / `bend` = CC 74 at `value` (rate-limited to 30/s per pad); `release` = CC 74 to 0 then note off, except plucked instruments (`plant`) where release is the note itself at the spike `velocity`, gated. Channel from `ch` (defaults mycelium 1, geode 2, plant 3, tubes 4); note from `note`; a retained `config/midi/<instrument_id>` overrides both per pad. **Velocity on a capacitive pad is not a switch** (Ben's question, 2026-09-21): a one-wire sensor reads how well the body couples to the electrode, and that differs by person and situation - body size, footwear and grounding (barefoot on damp sand reads far higher than trainers on a mat), skin moisture, how much of the hand is on the frame, and whether the person is also touching another person or a grounded object (two people holding hands read higher than either alone). So the bridge normalises each pad against its own baseline (re-learned slowly while untouched) and publishes the delta at contact as `velocity` and the delta while held as `value`; consumers get a natural "how much of you is on it" parameter for free. |
| `midi/<source>` | `{"note": 60, "vel": 100, "ch": 1, "on": true, "ts": 0}` | Playtron/pads republished by a Pi USB-MIDI host |
| `kitchen/foodcall` | `{"meal": "dinner", "soft": false, "ts": 0}` | food-call fan-out trigger |
| `dj/link` | `{"bpm": 128.0, "key": "8A", "deck": 1, "playing": true, "ts": 0}` | Pro DJ Link ingestion (later) |

Props and their gestures (v0.2). Each is one mapping rule; add a prop by adding rules.

| Prop | Gesture | Default cast |
|---|---|---|
| `staff` | `slam` | circle: hallway shockwave + crystals flash, 6 m/s, 60 m |
| `staff` | `spin` | spiral: crystals charge sweeping around the staff in `spin_dir`, one turn per 2 s; rotation only, every crystal, no falloff |
| `staff` | `swing` | cone 70°: tint everything the staff points at for 6 s, fast (14 m/s) |
| `wand` | `flick` | bolt: cone 25°, 14 m/s, 80 m, short flash |
| `wand` | `circle` | orb: circle pulse, only within 18 m of the caster |
| `whip` | `crack` | needle: cone 12°, 30 m/s, 80 m, short flash + hallway rings |
| `whip` | `lash` | arc: cone 140° within 25 m, 3 s tint + hallway sweep |
| `drum` | `hit` | heavy wave: everything pulses, 4 m/s, 90 m |
| `drum` | `roll` | steady charge on everything within 40 m |

`value` by event: `press/release/pull` = 1; `hold` = seconds held; `rate` = signed turns/sec; `level` = 0.0–1.0 (add `"i"` for slider index); `note` = velocity; `mode` = string label.

## Commands (the mapping engine's output)

| Topic | Payload |
|---|---|
| `cmd/<target>/<effect>` | effect-specific, e.g. `cmd/hallway/sweep` = `{"color": "#ff4000", "speed": 1.5, "ts": 0}` |
| `cmd/all/<effect>` | broadcast to everything that implements the effect |

- **Only the mapping engine publishes to `cmd/`.** Toys, staffs, and kiosks never do. Remapping is a YAML edit on the booth Pi (`booth/mappings.yaml`), hot-reloaded. The engine may also publish `display/*`.
- A responder subscribes to `cmd/<my_target>/#` and `cmd/all/#` and ignores effects it doesn't implement. Targets so far: `crystals` (and `crystal_<nn>` for one crystal), `hallway`, `mycelium` (the UV beds under the mushrooms: `flash`/`pulse`/`color`/`charge`, every bed at once, same propagation and falloff as a crystal), `geode` (the COB RGBW inside each geode, same effects), `plant` (the LED filaments in the leaves), `tubes` (the RGB puck under each water tube, lighting the column), `facade` (the lotus / flower-of-life DJ facade: one Pixelblaze, 1215 px on a camera-measured 2D map; `flash`/`pulse`/`breathe`/`color`/`charge`/`sweep`/`shockwave`/`rings`/`spiral`/`wipe`/`rain`/`comet`/`speed`/`mode`, plus two facade-only vibes `bloom` and `kaleido`; propagation from its own `map/facade` as a point node. **Facade extension:** any effect may carry `zone` = a zone or group name or a list of them (`ring`, `grid`, `mushroom_left`, `mushroom_right`, `leaves_left`, `leaves_right`, `crown` | `circle`, `mushrooms`, `leaves`, `left`, `right`, `all`); missing = the whole facade; `mode` with a `zone` sets that zone's vibe only. Other responders ignore the field. Added 2026-10-06, Ben), `resolume`, `kitchen`, `all`.
- **Colour mixing rule (all responders):** any effect payload may carry `color` (`#rrggbb`) and `intensity` (0.0–1.0). The responder mixes that colour into whatever it is currently showing with weight = intensity, so a gentle staff swing tints the hallway slightly toward the staff's colour and a hard slam paints it. Missing `intensity` = 1.0; missing `color` = the effect's default (white for shockwave/flash).
- Effect vocabulary (grows as responders exist; the visual simulators in `sim/camp.html` are the reference implementation):

| Effect | Payload | Meaning |
|---|---|---|
| `flash` | `{color, ms, intensity}` | hit the colour, decay over `ms` (staff slam uses 2500) |
| `pulse` | `{color, seconds, intensity}` | breathe the colour for `seconds` |
| `sweep` | `{color, speed, intensity}` | band of colour travels the length (hallway) |
| `shockwave` | `{color, intensity, from}` | expanding ring, size/brightness by intensity |
| `charge` | `{color, intensity}` | accumulate glow toward the colour; bleeds off |
| `color` | `{color, intensity, seconds}` | tint toward the colour by intensity, fading out over `seconds` (default 2.5) |
| `speed` | `{value}` | animation speed multiplier (crank) |
| `mode` | `{mode}` | palette (vibe): `cozy` / `party` / `chaos` / `wave` / `jumping` / `ethereal`. `wave` rolls a brightness wave north to south across camp (40 m wavelength, one bar per wave when BPM-synced, else 4 s) using each node's calibrated position. `jumping`: a single spark hops between nearby nodes (nearest three, never straight back), on every beat when synced else every 0.45 s, occasionally leaping through the hallway as a fast sweep from the crystal it came from; crystals show a short trail. `jumping` runs **three sparks** with their own hues; when two land on the same crystal or within 6 m they *burn* each other: both hues rotate forward on the colour wheel and pull a third of the way toward each other, and the crystal flashes white. `ethereal`: slow colour mixes from 3D Perlin noise over each node's position and time. `snake`: a chain of 7 crystals slithers to nearby unvisited crystals (head brightest, hue shifting along the body, tail fading out), leaping through the hallway when close. `twinkle`: dim base with random 0.7 s sparkles per LED (hallway: per-pixel twinkles). `heartbeat`: a lub-dub envelope on every beat when synced (else 1.2 s), the hallway lagging slightly along its length. `aurora`: green-to-purple curtains banded by northing, drifting north. `meteor`: every 2-6 s a streak crosses camp at 28-48 m/s lighting every node within 5 m and sweeping the hallway if it passes within 12 m. **Vibe and camp-mode changes crossfade over 2 s** (responders keep rendering the old one and blend). |
| `clip` | `{name, fallback}` | Resolume clip by name |
| `bell` | `{soft}` | kitchen bell, soft = quiet hours |
| `mute` | `{muted}` | displays/speakers |
| `comet` | `{color, intensity, speed_mps}` | hallway: bright head + fading tail through the tunnel away from the caster |
| `rings` | `{color, intensity, seconds, count}` | hallway: frame-by-frame chase away from the caster |
| `wipe` | `{color, intensity, seconds}` | hallway: fill from the entry face to the far end, hold, release |
| `warp` | `{color, intensity, seconds}` | hallway: stripes racing along the tunnel, accelerating |
| `spiral` | `{color, intensity, seconds, spin_dir}` | hallway: helix rotating around the axis while flowing along it |
| `rain` | `{color, intensity, seconds}` | hallway: random twinkles in the colour |
| `breathe` | `{color, intensity, seconds}` | hallway: whole tunnel breathes in the colour |
- The engine publishes `mapping/loaded` (retained) = `{"file": "mappings.yaml", "rules": 8, "names": ["..."], "ts": 0}` after every successful load, so dashboards and tests can see which rules are live.
- **Editing routes over the bus (2026-09-21):** the engine also publishes **`mapping/rules`** (retained) = `{"file", "rules": [{name, when, then, throttle_ms?, enabled?}, ...], "subscribe": [...]}`, the full live rule list. A UI (the Routes page, `sim/routes.html`) publishes the whole edited list to **`mapping/set`** = `{"rules": [...], "subscribe": [...], "by": "routes.html"}`; the engine validates every rule exactly as it validates the file (outputs only under `cmd/`/`display/`, no loops), **rewrites `mappings.yaml`** in a compact one-line-per-item style (still hand-editable; comments are not preserved), reloads, and answers with a fresh `mapping/rules`. A rejected edit is answered on **`mapping/error`** = `{"error": "...", "by"}` (not retained) and nothing changes. A rule may carry `enabled: false`: it stays in the file and the list but never fires, and an optional `group` label (e.g. `Props`, `Toys`, `Friday set`): purely for people, the Routes page lists, boxes and bulk-toggles rules by group and the writer puts a comment banner per group in the yaml.

## Positions, calibration and propagation (proposed 2026-09-20, Ben)

Goal: a staff cast ripples outward across camp, each node reacting later the farther it is from the staff. Needs every node's position and the staff's position at the moment of the cast.

| Topic | Payload | Retained |
|---|---|---|
| `map/<node_id>` | `{"lat": 25.73671, "lon": -80.15479, "acc_m": 2.5, "sats": 12, "hdop": 0.79, "ts": 0}` - extended nodes add `"corners": {"FL": {lat, lon}, "FR": ..., "BR": ..., "BL": ...}`; `sats`/`hdop` are optional GPS quality hints (added 2026-09-22) that only the GPS page reads; **`moving`** (optional bool, 2026-09-22) says whether the node was moving when this was published - `false` means it is a *held average* taken while stationary, not a stale reading, so a consumer must not treat its age as staleness | yes |
| `config/origin` | `{"lat": 25.7367, "lon": -80.1548, "acc_m": 2.1, "n": 24, "set_by": "staff_01", "ts": 0}` - **where the camp's metre grid sits in the world** (added 2026-09-22, Ben's request). Set from the GPS page's *Set camp origin here*, which uses the **averaged** position of the tracked node, not one fix. Publishing an **empty** payload clears it and every page falls back to its built-in default. Only the anchor moves: nodes keep their metres, so the camp layout is unchanged and simply lands somewhere real. | yes |
| `cal/start` | `{"nodes": ["crystal_01", "..."]}` (omit = every node with an LED) | no |
| `cal/target` | `{"node_id": "crystal_03", "i": 3, "n": 6}` | yes while calibrating |
| `cal/mark` | `{"staff_id": "staff_01", "lat": ..., "lon": ..., "acc_m": 2.1}` | no |
| `cal/done` | `{"nodes": 6, "ts": 0}` | no |

- **Fixed instruments:** a touch instrument has no GPS; its bridge subscribes to its own retained `map/<instrument_id>` (written by calibration, or by hand) and stamps that position on every hit. In the simulator the Virtual Camp map publishes `map/mycelium_01` when the cluster is dragged.
- **Staff position:** a GPS module on the staff. Every staff action carries `lat`, `lon`, `acc_m` when it has a fix (`null` otherwise; everything still works, just without propagation). A prop that *has* a GPS may also publish its **own** `map/<prop_id>` (retained, ~1 Hz) so the camp knows where it is between casts - that is what `nodes/staff_pi/staff_node.py --track` does, and what the **GPS** page plots. Nothing routes `map/`, so this cannot trigger anything; it is position, not an event.
- **Extended nodes (the space hallway):** calibration marks **four corners in order - front-left, front-right, back-right, back-left** (front = the mouth people walk in through) - so the node knows its position, size *and heading*. The hallway's map covers the frames' edges *and* the six longitudinal rails running the full length at each vertex. Every pixel then has a world position (Pixelblaze 3D map + the corner frame), and a cast with an `origin` reaches each pixel after `distance(origin, pixel) / speed_mps`: the wave **enters through whichever face is nearest the caster** (front, back, left or right) and travels away from them, with no special cases. The node's delay for scheduling is the distance to its nearest corner, and **cone and range tests use the whole footprint** (corners, centre and the centre line; the best-hit point wins), so a narrow cone aimed through the middle of the tunnel still triggers it.
- **Calibration mode:** the booth publishes `cal/start`; the mapping engine walks the node list, publishing `cal/target` and a white `cmd/<node_id>/pulse` so the current node visibly breathes. The person carries the staff to it and presses the staff button, which publishes `cal/mark` with the averaged GPS fix. The engine writes `map/<node_id>` (retained) and moves to the next node. Nodes never need GPS themselves; they subscribe to their own `map/<id>` on boot.
- **Propagation rule (all responders):** an effect payload may carry `origin: {lat, lon}`, `speed_mps` and `range_m`. A node delays the effect by `distance(origin, my map position) / speed_mps` (equirectangular distance is plenty at camp scale), and scales the intensity by `1 - 0.6 * min(1, distance / range_m)` so a cast fades gently as it travels, down to 40% at `range_m` and flat beyond (nothing is ever fully out of range). **Perfect cast exception (Ben, 2026-09-20):** `intensity >= 1.0` means no falloff, every node in camp gets it at full strength. No origin, or no known position for me, means react immediately at the given intensity. Defaults: `speed_mps` 6 (a 40 m camp ripples in ~7 s), `range_m` 60.
- **Cast shapes (Ben, 2026-09-20):** an effect payload may carry `shape` = `circle` (default), `cone` or `spiral`, plus `heading` (deg), `cone_deg` (cone width, default 70) and, for spirals, `spin_dir` (±1) and `turn_s` (seconds per full turn, default 2). A responder computes the bearing from `origin` to itself (0 = north, clockwise) and the signed angle `a = wrap(bearing - heading)`:
  - `circle`: everyone in range, as above (slam).
  - `cone`: only nodes with `|a| <= cone_deg / 2` react; intensity is further scaled by `0.3 + 0.7 * cos(90° * a / (cone_deg/2))` so the centre line is strongest (swing).
  - `spiral`: everyone reacts, but the delay gains `((spin_dir * a) mod 360) / 360 * turn_s`, so the effect sweeps around the staff like a radar in the direction of the spin (spin).
  Shape applies to perfect casts too; only the distance falloff is waived.
- **`nearest: N` and `instant: true` (Ben, 2026-09-21, for the fixed instruments):** `nearest` makes only the N crystals closest to `origin` react, at full strength (no distance falloff, no cutoff); everything else ignores the message. `instant` drops the propagation delay entirely (the hallway still lights from the face nearest the origin, just without waiting). Instrument routes use both, so the crystals around the mushrooms, geodes, plant and tubes answer the moment they are played, while a prop's cast still ripples across camp.
- **`combo: false`** on a payload keeps that hit out of the combo rule (it still counts for rhythm, stain and paint). Instrument routes carry it: the mushrooms, geodes, plant and tubes are played together all the time, so two of them landing on one crystal is normal, not a combo; props still combo with each other and with instruments' hits that lack the flag.
- **`cutoff_m`** on a payload is a hard edge: nodes farther than that do nothing at all (for deliberately local effects like the wand's orb or the drum roll; without it the 40% floor means every node in camp reacts a little).
- **`falloff: none`** on a payload skips the distance falloff (Ben, 2026-09-20: spin hits every crystal no matter how far). A `spiral` cast's delay is **rotation only**: `((spin_dir * a) mod 360) / 360 * turn_s`, with no distance term, so the sweep reaches near and far nodes at the same angle.
- **Stain rule (Ben, 2026-09-20):** whenever a coloured effect hits a crystal, that colour lingers in the crystal's idle colour with a ~60 s half-life (weight by intensity), so in show mode a painted crystal stays tinted long after the cast.
- **Rhythm rule (Ben, 2026-09-20):** a responder also keeps the arrival times of hits per `from`. The last four hits from the same prop spaced evenly (0.25-2 s apart, each interval within 15% of the mean) lock a tempo: the node pulses in that prop's colour at `60 / mean` BPM, phase-aligned to the last hit, for at least 6 s (8 beats), re-synced by further hits, fading over the last 2 s. So drumming on a crystal makes it keep the beat.
- **Combo rule (Ben, 2026-09-20):** every prop output carries `from` = `prop_id`. A responder remembers the last hit per `from`; when a hit from a *different* prop lands within `combo_ms` (500) of the previous one, it plays a **combo animation blending both colours** (the reference crystal: the two LEDs alternate between the two colours at ~4 Hz with a white burst at the start, for 3 s, at the higher of the two intensities). Arrival time at the node counts, not send time, so two people can time a combo on a far crystal by casting from different distances.
- **Accuracy reality:** consumer GPS is 2-5 m absolute, worse under structures and near metal. Two things make it workable at camp scale: average 20-30 s of fixes per calibration mark, and note that marks taken minutes apart with the same receiver share most of their error, so *relative* positions are better than absolute. Crystals closer together than ~3 m will not reliably order. If sub-metre matters later, the upgrade path is UWB (DWM1000-class modules with 3-4 fixed anchors, ~10 cm) with the same topics.

## Game: Paint the Camp (proposed 2026-09-20, Ben)

| Topic | Payload | Retained |
|---|---|---|
| `game/state` | `{"game": "paint", "phase": "lobby" \| "play" \| "climax" \| "victory" \| "idle", "players": {"staff_01": "#ff4000", "wand_01": "#00c8ff"}, "started": ts, "ends": ts, "winner": "staff_01", "lock_order": ["crystal_07", "..."], "lock_s": 0.6}` | yes |
| `game/score` | `{"phase": "play", "energy": {"staff_01": 12.3}, "lead": {"staff_01": 7}, "n": 20, "coverage": {"staff_01": 0.35}, "ts": 0}` - the booth's scoreboard feed, 2 Hz while a game runs | yes |
| `crystal/<id>/paint` | `{"shares": {"staff_01": 2.4, "wand_01": 0.9}, "total": 3.3, "ts": 0}` | yes, 1 Hz during play |

- **Master** = the booth (the simulator stands in). **Lobby** (8 s): a prop's first cast claims its colour; a colour already taken gets the next free swatch. **Play** (booth-configured, default 45 s): every prop hit that reaches a crystal adds its *effective* intensity (after falloff and shape) **x the effect's `paint` weight** to that crystal's ledger for that player, so distance and aim matter and whole-camp effects (drum hit 0.15, staff spin 0.25) are worth less per crystal than precise ones (wand bolt 1.0, staff swing 0.7). Weights live in `mappings.yaml`. Crystals fade between the players' colours with a **duty cycle proportional to each player's share**, overall brightness by total energy (untouched crystals stay dark). **Climax / lock-in** (0.6 s per crystal, 6-20 s; revised 2026-09-20): the master publishes `lock_order` (most decided crystals first, contested last) and each crystal, at its turn, stops cycling and commits to its leading colour with a short pop. **Victory** (20 s): the master sums `crystal/+/paint` and publishes the winner; every crystal shows the winner's colour, breathing **in synchrony** (same phase everywhere, 2.5 s cycle), then fades and resets its ledger. No energy anywhere = draw, everything fades to dark.
- Crystals keep their own ledgers, so the game survives a flaky master; the master only needs the 1 Hz paint reports to score. **Displays** follow `game/state` + `game/score`: lobby = "cast to claim your colour" with the joined players' colours; play = per-player coverage bars (share of crystals led) and the countdown; climax = "locking in"; victory = the winner's colour full-screen with their name; idle = back to now-playing. Rhythm and combo rules stay active during play (they are feedback, not score). `paint` weights are tuned with `tools/balance.py` (Monte Carlo over random positions/headings) so every gesture has the same *expected* energy per cast; precise casts therefore carry big per-crystal weights and whole-camp casts small ones. Combo heat on a node fades with a ~12 s half-life.

## Displays

| Topic | Payload | Retained |
|---|---|---|
| `display/banner` | `{"text": "FOOD'S UP — dinner!", "level": "takeover", "until": 0, "ts": 0}` | yes (cleared with empty text) |
| `display/mode` | `{"mode": "nowplaying"}` — one of `nowplaying, schedule, comein, comments` | yes |

Displays render `booth/dj` + `display/*` from retained state. If the broker vanishes they keep showing the last thing they had.

## Discovery and health

- `campbus.local` via mDNS/avahi, and a static IP as fallback. Both in `deploy/README.md`.
- `$SYS/#` on mosquitto gives broker stats. The booth dashboard (M2) shows every `node/+/status` so a dead node is visible at a glance.

## Versioning

Additive changes only (new topics, new optional fields). Renames or removals bump the version header above and get announced in `#interactive-art` with a two-week overlap.
