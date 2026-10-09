# Monstera Piezo Plant

A bass instrument you play by bending leaves. A camp member's concept (#plants-midi-bass, 2026-09-20).

## Physical

- Silicone/rubber **leaves** with flexible **LED filament** and **piezo sensor tape** cast in; a
  copper/steel wire skeleton holds the leaf shape.
- Each leaf stem ends in a **TRRS jack** (1&2 = piezo, 3&4 = the mono LED filament) plugging into ports
  around the trunk. There are **more ports than leaves**, and a port maps to a bass synth patch — plug a
  leaf in to choose its voice, flex it to sculpt parameters.

**Five leaves** (Ben, 2026-09-21; was four). The fifth is the pale young one, so the default preset is a
Cmaj9 across the plant:

| Pad | Note (ch 3) | Colour | Key in the sim |
|---|---|---|---|
| `leaf_1` | 36 | `#3ddc84` | A |
| `leaf_2` | 40 | `#8fff5a` | S |
| `leaf_3` | 43 | `#1fb6a6` | D |
| `leaf_4` | 47 | `#b8ff8a` | F |
| `leaf_5` | 50 | `#d8ff3d` | G |

## Contract

Piezo **tape** reads strain *rate*, not position, so the event shape is different from the geodes:

| Event | When | Fields |
|---|---|---|
| `bend` | ~8 Hz while a leaf is being worked | `value` = the flex 0–1 |
| `release` | once, when it springs back | `velocity` = the spike — **this is the note** |
| `hit` | a light tap | small `velocity` |

```json
{"instrument":"plant","instrument_id":"plant_01","pad":"leaf_2","pad_i":1,"event":"release",
 "velocity":0.85,"value":0,"note":40,"ch":3,"color":"#8fff5a","lat":25.73659,"lon":-80.15466,"ts":0}
```

**Subscribes:** `map/plant_01`, `config/midi/plant_01`, `cmd/plant/#` + `cmd/all/#` (the LED filaments in
the leaves).

**Default routes** (group `Plant`): `bend` tints the 3 nearest crystals with the live flex (throttled
250 ms); `release` sends a slow bass wave — everything pulses for 2.5 s scaled by the snap.

**MIDI:** channel 3. `bend` = CC 74 (filter), `release` = the note at the spike velocity, gated off after
the bridge's gate time (`--gate`, 120 ms by default). Ableton track 3 is a rhythmic rack, so a snapped leaf starts a pulsing pattern for the gate time.

## Simulated today

`sim/instruments.html` panel 3 — grab a leaf with the mouse and pull it (or hold A S D F G); the readout
shows the live flex and what the snap will be worth. Test sound is wind-through-leaves over a soft drone.
CLI: `docker compose run --rm input bend leaf_2 0.85` (or `event: bend` with a `value`).

## Prototyping it

1. **One leaf, one tape.** Piezo tape into an ESP32 ADC through a 1 MΩ bleed resistor and clamp diodes.
   Watch the raw signal while you bend slowly and then let go: a slow bend is a small sustained offset, the
   snap is a large fast spike. Those are your two features.
2. **Publish the shape, not the raw signal.** `bend` at 8 Hz with a smoothed magnitude as `value`; a
   `release` with the peak of the spike as `velocity` when the signal crosses back; a `hit` for a spike
   with no preceding bend.
3. **Decide the leaf-to-port mapping in firmware** and publish the resulting `pad`; the bus does not care
   which physical port a leaf is in, only which pad name you claim. The retained `config/midi/plant_01`
   then decides the note.
4. Drive the leaf's LED filament from `cmd/plant/*` on the same board.

Open: how many leaves are actually cast, and whether the trunk hosts one board for all leaves (simplest —
one `instrument_id`, five pads) or one per leaf.
