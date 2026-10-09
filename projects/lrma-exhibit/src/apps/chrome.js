// Chrome driver: launches a dedicated Chrome instance (own profile dir, so the login
// persists and it never collides with a Chrome someone opened by hand) with the
// remote-debugging port on. Running/ready/kill all go through that port, not the
// process name, because chrome.exe may belong to somebody else's window.
import fs from 'node:fs';
import { httpOk } from '../process.js';
import { closeBrowser, browserInfo } from '../cdp.js';

export function debugPort(app) { return app.debug_port ?? 9222; }

export async function prepare(app, cfg, log) {
  if (app.user_data_dir && !fs.existsSync(app.user_data_dir)) {
    fs.mkdirSync(app.user_data_dir, { recursive: true });
    log.info(`created Chrome profile dir ${app.user_data_dir} (sign in to the account there once)`);
  }
}

export function launchSpec(app) {
  const args = [
    `--remote-debugging-port=${debugPort(app)}`,
    '--remote-allow-origins=*',
    '--autoplay-policy=no-user-gesture-required',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-session-crashed-bubble',
    '--hide-crash-restore-bubble',
    '--disable-features=TranslateUI',
    '--new-window',
  ];
  if (app.user_data_dir) args.push(`--user-data-dir=${app.user_data_dir}`);
  if (app.window?.width && app.window?.height) args.push(`--window-size=${app.window.width},${app.window.height}`);
  if (app.window?.x != null && app.window?.y != null) args.push(`--window-position=${app.window.x},${app.window.y}`);
  if (app.kiosk) args.push('--kiosk');
  args.push(...(app.args ?? []));
  // app_mode: no tabs/address bar (--app=url); with kiosk it is a clean fullscreen panel.
  if (app.url) args.push(app.app_mode ? `--app=${app.url}` : app.url);
  return { exe: app.exe, args };
}

export function isRunning(app) {
  return httpOk(`http://127.0.0.1:${debugPort(app)}/json/version`, 1500);
}

export async function kill(app, cfg, log) {
  if (!(await isRunning(app))) return 'not-running';
  try { await closeBrowser(debugPort(app)); } catch (err) { log.debug(`Browser.close failed: ${err.message}`); }
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (!(await isRunning(app))) return 'closed';
    await new Promise((r) => setTimeout(r, 300));
  }
  return 'still-running';
}

export function readyDefaults(app) {
  return { type: 'http', url: `http://127.0.0.1:${debugPort(app)}/json/version`, hint: 'Chrome did not open its debugging port; another Chrome using the same user_data_dir may already be open.' };
}

export function check(app) {
  const issues = [];
  if (!app.user_data_dir) issues.push({ level: 'warn', msg: 'user_data_dir not set: Chrome will share the default profile and may not open a separate instance' });
  if (!app.url) issues.push({ level: 'warn', msg: 'url not set' });
  return issues;
}

export async function version(app) {
  return (await browserInfo(debugPort(app))).Browser;
}
