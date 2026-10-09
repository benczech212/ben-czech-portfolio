# Exhibit Macro System — v2 Plan

Fresh start on branch `v2`. The `main` branch (Node server, web UIs, Spotify, ComfyUI)
stays as reference; nothing from it is required by v2. Reusable knowledge from it
(OSC addresses, app paths, Synesthesia output parsing) is folded in below.

## Goal

Standalone "macro" scripts for the kiosk that:

1. Launch Resolume Arena, Synesthesia, and (later) TouchDesigner with the right
   composition / project / output settings.
2. Wait until each app is actually ready, not just running.
3. Send OSC (and Resolume REST) commands after startup.
4. Are driven entirely by a YAML config so paths, ports, presets, and the macro
   sequences can be changed without touching code.

## Verified facts (kiosk, 2026-09-10)

Machine: `<kiosk-user>`, Windows 11. Neither Node nor Python is installed
(the `python` command is only the Microsoft Store stub).

| App | Path |
|-----|------|
| Resolume Arena 7.27.1 | `C:\Program Files\Resolume Arena\Arena.exe` |
| Synesthesia | `C:\Program Files (x86)\Synesthesia\Synesthesia.exe` |
| TouchDesigner | `C:\Program Files\Derivative\TouchDesigner\bin\TouchDesigner.exe` |
| TouchPlayer (perform-only) | `C:\Program Files\Derivative\TouchDesigner\bin\TouchPlayer.exe` |

### Resolume Arena

- **Open a composition on launch:**
  `Arena.exe --suppressCompositionChangeConfirmation "C:\...\SPC Test.avc"`
  (the flag skips the "open composition?" dialog). Existing compositions:
  `Documents\Resolume Arena\Compositions\SPC Test.avc`, `Example.avc`.
- **Advanced Output is NOT stored in the .avc.** The active setup is a pointer in
  `Documents\Resolume Arena\Preferences\AdvancedOutput.xml`:
  ```xml
  <ScreenSetup name="ScreenSetup" presetFile="SPC Final">
  ```
  Presets live in `Documents\Resolume Arena\Presets\Advanced Output\<name>.xml`
  (currently `SPC Final`, `SPC Final Flip`, both two screens: Projector Right /
  Projector Left, 1920x1200 composition). There is no OSC/REST call to switch
  presets, so the macro tool selects one by rewriting `presetFile` **before**
  Arena starts. Preferences are written by Arena on exit, so edits must only
  happen while Arena is not running.
- **OSC input is currently disabled** on this machine
  (`Preferences\osc.xml`: `<Input Enabled="0" Port="7000"/>`). The tool should
  detect this and either enable it (same pre-launch XML edit) or fail loudly.
- **REST API** (webserver) is toggled in `Preferences\server.xml`
  (`<ServerController enabled="0" port="8080">` on the kiosk today); the tool
  enables it before launch.
  `GET /api/v1/composition` returning 200 is the readiness signal, and REST
  covers things OSC cannot (querying layer names, etc.).

### Synesthesia

- No documented command-line arguments. Launch the exe, then do everything via OSC.
- OSC requires a Pro license (already in use on this project). Ports live in
  `%LOCALAPPDATA%\Synesthesia\Data\preferences.json` (`OSC_INPUT_PORT`,
  `OSC_OUTPUT_PORT`, `OSC_OUTPUT_ADDRESS`, ...). On 2026-09-10 the kiosk file had
  input **6000** and output **127.0.0.1:7000** (Resolume's OSC input port!). The
  tool aligns these with `osc.synesthesia` (12000 in / 12001 out) before launch.
- **Readiness signal:** once a scene is running, Synesthesia streams `/audio/*`
  messages continuously on its output port. "First OSC packet received on 12001"
  is a much better ready check than process existence.
- Startup sequence (addresses confirmed in the old server):
  `/playlist/select <name>`, `/playlist/play 1`, `/playlist/shuffle 0|1`,
  `/playlist/auto 0|1`, `/render/enabled 1`.

### TouchDesigner

- `TouchDesigner.exe project.toe` opens a project; `TouchPlayer.exe project.toe`
  opens it in perform mode with no editor. "Start in perform mode" is also a
  per-project setting, so either works.
- Readiness: process running plus an OSC heartbeat from the project (add a
  small OSC Out CHOP to the .toe sending `/td/alive` once a second).

### Music (added 2026-09-10)

Spotify is not an option for now. Sound comes from **YouTube Music in a dedicated
Chrome instance** (own `user_data_dir`, `--remote-debugging-port`, autoplay flag),
driven through the Chrome DevTools Protocol from `src/cdp.js`. Verified on the
kiosk: launch, play, pause, next, volume, status and a clean close all work; an
unsigned profile plays ads, so a signed-in account (Premium) is the intended
setup. Synesthesia's audio input is "Desktop Audio", so this feeds the visuals.

Later: **local MP3 files**. Plan is a `local_player` driver that reuses the same
Chrome + DevTools path with a tiny local HTML page (audio element + playlist),
so the `music` step vocabulary stays identical.

### Resolume MCP servers (added 2026-09-10)

Resolume 7.26+ ships official MCP servers as standalone exes under each app's
`mcp\` folder. `.mcp.json` registers both for Claude Code sessions in this repo:
Arena (22 tools, needs Arena + its webserver on 8080) and Wire (13 tools, needs
Wire + its webserver on 8081; enabled in `Documents\Resolume Wire\Preferences\server.xml`).
Both verified connected on the kiosk. They are development aids for building the
composition and Wire patches, not part of the runtime boot.

## Architecture

```
exhibit.yaml           # committed config: apps, OSC targets, macros
exhibit.local.yaml     # gitignored per-machine overrides (paths, ports)
bin/exhibit.js         # CLI entry point
src/
  config.js            # load + merge YAML, validate paths/presets, expand {{vars}}
  runner.js            # executes a macro (ordered steps) and holds the step handlers
  apps/
    resolume.js        # builds launch args, writes AdvancedOutput.xml / osc.xml, REST ready check
    synesthesia.js     # OSC heartbeat ready check, playlist startup
    touchdesigner.js   # TouchPlayer/TouchDesigner launch, heartbeat ready check
  osc.js               # send + listen (bundle-aware parser carried over from main)
  process.js           # spawn detached, is-running, kill by image name
shortcuts/             # one .bat per macro, for desktop icons and Task Scheduler
docs/PLAN.md
```

**Design rule:** macros are data, not code. A macro is a YAML list of steps. The
step vocabulary is deliberately small; app-specific smarts live in `src/apps/*`
"drivers" that the `launch` and `wait` steps call.

### Step vocabulary

| Step | Meaning |
|------|---------|
| `launch: <app>` | Run the app's pre-launch hook (e.g. write Advanced Output preset), then spawn it detached. Skips if already running unless `force: true`. |
| `wait: <app> \| [apps]` | Block until the app's ready check passes or its timeout expires. |
| `osc: {to, address, args}` | Send one OSC message to a named target. |
| `rest: {to, method, path, body}` | Resolume REST call. |
| `sleep: <seconds>` | Fixed delay. |
| `kill: <app> \| [apps]` | Graceful close (WM_CLOSE) then taskkill after a grace period. |
| `macro: <name>` | Run another macro inline. |
| `shell: <cmd>` | Escape hatch. |

Steps support `on_fail: abort | continue` (default abort) and `{{path.to.value}}`
interpolation from the merged config.

### CLI

```
exhibit run boot                      # run a macro
exhibit launch resolume               # single app
exhibit wait synesthesia
exhibit osc synesthesia /playlist/next 1
exhibit status                        # which apps are running / ready
exhibit stop                          # run the "shutdown" macro
exhibit check                         # validate config: exes exist, preset exists, ports free, OSC enabled
```

Every run logs to `logs/exhibit-YYYY-MM-DD.log` with timestamps, so a failed
morning boot can be diagnosed after the fact.

## Draft `exhibit.yaml`

```yaml
osc:
  resolume:      { host: 127.0.0.1, port: 7000 }
  synesthesia:   { host: 127.0.0.1, port: 12000, listen: 12001 }
  touchdesigner: { host: 127.0.0.1, port: 9002,  listen: 9003 }

apps:
  resolume:
    type: resolume
    exe: C:\Program Files\Resolume Arena\Arena.exe
    process: Arena.exe
    prefs_dir: C:\Users\<kiosk-user>\Documents\Resolume Arena
    composition: C:\Users\<kiosk-user>\Documents\Resolume Arena\Compositions\SPC Test.avc
    advanced_output: SPC Final         # preset name under Presets\Advanced Output
    ensure_osc_input: true             # flip osc.xml Input Enabled="1" if needed
    rest: { port: 8080 }
    ready: { type: rest, timeout: 90 }

  synesthesia:
    type: synesthesia
    exe: C:\Program Files (x86)\Synesthesia\Synesthesia.exe
    process: Synesthesia.exe
    playlist: SPC
    ready: { type: osc_heartbeat, timeout: 60 }

  touchdesigner:
    type: touchdesigner
    exe: C:\Program Files\Derivative\TouchDesigner\bin\TouchPlayer.exe
    process: TouchPlayer.exe
    project: D:\exhibit\exhibit.toe
    enabled: false                     # flip on when the project exists
    ready: { type: osc_heartbeat, address: /td/alive, timeout: 60 }

macros:
  boot:
    - launch: resolume
    - launch: synesthesia
    - wait: [resolume, synesthesia]
    - osc: { to: synesthesia, address: /playlist/select, args: ["{{apps.synesthesia.playlist}}"] }
    - osc: { to: synesthesia, address: /playlist/play,   args: [1] }
    - osc: { to: synesthesia, address: /playlist/auto,   args: [1] }
    - sleep: 2
    - osc: { to: resolume, address: /composition/columns/1/connect, args: [1] }

  shutdown:
    - kill: [touchdesigner, synesthesia, resolume]

  restart:
    - macro: shutdown
    - sleep: 5
    - macro: boot

  flip_output:                         # example: swap projector mapping preset
    - kill: resolume
    - launch: { app: resolume, advanced_output: SPC Final Flip }
    - wait: resolume
```

## Build phases

1. **Skeleton** — config loader with local overrides and `check`, CLI, runner,
   `launch / wait(process) / kill / sleep / osc` steps, logging. `boot` works
   for Resolume + Synesthesia with process-based readiness.
2. **Resolume driver** — composition argument, Advanced Output preset write,
   OSC-input enable, REST readiness, `rest` step.
3. **Synesthesia driver** — OSC listener, heartbeat readiness, playlist startup.
4. **Deployment** — `.bat` shortcuts per macro, Task Scheduler entry to run
   `boot` at logon, install Node LTS on the kiosk.
5. **TouchDesigner driver** — once the .toe exists.
6. **Later** — an operator UI or the old server can import `src/` directly; the
   macro runner becomes its launcher.

## Decisions (2026-09-10)

1. **Runtime:** Node LTS.
2. **Old code:** moved to `legacy/` on `v2` (also still on `main`); not included in this portfolio copy.
3. **Resolume OSC input:** auto-enable via the pre-launch `osc.xml` edit.
4. **Synesthesia OSC ports:** kiosk uses **12000 in** (we send to it) and **12001 out** (it sends to us).

### VoiceMeeter + Windows audio (added 2026-09-10)

`voicemeeter` driver (`src/apps/voicemeeter.js` + `src/vmr.ps1`): VoiceMeeter Remote API
(official `VoicemeeterRemote64.dll`) and Windows Core Audio through PowerShell P/Invoke, no
npm deps. Boot runs `action: voicemeeter apply` first: strip labels/routing/mutes, A1 pinned
to the Epson HDMI, Windows defaults (playback = Voicemeeter Input, recording = Voicemeeter
Out B1), and the unused VAIO endpoints disabled. Gotchas learned: never call the API after
`VBVMR_Logout` (access violation); keep `ref` marshaling in C#; enumerate endpoints with
state mask 3 (not-present devices have no name property). Verified live on the kiosk.

### Pause mode + schedule (added 2026-09-10)

`screens` driver (`src/apps/screens.js` + `src/blackout.ps1`): topmost black WinForms
window per display, message + hidden bottom-right button on the primary monitor; the
button runs the `wake` macro. `pause` = music pause + screens show; `wake` = screens hide
+ music play. Windows scheduled tasks "Exhibit Pause" 19:00 / "Exhibit Wake" 07:00 are
created by `exhibit schedule install` from the `schedule:` block (interactive, runs as the
kiosk user). Boot ends with `macro: { name: pause, between: [19:00, 07:00] }`. Gotcha: a
PowerShell GUI spawned detached from Node must go through `cmd /c start /min`; spawning
it hidden/detached directly never starts. Verified on the kiosk incl. a simulated click.

### Control panel, feedback + analytics (added 2026-09-10 evening)

Control server (`src/server.js`, port 9876) + kiosk Chrome `panel` app on display 1 is the
operator GUI (Exhibit / Music / Visuals / Feedback / System). Synesthesia model lives in the
server (`src/synesthesia_state.js`, persisted to `data/synesthesia_state.json`). Telemetry
(`src/telemetry.js`): `/metrics` for Prometheus, NDJSON events + SQLite (`data/exhibit.db`) +
Loki push. Feedback votes snapshot the whole state. Prometheus 3.14 / Loki 3.7 / Grafana 13.2
run as portable, console-less processes from `C:\dev\tools\monitoring` (configs and the
provisioned "Exhibit Overview" dashboard in `monitoring/`), launched by the `monitoring` macro
inside `boot` with on_fail: continue. Grafana: http://127.0.0.1:3000 (local only; admin password from GF_SECURITY_ADMIN_PASSWORD).
