// Keepalive driver: a detached PowerShell process (src/keepalive.ps1) that tells Windows the
// system (and optionally the display) must stay awake, so Modern Standby / display-off and the
// lock screen that follows never interrupt the exhibit. Actions: start, stop, status.
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const execFileP = promisify(execFile);
const SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'keepalive.ps1');

export const NO_EXE = true;
export const ACTIONS = ['start', 'stop', 'status'];

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
export function launchSpec() { throw new Error('keepalive: use `action: keepalive start`, not launch'); }
export async function isRunning(app, cfg) { return (await readPid(app, cfg)) != null; }
export function readyDefaults() { return { type: 'process' }; }
export function check() { return fs.existsSync(SCRIPT) ? [] : [{ level: 'error', msg: `missing ${SCRIPT}` }]; }

export async function kill(app, cfg) {
  const pid = await readPid(app, cfg);
  if (!pid) return 'not-running';
  try { await execFileP('taskkill', ['/F', '/PID', String(pid)]); } catch {}
  try { fs.unlinkSync(pidFile(app, cfg)); } catch {}
  return 'closed';
}

async function start(app, cfg, log) {
  if (await isRunning(app, cfg)) { log.info(`${app.id}: already running`); return { running: true, already: true }; }
  fs.mkdirSync(path.dirname(pidFile(app, cfg)), { recursive: true });
  const args = ['/c', 'start', '', '/min', 'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
    '-File', SCRIPT, '-PidFile', pidFile(app, cfg), '-IntervalSeconds', String(app.interval ?? 30)];
  if (app.display !== false) args.push('-DisplayToo');
  const child = spawn('cmd.exe', args, { detached: true, stdio: 'ignore', windowsHide: true, cwd: cfg._root });
  child.unref();
  for (let i = 0; i < 40; i++) { await new Promise((r) => setTimeout(r, 250)); if (await isRunning(app, cfg)) break; }
  if (!(await isRunning(app, cfg))) throw new Error('keepalive: process did not start');
  log.info(`${app.id}: started (system${app.display !== false ? ' + display' : ''} kept awake)`);
  return { running: true };
}

export async function action(app, cfg, log, name) {
  if (!ACTIONS.includes(name)) throw new Error(`keepalive: unknown action "${name}" (${ACTIONS.join(', ')})`);
  switch (name) {
    case 'start':  return start(app, cfg, log);
    case 'stop':   return { running: false, result: await kill(app, cfg) };
    case 'status': return { running: await isRunning(app, cfg) };
  }
}
