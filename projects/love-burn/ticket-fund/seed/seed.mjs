#!/usr/bin/env node
/**
 * Seed / upsert the Ticket Fund project list from seed/projects.json.
 *
 *   node seed/seed.mjs --admin you@example.com   # -> local emulator (demo-awetisticats); creates
 *                                            #    config/main with that admin if it doesn't exist
 *   node seed/seed.mjs --wipe                # emulator: delete ALL projects/items/ballots first
 *   node seed/seed.mjs --project <id> --token "$(gcloud auth print-access-token)"
 *                                            # -> production Firestore via REST (needs a Google
 *                                            #    account with Firestore access on that project)
 *
 * Safe to re-run: projects/items are keyed by stable slugs. On existing docs only the
 * planning fields are updated (name, emoji, description, parentId, required, order /
 * name, cost, qty, dependsOn, order). status, helpers and donations are never touched
 * once a doc exists, so admin decisions and member input survive a re-seed.
 * Items removed from projects.json are NOT deleted (delete them in the app).
 *
 * config/main (budget, ticket prices, admin emails) can only be created here, server-side:
 * firestore.rules refuse to create it from the app, so nobody can make themselves admin by
 * being the first to sign in. Pass --admin (repeatable, or comma-separated) on the first run;
 * an existing config is never overwritten (edit admins later in the app's Admin Config).
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const args = process.argv.slice(2);
const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const project = opt('--project') || 'demo-awetisticats';
const token = opt('--token') || 'owner';
const emulator = !opt('--project');
const wipe = args.includes('--wipe');
const admins = args.flatMap((a, i) => args[i - 1] === '--admin' ? a.split(',') : [])
  .map(s => s.trim().toLowerCase()).filter(Boolean);
const DEFAULT_CONFIG = { goal: 5950, prices: { builder: 150, performer: 150, member: 300, guest: 600 }, payNote: 'ask a lead' };
const BASE = emulator
  ? `http://localhost:8080/v1/projects/${project}/databases/(default)/documents`
  : `https://firestore.googleapis.com/v1/projects/${project}/databases/(default)/documents`;
const H = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };

const data = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'projects.json'), 'utf8'));

const V = (x) => x === null ? { nullValue: null }
  : typeof x === 'boolean' ? { booleanValue: x }
  : typeof x === 'number' ? (Number.isInteger(x) ? { integerValue: String(x) } : { doubleValue: x })
  : typeof x === 'string' ? { stringValue: x }
  : Array.isArray(x) ? { arrayValue: { values: x.map(V) } }
  : { mapValue: { fields: Object.fromEntries(Object.entries(x).map(([k, v]) => [k, V(v)])) } };
const fields = (o) => ({ fields: Object.fromEntries(Object.entries(o).map(([k, v]) => [k, V(v)])) });

async function call(method, path, body, query = '') {
  const r = await fetch(`${BASE}/${path}${query}`, { method, headers: H, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok && r.status !== 404) throw new Error(`${method} ${path} -> ${r.status} ${await r.text()}`);
  return r.status === 404 ? null : r.json();
}
const exists = async (path) => !!(await call('GET', path));
async function upsert(path, planning, defaults) {
  if (await exists(path)) {
    const mask = Object.keys(planning).map(k => `updateMask.fieldPaths=${k}`).join('&');
    await call('PATCH', path, fields(planning), '?' + mask);
    return 'updated';
  }
  await call('PATCH', path, fields({ ...planning, ...defaults }));
  return 'created';
}
async function listIds(path) {
  const ids = []; let pageToken = '';
  do {
    const r = await call('GET', path, null, `?pageSize=300${pageToken ? '&pageToken=' + pageToken : ''}`);
    (r?.documents || []).forEach(d => ids.push(d.name.split('/').pop()));
    pageToken = r?.nextPageToken || '';
  } while (pageToken);
  return ids;
}

const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);

if (wipe) {
  if (!emulator) { console.error('--wipe is only allowed against the emulator'); process.exit(1); }
  for (const pid of await listIds('projects')) {
    for (const iid of await listIds(`projects/${pid}/items`)) await call('DELETE', `projects/${pid}/items/${iid}`);
    await call('DELETE', `projects/${pid}`);
  }
  for (const bid of await listIds('ballots')) await call('DELETE', `ballots/${bid}`);
  console.log('wiped projects, items, ballots');
}

if (await exists('config/main')) {
  if (admins.length) console.log('config/main exists: --admin ignored (edit admins in the app)');
} else if (admins.length) {
  await call('PATCH', 'config/main', fields({ ...DEFAULT_CONFIG, admins }));
  console.log(`created config/main (admins: ${admins.join(', ')})`);
} else {
  console.warn('config/main does not exist yet: re-run with --admin you@example.com, or nobody can administer the fund');
}

let order = 1;
for (const p of data.projects) {
  const { items = [], id, ...rest } = p;
  const planning = { name: rest.name, emoji: rest.emoji || '🎪', description: rest.description || '', parentId: rest.parentId || null, required: !!rest.required, order: order++ };
  const res = await upsert(`projects/${id}`, planning, { status: 'undecided', helpers: {} });
  let io = 1, created = 0, updated = 0;
  for (const it of items) {
    const ip = { name: it.name, cost: +it.cost || 0, qty: it.qty > 0 ? it.qty : 1, dependsOn: it.dependsOn || null, order: io++ };
    const r = await upsert(`projects/${id}/items/${slug(it.name)}`, ip, { donations: {} });
    r === 'created' ? created++ : updated++;
  }
  console.log(`${res.padEnd(7)} ${id.padEnd(22)} items: +${created} ~${updated}`);
}
console.log(`done -> ${emulator ? 'emulator' : project}`);
