# Device briefs

One file per device that joins the bus: what it is physically, its exact contract, how it is simulated
today, and how to prototype it. Read [`../04-build-a-device.md`](../04-build-a-device.md) first for the
workflow these briefs assume.

| Device | Kind | Topic | Built? |
|---|---|---|---|
| [Sonic Mycelium Cluster](sonic-mycelium-cluster.md) | instrument + responder | `instrument/mycelium_01`, `cmd/mycelium/*` | simulated only |
| [Crystal Geode Synth](crystal-geode-synth.md) | instrument + responder | `instrument/geode_01`, `cmd/geode/*` | simulated only |
| [Monstera Piezo Plant](monstera-piezo-plant.md) | instrument + responder | `instrument/plant_01`, `cmd/plant/*` | simulated only |
| [Water Chord Tubes](water-chord-tubes.md) | instrument + responder | `instrument/tubes_01`, `cmd/tubes/*` | simulated only |
| [Magic Crystals](magic-crystals.md) | responder | `cmd/crystals/*`, `crystal/<id>/paint` | simulated only |
| [Space Hallway](space-hallway.md) | responder (extended node) | `cmd/hallway/*` | simulated only |
| [Props: staff, wand, drum, whip](props.md) | input | `prop/<prop_id>` | **staff runs on a Pi** (`nodes/staff_pi/`), keypress casts, no IMU |
| [Input toys](toys.md) | input | `toy/<id>` | sketch written, never on a board |
| [Booth, displays, kitchen, Resolume](booth-and-displays.md) | mixed | `booth/*`, `display/*`, `cmd/kitchen/bell`, `cmd/resolume/clip` | simulated only |

Every one of these is expected to serve its own console and advertise it as `ui` in its status, so the
Devices page can link to it — see **Every device serves its own console** in
[`../01-architecture.md`](../01-architecture.md).

**Only the staff exists as hardware, and only as a Pi mule** (2026-09-22). Every other brief's
"prototype" section is therefore the real next step for that device, not a rewrite of something working.

Two more pieces are software-only and documented elsewhere: the **MIDI bridge**
(`tools/midi_bridge.py`, plus the Ableton set in `../../deploy/ableton.md`) and the **mapping engine**
(`../01-architecture.md`).
