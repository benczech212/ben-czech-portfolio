// TouchDesigner / TouchPlayer driver. The .toe path is the only argument.
// Readiness = OSC heartbeat from the project (add an OSC Out CHOP sending /td/alive ~1/s),
// falling back to process detection when no listen port is configured.
import fs from 'node:fs';

export async function prepare() {}

export function launchSpec(app) {
  const args = [];
  if (app.project) {
    if (!fs.existsSync(app.project)) throw new Error(`TouchDesigner project not found: ${app.project}`);
    args.push(app.project);
  }
  return { exe: app.exe, args: [...args, ...(app.args ?? [])] };
}

export function readyDefaults(app, cfg) {
  const listen = cfg.osc?.touchdesigner?.listen;
  if (!listen) return { type: 'process' };
  return { type: 'osc_heartbeat', port: listen, address: '/td/alive', hint: `No heartbeat from TouchDesigner on port ${listen}. Add an OSC Out CHOP sending /td/alive to 127.0.0.1:${listen}.` };
}

export function check(app) {
  const issues = [];
  if (app.project && !fs.existsSync(app.project)) issues.push({ level: 'error', msg: `TouchDesigner project not found: ${app.project}` });
  return issues;
}
