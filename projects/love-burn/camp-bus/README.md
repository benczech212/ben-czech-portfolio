Part of Ben Czech's portfolio. Built with Claude Code.

# Camp Bus — AWEtisticats camp event bus

Software for the camp-wide message bus at Love Burn 2027. The concept, current status and milestones are summarised in [`handbook/`](handbook/) (start with `handbook/03-state-of-play.md`); the topic contract is [`docs/SCHEMA.md`](docs/SCHEMA.md). This folder is the code.

## The lesson

**The problem.** A camp full of interactive art has many inputs (toys, props, instruments) and many
outputs (crystals, a lit hallway, displays, a bell), built by different people on different boards.
If every input talks to every output directly, nobody can change who answers what without reflashing
firmware, and nothing can be built until the hardware exists.

**The idea.** Put one offline MQTT broker in the middle and make a single **mapping engine** the only
thing allowed to write commands (`cmd/`). Inputs only report what happened; a YAML rule table decides
what that controls, and the engine hot-reloads it, so re-routing the camp is an edit, not a deploy.
Because devices only see topics, browser simulators and mock devices can stand in for the hardware,
and the same scenarios become the tests.

### Walkthrough

1. **Start from the contract.** [`docs/SCHEMA.md`](docs/SCHEMA.md) lists every topic and payload. The
   layout table below says "Change here first, code second": builders agree on topics, not on each
   other's code.
2. **Make liveness free.** In [`clients/python/campbus/__init__.py`](clients/python/campbus/__init__.py)
   every `Node` registers a retained last-will, so the broker itself announces when a device drops off:
   ```python
   c.will_set(self.status_topic, json.dumps({"online": False, "ts": 0}), qos=0, retain=True)
   ```
3. **Read a rule.** In [`booth/mappings.yaml`](booth/mappings.yaml), `when` is a topic filter plus fields
   that must match, and `then` lists the outputs:
   ```yaml
   - name: big red button -> hallway sweep + crystals flash
     group: Toys
     when: { topic: "toy/big_red_button", event: "press" }
     then:
       - { topic: "cmd/hallway/sweep",   payload: { color: "#ff4000", speed: 1.5 } }
       - { topic: "cmd/crystals/flash",  payload: { color: "#ffffff", ms: 300 } }
   ```
4. **Validate before you trust.** `Rule.__init__` in [`booth/mapping_engine.py`](booth/mapping_engine.py)
   refuses outputs outside `cmd/` and `display/`, and any output that would re-trigger its own input:
   ```python
   if not t.startswith(ALLOWED_OUTPUT_PREFIXES):
       raise ValueError(f"rule {self.name!r}: output {t!r} must start with one of {ALLOWED_OUTPUT_PREFIXES}")
   if mqtt.topic_matches_sub(self.topic, t):
       raise ValueError(f"rule {self.name!r}: output {t!r} would re-trigger its own input (loop)")
   ```
5. **Fail safe on reload.** `reload()` watches the file's modification time. If the new file doesn't
   parse, the engine logs `REJECTED` and **keeps the previous rules live**, so a typo at 2 a.m. doesn't
   black out the camp.
6. **Let a UI edit the same rules.** [`sim/routes.html`](sim/routes.html) publishes the whole rule list to
   `mapping/set`. `apply()` validates it, writes a temp file, swaps it in with `os.replace`, and reloads; a
   bad edit comes back on `mapping/error` with the reason. The YAML file stays the single source of truth.
7. **Test the workflow, not just the functions.** [`sim/scenarios/smoke.yaml`](sim/scenarios/smoke.yaml)
   fires inputs and asserts what must (and must not) appear on the bus.
   [`tests/conftest.py`](tests/conftest.py) starts a pure-Python broker (amqtt) in-process, and
   [`tests/test_workflow.py`](tests/test_workflow.py) runs the same scenario through it.

For the bigger picture (architecture, what each feature is *for*, and how to prototype a device), read
the [`handbook/`](handbook/).

### Try it

No boards, Docker or broker needed:

```
python -m venv .venv
.venv\Scripts\activate                       # Windows;  source .venv/bin/activate on Linux/macOS
pip install -r requirements-dev.txt -e clients/python
python booth/mapping_engine.py --check       # validates booth/mappings.yaml and lists every rule
pytest tests/test_workflow.py                # input -> broker -> engine -> mock device, end to end
```

Then break a rule on purpose: copy `booth/mappings.yaml` to `my-rules.yaml`, change one output topic from
`cmd/...` to `toy/lever`, and run `python booth/mapping_engine.py --check --file my-rules.yaml`. It prints
`INVALID:` with the reason and exits non-zero. To watch the whole system, run the Docker "virtual camp" below.

### Take it to your own project

- Give exactly one component the right to issue commands. Everything else reports events.
- Keep routing in data (a rule file), validate it on load, and keep the last good version when an edit fails.
- Write the topic contract before the code, and let mocks subscribe to exactly what the real devices will.
- Turn your acceptance scenarios into tests that run against an in-process broker.
- Ship any web assets the system needs (here `mqtt.js` and Leaflet) so it works with no internet.

## Highlights

- **Offline MQTT bus:** Eclipse Mosquitto on TCP 1883 (devices) and MQTT-over-WebSockets 9001 (browser pages). `mqtt.js` and Leaflet are vendored under `sim/vendor/`, so every page works on a camp LAN with no internet.
- **Docker "virtual camp":** `docker compose` brings up the broker, an nginx-served web UI, a fake beat source, the mapping engine and mock devices, so the whole system runs on one PC with no boards.
- **Hot-reloading YAML rules engine:** `booth/mapping_engine.py` routes inputs to device commands from `booth/mappings.yaml` and reloads on save; `sim/routes.html` edits the same rules as a matrix or a browser node graph and applies them over the bus.
- **Raspberry Pi staff node** (`nodes/staff_pi/`): Python I2C drivers for the BNO055 IMU and PA1010D GPS, gesture casting (slam / spin / swing), and a phone-friendly web console with a live Server-Sent Events (SSE) feed.
- **CircuitPython Matrix Portal M4 node** (`nodes/matrix_portal/`): a 64x32 LED panel that renders bus effects.
- **Ableton:** the booth's Live set was built through the third-party AbletonMCP server. No Ableton MCP code lives here; this folder ships only `tools/midi_bridge.py` (bus to MIDI) and the set notes in `deploy/ableton.md`.
- **Tests:** 169 pytest tests (unit tests for the mapping rules, gestures, sound, matrix effects, plus end-to-end workflow tests through an in-process broker).

## Security notes

- **Two broker profiles.** `bus/mosquitto.dev.conf` is the anonymous *dev* profile used by `docker compose` (the browser sim and mock devices connect without credentials); run it only on a trusted machine or network. `bus/mosquitto.conf` is the booth-Pi profile: `allow_anonymous false`, a `password_file`, and an `acl_file` based on [`bus/acl.example`](bus/acl.example), which lets only the `engine` and `booth` accounts publish `cmd/#` and `mapping/set`. Python services pass credentials through `CAMPBUS_USER` / `CAMPBUS_PASSWORD`.
- **Staff web console** (`nodes/staff_pi/webui.py`) binds `127.0.0.1` by default; pass `--lan` to serve phones on the camp Wi-Fi. Every state-changing route is POST-only.
- **Clip server** (`tools/clipserver.py`) is a dev-only tool: no auth, binds `127.0.0.1` outside Docker, and is reachable only through nginx inside the compose network.
- **Sim pages** escape bus data before inserting it into HTML.
- Wi-Fi credentials, home-LAN addresses and user names are placeholders (`YOUR_SSID`, `campbus.local`, `<pi-ip>`, `<user>`).

**Core:** mosquitto (MQTT) on the booth Pi. Everything on the camp LAN publishes and subscribes to it. Browser pages use the WebSocket listener. Protocol transformation to Resolume, MIDI, ESP-NOW, etc. is done by small adapter services at the edges.

**The contract other builders need:** [`docs/SCHEMA.md`](docs/SCHEMA.md) — topics, payloads, and the rules.

**New here (or an agent picking this up)?** Start with [`handbook/`](handbook/): what exists today, how it works, what each feature is *for*, and the playbook for prototyping a device.

## Layout

| Path | What |
|---|---|
| `docs/SCHEMA.md` | Topic + payload contract. Change here first, code second. |
| `bus/` | mosquitto config + docker-compose for the dev PC |
| `clients/python/` | `campbus` package — the Pi-class reference client |
| `clients/esp32/` | Arduino reference sketch for ESP32 toys and responders |
| `tools/` | `fake_beat.py` (synthetic beat/energy publisher), `spy.py` (bus monitor), `balance.py` (Monte Carlo coverage + energy per cast per prop gesture; `--apply` rewrites the `paint` weights for a fair game), **`midi_bridge.py`** (bus -> MIDI for Ableton: every `instrument/#` event becomes notes and CCs on a loopMIDI / IAC port; `--list`, `--port`, `--dry`; needs `pip install -r requirements-midi.txt`) |
| `booth/` | `mapping_engine.py` + `mappings.yaml` (input -> command routing, hot-reload; also editable live from the Routes page over `mapping/set`, see SCHEMA). M2 adds the check-in kiosk, now-playing page, Resolume adapter. |
| `sim/` | **Mock/testing mode + web UI** (served by the `web` container at http://localhost:8080): `shared.js` (the header navigation every page shares - add a page to `PAGES` there and it appears everywhere - plus per-browser settings in localStorage and a small WebAudio synth for test sounds), `settings.html` (test sounds on/off + volume, who is touching the geodes, broker URL override, activity logs on/off), `index.html` landing page, **`instruments.html`** (touch instruments: the Sonic Mycelium Cluster, six mushroom drum pads with UV beds - tap, pull-and-release, keys, or a jam pattern - and the Crystal Geode Synth, four capacitive brass geodes you press and hold (keys Q W E R for chords) whose touch strength depends on who is touching; synthesised test sounds; also a responder for `cmd/mycelium/*` and `cmd/geode/*`), **`routes.html`** (the input -> output mapper: a matrix or a node graph of devices, rules and targets with dots running along the wires as messages flow, the rule list with a form editor, live "fired" highlights, Test per rule, Apply writes the engine's yaml over the bus), `devices.html` (mock Resolume output + kitchen bell with buttons to drive them), **`camp.html` visual simulators** (6 crystals x 2 diffused RGB LEDs, the 16'x12'x8' hexagonal-frame hallway with pixels at real 3D positions, camp map with cast ripples, entrance screen, Resolume output, bell), `dashboard.html` (console: nodes, rules, bus log), `devices.py` (log-only mocks), `inputs.py` (fire simulated inputs; scenario runner), `scenarios/*.yaml`, `vendor/mqtt.min.js` (vendored so it works on the offline camp LAN) |
| `tests/` | pytest: unit tests for the mapping rules + end-to-end workflow tests through a broker |
| `clients/python/campbus/console.py` | the per-device web console: two lines in any node and it serves its own health/traffic/controls page, advertised on the bus as `ui` |
| `deploy/` | Pi install notes + systemd unit templates |
| `handbook/` | Orientation: architecture, feature intent, state of play, and a brief per device (`handbook/devices/`) |

## Run the virtual camp on Docker (no boards)

```
docker compose up -d                          # broker (mosquitto :1883 mqtt, :9001 websockets) + web UI at http://localhost:8080
docker compose --profile sim up --build       # + fake audio + mapping engine + mock devices, logs in the foreground
```

Then, from another terminal:

```
docker compose run --rm input press big_red_button        # fire an input; watch the devices log react
docker compose run --rm input checkin "SideQuest"
docker compose run --rm input hit chanterelle 0.9                # whack a mushroom of the Sonic Mycelium Cluster
docker compose run --rm input touch aquamarine 0.7                # a capacitive touch on a geode (velocity = coupling strength)
docker compose run --rm input bend leaf_2 0.85                    # a plant leaf snapping back (velocity = the piezo spike)
docker compose run --rm input dip tube_2 0.8                     # a hand in a water chord tube (the water is the electrode)
docker compose run --rm input run sim/scenarios/smoke.yaml  # validate every default mapping end to end
docker compose run --rm input run sim/scenarios/audio-mode.yaml  # camp/mode idle silences the audio source (needs fake-beat up)
docker compose run --rm test                              # full pytest suite against the Docker broker
```

Open **http://localhost:8080** (the `web` container serves `sim/`; the files also open straight from disk). **Virtual Camp** starts from a clean slate: camp mode `idle` (no simulated music), a **Props panel** (Staff / Wand / Drum / Whip tabs, each with its own colour, intensity, heading and map position: six colour swatches + custom colour, intensity slider where 1.00 is a *perfect cast*, heading slider (the IMU's compass; Q/E turn 15 degrees), spin direction for the staff, gesture buttons per prop, keys 1/2/3, Tab cycles props, and a **Test props** button that walks the props around the map firing random gestures plus deliberately timed combos and a 120 BPM drum rhythm), a configurable number of crystals (default 20, `count` box in the Crystals header; always one row, gems shrink and labels drop out when cramped; new ones get spiral positions on the map) resting near-dark until a cast lights them, and the camp map. Crystals, camp map, space hallway and entrance screen all fit on one screen (crystals across the top, map on the left, hallway + display on the right); other inputs are folded away; Resolume and the kitchen bell live on `devices.html`. The **entrance display follows the game** over the bus (`game/state` + `game/score`): colour claims in the lobby, per-player coverage bars during play, "locking in", then the winner's colour full-screen. **Record clip** in the header composites crystals + map + hallway + display into a 1280x720 WebM (30 fps) and saves it through the dev-only `clips` container straight into `./clips/` (falls back to a browser download when the page is opened from disk; `__sim.recordClip(seconds, name)` from the console). During a game, **Test props** plays properly: each prop picks a random unclaimed colour in the lobby, claims it with one cast, waits for play, competes, and stands down at victory. The devices render from the same effect engine the firmware will mirror: crystals are 2 RGB LEDs each diffused into one glow; the hallway is five hexagonal frames with an LED strip on every edge plus six full-length rails along the vertices (492 px) and every pixel at a real (x,y,z) like a Pixelblaze 3D map, placed in the world by its four calibrated corners (drag its body to move it, the yellow handle to rotate it); casts enter through whichever face is nearest the caster and travel away from them, and the tunnel has its own effects (sweep, shockwave, comet, rings, wipe, warp, spiral, rain, breathe). The **interactive camp map**: drag any node or the staff marker to place them (10 m grid, layout persists in the browser, `reset layout` restores the default), each node shows its distance, ripple delay and effective intensity for the current slider value, and a staff cast ripples outward so each node reacts when the ring reaches it, fading gently with distance (to 40% at 60 m) unless the cast is perfect. Casts have a **shape**: slam is a circle, swing is a fast 70-degree cone the way the staff points (the map shows the cone preview), spin is pure rotation that reaches every crystal in angular order with no falloff; the wand flicks a narrow fast bolt or pulses an orb around itself; the drum sends a slow heavy wave or a steady charge. When **two different props land on a crystal within 0.5 s** it plays a two-colour combo animation, and **four evenly spaced hits from one prop** lock the crystal into pulsing at that tempo in the prop's colour. **Game: Paint the Camp** (panel under Camp mode): props claim colours in a 10 s lobby, then paint crystals with casts for a set time; crystals fade between players' colours by energy share, the climax pulses faster and faster into a flash, and the winner's colour sweeps across camp from where they stand. Starting a game auto-runs Test props (checkbox to disable) and stops it when the game ends (`origin` + `speed_mps` + `range_m` + `shape`/`heading` + `from`, see SCHEMA). Untick `GPS fix` to cast without a position. **Camp mode** buttons (idle / show / quiet) publish `camp/mode` the way the booth will: idle silences the fake audio source (no beats, no BPM) and the devices fall back to ambient animations; quiet dims everything. The same panel holds the **vibe**: cozy / party / chaos / wave (a brightness wave rolling north to south from every node's position, one bar per wave when synced) / jumping (three coloured sparks hop between nearby crystals on the beat, leap through the hallway, and burn each other's hue when they meet) / ethereal (3D Perlin colour drift) / snake (a 7-crystal chain slithering across camp) / twinkle (random sparkles) / heartbeat (a synced lub-dub) / aurora (green-purple curtains drifting north) / meteor (streaks crossing camp every few seconds); vibe and camp-mode changes crossfade over 2 s plus **Vibe settings** (sliders for every vibe's parameters, persisted and shared on `vibe/params`, per-vibe reset) and two independent toggles on `camp/sync`: **BPM sync (rates)**, which locks every cycle rate to the BPM, and **Pulse to beat**, which controls beat flashes and bass reactivity (off = steady brightness from smoothed loudness). In show mode a prop's colour **stains** the crystals it hits and lingers with a 60 s half-life. Staff buttons carry a colour picker; every effect mixes its colour into the device by intensity. **Bus Console** is the plumbing view: nodes, loaded rules, bus log. Both have input buttons. **Touch Instruments** (`instruments.html`) is the Sonic Mycelium Cluster: six mushroom species, each a piezo drum pad over a UV bed with its own colour and MIDI note. Tap one (or keys 1-6) to whack it at the slider velocity, or **pull it and let go**: the cap follows the pointer, springs back, and the further the pull the harder the hit (25-100%). "Play pattern" jams an 8th-note pattern at a chosen BPM so the crystals lock to it. The cluster's position comes from `map/mycelium_01`, which the Virtual Camp map publishes when you drag the mushroom node. Instrument routes use `nearest: N` + `instant: true`: only the few crystals closest to the instrument react and they do it immediately (the map shows spokes from the instrument to the crystals it hit instead of a ripple); drag an instrument node next to different crystals to change which ones answer it. Below it, the **Crystal Geode Synth**: four brass geodes with a COB inside; press and hold one (or hold Q/W/E/R for chords). The touch strength comes from the **who is touching** profile (barefoot adult, adult in shoes, kid, two people holding hands, gloved fingertip), jittered per touch, and is published as `velocity`; while held the reading settles and wanders and rides along as `value` at 8 Hz (the synth-patch parameter; the default routes tint the crystals on touch and keep charging the nearby ones with the live value). The third panel is the **Monstera Piezo Plant**: four silicone leaves with LED filament and piezo tape; grab a leaf and pull (or hold A/S/D/F): while flexed it publishes `bend` at 8 Hz with the flex as `value`, letting go snaps it back and publishes `release` with the spike as `velocity` (a light tap is a small `hit`); the test sound is a leafy wind over a soft bass drone that opens with the bend. The fourth panel is reserved for the **Water Chord Tubes** (planned). Below the four instruments: one **Activity** log for all of them with per-instrument counters, and a **MIDI mapping** table (note + channel per pad, note names shown) saved in the browser and published retained on `config/midi/<instrument_id>` for the real bridges, with a geode **chord mode** (Mode select in the geode panel: single note or chord, plus a chord preset so each geode plays a whole chord; the chord rides the bus as `notes`, the MIDI table shows chord names, and the page's synth plays one voice per note), **note presets** per instrument (GM drums, Drum Rack C1, 808, percussion; C major 7, A minor 7, pentatonic, D sus2, octaves; bass C, bass A minor, D fifths, sub octaves) and **bundles** that switch every instrument at once (Default, Key of A minor, Key of D, Drum Rack + pentatonic, Wide test); editing a note shows "custom". Every instrument has a **pattern player** (mushrooms: rolling 8ths, four on the floor, breakbeat, tom and clap, one pad quarter notes, round robin, random; geodes: arpeggios, chord swell, alternating pairs, slow drone hand-over, random; plant: slow bend and snap, quick flicks, two leaves swaying, breeze) on one tempo slider with Play all / Stop all in the Activity section; patterns share one transport, so a pattern started while another plays waits for the next bar line and comes in in time. Each instrument has its own map node (mushroom, gem, leaf icons on the Virtual Camp map) and its own position. Hits and holds make **test sounds** in the browser (synthesised drums, a filtered pad voice that opens with the reading); turn them off or set the volume on the **Settings** page, which every open tab follows. **Routes** has two views: the matrix, and a **Graph** of inputs -> rules -> outputs where a dot runs along each wire when a message flows (blue in, orange out, in the prop's colour when it has one) and nodes light up; click a rule node to edit it, **right-click an input or a rule to fire a test** through the real engine. Rules carry an optional **group** (Toys, Props, Mycelium, Geodes, Booth by default): the list folds by group with a whole-group on/off checkbox and a + to add into it, the editor's Group field suggests existing names, the graph boxes each group, and the yaml gets a banner comment per group. **Routes** (`routes.html`) is the mapper: a matrix of input devices against output targets (numbers = rules, click a cell to filter or an empty one to add), the rule list with enable checkboxes, edit/duplicate/delete/Test, a drawer editor that knows every device's actions and fields and prefills sensible payload templates (props and instruments get `origin`/`speed_mps`/`range_m`/`shape`/`from`/`paint`), live highlights when a rule fires, and **Apply to engine**, which sends the list over `mapping/set`; the engine validates, rewrites `booth/mappings.yaml` and reloads (rejections show up with the reason). You can still edit `booth/mappings.yaml` by hand while it all runs; the engine reloads on save.

## MIDI into Ableton

Two paths; use one at a time per port or every note doubles.

1. **Bridge** (what the Ableton laptop runs at camp): create a loopMIDI port named `Camp Bus` (loopMIDI window, type the name, press +), enable it in Ableton under Preferences > MIDI for Track and Remote, arm a MIDI track on it, then

   ```
   .venv\Scripts\python tools\midi_bridge.py --host localhost            # picks the "Camp Bus" port; --list shows ports, --port picks one
   ```

   Whack a mushroom on the Instruments page (or `docker compose run --rm input hit amanita 0.9`) and the track's input meter moves. Channels are sequential: mushrooms are notes on channel 1, geodes hold notes on channel 2 with CC 74 following the touch reading, plant leaves send CC 74 while bent and the snap as a note on channel 3, and the water chord tubes hold notes on channel 4 like the geodes. Note numbers follow the page's MIDI table (retained `config/midi/<id>`).
   The booth set itself (tracks, instruments, channels, the CC 74 macro mappings, returns) is described in `deploy/ableton.md`.
2. **Direct from the page** (dev shortcut, Chrome on localhost): in the MIDI mapping section pick the port under "MIDI out from this page" and press Test note. Same translation, no extra process, simulator only.

## Run it natively (no Docker)

```
python -m venv .venv
.venv\Scripts\activate                 # Windows;  source .venv/bin/activate on Pi/Linux
pip install -r requirements-dev.txt -e clients/python
pytest                                 # starts a pure-Python broker (amqtt) by itself; no mosquitto needed
```

Against a running broker (Docker, the Pi, or `mosquitto -c bus/mosquitto.dev.conf` for a local dev broker), the same tools work with `--host`:

```
python tools/fake_beat.py --host localhost --bpm 124
python booth/mapping_engine.py --host localhost
python sim/devices.py --host localhost
python sim/inputs.py --host localhost staff staff_01 slam 0.9
python sim/inputs.py --host localhost run sim/scenarios/smoke.yaml
python tools/spy.py --host localhost --no-energy
```

Broker host defaults to `campbus.local`; override with `CAMPBUS_HOST=localhost` (env) or `--host`. `CAMPBUS_HOST=localhost pytest` runs the test suite against a real broker instead of the in-process one.

## Writing a scenario

A scenario fires inputs and asserts what must (and must not) appear on the bus within a window. See `sim/scenarios/smoke.yaml` and the docstring in `sim/inputs.py`. Use one per workflow you care about ("Friday night set change", "food call after midnight"); they double as the acceptance tests for the real devices later, since the mocks subscribe to exactly what the real ones will.

## Node naming

Every device on the bus has a `node_id`: lowercase snake_case with a type prefix, e.g. `crystal_03`, `toy_big_red_button`, `display_entrance_left`, `booth`. It is the MQTT client id and the key under `node/<id>/...`.

## Rules that never bend

1. Audio tap is a **spare** mixer output. The PA chain is never on any Pi.
2. Bus is opt-in. Every piece runs standalone; a dead broker breaks nothing.
3. Publishers never write to `cmd/`. Only the mapping engine does.
4. Offline-first. Anything needing internet is fire-and-forget garnish.
