import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { deepMerge, interpolate, loadConfig } from '../src/config.js';
import { normalizeStep, checkMacros, isTimeBetween } from '../src/runner.js';

test('isTimeBetween handles same-day and overnight windows', () => {
  const at = (h, m = 0) => new Date(2026, 0, 1, h, m);
  assert.equal(isTimeBetween('09:00', '17:00', at(12)), true);
  assert.equal(isTimeBetween('09:00', '17:00', at(18)), false);
  assert.equal(isTimeBetween('19:00', '07:00', at(23)), true);
  assert.equal(isTimeBetween('19:00', '07:00', at(3)), true);
  assert.equal(isTimeBetween('19:00', '07:00', at(7)), false);
  assert.equal(isTimeBetween('19:00', '07:00', at(12)), false);
  assert.throws(() => isTimeBetween('7pm', '07:00'), /bad time/);
});

test('deepMerge overrides nested keys but replaces arrays', () => {
  const out = deepMerge({ a: { b: 1, c: 2 }, l: [1, 2] }, { a: { c: 3 }, l: [9] });
  assert.deepEqual(out, { a: { b: 1, c: 3 }, l: [9] });
});

test('interpolate keeps types for whole-string refs and stringifies inline', () => {
  const cfg = { apps: { syn: { playlist: 'Exhibit', n: 3 } } };
  assert.equal(interpolate('{{apps.syn.n}}', cfg), 3);
  assert.equal(interpolate('list {{apps.syn.playlist}} #{{apps.syn.n}}', cfg), 'list Exhibit #3');
  assert.deepEqual(interpolate({ args: ['{{apps.syn.playlist}}', 1] }, cfg), { args: ['Exhibit', 1] });
  assert.throws(() => interpolate('{{nope.x}}', cfg), /Unknown config reference/);
});

test('normalizeStep accepts shorthand and object forms', () => {
  assert.deepEqual(normalizeStep({ launch: 'resolume' }, 0).params, { app: 'resolume' });
  assert.deepEqual(normalizeStep({ wait: 'a' }, 0).params, { apps: ['a'] });
  assert.deepEqual(normalizeStep({ kill: ['a', 'b'] }, 0).params, { apps: ['a', 'b'] });
  assert.equal(normalizeStep({ sleep: '2' }, 0).params.seconds, 2);
  assert.equal(normalizeStep({ osc: { to: 'x', address: '/y' }, on_fail: 'continue' }, 0).on_fail, 'continue');
  assert.throws(() => normalizeStep({ launch: 'a', wait: 'b' }, 0), /exactly one/);
});

test('loadConfig merges local file and checkMacros flags bad references', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'exhibit-'));
  fs.writeFileSync(path.join(dir, 'exhibit.yaml'), `
osc: { syn: { host: 127.0.0.1, port: 12000 } }
apps: { syn: { exe: C:\\nope.exe } }
macros:
  ok:  [ { osc: { to: syn, address: /a, args: [1] } }, { launch: syn }, { wait: syn } ]
  bad: [ { osc: { to: nope, address: /a } }, { macro: missing }, { launch: ghost } ]
`);
  fs.writeFileSync(path.join(dir, 'exhibit.local.yaml'), `apps: { syn: { exe: D:\\other.exe } }`);
  const cfg = loadConfig(dir);
  assert.equal(cfg.apps.syn.exe, 'D:\\other.exe');
  assert.equal(cfg.apps.syn.type, 'syn');
  const issues = checkMacros(cfg);
  assert.equal(issues.filter((i) => i.msg.startsWith('macro "ok"')).length, 0);
  assert.equal(issues.filter((i) => i.msg.startsWith('macro "bad"')).length, 3);
});
