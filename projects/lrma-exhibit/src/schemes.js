// Per-scene input schemes: which scene control each physical gesture drives.
//   Slots: x (left/right), y (forward/back), z (push/pull), yaw (twist), pitch (tilt fwd/back), roll (tilt sideways),
//          left / right (the two SpaceMouse buttons; Xbox A / B).
// Generated automatically from the scene catalog with the rules below, written to input_schemes.yaml
// where any scene can be hand-tuned (set auto: false to keep it from being regenerated).
import fs from 'node:fs';
import path from 'node:path';
import YAML from 'yaml';
import { loadCatalog, playlistScenes, oscName } from './scenes.js';

export const AXES = ['x', 'y', 'z', 'yaw', 'pitch', 'roll'];
export const BUTTONS = ['left', 'right'];

const RULES = {
  // slot → [regex on control name/label/group, reason]
  z:     [[/speed|rate|velocity|tempo|pace|flow|bpm|time|fps(?!_)/, 'push/pull = speed'], [/zoom|scale|size|depth|dist|radius|magnif/, 'push/pull = zoom / size']],
  yaw:   [[/rot|spin|angle|twist|turn|yaw|swirl|kaleid|orbit/, 'twist = rotation'], [/hue|phase|cycle/, 'twist = hue / phase']],
  x:     [[/(^|_)x($|_)|horiz|pan|left|right|offset|shift|slide|width|position/, 'left/right']],
  y:     [[/(^|_)y($|_)|vert|height|up|down|elev|tilt|altitude/, 'forward/back']],
  pitch: [[/tilt|pitch|camera|cam|perspective|fov|lift/, 'tilt fwd/back = camera']],
  roll:  [[/amount|intensity|strength|mix|blend|density|detail|complex|bright|glow|contrast/, 'tilt sideways = amount']],
};
const SKIP_CTRL = /fps_?fix|fps_?warning|^reset|automation|media|logo|bpm_tap|preset|debug/i;
const SKIP_GROUP = /fps|reset|media|logo|automation|advanced|settings|utility|default/i;
const AUTO_TYPES = new Set(['slider', 'knob']);

// Control names outrank group hints: a "rotation" knob beats a slider that merely sits in a "kaleido_motion" group.
function score(ctrl, slot) {
  const name = `${ctrl.label} ${ctrl.name}`.toLowerCase(), group = String(ctrl.group).toLowerCase();
  for (const [re, reason] of RULES[slot] ?? []) if (re.test(name)) return { hit: 2, reason };
  for (const [re, reason] of RULES[slot] ?? []) if (re.test(group)) return { hit: 1, reason: reason + ' (group)' };
  return { hit: 0 };
}

export function generateScheme(scene) {
  const used = new Set();
  const out = { bank: scene.bank, title: scene.title, auto: true };
  const usable = scene.controls.filter((c) => !SKIP_CTRL.test(c.label) && !SKIP_GROUP.test(c.group));
  const analog = usable.filter((c) => AUTO_TYPES.has(c.type) && c.variant !== 'traveler');
  const take = (ctrl, slot, reason, extra = {}) => { used.add(ctrl.name); out[slot] = { control: ctrl.name, label: ctrl.label, type: ctrl.type, group: ctrl.group || undefined, reason, ...extra }; };

  // 1) an xy pad owns x + y
  const xy = usable.find((c) => c.type === 'xy' && c.variant !== 'traveler') ?? usable.find((c) => c.type === 'xy');
  if (xy) { take(xy, 'x', 'xy pad: left/right', { sub: 'x' }); out.y = { ...out.x, sub: 'y', reason: 'xy pad: forward/back' }; }
  // 2) named matches, in priority order
  for (const slot of ['z', 'yaw', 'x', 'y', 'pitch', 'roll']) {
    if (out[slot]) continue;
    const c = analog.filter((k) => !used.has(k.name)).map((k) => ({ k, s: score(k, slot) })).filter((o) => o.s.hit).sort((a, b) => b.s.hit - a.s.hit)[0];
    if (c) take(c.k, slot, c.s.reason);
  }
  // 3) remaining axes get remaining sliders/knobs in UI order (most prominent first)
  for (const slot of ['x', 'y', 'z', 'yaw', 'pitch', 'roll']) {
    if (out[slot]) continue;
    const c = analog.find((k) => !used.has(k.name));
    if (c) take(c, slot, 'next free slider');
  }
  // 4) buttons: prefer action-ish toggles that start off, then bangs (momentary), then any toggle
  const btnPool = [
    ...usable.filter((c) => c.type === 'toggle' && /action|mode|toggle|fx|effect|shape|look/i.test(c.group) && !Number(c.default)),
    ...usable.filter((c) => c.type === 'bang'),
    ...usable.filter((c) => c.type === 'toggle'),
  ].filter((c, i, a) => a.findIndex((x) => x.name === c.name) === i);
  for (const slot of BUTTONS) {
    const c = btnPool.find((k) => !used.has(k.name));
    if (!c) break;
    take(c, slot, c.type === 'bang' ? 'momentary trigger' : 'toggle', { action: c.type === 'bang' ? 'momentary' : 'toggle' });
  }
  out.unassigned = scene.controls.filter((c) => !used.has(c.name)).map((c) => `${c.label}:${c.type}`);
  return out;
}

export function schemesFile(rootDir, cfg) { return path.resolve(rootDir, cfg?.inputs?.schemes_file ?? 'input_schemes.yaml'); }

export function loadSchemes(rootDir, cfg) {
  const f = schemesFile(rootDir, cfg);
  if (!fs.existsSync(f)) return { version: 1, scenes: {} };
  try { const y = YAML.parse(fs.readFileSync(f, 'utf8')) ?? {}; y.scenes ??= {}; return y; } catch (e) { throw new Error(`cannot parse ${f}: ${e.message}`); }
}

// Regenerate: scenes with auto: false are kept verbatim; everything else is recomputed.
export function generateAll(rootDir, cfg, { playlist = null, force = false } = {}) {
  const cat = loadCatalog();
  const existing = loadSchemes(rootDir, cfg);
  const titles = playlist ? playlistScenes(playlist) : Object.values(cat.scenes).map((s) => s.title);
  const scenes = {};
  const missing = [];
  for (const t of titles) {
    const sc = cat.scenes[oscName(t)];
    if (!sc) { missing.push(t); continue; }
    const prev = existing.scenes[t];
    scenes[t] = prev && prev.auto === false && !force ? prev : generateScheme(sc);
  }
  const doc = { version: 1, generated: new Date().toISOString(), playlist: playlist ?? null, note: 'Slots: x y z yaw pitch roll left right. Set auto: false on a scene to hand-tune it; `exhibit schemes generate` keeps such scenes. control = OSC name, sub = x|y for xy pads, action = toggle|momentary for buttons.', scenes };
  fs.writeFileSync(schemesFile(rootDir, cfg), YAML.stringify(doc, { lineWidth: 0 }));
  return { file: schemesFile(rootDir, cfg), count: Object.keys(scenes).length, kept: Object.values(scenes).filter((s) => s.auto === false).length, missing };
}

// Runtime resolver: current bank → slot → { bank, control, sub, action, type }
export function resolveSlot(schemes, bank, slot) {
  const entry = Object.values(schemes.scenes ?? {}).find((s) => s.bank === bank || oscName(s.title ?? '') === bank);
  const e = entry?.[slot];
  if (!e?.control) return null;
  return { bank, control: e.control, sub: e.sub ?? null, action: e.action ?? null, type: e.type ?? null, label: e.label ?? e.control, title: entry.title };
}
export function schemeFor(schemes, bank) {
  return Object.values(schemes.scenes ?? {}).find((s) => s.bank === bank || oscName(s.title ?? '') === bank) ?? null;
}
