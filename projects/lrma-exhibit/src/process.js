// Windows process helpers: spawn detached, is-running by image name, graceful → forced kill.
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs';
import path from 'node:path';

const execFileP = promisify(execFile);

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function isRunning(imageName) {
  try {
    const { stdout } = await execFileP('tasklist', ['/FI', `IMAGENAME eq ${imageName}`, '/NH', '/FO', 'CSV']);
    return stdout.toLowerCase().includes(`"${imageName.toLowerCase()}"`);
  } catch {
    return false;
  }
}

export async function pids(imageName) {
  try {
    const { stdout } = await execFileP('tasklist', ['/FI', `IMAGENAME eq ${imageName}`, '/NH', '/FO', 'CSV']);
    return stdout.split('\n')
      .filter((l) => l.toLowerCase().startsWith(`"${imageName.toLowerCase()}"`))
      .map((l) => parseInt(l.split('","')[1], 10))
      .filter(Number.isFinite);
  } catch { return []; }
}

// PIDs of processes with this image name whose command line contains `needle` — for apps that
// share an executable (several python.exe services). Uses CIM via PowerShell (~300 ms).
export async function findByCmdline(imageName, needle) {
  try {
    const ps = `Get-CimInstance Win32_Process -Filter "Name='${imageName.replace(/'/g, "''")}'" | Where-Object { $_.CommandLine -like '*${String(needle).replace(/'/g, "''")}*' } | ForEach-Object { $_.ProcessId }`;
    const { stdout } = await execFileP('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', ps], { timeout: 8000, windowsHide: true });
    return stdout.split(/\r?\n/).map((s) => parseInt(s.trim(), 10)).filter(Number.isFinite);
  } catch { return []; }
}

export async function killPids(pids, { graceMs = 5000, force = true } = {}) {
  if (!pids.length) return 'not-running';
  for (const pid of pids) { try { await execFileP('taskkill', ['/PID', String(pid)]); } catch {} }
  const alive = async () => { const { stdout } = await execFileP('tasklist', ['/FO', 'CSV', '/NH']).catch(() => ({ stdout: '' })); return pids.filter((p) => stdout.includes(`","${p}",`)); };
  const deadline = Date.now() + graceMs;
  while (Date.now() < deadline) { if (!(await alive()).length) return 'closed'; await sleep(400); }
  if (!force) return 'still-running';
  for (const pid of await alive()) { try { await execFileP('taskkill', ['/F', '/T', '/PID', String(pid)]); } catch {} }
  await sleep(400);
  return (await alive()).length ? 'still-running' : 'killed';
}

// Spawn fully detached so the app outlives this script.
export function launchDetached(exe, args = [], { cwd, hideConsole = false } = {}) {
  if (!fs.existsSync(exe)) throw new Error(`Executable not found: ${exe}`);
  const child = spawn(exe, args, {
    cwd: cwd ?? path.dirname(exe),
    detached: true,
    stdio: 'ignore',
    windowsHide: hideConsole,   // true = no console window for console apps (Prometheus, Loki, ...)
  });
  child.on('error', () => {});
  child.unref();
  return child.pid;
}

// Ask nicely (WM_CLOSE via taskkill), wait up to graceMs, then force.
export async function kill(imageName, { graceMs = 8000, force = true } = {}) {
  if (!(await isRunning(imageName))) return 'not-running';
  try { await execFileP('taskkill', ['/IM', imageName]); } catch { /* may already be closing */ }
  const deadline = Date.now() + graceMs;
  while (Date.now() < deadline) {
    if (!(await isRunning(imageName))) return 'closed';
    await sleep(500);
  }
  if (!force) return 'still-running';
  try { await execFileP('taskkill', ['/F', '/T', '/IM', imageName]); } catch {}
  await sleep(500);
  return (await isRunning(imageName)) ? 'still-running' : 'killed';
}

export async function waitUntil(fn, { timeoutMs, intervalMs = 1000, label = 'condition' }) {
  const deadline = Date.now() + timeoutMs;
  let lastErr = null;
  while (Date.now() < deadline) {
    try { if (await fn()) return true; } catch (e) { lastErr = e; }
    await sleep(intervalMs);
  }
  throw new Error(`Timed out after ${timeoutMs / 1000}s waiting for ${label}${lastErr ? ` (last error: ${lastErr.message})` : ''}`);
}

export async function httpOk(url, timeoutMs = 2000) {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
    return res.ok;
  } catch { return false; }
}

export function tcpOpen(host, port, timeoutMs = 2000) {
  return new Promise((resolve) => {
    import('node:net').then(({ default: net }) => {
      const sock = new net.Socket();
      const done = (r) => { sock.destroy(); resolve(r); };
      sock.setTimeout(timeoutMs);
      sock.once('connect', () => done(true));
      sock.once('error', () => done(false));
      sock.once('timeout', () => done(false));
      sock.connect(port, host);
    });
  });
}
