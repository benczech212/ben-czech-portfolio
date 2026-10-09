# Sonic Mycelium Cluster

Whackable mushroom sculptures that play drums. Named by a camp member, 2026-09-21. Concept: one camp member (the
e-drum-brain trick) + another (the castings).

## Physical

- Fluorescent-pigment silicone/rubber castings, shaped per species; a **piezo contact under each cap**.
- A **UV COB LED bed** under each mushroom, which is also a bus responder — the camp can light the
  mushrooms back.
- The piezos go into a **~$100 e-drum brain** (a camp member's trick) rather than custom electronics. Whatever
  hosts that brain — a Pi reading its USB-MIDI out is enough — is the bridge that publishes to the bus.

Six mushrooms, one drum voice each:

| Pad | Species | Drum | Note (ch 1) | Colour |
|---|---|---|---|---|
| `amanita` | fly agaric | kick | 36 | `#ff3b3b` |
| `chanterelle` | chanterelle | snare | 38 | `#ffb300` |
| `indigo_milk` | indigo milk cap | closed hat | 42 | `#3d7bff` |
| `ghost` | ghost fungus | tom | 45 | `#4dffa6` |
| `violet_coral` | violet coral | clap | 39 | `#b04dff` |
| `pink_oyster` | pink oyster | ride | 51 | `#ff4da6` |

## Contract

**Publishes** one message per whack:

```json
{"instrument":"mycelium","instrument_id":"mycelium_01","pad":"amanita","pad_i":0,"event":"hit",
 "velocity":0.8,"note":36,"ch":1,"color":"#ff3b3b","lat":25.73666,"lon":-80.1546,"acc_m":2,"ts":0}
```

`velocity` is 0.0–1.0 (the brain's MIDI velocity / 127). Position comes from its own retained
`map/mycelium_01` — the bridge stamps it on every hit, so a whack ripples out from where the cluster stands.

**Subscribes:** `map/mycelium_01`, `config/midi/mycelium_01` (retained MIDI table — note/channel per pad,
written by the Instruments page), `cmd/mycelium/#` and `cmd/all/#` for the UV beds
(`flash` / `pulse` / `color` / `charge`, every bed at once, same colour-mix and propagation rules as a crystal).

**Default routes** (`booth/mappings.yaml`, group `Mycelium`): any hit flashes the 4 nearest crystals in that
mushroom's colour (`nearest: 4`, `instant: true`, `combo: false`); the kick also breathes the hallway; the
snare rings it.

**MIDI:** channel 1. The bridge turns `hit` into CC 11 = velocity, note on, note off after a 120 ms gate.
Ableton track 1 is a pitched drum rack, so each mushroom's note is a different sound.

## Simulated today

`sim/instruments.html` panel 1 — tap a mushroom (keys 1–6) at the velocity slider, or pull it and let go
(the further you pull, the harder it hits). Test patterns play it against the shared bar clock. The UV bed
under each mushroom renders `cmd/` effects. CLI: `docker compose run --rm input hit amanita 0.9`.
Mock responder: `MockMycelium` in `sim/devices.py`.

## Prototyping it

1. **Bench a single pad first.** One piezo + a 1 MΩ resistor and clamp diodes into any e-drum brain input
   (or an ESP32 ADC if you skip the brain), and confirm you get a clean velocity from a soft and a hard hit.
2. **Bridge it.** On the host with the brain: read MIDI note-ons (`mido`), map note → pad name, publish the
   payload above with `campbus.Node`. ~30 lines. Subscribe to `config/midi/mycelium_01` and let it override
   your note table — the Instruments page is the source of truth.
3. **Prove it against the virtual camp.** With `docker compose --profile sim up`, hitting a real pad should
   light the virtual crystals. That is the acceptance test, and it works before any crystal exists.
4. **Then the beds.** An ESP32 (or the same Pi) subscribing `cmd/mycelium/#` + `cmd/all/#`, driving the UV
   COBs, honouring `color`/`intensity` mixing and `origin`/`speed_mps` delay.

Open: how many mushrooms actually get built, and whether the beds share one controller or one per mushroom.
