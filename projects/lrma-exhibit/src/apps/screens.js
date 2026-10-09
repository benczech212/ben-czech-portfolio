// "Screens" driver: black fullscreen windows over every display (src/blackout.ps1), with
// the do-not-touch message and a hidden bottom-right button on the message screen.
// Actions: show, hide, status. Not a launchable exe — use `action: screens show`.
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const execFileP = promisify(execFile);
const SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'blackout.ps1');

export const NO_EXE = true;
export const ACTIONS = ['show', 'hide', 'status'];

// One pid/config file per app entry, so several overlays (pause screens, running screen) can coexist.
function pidFile(app, cfg) { return app.pid_file ?? path.join(cfg._root, 'data', `${app.id}.pid`); }

async function readPid(app, cfg) {
  try {
    const pid = parseInt(fs.readFileSync(pidFile(app, cfg), 'utf8').trim(), 10);
    if (!Number.isFinite(pid)) return null;
    const { stdout } = await execFileP('tasklist', ['/FI', `PID eq ${pid}`, '/NH', '/FO', 'CSV']);
    return stdout.toLowerCase().includes('powershell') ? pid : null;
  } catch { return null; }
}

export async function prepare() {}
export function launchSpec() { throw new Error('screens: use `action: screens show`, not launch'); }
export async function isRunning(app, cfg) { return (await readPid(app, cfg)) != null; }
export function readyDefaults() { return { type: 'process' }; }
export function check(app) {
  const issues = [];
  if (!fs.existsSync(SCRIPT)) issues.push({ level: 'error', msg: `missing ${SCRIPT}` });
  if (!app.message) issues.push({ level: 'warn', msg: 'message not set; default text will be shown' });
  return issues;
}

export async function kill(app, cfg, log) {
  const pid = await readPid(app, cfg);
  if (!pid) return 'not-running';
  try { await execFileP('taskkill', ['/PID', String(pid)]); } catch {}
  for (let i = 0; i < 10; i++) {
    await new Promise((r) => setTimeout(r, 300));
    if (!(await readPid(app, cfg))) break;
  }
  if (await readPid(app, cfg)) { try { await execFileP('taskkill', ['/F', '/PID', String(pid)]); } catch {} await new Promise((r) => setTimeout(r, 300)); }
  try { fs.unlinkSync(pidFile(app, cfg)); } catch {}
  log?.debug?.(`screens: closed pid ${pid}`);
  return 'closed';
}

async function show(app, cfg, log) {
  if (await isRunning(app, cfg)) { log.info(`${app.id}: already shown`); return { shown: true, already: true }; }
  fs.mkdirSync(path.dirname(pidFile(app, cfg)), { recursive: true });
  // Hidden button runs the wake macro so music resumes as well (the macro's own hide step is a no-op by then).
  const configFile = path.join(path.dirname(pidFile(app, cfg)), `${app.id}.json`);
  const conf = {
    Message: app.message ?? '', SubMessage: app.sub_message ?? '', MessageScreen: String(app.message_screen ?? 'primary'),
    ButtonSize: app.button_size ?? 80, PidFile: pidFile(app, cfg), Title: app.title ?? `Exhibit ${app.id}`,
    OnlyMessageScreen: app.only_message_screen === true,
  };
  // hide_macro: macro the hidden button runs after closing (omit for "just close").
  if (app.hide_macro) { conf.OnHideExe = process.execPath; conf.OnHideArgs = [path.join(cfg._root, 'bin', 'exhibit.js'), 'run', app.hide_macro]; }
  fs.writeFileSync(configFile, JSON.stringify(conf, null, 2));
  // A PowerShell spawned detached/hidden straight from Node never gets going (no console to hide);
  // `cmd start /min` gives it a console of its own that it then hides, and it outlives this CLI run.
  const args = ['/c', 'start', '', '/min', 'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-WindowStyle', 'Hidden', '-File', SCRIPT, '-ConfigFile', configFile];
  const child = spawn('cmd.exe', args, { detached: true, stdio: 'ignore', windowsHide: true, cwd: cfg._root });
  child.unref();
  // Wait for the pid file so callers can rely on it.
  for (let i = 0; i < 40; i++) { await new Promise((r) => setTimeout(r, 250)); if (await isRunning(app, cfg)) break; }
  const ok = await isRunning(app, cfg);
  if (!ok) throw new Error('screens: overlay process did not start (check logs\\ and run src\\blackout.ps1 by hand)');
  log.info(`${app.id}: shown on ${app.only_message_screen ? 'the message screen' : 'all displays'}`);
  return { shown: true };
}

export async function action(app, cfg, log, name) {
  if (!ACTIONS.includes(name)) throw new Error(`screens: unknown action "${name}" (${ACTIONS.join(', ')})`);
  switch (name) {
    case 'show':   return show(app, cfg, log);
    case 'hide':   return { hidden: true, result: await kill(app, cfg, log) };
    case 'status': return { shown: await isRunning(app, cfg) };
  }
}
