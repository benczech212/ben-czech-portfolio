# Crystal Geode Synth

Brass wireframe geodes you hold to play a synth. A camp member's concept (#crystals, 2026-09-20).

## Physical

- Soldered **brass wireframe** geode with holographic-film poly facets, epoxy-fixed.
- Hidden **COB RGBW** inside, lighting on touch — and a bus responder in its own right.
- **One-wire capacitive circuit**: the brass frame is the antenna. Multiple geodes played together are a
  chord.

Four geodes:

| Pad | Note (ch 2) | Colour | Key in the sim |
|---|---|---|---|
| `amethyst` | 60 | `#b04dff` | Q |
| `citrine` | 64 | `#ffc83d` | W |
| `aquamarine` | 67 | `#4de0ff` | E |
| `rose_quartz` | 71 | `#ff7ab8` | R |

## Contract

Three events. `touch` once on contact, `hold` at ~8 Hz while held (throttle it in the mapping), `release`
once at the end with `held_ms`:

```json
{"instrument":"geode","instrument_id":"geode_01","pad":"aquamarine","pad_i":2,"event":"touch",
 "velocity":0.62,"value":0.62,"raw":1798,"baseline":1240,"note":67,"ch":2,"color":"#4de0ff",
 "lat":25.73675,"lon":-80.15453,"acc_m":2,"ts":0}
```

- **`velocity`** = the normalised coupling at contact. **`value`** = the live reading while held.
  **`raw`** / **`baseline`** = sensor counts and the pad's untouched baseline, which the bridge re-learns
  slowly while nobody is touching.
- Readings differ by person — body size, footwear and grounding, skin moisture, hand area, whether two
  people are touching each other. That is a feature, not noise; see `../02-feature-intent.md`.
- **Chord mode** (Ben, 2026-09-21): a switch on the Instruments page puts one chord under each geode. The
  message then carries `notes: [60, 64, 67, 71]` with `note` as the root, and the retained
  `config/midi/geode_01` carries `mode: "chord"`, `chord_preset` and per-pad `notes`.

**Subscribes:** `map/geode_01`, `config/midi/geode_01`, `cmd/geode/#` + `cmd/all/#` (the COB inside each
geode: `flash` / `pulse` / `color` / `charge`).

**Default routes** (group `Geodes`): `touch` tints the 3 nearest crystals in the geode's colour for 5 s and
rains the hallway; `hold` keeps charging those crystals with the live `value` (throttled 250 ms); `release`
does nothing.

**MIDI:** channel 2, `touch` = CC 74 (the reading) then note on, `hold` = CC 74, `release` = CC 74 to 0 then
note off. Ableton track 2 wants a polyphonic sustaining patch with a filter on CC 74.

## Simulated today

`sim/instruments.html` panel 2 — press and hold a geode (Q W E R), with a **"who is touching"** selector
(barefoot adult / adult in shoes / kid / two people holding hands / gloved fingertip) that changes the
coupling exactly as the real sensor would. The reading bar and the `raw` counts are drawn live.
CLI: `docker compose run --rm input touch aquamarine 0.7` (add `event: hold` / `release`).

## Prototyping it

1. **One pad on a bench.** ESP32 `touchRead()` or an MPR121 against a scrap of brass; print raw counts with
   a finger on and off, standing on carpet and barefoot. You are looking for a stable untouched baseline and
   a clear delta.
2. **Normalise, don't threshold.** Track a slow-moving baseline while untouched (e.g. an EMA that only
   updates when no touch is active), publish `velocity` = normalised delta at contact and `value` = the live
   delta. Send `raw` and `baseline` too — they are what makes a bad pad debuggable at camp.
3. **Rate-limit `hold` to ~8 Hz** at the source. This is the one topic that can flood the bus.
4. **Always send `release`.** A missed release leaves a note hanging in Ableton and a crystal charging
   forever. Send one on touch-loss *and* on a watchdog timeout.
5. Grounding caveat (build team, 2026-09-14): plain capacitive needs no grounding scheme, Playtronica-style gear
   does. Prototype on the plain-capacitive path unless a member's Playtron devices turn up.

Open: whether the geodes share one ESP32 or one each, and whether the COB lives on the same board.
