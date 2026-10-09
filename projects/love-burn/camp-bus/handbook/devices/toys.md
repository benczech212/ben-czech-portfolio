# Input toys

Buttons, levers, cranks, dials, faders — the cheap, obvious, irresistible things. The simplest device on the
bus and the right first build for anyone learning it.

## Physical

An ESP32, a switch or pot, and a battery or USB. The reference sketch
(`clients/esp32/campbus_template/`) already is one: debounced button → `toy/<id>` press, LED flashing on
`audio/beat`, retained status, last will, interaction counter, and a deep-sleep block for battery toys.

## Contract

```
toy/<toy_id>   {"toy": "big_red_button", "event": "press", "value": 1, "ts": 0}
```

| `event` | `value` means |
|---|---|
| `press` / `release` / `pull` | 1 |
| `hold` | seconds held |
| `rate` | signed turns per second (crank) |
| `level` | 0.0–1.0, plus `"i"` for which slider |
| `note` | velocity |
| `mode` | a string label (dial-a-vibe) |

A toy publishes **only** what happened to it. What it controls is a rule.

## The toys routed today

| Toy | Events | Default route |
|---|---|---|
| `big_red_button` | `press`, `hold`, `release` | press → hallway sweep + crystals flash; hold → everything pulses for `value` seconds |
| `lever` ("DO NOT PULL") | `pull` | hallway warp + rings |
| `crank` | `rate` (throttled 100 ms) | hallway animation speed |
| `dial_a_vibe` | `mode` | `cmd/all/mode` — the camp's vibe palette |
| `fader_wall` | `level` with `i` | (no default route yet — add one) |

## Simulated today

Fire any of them from the CLI and watch the virtual camp answer:

```
docker compose run --rm input press big_red_button
docker compose run --rm input hold big_red_button 2.5
docker compose run --rm input crank 3.0
docker compose run --rm input level fader_wall 0 0.8
docker compose run --rm input mode dial_a_vibe chaos
```

The Routes page lists them all as input devices, so you can build or re-point rules for them in the UI.

## Prototyping it

1. Flash the template to any ESP32 dev board with `NODE_ID` / `TOY_ID` changed. Press the BOOT button; the
   Console page (`dashboard.html`) should show the node online and the press on the bus.
2. Add your own rule in `booth/mappings.yaml`, save, and watch it hot-reload — no re-flash.
3. **Local effect first, then the bus.** If the toy has its own light or click, do it before publishing, so
   it still feels alive with the broker down.
4. **Battery toys**: use the deep-sleep block. Wake-to-publish is ~1–2 s with DHCP; a static IP plus a
   cached BSSID/channel in RTC memory gets it under ~400 ms.
5. Debounce in firmware (the template uses 50 ms). A bouncing switch publishing ten presses is indis-
   tinguishable from an enthusiastic person, and the counters are grant analytics.

A toy is the cheapest way to test *any* new responder: give the responder a rule off `toy/test_button` and
you have a one-press trigger for it.
