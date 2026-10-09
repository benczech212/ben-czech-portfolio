# Space Hallway

The entrance tunnel people walk through, and the camp's biggest single light surface.

## Physical

- **Five hexagonal frames**, tunnel roughly **16′ long × 12′ wide × 8′ high**, with LED runs along the frame
  edges and **six longitudinal rails** running the full length at each vertex.
- Driven by **PixelBlaze** (a Pi bridge subscribes to the bus and drives it).
- **No bottom LED segments** (build team) — do not design an effect that needs the floor.
- Final pixel counts and measurements are pending. The simulator models 5 frames × 6 edges × 10 px +
  6 rails × 32 px = 492 pixels at real 3D coordinates; treat that as a placeholder for the real map.

## Contract

It is an **extended node**: one `node_id`, but a footprint and a heading rather than a point.

```
map/hallway  {"lat": ..., "lon": ..., "corners": {"FL": {...}, "FR": {...}, "BR": {...}, "BL": {...}}}
```

Calibration marks **four corners in order — front-left, front-right, back-right, back-left** (front = the
mouth people walk in through). From those the bridge knows position, size *and* heading, and every pixel
gets a world position (PixelBlaze 3D map + the corner frame).

**Subscribes:** `cmd/hallway/#`, `cmd/all/#`, `audio/beat`, `audio/energy`, `camp/mode`, `camp/sync`,
`map/hallway`.

**Effects:** `sweep`, `shockwave`, `comet`, `rings`, `wipe`, `warp`, `spiral`, `rain`, `breathe`, `speed`,
plus the common `flash` / `pulse` / `color` / `mode`. Payloads are in `../../docs/SCHEMA.md`.

**Per-pixel propagation is the interesting part:** a cast with an `origin` reaches each *pixel* after
`distance(origin, pixel) / speed_mps`, so the wave **enters through whichever face is nearest the caster**
and travels away from them, with no special cases. For scheduling, the node's own delay is the distance to
its nearest corner; **cone and range tests use the whole footprint** (corners, centre and centre line, best
hit wins), so a narrow cone aimed down the middle of the tunnel still triggers it.

## Simulated today

`sim/camp.html` — a 3D-projected tunnel with every pixel at its real coordinate, all the effects above, and
a draggable world frame: drag the tunnel on the camp map, drag the yellow back handle to rotate it, and the
FL/FR/BR/BL corners are drawn and labelled exactly as calibration will mark them.

## Prototyping it

1. **Get the geometry right on paper first** — frame spacing, rail count, pixel pitch — then update the
   simulator's constants so the sim and the real tunnel agree. Everything downstream depends on the 3D map.
2. **Bridge, don't reinvent.** A Pi Zero 2 W subscribing `cmd/hallway/#` and driving PixelBlaze patterns
   (variables over its WebSocket API) is the intended shape. The bridge's job is: receive effect, compute
   per-pixel delay and intensity, hand PixelBlaze the parameters.
3. **Start with `sweep` and `breathe`.** They exercise origin → entry face → travel without needing shapes.
4. **Then `rings`, `comet`, `wipe`** (directional, all fall out of per-pixel distance), then `spiral`
   (needs `spin_dir` + `turn_s`), then `warp` / `rain` (no geometry).
5. **Test with the sim as the input side**: `docker compose run --rm input staff staff_01 slam 0.9` fires a
   real cast at your bridge, and `tools/spy.py` shows exactly what it received.

Open: pixel counts and final dimensions; who builds the PixelBlaze bridge; whether the rails and edges are
one PixelBlaze or several.
