#!/usr/bin/env node
// exhibit — YAML-driven launch + OSC macros for the exhibit kiosk.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadConfig, getApp, getOscTarget } from '../src/config.js';
import { initLog, logger } from '../src/log.js';
import { runMacro, checkMacros } from '../src/runner.js';
import { launchApp, waitReady, probeReady, checkApp, processName, readySpec, appIsRunning, killApp, runAction } from '../src/apps/index.js';
import * as osc from '../src/osc.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const USAGE = `exhibit — launch + OSC macros driven by exhibit.yaml

Usage:
  exhibit run <macro>                 run a macro (e.g. boot, shutdown, restart)
  exhibit launch <app> [--force]      launch one app (force: even if already running)
  exhibit wait <app...>               wait until app(s) are ready
  exhibit kill <app...>               close app(s), force after grace period
  exhibit stop                        alias for: run shutdown
  exhibit osc <target> <addr> [args]  send one OSC message (numbers auto-typed; s:text forces string)
  exhibit music <action> [value]      start | play | pause | next | prev | volume 0-1 | status | playlist <id|url> | pair
  exhibit action <app> <name> [value] run any app action, e.g. action voicemeeter status | apply | set "Bus[0].mute = 0"
  exhibit schedule install|remove|list  Windows scheduled tasks from the schedule: block (daily pause / wake)
  exhibit serve [--port N]            run the control-panel web server in the foreground
  exhibit input <source> [detail]     report a visitor input to the control server (→ engaged mode)
  exhibit schemes generate|list|show <scene>   per-scene controller schemes (input_schemes.yaml); --force regenerates hand-tuned ones
  exhibit status                      running / ready state of every app
  exhibit check                       validate config, paths, presets, macros
  exhibit list                        list apps, OSC targets, macros

Options:
  --config <file>   config file (default exhibit.yaml; exhibit.local.yaml is merged on top)
  --dry-run         print what would happen without launching, killing, or sending
  --verbose         include debug lines
  -h, --help        this text
`;

function parseArgv(argv) {
  const opts = { config: undefined, dryRun: false, verbose: false, force: false };
  const pos = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--config')        opts.config = argv[++i];
    else if (a === '--dry-run')  opts.dryRun = true;
    else if (a === '--verbose')  opts.verbose = true;
    else if (a === '--force')    opts.force = true;
    else if (a === '--app')      opts.app = argv[++i];
    else if (a === '--port')     opts.port = parseInt(argv[++i], 10);
    else if (a === '--pid-file') opts.pidFile = argv[++i];
    else if (a === '-h' || a === '--help') opts.help = true;
    else if (a.startsWith('--')) throw new Error(`Unknown option ${a}`);
    else pos.push(a);
  }
  return { opts, pos };
}

function parseOscArg(s) {
  if (s.startsWith('s:')) return s.slice(2);
  if (s.startsWith('i:')) return { i: Number(s.slice(2)) };
  if (s.startsWith('f:')) return { f: Number(s.slice(2)) };
  if (s === 'true') return true;
  if (s === 'false') return false;
  if (/^-?\d+$/.test(s)) return parseInt(s, 10);
  if (/^-?\d*\.\d+$/.test(s)) return parseFloat(s);
  return s;
}

const LEVEL_ICON = { error: '✖', warn: '⚠', info: 'ℹ' };

// Best-effort: tell the control server (if it is up) about a CLI-side macro run so it lands in the analytics.
async function postEvent(cfg, event) {
  const ctl = cfg.apps?.control;
  if (!ctl || ctl.enabled === false) return;
  try { await fetch(`http://127.0.0.1:${ctl.port ?? 9876}/api/event`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(event), signal: AbortSignal.timeout(1500) }); } catch { /* server not running */ }
}

async function main() {
  const { opts, pos } = parseArgv(process.argv.slice(2));
  const [cmd, ...rest] = pos;
  if (opts.help || !cmd) { console.log(USAGE); return cmd ? 0 : 1; }

  initLog(ROOT, { verbose: opts.verbose });
  const log = logger('exhibit');
  const cfg = loadConfig(ROOT, opts.config);
  const ctx = { log, dryRun: opts.dryRun, force: opts.force };
  log.debug(`config: ${cfg._files.join(' + ')}`);

  switch (cmd) {
    case 'run': {
      if (!rest[0]) throw new Error('run: macro name required');
      const t0 = Date.now();
      let ok = true, error = null;
      try { await runMacro(cfg, rest[0], ctx); } catch (e) { ok = false; error = e.message; throw e; }
      finally { if (!opts.dryRun) await postEvent(cfg, { type: 'macro', macro: rest[0], ok, ms: Date.now() - t0, error: error ?? undefined, source: 'cli' }); }
      return 0;
    }
    case 'stop':
      await runMacro(cfg, 'shutdown', ctx);
      return 0;
    case 'launch': {
      if (!rest[0]) throw new Error('launch: app name required');
      await launchApp(getApp(cfg, rest[0]), cfg, log, { force: opts.force, dryRun: opts.dryRun });
      return 0;
    }
    case 'wait': {
      if (!rest.length) throw new Error('wait: app name(s) required');
      await Promise.all(rest.map((id) => waitReady(getApp(cfg, id), cfg, log, { dryRun: opts.dryRun })));
      return 0;
    }
    case 'kill': {
      if (!rest.length) throw new Error('kill: app name(s) required');
      let bad = 0;
      for (const id of rest) {
        const app = getApp(cfg, id);
        if (opts.dryRun) { log.info(`${id}: [dry-run] would kill`); continue; }
        const r = await killApp(app, cfg, log);
        log.info(`${id}: ${r}`);
        if (r === 'still-running') bad++;
      }
      return bad ? 1 : 0;
    }
    case 'osc': {
      const [target, address, ...args] = rest;
      if (!target || !address) throw new Error('osc: usage exhibit osc <target> </address> [args...]');
      const t = getOscTarget(cfg, target);
      const typed = args.map(parseOscArg);
      log.info(`osc → ${target} (${t.host}:${t.port}) ${address} ${JSON.stringify(typed)}${opts.dryRun ? ' [dry-run]' : ''}`);
      if (!opts.dryRun) await osc.send(t.host, t.port, address, typed);
      return 0;
    }
    case 'music': {
      const [action, value] = rest;
      if (!action) throw new Error('music: action required');
      const app = getApp(cfg, opts.app ?? 'music');
      const result = await runAction(app, cfg, log, action, value);
      console.log(JSON.stringify(result, null, 2));
      return 0;
    }
    case 'action': {
      const [id, name, ...valueParts] = rest;
      if (!id || !name) throw new Error('action: usage exhibit action <app> <name> [value]');
      const result = await runAction(getApp(cfg, id), cfg, log, name, valueParts.length ? valueParts.join(' ') : undefined);
      console.log(JSON.stringify(result, null, 2));
      return 0;
    }
    case 'schemes': {
      const { generateAll, loadSchemes, schemesFile, AXES, BUTTONS } = await import('../src/schemes.js');
      const { loadCatalog, playlistScenes, oscName } = await import('../src/scenes.js');
      const sub = rest[0] ?? 'list';
      if (sub === 'generate') {
        const playlist = rest[1] ?? cfg.apps.synesthesia?.playlist ?? null;
        const r = generateAll(ROOT, cfg, { playlist, force: opts.force });
        log.info(`wrote ${r.count} scene schemes to ${r.file}${r.kept ? ` (${r.kept} hand-tuned kept)` : ''}${r.missing.length ? `; no scene file for: ${r.missing.join(', ')}` : ''}`);
        return 0;
      }
      const s = loadSchemes(ROOT, cfg);
      if (sub === 'show') {
        const name = rest.slice(1).join(' '); const e = Object.values(s.scenes).find((x) => oscName(x.title) === oscName(name));
        if (!e) throw new Error(`no scheme for "${name}" (run: exhibit schemes generate)`);
        console.log(`${e.title}  (bank ${e.bank}${e.auto === false ? ', hand-tuned' : ''})`);
        for (const k of [...AXES, ...BUTTONS]) console.log(`  ${k.padEnd(6)} ${e[k] ? `${e[k].label}${e[k].sub ? '.' + e[k].sub : ''}  [${e[k].action ?? e[k].type}]  ${e[k].reason ?? ''}` : '–'}`);
        if (e.unassigned?.length) console.log(`  unassigned: ${e.unassigned.join(', ')}`);
        return 0;
      }
      const cat = loadCatalog(); const pl = cfg.apps.synesthesia?.playlist; const titles = pl ? playlistScenes(pl) : [];
      console.log(`schemes file: ${schemesFile(ROOT, cfg)} — ${Object.keys(s.scenes).length} scenes (${Object.values(s.scenes).filter((x) => x.auto === false).length} hand-tuned); catalog ${Object.keys(cat.scenes).length} scenes; playlist ${pl ?? '-'} ${titles.length} scenes, ${titles.filter((t) => !s.scenes[t]).length} without a scheme`);
      for (const [t, e] of Object.entries(s.scenes)) console.log(`  ${t.padEnd(28)} ${[...AXES, ...BUTTONS].map((k) => (e[k] ? `${k}=${e[k].label}${e[k].sub ? '.' + e[k].sub : ''}` : `${k}=–`)).join('  ')}`);
      return 0;
    }
    case 'input': {
      const [source, ...detail] = rest;
      if (!source) throw new Error('input: source required (e.g. midi, osc, gpio)');
      const ctl = cfg.apps?.control; const port = ctl?.port ?? 9876;
      const r = await fetch(`http://127.0.0.1:${port}/api/input`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ source, detail: detail.length ? detail.join(' ') : undefined }), signal: AbortSignal.timeout(3000) });
      const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText);
      log.info(`input from ${source} → ${j.engagement.mode}`); return 0;
    }
    case 'serve': {
      const { startServer } = await import('../src/server.js');
      const port = opts.port ?? cfg.apps.control?.port ?? 9876;
      if (opts.pidFile) {
        const fs = await import('node:fs');
        fs.mkdirSync(path.dirname(opts.pidFile), { recursive: true });
        fs.writeFileSync(opts.pidFile, String(process.pid));
        const cleanup = () => { try { fs.unlinkSync(opts.pidFile); } catch {} };
        process.on('exit', cleanup); process.on('SIGINT', () => { cleanup(); process.exit(0); }); process.on('SIGTERM', () => { cleanup(); process.exit(0); });
      }
      await startServer({ port, configFile: opts.config });
      log.info(`serving control panel on http://127.0.0.1:${port}/ (Ctrl+C to stop)`);
      await new Promise(() => {});   // run forever
      return 0;
    }
    case 'schedule': {
      const sub = rest[0] ?? 'list';
      const entries = Object.entries(cfg.schedule ?? {});   // { pause: "19:00", wake: "07:00" }
      if (!entries.length && sub !== 'list') throw new Error('schedule: no `schedule:` block in config');
      const taskName = (macro) => `Exhibit ${macro[0].toUpperCase()}${macro.slice(1)}`;
      const { execFile } = await import('node:child_process');
      const { promisify } = await import('node:util');
      const run = async (args) => { try { const { stdout } = await promisify(execFile)('schtasks', args, { windowsHide: true }); return stdout.trim(); } catch (e) { throw new Error(`schtasks ${args[0]} failed: ${(e.stderr || e.stdout || e.message).toString().trim()}`); } };
      if (sub === 'install') {
        for (const [macro, time] of entries) {
          if (!cfg.macros[macro]) throw new Error(`schedule: macro "${macro}" not found`);
          if (!/^\d{1,2}:\d{2}$/.test(String(time))) throw new Error(`schedule: bad time "${time}" for ${macro} (HH:MM)`);
          const tr = `"${process.execPath}" "${path.join(ROOT, 'bin', 'exhibit.js')}" run ${macro}`;
          if (opts.dryRun) { log.info(`[dry-run] schtasks /Create /TN "${taskName(macro)}" /SC DAILY /ST ${time} /TR ${tr}`); continue; }
          await run(['/Create', '/F', '/SC', 'DAILY', '/ST', String(time).padStart(5, '0'), '/TN', taskName(macro), '/TR', tr]);
          log.info(`scheduled "${taskName(macro)}" daily at ${time}`);
        }
        return 0;
      }
      if (sub === 'remove') {
        for (const [macro] of entries) {
          if (opts.dryRun) { log.info(`[dry-run] would delete task "${taskName(macro)}"`); continue; }
          try { await run(['/Delete', '/F', '/TN', taskName(macro)]); log.info(`removed "${taskName(macro)}"`); } catch (e) { log.warn(e.message); }
        }
        return 0;
      }
      if (sub === 'list') {
        for (const [macro, time] of entries) {
          let state = 'not installed';
          try { const out = await run(['/Query', '/FO', 'LIST', '/TN', taskName(macro)]); const m = /Next Run Time:\s*(.+)/i.exec(out); state = m ? `installed, next run ${m[1].trim()}` : 'installed'; } catch {}
          console.log(`${taskName(macro).padEnd(16)} ${String(time).padEnd(6)} → run ${macro}   [${state}]`);
        }
        return 0;
      }
      throw new Error('schedule: usage exhibit schedule install|remove|list');
    }
    case 'status': {
      const rows = await Promise.all(Object.values(cfg.apps).map(async (app) => {
        if (app.enabled === false) return [app.id, 'disabled', '-', ''];
        const proc = (app.exe || app.process) ? processName(app) : `(${app.type})`;
        const running = await appIsRunning(app, cfg);
        const spec = readySpec(app, cfg);
        const ready = running ? await probeReady(app, cfg, 2500) : false;
        return [app.id, running ? 'running' : 'stopped', ready ? 'ready' : (running ? 'not ready' : '-'), `${proc} · ${spec.type}`];
      }));
      const table = [['APP', 'PROCESS', 'READY', 'DETAIL'], ...rows];
      const w = table[0].map((_, i) => Math.max(...table.map((r) => String(r[i]).length)));
      for (const r of table) console.log(r.map((c, i) => String(c).padEnd(w[i])).join('  ').trimEnd());
      return 0;
    }
    case 'check': {
      let errors = 0;
      console.log(`Config: ${cfg._files.join(' + ')}`);
      console.log('\nOSC targets:');
      for (const [id, t] of Object.entries(cfg.osc)) {
        const ok = t.host && Number.isInteger(t.port);
        if (!ok) errors++;
        console.log(`  ${ok ? '✔' : '✖'} ${id}: send → ${t.host}:${t.port}${t.listen ? `, listen ← udp ${t.listen}` : ''}`);
      }
      console.log('\nApps:');
      for (const app of Object.values(cfg.apps)) {
        const issues = checkApp(app, cfg);
        const errs = issues.filter((i) => i.level === 'error').length;
        errors += errs;
        console.log(`  ${errs ? '✖' : '✔'} ${app.id} (${app.type}) — ${app.exe ?? 'no exe'}`);
        for (const i of issues) console.log(`      ${LEVEL_ICON[i.level] ?? '·'} ${i.msg}`);
      }
      console.log('\nMacros:');
      const macroIssues = checkMacros(cfg);
      errors += macroIssues.filter((i) => i.level === 'error').length;
      for (const [name, steps] of Object.entries(cfg.macros)) {
        const mine = macroIssues.filter((i) => i.msg.startsWith(`macro "${name}"`));
        console.log(`  ${mine.length ? '✖' : '✔'} ${name} (${Array.isArray(steps) ? steps.length : '?'} steps)`);
        for (const i of mine) console.log(`      ${LEVEL_ICON[i.level] ?? '·'} ${i.msg}`);
      }
      console.log(errors ? `\n${errors} problem(s) found.` : '\nAll good.');
      return errors ? 1 : 0;
    }
    case 'list': {
      console.log('Apps:    ' + Object.values(cfg.apps).map((a) => `${a.id}${a.enabled === false ? ' (disabled)' : ''}`).join(', '));
      console.log('OSC:     ' + Object.keys(cfg.osc).join(', '));
      console.log('Macros:  ' + Object.keys(cfg.macros).join(', '));
      return 0;
    }
    default:
      throw new Error(`Unknown command "${cmd}"\n\n${USAGE}`);
  }
}

main()
  .then((code) => { osc.closeSender(); process.exitCode = code ?? 0; })
  .catch((err) => { console.error(`ERROR: ${err.message}`); osc.closeSender(); process.exitCode = 1; });
