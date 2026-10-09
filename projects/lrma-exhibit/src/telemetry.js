// Telemetry: Prometheus metrics registry (/metrics text format), an event stream written as
// NDJSON (Loki-friendly), pushed straight to Loki when configured, and mirrored into a local
// SQLite database (node:sqlite) for ad-hoc queries. No dependencies.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

// ── metrics ──────────────────────────────────────────────────────────────────
const esc = (s) => String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
const labelKey = (labels) => Object.keys(labels).sort().map((k) => `${k}=${labels[k]}`).join('');
const fmtLabels = (labels) => { const ks = Object.keys(labels); return ks.length ? '{' + ks.map((k) => `${k}="${esc(labels[k])}"`).join(',') + '}' : ''; };

export class Metrics {
  constructor(prefix = 'exhibit_') { this.prefix = prefix; this.m = new Map(); }
  _get(name, type, help) {
    const full = this.prefix + name;
    let e = this.m.get(full);
    if (!e) { e = { type, help: help ?? '', series: new Map() }; this.m.set(full, e); }
    return e;
  }
  set(name, labels, value, help) {
    if (value == null || Number.isNaN(Number(value))) return;
    const e = this._get(name, 'gauge', help);
    e.series.set(labelKey(labels), { labels, value: Number(value), t: Date.now() });
  }
  // One live series per name (info-style gauges such as "current scene"); older label sets are dropped.
  setExclusive(name, labels, value = 1, help) {
    const e = this._get(name, 'gauge', help);
    e.series.clear();
    e.series.set(labelKey(labels), { labels, value, t: Date.now() });
  }
  clear(name) { const e = this.m.get(this.prefix + name); if (e) e.series.clear(); }
  inc(name, labels = {}, by = 1, help) {
    const e = this._get(name, 'counter', help);
    const k = labelKey(labels);
    const cur = e.series.get(k) ?? { labels, value: 0 };
    cur.value += by; cur.t = Date.now();
    e.series.set(k, cur);
  }
  render() {
    const out = [];
    for (const [name, e] of this.m) {
      if (!e.series.size) continue;
      out.push(`# HELP ${name} ${esc(e.help)}`, `# TYPE ${name} ${e.type}`);
      for (const s of e.series.values()) out.push(`${name}${fmtLabels(s.labels)} ${Number.isFinite(s.value) ? s.value : 0}`);
    }
    return out.join('\n') + '\n';
  }
}

// ── events + storage ─────────────────────────────────────────────────────────
export function createTelemetry({ rootDir, config = {}, log }) {
  const metrics = new Metrics();
  const eventsDir = config.events_dir ?? path.join(rootDir, 'data', 'events');
  fs.mkdirSync(eventsDir, { recursive: true });
  const host = os.hostname();

  // SQLite (optional; Node ships node:sqlite)
  let db = null;
  if (config.sqlite !== false) {
    try {
      const { DatabaseSync } = await_import_sqlite();
      db = new DatabaseSync(config.sqlite ?? path.join(rootDir, 'data', 'exhibit.db'));
      db.exec(`CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, type TEXT NOT NULL, json TEXT NOT NULL);
               CREATE INDEX IF NOT EXISTS events_ts ON events(ts); CREATE INDEX IF NOT EXISTS events_type ON events(type);
               CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, vote TEXT NOT NULL, note TEXT, tags TEXT,
                 scene TEXT, track TEXT, artist TEXT, composition TEXT, snapshot TEXT NOT NULL);
               CREATE INDEX IF NOT EXISTS feedback_ts ON feedback(ts);`);
      log?.info?.(`telemetry: sqlite at ${config.sqlite ?? path.join(rootDir, 'data', 'exhibit.db')}`);
    } catch (e) { log?.warn?.(`telemetry: sqlite unavailable (${e.message}); events go to NDJSON only`); db = null; }
  }

  // Loki push (batched, best effort; NDJSON on disk is the source of truth)
  const loki = config.loki ?? {};
  const lokiUrl = loki.enabled !== false && loki.url ? String(loki.url).replace(/\/$/, '') : null;
  const lokiQueue = [];
  let lokiTimer = null, lokiFailures = 0, lokiLastError = null;
  async function flushLoki() {
    lokiTimer = null;
    if (!lokiUrl || !lokiQueue.length) return;
    const batch = lokiQueue.splice(0, 200);
    const streams = new Map();
    for (const { type, ns, line } of batch) {
      const key = type;
      if (!streams.has(key)) streams.set(key, { stream: { app: 'exhibit', host, type, ...(loki.labels ?? {}) }, values: [] });
      streams.get(key).values.push([ns, line]);
    }
    try {
      const res = await fetch(`${lokiUrl}/loki/api/v1/push`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ streams: [...streams.values()] }), signal: AbortSignal.timeout(5000) });
      if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status} ${(await res.text()).slice(0, 200)}`);
      lokiFailures = 0; lokiLastError = null;
      metrics.inc('loki_pushed_lines_total', {}, batch.length, 'Event lines successfully pushed to Loki');
    } catch (e) {
      lokiFailures++; lokiLastError = e.message;
      metrics.inc('loki_push_failures_total', {}, 1, 'Failed Loki push batches');
      if (lokiFailures <= 3) { lokiQueue.unshift(...batch); }   // retry a few times, then drop (file has it)
      if (lokiFailures === 1 || lokiFailures % 20 === 0) log?.warn?.(`telemetry: loki push failed (${e.message})`);
    }
    if (lokiQueue.length) lokiTimer ??= setTimeout(flushLoki, lokiFailures ? 10000 : 2000);
  }

  function logEvent(type, data = {}) {
    const ts = new Date();
    const record = { ts: ts.toISOString(), level: data.level ?? (data.ok === false ? 'error' : 'info'), type, ...data };
    const line = JSON.stringify(record);
    try { fs.appendFileSync(path.join(eventsDir, `events-${ts.toISOString().slice(0, 10)}.ndjson`), line + '\n'); } catch (e) { log?.warn?.(`telemetry: cannot write event file: ${e.message}`); }
    if (db) { try { db.prepare('INSERT INTO events (ts, type, json) VALUES (?, ?, ?)').run(record.ts, type, line); } catch (e) { log?.debug?.(`telemetry: sqlite insert failed: ${e.message}`); } }
    if (lokiUrl) { lokiQueue.push({ type, ns: String(ts.getTime()) + '000000', line }); lokiTimer ??= setTimeout(flushLoki, 2000); }
    metrics.inc('events_total', { type }, 1, 'Events emitted by the control server');
    return record;
  }

  function recordFeedback({ vote, note, tags, snapshot }) {
    const summary = {
      vote, note: note || null, tags: tags?.length ? tags : null,
      scene: snapshot?.synesthesia?.scene ?? null,
      track: snapshot?.music?.title ?? null, artist: snapshot?.music?.artist ?? null,
      composition: snapshot?.resolume?.composition ?? null,
    };
    if (db) {
      try { db.prepare('INSERT INTO feedback (ts, vote, note, tags, scene, track, artist, composition, snapshot) VALUES (?,?,?,?,?,?,?,?,?)')
        .run(new Date().toISOString(), vote, summary.note, summary.tags ? JSON.stringify(summary.tags) : null, summary.scene, summary.track, summary.artist, summary.composition, JSON.stringify(snapshot)); }
      catch (e) { log?.warn?.(`telemetry: feedback insert failed: ${e.message}`); }
    }
    metrics.inc('feedback_total', { vote, scene: summary.scene ?? 'unknown' }, 1, 'Thumbs up/down votes by Synesthesia scene');
    return logEvent('feedback', { ...summary, snapshot });
  }

  function recentFeedback(limit = 20) {
    if (!db) return [];
    try { return db.prepare('SELECT id, ts, vote, note, tags, scene, track, artist, composition FROM feedback ORDER BY id DESC LIMIT ?').all(limit); } catch { return []; }
  }
  function feedbackCounts() {
    if (!db) return { today: { up: 0, down: 0 }, total: { up: 0, down: 0 } };
    try {
      const today = new Date(); today.setHours(0, 0, 0, 0);
      const rows = db.prepare('SELECT vote, SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END) AS today, COUNT(*) AS total FROM feedback GROUP BY vote').all(today.toISOString());
      const out = { today: { up: 0, down: 0 }, total: { up: 0, down: 0 } };
      for (const r of rows) { out.today[r.vote] = Number(r.today); out.total[r.vote] = Number(r.total); }
      return out;
    } catch { return { today: { up: 0, down: 0 }, total: { up: 0, down: 0 } }; }
  }
  function recentEvents(limit = 50, type = null) {
    if (!db) return [];
    try { return (type ? db.prepare('SELECT ts, type, json FROM events WHERE type = ? ORDER BY id DESC LIMIT ?').all(type, limit) : db.prepare('SELECT ts, type, json FROM events ORDER BY id DESC LIMIT ?').all(limit)).map((r) => JSON.parse(r.json)); } catch { return []; }
  }

  return { metrics, logEvent, recordFeedback, recentFeedback, feedbackCounts, recentEvents, lokiStatus: () => ({ url: lokiUrl, queued: lokiQueue.length, failures: lokiFailures, lastError: lokiLastError }), db };
}

// node:sqlite is loaded lazily so the module still works on a Node without it.
function await_import_sqlite() {
  // eslint-disable-next-line no-undef
  return process.getBuiltinModule ? process.getBuiltinModule('node:sqlite') : (() => { throw new Error('node:sqlite not available'); })();
}
