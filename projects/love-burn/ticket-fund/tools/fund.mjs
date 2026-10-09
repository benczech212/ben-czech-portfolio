#!/usr/bin/env node
/**
 * fund.mjs — agent/dev CLI for the Ticket Fund Firestore data (emulator or production).
 *
 *   node tools/fund.mjs dump                       # full JSON snapshot of every collection
 *   node tools/fund.mjs summary                    # human/agent-readable state: members, projects, needs, votes
 *   node tools/fund.mjs results                    # run the same STV tally the app uses
 *   node tools/fund.mjs list <collection>          # e.g. members, projects, ballots, requests, codes,
 *                                                  #      projects/<id>/items, projects/<id>/comments
 *   node tools/fund.mjs get <doc/path>             # e.g. projects/dance-floor, ballots/<memberId>
 *   node tools/fund.mjs set <doc/path> '<json>'    # merge fields into a doc (creates if missing)
 *   node tools/fund.mjs add <collection> '<json>'  # add a doc with an auto id
 *   node tools/fund.mjs del <doc/path>
 *   node tools/fund.mjs wipe <collection>          # emulator only
 *   node tools/fund.mjs link <email> <memberId>    # emulator only: create/verify that auth user and link
 *                                                  #   it to members/<memberId> (+ memberUids/<uid>)
 *
 * Auth:
 *   default            emulator at localhost:8080 as "owner" (bypasses security rules)
 *   --as <email>       act as that user THROUGH the rules (emulator auth: the user is created if
 *                      needed and marked email-verified, which the rules require). To act as a
 *                      linked member, `link` the email to a member record first.
 *   --project <id> --token "$(gcloud auth print-access-token)"   production
 *
 * JSON in/out uses plain values ({name:"x", qty:2, hearts:{uid:true}}); Firestore's typed
 * wire format is handled here. Timestamps are stored as epoch ms numbers by the app.
 */
const args = process.argv.slice(2);
const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? args.splice(i, 2)[1] : null; };
const project = opt('--project') || 'demo-awetisticats';
const asEmail = opt('--as');
let token = opt('--token') || 'owner';
const emulator = project === 'demo-awetisticats';
const FS = emulator ? `http://localhost:8080/v1/projects/${project}/databases/(default)/documents`
                    : `https://firestore.googleapis.com/v1/projects/${project}/databases/(default)/documents`;
const AUTH = 'http://localhost:9099/identitytoolkit.googleapis.com/v1';
const [cmd, a1, a2] = args;

/* ---------- Firestore value (de)serialisation ---------- */
const enc = (x) => x === null || x === undefined ? { nullValue: null }
  : typeof x === 'boolean' ? { booleanValue: x }
  : typeof x === 'number' ? (Number.isInteger(x) ? { integerValue: String(x) } : { doubleValue: x })
  : typeof x === 'string' ? { stringValue: x }
  : Array.isArray(x) ? { arrayValue: { values: x.map(enc) } }
  : { mapValue: { fields: Object.fromEntries(Object.entries(x).map(([k, v]) => [k, enc(v)])) } };
const dec = (v) => {
  if (!v) return null;
  if ('nullValue' in v) return null;
  if ('integerValue' in v) return Number(v.integerValue);
  if ('doubleValue' in v) return v.doubleValue;
  if ('arrayValue' in v) return (v.arrayValue.values || []).map(dec);
  if ('mapValue' in v) return Object.fromEntries(Object.entries(v.mapValue.fields || {}).map(([k, x]) => [k, dec(x)]));
  return Object.values(v)[0];
};
const decDoc = (d) => ({ id: d.name.split('/').pop(), ...Object.fromEntries(Object.entries(d.fields || {}).map(([k, v]) => [k, dec(v)])) });

/* ---------- HTTP ---------- */
async function call(method, path, body, query = '') {
  const r = await fetch(`${FS}/${path}${query}`, { method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const text = await r.text();
  if (!r.ok) { const e = new Error(`${method} ${path} -> ${r.status} ${text.slice(0, 300)}`); e.status = r.status; throw e; }
  return text ? JSON.parse(text) : {};
}
async function list(path) {
  const out = []; let pageToken = '';
  do {
    const r = await call('GET', path, null, `?pageSize=300${pageToken ? '&pageToken=' + pageToken : ''}`);
    (r.documents || []).forEach(d => out.push(decDoc(d)));
    pageToken = r.nextPageToken || '';
  } while (pageToken);
  return out;
}
const get = async (path) => decDoc(await call('GET', path));
const set = async (path, obj) => { const mask = Object.keys(obj).map(k => 'updateMask.fieldPaths=' + k).join('&'); return decDoc(await call('PATCH', path, { fields: Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, enc(v)])) }, '?' + mask)); };
const add = async (path, obj) => decDoc(await call('POST', path, { fields: Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, enc(v)])) }));
const del = (path) => call('DELETE', path);

/* ---------- act as a member through the rules (emulator auth) ---------- */
// Emulator-only test users. The rules require email_verified, so after sign-up the user is
// marked verified through the emulator's admin API ("Bearer owner") and signed in again to
// get a token that carries the claim.
async function emulatorUser(email) {
  if (!emulator) throw new Error('emulator-only');
  const json = { 'Content-Type': 'application/json' };
  const body = JSON.stringify({ email, password: 'passw0rd!', returnSecureToken: true });
  const signIn = () => fetch(`${AUTH}/accounts:signInWithPassword?key=fake`, { method: 'POST', body, headers: json });
  let r = await signIn();
  if (!r.ok) r = await fetch(`${AUTH}/accounts:signUp?key=fake`, { method: 'POST', body, headers: json });
  let j = await r.json(); if (!j.idToken) throw new Error('auth emulator: ' + JSON.stringify(j));
  const u = await fetch(`${AUTH}/projects/${project}/accounts:update`, { method: 'POST', headers: { ...json, Authorization: 'Bearer owner' },
    body: JSON.stringify({ localId: j.localId, emailVerified: true }) });
  if (!u.ok) throw new Error('auth emulator (verify): ' + (await u.text()).slice(0, 300));
  j = await (await signIn()).json(); if (!j.idToken) throw new Error('auth emulator: ' + JSON.stringify(j));
  return { uid: j.localId, idToken: j.idToken };
}
async function signInAs(email) {
  if (!emulator) throw new Error('--as only works against the emulator');
  const u = await emulatorUser(email);
  token = u.idToken; return u.uid;
}

/* ---------- STV tally (same algorithm as public/index.html runSTV) ---------- */
function runSTV(candidates, rankings) {
  const ids = candidates.map(c => c.id), order = Object.fromEntries(candidates.map((c, i) => [c.id, i]));
  const clean = rankings.map(r => { const seen = new Set(); return (r || []).filter(x => ids.includes(x) && !seen.has(x) && seen.add(x)); }).filter(r => r.length);
  const borda = {}; ids.forEach(id => borda[id] = 0); clean.forEach(r => r.forEach((id, i) => borda[id] += ids.length - i));
  let remaining = [...ids]; const rounds = [], eliminated = [];
  while (remaining.length > 1) {
    const tally = {}; remaining.forEach(id => tally[id] = 0); let exhausted = 0;
    clean.forEach(r => { const top = r.find(id => remaining.includes(id)); if (top) tally[top]++; else exhausted++; });
    const min = Math.min(...remaining.map(id => tally[id]));
    const out = remaining.filter(id => tally[id] === min).sort((a, b) => (borda[a] - borda[b]) || (order[b] - order[a]))[0];
    rounds.push({ round: rounds.length + 1, tally, exhausted, eliminated: out });
    eliminated.push(out); remaining = remaining.filter(id => id !== out);
  }
  const first = {}; ids.forEach(id => first[id] = 0); clean.forEach(r => first[r[0]]++);
  return { ranking: [...remaining, ...eliminated.reverse()], first, rounds, ballots: clean.length };
}

/* ---------- snapshot + derived state ---------- */
async function snapshot() {
  const [config, members, projects, ballots, requests, codes] = await Promise.all([
    get('config/main').catch(() => null), list('members').catch(() => []), list('projects'), list('ballots').catch(() => []),
    list('requests').catch(() => []), list('codes').catch(() => [])]);
  for (const p of projects) { p.items = await list(`projects/${p.id}/items`); p.comments = await list(`projects/${p.id}/comments`).catch(() => []); }
  projects.sort((a, b) => (a.order || 0) - (b.order || 0));
  return { config, members, projects, ballots, requests, codes };
}
function derive(s) {
  const P = Object.fromEntries(s.projects.map(p => [p.id, p]));
  const shelved = p => (p.status || 'undecided') === 'shelved';
  const qty = i => (+i.qty > 0 ? +i.qty : 1);
  const crossed = i => !!(i.dependsOn && P[i.dependsOn] && shelved(P[i.dependsOn]));
  const donatedQty = i => Math.min(qty(i), Object.values(i.donations || {}).reduce((a, d) => a + (+d.qty || 0), 0));
  const kids = id => s.projects.filter(p => p.parentId === id);
  const cost = id => P[id].items.filter(i => !crossed(i)).reduce((a, i) => a + qty(i) * (+i.cost || 0), 0) + kids(id).filter(c => !shelved(c)).reduce((a, c) => a + cost(c.id), 0);
  const donated = id => P[id].items.filter(i => !crossed(i)).reduce((a, i) => a + donatedQty(i) * (+i.cost || 0), 0) + kids(id).filter(c => !shelved(c)).reduce((a, c) => a + donated(c.id), 0);
  const top = s.projects.filter(p => !p.parentId || !P[p.parentId]);
  const voteable = p => !p.required && !shelved(p);
  const results = { top: runSTV(top.filter(voteable), s.ballots.map(b => b.ranking || [])) };
  s.projects.filter(p => kids(p.id).length && !shelved(p)).forEach(p => results[p.id] = runSTV(kids(p.id).filter(voteable), s.ballots.map(b => ((b.sub || {})[p.id]) || [])));
  const name = id => P[id] ? `${P[id].emoji || ''} ${P[id].name}`.trim() : id;
  const memberName = id => (s.members.find(m => m.id === id) || {}).name || id;
  const projectRows = s.projects.map(p => ({
    id: p.id, name: p.name, parentId: p.parentId || null, required: !!p.required, status: p.status || 'undecided',
    cost: cost(p.id), donated: donated(p.id), stillNeeded: Math.max(0, cost(p.id) - donated(p.id)),
    rank: (() => { const g = p.parentId && P[p.parentId] ? p.parentId : 'top'; const r = results[g]; const i = r ? r.ranking.indexOf(p.id) : -1; return i < 0 ? null : i + 1; })(),
    helpers: Object.values(p.helpers || {}).map(h => h.name), comments: p.comments.length,
    items: p.items.map(i => ({ id: i.id, name: i.name, cost: i.cost, qty: qty(i), total: qty(i) * (+i.cost || 0), donatedQty: donatedQty(i), crossed: crossed(i), dependsOn: i.dependsOn || null, donors: Object.values(i.donations || {}).map(d => `${d.name}×${d.qty}`) })),
  }));
  const budget = +(s.config || {}).goal || 0, reqNeed = projectRows.filter(r => !r.parentId && r.required && r.status !== 'shelved').reduce((a, r) => a + r.stillNeeded, 0);
  const actNeed = projectRows.filter(r => !r.parentId && !r.required && r.status === 'active').reduce((a, r) => a + r.stillNeeded, 0);
  return {
    config: s.config, budget: { campBudget: budget, requiredStillNeeded: reqNeed, leftForVoted: budget - reqNeed, activeStillNeeded: actNeed, unallocated: budget - reqNeed - actNeed },
    members: s.members.map(m => ({ id: m.id, name: m.name, role: m.role, claimed: !!m.uid, uid: m.uid || null, email: m.email || null,
      voted: !!s.ballots.find(b => b.id === m.id && ((b.ranking || []).length || Object.values(b.sub || {}).some(r => r && r.length))),
      secretBallot: !!(s.ballots.find(b => b.id === m.id) || {}).secret,
      tickets: s.requests.filter(r => r.memberId === m.id).map(r => `${r.tier}:${r.status}${r.code ? ':' + r.code : ''}`) })),
    projects: projectRows,
    results: Object.fromEntries(Object.entries(results).map(([g, r]) => [g === 'top' ? 'top' : name(g), { ballots: r.ballots, ranking: r.ranking.map((id, i) => `${i + 1}. ${name(id)} (${r.first[id] || 0} first)`), rounds: r.rounds.map(x => ({ ...x, tally: Object.fromEntries(Object.entries(x.tally).map(([k, v]) => [name(k), v])), eliminated: name(x.eliminated) })) }])),
    ballots: s.ballots.map(b => ({ member: memberName(b.id), secret: !!b.secret, ranking: (b.ranking || []).map(name), sub: Object.fromEntries(Object.entries(b.sub || {}).map(([g, r]) => [name(g), (r || []).map(name)])) })),
    requests: s.requests, codes: s.codes.map(c => ({ ...c, assignedTo: c.assignedTo || null })),
  };
}

/* ---------- main ---------- */
const out = (x) => console.log(JSON.stringify(x, null, 2));
try {
  if (asEmail) { const uid = await signInAs(asEmail); console.error(`acting as ${asEmail} (uid ${uid}) — rules apply`); }
  if (!cmd || cmd === 'help') { console.log((await import('node:fs')).readFileSync(new URL(import.meta.url), 'utf8').split('*/')[0]); }
  else if (cmd === 'dump') out(await snapshot());
  else if (cmd === 'summary') out(derive(await snapshot()));
  else if (cmd === 'results') out(derive(await snapshot()).results);
  else if (cmd === 'list') out(await list(a1));
  else if (cmd === 'get') out(await get(a1));
  else if (cmd === 'set') out(await set(a1, JSON.parse(a2)));
  else if (cmd === 'add') out(await add(a1, JSON.parse(a2)));
  else if (cmd === 'del') { await del(a1); console.log('deleted', a1); }
  else if (cmd === 'link') {
    if (!emulator || asEmail) throw new Error('link is emulator-only and runs as the owner (no --as)');
    const email = (a1 || '').toLowerCase(), memberId = a2; if (!email || !memberId) throw new Error('usage: link <email> <memberId>');
    const { uid } = await emulatorUser(email);
    await set(`members/${memberId}`, { uid, email }); await set(`memberUids/${uid}`, { memberId });
    console.log(`linked ${email} (uid ${uid}) -> members/${memberId}`);
  }
  else if (cmd === 'wipe') { if (!emulator) throw new Error('wipe is emulator-only'); const docs = await list(a1); for (const d of docs) await del(`${a1}/${d.id}`); console.log(`wiped ${docs.length} from ${a1}`); }
  else { console.error('unknown command', cmd); process.exit(2); }
} catch (e) { console.error(e.message); process.exit(1); }
