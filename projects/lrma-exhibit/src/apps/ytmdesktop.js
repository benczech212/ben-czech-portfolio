// YouTube Music Desktop App (ytmdesktop.github.io) driver: talks to its Companion
// Server REST API on http://127.0.0.1:9863/api/v1. Same action vocabulary as the
// Chrome-based youtube_music driver so `music:` steps are interchangeable.
//
// One-time setup: sign in to Google inside the app, then `exhibit music pair` and
// click "Allow" in the app within 30 s. The token is kept in data/ytmdesktop-token.json.
// The companion server itself is enabled by editing the app's config.json before launch.
import fs from 'node:fs';
import path from 'node:path';
import { kill as killProcess, launchDetached, httpOk, sleep } from '../process.js';
import { stripLevel } from './voicemeeter.js';

const APP_ID = 'exhibit';
const APP_NAME = 'Exhibit Macros';
const APP_VERSION = '2.0.0';

export const ACTIONS = ['status', 'play', 'pause', 'next', 'prev', 'volume', 'mute', 'unmute', 'shuffle', 'seek', 'playlist', 'video', 'start', 'pair'];

export function configFile(app) {
  return app.config_file ?? path.join(process.env.APPDATA ?? '', 'YouTube Music Desktop App', 'config.json');
}
export function tokenFile(app, cfg) {
  return app.token_file ?? path.join(cfg._root, 'data', 'ytmdesktop-token.json');
}
function base(app) { return `http://127.0.0.1:${app.port ?? 9863}/api/v1`; }

function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}

// ─── Driver hooks ─────────────────────────────────────────────────────────────

export async function prepare(app, cfg, log) {
  if (app.ensure_companion === false) return;
  const file = configFile(app);
  const conf = readJson(file);
  if (!conf) { log.warn(`YTMDesktop config not found at ${file} (start the app once); companion server not verified`); return; }
  const want = { companionServerEnabled: true };
  const integ = conf.integrations ?? {};
  const diff = Object.entries(want).filter(([k, v]) => integ[k] !== v);
  if (!diff.length) { log.info('YTMDesktop companion server already enabled'); return; }
  const bak = file + '.bak';
  if (!fs.existsSync(bak)) fs.copyFileSync(file, bak);
  conf.integrations = { ...integ, ...want };
  fs.writeFileSync(file, JSON.stringify(conf, null, '\t'));
  log.info(`YTMDesktop config updated: ${diff.map(([k, v]) => `${k}=${v}`).join(', ')}`);
}

export function launchSpec(app) {
  return { exe: app.exe, args: app.args ?? [] };
}

export function readyDefaults(app) {
  return { type: 'http', url: `http://127.0.0.1:${app.port ?? 9863}/metadata`, hint: 'YTMDesktop companion server not answering. Check Settings > Integrations > Companion Server is enabled.' };
}

export function check(app, cfg) {
  const issues = [];
  const conf = readJson(configFile(app));
  if (!conf) issues.push({ level: 'warn', msg: `config.json not found at ${configFile(app)}; companion server cannot be verified/enabled until the app has run once` });
  else if (!conf.integrations?.companionServerEnabled) issues.push({ level: 'info', msg: 'companion server is disabled in YTMDesktop config; will be enabled at next launch' });
  if (!fs.existsSync(tokenFile(app, cfg))) issues.push({ level: 'warn', msg: `not paired yet: run "exhibit music pair" (token file ${tokenFile(app, cfg)})` });
  if (!app.playlist && !app.video) issues.push({ level: 'warn', msg: 'neither playlist nor video set; "start" will just resume whatever the app has loaded' });
  return issues;
}

// ─── API helpers ──────────────────────────────────────────────────────────────

async function api(app, cfg, method, route, body, { auth = true, timeoutMs = 8000 } = {}) {
  const headers = { 'content-type': 'application/json' };
  if (auth) {
    const tok = readJson(tokenFile(app, cfg))?.token;
    if (!tok) throw new Error('YTMDesktop not paired: run "exhibit music pair" and approve in the app');
    headers.authorization = tok;
  }
  let res, text, data;
  // Every route is rate-limited; on 429 wait what the server asks (bounded) and retry.
  for (let attempt = 0; ; attempt++) {
    res = await fetch(`${base(app)}${route}`, {
      method, headers, body: body != null ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(timeoutMs),
    });
    text = await res.text();
    data = null; try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (res.status !== 429 || attempt >= 4) break;
    const m = /retry in (\d+)/i.exec(data?.message ?? '');
    await sleepMs(Math.min(10, m ? Number(m[1]) : 2) * 1000 + 200);
  }
  if (data?.code === 'AUTHORIZATION_DISABLED') throw new Error('YTMDesktop refuses pairing: open the app, Settings > Integrations, turn on "Enable companion authorization" (it auto-disables after a few minutes), then run pair again.');
  if (res.status === 401 || res.status === 403) throw new Error(`YTMDesktop ${route} → ${res.status} ${data?.message ?? ''}: token missing/invalid or authorization disabled in the app. Re-run "exhibit music pair".`);
  if (!res.ok) throw new Error(`YTMDesktop ${method} ${route} → HTTP ${res.status} ${typeof data === 'string' ? data : JSON.stringify(data)}`);
  return data;
}

const command = (app, cfg, name, data) => api(app, cfg, 'POST', '/command', data === undefined ? { command: name } : { command: name, data });

const TRACK_STATE = { '-1': 'unknown', 0: 'paused', 1: 'playing', 2: 'buffering' };

function summarize(state) {
  const p = state?.player ?? {};
  const v = state?.video ?? null;
  return {
    state: TRACK_STATE[p.trackState] ?? String(p.trackState),
    playing: p.trackState === 1,
    stateKnown: p.trackState !== -1 && p.trackState != null,   // the app's player hook sometimes fails: state stays "unknown" while audio plays
    hasMedia: !!v,
    title: v?.title ?? null,
    artist: v?.author ?? null,
    album: v?.album ?? null,
    thumbnail: Array.isArray(v?.thumbnails) && v.thumbnails.length ? v.thumbnails[v.thumbnails.length - 1].url : null,
    progress: p.videoProgress != null ? Math.round(p.videoProgress) : null,
    duration: v?.durationSeconds ?? null,
    volume: p.volume != null ? p.volume / 100 : null,
    muted: p.muted ?? null,
    adPlaying: p.adPlaying ?? null,
    playlistId: state?.playlistId ?? null,
    queueLength: p.queue?.items?.length ?? 0,
  };
}

async function status(app, cfg) {
  return summarize(await api(app, cfg, 'GET', '/state'));
}

// Accepts a playlist id, a video id, or any music.youtube.com URL.
export function parseTarget(value) {
  const s = String(value ?? '').trim();
  if (!s) return {};
  try {
    const u = new URL(s);
    return { playlistId: u.searchParams.get('list') || null, videoId: u.searchParams.get('v') || null };
  } catch {
    return /^(PL|RD|OL|VL|LL)/.test(s) || s.length > 20 ? { playlistId: s, videoId: null } : { playlistId: null, videoId: s };
  }
}

async function changeVideo(app, cfg, target) {
  if (!target.playlistId && !target.videoId) throw new Error('playlist/video: nothing to play (give a playlist id, video id, or music.youtube.com URL)');
  await command(app, cfg, 'changeVideo', { videoId: target.videoId ?? null, playlistId: target.playlistId ?? null });
  // The app needs a moment to load; poll gently (the API is rate-limited).
  for (let i = 0; i < 6; i++) {
    await sleepMs(2000);
    const s = await status(app, cfg);
    if (s.playing) return s;
  }
  return status(app, cfg);
}

async function pair(app, cfg, log) {
  const { code } = await api(app, cfg, 'POST', '/auth/requestcode', { appId: APP_ID, appName: APP_NAME, appVersion: APP_VERSION }, { auth: false });
  log.info(`Pairing code ${code}: click ALLOW in the YouTube Music Desktop App window within 30 seconds...`);
  console.log(`\n  >>> Approve "${APP_NAME}" (code ${code}) in the YouTube Music Desktop App window now. <<<\n`);
  const { token } = await api(app, cfg, 'POST', '/auth/request', { appId: APP_ID, code }, { auth: false, timeoutMs: 40000 });
  const file = tokenFile(app, cfg);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ token, appId: APP_ID, pairedAt: new Date().toISOString() }, null, 2));
  log.info(`Paired. Token saved to ${file}`);
  return { paired: true, tokenFile: file };
}

// ─── Actions ──────────────────────────────────────────────────────────────────

export async function action(app, cfg, log, name, value) {
  if (!ACTIONS.includes(name)) throw new Error(`music: unknown action "${name}" (${ACTIONS.join(', ')})`);
  switch (name) {
    case 'status':   return status(app, cfg);
    case 'pair':     return pair(app, cfg, log);
    case 'play':     await command(app, cfg, 'play');     await sleepMs(1000); return status(app, cfg);
    case 'pause':    await command(app, cfg, 'pause');    await sleepMs(1000); return status(app, cfg);
    case 'next':     await command(app, cfg, 'next');     await sleepMs(1500); return status(app, cfg);
    case 'prev':     await command(app, cfg, 'previous'); await sleepMs(1500); return status(app, cfg);
    case 'mute':     await command(app, cfg, 'mute');     return status(app, cfg);
    case 'unmute':   await command(app, cfg, 'unmute');   return status(app, cfg);
    case 'shuffle':  await command(app, cfg, 'shuffle');  return status(app, cfg);
    case 'seek':     await command(app, cfg, 'seekTo', Number(value)); return status(app, cfg);
    case 'volume': {
      const x = Number(value);
      if (!(x >= 0 && x <= 1)) throw new Error('music volume expects 0..1');
      await command(app, cfg, 'setVolume', Math.round(x * 100));
      return status(app, cfg);
    }
    case 'playlist': return changeVideo(app, cfg, parseTarget(value));
    case 'video':    return changeVideo(app, cfg, parseTarget(value));
    case 'start':     return start(app, cfg, log);
  }
}

// Boot-time entry point: play the configured playlist/video (else resume), then make sure
// audio is actually flowing. The companion API's trackState is unreliable (often -1 "unknown"
// even while playing), so when `verify_audio` names a VoiceMeeter app + strip we trust the
// meter instead. Escalation: play again → change video again → relaunch the app once.
// Kill + relaunch the app; prepare() re-enables the companion server in config.json on the way.
async function relaunch(app, cfg, log, why) {
  log.warn(`${app.id}: ${why}; relaunching the app`);
  await killProcess(app.process ?? 'youtube-music-desktop-app.exe', { graceMs: 8000 });
  await sleepMs(2000);
  await prepare(app, cfg, log);
  launchDetached(app.exe, app.args ?? []);
  const url = `http://127.0.0.1:${app.port ?? 9863}/metadata`;
  for (let i = 0; i < 60 && !(await httpOk(url, 1500)); i++) await sleep(1000);
  if (!(await httpOk(url, 1500))) throw new Error(`${app.id}: companion server still not answering after relaunch (Settings > Integrations > Companion Server)`);
  await sleepMs((app.ready?.settle ?? 3) * 1000 + 5000);
}

async function start(app, cfg, log) {
  // The app occasionally comes up with its companion server off (the API is then unreachable while
  // the process runs). A relaunch through prepare() fixes the config; do that before anything else.
  if (!(await httpOk(`http://127.0.0.1:${app.port ?? 9863}/metadata`, 1500))) await relaunch(app, cfg, log, 'companion server not reachable');
  const target = app.playlist ? parseTarget(app.playlist) : app.video ? parseTarget(app.video) : null;
  const kick = async () => {
    if (!target) { await command(app, cfg, 'play'); return status(app, cfg); }
    let s = await changeVideo(app, cfg, target);
    if (app.shuffle && s.queueLength > 1) { await sleepMs(1500); await command(app, cfg, 'shuffle'); await sleepMs(1500); s = await status(app, cfg); }
    return s;
  };
  let s = await kick();
  const verify = app.verify_audio;
  if (!verify?.app) return s;   // no meter available: best effort
  const vmApp = cfg.apps[verify.app];
  if (!vmApp) throw new Error(`music.verify_audio.app "${verify.app}" not found`);
  const stripIdx = verify.strip ?? 2, threshold = verify.threshold ?? 0.01, timeout = (verify.timeout ?? 30) * 1000;

  const audible = async (ms) => {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
      let lvl = 0; try { lvl = await stripLevel(vmApp, stripIdx); } catch (e) { log.debug(`verify_audio: ${e.message}`); }
      if (lvl > threshold) return lvl;
      await sleepMs(3000);
    }
    return 0;
  };

  for (const [attempt, remedy] of [['initial', null], ['play', 'play'], ['change video', 'kick'], ['relaunch', 'relaunch']]) {
    if (remedy === 'play') { await command(app, cfg, 'play'); }
    else if (remedy === 'kick') { s = await kick(); }
    else if (remedy === 'relaunch') {
      await relaunch(app, cfg, log, 'no audio after retries');
      s = await kick();
    }
    const lvl = await audible(timeout);
    if (lvl > 0) {
      log.info(`${app.id}: audio confirmed on ${verify.app} strip ${stripIdx + 1} (level ${lvl.toFixed(2)}${remedy ? `, after ${attempt}` : ''})`);
      const final = await status(app, cfg);
      return { ...final, audioConfirmed: true, level: lvl };
    }
    log.warn(`${app.id}: no audio on ${verify.app} strip ${stripIdx + 1} within ${timeout / 1000}s (${attempt})`);
  }
  throw new Error(`${app.id}: started but no audio is reaching ${verify.app} strip ${stripIdx + 1}`);
}

const sleepMs = (ms) => new Promise((r) => setTimeout(r, ms));
