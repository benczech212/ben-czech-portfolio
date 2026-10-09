# State of play — 2026-09-22

Honest status. "Works" here means *verified against a real broker*, in Docker or on a dev PC.

**2026-09-22: the first physical device reached the bus.** A Raspberry Pi running
[`../nodes/staff_pi/`](../nodes/staff_pi/) published real `prop/staff_01` casts to the broker on the dev
PC and the mapping engine routed all three staff gestures to all six expected `cmd/` outputs — then did it
again carrying a **real GPS position** from the Pi's PA1010D into each effect's `origin`. Gestures are
still a keypress and `heading` is still `null` (the BNO055 on board is unread), but the chain from a real
device to the virtual camp, position included, is proven.
Everything else physical is still a sculpture-in-progress or a parts list.

## In one table

| Piece | Software | Hardware | Notes |
|---|---|---|---|
| Broker (mosquitto) | ✅ config + Docker + Pi install notes | ⏳ booth Pi not built | `bus/mosquitto.conf`, `deploy/README.md` |
| `campbus` Python client | ✅ works | — | node id, retained status, last will, heartbeat, counters |
| ESP32 reference sketch | ✅ written | ❌ **never run on a board** | `clients/esp32/campbus_template/` — first real board is the acceptance test |
| Mapping engine | ✅ works, hot-reload, live editing over the bus | ⏳ runs on the booth Pi | pulled forward from M4 |
| Virtual camp (crystals, hallway, screen, bell, Resolume) | ✅ browser + CLI mocks | ❌ none built | `sim/camp.html`, `sim/devices.py` |
| Touch instruments (4) | ✅ simulated, routed, MIDI-bridged | ❌ none built | see below |
| MIDI bridge → Ableton | ✅ works, tested dry + into Live | ✅ laptop exists | `tools/midi_bridge.py`, set described in `deploy/ableton.md` |
| Props (staff/wand/drum/whip) | ✅ simulated, 9 gestures routed + balanced | ⚠️ **staff runs on a Pi** as a systemd service: GPS, IMU, gesture casting, live sensor page | **one real gesture** (hold still 2 s, then sweep → `spin` + `spin_dir` + `heading`); slam/swing still keypresses; needs the ESP-NOW→MQTT bridge |
| Toys (button, lever, crank, dial, faders) | ✅ simulated + routed | ❌ none built | the ESP32 sketch is already a button toy |
| Positions / calibration | ✅ designed + simulated + **GPS page** (grid + satellite map, settable camp origin) | ✅ **PA1010D gives real fixes** on the staff Pi, running as a systemd service | casts carry real `lat`/`lon`; `config/origin` anchors camp; **offline tiles not built** (licensing, see node README); calibration walk still never run |
| Paint the Camp game | ✅ simulated end to end | — | booth is master; crystals keep their own ledgers |
| DJ check-in / now-playing | ✅ topics + mocks | ⏳ kiosk + display Pis not built | M2 |
| Resolume adapter | ✅ mocked (`cmd/resolume/clip`) | ⏳ not written against Arena | M2, needs Ben's Arena box |
| Audio capture + analysis | ❌ `tools/fake_beat.py` only | ❌ | M3: real capture, aubio, Snapcast, phone stream |
| Kitchen food call | ✅ routed + mocked | ❌ bell node not built | M4 |

## Tests

`pytest` — **25 passing** (rule unit tests, the mapping-engine file/edit lifecycle, MIDI translation, and
end-to-end workflow tests that run a real broker in-process). `sim/scenarios/smoke.yaml` asserts every
default route end to end and is run by the suite; `CAMPBUS_HOST=localhost pytest` runs the same suite
against a real broker, and `docker compose run --rm test` runs it against its own throwaway one.

## The four touch instruments

All four are simulated, routed, MIDI-mapped and covered by the smoke scenario. None exist physically.

| Instrument | `instrument` | Pads | Notes / ch | Events | Physical status |
|---|---|---|---|---|---|
| Sonic Mycelium Cluster | `mycelium` | `amanita`, `chanterelle`, `indigo_milk`, `ghost`, `violet_coral`, `pink_oyster` | 36/38/42/45/39/51, ch 1 | `hit` | two camp builders; silicone castings + piezo + ~$100 e-drum brain |
| Crystal Geode Synth | `geode` | `amethyst`, `citrine`, `aquamarine`, `rose_quartz` | 60/64/67/71, ch 2 | `touch`/`hold`/`release` | camp build team; brass wireframe + holographic facets + COB RGBW |
| Monstera Piezo Plant | `plant` | `leaf_1`…`leaf_5` | 36/40/43/47/50, ch 3 | `bend`/`release`/`hit` | camp build team; silicone leaves, LED filament + piezo tape, TRRS per stem |
| Water Chord Tubes | `tubes` | `tube_1`…`tube_3` | 48/52/55, ch 4 | `touch`/`hold`/`release` | camp build team; water cylinders, RGB puck per base, wire in the water |

Recent changes worth knowing (2026-09-21): the plant went from **four leaves to five**; the tubes went from
a reserved slot to a **built instrument with three tubes** (the original concept said about five — three
is Ben's call); MIDI channels became sequential 1/2/3/4.

## Ableton

A Live 12 Lite set exists (built 2026-09-21 over the AbletonMCP link, rebuilt the same day with sequential
channels), described in `deploy/ableton.md`: four MIDI tracks fed from the `Camp Bus` loopMIDI port, one
channel each, Monitor = In, no clips — it is a live play-through rig.

**Open:** track 4 (Tubes) has **no instrument loaded**. It wants something polyphonic that sustains with a
filter on CC 74, like the geodes' patch. Everything else (CC 11 → Utility gain, CC 74 → filter macro,
returns) is documented as one-time manual steps because the Remote Script cannot do them.

## Milestones (from the camp planning notes, not included in this copy)

| # | Target | Status |
|---|---|---|
| M1 | 2026-10-05 | scaffold, schema, clients, fake beat — **software done 2026-09-16**; a bench ESP32 blinking on beats is still owed |
| M2 | October camp meeting | check-in kiosk + now-playing + Resolume against Ben's Arena box — **not started** |
| M3 | 2026-11-15 | real audio capture, Snapcast, phone stream, aubio replacing the fake beat — **not started** |
| M4 | 2026-12-15 | mapping engine (**done early**), Big Red Button reference toy, kitchen food-call node |
| M5 | January 2027 | staff ESP-NOW bridge, hallway PixelBlaze bridge, range + echo test. **Feature freeze 2027-01-15** |

Camp and art applications (due 2026-09-30) come before bus work.

## Open questions

Resolve with Ben before building the affected part.

- **GPS module for the props — effectively answered, needs Ben to bless it.** An Adafruit Mini GPS
  **PA1010D** on the staff Pi (`nodes/staff_pi/pa1010d.py`) delivers 1 Hz fixes, 1.3-6.5 s warm TTFF,
  9 sats / HDOP 0.89, and 0.2 m short-term scatter, and real `lat`/`lon` now reach `cmd/` `origin`
  end to end. A 20-30 s calibration average is comfortably clear of that scatter. Still untested: the
  antenna in a metal-heavy camp, and a cold start. Whether the engine or each node applies propagation
  delay *is* settled, in favour of the node.
- **The staff casts from a real gesture (2026-09-23).** Hold it as still as you can for 2 s to arm,
  then sweep it clockwise or counter-clockwise. It publishes the existing `spin` gesture with
  `spin_dir` (+1 cw / -1 ccw), a real `intensity`, and `heading` from the fused compass - which means
  spirals are readable for the first time, with no mapping changes, because `staff spin` was already
  routed to the spiral cast. Direction comes from projecting the gyro onto the BNO055's gravity vector,
  so it reads the same however the staff is held.
- **The first real output device is on the bus (2026-09-23):** an Adafruit Matrix Portal M4
  driving an RGB matrix panel, in `nodes/matrix_portal/`. It answers `cmd/matrix/#` and
  `cmd/all/#` with a colour or a fill from any of eight directions, using `heading` for
  the direction - an existing field, so nothing was invented. It obeys the propagation
  and falloff rules like any other responder.
- The panel also does a **Perlin texture** (brightness and hue), a **circular fill
  front** rather than a straight edge, `twinkle`, and **zones**: a pixel map saying
  which pixels light which rock, drawn in a Booth Devices editor and stored retained on
  `config/zones/<node_id>`. `rainbow` gives every zone its own hue and walks the wheel.
- The Booth Devices zone editor is full-window, with a polygon tool, view rotation, a
  live-as-you-draw mode, and a **Calibrate** pass that lights one block of the panel
  white at a time and asks which rock it is behind — which is how a 17-rock map gets
  made without guessing.
- **Open question for Ben, not a task:** `matrix` is not yet in SCHEMA.md's list of
  responder targets, and `config/zones/<node_id>` is not in the topic table either
  (it follows the existing `config/midi/<instrument_id>` shape). The topic shape and every effect it implements are already in the
  contract; only the target name needs a decision. The proposed one-line edit is in
  `nodes/matrix_portal/README.md`, and no mapping rule has been added to
  `booth/mappings.yaml` - a suggested one is in the same README.
- **Open question for Ben, not a task:** a sweep currently *is* a `spin`. If sweeping should be its own
  gesture, distinct from a twirl, that is a SCHEMA addition plus a mapping rule - the detector does not
  care which name it sends.
- **`heading` is published only while the magnetometer reads calibration 3**, `null` otherwise. A wrong
  heading points the spiral the wrong way and nothing downstream can tell; `null` is a value the
  contract already defines and every consumer already handles.
- **Still unbuilt: slam and swing.** They are keypresses. Both want the same arm-then-act structure the
  sweep has (a slam is a linear-acceleration spike, a swing is rotation about a horizontal axis), so the
  hard part - deciding when the staff is being *asked* something - is done.
- **There is no BME680/BME280 on the staff Pi** — the bus holds only the GPS and the IMU. The
  environment-topic question is parked until such a sensor actually turns up; SCHEMA.md still has no
  shape for one, and this project does not invent topics.
- **ESP-NOW ↔ WiFi bridge** for the props: design not written (M5).
- **`display/banner` retention**: a takeover with no `until` owns a screen forever. The simulator papers over
  it with a 45 s auto-clear; the real kitchen node needs a policy.
- **Quiet hours** policy (`booth/quiet`, `camp/mode: quiet`) — a camp-meeting decision, not a code one.
- **Pro DJ Link** ingestion (a camp DJ's lead) — M3 or later.
- **A member's wireless audio into the mixer** — may stay out of scope.
- **Ableton ch 4 instrument** for the tubes (above).

## How work lands here

Much of this tree is **uncommitted** in Ben's working copy at any given time, and pushes are Ben's to make
(`! git push origin main`). Treat `../../../HANDOFF.md` as the running log — it has a dated entry per work
session, including the gotchas — and add to it when you finish something.
