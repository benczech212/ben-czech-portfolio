# Props — staff, wand, drum, whip

Handheld pieces people carry around camp and cast with. The input side of every cast, the game and the
combo/rhythm rules.

## Physical

- ESP32 in each prop, an **IMU** for gestures and heading, a **GPS** for position, an RGB element showing
  the prop's current colour, a battery.
- They talk **ESP-NOW** (infra-free, ~100 m, and `rssi` at the receiver is crude proximity), and a
  **Pi Zero 2 W bridge** republishes onto MQTT as `prop/<prop_id>`. That bridge is **not designed yet** (M5).

## Contract

**One payload shape for every prop** (v0.2, 2026-09-20 — the old `staff/<id>` topic is retired; the engine
still listens there during the overlap):

```json
{"prop":"staff","prop_id":"staff_01","gesture":"slam","intensity":0.9,"color":"#ff4000",
 "heading":135,"spin_dir":null,"lat":25.73671,"lon":-80.15479,"acc_m":3,
 "via":"bridge_hallway","rssi":-52,"ts":0}
```

- `intensity` 0.0–1.0, where **1.00 is a perfect cast** (no distance falloff anywhere in camp).
- `heading` degrees, 0 = north, clockwise, from the IMU; `null` if unknown. `spin_dir` ±1 for spins only.
- `lat`/`lon`/`acc_m` when the GPS has a fix; everything still works without one, just with no propagation.
- `via` + `rssi` come from the bridge that heard it.

**Gestures and their default casts** (`booth/mappings.yaml`, group `Props`):

| Prop | Gesture | Cast |
|---|---|---|
| staff | `slam` | circle: hallway shockwave + crystals flash, 6 m/s, 60 m |
| staff | `spin` | spiral: crystals charge sweeping around the caster, one turn per 2 s, no falloff |
| staff | `swing` | cone 70°: tint everything it points at for 6 s, 14 m/s |
| wand | `flick` | bolt: cone 25°, 14 m/s, 80 m, short flash |
| wand | `circle` | orb: pulse within 18 m only (`cutoff_m`) |
| whip | `crack` | needle: cone 12°, 30 m/s, 80 m, flash + hallway rings |
| whip | `lash` | arc: cone 140° within 25 m, 3 s tint + hallway sweep |
| drum | `hit` | heavy wave: everything pulses, 4 m/s, 90 m |
| drum | `roll` | steady charge on everything within 40 m |

Every prop output carries **`from` = `prop_id`**, which is what makes the combo rule (two different props
within 500 ms), the rhythm rule (four evenly spaced hits from one prop) and the game's per-player scoring
possible. Each route also carries a **`paint`** weight; `tools/balance.py --apply` keeps those weights fair
across gestures (a whip crack reaches almost nothing and is worth 8.0 per crystal; a drum hit reaches
everything and is worth 0.19).

Adding a prop or a gesture is **rules only** — no new topic, no responder change.

## Simulated today

`sim/camp.html` **Props panel**: Staff / Wand / Drum / Whip tabs, each with its own colour (six swatches +
custom), intensity slider (1.00 = perfect cast), heading dial, spin direction, and a map position you drag.
Cone previews are drawn for the active prop. "Test props" runs an automated demo, and the Paint the Camp
game is played with them. CLI: `docker compose run --rm input staff staff_01 slam 0.9 "#ff4000"`.

## Prototyping it

1. **Publish before you gesture.** Get an ESP32 publishing a hardcoded `slam` on a button press onto
   `prop/<id>` and watch the virtual camp answer. That proves the whole chain in an afternoon.
2. **Then the IMU.** Classify the gestures in the table above on-device and publish one message per gesture
   with an `intensity` you are happy calling 0–1. Do **not** stream IMU data onto the bus.
3. **Then heading**, which is what makes cones and spirals readable. Calibrate the magnetometer indoors and
   outdoors; a wrong heading is worse than `null`.
4. **Then GPS.** Average 20–30 s of fixes per calibration mark; consumer GPS is 2–5 m absolute and worse
   near metal and structures, but marks taken minutes apart share most of their error, so *relative*
   positions across camp are better than the absolute numbers suggest.
5. **ESP-NOW bridge last** — until it exists, publish straight to MQTT from the prop over WiFi for bench
   work. The payload is identical either way; only `via`/`rssi` change.

## Where it actually is (2026-09-22)

Step 1 is **done**: [`../../nodes/staff_pi/`](../../nodes/staff_pi/) is the staff running on a Raspberry Pi.
It publishes real `prop/staff_01` casts over WiFi and the mapping engine routes all three gestures to all
six expected `cmd/` outputs. Gestures come from a keypress (`1`/`2`/`3`) and `heading`/`spin_dir` are `null`,
because nothing reads the **BNO055** sitting on its I2C bus yet — steps 2 and 3 are still ahead, but the
hardware for them is already there. **Step 4 is done**: the PA1010D gives 1 Hz fixes and casts carry real
`lat`/`lon` through to each effect's `origin`.

Open: reading the BNO055 (heading first, gestures after), the calibration walk, the ESP-NOW↔WiFi bridge
design, and the battery/charging plan for a four-night burn.
