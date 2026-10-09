# Magic Crystals

The camp's scattered light responders, and the pieces the Paint the Camp game is played on. A camp member's concept
(**30×**, 2026-09-20).

## Physical

- Unique geode shells (3D printed, or cast from printed masters), semi-sealed and weather-resistant,
  fluorescent material.
- Each: **IC + battery + speaker + RGB filament**. In the bus model each crystal is **2 diffused RGB LEDs**
  glowing into one body.
- Networked behaviours over camp WiFi: resonate when assembled, go dark when separated, react to flow toys
  and the MIDI instruments, several group games, hidden around camp to be found.
- Anti-theft: screech mode at the camp boundary.

The simulator runs **20** by default (configurable up to 60) because that is a realistic camp-night count
of crystals actually placed and powered.

## Contract

**Subscribes:** `cmd/crystals/#`, `cmd/crystal_<nn>/#` (one crystal), `cmd/all/#`, plus `audio/beat`,
`audio/energy`, `camp/mode`, `camp/sync`, `vibe/params`, and its own `map/crystal_<nn>`.

**Effects it must implement:** `flash` `{color, ms, intensity}`, `pulse` `{color, seconds, intensity}`,
`color` `{color, seconds, intensity}`, `charge` `{color, intensity}`, `mode` `{mode}` (the vibe palette).
Ignore anything else.

**Publishes:** `node/<id>/status`, `node/<id>/count`, and during a game
`crystal/<id>/paint` = `{"shares": {"staff_01": 2.4}, "total": 3.3}` retained at 1 Hz.

**Every responder rule lives here.** A crystal is the reference implementation of the whole responder
contract, so it is the piece to build first:

- **Colour mixing:** mix the payload's `color` into what you are showing with weight = `intensity`.
- **Propagation:** delay by `distance(origin, me) / speed_mps`; scale by `1 - 0.6 * min(1, d / range_m)`;
  `intensity >= 1.0` or `falloff: none` waives the falloff; `cutoff_m` is a hard edge; `shape` (`circle` /
  `cone` / `spiral`) uses the bearing from `origin` to you.
- **`nearest: N`**: only the N crystals closest to `origin` react — which means a crystal must be able to
  answer "am I in the nearest N", i.e. it needs the other crystals' positions, or the booth resolves it.
  *(In the simulator the camp page resolves `nearest` centrally; on real hardware this is an open design
  point — see below.)*
- **`instant: true`**: no propagation delay.
- **Stain / rhythm / combo**: the three memory rules in `../02-feature-intent.md`, all computed locally.
- **Paint ledger**: accumulate `intensity * paint` per `from`, publish at 1 Hz during a game.

## Simulated today

`sim/camp.html` — the crystal row is a firmware-shaped effect engine: 2 LEDs per crystal, near-dark resting
breath in `idle`, full propagation/shape/falloff maths, stain, rhythm lock, combos, the paint ledgers and the
game's lock-in climax. It is the reference implementation; port *from* it.

## Prototyping it

1. **One crystal, one ESP32.** Copy `clients/esp32/campbus_template/`, keep the beat-flash subscription,
   add `cmd/crystals/#` + `cmd/all/#` and two WS2812-class pixels. Implement `flash` and `color` only.
2. **Add the position.** Subscribe your own `map/crystal_01`, store `lat`/`lon`, and implement the
   propagation delay + falloff. Drag the node on the Virtual Camp map and watch your real crystal answer a
   simulated staff slam late and dim. That single test proves most of the contract.
3. **Then the memory rules** in this order: stain (easy, one decaying colour), rhythm (four timestamps per
   `from`), combo (last hit per `from` + a 500 ms window).
4. **Then the game**: the paint ledger and `crystal/<id>/paint`.
5. **Battery and sleep last.** A crystal is a pure responder, so it stays awake; budget for that or accept
   a nightly recharge.

Open: how `nearest: N` is resolved on real hardware (each crystal knowing every other crystal's `map/`
versus the booth resolving it into per-crystal commands); speaker behaviour and the screech mode are
unspecified on the bus so far.
