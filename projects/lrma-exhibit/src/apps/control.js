// Control-server driver: runs `node bin/exhibit.js serve` detached (pid file) so the
// control panel API stays up after boot. Actions: start, stop, status. Ready = HTTP.
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs';
import path from 'node:path';
import { httpOk } from '../process.js';

const execFileP = promisify(execFile);
export const NO_EXE = true;
export const ACTIONS = ['start', 'stop', 'status'];

function pidFile(app, cfg) { return app.pid_file ?? path.join(cfg._root, 'data', `${app.id}.pid`); }
function url(app) { return `http://127.0.0.1:${app.port ?? 9876}/api/status`; }

async function readPid(app, cfg) {
  try {
    const pid = parseInt(fs.readFileSync(pidFile(app, cfg), 'utf8').trim(), 10);
    if (!Number.isFinite(pid)) return null;
    const { stdout } = await execFileP('tasklist', ['/FI', `PID eq ${pid}`, '/NH', '/FO', 'CSV']);
    return stdout.toLowerCase().includes('node') ? pid : null;
  } catch { return null; }
}

export async function prepare() {}
export function launchSpec() { throw new Error('control: use `action: control start`'); }
export async function isRunning(app) { return httpOk(url(app), 4000); }   // /api/status can take a moment while it polls the apps
export function readyDefaults(app) { return { type: 'http', url: url(app), hint: 'control server not answering; run `exhibit serve` by hand to see the error' }; }
export function check() { return []; }

export async function kill(app, cfg) {
  const pid = await readPid(app, cfg);
  if (!pid) return (await isRunning(app)) ? 'still-running' : 'not-running';
  try { await execFileP('taskkill', ['/F', '/T', '/PID', String(pid)]); } catch {}
  try { fs.unlinkSync(pidFile(app, cfg)); } catch {}
  return 'closed';
}

async function start(app, cfg, log) {
  if (await isRunning(app)) { log.info(`${app.id}: already running`); return { running: true, already: true }; }
  fs.mkdirSync(path.dirname(pidFile(app, cfg)), { recursive: true });
  const exhibit = path.join(cfg._root, 'bin', 'exhibit.js');
  // Paths must be space-free here (cmd `start` quoting is a minefield); C:\dev\... satisfies that.
  // Oddity on this kiosk: `cmd start <abs node.exe> <x.js>` is refused with EPERM, while `node`
  // resolved through PATH works, so we pass `node` and put our node dir first on the child's PATH.
  const args = ['/c', 'start', '', '/min', 'node', exhibit, 'serve', '--port', String(app.port ?? 9876), '--pid-file', pidFile(app, cfg)];
  const env = { ...process.env, PATH: `${path.dirname(process.execPath)};${process.env.PATH ?? ''}` };
  const child = spawn('cmd.exe', args, { detached: true, stdio: 'ignore', windowsHide: true, cwd: cfg._root, env });
  child.unref();
  for (let i = 0; i < 60; i++) { await new Promise((r) => setTimeout(r, 250)); if (await isRunning(app)) break; }
  if (!(await isRunning(app))) throw new Error('control: server did not come up (try `node bin/exhibit.js serve`)');
  log.info(`${app.id}: control panel server on http://127.0.0.1:${app.port ?? 9876}/`);
  return { running: true };
}

export async function action(app, cfg, log, name) {
  if (!ACTIONS.includes(name)) throw new Error(`control: unknown action "${name}" (${ACTIONS.join(', ')})`);
  switch (name) {
    case 'start':  return start(app, cfg, log);
    case 'stop':   return { running: false, result: await kill(app, cfg) };
    case 'status': return { running: await isRunning(app), url: `http://127.0.0.1:${app.port ?? 9876}/` };
  }
}
