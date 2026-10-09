// Fallback driver for any other exe: launch with configured args, readiness by process/tcp/http.
export async function prepare() {}
export function launchSpec(app) { return { exe: app.exe, args: app.args ?? [] }; }
export function readyDefaults() { return { type: 'process' }; }
export function check() { return []; }
