// Console + daily file logger. Every run appends to logs/exhibit-YYYY-MM-DD.log
// so a failed morning boot can be diagnosed later.
import fs from 'node:fs';
import path from 'node:path';

let logDir = null;
let verbose = false;

export function initLog(rootDir, opts = {}) {
  logDir = path.join(rootDir, 'logs');
  verbose = !!opts.verbose;
  try { fs.mkdirSync(logDir, { recursive: true }); } catch { logDir = null; }
}

function stamp() {
  const d = new Date();
  const p = (n, w = 2) => String(n).padStart(w, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
}

function write(level, scope, msg) {
  const line = `${stamp()} ${level.padEnd(5)} [${scope}] ${msg}`;
  if (level === 'ERROR' || level === 'WARN') console.error(line);
  else if (level !== 'DEBUG' || verbose) console.log(line);
  if (logDir) {
    const d = new Date();
    const file = path.join(logDir, `exhibit-${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}.log`);
    try { fs.appendFileSync(file, line + '\n'); } catch { /* logging must never break a macro */ }
  }
}

export function logger(scope) {
  return {
    info:  (m) => write('INFO',  scope, m),
    warn:  (m) => write('WARN',  scope, m),
    error: (m) => write('ERROR', scope, m),
    debug: (m) => write('DEBUG', scope, m),
  };
}
