Part of Ben Czech's portfolio. Built with Claude Code. Licensed CC BY 4.0; created for the Leepa-Rattner Museum of Art, St. Petersburg College.

# Leepa-Rattner Exhibit — Macro Launcher

YAML-driven launch + OSC macros for the projection mapping kiosk: start Resolume
Arena, Synesthesia and (later) TouchDesigner with the right composition / output
preset, wait until each is actually ready, then send OSC. Everything an operator
might need to change (paths, ports, presets, the macro sequences themselves)
lives in `exhibit.yaml`. Design notes: `docs/PLAN.md`.

## The lesson

**The problem.** A museum exhibit runs unattended on one kiosk PC. Every morning it has to bring up
several apps (a VJ app, a visuals engine, an audio mixer, a music player, a control panel) in the
right order. It has to survive a reboot or a crashed app with no technician in the room. "Launch
everything, sleep 20 seconds, send the OSC and hope" fails on a slow morning, and it fails silently.

**The idea.** Write the start-up as **data, not a script**. A macro is an ordered list of small,
named steps in YAML. Every app declares what "ready" means (its web API answers, it sends OSC, its
process exists). The runner waits for that real signal instead of a fixed delay. Each step says
whether a failure should stop the show or be logged and skipped. The same file can be checked
statically and rehearsed with `--dry-run` before it ever runs for real.

### Walkthrough

1. **Read the boot macro first: [`exhibit.example.yaml`](exhibit.example.yaml)** (search for `macros:`).
   This is the whole start-up, top to bottom:
   ```yaml
   boot:
     - action: keepalive start         # never sleep, never blank, never lock
     - action: control start           # control-panel server (GUI + API + /metrics + input OSC port)
     - { launch: inputs, on_fail: continue }      # Xbox / SpaceMouse bridge
     - { macro: monitoring, on_fail: continue }   # analytics stack; the exhibit does not depend on it
     - launch: voicemeeter
     - wait: voicemeeter
     - action: voicemeeter apply       # mixer routing + Windows default devices, before anything makes sound
   ```
   Notice the rhythm: `launch`, then `wait`, then act. Only optional pieces (controllers, monitoring,
   and later every music step) carry `on_fail: continue`. The comment "Music must never block the
   visuals" is a priority decision written where the operator will see it.

2. **See how a step is parsed: [`src/runner.js`](src/runner.js)**, `normalizeStep`. A step must have
   exactly one step-type key (`launch`, `wait`, `osc`, `sleep`, `macro`, `action`…), and `on_fail`
   defaults to `abort`. `runMacro` logs `3/26 …` for each step, and when a required step fails it stops
   with the macro name and step number. A failure becomes a log line someone can act on.

3. **See what "ready" means: [`src/apps/index.js`](src/apps/index.js)**, `readySpec` and `waitReady`.
   Each driver supplies a default readiness probe, and `ready:` in YAML overrides it. The probe types
   are `process`, `driver`, `http`, `tcp` and `osc_heartbeat`. If the wait times out, the error says
   whether the process is running and adds the driver's hint. For example, Resolume's default in
   [`src/apps/resolume.js`](src/apps/resolume.js):
   ```js
   export function readyDefaults(app, cfg) {
     return { type: 'http', url: restUrl(app, cfg), hint: 'Arena REST API not answering. Enable Preferences > Webserver in Arena (port must match apps.resolume.rest.port), or set ready.type: process.' };
   }
   ```
   "The process exists" and "the app can take commands" are different states. Wait for the second.

4. **Catch mistakes before the show: `checkMacros` in [`src/runner.js`](src/runner.js)** and
   `checkApp` in [`src/apps/index.js`](src/apps/index.js). `exhibit check` resolves every app,
   OSC target, nested macro and time window, and checks that each executable exists. A typo in a macro
   then shows up on the bench, not at 7 AM in front of visitors.

5. **Rehearse safely: `--dry-run`.** In `runStep`, every step with a side effect (`osc`, `rest`,
   `shell`, `sleep`, `music`, `action`, `kill`) returns early when `ctx.dryRun` is set. `launchApp` and
   `waitReady` log "would launch" / "would wait" instead. You can read exactly what boot would do.

6. **Keep the control surface small: [`src/server.js`](src/server.js)** and the
   [Security notes](#security-notes) below. The panel's API is the same runner the CLI uses. It binds
   `127.0.0.1`, checks `Host`/`Origin`, accepts only JSON posts, and puts raw OSC and most actions
   behind the admin password. An unattended kiosk is still a computer the public can touch.

### Try it

You need Node 20+. Resolume, Synesthesia and the other apps are not required for this.

```
npm install
npm test                                   # OSC encoding and config-merge tests
cp exhibit.example.yaml exhibit.yaml
node bin/exhibit.js list                   # apps, OSC targets and macros from the YAML
node bin/exhibit.js check                  # expect ✖ "exe not found" lines on a machine without the apps
node bin/exhibit.js run boot --dry-run     # read the plan; it stops at the first required app that is missing
```

That last result is the lesson in action. Optional steps (`inputs`, `monitoring`) warn and carry
on, and the first required `launch` with no executable aborts the macro with its step number.

Now write your own macro. Create `exhibit.local.yaml`, which is deep-merged over `exhibit.yaml`:

```yaml
macros:
  hello:
    - log: hello from a macro
    - sleep: 1
    - { shell: "exit 1", on_fail: continue, label: a step that is allowed to fail }
    - osc: { to: synesthesia, address: /hello, args: [1] }   # a UDP packet to 127.0.0.1; nothing needs to listen
    - log: still here, so the failure above did not stop the show
  hello_strict:
    - shell: "exit 1"
    - log: you will never see this line
```

```
node bin/exhibit.js run hello          # warns at step 3, finishes, exit code 0
node bin/exhibit.js run hello_strict   # aborts at step 1, exit code 1
```

`exhibit.yaml`, `exhibit.local.yaml` and `logs/` are gitignored, so delete them when you're done.

### Take it to your own project

- **Make start-up a list of named steps in a config file.** Operators can read it, reorder it and
  diff it, and one runner can check it, rehearse it and log it.
- **Wait for a readiness signal, not a timer.** Use an API that answers, a port that opens or a
  heartbeat that arrives. When the wait fails, say whether the process was even running.
- **Decide failure policy per step.** Abort on what the audience would notice, and continue past
  what they wouldn't. Write the reason in a comment next to the step.
- **Ship a validator and a dry run with the runner.** Most broken shows are config mistakes, and
  both of these catch config mistakes.
- **Give the GUI, CLI and schedule one engine.** Here the panel, the `.bat` shortcuts and the
  scheduled tasks all run the same macros, so a fix in one place fixes them all.

## Reference

## Setup

Node 20+ is required. On the kiosk it is installed portably at
`C:\dev\tools\node` (on the user PATH).

```
npm install
cp exhibit.example.yaml exhibit.yaml              # then replace <kiosk-user> in the paths
cp exhibit.local.yaml.example exhibit.local.yaml  # set admin.password here (gitignored)
node bin/exhibit.js check      # validate config, exe paths, presets, macros
```

Per-machine differences (a different composition, a dev box without the Arena
webserver) go in `exhibit.local.yaml`, which is gitignored and deep-merged over
`exhibit.yaml`. See `exhibit.local.yaml.example`.

## Everyday use

Double-click the shortcuts in `shortcuts\` (`Boot.bat`, `Shutdown.bat`,
`Restart.bat`, `Flip Output.bat`, `Status.bat`, `Check Config.bat`), or use the CLI:

```
node bin/exhibit.js run boot                  # run a macro
node bin/exhibit.js run boot --dry-run        # show what would happen
node bin/exhibit.js status                    # running / ready per app
node bin/exhibit.js launch resolume           # one app (skips if already running)
node bin/exhibit.js wait synesthesia          # block until ready
node bin/exhibit.js kill resolume             # graceful close, then force
node bin/exhibit.js osc synesthesia /playlist/next 1
node bin/exhibit.js osc synesthesia /playlist/select s:Exhibit   # s: forces a string
```

Every run appends to `logs\exhibit-YYYY-MM-DD.log`.

## Control panel (GUI)

`boot` starts a small web server (`control` app, `node bin/exhibit.js serve`, port 9876)
and opens the panel in a kiosk Chrome window on display 1 (`panel` app). The page shows
whether each app is running/ready, what the music is playing, the last macro's log, and
has one-tap buttons: Pause / Wake exhibit, Boot, Restart apps, next scene, flip output,
music play/pause/next/prev/volume. "Hide panel" closes the window so the desktop is
usable; `Show Control Panel.bat` (or `run show_panel`) brings it back, and it returns at
wake/boot anyway. The API (`/api/status`, `POST /api/run/<macro>`, `/api/music/<action>`,
`/api/action/<app>/<name>`) is the same runner the CLI uses, one macro at a time.

### Idle / engaged mode

The control server keeps an engagement state. Any reported input switches it to **engaged**
and runs the `engaged` macro (today: Synesthesia auto-advance off so visitors can explore the
scene); after `engagement.idle_seconds` (30) without input it returns to **idle** and runs the
`idle` macro (auto-advance and shuffle back on; a commented step resets visitor tweaks). Boot
runs `idle` after selecting the playlist, which is also how shuffle is (re)enabled at launch.
Inputs come from `POST /api/input {source, detail}` (future controllers: MIDI, OSC, GPIO…),
`exhibit input <source>`, and, when `engagement.panel_controls_count` is on, touching
Synesthesia controls on the Visuals page. State: `GET /api/engagement`; force idle with
`POST /api/engagement/idle`; the Admin page has "Simulate visitor input" / "Force idle".
Transitions are logged as `engagement` events and exposed as `exhibit_engaged` /
`exhibit_inputs_total{source}` / `exhibit_engagement_transitions_total{mode}`.

**Simple scene mode**: while engaged, Arena's "Main Scene" layer group is reduced to the
Synesthesia layer's clip 1 (the plain Synesthesia spout) and every layer in the group gets its
autopilot switched off, so nothing advances while a visitor is experimenting. The
`resolume simple` action snapshots each group layer's autopilot target and connected clip to
`data\resolume_scene_mode.json`, sets autopilot Off *before* clearing (a cleared layer on random
autopilot would otherwise pick a new clip), connects the keep clip and clears the rest;
`resolume full` restores the snapshot. Configured in `apps.resolume.simple_mode`
(`group`, `keep: { layer, clip }`); wired into the `engaged` / `idle` macros;
`exhibit action resolume mode` shows the current state.

### Controllers (Xbox, SpaceMouse, TouchDesigner, Raspberry Pi)

All visitor input arrives as OSC on `inputs.osc_port` (9004) with the shape
`/input/<source>/<control> <value>`. The `inputs` app (`src\inputs.py`, per-user Python with
pygame + pyspacemouse) bridges the Xbox controller (`/input/xbox/lx ly rx ry lt rt btn_a…
dpad_x dpad_y`) and the 3Dconnexion SpaceMouse (`/input/spacemouse/x y z roll pitch yaw btn_0…`);
it runs from boot and keeps polling for devices that are plugged in later. TouchDesigner (camera
AI positions) and Raspberry Pi sensors send the same shape directly, e.g. `/input/td/person_x 0.63`.

Every message marks the exhibit engaged (see above). `inputs.mappings` routes controls:

```yaml
- { match: /input/xbox/lx, mode: relative, rate: 0.6, to: { synesthesia: { scene_control_index: 0 } } }
- { match: /input/xbox/rt, mode: absolute, range: [0, 1], to: { synesthesia: { bank: meta, name: reactivity } } }
- { match: /input/xbox/btn_a, mode: button, to: { macro: syn_next } }
- { match: /input/td/person_x, mode: absolute, range: [0, 1], out: [0.3, 0.8], to: { synesthesia: { bank: meta, name: brightness } } }
```

The panel's **Inputs** page shows every source live: device cards (connected / rate), a 3D
puck that follows the SpaceMouse (translate x/y, push/pull z, roll/pitch/yaw) with per-axis
meters and buttons, an Xbox controller mimic (sticks, triggers, ABXY, bumpers, D-pad), bars for
any other source, and the mapping table highlighting each mapping as it fires. The bridge
reports device presence as `/input/_devices/<source> <0|1> <name>` every 2 s. Windows also
exposes the SpaceMouse as a 6-axis SDL joystick, so the bridge picks the Xbox pad by name
(`--xbox-match`) and can read the SpaceMouse through SDL if raw HID access fails.

`mode: absolute` scales the input `range` to 0..1 (optionally to `out`), `relative` lets a stick
or SpaceMouse deflection nudge the target at `rate` of the range per second (2.0 = a full sweep
in half a second at full deflection), `button` fires on the rising edge. `gain` scales the input
first and `curve` > 1 gives an expo response (fine near the centre, fast at the edges); the
SpaceMouse mappings use gain 1.5 / curve 1.4 because a light touch only reads 0.2–0.4. The
Inputs page is fed by `GET /api/inputs/stream` (server-sent events, ~30 Hz while values change). Targets: a Synesthesia control by bank/name, the Nth control of whatever scene is playing
(`scene_control_index`), any OSC target/address, `music: volume`, or a macro. `match` accepts `*`
wildcards. Live values and the message count are on the Admin page and at `/api/inputs`;
`POST /api/input {source, control, value}` injects a message through the same path for testing.

### Per-scene input schemes

Every Synesthesia scene exposes different controls, so a fixed "stick → control #1" mapping
feels arbitrary. `exhibit schemes generate` reads every scene definition (`scene.json` under the
Synesthesia install and user folders, 110 scenes; OSC names = file names lowercased without
underscores) and writes `input_schemes.yaml` with one scheme per scene in the exhibit playlist.
Slots and the rules that fill them:

| slot | gesture | rule |
|------|---------|------|
| x, y | left/right, forward/back | an `xy` pad if the scene has one, else controls named like x/horizontal/pan/offset and y/vertical/height/tilt |
| z | push / pull | speed / rate / flow / tempo, else zoom / scale / size / depth |
| yaw | twist | rotation / spin / angle / twist / kaleido, else hue / phase |
| pitch, roll | tilt | camera-ish, then amount / intensity / mix; remaining slots take the next free sliders in UI order |
| left, right | the two SpaceMouse buttons (Xbox A / B) | action-group toggles that start off, then bangs (momentary), then any toggle |

Control names outrank group names, auto-animated "traveler" controls and fps/reset/media/preset
controls are skipped. Every entry records its `reason`; edit a scene freely and set `auto: false`
to keep it across regenerations (`--force` overrides). `exhibit schemes list` / `show <scene>`
inspect the file. Mappings use `to: { scheme: <slot> }` and resolve at runtime against the
playing scene (`/api/schemes/current`); toggles flip the control, momentary bangs are held while
the button is down. The Inputs page shows the live scheme with each slot's value and highlights
the slot being driven. Scenes without a scheme (or with an empty slot) simply ignore that gesture.

### Admin page

The panel's **Admin** page is locked behind `admin.password`, which lives in `exhibit.local.yaml`
(gitignored; never in `exhibit.yaml`). Unlocking is checked by the server and returns a
token the page sends in the `x-admin-token` header (query-string tokens are not accepted); it expires after `admin.session_minutes` of admin
inactivity, and "Lock now" ends it early. The token is required for: the macros listed in
`admin.protected_macros` (restart, shutdown, flip_output, monitoring_stop, hide_panel by
default), `POST /api/launch|kill/<app>`, `POST /api/reload`, `POST /api/osc`,
`POST /api/synesthesia/raw`, any `/api/action/<app>/<name>` or `/api/music/<name>` not in
`admin.public_actions` (see Security notes), and `POST /api/admin/panel`
(`mode: windowed` reopens the panel as a normal 1440×900 window so the desktop is reachable,
`kiosk` returns to fullscreen, `close` closes it). Unlock attempts, locks and panel-mode changes
are logged as `admin` events. Add further admin controls to the page's cards as needed.

### Synesthesia tab

The control server listens on Synesthesia's OSC output port (`osc.synesthesia.listen`)
and keeps a live model of the current scene, the global "meta" controls (master,
brightness, contrast, hue, reactivity, colours, media…) and the scene's own controls,
plus audio meters. Synesthesia must have **OSC output controls** on
(`OSC_OUTPUT_CONTROLS: 1`, pinned by the driver); it then broadcasts every control
when a scene loads and on change. The panel builds a widget per control (slider,
toggle, dropdown with labels, colour, xy) with reset / randomize, bank-wide reset /
randomize / undo, playlist transport (prev, play/pause, next, shuffle, auto, select),
favslots, render on/off, a raw OSC sender and a live message log.

API: `GET /api/synesthesia`, `POST /api/synesthesia/control {bank,name,value[,sub]}`,
`/control/default|random`, `/bank/default|random|undo`, `/playlist {action,value}`,
`/scene {name,preset}`, `/favslot {position}`, `/render {enabled}`, `/raw {address,args}`,
and `POST /api/osc {to,address,args}` for any configured OSC target. Because the server
owns the listen port, the CLI's Synesthesia readiness check asks
`GET /api/osc/heartbeat/<port>` when the server is up instead of binding the port itself.

### Feedback and analytics (Prometheus, Loki, Grafana)

The panel's **Feedback** page has thumbs up / thumbs down (plus optional tags and a
note). Each vote stores a full snapshot of the moment: Synesthesia scene and every
control value, Resolume composition with the connected clip per layer, the track
playing, VoiceMeeter levels, app states, and the relevant config. Votes and all other
events (scene changes, track changes, macro runs, pause/wake, server start) go to:

- `data\events\events-YYYY-MM-DD.ndjson` (one JSON object per line, Loki-friendly);
- `data\exhibit.db` (SQLite, tables `events` and `feedback`, via Node's built-in `node:sqlite`);
- Loki at `telemetry.loki.url` (labels `app=exhibit`, `type=<event>`), pushed in batches.

The control server also exposes Prometheus metrics at `http://127.0.0.1:9876/metrics`:
`exhibit_app_running/ready{app}`, `exhibit_paused`, `exhibit_music_*` and
`exhibit_music_track_info{title,artist,album}`, `exhibit_synesthesia_scene_info{scene}`,
`exhibit_synesthesia_audio{band}`, `exhibit_synesthesia_bpm`,
`exhibit_synesthesia_control{bank,name,component}` (every control value),
`exhibit_resolume_layer_clip_info{layer,name,clip,state}`, `exhibit_resolume_layer_opacity`,
`exhibit_voicemeeter_strip_level` / `_bus_level`, `exhibit_feedback_total{vote,scene}`,
`exhibit_macro_runs_total{macro,result}`, `exhibit_events_total{type}`.

The stack itself runs on the kiosk as portable installs (no admin): `monitoring\install.ps1`
downloads/extracts Prometheus, Loki and Grafana into `C:\dev\tools\monitoring`; the
`prometheus`, `loki` and `grafana` apps in `exhibit.yaml` launch them (boot runs the
`monitoring` macro, failures there never block the exhibit). Configs live in `monitoring\`:
`prometheus.yml` (scrapes the control server every 5 s, 400 days retention),
`loki.yaml` (filesystem storage, one year retention), `grafana\grafana.ini` plus
provisioned datasources and the "Exhibit Overview" dashboard. Grafana is at
`http://127.0.0.1:3000` (local only, no anonymous access; set the admin password with the
`GF_SECURITY_ADMIN_PASSWORD` environment variable before first start), Prometheus at
`127.0.0.1:9090`, Loki at `127.0.0.1:3100`. Data lives under `C:\dev\tools\monitoring\data`.

### PC stats, output FPS and the live preview

- **PC stats**: the control server samples CPU, memory, disk free (`telemetry.disks`) and the
  NVIDIA GPU via `nvidia-smi` (utilisation, VRAM, temperature, power, clocks, fan) into
  `exhibit_system_*` / `exhibit_gpu_*` metrics and shows them on the panel's Exhibit and System
  pages (`/api/system`). The Prometheus **windows_exporter** (`windows_exporter` app, :9182)
  adds per-core CPU, memory, disks, network, thermal zones and per-process CPU/memory for the
  exhibit apps (`windows_*` metrics, job `windows`).
- **Output FPS + preview**: `src\spout_preview.py` (`spout_preview` app; per-user Python 3.12 +
  SpoutGL) receives every Spout sender, measures its real frame rate and serves JPEG frames.
  FPS comes from the Spout frame counter when the sender supports it (Resolume does; needs
  `HKCU\Software\Leading Edge\Spout\Framecount = 1`, set on the kiosk), otherwise from counting
  actual pixel changes of a 48×27 thumbnail ("content fps", what Synesthesia gets). The panel's
  Exhibit page shows the feed with an FPS badge and a sender switch; metrics are
  `exhibit_output_fps{sender}`, `exhibit_output_sender_connected`, `exhibit_output_resolution_pixels`.
  Routes: `/api/preview/senders`, `/api/preview/frame.jpg?sender=`, `/api/preview/stream?sender=`.
  Arena's Advanced Output presets ("SPC Final" and "SPC Final Flip") both contain a third
  screen, **Output Preview**, whose device is a Spout sender; Arena publishes it as
  "Arena - Output Preview" (1920×1200, real frame-counter FPS). `apps.spout_preview.default_sender`
  makes it the panel's default; keep the screen in both presets when editing the output setup.

Desktop shortcuts: run `shortcuts\Install Desktop Shortcuts.bat` once to put
"Exhibit - Boot / Pause / Wake / Control Panel / Status / Restart Apps / Stop Apps"
icons on the kiosk desktop.

## Never sleep, never lock

The kiosk is a domain PC: after a display timeout it drops into Modern Standby and
resumes at the lock screen, which stops the exhibit. Two layers prevent that:

- `keepalive` app (`src\keepalive.ps1`, started first in `boot`): a detached process
  that asserts `SetThreadExecutionState` (system + display required) every 30 s.
  Works regardless of power-plan settings. `action keepalive status|stop`.
- Power plan: `powercfg /change monitor-timeout-ac 0` and `standby-timeout-ac 0`
  were applied on the kiosk (allowed for a standard user). "Require sign-in on
  wake" needs admin and is left alone; it never triggers if the machine never sleeps.

### Music playback verification

The YouTube Music Desktop companion API often reports the play state as "unknown"
even while audio plays (the app logs a player-hook error at launch). `music start`
therefore verifies playback with the VoiceMeeter meter (`apps.music.verify_audio`,
Strip 3) and escalates if nothing is heard within 30 s: play again, change video
again, then relaunch the app once.

## Pause mode (overnight)

`run pause` pauses the music and covers every display with a black fullscreen
window; the primary monitor shows the "exhibit computer, do not touch" message
(`apps.screens.message` / `sub_message`). A hidden button sits in the bottom-right
corner of that monitor (`button_size` px): clicking it runs the `wake` macro, which
closes the screens and resumes the music. `run wake` does the same from the CLI, and
there are `Pause Exhibit.bat` / `Wake Exhibit.bat` shortcuts.

The overlay is `src\blackout.ps1` (WinForms, no dependencies), launched detached by
the `screens` driver; `data\screens.pid` tracks it. Projectors keep rendering behind
the black windows, so waking is instant.

### Running screen (operator monitor)

While the exhibit runs, `running_screen` (a second `screens` app with
`only_message_screen: true`) covers just the primary monitor with an "Exhibit is
running" notice so the desktop and app UIs stay out of sight. Click its hidden
bottom-right corner to close it and use the computer; nothing else happens. Bring
it back with `Show Running Screen.bat` (`run show_running`). Boot and `wake` show
it; `pause` hides it before putting up the black screens.

### Daily schedule

```yaml
schedule:
  pause: "19:00"
  wake:  "07:00"
```

`node bin/exhibit.js schedule install` creates two Windows scheduled tasks
("Exhibit Pause", "Exhibit Wake") that run those macros daily as the logged-on
kiosk user; `schedule list` shows their next run, `schedule remove` deletes them.
Re-run `install` after changing the times. The boot macro ends with
`macro: { name: pause, between: [19:00, 07:00] }`, so a kiosk that restarts at
night goes straight back into pause mode.

## Automatic start at logon

The kiosk user's Startup folder
(`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`) holds a shortcut
"Exhibit Boot" to `shortcuts\Startup Boot.bat`, which waits 30 seconds after
logon and then runs the `boot` macro. Closing that console window during the
countdown cancels the boot. To recreate the shortcut on another machine:

```powershell
$s = (New-Object -ComObject WScript.Shell).CreateShortcut("$([Environment]::GetFolderPath('Startup'))\Exhibit Boot.lnk")
$s.TargetPath = (Resolve-Path ".\shortcuts\Startup Boot.bat").Path; $s.Save()   # run from the repo root
```

Keep the apps' own "start with Windows" options off so the macro controls the
launch order.

## Config reference (`exhibit.yaml`)

```yaml
osc:
  <target>: { host, port, listen }      # port = where we send; listen = where it sends to us

apps:
  <id>:
    type: resolume | synesthesia | touchdesigner | generic
    exe: C:\...\App.exe
    process: App.exe                    # image name for running/kill checks (default: exe basename)
    args: []                            # extra command-line args
    enabled: true
    ready: { type: process | http | tcp | osc_heartbeat, timeout: 60, settle: 0, ... }
    match_cmdline: text                 # optional: identify the process by command line when several apps share an exe

macros:
  <name>:
    - launch: <app>                     # or { app, force: true, <any app key override> }
    - wait: <app> | [apps]
    - kill: <app> | [apps]
    - osc: { to: <target>, address: /x/y, args: [1, 0.5, "text", {f: 1}] }
    - rest: { to: resolume, method: GET, path: /composition }
    - sleep: <seconds>
    - macro: <other macro>
    - shell: <command>
    - log: <message>
```

Any string may reference config values with `{{apps.synesthesia.playlist}}`.
Add `on_fail: continue` to a step to keep going if it fails.

### Resolume specifics

| Key | Effect |
|-----|--------|
| `composition` | Passed on the command line with the change-confirmation dialog suppressed. |
| `advanced_output` | Preset name from `Documents\Resolume Arena\Presets\Advanced Output`. Written into `Preferences\AdvancedOutput.xml` before launch. |
| `ensure_osc_input` | Enables OSC input in `Preferences\osc.xml` on the `osc.resolume.port`. Default on. |
| `ensure_webserver` | Enables the REST webserver in `Preferences\server.xml` on `rest.port`. Default on. |
| `rest.port` | REST API port; `ready: {type: http}` polls `/api/v1/composition`. |

Prefs are only edited while Arena is not running (Arena rewrites them on exit).
A `.bak` copy is made next to each file the first time it is touched. Changing
the Advanced Output preset therefore requires a restart of Arena; see the
`flip_output` macro.

### Synesthesia specifics

No command-line options. Readiness is the first OSC packet received on
`osc.synesthesia.listen`, which requires Synesthesia's OSC output to be enabled
and pointed at this machine on that port (Pro license). Startup behaviour
(playlist, play, auto-advance) is plain OSC steps in the `boot` macro.

### VoiceMeeter and Windows audio devices

The `voicemeeter` app (`type: voicemeeter`) drives VoiceMeeter through its official
Remote API and sets Windows default devices, via `src\vmr.ps1` (PowerShell +
the `VoicemeeterRemote64.dll` that ships with VoiceMeeter; no npm dependencies).
Boot runs `action: voicemeeter apply` before anything else makes sound, which:

- applies the `settings` script (labels, routing A1/B1, gains, mutes, the A1
  output device) so a nudged slider cannot silence the show;
- sets the Windows default playback and recording devices from `windows_audio`
  (apps → Voicemeeter Input → Strip 3 → A1 projector HDMI and B1 virtual out);
- hides the unused endpoints listed under `windows_audio.hide` (the VAIO driver
  installs endpoints for every VoiceMeeter edition). They are disabled, not
  removed; list them under `show` to bring one back.

```
node bin/exhibit.js action voicemeeter status     # strips, buses, levels, Windows defaults
node bin/exhibit.js action voicemeeter list       # every playback/recording endpoint and its state
node bin/exhibit.js action voicemeeter apply
node bin/exhibit.js action voicemeeter set "Bus[0].gain = -6"
node bin/exhibit.js action voicemeeter load C:\path\settings.xml
node bin/exhibit.js action voicemeeter restart    # restart the audio engine
```

Signal path: music app → Windows default playback (Voicemeeter Input) →
Strip 3 "PC / Music" → Bus A1 (projector) + Bus B1 (what Synesthesia hears).

### Music (YouTube Music Desktop App)

The `music` app (`type: ytmdesktop`) is the [YouTube Music Desktop App](https://ytmdesktop.github.io/)
(installed per user with `winget install Ytmdesktop.Ytmdesktop`), controlled
through its Companion Server REST API on port 9863. Synesthesia listens to
"Desktop Audio", so this is the exhibit's sound source. The driver enables the
companion server in the app's `config.json` before launch.

One-time setup on the kiosk:

1. `shortcuts\Boot.bat` (or `node bin/exhibit.js launch music`) to start the app,
   then sign in to Google inside it (Premium avoids ads).
2. In the app: Settings > Integrations > turn on **Enable companion authorization**
   (this toggle switches itself off again after a few minutes).
3. Run `shortcuts\Pair Music.bat`, then click **Allow** in the app when the code
   appears. The token is saved to `data\ytmdesktop-token.json`.
4. Put the exhibit playlist (id or `music.youtube.com` URL) in `apps.music.playlist`.

Control it from macros (`music: start`) or the CLI:

```
node bin/exhibit.js music status          # state, title, artist, progress, volume, adPlaying
node bin/exhibit.js music start           # play apps.music.playlist (or video), else resume
node bin/exhibit.js music play | pause | next | prev | mute | unmute | shuffle
node bin/exhibit.js music volume 0.5
node bin/exhibit.js music playlist https://music.youtube.com/playlist?list=...
node bin/exhibit.js music video dQw4w9WgXcQ
```

`music_chrome` (`type: youtube_music`, disabled) is the fallback: YouTube Music in
a dedicated Chrome instance driven over DevTools with the same actions. A local
MP3 player can later reuse that Chrome + DevTools mechanism.

### TouchDesigner specifics

`project` is passed to `TouchPlayer.exe` (perform mode, no editor) or
`TouchDesigner.exe`. Readiness is an OSC heartbeat from the project: add an OSC
Out CHOP sending `/td/alive` about once a second to `127.0.0.1:<listen>`.

## Resolume MCP servers (for Claude Code)

`.mcp.json` registers Resolume's official MCP servers (shipped with Arena and Wire
7.26+ under each install's `mcp\` folder) so Claude Code sessions in this repo can
build compositions and Wire patches by description:

- `resolume-arena`: 22 tools (composition, layer, column, clip, effect, transport,
  file, diagnose, ...). Needs Arena running with its webserver on (the boot macro
  enables it). Per Resolume it cannot edit slices/screens/mappings, presets or
  recordings, and only sees the current deck.
- `resolume-wire`: 13 tools (patch, node, connection, input, output, canvas,
  catalog, ...). Needs Wire running.

Claude Code asks once to approve project-scoped MCP servers; use `/mcp` to see
their state. Docs: https://resolume.com/support/en/mcp-servers

## Development

```
npm test
```

## Security notes

The control server is meant for a single kiosk PC and is not hardened for the open internet.

- **Local only by default.** The server binds `127.0.0.1` and refuses any request whose `Host`
  (or `Origin`, when sent) is not `localhost`, `127.0.0.1` or `[::1]` on the configured port. This
  blocks DNS-rebinding and cross-site requests from other web pages. To use the panel from a phone or
  tablet on the LAN, change the bind address in `src/server.js` and list the names it is reached by
  in `apps.control.allowed_hosts`. No CORS headers are sent.
- **JSON only.** Every `POST` must send `Content-Type: application/json` (415 otherwise), so plain
  HTML forms on another site cannot post to it.
- **Admin password.** `admin.password` lives in `exhibit.local.yaml` (gitignored). It is checked
  with a constant-time comparison (`crypto.timingSafeEqual` over SHA-256 digests); 5 wrong
  attempts from one IP lock that IP out for 60 s. The session token is accepted only in the
  `x-admin-token` header, never in the URL.
- **Admin-gated routes.** Raw OSC (`POST /api/osc`, `POST /api/synesthesia/raw`) always needs
  admin. `/api/action/<app>/<name>` and `/api/music/<name>` need admin unless the pair is in
  `admin.public_actions` (default: Resolume `column`, `master`, `speed`, `simple`, `full`,
  `defaults`; music `play`, `pause`, `next`, `prev`, `shuffle`, which the visitor-facing panel uses).
- **OSC inputs.** The `/input/*` listener accepts packets only from `inputs.allowed_sources`
  (default `127.0.0.1`). Add the IPs of any sensor box (Raspberry Pi, TouchDesigner PC) that
  sends from another machine.
- **Monitoring.** Grafana and Loki bind `127.0.0.1`; Grafana anonymous access is off and its admin
  password comes from `GF_SECURITY_ADMIN_PASSWORD`, not from `grafana.ini`.
- **Not in this repo.** The real `exhibit.yaml`, `exhibit.local.yaml`, `data/` (SQLite events,
  feedback), `logs/` and any tunnel links are gitignored.

