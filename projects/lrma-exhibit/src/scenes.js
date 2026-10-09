// Synesthesia scene catalog: reads every scene.json (bundled + user) and normalises control
// definitions to the names Synesthesia uses on OSC:
//   bank   = TITLE lowercased with non-alphanumerics removed   ("Cloud Ten" → cloudten)
//   control = NAME lowercased with non-alphanumerics removed   ("sun_position" → sunposition)
import fs from 'node:fs';
import path from 'node:path';

const DEFAULT_ROOTS = [
  'C:/Program Files (x86)/Synesthesia/release',
  'C:/Program Files (x86)/Synesthesia/default',
  path.join(process.env.LOCALAPPDATA ?? '', 'Synesthesia', '.ui-cache'),
  path.join(process.env.USERPROFILE ?? '', 'Documents', 'Synesthesia'),
];

export const oscName = (s) => String(s ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');

function baseType(t) {
  const s = String(t ?? '').toLowerCase();
  const base = ['toggle', 'slider', 'knob', 'xy', 'bang', 'color', 'dropdown'].find((b) => s.startsWith(b)) ?? (s === 'undefined' || s === '' ? 'slider' : s.split(' ')[0]);
  return { type: base, variant: s.includes('traveler') ? 'traveler' : s.includes('speed') ? 'speed' : s.includes('smooth') ? 'smooth' : s.includes('counter') ? 'counter' : null };
}

let cache = null;
export function loadCatalog(roots = DEFAULT_ROOTS, { force = false } = {}) {
  if (cache && !force) return cache;
  const scenes = {};
  for (const r of roots) {
    if (!r || !fs.existsSync(r)) continue;
    for (const d of fs.readdirSync(r)) {
      const f = path.join(r, d, 'scene.json');
      if (!fs.existsSync(f)) continue;
      try {
        const j = JSON.parse(fs.readFileSync(f, 'utf8'));
        const title = j.TITLE || d.replace(/\.synScene$/i, '');
        const bank = oscName(title);
        if (scenes[bank]) continue;   // first root wins (release before ui-cache copies)
        scenes[bank] = {
          title, bank, dir: path.join(r, d), credit: j.CREDIT ?? '', description: j.DESCRIPTION ?? '', tags: j.TAGS ?? [],
          controls: (j.CONTROLS ?? []).map((c, i) => ({ index: i, name: oscName(c.NAME), label: c.NAME, ...baseType(c.TYPE), rawType: c.TYPE ?? 'undefined', group: c.UI_GROUP ?? '', description: c.DESCRIPTION ?? '', default: c.DEFAULT, min: c.MIN, max: c.MAX })),
        };
      } catch { /* skip broken scene */ }
    }
  }
  cache = { scenes, roots: roots.filter((r) => r && fs.existsSync(r)), loadedAt: Date.now() };
  return cache;
}

export function sceneByBank(bank) { return loadCatalog().scenes[oscName(bank)] ?? null; }
export function sceneByTitle(title) { return sceneByBank(oscName(title)); }

// Scene titles of a Synesthesia playlist, from the app's saved state.
export function playlistScenes(name, stateFile = path.join(process.env.LOCALAPPDATA ?? '', 'Synesthesia', 'Data', 'savedState.json')) {
  try {
    const st = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    const cfg = st['visual.scenePlaylists.configurations']?.[name];
    return cfg?.allIds ?? [];
  } catch { return []; }
}
