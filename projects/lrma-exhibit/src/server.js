// Control-panel server: serves client/panel.html and a small JSON API over the same
// config/runner the CLI uses. Started by `exhibit serve` (foreground) or the `control`
// app driver (detached at boot). No dependencies beyond node:http.
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFile as execFileCb } from 'node:child_process';
import { promisify } from 'node:util';
import crypto from 'node:crypto';
const execFileP = promisify(execFileCb);
import { fileURLToPath } from 'node:url';
import { loadConfig, getApp } from './config.js';
import { logger } from './log.js';
import { runMacro } from './runner.js';
import { appIsRunning, probeReady, runAction, launchApp, killApp, readySpec } from './apps/index.js';
import { isTimeBetween } from './runner.js';
import { createSynState } from './synesthesia_state.js';
import * as osc from './osc.js';
import { getOscTarget } from './config.js';
import { createTelemetry } from './telemetry.js';
import { createInputHub } from './inputs.js';
import { loadSchemes, resolveSlot, schemeFor, AXES, BUTTONS } from './schemes.js';
import { sceneByBank } from './scenes.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PANEL_HTML = path.join(ROOT, 'client', 'panel.html');

export function startServer({ port = 9876, host = '127.0.0.1', configFile } = {}) {
  const log = logger('server');
  let cfg = loadConfig(ROOT, configFile);

  // Synesthesia live state (owns the OSC listen port; the CLI's readiness check asks us instead of binding)
  let syn = null;
  if (cfg.osc?.synesthesia?.listen) {
    try { syn = createSynState({ listenPort: cfg.osc.synesthesia.listen, sendHost: cfg.osc.synesthesia.host, sendPort: cfg.osc.synesthesia.port, log, persistFile: path.join(ROOT, 'data', 'synesthesia_state.json') }); }
    catch (e) { log.warn(`synesthesia listener not started: ${e.message}`); }
  }

  // ── telemetry: /metrics, NDJSON + Loki + SQLite events, feedback ─────────────
  const tel = createTelemetry({ rootDir: ROOT, config: cfg.telemetry ?? {}, log });
  const { metrics } = tel;
  const startedAt = Date.now();
  tel.logEvent('server', { action: 'start', pid: process.pid });

  // ── idle / engaged: any input → engaged (runs on_engaged macro); quiet → idle ─
  const eng = { mode: 'idle', since: Date.now(), lastInputAt: null, lastSource: null, inputs: 0, timer: null, running: false, resetTimer: null, resetAt: null, lastResetAt: null };
  const engCfg = () => ({ idleSeconds: cfg.engagement?.idle_seconds ?? 30, onEngaged: cfg.engagement?.on_engaged ?? null, onIdle: cfg.engagement?.on_idle ?? null, panelCounts: cfg.engagement?.panel_controls_count !== false, resetSeconds: cfg.engagement?.reset_seconds ?? 600, onReset: cfg.engagement?.on_reset ?? null });
  async function setMode(mode, reason) {
    if (eng.mode === mode) return;
    eng.mode = mode; eng.since = Date.now();
    log.info(`engagement: ${mode} (${reason})`);
    tel.logEvent('engagement', { mode, reason, source: eng.lastSource });
    metrics.inc('engagement_transitions_total', { mode }, 1, 'Idle/engaged transitions');
    const macro = mode === 'engaged' ? engCfg().onEngaged : engCfg().onIdle;
    if (macro && cfg.macros[macro]) {
      eng.running = true;
      try { await runMacro(cfg, macro, { log: { info: (m) => log.debug(m), warn: (m) => log.warn(m), error: (m) => log.error(m), debug: () => {} } }); }
      catch (e) { log.warn(`engagement macro ${macro} failed: ${e.message}`); }
      finally { eng.running = false; }
    }
  }
  // Long idle (engagement.reset_seconds, default 10 min without any input) puts the visuals
  // back to their defaults so the next visitor never inherits the last one's tweaks.
  async function resetToDefaults(reason) {
    eng.resetAt = null; eng.lastResetAt = Date.now();
    const macro = engCfg().onReset;
    if (!macro || !cfg.macros[macro]) return { ok: false, error: `reset macro "${macro ?? '(none)'}" not configured` };
    log.info(`engagement: resetting controls to defaults (${reason})`);
    tel.logEvent('engagement', { action: 'reset_defaults', reason, macro });
    metrics.inc('control_resets_total', {}, 1, 'Automatic/manual resets of the visuals to defaults');
    try { await runMacro(cfg, macro, { log: { info: (m) => log.debug(m), warn: (m) => log.warn(m), error: (m) => log.error(m), debug: () => {} } }); return { ok: true, macro }; }
    catch (e) { log.warn(`reset macro ${macro} failed: ${e.message}`); return { ok: false, error: e.message }; }
  }
  function armReset() {
    clearTimeout(eng.resetTimer);
    const { resetSeconds, onReset } = engCfg();
    if (!onReset || !(resetSeconds > 0)) { eng.resetAt = null; return; }
    eng.resetAt = Date.now() + resetSeconds * 1000;
    eng.resetTimer = setTimeout(() => resetToDefaults(`no input for ${resetSeconds}s`), resetSeconds * 1000);
    eng.resetTimer.unref?.();
  }
  function registerInput(source, detail) {
    eng.lastInputAt = Date.now(); eng.lastSource = source; eng.inputs++;
    metrics.inc('inputs_total', { source }, 1, 'Detected visitor inputs');
    clearTimeout(eng.timer);
    eng.timer = setTimeout(() => setMode('idle', `no input for ${engCfg().idleSeconds}s`), engCfg().idleSeconds * 1000);
    eng.timer.unref?.();
    armReset();
    if (eng.mode !== 'engaged') setMode('engaged', `input from ${source}${detail ? ' (' + JSON.stringify(detail).slice(0, 80) + ')' : ''}`);
  }
  // Input hub: OSC /input/<source>/<control> → engaged + mappings (config `inputs:`)
  const sceneControls = () => {
    const s = syn?.state(); if (!s?.banks) return [];
    const bank = Object.keys(s.banks).find((b) => b !== 'meta' && b !== 'global');
    return bank ? Object.values(s.banks[bank]).filter((c) => c.type === 'float' || c.type === 'toggle') : [];
  };
  let schemes = { scenes: {} };
  const reloadSchemes = () => { try { schemes = loadSchemes(ROOT, cfg); log.info(`input schemes: ${Object.keys(schemes.scenes).length} scenes`); } catch (e) { log.warn(`input schemes: ${e.message}`); } };
  reloadSchemes();
  const inputs = createInputHub({
    cfg, log, syn, registerInput,
    getScheme: (bank, slot) => resolveSlot(schemes, bank, slot),
    runMacro: (name) => submit('macro', `macro ${name}`, (jl) => runMacro(cfg, name, { log: jl, dryRun: false })).catch((e) => log.debug(`input macro ${name}: ${e.message}`)),
    runAction: (appId, action, value) => runAction(getApp(cfg, appId), cfg, log, action, value),
    getSceneControls: sceneControls,
  });

  const engagementState = () => ({ mode: eng.mode, since: eng.since, secondsInMode: Math.round((Date.now() - eng.since) / 1000), lastInputAt: eng.lastInputAt, lastSource: eng.lastSource, inputs: eng.inputs, idleSeconds: engCfg().idleSeconds, idleIn: eng.mode === 'engaged' && eng.lastInputAt ? Math.max(0, Math.round((eng.lastInputAt + engCfg().idleSeconds * 1000 - Date.now()) / 1000)) : null, resetSeconds: engCfg().resetSeconds, resetMacro: engCfg().onReset, resetIn: eng.resetAt ? Math.max(0, Math.round((eng.resetAt - Date.now()) / 1000)) : null, lastResetAt: eng.lastResetAt });

  // ── admin sessions: password → token (header x-admin-token only), idle expiry ──
  const adminSessions = new Map();   // token → expiresAt
  const adminCfg = () => ({ password: cfg.admin?.password ?? null, minutes: cfg.admin?.session_minutes ?? 15, protected: new Set(cfg.admin?.protected_macros ?? ['restart', 'shutdown', 'flip_output', 'monitoring_stop', 'hide_panel']) });
  // App actions the visitor-facing panel may call without an admin unlock. Everything else under
  // /api/action/<app>/<name> and /api/music/<name> needs admin. Override with admin.public_actions.
  const DEFAULT_PUBLIC_ACTIONS = { resolume: ['column', 'master', 'speed', 'simple', 'full', 'defaults'], music: ['play', 'pause', 'next', 'prev', 'shuffle'] };
  const publicAction = (appId, name) => (cfg.admin?.public_actions ?? DEFAULT_PUBLIC_ACTIONS)[appId]?.includes(name) ?? false;
  // Constant-time password check: hash both sides so the buffers are always equal length.
  const sha256 = (s) => crypto.createHash('sha256').update(String(s), 'utf8').digest();
  const passwordMatches = (given, expected) => crypto.timingSafeEqual(sha256(given), sha256(expected));
  // Per-IP lockout after repeated wrong passwords.
  const MAX_FAILS = 5, LOCKOUT_MS = 60000;
  const unlockFails = new Map();   // ip → { count, until }
  function adminToken(req) { return req.headers['x-admin-token'] || null; }
  function adminOk(req) {
    const t = adminToken(req); if (!t) return false;
    const exp = adminSessions.get(t); if (!exp) return false;
    if (Date.now() > exp) { adminSessions.delete(t); return false; }
    adminSessions.set(t, Date.now() + adminCfg().minutes * 60000);   // sliding expiry
    return true;
  }
  const adminDenied = (res) => json(res, 401, { error: 'admin required', admin: true });

  // ── job runner: one macro at a time, output lines kept for the UI ─────────────
  const jobs = [];
  let current = null;
  function jobLogger(job) {
    const push = (level, m) => { job.lines.push({ t: Date.now(), level, m }); if (job.lines.length > 400) job.lines.shift(); };
    return {
      info:  (m) => { log.info(m);  push('info',  m); },
      warn:  (m) => { log.warn(m);  push('warn',  m); },
      error: (m) => { log.error(m); push('error', m); },
      debug: (m) => { log.debug(m); },
    };
  }
  async function submit(kind, label, fn) {
    if (current) throw Object.assign(new Error(`busy: "${current.label}" is still running`), { status: 409 });
    const job = { id: jobs.length + 1, kind, label, started: Date.now(), finished: null, ok: null, error: null, lines: [] };
    jobs.push(job); if (jobs.length > 50) jobs.shift();
    current = job;
    (async () => {
      try { job.result = await fn(jobLogger(job)); job.ok = true; }
      catch (err) { job.ok = false; job.error = err.message; job.lines.push({ t: Date.now(), level: 'error', m: err.message }); }
      finally {
        job.finished = Date.now(); current = null;
        tel.logEvent('macro', { macro: label.replace(/^macro /, ''), ok: job.ok, ms: job.finished - job.started, error: job.error ?? undefined, source: 'panel' });
        metrics.inc('macro_runs_total', { macro: label.replace(/^macro /, ''), result: job.ok ? 'ok' : 'error' }, 1, 'Macro runs');
      }
    })();
    return job;
  }

  // ── cached status ─────────────────────────────────────────────────────────────
  let statusCache = { at: 0, data: null }, statusPending = null;
  async function buildStatus() {
    const apps = await Promise.all(Object.values(cfg.apps).map(async (app) => {
      const row = { id: app.id, type: app.type, enabled: app.enabled !== false, running: false, ready: false };
      if (!row.enabled) return row;
      if (app.type === 'control') { row.running = row.ready = true; row.readyType = 'self'; return row; }   // that's us; probing ourselves would deadlock
      try { row.running = await appIsRunning(app, cfg); } catch {}
      if (row.running) { try { row.ready = await probeReady(app, cfg, 1500); } catch {} }
      row.readyType = readySpec(app, cfg).type;
      return row;
    }));
    let music = null;
    const musicApp = cfg.apps.music;
    if (musicApp && musicApp.enabled !== false && apps.find((a) => a.id === 'music')?.ready) {
      try { music = await runAction(musicApp, cfg, log, 'status'); } catch (e) { music = { error: e.message }; }
    }
    const sched = cfg.schedule ?? {};
    return {
      time: new Date().toISOString(),
      apps, music,
      schedule: sched,
      pausedWindow: sched.pause && sched.wake ? isTimeBetween(sched.pause, sched.wake) : null,
      engagement: engagementState(),
      volume: vol.cache,
      panel: cfg.panel ?? {},
      macros: Object.keys(cfg.macros),
      current: current ? { id: current.id, label: current.label, started: current.started } : null,
    };
  }
  async function getStatus() {
    if (Date.now() - statusCache.at < 4000 && statusCache.data) return statusCache.data;
    if (!statusPending) statusPending = buildStatus().then((d) => { statusCache = { at: Date.now(), data: d }; statusPending = null; return d; }, (e) => { statusPending = null; throw e; });
    return statusPending;
  }

  // ── slow pollers (Resolume REST, VoiceMeeter) with their own caches ─────────
  let resolumeCache = { at: 0, data: null };
  async function getResolume() {
    if (Date.now() - resolumeCache.at < 10000) return resolumeCache.data;
    const app = cfg.apps.resolume;
    let data = null;
    if (app && app.enabled !== false) {
      try {
        const res = await fetch(`http://${cfg.osc?.resolume?.host ?? '127.0.0.1'}:${app.rest?.port ?? 8080}/api/v1/composition`, { signal: AbortSignal.timeout(3000) });
        if (res.ok) {
          const c = await res.json();
          data = {
            composition: c.name?.value ?? null, bpm: c.tempocontroller?.tempo?.value ?? null, master: c.master?.value ?? null,
            columnsConnected: (c.columns ?? []).map((col, i) => (col.connected?.value === 'Connected' ? i + 1 : null)).filter(Boolean),
            layers: (c.layers ?? []).map((l, i) => {
              const clips = (l.clips ?? []).map((cl, j) => ({ index: j + 1, name: cl.name?.value ?? null, state: cl.connected?.value ?? 'Empty' }));
              const active = clips.find((x) => x.state === 'Connected') ?? clips.find((x) => x.state === 'Previewing') ?? null;
              return { index: i + 1, name: l.name?.value ?? null, opacity: l.video?.opacity?.value ?? null, bypassed: !!l.bypassed?.value, solo: !!l.solo?.value, master: l.master?.value ?? null, clip: active };
            }),
          };
        }
      } catch { data = null; }
    }
    resolumeCache = { at: Date.now(), data };
    return data;
  }
  // ── output volume (VoiceMeeter bus gain) ────────────────────────────────────
  // Each set shells out to PowerShell (~0.8 s), so a fader drag is coalesced: one call in
  // flight, only the newest pending value survives, and the result is cached for readers.
  const vol = { cache: null, at: 0, inFlight: null, pending: null };
  async function volumeGet(force = false) {
    if (!force && vol.cache && Date.now() - vol.at < 3000) return vol.cache;
    const app = cfg.apps.voicemeeter;
    if (!app || app.enabled === false) return null;
    try { vol.cache = await runAction(app, cfg, log, 'volume'); vol.at = Date.now(); } catch (e) { log.debug(`volume read: ${e.message}`); }
    return vol.cache;
  }
  function volumeSet(value) {
    vol.pending = value;
    if (vol.inFlight) return vol.inFlight;
    vol.inFlight = (async () => {
      try {
        while (vol.pending != null) {
          const v = vol.pending; vol.pending = null;
          vol.cache = await runAction(cfg.apps.voicemeeter, cfg, log, 'volume', v);
          vol.at = Date.now();
        }
        return vol.cache;
      } finally { vol.inFlight = null; }
    })();
    return vol.inFlight;
  }

  let vmCache = { at: 0, data: null };
  async function getVoicemeeter() {
    if (Date.now() - vmCache.at < 15000) return vmCache.data;
    const app = cfg.apps.voicemeeter;
    let data = null;
    if (app && app.enabled !== false) { try { data = await runAction(app, cfg, log, 'status'); } catch { data = null; } }
    vmCache = { at: Date.now(), data };
    return data;
  }

  // ── PC stats: CPU / memory in-process, GPU via nvidia-smi, disk via fsutil ──
  let cpuPrev = null;
  function cpuPercent() {
    const cpus = os.cpus(); let idle = 0, total = 0;
    for (const c of cpus) { for (const k of Object.keys(c.times)) total += c.times[k]; idle += c.times.idle; }
    const cur = { idle, total }; let pct = null;
    if (cpuPrev) { const dt = cur.total - cpuPrev.total; pct = dt > 0 ? (1 - (cur.idle - cpuPrev.idle) / dt) * 100 : 0; }
    cpuPrev = cur; return pct;
  }
  let gpuCache = { at: 0, data: null }, gpuMissing = false;
  async function getGpu() {
    if (gpuMissing) return null;
    if (Date.now() - gpuCache.at < 4000) return gpuCache.data;
    try {
      const { stdout } = await execFileP('nvidia-smi', ['--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,clocks.gr,clocks.mem,fan.speed,pstate', '--format=csv,noheader,nounits'], { timeout: 4000, windowsHide: true });
      const f = stdout.trim().split('\n')[0].split(',').map((s) => s.trim());
      const num = (s) => (s === '' || s === '[N/A]' ? null : Number(s));
      gpuCache = { at: Date.now(), data: { name: f[0], utilization: num(f[1]), memoryUtilization: num(f[2]), memoryUsedMB: num(f[3]), memoryTotalMB: num(f[4]), temperatureC: num(f[5]), powerW: num(f[6]), clockMHz: num(f[7]), memClockMHz: num(f[8]), fanPercent: num(f[9]), pstate: f[10] } };
    } catch (e) { if (/ENOENT/.test(e.message)) { gpuMissing = true; log.warn('nvidia-smi not found; GPU stats disabled'); } gpuCache = { at: Date.now(), data: null }; }
    return gpuCache.data;
  }
  let diskCache = { at: 0, data: null };
  async function getDisks() {
    if (Date.now() - diskCache.at < 30000) return diskCache.data;
    const out = [];
    for (const drive of cfg.telemetry?.disks ?? ['C:', 'D:']) {
      try {
        const st = fs.statfsSync(drive.endsWith('\\') ? drive : drive + '\\');
        out.push({ drive, freeBytes: Number(st.bavail) * Number(st.bsize), totalBytes: Number(st.blocks) * Number(st.bsize) });
      } catch { /* drive absent */ }
    }
    diskCache = { at: Date.now(), data: out };
    return out;
  }
  async function getSystem() {
    const [gpu, disks] = await Promise.all([getGpu(), getDisks()]);
    return { cpuPercent: cpuPercent(), cpuCount: os.cpus().length, memTotalBytes: os.totalmem(), memFreeBytes: os.freemem(), uptimeSeconds: os.uptime(), loadavg: os.loadavg(), gpu, disks };
  }

  // ── Spout preview service (src/spout_preview.py): sender FPS + JPEG frames ──
  const previewApp = () => cfg.apps.spout_preview;
  const previewBase = () => { const a = previewApp(); const port = a?.args?.[a.args.indexOf('--port') + 1] ?? 9877; return `http://127.0.0.1:${port}`; };
  let previewCache = { at: 0, data: null };
  async function getPreview() {
    const a = previewApp();
    if (!a || a.enabled === false) return null;
    if (Date.now() - previewCache.at < 2000) return previewCache.data;
    let data = null;
    try { const res = await fetch(`${previewBase()}/senders`, { signal: AbortSignal.timeout(1500) }); if (res.ok) data = await res.json(); } catch { data = null; }
    previewCache = { at: Date.now(), data };
    return data;
  }
  function proxyPreview(req, res, upstreamPath) {
    return new Promise((resolve) => {
      const up = http.get(`${previewBase()}${upstreamPath}`, (ur) => {
        res.writeHead(ur.statusCode, { 'content-type': ur.headers['content-type'] ?? 'application/octet-stream', 'cache-control': 'no-store' });
        ur.pipe(res); ur.on('end', resolve); req.on('close', () => { ur.destroy(); resolve(); });
      });
      up.on('error', () => { if (!res.headersSent) json(res, 503, { error: 'preview service not running' }); resolve(); });
    });
  }

  // ── snapshot: everything we know about "now", stored with each feedback vote ──
  function flattenControls(banks) {
    const out = {};
    for (const [bank, controls] of Object.entries(banks ?? {})) for (const c of Object.values(controls)) {
      if (c.type === 'color' && c.rgb) out[`${bank}.${c.name}`] = { r: round(c.rgb.r), g: round(c.rgb.g), b: round(c.rgb.b) };
      else if (c.type === 'xy' && c.xy) out[`${bank}.${c.name}`] = { x: round(c.xy.x), y: round(c.xy.y) };
      else if (c.type === 'dropdown') out[`${bank}.${c.name}`] = { index: c.value, label: c.options?.[Math.round(c.value ?? -1)] ?? null };
      else out[`${bank}.${c.name}`] = round(c.value);
    }
    return out;
  }
  const round = (v) => (typeof v === 'number' ? Math.round(v * 1000) / 1000 : v);
  async function buildSnapshot() {
    const [status, resolume, voicemeeter] = await Promise.all([getStatus().catch(() => null), getResolume(), getVoicemeeter()]);
    const s = syn?.state() ?? null;
    const m = status?.music && !status.music.error ? status.music : null;
    return {
      ts: new Date().toISOString(),
      paused: !!status?.apps?.find((a) => a.id === 'screens')?.running,
      engagement: engagementState(),
      apps: Object.fromEntries((status?.apps ?? []).map((a) => [a.id, a.enabled ? (a.ready ? 'ready' : a.running ? 'running' : 'stopped') : 'disabled'])),
      music: m ? { title: m.title, artist: m.artist, album: m.album, progress: m.progress, duration: m.duration, volume: m.volume, playing: m.playing, playlistId: m.playlistId, queueLength: m.queueLength, adPlaying: m.adPlaying } : null,
      synesthesia: s ? { scene: s.scene, playlist: cfg.apps.synesthesia?.playlist ?? null, audio: Object.fromEntries(Object.entries(s.audio ?? {}).map(([k, v]) => [k, round(v)])), controls: flattenControls(s.banks) } : null,
      output: (await getPreview())?.senders?.map((s) => ({ sender: s.name, fps: s.fps, width: s.width, height: s.height, connected: s.connected })) ?? null,
      system: await getSystem().catch(() => null),
      resolume, voicemeeter: voicemeeter ? {
        strips: (voicemeeter.strips ?? []).map((x) => ({ index: x.index, label: x.label, gain: x.gain, mute: x.mute, A1: x.A1, B1: x.B1, level: x.level })),
        buses: (voicemeeter.buses ?? []).map((b) => ({ name: b.name, device: b.device, gain: b.gain, mute: b.mute, level: b.level })),
        windows: voicemeeter.windows,
      } : null,
      config: {
        schedule: cfg.schedule ?? null,
        music: cfg.apps.music ? { playlist: cfg.apps.music.playlist ?? null, shuffle: !!cfg.apps.music.shuffle, volume: cfg.apps.music.volume ?? null } : null,
        synesthesia: cfg.apps.synesthesia ? { playlist: cfg.apps.synesthesia.playlist ?? null, prefs: cfg.apps.synesthesia.prefs ?? null } : null,
        resolume: cfg.apps.resolume ? { composition: cfg.apps.resolume.composition ?? null, advanced_output: cfg.apps.resolume.advanced_output ?? null } : null,
        osc: cfg.osc,
      },
      lastJob: jobs.length ? { label: jobs[jobs.length - 1].label, ok: jobs[jobs.length - 1].ok, finished: jobs[jobs.length - 1].finished } : null,
      server: { uptimeSeconds: Math.round((Date.now() - startedAt) / 1000), host: os.hostname() },
    };
  }

  // ── metrics sampler ───────────────────────────────────────────────────────────
  let lastScene = null, lastTrack = null, lastPaused = null;
  async function sample() {
    try {
      const status = await getStatus();
      metrics.set('control_uptime_seconds', {}, (Date.now() - startedAt) / 1000, 'Control server uptime');
      const paused = status.apps.find((a) => a.id === 'screens')?.running ? 1 : 0;
      metrics.set('paused', {}, paused, '1 while the pause screens are up');
      if (lastPaused !== null && paused !== lastPaused) tel.logEvent('exhibit', { state: paused ? 'paused' : 'running' });
      lastPaused = paused;
      for (const a of status.apps) {
        if (!a.enabled) continue;
        metrics.set('app_running', { app: a.id }, a.running ? 1 : 0, 'App process running');
        metrics.set('app_ready', { app: a.id }, a.ready ? 1 : 0, 'App passed its readiness check');
      }
      const m = status.music && !status.music.error ? status.music : null;
      if (m) {
        metrics.set('music_playing', {}, m.playing ? 1 : 0, 'Music playing'); metrics.set('music_volume', {}, m.volume ?? 0, 'Music app volume 0..1');
        metrics.set('music_progress_seconds', {}, m.progress ?? 0, 'Track position'); metrics.set('music_duration_seconds', {}, m.duration ?? 0, 'Track length');
        metrics.set('music_queue_length', {}, m.queueLength ?? 0, 'Queue length'); metrics.set('music_ad_playing', {}, m.adPlaying ? 1 : 0, 'Ad playing');
        if (m.title) metrics.setExclusive('music_track_info', { title: m.title, artist: m.artist ?? '', album: m.album ?? '' }, 1, 'Current track (info)');
        const key = `${m.title}|${m.artist}`;
        if (m.title && key !== lastTrack) { if (lastTrack !== null) tel.logEvent('track', { title: m.title, artist: m.artist, album: m.album, duration: m.duration }); lastTrack = key; }
      }
      if (syn) {
        const s = syn.state();
        if (s.scene) { metrics.setExclusive('synesthesia_scene_info', { scene: s.scene }, 1, 'Current Synesthesia scene (info)'); if (s.scene !== lastScene) { if (lastScene !== null) tel.logEvent('scene', { scene: s.scene, previous: lastScene }); lastScene = s.scene; } }
        for (const [band, v] of Object.entries(s.audio ?? {})) { if (band === 'bpm') metrics.set('synesthesia_bpm', {}, v, 'Detected BPM'); else metrics.set('synesthesia_audio', { band }, v, 'Synesthesia audio analysis 0..1'); }
        metrics.set('synesthesia_osc_age_seconds', {}, s.ageMs != null ? s.ageMs / 1000 : 9999, 'Seconds since the last OSC message from Synesthesia');
        metrics.clear('synesthesia_control');
        for (const [bank, controls] of Object.entries(s.banks ?? {})) for (const c of Object.values(controls)) {
          if (c.type === 'color' && c.rgb) for (const k of ['r', 'g', 'b']) metrics.set('synesthesia_control', { bank, name: c.name, component: k }, c.rgb[k], 'Synesthesia control values');
          else if (c.type === 'xy' && c.xy) for (const k of ['x', 'y']) metrics.set('synesthesia_control', { bank, name: c.name, component: k }, c.xy[k], 'Synesthesia control values');
          else if (typeof c.value === 'number') metrics.set('synesthesia_control', { bank, name: c.name, component: 'value' }, c.value, 'Synesthesia control values');
        }
      }
      const r = await getResolume();
      if (r) {
        metrics.set('resolume_bpm', {}, r.bpm ?? 0, 'Arena BPM'); metrics.set('resolume_master', {}, r.master ?? 0, 'Arena master');
        metrics.setExclusive('resolume_composition_info', { composition: r.composition ?? '' }, 1, 'Open composition (info)');
        metrics.clear('resolume_layer_clip_info'); metrics.clear('resolume_layer_opacity');
        r.layers.forEach((l) => { metrics.set('resolume_layer_opacity', { layer: String(l.index), name: l.name ?? '' }, l.opacity ?? 0, 'Layer opacity'); if (l.clip) metrics.set('resolume_layer_clip_info', { layer: String(l.index), name: l.name ?? '', clip: l.clip.name ?? '', state: l.clip.state }, 1, 'Connected clip per layer (info)'); });
      }
      const v = await volumeGet();
      if (v) { metrics.set('output_volume', {}, v.volume ?? 0, 'Exhibit output volume (0..1 of the configured dB range)'); metrics.set('output_volume_db', {}, v.db ?? 0, 'Exhibit output bus gain in dB'); }
      const vm = await getVoicemeeter();
      if (vm) {
        for (const st of vm.strips ?? []) metrics.set('voicemeeter_strip_level', { strip: String(st.index + 1), label: st.label ?? '' }, Math.max(...(st.level ?? [0])), 'VoiceMeeter strip input level');
        for (const b of vm.buses ?? []) metrics.set('voicemeeter_bus_level', { bus: b.name }, Math.max(...(b.level ?? [0])), 'VoiceMeeter bus output level');
      }
      const sys = await getSystem();
      if (sys.cpuPercent != null) metrics.set('system_cpu_percent', {}, sys.cpuPercent, 'Total CPU utilisation %');
      metrics.set('system_memory_total_bytes', {}, sys.memTotalBytes, 'Physical memory'); metrics.set('system_memory_free_bytes', {}, sys.memFreeBytes, 'Free physical memory');
      metrics.set('system_uptime_seconds', {}, sys.uptimeSeconds, 'Windows uptime');
      for (const d of sys.disks) { metrics.set('system_disk_free_bytes', { drive: d.drive }, d.freeBytes, 'Free disk space'); metrics.set('system_disk_total_bytes', { drive: d.drive }, d.totalBytes, 'Disk size'); }
      if (sys.gpu) {
        const g = sys.gpu; const gl = { gpu: g.name ?? 'gpu0' };
        metrics.set('gpu_utilization_percent', gl, g.utilization, 'GPU utilisation %'); metrics.set('gpu_memory_utilization_percent', gl, g.memoryUtilization, 'GPU memory controller utilisation %');
        metrics.set('gpu_memory_used_bytes', gl, g.memoryUsedMB != null ? g.memoryUsedMB * 1048576 : null, 'GPU memory used'); metrics.set('gpu_memory_total_bytes', gl, g.memoryTotalMB != null ? g.memoryTotalMB * 1048576 : null, 'GPU memory total');
        metrics.set('gpu_temperature_celsius', gl, g.temperatureC, 'GPU temperature'); metrics.set('gpu_power_watts', gl, g.powerW, 'GPU power draw');
        metrics.set('gpu_clock_mhz', { ...gl, clock: 'graphics' }, g.clockMHz, 'GPU clocks'); metrics.set('gpu_clock_mhz', { ...gl, clock: 'memory' }, g.memClockMHz, 'GPU clocks');
        metrics.set('gpu_fan_percent', gl, g.fanPercent, 'GPU fan speed %');
      }
      const pv = await getPreview();
      if (pv) {
        metrics.clear('output_fps'); metrics.clear('output_resolution_pixels'); metrics.clear('output_sender_connected');
        for (const s of pv.senders ?? []) {
          if (s.fps != null) metrics.set('output_fps', { sender: s.name }, s.fps, 'Real output frame rate of a Spout sender (from its frame counter)');
          metrics.set('output_sender_connected', { sender: s.name }, s.connected ? 1 : 0, 'Spout sender receivable');
          if (s.width) metrics.set('output_resolution_pixels', { sender: s.name, axis: 'width' }, s.width, 'Sender resolution'), metrics.set('output_resolution_pixels', { sender: s.name, axis: 'height' }, s.height, 'Sender resolution');
        }
        metrics.set('output_frame_counting_enabled', {}, pv.frameCountEnabled ? 1 : 0, 'Spout frame counting enabled (needed for FPS)');
      }
      metrics.set('engaged', {}, eng.mode === 'engaged' ? 1 : 0, '1 while visitors are interacting (engaged mode)');
      const inp = inputs.state(); metrics.set('input_messages_total_gauge', {}, inp.msgCount, 'OSC input messages received (since server start)');
      for (const [k, v] of Object.entries(inp.values)) if (typeof v.value === 'number' && Date.now() - v.at < 10000) { const [source, control] = k.split('/'); metrics.set('input_value', { source, control }, v.value, 'Latest controller values'); }
      const lk = tel.lokiStatus(); metrics.set('loki_queue_length', {}, lk.queued, 'Events waiting to be pushed to Loki');
      const fc = tel.feedbackCounts();   // from SQLite, so it survives server restarts (unlike the counter)
      for (const vote of ['up', 'down']) { metrics.set('feedback_count', { vote, period: 'today' }, fc.today[vote], 'Feedback votes (from the database)'); metrics.set('feedback_count', { vote, period: 'total' }, fc.total[vote], 'Feedback votes (from the database)'); }
    } catch (e) { log.debug(`sampler: ${e.message}`); }
  }
  sample(); const sampler = setInterval(sample, cfg.telemetry?.sample_interval_seconds ? cfg.telemetry.sample_interval_seconds * 1000 : 5000);
  sampler.unref?.();

  // ── http ──────────────────────────────────────────────────────────────────────
  // No CORS headers: the panel is same-origin, and other origins must not drive the exhibit.
  const json = (res, code, body) => { res.writeHead(code, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' }); res.end(JSON.stringify(body)); };
  const readBody = (req) => new Promise((resolve) => { let s = ''; req.on('data', (d) => { s += d; if (s.length > 1e6) req.destroy(); }); req.on('end', () => { try { resolve(s ? JSON.parse(s) : {}); } catch { resolve({}); } }); });

  // Host / Origin allowlist (blocks DNS rebinding and cross-site requests from other pages).
  // Default: localhost / 127.0.0.1 / [::1] on this port. To reach the panel from a phone or tablet on
  // the LAN, bind a LAN address and list the names it is reached by in apps.control.allowed_hosts
  // (e.g. [192.0.2.50, kiosk.local]).
  const allowedHosts = () => new Set(['localhost', '127.0.0.1', '[::1]', ...(cfg.apps?.control?.allowed_hosts ?? [])].map((h) => `${String(h).toLowerCase()}:${port}`));
  function hostAllowed(req) {
    const ok = allowedHosts();
    if (!ok.has(String(req.headers.host ?? '').toLowerCase())) return false;
    const origin = req.headers.origin;
    if (origin == null) return true;   // same-origin GETs and non-browser clients (CLI, Prometheus) send none
    try { const o = new URL(origin); return o.protocol === 'http:' && ok.has(o.host.toLowerCase()); } catch { return false; }
  }

  const server = http.createServer(async (req, res) => {
    if (!hostAllowed(req)) return json(res, 403, { error: 'host or origin not allowed' });
    if (req.method === 'POST' && !/^application\/json\b/i.test(req.headers['content-type'] ?? '')) return json(res, 415, { error: 'content-type must be application/json' });
    const url = new URL(req.url, `http://${req.headers.host}`);
    const p = url.pathname;
    try {
      if (req.method === 'GET' && (p === '/' || p === '/panel')) {
        res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
        return res.end(fs.readFileSync(PANEL_HTML));
      }
      if (req.method === 'GET' && p === '/metrics') { res.writeHead(200, { 'content-type': 'text/plain; version=0.0.4; charset=utf-8' }); return res.end(metrics.render()); }
      if (req.method === 'GET' && p === '/api/status') return json(res, 200, await getStatus());
      if (req.method === 'GET' && p === '/api/snapshot') return json(res, 200, await buildSnapshot());
      if (req.method === 'GET' && p === '/api/system') return json(res, 200, await getSystem());
      if (req.method === 'GET' && p === '/api/engagement') return json(res, 200, engagementState());
      if (req.method === 'POST' && p === '/api/input') {
        const b = await readBody(req);
        if (b.control != null) await inputs.inject({ address: `/input/${b.source ?? 'api'}/${b.control}`, args: [b.value ?? 1] });   // goes through the mappings too
        else registerInput(String(b.source ?? 'unknown').slice(0, 40), b.detail);
        return json(res, 200, { ok: true, engagement: engagementState() });
      }
      if (req.method === 'GET' && p === '/api/inputs') return json(res, 200, inputs.state());
      if (req.method === 'GET' && p === '/api/schemes') return json(res, 200, schemes);
      if (req.method === 'GET' && p === '/api/schemes/current') {
        const s = syn?.state(); const bank = s?.scene ? String(s.scene).toLowerCase().replace(/[^a-z0-9]/g, '') : null;
        const scheme = bank ? schemeFor(schemes, bank) : null; const cat = bank ? sceneByBank(bank) : null;
        const slots = Object.fromEntries([...AXES, ...BUTTONS].map((k) => { const e = scheme?.[k]; if (!e) return [k, null]; const live = s?.banks?.[bank]?.[e.control]; const val = e.sub && live?.xy ? live.xy[e.sub] : live?.value; const desc = cat?.controls.find((c) => c.name === e.control)?.description ?? ''; return [k, { ...e, value: val ?? null, description: desc }]; }));
        return json(res, 200, { scene: s?.scene ?? null, bank, title: scheme?.title ?? cat?.title ?? null, hasScheme: !!scheme, auto: scheme?.auto ?? null, slots });
      }
      if (req.method === 'POST' && p === '/api/schemes/reload') { if (!adminOk(req)) return adminDenied(res); reloadSchemes(); return json(res, 200, { ok: true, scenes: Object.keys(schemes.scenes).length }); }
      if (req.method === 'GET' && p === '/api/inputs/stream') {   // server-sent events, ~30 Hz while values change
        res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store', 'connection': 'keep-alive' });
        res.write(`event: full\ndata: ${JSON.stringify(inputs.state())}\n\n`);
        let lastJson = '';
        const timer = setInterval(() => { const d = inputs.delta(); d.engaged = eng.mode === 'engaged'; const js = JSON.stringify({ values: d.values, mapped: d.mapped, engaged: d.engaged }); if (js !== lastJson) { lastJson = js; res.write(`data: ${JSON.stringify(d)}\n\n`); } }, 33);
        const full = setInterval(() => res.write(`event: full\ndata: ${JSON.stringify(inputs.state())}\n\n`), 2000);
        req.on('close', () => { clearInterval(timer); clearInterval(full); });
        return;
      }
      if (req.method === 'POST' && p === '/api/engagement/idle') { clearTimeout(eng.timer); await setMode('idle', 'forced'); return json(res, 200, { ok: true, engagement: engagementState() }); }
      if (req.method === 'POST' && p === '/api/engagement/reset') { clearTimeout(eng.resetTimer); const r = await resetToDefaults('requested from the panel'); return json(res, r.ok ? 200 : 503, { ...r, engagement: engagementState() }); }
      if (req.method === 'GET' && p === '/api/volume') return json(res, 200, (await volumeGet()) ?? { error: 'voicemeeter not available' });
      if (req.method === 'POST' && p === '/api/volume') {
        const b = await readBody(req);
        const app = cfg.apps.voicemeeter;
        if (!app || app.enabled === false) return json(res, 503, { error: 'voicemeeter not enabled' });
        // mute: true / false, or "toggle" — the driver flips from the LIVE state, so a
        // panel whose cached copy is a few seconds old still toggles the right way.
        if (b.mute != null) { const r = await runAction(app, cfg, log, 'mute', b.mute === 'toggle' ? null : b.mute); vol.cache = r; vol.at = Date.now(); return json(res, 200, r); }
        if (b.value == null && b.db == null) return json(res, 400, { error: 'value (0..1) or db required' });
        return json(res, 200, (await volumeSet(b.db != null ? { db: b.db } : b.value)) ?? { error: 'volume not set' });
      }
      if (req.method === 'GET' && p === '/api/resolume') {
        try { return json(res, 200, await runAction(getApp(cfg, 'resolume'), cfg, log, 'state')); }
        catch (e) { return json(res, 200, { error: e.message, mode: null, groupLayers: [], columns: [] }); }
      }
      if (req.method === 'GET' && p === '/api/preview/senders') { const pv = (await getPreview()) ?? { ready: false, senders: [], error: 'preview service not running' }; return json(res, 200, { ...pv, defaultSender: previewApp()?.default_sender ?? null }); }
      if (req.method === 'GET' && (p === '/api/preview/frame.jpg' || p === '/api/preview/stream')) return proxyPreview(req, res, `/${p.split('/').pop()}${url.search}`);
      if (req.method === 'GET' && p === '/api/feedback') return json(res, 200, { counts: tel.feedbackCounts(), recent: tel.recentFeedback(Number(url.searchParams.get('limit') ?? 20)), loki: tel.lokiStatus() });
      if (req.method === 'POST' && p === '/api/feedback') {
        const b = await readBody(req);
        if (!['up', 'down'].includes(b.vote)) return json(res, 400, { error: 'vote must be "up" or "down"' });
        const snapshot = await buildSnapshot();
        const rec = tel.recordFeedback({ vote: b.vote, note: typeof b.note === 'string' ? b.note.slice(0, 500) : null, tags: Array.isArray(b.tags) ? b.tags.slice(0, 8).map(String) : null, snapshot });
        log.info(`feedback: ${b.vote} (scene ${rec.scene ?? '-'}, track ${rec.track ?? '-'})`);
        return json(res, 200, { ok: true, recorded: { ts: rec.ts, vote: rec.vote, scene: rec.scene, track: rec.track, artist: rec.artist }, counts: tel.feedbackCounts() });
      }
      if (req.method === 'GET' && p === '/api/events') return json(res, 200, tel.recentEvents(Number(url.searchParams.get('limit') ?? 50), url.searchParams.get('type')));
      if (req.method === 'POST' && p === '/api/event') { const b = await readBody(req); if (!b.type) return json(res, 400, { error: 'type required' }); const { type, ...data } = b; return json(res, 200, { ok: true, event: tel.logEvent(String(type).slice(0, 40), data) }); }
      if (req.method === 'GET' && p === '/api/jobs') return json(res, 200, jobs.slice(-10).map((j) => ({ ...j, lines: j.lines.slice(-60) })));
      if (req.method === 'GET' && p === '/api/config') return json(res, 200, { files: cfg._files, macros: cfg.macros, schedule: cfg.schedule ?? {}, panel: cfg.panel ?? {}, apps: Object.fromEntries(Object.entries(cfg.apps).map(([k, a]) => [k, { type: a.type, enabled: a.enabled !== false }])) });
      if (req.method === 'POST' && p === '/api/reload') { if (!adminOk(req)) return adminDenied(res); cfg = loadConfig(ROOT, configFile); inputs.reload(cfg); reloadSchemes(); statusCache = { at: 0, data: null }; return json(res, 200, { ok: true, files: cfg._files }); }

      // ── admin ────────────────────────────────────────────────────────────────
      if (p.startsWith('/api/admin')) {
        const sub = p.slice('/api/admin'.length);
        if (req.method === 'GET' && sub === '/status') { const ok = adminOk(req); const t = adminToken(req); return json(res, 200, { enabled: !!adminCfg().password, unlocked: ok, expiresAt: ok ? adminSessions.get(t) : null, sessionMinutes: adminCfg().minutes, protectedMacros: [...adminCfg().protected] }); }
        if (req.method === 'POST' && sub === '/unlock') {
          const b = await readBody(req);
          if (!adminCfg().password) return json(res, 503, { error: 'admin password not configured (admin.password)' });
          const ip = req.socket.remoteAddress ?? 'unknown';
          let f = unlockFails.get(ip);
          if (f?.until > Date.now()) return json(res, 429, { error: `too many wrong passwords; try again in ${Math.ceil((f.until - Date.now()) / 1000)}s` });
          if (f?.until) f = null;   // lockout served; start counting again
          if (!passwordMatches(b.password ?? '', adminCfg().password)) {
            const count = (f?.count ?? 0) + 1;
            unlockFails.set(ip, { count, until: count >= MAX_FAILS ? Date.now() + LOCKOUT_MS : 0 });
            tel.logEvent('admin', { action: 'unlock_failed', lockedOut: count >= MAX_FAILS });
            await new Promise((r) => setTimeout(r, 800));
            return json(res, 403, { error: 'wrong password' });
          }
          unlockFails.delete(ip);
          const token = [...crypto.getRandomValues(new Uint8Array(24))].map((x) => x.toString(16).padStart(2, '0')).join('');
          adminSessions.set(token, Date.now() + adminCfg().minutes * 60000);
          tel.logEvent('admin', { action: 'unlock' }); log.info('admin unlocked');
          return json(res, 200, { ok: true, token, expiresAt: adminSessions.get(token) });
        }
        if (req.method === 'POST' && sub === '/lock') { const t = adminToken(req); if (t) adminSessions.delete(t); tel.logEvent('admin', { action: 'lock' }); return json(res, 200, { ok: true }); }
        if (!adminOk(req)) return adminDenied(res);
        if (req.method === 'POST' && sub === '/panel') {
          // mode: kiosk (fullscreen), windowed (leave fullscreen), close
          const b = await readBody(req); const app = getApp(cfg, 'panel');
          await killApp(app, cfg, log); await new Promise((r) => setTimeout(r, 800));
          if (b.mode === 'windowed') { await launchApp({ ...app, kiosk: false, window: { x: 160, y: 90, width: 1440, height: 900 } }, cfg, log, { force: true }); }
          else if (b.mode !== 'close') { await launchApp(app, cfg, log, { force: true }); }
          tel.logEvent('admin', { action: 'panel', mode: b.mode });
          return json(res, 200, { ok: true, mode: b.mode ?? 'kiosk' });
        }
        return json(res, 404, { error: 'unknown admin route' });
      }

      let m;
      if (req.method === 'POST' && (m = /^\/api\/run\/([\w-]+)$/.exec(p))) {
        const name = m[1];
        if (!cfg.macros[name]) return json(res, 404, { error: `unknown macro ${name}` });
        if (adminCfg().protected.has(name) && !adminOk(req)) return adminDenied(res);
        const job = await submit('macro', `macro ${name}`, (jl) => runMacro(cfg, name, { log: jl, dryRun: false }));
        statusCache.at = 0;
        return json(res, 202, { job: job.id, label: job.label });
      }
      if (req.method === 'POST' && (m = /^\/api\/action\/([\w-]+)\/([\w-]+)$/.exec(p))) {
        const [, id, name] = m;
        if (!publicAction(id, name) && !adminOk(req)) return adminDenied(res);
        const body = await readBody(req);
        const app = getApp(cfg, id);
        const result = await runAction(app, cfg, log, name, body.value);
        statusCache.at = 0;
        return json(res, 200, { ok: true, result });
      }
      if (req.method === 'POST' && (m = /^\/api\/music\/([\w-]+)$/.exec(p))) {
        if (!publicAction('music', m[1]) && !adminOk(req)) return adminDenied(res);
        const body = await readBody(req);
        const result = await runAction(getApp(cfg, 'music'), cfg, log, m[1], body.value);
        statusCache.at = 0;
        return json(res, 200, { ok: true, result });
      }
      // ── OSC / Synesthesia ────────────────────────────────────────────────────
      if (req.method === 'GET' && (m = /^\/api\/osc\/heartbeat\/(\d+)$/.exec(p))) {
        const port = Number(m[1]);
        if (syn && port === cfg.osc.synesthesia.listen) return json(res, 200, { port, ageMs: syn.heartbeatAge() });
        return json(res, 404, { error: `no listener on ${port}` });
      }
      if (req.method === 'POST' && p === '/api/osc') {
        if (!adminOk(req)) return adminDenied(res);
        const b = await readBody(req); const t = getOscTarget(cfg, b.to);
        if (!b.address?.startsWith('/')) return json(res, 400, { error: 'address must start with /' });
        await osc.send(t.host, t.port, b.address, b.args ?? []);
        return json(res, 200, { ok: true });
      }
      if (p.startsWith('/api/synesthesia')) {
        if (!syn) return json(res, 503, { error: 'synesthesia listener not configured (osc.synesthesia.listen)' });
        const sub = p.slice('/api/synesthesia'.length);
        if (req.method === 'GET' && (sub === '' || sub === '/state')) return json(res, 200, syn.state());
        if (req.method === 'GET' && sub === '/stream') {   // server-sent events: changed controls + audio at ~30 Hz
          res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store', 'connection': 'keep-alive' });
          res.write(`event: full\ndata: ${JSON.stringify(syn.state())}\n\n`);
          let since = Date.now(), lastAudio = '';
          const timer = setInterval(() => { const d = syn.delta(since); since = d.t; const aj = JSON.stringify(d.audio); const hasCtl = Object.keys(d.controls).length > 0; if (hasCtl || aj !== lastAudio) { lastAudio = aj; res.write(`data: ${JSON.stringify(d)}\n\n`); } }, 33);
          const full = setInterval(() => res.write(`event: full\ndata: ${JSON.stringify(syn.state())}\n\n`), 3000);
          req.on('close', () => { clearInterval(timer); clearInterval(full); });
          return;
        }
        const b = req.method === 'POST' ? await readBody(req) : {};
        if (req.method === 'POST') {
          if (sub === '/control') { if (!b.bank || !b.name) return json(res, 400, { error: 'bank and name required' }); await syn.setControl(b.bank, b.name, b.value, b.sub); if (engCfg().panelCounts) registerInput('panel', { control: `${b.bank}/${b.name}` }); return json(res, 200, { ok: true }); }
          if ((m = /^\/control\/(default|random|lock)$/.exec(sub))) { await syn.controlCommand(b.bank, b.name, m[1], b.on); if (engCfg().panelCounts) registerInput('panel', { control: `${b.bank}/${b.name}`, cmd: m[1] }); return json(res, 200, { ok: true }); }
          if ((m = /^\/bank\/(default|random|undo)$/.exec(sub))) { await syn.bankCommand(b.bank ?? 'scene', m[1]); if (engCfg().panelCounts) registerInput('panel', { bank: b.bank ?? 'scene', cmd: m[1] }); return json(res, 200, { ok: true }); }
          if (sub === '/playlist') { await syn.playlist(b.action, b.value); return json(res, 200, { ok: true }); }
          if (sub === '/scene') { await syn.scene(b.name, b.preset); return json(res, 200, { ok: true }); }
          if (sub === '/favslot') { await syn.favslot(Number(b.position)); return json(res, 200, { ok: true }); }
          if (sub === '/preset') { await syn.preset(b.name); return json(res, 200, { ok: true }); }
          if (sub === '/render') { await syn.render(!!b.enabled); return json(res, 200, { ok: true }); }
          if (sub === '/media') { await syn.media(b.name); return json(res, 200, { ok: true }); }
          if (sub === '/raw') { if (!adminOk(req)) return adminDenied(res); if (!b.address?.startsWith('/')) return json(res, 400, { error: 'address must start with /' }); await syn.raw(b.address, b.args ?? []); return json(res, 200, { ok: true }); }
        }
        return json(res, 404, { error: 'unknown synesthesia route' });
      }
      if (req.method === 'POST' && (m = /^\/api\/(launch|kill)\/([\w-]+)$/.exec(p))) {
        if (!adminOk(req)) return adminDenied(res);
        const app = getApp(cfg, m[2]);
        const result = m[1] === 'launch' ? await launchApp(app, cfg, log, {}) : await killApp(app, cfg, log);
        statusCache.at = 0;
        return json(res, 200, { ok: true, result });
      }
      return json(res, 404, { error: 'not found' });
    } catch (err) {
      log.warn(`${req.method} ${p} → ${err.message}`);
      return json(res, err.status ?? 500, { error: err.message });
    }
  });

  return new Promise((resolve, reject) => {
    server.on('error', reject);
    server.listen(port, host, () => { log.info(`control panel on http://${host}:${port}/`); resolve(server); });
  });
}
