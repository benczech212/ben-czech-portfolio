// Resolume Arena driver.
//  - composition is passed on the command line (with the confirmation dialog suppressed)
//  - Advanced Output preset is selected by rewriting presetFile= in Preferences\AdvancedOutput.xml
//  - OSC input is enabled by editing Preferences\osc.xml
// Prefs edits only happen while Arena is NOT running: Arena rewrites its prefs on exit.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { httpOk } from '../process.js';

export function prefsDir(app) {
  return app.prefs_dir ?? path.join(os.homedir(), 'Documents', 'Resolume Arena');
}

export function presetPath(app, name) {
  return path.join(prefsDir(app), 'Presets', 'Advanced Output', `${name}.xml`);
}

function restUrl(app, cfg) {
  const host = cfg.osc?.resolume?.host ?? '127.0.0.1';
  const port = app.rest?.port ?? 8080;
  return `http://${host}:${port}/api/v1/composition`;
}

// Replace or insert an attribute on the first occurrence of <tag ...>.
function setXmlAttr(xml, tag, attr, value) {
  const tagRe = new RegExp(`<${tag}\\b([^>]*)>`);
  const m = xml.match(tagRe);
  if (!m) return null;
  let attrs = m[1];
  const attrRe = new RegExp(`\\b${attr}="[^"]*"`);
  attrs = attrRe.test(attrs) ? attrs.replace(attrRe, `${attr}="${value}"`) : `${attrs.replace(/\s*\/$/, '')} ${attr}="${value}"${/\/$/.test(m[1].trim()) ? '/' : ''}`;
  return xml.replace(tagRe, `<${tag}${attrs}>`);
}

function editXml(file, log, mutate) {
  if (!fs.existsSync(file)) { log.warn(`${path.basename(file)} not found (Arena writes it on first run); skipping`); return false; }
  const before = fs.readFileSync(file, 'utf8');
  const after = mutate(before);
  if (after == null) { log.warn(`Could not find expected element in ${path.basename(file)}; left untouched`); return false; }
  if (after === before) return false;
  const bak = file + '.bak';
  if (!fs.existsSync(bak)) fs.copyFileSync(file, bak);
  fs.writeFileSync(file, after);
  return true;
}

export async function prepare(app, cfg, log) {
  const prefs = path.join(prefsDir(app), 'Preferences');

  if (app.advanced_output) {
    const preset = presetPath(app, app.advanced_output);
    if (!fs.existsSync(preset)) throw new Error(`Advanced Output preset not found: ${preset}`);
    const changed = editXml(path.join(prefs, 'AdvancedOutput.xml'), log,
      (xml) => setXmlAttr(xml, 'ScreenSetup', 'presetFile', app.advanced_output));
    log.info(changed ? `Advanced Output preset set to "${app.advanced_output}"`
                     : `Advanced Output preset already "${app.advanced_output}"`);
  }

  if (app.ensure_osc_input !== false) {
    const port = cfg.osc?.resolume?.port ?? 7000;
    const changed = editXml(path.join(prefs, 'osc.xml'), log, (xml) => {
      let out = setXmlAttr(xml, 'Input', 'Enabled', '1');
      return out == null ? null : setXmlAttr(out, 'Input', 'Port', String(port));
    });
    if (changed) log.info(`OSC input enabled on port ${port}`);
  }

  // REST API lives behind Preferences > Webserver (Preferences\server.xml). Needed for the http ready check.
  if (app.ensure_webserver !== false && app.rest?.port) {
    const changed = editXml(path.join(prefs, 'server.xml'), log, (xml) => {
      const out = setXmlAttr(xml, 'ServerController', 'enabled', '1');
      return out == null ? null : setXmlAttr(out, 'ServerController', 'port', String(app.rest.port));
    });
    if (changed) log.info(`Webserver / REST API enabled on port ${app.rest.port}`);
  }
}

export function launchSpec(app) {
  const args = [];
  if (app.composition) {
    if (!fs.existsSync(app.composition)) throw new Error(`Composition not found: ${app.composition}`);
    args.push('--suppressCompositionChangeConfirmation', app.composition);
  }
  return { exe: app.exe, args };
}

export function readyDefaults(app, cfg) {
  return { type: 'http', url: restUrl(app, cfg), hint: 'Arena REST API not answering. Enable Preferences > Webserver in Arena (port must match apps.resolume.rest.port), or set ready.type: process.' };
}

// ── Scene modes: "simple" strips a layer group down to one clip and stops its autopilots ──
// apps.resolume.simple_mode: { group: "Main Scene", keep: { layer: "Synesthesia", clip: 1 } }
// simple: snapshot each group layer (autopilot target + connected clip), set autopilot Off, connect the
//         keep clip on the keep layer, clear every other layer. full: restore from the snapshot.
export const ACTIONS = ['simple', 'full', 'mode', 'state', 'defaults', 'master', 'speed', 'column'];

function restBase(app, cfg) { return `http://${cfg.osc?.resolume?.host ?? '127.0.0.1'}:${app.rest?.port ?? 8080}/api/v1`; }
async function rest(app, cfg, method, route, body) {
  const res = await fetch(restBase(app, cfg) + route, { method, headers: body != null ? { 'content-type': 'application/json' } : undefined, body: body != null ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new Error(`Arena REST ${method} ${route} → HTTP ${res.status}`);
  const text = await res.text(); return text ? JSON.parse(text) : null;
}
function snapshotFile(app, cfg) { return path.join(cfg._root, 'data', `${app.id}_scene_mode.json`); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function groupLayers(app, cfg) {
  const sm = app.simple_mode ?? {};
  const comp = await rest(app, cfg, 'GET', '/composition');
  const group = (comp.layergroups ?? []).find((g) => g.name?.value === (sm.group ?? 'Main Scene'));
  if (!group) throw new Error(`layer group "${sm.group ?? 'Main Scene'}" not found in the composition`);
  const ids = new Set((group.layers ?? []).map((l) => l.id ?? l));
  const layers = comp.layers.map((l, i) => ({ index: i + 1, id: l.id, name: l.name?.value ?? '', autopilotId: l.autopilot?.target?.id, autopilot: l.autopilot?.target?.value ?? 'Off',
    connected: (l.clips ?? []).findIndex((c) => c.connected?.value === 'Connected') + 1 || null,
    clipNames: (l.clips ?? []).map((c) => c.name?.value ?? '') })).filter((l) => ids.has(l.id));
  const keepName = sm.keep?.layer ?? 'Synesthesia';
  const keep = layers.find((l) => l.name === keepName) ?? layers.find((l) => /synesthesia/i.test(l.name));
  return { layers, keep, keepClip: sm.keep?.clip ?? 1, groupName: group.name.value, comp };
}

async function setAutopilot(app, cfg, layer, value) {
  if (!layer.autopilotId) return;
  await rest(app, cfg, 'PUT', `/parameter/by-id/${layer.autopilotId}`, { value });
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const defaultsOf = (app) => ({ master: 1, speed: 1, column: 1, mode: 'full', ...(app.defaults ?? {}) });

// Composition summary for the panel's Resolume section: the two master faders, the
// tempo, which column is live, and the Main Scene layers with their autopilot state.
async function compSummary(app, cfg) {
  const file = snapshotFile(app, cfg);
  const comp = await rest(app, cfg, 'GET', '/composition');
  const par = (o) => (o ? { id: o.id, value: o.value, min: o.min ?? 0, max: o.max ?? 1 } : null);
  const sm = app.simple_mode ?? {};
  const group = (comp.layergroups ?? []).find((g) => g.name?.value === (sm.group ?? 'Main Scene'));
  const ids = new Set((group?.layers ?? []).map((l) => l.id ?? l));
  const layers = (comp.layers ?? []).map((l, i) => ({ index: i + 1, id: l.id, name: l.name?.value ?? '',
    autopilot: l.autopilot?.target?.value ?? 'Off', bypassed: l.bypassed?.value === true,
    master: l.master?.value ?? null,
    connected: (l.clips ?? []).findIndex((c) => c.connected?.value === 'Connected') + 1 || null,
    clip: null })).map((l) => { const src = comp.layers[l.index - 1]; l.clip = l.connected ? (src.clips[l.connected - 1]?.name?.value ?? '') : null; return l; });
  return {
    name: comp.name?.value ?? null,
    mode: fs.existsSync(file) ? 'simple' : 'full',
    since: fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')).at : null,
    master: par(comp.master), speed: par(comp.speed), tempo: par(comp.tempocontroller?.tempo),
    group: group?.name?.value ?? null,
    groupLayers: layers.filter((l) => ids.has(l.id)),
    columns: (comp.columns ?? []).map((c, i) => ({ index: i + 1, name: c.name?.value ?? '', connected: c.connected?.value === 'Connected' })),
    defaults: defaultsOf(app),
  };
}

async function setParam(app, cfg, param, value) {
  if (!param) throw new Error('parameter not found in the composition');
  const v = clamp(Number(value), param.min ?? 0, param.max ?? 1);
  await rest(app, cfg, 'PUT', `/parameter/by-id/${param.id}`, { value: v });
  return v;
}

export async function action(app, cfg, log, name, value) {
  if (!ACTIONS.includes(name)) throw new Error(`resolume: unknown action "${name}" (${ACTIONS.join(', ')})`);
  const file = snapshotFile(app, cfg);
  const snap = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : null;
  if (name === 'mode') {
    const g = await groupLayers(app, cfg);
    return { mode: snap ? 'simple' : 'full', group: g.groupName, since: snap?.at ?? null, layers: g.layers.map((l) => ({ index: l.index, name: l.name, autopilot: l.autopilot, connected: l.connected, clip: l.connected ? l.clipNames[l.connected - 1] : null })) };
  }
  if (name === 'state') return compSummary(app, cfg);
  if (name === 'master' || name === 'speed') {
    const comp = await rest(app, cfg, 'GET', '/composition');
    const v = await setParam(app, cfg, comp[name], value);
    log.debug(`resolume: composition ${name} → ${v}`);
    return { [name]: v };
  }
  if (name === 'column') {
    const n = Math.max(1, Math.round(Number(value) || 1));
    await rest(app, cfg, 'POST', `/composition/columns/${n}/connect`);
    log.info(`resolume: column ${n} connected`);
    return { column: n };
  }
  if (name === 'defaults') {
    const d = defaultsOf(app);
    if (d.mode !== 'simple') await action(app, cfg, log, 'full');   // restore layers/autopilots first
    const comp = await rest(app, cfg, 'GET', '/composition');
    if (d.master != null) await setParam(app, cfg, comp.master, d.master);
    if (d.speed != null) await setParam(app, cfg, comp.speed, d.speed);
    if (d.column != null) await rest(app, cfg, 'POST', `/composition/columns/${Math.round(d.column)}/connect`);
    log.info(`resolume: reset to defaults (master ${d.master}, speed ${d.speed}, column ${d.column})`);
    return { reset: d };
  }
  if (name === 'simple') {
    if (snap) { log.info(`resolume: already in simple mode (since ${snap.at})`); return { mode: 'simple', already: true }; }
    const g = await groupLayers(app, cfg);
    if (!g.keep) throw new Error(`keep layer "${app.simple_mode?.keep?.layer ?? 'Synesthesia'}" not found in group ${g.groupName}`);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify({ at: new Date().toISOString(), group: g.groupName, layers: g.layers.map(({ index, name, autopilot, connected }) => ({ index, name, autopilot, connected })) }, null, 2));
    for (const l of g.layers) if (l.autopilot !== 'Off') await setAutopilot(app, cfg, l, 'Off');   // autopilot first, or a cleared layer picks a new clip
    await sleep(150);
    for (const l of g.layers) {
      if (l.id === g.keep.id) { if (l.connected !== g.keepClip) await rest(app, cfg, 'POST', `/composition/layers/${l.index}/clips/${g.keepClip}/connect`); }
      else if (l.connected) await rest(app, cfg, 'POST', `/composition/layers/${l.index}/clear`);
    }
    log.info(`resolume: simple mode — ${g.groupName}: ${g.keep.name} clip ${g.keepClip} only, autopilot off on ${g.layers.length} layers`);
    return { mode: 'simple', group: g.groupName, kept: `${g.keep.name}/${g.keepClip}`, cleared: g.layers.filter((l) => l.id !== g.keep.id && l.connected).map((l) => l.name) };
  }
  // full: restore
  if (!snap) { log.debug('resolume: already in full mode'); return { mode: 'full', already: true }; }
  const g = await groupLayers(app, cfg);
  const restored = [];
  for (const s of snap.layers) {
    const l = g.layers.find((x) => x.index === s.index) ?? g.layers.find((x) => x.name === s.name);
    if (!l) continue;
    if (s.autopilot && s.autopilot !== l.autopilot) await setAutopilot(app, cfg, l, s.autopilot);
    // Arena reports a clip as Connected until its transition ends, so the live state can lag by seconds:
    // always reconnect what the snapshot had (connecting an already-connected clip is harmless).
    if (s.connected) { await rest(app, cfg, 'POST', `/composition/layers/${l.index}/clips/${s.connected}/connect`); restored.push(`${l.name}/${s.connected}`); }
  }
  fs.unlinkSync(file);
  log.info(`resolume: full mode restored (${restored.length} clips reconnected, autopilots back)`);
  return { mode: 'full', restored };
}

export function check(app, cfg) {
  const issues = [];
  const prefs = path.join(prefsDir(app), 'Preferences');
  if (!fs.existsSync(prefsDir(app))) issues.push({ level: 'error', msg: `Resolume prefs dir not found: ${prefsDir(app)}` });
  if (app.composition && !fs.existsSync(app.composition)) issues.push({ level: 'error', msg: `Composition not found: ${app.composition}` });
  if (app.advanced_output && !fs.existsSync(presetPath(app, app.advanced_output))) {
    const dir = path.join(prefsDir(app), 'Presets', 'Advanced Output');
    const have = fs.existsSync(dir) ? fs.readdirSync(dir).filter(f => f.endsWith('.xml')).map(f => f.slice(0, -4)) : [];
    issues.push({ level: 'error', msg: `Advanced Output preset "${app.advanced_output}" not found. Available: ${have.join(', ') || 'none'}` });
  }
  const oscXml = path.join(prefs, 'osc.xml');
  if (fs.existsSync(oscXml)) {
    const xml = fs.readFileSync(oscXml, 'utf8');
    const m = xml.match(/<Input\b[^>]*Enabled="(\d)"[^>]*Port="(\d+)"/);
    if (m && (m[1] !== '1' || m[2] !== String(cfg.osc?.resolume?.port ?? 7000))) {
      issues.push({ level: app.ensure_osc_input === false ? 'warn' : 'info',
        msg: `Arena OSC input is currently Enabled=${m[1]} Port=${m[2]}; ${app.ensure_osc_input === false ? 'ensure_osc_input is off, so this will not be fixed' : 'will be fixed at next launch'}` });
    }
  }
  const serverXml = path.join(prefs, 'server.xml');
  if (app.rest?.port && fs.existsSync(serverXml)) {
    const m = fs.readFileSync(serverXml, 'utf8').match(/<ServerController\b[^>]*enabled="(\d)"[^>]*port="(\d+)"/);
    if (m && (m[1] !== '1' || m[2] !== String(app.rest.port))) {
      issues.push({ level: app.ensure_webserver === false ? 'warn' : 'info',
        msg: `Arena webserver (REST) is currently enabled=${m[1]} port=${m[2]}; ${app.ensure_webserver === false ? 'ensure_webserver is off, so the http ready check will fail' : 'will be fixed at next launch'}` });
    }
  }
  return issues;
}
