# Water Chord Tubes

Water cylinders tuned as one chord; touch the water to play a note. A camp member's concept (2026-09-14);
**three tubes** on Ben's call (2026-09-21) — the original concept said about five.

## Physical

- Sealed acrylic/glass cylinders holding water at different levels, tuned as one chord.
- An **RGB puck under each base** lighting the column; it is a bus responder (`cmd/tubes/*`).
- A **wire in the water** makes the whole column the capacitive electrode. Hand-to-water is a far larger and
  steadier contact than the geodes' brass frame — the reading is bigger, quicker to settle, and wanders less.

| Pad | Note (ch 4) | Colour | Water level | Key in the sim |
|---|---|---|---|---|
| `tube_1` | 48 | `#4de0ff` | high | Z |
| `tube_2` | 52 | `#3d9bff` | mid | X |
| `tube_3` | 55 | `#7cffd4` | low | C |

## Contract

Identical in shape to the geodes — `touch` / `hold` (~8 Hz) / `release`, with `raw` + `baseline` — because
it is the same class of sensor:

```json
{"instrument":"tubes","instrument_id":"tubes_01","pad":"tube_2","pad_i":1,"event":"touch",
 "velocity":0.75,"value":0.75,"raw":2105,"baseline":980,"note":52,"ch":4,"color":"#3d9bff",
 "lat":25.73657,"lon":-80.15456,"acc_m":2,"ts":0}
```

The simulator models the bigger electrode as a lower baseline and a wider span (980 / 1500 counts, against
the geodes' 1240 / 900) and a ~0.25 s settle. Match that with real numbers once a tube exists.

**Subscribes:** `map/tubes_01`, `config/midi/tubes_01`, `cmd/tubes/#` + `cmd/all/#` (the base pucks:
`flash` / `pulse` / `color` / `charge`).

**Default routes** (group `Tubes`): `touch` is a ripple — the 3 nearest crystals pulse in that tube's colour
for 4 s (`nearest: 3`, `instant: true`, `combo: false`) and three rings run down the hallway; `hold` keeps
charging those crystals with the live water reading (throttled 250 ms); `release` does nothing.

**MIDI:** channel 4, held like the geodes (`touch` = note on, held until `release`; `hold` = CC 74).
**Ableton track 4 currently has no instrument loaded** — it wants a polyphonic sustaining patch with a
filter on CC 74. That is the one open item on the Ableton side.

## Simulated today

`sim/instruments.html` panel 4 — click into a tube or hold Z X C; the water surface wobbles, ripples ring
out on touch and release, the finger sinks as deep as the reading is strong, and the base puck lights.
Shares the "who is touching" coupling model with the geodes. Test voice is a glass harmonica.
CLI: `docker compose run --rm input dip tube_2 0.8` (add `event: hold` / `release`).

## Prototyping it

1. **One tube, one wire.** A stainless wire or foil electrode in the water, an ESP32 `touchRead()` or MPR121
   channel, and a sealed lid. Log counts untouched, one finger in, whole hand in, and with someone else
   holding your other hand.
2. **Sealing and safety come before electronics.** It is a water vessel in a beach camp: no mains anywhere
   near it, low voltage only, and it has to survive being knocked over.
3. **Same normalisation as the geodes** (baseline EMA while untouched, delta as `velocity`/`value`), rate-limit
   `hold` to ~8 Hz, and always send `release` — including on a watchdog timeout.
4. **Tune the chord physically if you can** (water level), but the note the bus sends comes from
   `config/midi/tubes_01`, so the two must agree or it will sound wrong against the rest of the camp.
5. Drive the base puck from `cmd/tubes/*` on the same board.

Open: final tube count (three today), the chord itself (C major 48/52/55 by default), and whether the pucks
share a controller with the electrode board.
