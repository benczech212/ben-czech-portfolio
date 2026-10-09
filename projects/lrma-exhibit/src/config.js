// Loads exhibit.yaml, deep-merges exhibit.local.yaml over it, and provides
// {{dotted.path}} interpolation for step arguments.
import fs from 'node:fs';
import path from 'node:path';
import YAML from 'yaml';

export const DEFAULT_CONFIG = 'exhibit.yaml';
export const LOCAL_CONFIG   = 'exhibit.local.yaml';

export function loadConfig(rootDir, file = DEFAULT_CONFIG) {
  const mainPath = path.isAbsolute(file) ? file : path.join(rootDir, file);
  if (!fs.existsSync(mainPath)) throw new Error(`Config not found: ${mainPath} (copy exhibit.example.yaml to exhibit.yaml)`);
  let cfg = YAML.parse(fs.readFileSync(mainPath, 'utf8')) ?? {};

  const localPath = path.join(path.dirname(mainPath), LOCAL_CONFIG);
  const loaded = [mainPath];
  if (fs.existsSync(localPath)) {
    cfg = deepMerge(cfg, YAML.parse(fs.readFileSync(localPath, 'utf8')) ?? {});
    loaded.push(localPath);
  }

  cfg.osc    ??= {};
  cfg.apps   ??= {};
  cfg.macros ??= {};
  for (const [id, app] of Object.entries(cfg.apps)) {
    app.id = id;
    app.type ??= id;
    app.enabled ??= true;
    app.ready ??= { type: 'process' };
    app.ready.timeout ??= cfg.defaults?.ready_timeout ?? 60;
  }
  cfg._files = loaded;
  cfg._root = rootDir;
  return cfg;
}

export function deepMerge(base, over) {
  if (Array.isArray(base) || Array.isArray(over) || typeof base !== 'object' || typeof over !== 'object' || !base || !over) {
    return over === undefined ? base : over;
  }
  const out = { ...base };
  for (const [k, v] of Object.entries(over)) out[k] = deepMerge(base[k], v);
  return out;
}

export function getPath(obj, dotted) {
  return dotted.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

// Replace {{a.b.c}} in strings (recursively through arrays/objects) with config values.
// A string that is exactly one template keeps the value's original type (numbers stay numbers).
export function interpolate(value, cfg) {
  if (typeof value === 'string') {
    const whole = value.match(/^\{\{\s*([\w.-]+)\s*\}\}$/);
    if (whole) {
      const v = getPath(cfg, whole[1]);
      if (v === undefined) throw new Error(`Unknown config reference {{${whole[1]}}}`);
      return v;
    }
    return value.replace(/\{\{\s*([\w.-]+)\s*\}\}/g, (_, p) => {
      const v = getPath(cfg, p);
      if (v === undefined) throw new Error(`Unknown config reference {{${p}}}`);
      return String(v);
    });
  }
  if (Array.isArray(value)) return value.map(v => interpolate(v, cfg));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, interpolate(v, cfg)]));
  }
  return value;
}

export function getApp(cfg, id) {
  const app = cfg.apps[id];
  if (!app) throw new Error(`Unknown app "${id}" (known: ${Object.keys(cfg.apps).join(', ') || 'none'})`);
  return app;
}

export function getOscTarget(cfg, id) {
  const t = cfg.osc[id];
  if (!t) throw new Error(`Unknown OSC target "${id}" (known: ${Object.keys(cfg.osc).join(', ') || 'none'})`);
  if (!t.host || !t.port) throw new Error(`OSC target "${id}" needs host and port`);
  return t;
}
