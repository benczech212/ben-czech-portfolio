// Synesthesia driver. No command-line options exist; everything after launch is OSC.
// Its OSC ports live in %LOCALAPPDATA%\Synesthesia\Data\preferences.json, so before
// launch (never while running: it rewrites prefs on exit) we align them with the
// osc.synesthesia block: input port = port we send to, output → 127.0.0.1:listen.
// Readiness = first OSC packet on the listen port (it streams /audio/* continuously
// once a scene is rendering).
import fs from 'node:fs';
import path from 'node:path';

export function prefsFile(app) {
  return app.prefs_file ?? path.join(process.env.LOCALAPPDATA ?? '', 'Synesthesia', 'Data', 'preferences.json');
}

function readPrefs(app) {
  const file = prefsFile(app);
  if (!fs.existsSync(file)) return null;
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}

// Desired OSC-related prefs derived from config. Returns {} when nothing is configured.
function desiredPrefs(app, cfg) {
  const t = cfg.osc?.synesthesia ?? {};
  const want = {};
  if (t.port)   { want.OSC_INPUT = true;  want.OSC_INPUT_PORT = t.port; }
  if (t.listen) { want.OSC_OUTPUT = true; want.OSC_OUTPUT_PORT = t.listen; want.OSC_OUTPUT_ADDRESS = '127.0.0.1'; want.OSC_OUTPUT_AUDIO = true; }
  // Any extra preferences.json keys pinned in YAML (apps.synesthesia.prefs), e.g. AUTO_TRANSITIONS: true
  Object.assign(want, app.prefs ?? {});
  return want;
}

function diffPrefs(prefs, want) {
  return Object.entries(want).filter(([k, v]) => prefs[k] !== v);
}

export async function prepare(app, cfg, log) {
  if (app.ensure_osc === false) return;
  const want = desiredPrefs(app, cfg);
  if (!Object.keys(want).length) return;
  const file = prefsFile(app);
  const prefs = readPrefs(app);
  if (!prefs) { log.warn(`Synesthesia preferences not found/readable at ${file}; OSC ports not verified`); return; }
  const diff = diffPrefs(prefs, want);
  if (!diff.length) { log.info('Synesthesia OSC prefs already match config'); return; }
  const bak = file + '.bak';
  if (!fs.existsSync(bak)) fs.copyFileSync(file, bak);
  fs.writeFileSync(file, JSON.stringify({ ...prefs, ...want }));
  log.info(`Synesthesia OSC prefs updated: ${diff.map(([k]) => `${k}=${JSON.stringify(want[k])} (was ${JSON.stringify(prefs[k])})`).join(', ')}`);
}

export function launchSpec(app) {
  return { exe: app.exe, args: app.args ?? [] };
}

export function readyDefaults(app, cfg) {
  const listen = cfg.osc?.synesthesia?.listen;
  if (!listen) return { type: 'process' };
  return { type: 'osc_heartbeat', port: listen, hint: `No OSC from Synesthesia on port ${listen}. It may have been started before its prefs were aligned (restart it via the macro), or no scene is playing (Pro license required for OSC).` };
}

export function check(app, cfg) {
  const issues = [];
  if (!cfg.osc?.synesthesia?.port)   issues.push({ level: 'error', msg: 'osc.synesthesia.port (Synesthesia OSC input) not set' });
  if (!cfg.osc?.synesthesia?.listen) issues.push({ level: 'warn',  msg: 'osc.synesthesia.listen not set; readiness falls back to process detection' });
  const prefs = readPrefs(app);
  if (!prefs) {
    issues.push({ level: 'warn', msg: `preferences.json not found at ${prefsFile(app)}; OSC ports cannot be verified or fixed` });
  } else {
    const diff = diffPrefs(prefs, desiredPrefs(app, cfg));
    if (diff.length) {
      const cur = `input ${prefs.OSC_INPUT ? prefs.OSC_INPUT_PORT : 'off'}, output ${prefs.OSC_OUTPUT ? `${prefs.OSC_OUTPUT_ADDRESS}:${prefs.OSC_OUTPUT_PORT}` : 'off'}`;
      issues.push({ level: app.ensure_osc === false ? 'warn' : 'info',
        msg: `Synesthesia OSC prefs currently: ${cur}; ${app.ensure_osc === false ? 'ensure_osc is off, so this will not be fixed' : 'will be aligned with config at next launch'}` });
    }
  }
  return issues;
}
