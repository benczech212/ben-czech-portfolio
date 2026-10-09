# Building a device (the agent playbook)

For an agent or dev asked to prototype a camp device. Follow this order; it is how every device already on
the bus was built, and it means your device is testable long before any hardware exists.

## 0. Orient (15 minutes, do not skip)

1. Read [`../docs/SCHEMA.md`](../docs/SCHEMA.md) — the contract. Everything else is commentary.
2. Read [`02-feature-intent.md`](02-feature-intent.md) for the behaviour your device has to fit into, and
   the brief for your device in [`devices/`](devices/).
3. Run the virtual camp and play with it until the message flow is obvious:
   ```
   cd apps/camp-bus && docker compose --profile sim up --build
   # http://localhost:8080  +  docker compose run --rm input press big_red_button
   ```
4. Watch the bus while you do it: `docker compose logs -f mapping devices`, or
   `python tools/spy.py --host localhost --no-energy`.

## 1. Decide what kind of device you are building

| Kind | You publish | You subscribe | Copy from |
|---|---|---|---|
| **Input** (button, crank, prop, sensor) | `toy/<id>` / `prop/<id>` / `instrument/<id>` | nothing (plus your own `map/<id>` if you have a position) | `clients/esp32/campbus_template/` |
| **Responder** (lights, bell, screen) | only `node/<id>/*` | `cmd/<my_target>/#`, `cmd/all/#`, optionally `audio/beat`, `audio/energy`, `camp/mode` | `sim/devices.py` mocks, the ESP32 sketch's commented responder block |
| **Instrument** | `instrument/<id>` + retained `config/midi/<id>` | `cmd/<kind>/#`, `cmd/all/#`, its own `map/<id>` and `config/midi/<id>` | `sim/instruments.html`, `tools/midi_bridge.py` |
| **Bridge/adapter** (MIDI, Resolume, ESP-NOW, PixelBlaze) | whatever it translates in | whatever it translates out | `tools/midi_bridge.py` |

**Hard rule: nothing but the mapping engine writes to `cmd/`.** If your device wants to make something else
happen, publish what happened *to you* and add a rule.

## 2. Contract first

Add your topic and a real example payload to `docs/SCHEMA.md` before writing code — that file is what other
camp builders read, and a device that isn't in it doesn't exist. Rules:

- Additive changes only (new topics, new optional fields). Renames bump the version header.
- Under ~320 bytes per message (raised from ~200 on 2026-09-22). An ESP32 parses these with a fixed buffer;
  the reference sketch sets `mqtt.setBufferSize(512)` and that buffer holds the **whole packet** - MQTT header + topic + payload - so ~320 leaves real headroom.
- Reuse an existing shape rather than inventing one: a new handheld prop is `prop/<id>` with a new
  `gesture`, not a new topic; a new touch instrument is `instrument/<id>` with new pads.
- `node_id` is lowercase snake_case with a type prefix: `crystal_03`, `toy_big_red_button`,
  `display_entrance_left`, `bridge_hallway`.

## 3. Simulate it before you build it

This is the step that makes the rest cheap. Work through as many of these as apply:

| File | Add |
|---|---|
| `sim/inputs.py` | a function that returns `(topic, payload, retain)` and register it in `INPUTS` — now it is a CLI command *and* usable in scenarios (`docker compose run --rm input dip tube_2 0.8`) |
| `sim/devices.py` | a `MockDevice` subclass (`icon`, `role`, `subs = [("cmd/<target>/#", "on_cmd"), ("cmd/all/#", "on_cmd")]`) and a line in `build_devices()` — it gets a console on 8101+ for free |
| `sim/routes.html` | an entry in `INPUT_DEVICES` (so the Routes UI can build rules for it) and/or `TARGETS` |
| `sim/camp.html` | a node in `DEFAULT_LAYOUT` (+ `INST_ICON` if it is a fixed instrument) so it has a map position and publishes `map/<id>` |
| `sim/instruments.html` | a playable panel, if it is an instrument |
| `booth/mappings.yaml` | the rules that decide what it controls |
| `sim/scenarios/smoke.yaml` | steps that fire it and assert what must (and must not) appear |
| `tests/` | unit tests for anything pure (translation tables, rule matching) |

A mock must subscribe to **exactly** what the real device will subscribe to, and publish exactly what it
will publish. That is what makes the scenario an acceptance test for the hardware later.

### Minimal responder (Python, Pi-class)

```python
from campbus import Node, console

node = Node("crystal_03", fw="crystal-0.1", role="responder")   # host: $CAMPBUS_HOST or campbus.local
console.serve(node, port=8102, actions={"Identify": lambda: flash_white()})   # its page, on the bus as `ui`

@node.on("cmd/crystals/#")
@node.on("cmd/all/#")
def on_cmd(topic, msg):
    effect = topic.rsplit("/", 1)[-1]                # flash / pulse / color / charge / mode
    if effect not in ("flash", "pulse", "color", "charge"):
        return                                        # ignore what you don't implement
    colour, intensity = msg.get("color", "#ffffff"), float(msg.get("intensity", 1))
    # ... mix `colour` into what you are showing with weight `intensity`, delayed by
    #     distance(origin, my position) / speed_mps if the payload carries an origin
    node.count()

@node.on("map/crystal_03")                            # your own position, retained, written by calibration
def on_map(topic, msg):
    ...

node.connect()
node.wait()
```

### Minimal input (ESP32)

Copy `clients/esp32/campbus_template/`, change `NODE_ID` / `TOY_ID` / pins. It already does the retained
status, the last will, the counter, a debounced button publishing `toy/<id>`, and an LED flashing on
`audio/beat`. The responder path is a commented block in `onMessage()`.

Two things the template is emphatic about and you should keep: **the local effect happens first, then the
bus hears about it** (opt-in rule), and **ESP32 mDNS is unreliable — hardcode the booth IP `10.42.0.1`.**

## 4. Route it

Add rules to `booth/mappings.yaml` under a `group:` so the Routes page files them together. Save the file
and the engine reloads it — no restart. Validate without running anything:

```
python booth/mapping_engine.py --check
```

**YAML trap:** a rule `name` containing `": "` must be quoted, or YAML reads it as a mapping. This has bitten
this repo twice.

## 5. Prove it

```
docker compose run --rm input run sim/scenarios/smoke.yaml    # your steps included
docker compose run --rm test                                  # full pytest suite
```

A scenario step is `do:` (any input, args as scalar/list/dict) plus `expect:` (topic + optional payload
subset, `within_ms`) and `expect_not:`. Write one per behaviour you care about — they double as the
hardware acceptance tests.

## 6. Then build the hardware

With the simulated device passing, the physical prototype has one job: **emit the same messages**. Bench rig:

1. A dev PC running `docker compose up -d` (broker on 1883 / ws 9001) — that is your camp.
2. The board on the same WiFi, `BROKER_HOST` = the PC's LAN IP.
3. `python tools/spy.py --host localhost` to watch what the board actually sends.
4. The browser sim open: your real board's messages drive the *virtual* camp, so you can see a real button
   flash virtual crystals before a single crystal exists.
5. Then the reverse: `docker compose run --rm input ...` to send your board commands and watch it light.

## Acceptance bar — a device is "done" when

- [ ] Its topic and payload are in `docs/SCHEMA.md` with a real example.
- [ ] It publishes retained `node/<id>/status` with a last will, and bumps `node/<id>/count` on interactions.
- [ ] It works with the broker **down** (opt-in rule) and recovers by itself when the broker returns.
- [ ] It re-renders correctly from retained state alone after a cold boot (`map/<id>`, `camp/mode`,
      `config/midi/<id>`, `booth/dj` — whatever applies).
- [ ] It ignores `cmd/` effects it does not implement instead of erroring.
- [ ] **It serves its own console and advertises it as `ui`** — `campbus.console.serve(node, port=…)` on a
      Pi, the template's built-in page on an ESP32 — with at least an Identify control.
- [ ] It never publishes to `cmd/`.
- [ ] A scenario in `sim/scenarios/` fires it and asserts the result; `pytest` passes.
- [ ] Payloads are under ~320 bytes.
- [ ] `../../../HANDOFF.md` has a dated line saying what you did and what is still open.

## Traps this project already hit

Each of these cost someone an hour. They are all still true.

**Bus / Python**
- Publishing immediately after `connect()` races your own subscriptions — use `node.wait_ready()` first.
- Windows `time.monotonic()` is 15.6 ms coarse; the scenario recorder uses `perf_counter()` for timing.
- The `devices` container does **not** hot-reload: `docker compose restart devices` after editing
  `sim/devices.py`. The mapping engine *does* hot-reload `mappings.yaml`.
- `docker compose run --rm test` uses its own throwaway broker on purpose, so the live sim can't
  double-route or steal client ids mid-test.

**MIDI / Ableton**
- Two instruments on one MIDI channel is silent breakage — the DAW cannot tell them apart. The Instruments
  page flags it in red and the bridge logs a WARNING; believe them.
- Never run `tools/midi_bridge.py` and the Instruments page's Web MIDI output into the same port — every
  note doubles.
- Whether velocity reaches volume is a property of the patch, not the bus. That is what CC 11 is for.

**Browser simulators**
- A throw inside a per-frame draw kills the `requestAnimationFrame` loop and freezes *every* canvas on the
  page, which looks exactly like "drag and drop stopped working". Keep draw loops catching per frame.
- `setPointerCapture` is best-effort. Also listen for `pointerup`/`pointercancel` on `window`, or letting go
  outside the canvas leaves a mushroom following the cursor or a pad streaming `hold` forever.
- The sim is served `Cache-Control: no-store` (`bus/nginx.conf`) because a cached `shared.js` paired with a
  newer page is a real and confusing failure. Keep it that way.
- Chrome-extension synthetic mouse drags do not reach a canvas; dispatch real `PointerEvent`s instead.

**Contract**
- `display/banner` is retained: a takeover with no `until` owns the screen forever.
- Retained topics you publish are the camp's memory — clear them deliberately (`booth/dj` with
  `artist: null`, banners with empty text).

## Working style for agents

- **Do not invent topics or fields.** If the shape you need isn't in SCHEMA.md, either reuse an existing one
  or propose the addition in your summary and let Ben decide — he owns the contract.
- Anything in the "Open questions" list in [`03-state-of-play.md`](03-state-of-play.md) is a **decision, not
  a task**. Build everything that doesn't depend on it and say plainly what you left.
- Dates in this repo are absolute (`2026-09-22`), never "tomorrow".
- Deploys, pushes and anything outward-facing are Ben's call.
