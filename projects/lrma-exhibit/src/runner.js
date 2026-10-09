// Executes macros: ordered YAML step lists. Each step is an object with one
// step-type key (launch, wait, osc, rest, sleep, kill, macro, shell, log) plus
// optional `on_fail: abort|continue` and `label`.
import { exec } from 'node:child_process';
import { promisify } from 'node:util';
import { interpolate, getApp, getOscTarget } from './config.js';
import { launchApp, waitReady, killApp, runAction, driverFor } from './apps/index.js';
import { sleep } from './process.js';
import * as osc from './osc.js';

const execP = promisify(exec);
export const STEP_TYPES = ['launch', 'wait', 'osc', 'rest', 'sleep', 'kill', 'macro', 'shell', 'log', 'music', 'action'];

export function normalizeStep(raw, index) {
  if (typeof raw === 'string') {
    // "macro_name" shorthand → { macro: name }
    return { type: 'macro', params: { name: raw }, on_fail: 'abort', label: raw };
  }
  if (!raw || typeof raw !== 'object') throw new Error(`Step ${index + 1}: must be a mapping`);
  const keys = Object.keys(raw).filter((k) => STEP_TYPES.includes(k));
  if (keys.length !== 1) throw new Error(`Step ${index + 1}: expected exactly one of ${STEP_TYPES.join(', ')} (got ${Object.keys(raw).join(', ') || 'nothing'})`);
  const type = keys[0];
  const v = raw[type];
  let params;
  switch (type) {
    case 'launch': params = typeof v === 'string' ? { app: v } : { ...v }; break;
    case 'wait':
    case 'kill':   params = { apps: Array.isArray(v) ? v : [v] }; break;
    case 'osc':    params = { ...v }; break;
    case 'rest':   params = { ...v }; break;
    case 'sleep':  params = { seconds: Number(v) }; break;
    case 'macro':  params = typeof v === 'string' ? { name: v } : { ...v }; break;   // { name, between: ["19:00", "07:00"] }
    case 'shell':  params = typeof v === 'string' ? { cmd: v } : { ...v }; break;
    case 'log':    params = { message: String(v) }; break;
    case 'music':  params = typeof v === 'string' ? { action: v } : { ...v }; break;
    case 'action': {
      // action: "voicemeeter apply" | { app, name, value }
      if (typeof v === 'string') { const [app, name, ...rest] = v.split(/\s+/); params = { app, name, value: rest.length ? rest.join(' ') : undefined }; }
      else params = { ...v };
      break;
    }
  }
  return { type, params, on_fail: raw.on_fail ?? 'abort', label: raw.label ?? null };
}

export async function runMacro(cfg, name, ctx, depth = 0) {
  const { log } = ctx;
  const steps = cfg.macros[name];
  if (!steps) throw new Error(`Unknown macro "${name}" (known: ${Object.keys(cfg.macros).join(', ') || 'none'})`);
  if (!Array.isArray(steps)) throw new Error(`Macro "${name}" must be a list of steps`);
  if (depth > 8) throw new Error(`Macro nesting too deep at "${name}" (loop?)`);

  const pad = '  '.repeat(depth);
  log.info(`${pad}▶ macro "${name}" (${steps.length} steps)${ctx.dryRun ? ' [dry-run]' : ''}`);
  const t0 = Date.now();
  let failures = 0;
  for (let i = 0; i < steps.length; i++) {
    const step = normalizeStep(steps[i], i);
    let desc = step.label ?? summarize(step);
    try { desc = step.label ?? summarize({ ...step, params: interpolate(step.params, cfg) }); } catch { /* reported by runStep */ }
    log.info(`${pad}  ${i + 1}/${steps.length} ${desc}`);
    try {
      await runStep(cfg, step, ctx, depth);
    } catch (err) {
      failures++;
      if (step.on_fail === 'continue') {
        log.warn(`${pad}  step ${i + 1} failed (continuing): ${err.message}`);
      } else {
        log.error(`${pad}  step ${i + 1} failed: ${err.message}`);
        throw new Error(`macro "${name}" aborted at step ${i + 1} (${desc}): ${err.message}`);
      }
    }
  }
  log.info(`${pad}✔ macro "${name}" done in ${((Date.now() - t0) / 1000).toFixed(1)}s${failures ? ` (${failures} step(s) failed, continued)` : ''}`);
  return failures;
}

export function summarize(step) {
  const p = step.params;
  switch (step.type) {
    case 'launch': return `launch ${p.app}`;
    case 'wait':   return `wait ${p.apps.join(', ')}`;
    case 'kill':   return `kill ${p.apps.join(', ')}`;
    case 'osc':    return `osc → ${p.to} ${p.address} ${JSON.stringify(p.args ?? [])}`;
    case 'rest':   return `rest ${(p.method ?? 'GET').toUpperCase()} ${p.path}`;
    case 'sleep':  return `sleep ${p.seconds}s`;
    case 'macro':  return `macro ${p.name}${p.between ? ` (only between ${p.between[0]} and ${p.between[1]})` : ''}`;
    case 'shell':  return `shell ${p.cmd}`;
    case 'log':    return `log`;
    case 'music':  return `music ${p.action}${p.value != null ? ` ${p.value}` : ''}`;
    case 'action': return `${p.app} ${p.name}${p.value != null ? ` ${p.value}` : ''}`;
  }
}

async function runStep(cfg, step, ctx, depth) {
  const { log, dryRun } = ctx;
  const p = interpolate(step.params, cfg);
  switch (step.type) {
    case 'launch': {
      const { app: id, force, ...overrides } = p;
      const app = { ...getApp(cfg, id), ...overrides };
      return launchApp(app, cfg, log, { force: !!force || !!ctx.force, dryRun });
    }
    case 'wait':
      return Promise.all(p.apps.map((id) => waitReady(getApp(cfg, id), cfg, log, { dryRun })));
    case 'kill':
      for (const id of p.apps) {
        const app = getApp(cfg, id);
        if (app.enabled === false) { log.info(`${id}: disabled, not killing`); continue; }
        if (dryRun) { log.info(`${id}: [dry-run] would kill`); continue; }
        const result = await killApp(app, cfg, log);
        log.info(`${id}: ${result}`);
        if (result === 'still-running') throw new Error(`${id}: could not stop it`);
      }
      return;
    case 'osc': {
      const t = getOscTarget(cfg, p.to);
      if (!p.address) throw new Error('osc step needs address');
      if (dryRun) return;
      return osc.send(t.host, t.port, p.address, p.args ?? []);
    }
    case 'rest': {
      const app = getApp(cfg, p.to ?? 'resolume');
      const host = cfg.osc?.[app.id]?.host ?? '127.0.0.1';
      const base = p.base ?? `http://${host}:${app.rest?.port ?? 8080}/api/v1`;
      const url = base + p.path;
      const method = (p.method ?? 'GET').toUpperCase();
      if (dryRun) return;
      const res = await fetch(url, {
        method,
        headers: p.body != null ? { 'content-type': 'application/json' } : undefined,
        body: p.body != null ? JSON.stringify(p.body) : undefined,
        signal: AbortSignal.timeout((p.timeout ?? 5) * 1000),
      });
      if (!res.ok) throw new Error(`${method} ${url} → HTTP ${res.status}`);
      log.debug(`rest ${method} ${url} → ${res.status}`);
      return;
    }
    case 'sleep':
      if (dryRun) return;
      return sleep(p.seconds * 1000);
    case 'macro':
      if (p.between && !isTimeBetween(p.between[0], p.between[1])) { log.info(`skipping macro ${p.name}: current time is outside ${p.between[0]}-${p.between[1]}`); return; }
      return runMacro(cfg, p.name, ctx, depth + 1);
    case 'shell': {
      if (dryRun) return;
      const { stdout, stderr } = await execP(p.cmd, { cwd: p.cwd ?? cfg._root, timeout: (p.timeout ?? 60) * 1000, windowsHide: true });
      if (stdout.trim()) log.info(stdout.trim());
      if (stderr.trim()) log.warn(stderr.trim());
      return;
    }
    case 'log':
      return log.info(p.message);
    case 'music': {
      const app = getApp(cfg, p.app ?? 'music');
      if (app.enabled === false) { log.info(`${app.id}: disabled, skipping music ${p.action}`); return; }
      if (dryRun) return;
      const result = await runAction(app, cfg, log, p.action, p.value);
      log.info(`${app.id}: ${p.action} → ${JSON.stringify(result)}`);
      return result;
    }
    case 'action': {
      const app = getApp(cfg, p.app);
      if (app.enabled === false) { log.info(`${app.id}: disabled, skipping ${p.name}`); return; }
      if (dryRun) return;
      const result = await runAction(app, cfg, log, p.name, p.value);
      log.debug(`${app.id}: ${p.name} → ${JSON.stringify(result).slice(0, 500)}`);
      return result;
    }
  }
}

// "HH:MM" local-time window test; handles overnight windows such as 19:00 → 07:00.
export function isTimeBetween(start, end, now = new Date()) {
  const toMin = (s) => { const m = /^(\d{1,2}):(\d{2})$/.exec(String(s).trim()); if (!m) throw new Error(`bad time "${s}" (use HH:MM)`); return Number(m[1]) * 60 + Number(m[2]); };
  const a = toMin(start), b = toMin(end), t = now.getHours() * 60 + now.getMinutes();
  return a <= b ? (t >= a && t < b) : (t >= a || t < b);
}

// Static validation of every macro: unknown apps/targets/macros surface before a show, not during one.
export function checkMacros(cfg) {
  const issues = [];
  for (const [name, steps] of Object.entries(cfg.macros)) {
    if (!Array.isArray(steps)) { issues.push({ level: 'error', msg: `macro "${name}" is not a list` }); continue; }
    steps.forEach((raw, i) => {
      try {
        const step = normalizeStep(raw, i);
        const p = interpolate(step.params, cfg);
        const where = `macro "${name}" step ${i + 1}`;
        if (step.type === 'launch') getApp(cfg, p.app);
        if (step.type === 'wait' || step.type === 'kill') p.apps.forEach((a) => getApp(cfg, a));
        if (step.type === 'osc') { getOscTarget(cfg, p.to); if (!p.address?.startsWith('/')) issues.push({ level: 'error', msg: `${where}: bad OSC address "${p.address}"` }); }
        if (step.type === 'macro' && !cfg.macros[p.name]) issues.push({ level: 'error', msg: `${where}: unknown macro "${p.name}"` });
        if (step.type === 'macro' && p.between) { try { isTimeBetween(p.between[0], p.between[1]); } catch (e) { issues.push({ level: 'error', msg: `${where}: ${e.message}` }); } }
        if (step.type === 'music') {
          const a = getApp(cfg, p.app ?? 'music');
          if (!['youtube_music', 'chrome', 'ytmdesktop'].includes(a.type)) issues.push({ level: 'error', msg: `${where}: app "${a.id}" (type ${a.type}) has no music actions` });
          if (!p.action) issues.push({ level: 'error', msg: `${where}: music step needs an action` });
        }
        if (step.type === 'action') {
          const a = getApp(cfg, p.app);
          if (!p.name) issues.push({ level: 'error', msg: `${where}: action step needs a name` });
          else if (a.enabled !== false) {
            const drv = driverFor(a);
            if (!drv.action) issues.push({ level: 'error', msg: `${where}: app "${a.id}" (type ${a.type}) has no actions` });
            else if (drv.ACTIONS && !drv.ACTIONS.includes(p.name)) issues.push({ level: 'error', msg: `${where}: unknown action "${p.name}" for ${a.id} (${drv.ACTIONS.join(', ')})` });
          }
        }
        if (step.type === 'sleep' && !(p.seconds >= 0)) issues.push({ level: 'error', msg: `${where}: sleep needs a number of seconds` });
      } catch (err) {
        issues.push({ level: 'error', msg: `macro "${name}" step ${i + 1}: ${err.message}` });
      }
    });
  }
  return issues;
}
