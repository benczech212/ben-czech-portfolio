# What each feature is *for*

The contract in `../docs/SCHEMA.md` says what the fields are. This says what they are trying to make a
person at camp feel, and which rule produces that feeling — so that when you change something you can tell
whether you broke the intent or just the implementation. Decisions are attributed and dated because most of
them were made by Ben in a specific session and should not be re-litigated without him.

## Casts: a prop throws light across camp

**Intent:** swinging a staff should not "turn on a light". It should send something *across camp* that
arrives later the farther away you are, like a wave.

- Every prop publishes the same payload on `prop/<prop_id>`: type, `gesture`, `intensity` 0–1, its current
  `color`, `heading` from the IMU, `spin_dir`, and GPS when it has a fix.
- The mapping engine turns one gesture into one or more `cmd/` effects carrying `origin`, `speed_mps`,
  `range_m`, and the shape keys.
- Each responder delays itself by `distance / speed_mps` and scales its own intensity. No central timeline.

**Falloff is deliberately gentle** (Ben, 2026-09-20): `1 - 0.6 * min(1, d / range_m)` — a 40% floor, never
fully out of range. Early versions let distant crystals do nothing, and camp felt dead on the edges.

**Perfect cast** (Ben, 2026-09-20): `intensity >= 1.0` waives distance falloff entirely — every node in camp
answers at full strength. The intensity slider maxing out at exactly 1.00 is the reward for a clean cast.

**Shapes** (Ben, 2026-09-20) make gestures readable from across camp:

| Shape | Feels like | Rule |
|---|---|---|
| `circle` | a shockwave | everyone in range, delay by distance |
| `cone` | "everything I point at" | only `abs(angle) <= cone_deg/2`, brightest on the centre line |
| `spiral` | a radar sweep around the caster | delay is **rotation only** — near and far light at the same angle |

**`cutoff_m`** is a hard edge for deliberately local effects (the wand's orb, the drum roll); without it the
40% floor means the whole camp reacts a little. **`falloff: none`** keeps intensity flat with distance (the
staff spin hits every crystal).

## Fixed instruments answer instantly and locally

**Intent:** a drum pattern on the mushrooms should lock the crystals *around the mushrooms* to its tempo —
not send a wave across camp every eighth note.

`nearest: N` + `instant: true` (Ben, 2026-09-21) on every instrument route: only the N crystals closest to
the instrument react, at full strength, with no propagation delay. Props still ripple; instruments are
immediate and local. This is also why instruments carry a `map/<instrument_id>` position at all: a whack
ripples out *from where the sculpture stands*.

## Memory: stain, rhythm, combo

Three rules that make the camp feel like it remembers people, all computed **inside each responder** so they
survive a flaky booth.

- **Stain** (Ben, 2026-09-20): any coloured effect leaves that colour in the crystal's idle colour with a
  ~60 s half-life. Someone who painted a crystal red can walk away and still see it tinted.
- **Rhythm** (Ben, 2026-09-20): four hits from the same `from`, evenly spaced (0.25–2 s, each within 15% of
  the mean), lock a tempo — the node pulses in that colour at that BPM for a few seconds. Drumming at a
  crystal makes it drum back.
- **Combo** (Ben, 2026-09-20): when a hit from a *different* prop lands within 500 ms of the last one, the
  responder plays a two-colour combo animation. Two people casting together get something neither gets alone.

**`combo: false`** on instrument routes (2026-09-21) keeps instruments out of the combo rule: the mushrooms,
geodes, plant and tubes are *played together all the time*, so two of them on one crystal is normal, not a
special event. They still count for rhythm, stain and paint.

## Paint the Camp

**Intent:** a game anyone can join by doing the thing they were already doing (casting), with a finish that
reads from across camp without strobing anyone.

Phases: **lobby** (8 s; your first cast claims a colour, a taken colour rolls to the next free swatch) →
**play** (default 45 s; every cast deposits energy on the crystals it reaches, weighted by the route's
`paint` value) → **climax** → **victory**.

The climax was replaced on Ben's call (2026-09-20): **no strobe, no flash**. Crystals lock one at a time
into their leading colour — most-decided first, contested last, ~0.6 s each — then victory is the winner's
colour breathing on every crystal in synchrony for 20 s. The tension comes from the order of the locks.

Crystals keep their own paint ledgers (`crystal/<id>/paint`, retained at 1 Hz) and the booth only scores
them, so the game survives the master dying mid-round.

**`tools/balance.py`** exists because gestures are not equally good at covering camp: it Monte-Carlos random
prop positions and headings over a 20-crystal camp, measures energy per cast per gesture, and `--apply`
rewrites the `paint` weights in `mappings.yaml` so every gesture is worth about the same (target 2.5). A
whip crack reaches almost nothing, so it is worth 8.0 per crystal; a drum hit reaches everything, so it is
worth 0.19.

## Camp mode and vibes

**Camp mode** (Ben, 2026-09-20) is the camp's operating state, retained on `camp/mode`:

- `idle` — no music, no BPM. The audio source publishes nothing, crystals rest near-dark with a faint
  breath, and **only casts light them**. The simulator boots into `idle` on purpose: a clean slate, so the
  first thing you see is something *you* did.
- `show` — normal. `quiet` — quiet hours.

**Vibes** are the palette/animation personality (`cozy`, `party`, `chaos`, `wave`, `jumping`, `ethereal`,
plus `snake`, `twinkle`, `heartbeat`, `aurora`, `meteor` in the sim), broadcast as `cmd/all/mode`. Two
retained switches shape them: `camp/sync.bpm_sync` makes every cycle rate follow the BPM instead of a
free-running period, and `camp/sync.pulse` allows beat flashes. `vibe/params` (retained) holds the tunable
numbers per vibe, so the look can be tuned live from a UI instead of by reflashing nodes.

## The touch instruments

Four sculptures people play, all publishing the same `instrument/<instrument_id>` shape and all reaching
Ableton through one MIDI port.

**Velocity on a capacitive pad is not a switch** (Ben's question, 2026-09-21). A one-wire sensor reads how
well a body couples to the electrode, and that genuinely differs by person and situation — body size,
footwear and grounding (barefoot on damp sand reads far higher than trainers on a mat), skin moisture, how
much of the hand is on the frame, whether two people are touching each other. So the bridge normalises each
pad against its own slowly re-learned baseline and publishes the delta: `velocity` at contact, `value` while
held, with `raw` and `baseline` alongside. Consumers get a free "how much of you is on it" parameter, and
the simulator has a "who is touching" selector so you can feel the difference.

**One channel per instrument** (sequential: mycelium 1, geode 2, plant 3, tubes 4 — changed from 10/1/2/3 on
2026-09-21) because a DAW on one port can only tell instruments apart by channel. The Instruments page flags
a shared channel or a duplicate note in red and the bridge logs a WARNING, since that failure is silent and
maddening otherwise.

**The MIDI table lives on the bus**, retained at `config/midi/<instrument_id>`: note and channel per pad,
plus the preset name it came from. The Instruments page writes it, `tools/midi_bridge.py` and the real
instrument bridges read it. Switching the camp into A minor is one dropdown, not a re-flash.

**CC 11 before every note on** (2026-09-21): whether a harder hit is louder is up to the patch — some route
velocity to volume, some ignore it, and a nested rack macro can't be reached from outside. So the bridge
sends expression = velocity right before each note on, and each Live track has a Utility whose Gain is
mapped to CC 11 (−18 dB … 0 dB). "Harder is louder" becomes true for any patch. **CC 74** separately carries
the continuous reading (touch/hold/bend) to a filter, per channel, so instruments never fight over it.

**Chord mode** (Ben, 2026-09-21): a pad may carry `notes: [...]` and the bridge plays all of them; `note`
stays the root. The geodes have a single-note/chord switch with presets; the tubes are chord-shaped by
nature — three tubes tuned as one chord.

**Event shape follows the physics of the sensor**, which is why the four instruments differ:

| Instrument | Sensor | Events | Why |
|---|---|---|---|
| Sonic Mycelium Cluster | piezo under each cap | `hit` | a whack is one impulse: note on, gated off |
| Crystal Geode Synth | one-wire capacitive, brass frame | `touch` / `hold` / `release` | you hold a geode; the note sustains and the reading rides along |
| Monstera Piezo Plant | piezo *tape* (reads strain rate) | `bend` / `release` / small `hit` | the tape reads the bend, and the **snap back** is the loud part — so release plays the note |
| Water Chord Tubes | capacitive, the water is the electrode | `touch` / `hold` / `release` | like a geode, but a hand in water is a much larger, steadier contact |

**Test patterns share one transport** (2026-09-21): the first pattern you start sets the bar clock and
anything started later waits for the next bar line, so whatever you add lands in time with what is playing.
The page is a rehearsal rig for the booth set, not a toy.

## Routes: re-routing the camp from a phone

The engine publishes its live rule list (`mapping/rules`, retained) and accepts a replacement
(`mapping/set`). The Routes page renders every input device, every target and every rule from that, and
writes edits back; the engine validates, rewrites `mappings.yaml` in a hand-editable form, reloads, and
answers. So the YAML on the Pi and the UI are the same thing, and a bad edit is rejected rather than
half-applied.

## Displays

`display/banner` is retained, which is what you want for "FOOD'S UP" (a screen that boots late still shows
it) and dangerous for the same reason: a takeover with no `until` owns the screen forever. The visual
simulator auto-clears a takeover after 45 s; **the real kitchen node must publish `until` or clear the
banner.** This is a known open item, not a solved one.

## Test sounds

The browser simulators synthesise their own audio (drums, a pad voice for the geodes, wind-and-drone for the
plant, glass harmonica for the tubes) so you can rehearse a set with nothing built. They are garnish: sound
calls are guarded so a missing voice is silence, never a broken frame. The real instruments make no sound in
the browser — Ableton does.
