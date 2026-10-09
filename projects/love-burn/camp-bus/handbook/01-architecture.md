# How it works

## The four layers

Every piece of the camp falls into exactly one of these. Knowing which one you are building tells you
what you may publish and what you must subscribe to.

| Layer | What it does | Writes | Reads | Examples |
|---|---|---|---|---|
| **Inputs** | turn a human action into one small JSON message | `toy/<id>`, `prop/<id>`, `instrument/<id>`, `booth/dj`, `kitchen/foodcall` | nothing (usually) | big red button, staff, mushroom drum pads, check-in kiosk |
| **Mapping engine** | decides what an input *controls* | `cmd/<target>/<effect>`, `display/*` | every input topic | `booth/mapping_engine.py` — one process, the booth Pi |
| **Responders** | make light/sound/pixels happen | `node/<id>/status`, `node/<id>/count` | `cmd/<my_target>/#`, `cmd/all/#`, `audio/*` | crystals, space hallway, UV beds, entrance screen, kitchen bell |
| **Bridges / adapters** | translate the bus to a protocol that isn't MQTT | varies | varies | `tools/midi_bridge.py` (bus→MIDI→Ableton), Resolume adapter, ESP-NOW→MQTT bridge |

The engine in the middle is the whole point of the design: a device publishes *what happened to it*, never
*what should happen elsewhere*. "Tonight the crank runs the tunnel" is a YAML edit on the booth Pi, not a
firmware flash on the crank.

## The transport

MQTT 3.1.1 on mosquitto, on the booth Pi (`campbus.local`, static `10.42.0.1`):

- **1883** plain MQTT — ESP32s, Pis, laptops. **9001** MQTT over WebSockets — browser pages and phones.
- No auth; the LAN is closed and has no internet route.
- JSON payloads, one object per message, **under ~320 bytes** so a $3 ESP32 can parse one with a fixed buffer
  (raised from ~200 on 2026-09-22; the reference sketch sets `mqtt.setBufferSize(512)` and that buffer holds the **whole packet** - MQTT header + topic + payload - so ~320 leaves real headroom).
- **QoS 0.** A lost beat does not matter; anything that must survive is **retained** instead.
- `ts` is unix milliseconds; a node with no clock sends `0` and consumers read that as "now".
- Client id **is** the `node_id`, so the broker enforces uniqueness for free.

**Retained state is the memory of the camp.** Who is DJing, where every node stands, the camp mode, the
live rule list, the MIDI table, the game state — all retained. A node that boots at 3 a.m. gets the whole
current world on connect and renders it correctly without asking anyone. This is why there is no "server"
to be down: the broker's retained set *is* the state.

## Node lifecycle (every node, no exceptions)

```
node/<id>/status   {"online": true, "ip": "10.42.0.31", "fw": "crystal-0.3", "uptime": 1234}   retained
node/<id>/count    {"interactions": 152}                                                       retained
```

- Set the **last will** to `status` = `{"online": false}` retained, so vanishing marks you offline.
- Publish `status` on connect and every 30 s; publish `count` when it changes, rate-limited to 1/s.
- The counters are the grant-application analytics ("N thousand interactions"). Keep them honest.

`clients/python/campbus/__init__.py` does all of this for you in ~40 lines of use; the ESP32 sketch does
the same in C. Copy one of them rather than writing it again.

## What runs where

| Host | Runs |
|---|---|
| **Booth Pi 5** | mosquitto, the mapping engine, audio capture + analysis, the check-in kiosk, NTP (chrony) for the LAN |
| **Ableton laptop** | `tools/midi_bridge.py` → loopMIDI port `Camp Bus` → Live (one MIDI channel per instrument) |
| **Resolume PC** | the Resolume adapter (clip triggers on check-in) |
| **Display Pis** | browser kiosk on the now-playing page |
| **Bridge Pi Zero 2 W** | ESP-NOW→MQTT for the props, PixelBlaze bridge for the hallway |
| **ESP32s** | toys, crystals, instrument sensors — DHCP, and they only ever talk to `10.42.0.1` |
| **Any dev PC** | the whole thing in Docker, with the browser simulators standing in for hardware |

## The simulator is not a toy

`sim/` is a full mock of the camp and it is how this project is developed and tested. Every mock
subscribes to exactly what the real device will subscribe to and publishes exactly what the real device
will publish, so a scenario that passes against the mocks is an acceptance test for the hardware later.

| Page (<http://localhost:8080>) | What it is |
|---|---|
| **Virtual Camp** (`camp.html`) | the output side: crystals (2 RGB LEDs each), the space hallway (5 hex frames × 6 edges × 10 px + 6 rails × 32 px, in real 3D), entrance screen, Resolume, kitchen bell, the camp **map** (drag nodes and props; dragging publishes `map/<id>`), camp mode, vibes, and the Paint the Camp game |
| **Instruments** (`instruments.html`) | the four touch instruments, playable with mouse and keyboard, plus the live MIDI table (`config/midi/<id>`) and Web MIDI output |
| **Routes** (`routes.html`) | the mapping table as a UI — every input, every target, every rule; edits publish `mapping/set` and go live in the engine |
| **Settings** (`settings.html`) | per-browser: test sounds, volume, broker URL, who is touching |
| **Devices** (`nodes.html`) | the fleet: every node's health from its retained status, grouped by layer, including the ones that are **expected and absent** — plus a link into each device's own console |
| **Booth Devices** (`devices.html`) | Resolume output, kitchen bell, DJ check-in |
| **GPS** (`gps.html`) | live position for anything publishing `map/<id>`, two ways: a metre **grid** (trail, 1σ scatter ellipse, accuracy circle, casts) and a **map** on satellite/street tiles. Also sets the retained camp origin (`config/origin`). Tiles need internet until an offline pack exists |
| **Console** (`dashboard.html`) | every node's status, the loaded rules, the raw bus log |

Command-line equivalents: `sim/devices.py` (mock responders that print what they receive), `sim/inputs.py`
(fire any input, or run a YAML **scenario** that asserts what must and must not appear on the bus),
`tools/spy.py` (watch everything), `tools/fake_beat.py` (synthetic beat/energy so the camp has music).

## Every device serves its own console

A node is expected to host a small page about itself and to advertise the address as `ui` in its retained
status (`docs/SCHEMA.md`). The Devices page reads that and links to it; nothing is hardcoded to a port.

| | |
|---|---|
| Pi-class | `campbus.console.serve(node, port=8102, actions={...})` — two lines. Health, what it is subscribed to, the last messages in and out, and a button per debug control. |
| ESP32 | the reference sketch serves a cut-down version on port 80 and advertises `ui: 80`. |
| Mock devices | every mock in `sim/devices.py` serves one on 8101+, so the virtual camp behaves like the real one. |

The bar is what a person standing in front of the device needs at 2 a.m. with a phone and no laptop:
*is it on the bus, is it hearing the right topics, and did my last action reach it.* Controls are local —
identify, self-test, reconnect — never a shortcut into `cmd/`, which stays the mapping engine's alone.

## The mapping engine

One file of rules, `booth/mappings.yaml`:

```yaml
- name: "staff slam -> circle: hallway shockwave + crystals flash"
  group: Props
  when: { topic: "prop/+", prop: staff, gesture: slam }
  then:
    - { topic: "cmd/hallway/shockwave", payload: { intensity: "{intensity}", color: "{color}", from: "{prop_id}", origin: { lat: "{lat}", lon: "{lon}" }, speed_mps: 6, range_m: 60 } }
```

- `when.topic` is an MQTT filter; every other key must equal that field in the incoming payload.
- `"{field}"` as a whole string keeps the original type (`"{intensity}"` stays a number); inside a longer
  string it interpolates (`"FOOD'S UP - {meal}!"`).
- `throttle_ms` drops repeats of that rule inside a window (used for `hold`/`bend` streams).
- The file **hot-reloads on save**. A broken edit is rejected, reported on `mapping/error`, and the previous
  rules stay live — you cannot take the camp down with a typo.
- The engine publishes `mapping/loaded` and the full live rule list on `mapping/rules` (both retained), and
  accepts a whole new rule list on `mapping/set`. That is how the Routes page edits routing over the bus.
- Validation refuses rules that would write outside `cmd/`/`display/` or that subscribe to `cmd/#`
  (which would loop).

## Positions and propagation

Casts ripple across camp, so nodes need positions.

- `map/<node_id>` (retained) carries `{lat, lon, acc_m}`; extended nodes like the hallway add four
  `corners` in order FL, FR, BR, BL so they know their footprint *and* heading.
- A node subscribes to **its own** `map/<id>` on boot. It never needs a GPS of its own.
- Calibration walks the node list (`cal/start` → `cal/target` → carry the staff there → `cal/mark`), and
  the engine writes `map/<id>`. In the simulator, dragging a node on the Virtual Camp map stands in for it.
- Effect payloads may carry `origin`, `speed_mps`, `range_m`, and the shape keys. Each responder computes
  its own delay and intensity from its own position — there is no central choreographer.

The exact falloff, shape and exception rules are in SCHEMA.md, and the *why* is in
[`02-feature-intent.md`](02-feature-intent.md).

## Sound path (it is not on the bus)

Art pieces send **commands, not audio**. The touch instruments publish `instrument/<id>` events; the MIDI
bridge turns those into notes and CCs on one loopMIDI port; Ableton makes the sound and feeds a spare mixer
channel. Audio *out* to the camp is Snapcast (Pi-class consumers) plus an HTTP stream for phones — a
separate path that never rides MQTT.
