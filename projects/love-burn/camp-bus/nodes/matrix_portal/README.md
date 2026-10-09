# matrix_portal — an RGB matrix panel as a camp bus output device

An Adafruit **Matrix Portal M4** driving a HUB75 panel, answering `cmd/matrix/#` and
`cmd/all/#` like any other responder. It is the first *output* device on this bus that
is not simulated — the staff Pi proved a real input, this proves a real output.

What it does: **be a colour**, and **fill from any of eight directions**.

---

## The one thing that needs Ben's approval

The target name. The topic shape is the contract's own (`cmd/<target>/<effect>`) and
every effect it implements is already in SCHEMA.md — but `matrix` is not yet in the
list of targets, and this project does not invent contract. The proposed edit is one
line in `docs/SCHEMA.md`, in the responder paragraph:

> Targets so far: `crystals` …, `plant`, `tubes`, **`matrix` (an RGB matrix panel:
> `wipe`/`sweep` fill it directionally from `heading`, `rainbow` walks a hue round its
> zones, plus `flash`/`pulse`/`color`/`charge`/`twinkle`)**, `resolume`, `kitchen`,
> `all`.

One retained topic goes with it, following the shape `config/midi/<instrument_id>`
already set:

> `config/zones/<node_id>` (retained) = `{"w": 64, "h": 32, "rle": [[zone, count], …],
> "zones": [{"id": 1, "name": "rock 1"}, …]}` — **which pixels light which rock.**
> Written by the Booth Devices zone editor, read by the panel.

Until that is agreed, the node still runs — `TARGET` in `settings.toml` changes what
it listens to, so it can answer `cmd/crystals/#` instead if you would rather it behave
as one more crystal.

**Nothing else here is new.** No new topic, no new field, no new effect name.

---

## Direction is `heading`, and that is deliberate

`heading` already means "degrees, 0 = north, clockwise" in SCHEMA.md, and it already
says which way a cone cast points. A fill reuses it unchanged:

> **`heading` is the direction the fill travels.** `heading: 0` moves toward north, so
> it enters from the bottom edge. The eight directions are 0, 45, 90 … 315.

Two things fall out of that for free:

* **The staff can aim it.** The staff Pi publishes a real fused compass heading on
  every cast now, so one mapping rule makes the panel fill the way the staff points —
  no new field, no special case.
* **It is not limited to eight.** Headings stay continuous on the bus; the panel
  rounds to the nearest direction it has a pixel map cached for. Eight is a rendering
  choice, and raising it costs only memory.

`PANEL_NORTH` is the compass bearing of the panel's own top edge. Leave it `0` on a
bench and headings are read in panel space (0 = toward the top). Set it once the panel
is bolted to something and bus headings become true compass directions.

## What it answers

| Effect | Payload | What the panel does |
|---|---|---|
| `wipe` | `{color, intensity, heading, seconds}` | **the fill.** Colour arrives from the edge opposite `heading` and sweeps across. `seconds: 0` is an instant fill, which is how you say "just be this colour" |
| `sweep` | `{color, intensity, heading, seconds}` | a band crosses the panel and leaves nothing behind |
| `flash` | `{color, ms, intensity}` | hit the colour, decay back to whatever was underneath |
| `pulse` | `{color, seconds, intensity}` | breathe |
| `color` | `{color, intensity, seconds}` | tint, fading out |
| `charge` | `{color, intensity}` | glow grows **out of the noise peaks** and spreads down the bands as intensity rises, then bleeds off with a 5 s half-life |
| `twinkle` | `{color, intensity, seconds}` | sparkles the small high bands of the texture, each on its own phase |
| `rainbow` | `{color, intensity, seconds}` | **every zone a different hue**, the set walking round the wheel. `seconds` is the time for one turn; `0` holds it still. A standing mode: it stays until a fill takes the panel back or `intensity: 0` |

Anything else is ignored, which is what the contract asks of a responder.

**A fill persists and a `color` does not.** That is not an inconsistency: `color` is
defined in SCHEMA as a tint that fades over `seconds`, and a fill is a fill. A light
that quietly faded out in the middle of the night would be a bug, not an effect.

It also obeys the shared rules, so it behaves like the crystal standing next to it:
`origin`/`speed_mps` delay, the `range_m` falloff to 40%, the perfect-cast exception at
`intensity >= 1.0`, `cutoff_m`, `falloff: none`, `instant`, and `shape: cone`/`spiral`.
Its own position comes from retained `map/<node_id>`, set by dragging it on the camp
map — until then it reacts immediately, which is the documented behaviour for a node
that does not know where it is.

## Zones: the panel as a set of light sources

Behind a pile of rocks a panel is not a picture, it is a set of lamps. A **zone map**
says which pixels light which rock, and once there is one the position bucket stops
meaning "how far along the fill" and starts meaning "which rock" — the same 32 buckets,
the same 256-entry palette, the same per-frame cost. That indirection is the reason
zones were nearly free to add.

**Booth Devices → Edit zones** opens a full-window pixel editor: a brush, a polygon
tool, a grid, and the colours on screen are the ones the panel uses. **Show zones** puts
exactly that picture on the rocks, because a static rainbow (`seconds: 0`) *is* the
debug view — the editor and the show effect are one code path, so what you author is
what you get.

* **Unpainted pixels are zone 0** and stay dark.
* **Polygon**: click the corners, click the first point again (or Enter) to enclose it.
  The fill you are about to make is previewed as the cursor moves. The crossing test is
  half-open, so the outline is stamped as well — otherwise a square drawn corner to
  corner comes back a row and a column short of the corners you clicked.
* **Rotate turns the view, not the map.** For working from behind the panel, or with it
  mounted on its side. What goes on the wire is always panel-native, so the board never
  has to know.
* **Live to panel** publishes as you draw (unretained, throttled to 3/s) so the rock
  lights up while the brush is still on it. **Save to panel** is the one that retains.
* **Save to panel** publishes retained `config/zones/<node>`, so the map survives a
  power cut. Run-length encoded, because zones are blobs: five hand-drawn rocks came
  to 1093 bytes against 2048 raw.
* **Up to 30 rocks.** `zone * LEVELS + texture` has to fit in one byte, which gives
  32 ids: 0 is "no rock", 31 is reserved for the calibration probe, 1–30 are rocks.
* While you are drawing, the editor ignores incoming configs — otherwise the retained
  message you just saved comes straight back and overwrites your work.

### Calibrate: ask the rocks, don't guess

A zone map is a claim about the world — *this pixel lights that rock* — and the only
way to check a claim like that is to light the pixel and go and look. **Calibrate**
walks the panel a block at a time, lighting one block white and asking which rock it is
behind; everything about it exists to cut keystrokes, because at one pixel a time a
64×32 panel is 2048 questions.

* **The rocks already worked out stay lit in their own colours** while it asks about
  the next block, because what you are checking at the rocks is *this* one against
  *those*. The probe travels inside the same map under the reserved id 31, which the
  panel paints white.
* **Block size 1/2/4/8 px.** At 8 px the whole panel is 32 questions, at 4 px it is 128.
  Start coarse, then re-run finer — a second pass offers the existing answer first.
* **Stop & save** (<kbd>S</kbd>) ends the walk and retains the map; *Calibrate* then
  picks up where you left off, and *Restart* goes back to the first block. The cursor
  lives in the browser, per panel — the map itself stays on the retained topic, because
  two places claiming to hold the same map is how you end up showing the wrong one.
  Progress is also retained every 20 blocks, so a stray refresh cannot cost an hour of
  answers.
* **It walks in a serpentine**, so the next block is always next to this one and
  <kbd>Enter</kbd> — *same as the last* — is usually right.
* **Zones touching the block are offered first**, most-touching first, on keys 1–9;
  <kbd>N</kbd> starts a new rock, <kbd>0</kbd> means none, <kbd>Esc</kbd> stops.
* **Back** (<kbd>⌫</kbd>, or the button) goes back one block and undoes what it was
  assigned, including a rock it created — so a misread answer costs one keystroke, not
  a restart. It greys out at the first block.
* The probe needs **no new topic or field**: it is a reserved value in the `rle` the
  map already carries. Probe maps go out **unretained** — a probe is a question, not a
  fact about the camp, and must not be what a board finds waiting after a power cut.

**Zone colours step by the golden angle (137.5°), not evenly round the wheel.** Not for
coverage — an even spread is better at that — but because an even spread repaints every
rock each time the count changes, which is unusable while you are adding them one at a
time; and because calibration hands rocks *next to each other* consecutive ids, so under
an even spread the two rocks most easily confused were also the two closest on the
wheel. `rainbow` without a zone map still spreads evenly along the fill axis, where
bucket order is distance and a gradient means something.

## Try it before flashing anything

`preview.py` runs the **same** `effects.py` against the real bus and draws the panel in
a terminal with half-block characters:

```bash
python nodes/matrix_portal/preview.py --host campbus.local
python nodes/matrix_portal/preview.py --host campbus.local --demo      # tour all eight
python nodes/matrix_portal/preview.py --host campbus.local --send wipe color=#00c8ff heading=45
```

This is worth more than it looks. When the panel misbehaves, the terminal tells you
*which half* is wrong: if the preview fills and the panel does not, it is the board;
if neither does, it is a mapping rule. That is a much shorter argument than staring at
LEDs.

## Putting it on the board

**Running on CircuitPython 10.3.1** on a Matrix Portal M4 (2026-09-23).

1. Install CircuitPython 10 (double-tap RESET for `MATRIXBOOT`, copy the `.uf2` on).
   On Windows, copy it with Explorer or PowerShell `Copy-Item` - a Git Bash `cp` was
   silently ignored by the bootloader here: the board rebooted looking fine and was
   still running its old firmware. Always check `boot_out.txt` afterwards.
2. From the **matching** Adafruit library bundle (`10.x-mpy` for CircuitPython 10 -
   `.mpy` files do not work across major versions), copy into `CIRCUITPY/lib/`:
   - `adafruit_esp32spi/`
   - `adafruit_minimqtt/`
   - `adafruit_bus_device/`
   - `adafruit_connection_manager.mpy`
   - `adafruit_ticks.mpy`
   The panel itself needs no library: `rgbmatrix`, `framebufferio` and `displayio` are
   built into CircuitPython for this board. Any older `lib/` from a previous project
   has to go, or its imports will fail against the new firmware.
3. Copy `code.py`, `effects.py` and `campbus_mini.py` to the root of `CIRCUITPY`.
4. Copy `settings.toml.example` to `CIRCUITPY/settings.toml` and fill it in. The WiFi
   lines are optional: if they are missing, `code.py` reads the `secrets.py` that
   older Adafruit projects on the board already use, so the password does not have to
   be duplicated.
5. Watch the serial console. It prints the node id, the broker, the IP it got, and
   `[matrix] running`.

If the board is already running CircuitPython you do not have to reach for the reset
button - from the REPL:

```python
import microcontroller
microcontroller.on_next_reset(microcontroller.RunMode.BOOTLOADER)
microcontroller.reset()
```

**While it is not on the bus the panel breathes dim amber**, over whatever it was last
told to show. A panel that is simply dark tells you nothing at 2 am; this says "I have
power and I do not have a broker" from across camp.

## Driving it from the sim

**Booth Devices** (`sim/devices.html`) has a Matrix panel section: a canvas mirroring
whatever the real panel was told to do (it subscribes to the same `cmd/matrix/#`), a
compass rose of the eight directions, a colour, an effect and an intensity.

> **The rose is a bench test.** It publishes straight to `cmd/`, which nothing else on
> this bus does - the contract says only the mapping engine writes there, and every
> other button in the sim publishes an *input* and lets the engine decide. It is
> labelled as such on the page. It exists because aiming a panel by hand is exactly
> the thing you want while the panel is on a bench and there are no rules yet.

The real path is one rule away: **Routes** (`sim/routes.html`) now lists *Matrix panel*
as an output target with all six effects, and its payload template fills in
`heading: "{heading}"` for a prop automatically - because a prop already carries a
compass heading. Add the rule, hit **Apply to engine**, and the Virtual Camp's existing
staff heading slider aims the panel with nothing new invented.

## A mapping rule, when you want one

Nothing sends `cmd/matrix/*` yet. This is **not** applied to `booth/mappings.yaml` —
rules change what camp does, so they are Ben's to add, through the Routes page or by
hand:

```yaml
  - name: "staff swing -> matrix fills the way the staff points"
    group: Props
    when: { topic: "prop/+", prop: staff, gesture: swing }
    then:
      - { topic: "cmd/matrix/wipe", payload: { color: "{color}", intensity: "{intensity}",
          heading: "{heading}", seconds: 0.8, from: "{prop_id}",
          origin: { lat: "{lat}", lon: "{lon}" }, speed_mps: 14, range_m: 60 } }
```

Until then, test it with `preview.py --send`, or straight from the broker:

```bash
mosquitto_pub -h campbus.local -t cmd/matrix/wipe \
  -m '{"color":"#ff4000","intensity":1.0,"heading":135,"seconds":1.0}'
```

## How it draws a fill without melting a SAMD51

A 64x32 panel is 2048 pixels, and CircuitPython cannot write all of them every frame
*and* answer MQTT. So the panel is drawn as a gradient ramp:

* every pixel is assigned, once, a 32-bucket index for how far along the fill axis it
  sits, and that goes into a `displayio.Bitmap`
* animating the fill is then just rewriting the **32-entry palette**

About sixty times less work per frame, and the bucketing gives a soft leading edge for
free. The pixel maps only change when the *direction* does, and there are only eight,
so they are cached — and built one at a time while the panel is idle, so neither boot
nor the first fill from a new direction stalls. If the board runs out of memory it
falls back to keeping one map and rebuilding it, rather than crashing.

`effects.py` holds all of this and imports nothing from CircuitPython, which is why it
can be unit-tested on a laptop and previewed in a terminal.

## What has and has not been proven

**Proven** (`tests/test_matrix_effects.py`, 33 tests): every fill direction starts from
the correct edge and uses the whole ramp; the eight are distinct; opposite headings
mirror; colour parsing survives malformed input; the contract's mixing rule; the
propagation delay, falloff, perfect-cast exception, cutoff and cone test; fills
persisting and sweeps not; flash decaying back to what was underneath; charge bleeding
off; unknown effects ignored; a delayed cast not lighting the panel early. Plus an
end-to-end test **through a real broker**: a panel node subscribing exactly as `code.py`
does receives `cmd/matrix/wipe`, paints the right colour, and picks the right direction.

**Proven on the board** (2026-09-23, CircuitPython 10.3.1, 64x32 panel): it joins
WiFi through the ESP32 co-processor, appears on the bus as a retained
`node/matrix_01/status` with its IP and firmware, receives `cmd/matrix/wipe` from the
broker, counts each one on `node/matrix_01/count`, and stays online across a run of
commands. All eight fill directions, `sweep`, `flash`, `pulse` and an instant black
fill were sent to it.

**Measured on the board**, because a 256-entry palette was the whole risk of carrying
the texture — the panel prints its own frame rate every five seconds:

| | fps | where it goes |
|---|---|---|
| idle | 16 | 58% is a deliberate 50 ms sleep |
| directional fill | ~23 | |
| rainbow cycle, 5 zones | ~16 | bus 42%, paint 50% |

**Not proven:** memory headroom over a long run with every pixel map cached. That needs
it left running, not a demo.

## Gotchas

- **`NODE_ID` is the MQTT client id.** Two panels sharing one will kick each other off
  the broker in a loop. Same rule as every other node on this bus.
- **Address lines are derived from `PANEL_HEIGHT`** (16 rows → A-C, 32 → A-D, 64 →
  A-E). If the panel draws half an image twice, that is the number being wrong.
- **`PANEL_BIT_DEPTH` is the first thing to lower** on a `MemoryError`, before anything
  else.
- **No auth**, like everything else on this LAN.
- **`socket_timeout` is 0.05 s and that number was bought twice.** At 0.002 the panel
  dropped off the broker and sat reconnecting, which on the bus is indistinguishable
  from a node that is gone. At 0.01 small messages were fine but a 792-byte retained
  zone map raised `ETIMEDOUT` mid-read and took the connection down with it — short
  payloads arrive in one packet, which hid it for a long time. The frame cost is paid
  back by polling every fourth frame instead of every one.
- **`auto_refresh=False`, and one `display.refresh()` per frame.** With it on, each
  palette write can drag a refresh behind it: 256 writes measured **230 ms**. The same
  frame with auto-refresh off is 4 ms. The matrix hardware scans continuously either
  way; this only decides when displayio pushes at it.
- **The texture bands are equalised** (`noise_field`). Stacked Perlin octaves are
  roughly Gaussian, so a linear mapping put 1 pixel in the top band — which would have
  left `twinkle` sparkling a single LED.
- **Two ESP32SPI API names changed** under CircuitPython 10 and cost a crash each:
  `esp.rssi` is now `esp.ap_info.rssi`, and `esp.ip_address` (raw bytes) is now
  `esp.ipv4_address`. `code.py` asks for whichever exists rather than assuming a
  bundle version. If you port this to another airlift board, that is the first thing
  to check - and the REPL will tell you: `dir(esp)`.
- **`minimqtt.loop(timeout)` must be >= the client's `socket_timeout`**, which
  defaults to 0.1 s. Passing 0 makes *every* call raise, which on the bus looks
  exactly like a clean disconnect - the only symptom is the last will firing. The
  client is built with `socket_timeout=0.01` so the panel can poll at animation
  speed, and `campbus_mini` now prints the first few errors instead of swallowing
  them, which is what made this invisible the first time.
