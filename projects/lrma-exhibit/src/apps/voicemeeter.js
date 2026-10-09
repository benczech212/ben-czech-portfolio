// VoiceMeeter driver: launches the app and pins its mixer state + the Windows default
// audio devices through src/vmr.ps1 (VoiceMeeter Remote API + Core Audio via PowerShell,
// no npm dependencies). Actions: status, apply, set <script>, load <xml>, restart,
// defaults, endpoints, list.
import { execFile } from 'node:child_process';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const HELPER = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'vmr.ps1');
export const ACTIONS = ['status', 'apply', 'set', 'load', 'restart', 'defaults', 'endpoints', 'list', 'volume', 'mute'];

function ps(cmd, payload = {}, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const child = execFile('powershell.exe',
      ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', HELPER, cmd],
      { timeout: timeoutMs, windowsHide: true, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout, stderr) => {
        const text = String(stdout ?? '').trim();
        let data = null;
        try { data = text ? JSON.parse(text) : null; } catch { /* fall through */ }
        if (data && data.error) return reject(new Error(`voicemeeter: ${data.error}`));
        if (err && !data) return reject(new Error(`voicemeeter helper failed: ${String(stderr ?? err.message).trim().slice(0, 400)}`));
        resolve(data);
      });
    child.stdin.end(JSON.stringify(payload));
  });
}

function dllDir(app) { return app.dll_dir ?? (app.exe ? path.dirname(app.exe) : undefined); }

// ── output volume: a 0..1 fader mapped onto one bus's gain in dB ──────────────
// `output` in YAML picks the bus and the dB range. max_db is the exhibit's ceiling:
// the fader cannot go above it, so nobody can turn the room up past what was set.
const clamp01 = (v) => Math.max(0, Math.min(1, Number(v)));
export function outputSpec(app) {
  const o = app.output ?? {};
  const bus = o.bus ?? 0;
  return { bus, param: `Bus[${bus}].gain`, muteParam: `Bus[${bus}].mute`, minDb: o.min_db ?? -60, maxDb: o.max_db ?? 0, name: o.name ?? `A${bus + 1}` };
}
const dbToVolume = (db, o) => (db == null ? null : clamp01((db - o.minDb) / (o.maxDb - o.minDb)));
const volumeToDb = (v, o) => o.minDb + (o.maxDb - o.minDb) * clamp01(v);

// value: 0..1 fader position, or null to just read. { db } sets decibels directly (still capped).
async function volume(app, value) {
  const o = outputSpec(app);
  const payload = { dll_dir: dllDir(app), param: o.param, mute_param: o.muteParam };
  if (value != null && value !== '') {
    const db = typeof value === 'object' && value.db != null
      ? Math.max(o.minDb, Math.min(o.maxDb, Number(value.db)))
      : volumeToDb(typeof value === 'object' ? value.value : value, o);
    payload.db = Math.round(db * 100) / 100;
  }
  const r = await ps('gain', payload, 20000);
  return { bus: o.bus, name: o.name, db: r.db, volume: dbToVolume(r.db, o), muted: r.mute >= 0.5, minDb: o.minDb, maxDb: o.maxDb };
}

export async function prepare() {}

export function launchSpec(app) { return { exe: app.exe, args: app.args ?? [] }; }

// Ready = the Remote API can log in and reports Voicemeeter as running.
export async function isReady(app) {
  try { return (await ps('status', { dll_dir: dllDir(app) }, 20000)).running === true; } catch { return false; }
}

export function readyDefaults() {
  return { type: 'driver', hint: 'VoiceMeeter Remote API login did not report the app running. Check the exe path and that VoicemeeterRemote64.dll is next to it.' };
}

export function check(app) {
  const issues = [];
  const dll = path.join(dllDir(app) ?? '', 'VoicemeeterRemote64.dll');
  if (!fs.existsSync(dll)) issues.push({ level: 'error', msg: `VoicemeeterRemote64.dll not found at ${dll} (set dll_dir)` });
  if (app.load && !fs.existsSync(app.load)) issues.push({ level: 'error', msg: `settings file not found: ${app.load}` });
  return issues;
}

// Apply everything pinned in YAML: optional XML preset, mixer script, Windows defaults, endpoint visibility.
async function apply(app, log) {
  const out = {};
  const dir = dllDir(app);
  if (app.load) { await ps('load', { dll_dir: dir, file: app.load }); out.loaded = app.load; log.info(`voicemeeter: loaded ${app.load}`); }
  if (app.settings) {
    const script = String(app.settings).split(/\r?\n/).map((l) => l.replace(/#.*$/, '').trim()).filter(Boolean).join('; ');
    await ps('set', { dll_dir: dir, script });
    out.settings = script.split(';').length;
    log.info(`voicemeeter: applied ${out.settings} mixer settings`);
  }
  const wa = app.windows_audio ?? {};
  if (wa.playback || wa.recording || wa.playback_communications || wa.recording_communications) {
    out.defaults = await ps('defaults', { dll_dir: dir, ...wa });
    log.info(`voicemeeter: Windows defaults → ${JSON.stringify(out.defaults)}`);
  }
  if (wa.hide || wa.show) {
    const r = await ps('endpoints', { dll_dir: dir, hide: wa.hide ?? {}, show: wa.show ?? {} });
    out.endpoints = r.changed;
    if (r.changed.length) log.info(`voicemeeter: endpoints ${r.changed.join(', ')}`);
  }
  return out;
}

// Peak input level (0..1) of one strip, for other drivers to confirm audio is really flowing.
export async function stripLevel(app, index) {
  const st = await ps('status', { dll_dir: dllDir(app) }, 20000);
  const strip = st?.strips?.[index];
  return strip ? Math.max(...strip.level) : 0;
}

export async function action(app, cfg, log, name, value) {
  if (!ACTIONS.includes(name)) throw new Error(`voicemeeter: unknown action "${name}" (${ACTIONS.join(', ')})`);
  const dir = dllDir(app);
  switch (name) {
    case 'status':    return ps('status', { dll_dir: dir });
    case 'apply':     return apply(app, log);
    case 'set':       if (!value) throw new Error('voicemeeter set needs a script, e.g. "Strip[2].mute = 0"'); await ps('set', { dll_dir: dir, script: String(value) }); return { ok: true };
    case 'load':      if (!value) throw new Error('voicemeeter load needs an .xml path'); return ps('load', { dll_dir: dir, file: value });
    case 'restart':   return ps('restart', { dll_dir: dir });
    case 'defaults':  return ps('defaults', { dll_dir: dir, ...(app.windows_audio ?? {}) });
    case 'endpoints': return ps('endpoints', { dll_dir: dir, hide: app.windows_audio?.hide ?? {}, show: app.windows_audio?.show ?? {} });
    case 'list':      return ps('list', { dll_dir: dir });
    case 'volume':    return volume(app, value);
    case 'mute': {
      const o = outputSpec(app);
      const on = value == null || value === '' ? !((await volume(app, null)).muted) : (value === true || value === 1 || value === '1' || value === 'true');
      await ps('set', { dll_dir: dir, script: `${o.muteParam} = ${on ? 1 : 0}` });
      return volume(app, null);
    }
  }
}
