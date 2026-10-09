# Booth, displays, kitchen, Resolume

The camp-operations end of the bus: who is playing, what the screens show, the food call, and the video wall.

## DJ check-in kiosk (booth Pi + 7″ touchscreen)

Tap your name from the roster (or "I'm not on the list" for a walk-up); checking in auto-checks-out the
previous artist.

```
booth/dj        {"artist":"SideQuest","slot":"2027-02-13T22:00","since":1770000000000,
                 "timer_end":1770002700000,"logo":"sidequest.png"}     retained
booth/schedule  {"slots":[{"artist":"...","start":"...","end":"..."}]}  retained
```

Empty booth = `{"artist": null, "since": ...}`. `logo` is a filename the booth serves at
`http://campbus.local/logos/<file>`. **The Resolume clip name is the `artist` string exactly.**

Also the booth's to publish: `camp/mode` (`idle` / `show` / `quiet`), `camp/sync`
(`bpm_sync`, `pulse`), `booth/quiet`, `vibe/params`, and the Paint the Camp `game/state` + `game/score`.

CLI: `docker compose run --rm input checkin "SideQuest"` / `checkout` / `campmode show`.

## Now-playing displays (Pi + browser kiosk)

**Subscribes:** `booth/dj`, `display/#`, and `game/state` + `game/score` during a game.

```
display/mode    {"mode":"nowplaying"}    one of nowplaying | schedule | comein | comments   retained
display/banner  {"text":"FOOD'S UP - dinner!","level":"takeover","until":0}                 retained
```

Everything is rendered from **retained state**, so a screen that boots at 3 a.m. — or after the broker
restarts — shows the right thing with no server to ask.

⚠️ **`display/banner` is retained and a takeover with no `until` owns the screen forever.** The visual
simulator auto-clears after 45 s to hide the problem; a real display or the kitchen node must set `until`
or clear the banner. Unresolved.

## Kitchen food call

```
kitchen/foodcall  {"meal":"dinner","soft":false}     ->  cmd/kitchen/bell {"soft":false}
                                                     +   display/banner "FOOD'S UP - dinner!" (takeover)
```

`soft: true` is the quiet-hours ring. The bell node is a Pi (or ESP32) driving a solenoid; it subscribes
`cmd/kitchen/#` and implements `bell` only. CLI: `docker compose run --rm input foodcall dinner`.

## Resolume adapter

Subscribes `cmd/resolume/#`, implements `clip` `{name, fallback}` and fires that clip on Ben's Arena box via
the Arena REST API or OSC. `fallback` is the camp logo clip when the artist has none.

This is the M2 piece and the one place the bus reaches a Windows PC; there is an MCP-driven Resolume control
path available on Ben's machine for exploring the API interactively.

## Prototyping these

1. **Displays first** — they are pure subscribers and need no hardware beyond a browser. Point a page at
   `ws://campbus.local:9001`, render `booth/dj` + `display/*`, and check it survives a broker restart and a
   cold boot.
2. **Kiosk second.** A small page that publishes retained `booth/dj`; the roster can be a JSON file on the
   Pi. Check-in must work with the screens unplugged.
3. **Resolume third**, against Arena on a dev machine: subscribe `cmd/resolume/clip`, resolve the name to a
   clip, fire it, and fall back to the logo clip when there's no match. Test with
   `docker compose run --rm input checkin "SideQuest"`.
4. **Bell last** — a solenoid, a relay and a 300 ms pulse; the "soft" version is one short tap.

Mocks for all four already exist in `sim/devices.py` (`MockDisplay`, `MockResolume`, `MockKitchenBell`) and
in the Booth Devices page, so you can build each one against a camp that already behaves correctly.
