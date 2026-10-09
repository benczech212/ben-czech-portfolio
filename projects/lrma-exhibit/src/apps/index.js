// Driver registry + the shared launch / readiness / kill logic every driver relies on.
// A driver may override isRunning(app, cfg), kill(app, cfg, log) and provide
// action(app, cfg, log, name, value) for app-specific commands (e.g. music play).
import fs from 'node:fs';
import * as resolume from './resolume.js';
import * as synesthesia from './synesthesia.js';
import * as touchdesigner from './touchdesigner.js';
import * as chrome from './chrome.js';
import * as youtube_music from './youtube_music.js';
import * as ytmdesktop from './ytmdesktop.js';
import * as voicemeeter from './voicemeeter.js';
import * as screens from './screens.js';
import * as keepalive from './keepalive.js';
import * as control from './control.js';
import * as generic from './generic.js';
import { isRunning as processRunning, kill as killProcess, launchDetached, waitUntil, httpOk, tcpOpen, sleep, findByCmdline, killPids } from '../process.js';
import { waitForMessage } from '../osc.js';

const DRIVERS = { resolume, synesthesia, touchdesigner, chrome, youtube_music, ytmdesktop, voicemeeter, screens, keepalive, control, generic };

export function driverFor(app) {
  return DRIVERS[app.type] ?? generic;
}

export function processName(app) {
  if (app.process) return app.process;
  if (app.exe) return app.exe.split(/[\\/]/).pop();
  throw new Error(`App "${app.id}" has neither process nor exe configured`);
}

// `match_cmdline: <text>` tells apps apart that share an executable (e.g. two python.exe services):
// running/kill then go by the process command line instead of the image name.
export async function appIsRunning(app, cfg) {
  const drv = driverFor(app);
  if (drv.isRunning) return drv.isRunning(app, cfg);
  if (app.match_cmdline) return (await findByCmdline(processName(app), app.match_cmdline)).length > 0;
  return processRunning(processName(app));
}

export async function killApp(app, cfg, log) {
  const drv = driverFor(app);
  if (drv.kill) return drv.kill(app, cfg, log);
  const graceMs = (cfg.defaults?.kill_grace ?? 8) * 1000;
  if (app.match_cmdline) return killPids(await findByCmdline(processName(app), app.match_cmdline), { graceMs });
  return killProcess(processName(app), { graceMs });
}

export function runAction(app, cfg, log, name, value) {
  const drv = driverFor(app);
  if (!drv.action) throw new Error(`${app.id}: app type "${app.type}" has no actions`);
  return drv.action(app, cfg, log, name, value);
}

// Effective readiness spec: driver defaults overridden by app.ready from YAML.
export function readySpec(app, cfg) {
  const d = driverFor(app).readyDefaults(app, cfg) ?? { type: 'process' };
  const r = app.ready ?? {};
  const merged = { ...d, ...r };
  if (!r.type) merged.type = d.type;
  merged.timeout ??= 60;
  return merged;
}

export async function launchApp(app, cfg, log, { force = false, dryRun = false } = {}) {
  const drv = driverFor(app);
  if (app.enabled === false) { log.info(`${app.id}: disabled in config, skipping`); return 'disabled'; }
  if (drv.NO_EXE) throw new Error(`${app.id}: type "${app.type}" is not launchable; use an action step instead`);
  if (!force && await appIsRunning(app, cfg)) { log.info(`${app.id}: already running, skipping launch`); return 'running'; }
  if (!app.exe || !fs.existsSync(app.exe)) throw new Error(`${app.id}: exe not found: ${app.exe}`);

  if (dryRun) { log.info(`${app.id}: [dry-run] would prepare + launch ${app.exe}`); return 'dry-run'; }
  await drv.prepare(app, cfg, log);
  const { exe, args } = drv.launchSpec(app, cfg);
  const pid = launchDetached(exe, args, { cwd: app.cwd, hideConsole: app.hide_console === true });
  log.info(`${app.id}: launched pid ${pid}: "${exe}" ${args.map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(' ')}`);
  return 'launched';
}

// If the control server is up it owns the OSC listen port; ask it for the heartbeat age instead of
// binding the port ourselves (two listeners on one UDP port would split the packets).
// Returns true/false when the server answered, null when it is not running.
async function serverHeartbeat(cfg, port, maxAgeMs = 5000) {
  const ctl = cfg.apps?.control;
  if (!ctl || ctl.enabled === false) return null;
  try {
    const res = await fetch(`http://127.0.0.1:${ctl.port ?? 9876}/api/osc/heartbeat/${port}`, { signal: AbortSignal.timeout(1500) });
    if (res.status === 404) return null;
    if (!res.ok) return null;
    const { ageMs } = await res.json();
    return ageMs != null && ageMs < maxAgeMs;
  } catch { return null; }
}

// Single-shot probe (used by `status`). Heartbeat probes wait up to probeMs.
export async function probeReady(app, cfg, probeMs = 2500) {
  const spec = readySpec(app, cfg);
  const host = cfg.osc?.[app.id]?.host ?? '127.0.0.1';
  switch (spec.type) {
    case 'process':       return appIsRunning(app, cfg);
    case 'driver':        return driverFor(app).isReady(app, cfg);
    case 'http':          return httpOk(spec.url, probeMs);
    case 'tcp':           return tcpOpen(spec.host ?? host, spec.port, probeMs);
    case 'osc_heartbeat': {
      const viaServer = await serverHeartbeat(cfg, spec.port);
      if (viaServer !== null) return viaServer;
      try { await waitForMessage(spec.port, { match: spec.address ?? null, timeoutMs: probeMs }); return true; }
      catch { return false; }
    }
    default: throw new Error(`${app.id}: unknown ready.type "${spec.type}"`);
  }
}

export async function waitReady(app, cfg, log, { dryRun = false } = {}) {
  const spec = readySpec(app, cfg);
  const timeoutMs = spec.timeout * 1000;
  if (app.enabled === false) { log.info(`${app.id}: disabled, not waiting`); return; }
  if (dryRun) { log.info(`${app.id}: [dry-run] would wait for ${spec.type} (${spec.timeout}s)`); return; }
  log.info(`${app.id}: waiting for ${describe(spec)} (timeout ${spec.timeout}s)`);
  const t0 = Date.now();
  try {
    if (spec.type === 'osc_heartbeat' && (await serverHeartbeat(cfg, spec.port)) !== null) {
      await waitUntil(() => serverHeartbeat(cfg, spec.port).then((r) => r === true), { timeoutMs, intervalMs: 1500, label: `${app.id} heartbeat (via control server)` });
    } else if (spec.type === 'osc_heartbeat') {
      const msg = await waitForMessage(spec.port, { match: spec.address ?? null, timeoutMs });
      log.debug(`${app.id}: heartbeat ${msg.address} ${JSON.stringify(msg.args)}`);
    } else {
      await waitUntil(() => probeReady(app, cfg, 2000), { timeoutMs, intervalMs: 1500, label: `${app.id} ${spec.type}` });
    }
  } catch (err) {
    const running = await appIsRunning(app, cfg);
    const extra = running && spec.hint ? ` Process is running. ${spec.hint}` : running ? ' Process is running.' : ' Process is NOT running.';
    throw new Error(`${app.id}: ${err.message}.${extra}`);
  }
  if (spec.settle) { log.info(`${app.id}: ready, settling ${spec.settle}s`); await sleep(spec.settle * 1000); }
  log.info(`${app.id}: ready after ${((Date.now() - t0) / 1000).toFixed(1)}s`);
}

function describe(spec) {
  switch (spec.type) {
    case 'process':       return 'process';
    case 'driver':        return 'driver readiness probe';
    case 'http':          return `HTTP ${spec.url}`;
    case 'tcp':           return `TCP ${spec.host ?? '127.0.0.1'}:${spec.port}`;
    case 'osc_heartbeat': return `OSC on udp ${spec.port}${spec.address ? ` (${spec.address})` : ''}`;
    default:              return spec.type;
  }
}

export function checkApp(app, cfg) {
  const issues = [];
  if (app.enabled === false) return [{ level: 'info', msg: 'disabled' }];
  if (driverFor(app).NO_EXE) { /* action-only driver, no executable */ }
  else if (!app.exe) issues.push({ level: 'error', msg: 'exe not set' });
  else if (!fs.existsSync(app.exe)) issues.push({ level: 'error', msg: `exe not found: ${app.exe}` });
  if (!DRIVERS[app.type]) issues.push({ level: 'warn', msg: `unknown type "${app.type}", using generic driver` });
  issues.push(...driverFor(app).check(app, cfg));
  const spec = readySpec(app, cfg);
  if (!['process', 'driver', 'http', 'tcp', 'osc_heartbeat'].includes(spec.type)) issues.push({ level: 'error', msg: `unknown ready.type "${spec.type}"` });
  return issues;
}
