# Camp Bus handbook

Written 2026-09-22. The orientation pack for anyone — human or agent — picking this project up cold.

This folder explains **what exists, how it works, why each feature behaves the way it does, and how to
build a prototype of a device that joins the bus.** It is documentation *about* the system; it is not
the contract. When this folder and [`../docs/SCHEMA.md`](../docs/SCHEMA.md) disagree, **SCHEMA.md wins** —
it is the canonical topic/payload contract that other camp builders code against.

| Read | For |
|---|---|
| [`01-architecture.md`](01-architecture.md) | How the system is put together and why: the bus, the four layers, what runs where. |
| [`02-feature-intent.md`](02-feature-intent.md) | The *intent* behind every feature — what each one is supposed to feel like at camp, and the rule that produces that feeling. |
| [`03-state-of-play.md`](03-state-of-play.md) | What is actually built, what is only simulated, what is waiting on hardware or a decision. |
| [`04-build-a-device.md`](04-build-a-device.md) | The playbook for adding a device: the workflow, the conventions, the acceptance bar, the traps we already hit. |
| [`devices/`](devices/) | One brief per device: hardware, its exact bus contract, how to prototype and bench-test it. |

Background that predates this folder (concept, who wants what, decisions, milestones, the original build
brief) lived in a separate camp planning folder that is not part of this portfolio copy; the handbook pages
summarise what the code needs from it.

## What this is

A camp-wide message bus for the **Awetists** theme camp at Love Burn 2027 (Feb 11–14, Virginia Key, Miami).
Everything interactive in camp — touch instruments, handheld props, buttons, crystals, the space hallway,
the entrance screen, the kitchen bell, the DJ booth, Resolume, Ableton — publishes and subscribes to one
MQTT broker on the booth Pi over a closed camp LAN with no internet.

The shape of it in one line:

```
inputs (toys, props, instruments)  ->  mapping engine (the only writer to cmd/)  ->  responders (lights, screens, sound)
```

Everything else in this handbook is detail hanging off that line.

## The rules that never bend

1. **The audio tap is a spare mixer output.** The PA chain is never on any Pi. If every Pi in camp dies
   at once, stage sound does not notice.
2. **The bus is opt-in.** Every piece runs standalone. A dead broker must break nothing.
3. **Publishers never write to `cmd/`.** Only the mapping engine does, so re-routing the camp is one
   hot-reloaded YAML edit and no device firmware changes.
4. **Offline first.** Anything needing internet is fire-and-forget garnish.

## Try it in three commands

No hardware required — the whole camp is simulated.

```
cd apps/camp-bus
docker compose --profile sim up --build        # broker + fake audio + mapping engine + mock devices
```

Then open <http://localhost:8080> (Virtual Camp, Instruments, Routes, Console) and fire inputs:

```
docker compose run --rm input press big_red_button
docker compose run --rm input hit amanita 0.9          # whack a mushroom
docker compose run --rm input dip tube_2 0.8           # hand into a water tube
docker compose run --rm input run sim/scenarios/smoke.yaml   # assert every default route end to end
docker compose run --rm test                           # the pytest suite (its own throwaway broker)
```

`docker compose run --rm input --help` prints every simulated input with example arguments.

## Where things live

| Path | What |
|---|---|
| `docs/SCHEMA.md` | **Canonical** topics, payloads, rules. Change here first, code second. |
| `booth/` | `mapping_engine.py` + `mappings.yaml` — the routing table, hot-reloaded, also editable live over the bus |
| `clients/python/` | `campbus` — the Pi-class reference client (node id, status, last will, counters) |
| `clients/esp32/` | Arduino reference sketch for ESP32 toys and responders |
| `sim/` | The virtual camp: browser simulators of every device, mock devices, input CLI, scenarios |
| `nodes/` | code that runs on **real** camp hardware. `staff_pi/` is the staff prop on a Raspberry Pi |
| `tools/` | `fake_beat.py`, `spy.py`, `balance.py`, `midi_bridge.py`, `clipserver.py` |
| `tests/` | pytest: rule unit tests + end-to-end workflow tests through a real broker |
| `deploy/` | Booth Pi install notes, systemd units, the Ableton set layout |
| `handbook/` | You are here |
