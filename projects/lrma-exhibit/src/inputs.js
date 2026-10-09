// Visitor-input hub: an OSC listener where every controller reports as
//   /input/<source>/<control> <value>      e.g. /input/xbox/lx 0.42   /input/td/person_x 0.7
// (src/inputs.py bridges the Xbox controller + SpaceMouse; TouchDesigner / Raspberry Pi send
// directly). Every message marks the exhibit "engaged"; `inputs.mappings` in exhibit.yaml routes
// controls to Synesthesia controls, OSC targets, music volume or macros.
import { listen, send } from './osc.js';
import { getOscTarget } from './config.js';

const clamp01 = (v) => Math.max(0, Math.min(1, v));

export function createInputHub({ cfg, log, syn, registerInput, runMacro, runAction, getSceneControls, getScheme }) {
  // getScheme(bank, slot) → { bank, control, sub, action, type, label } | null   (per-scene input schemes)
  const st = { lastAt: 0, msgCount: 0, values: {}, mapped: {}, errors: 0, devices: {}, sources: {} };
  const rateWin = {};      // source → timestamps in the last second (message rate for the UI)
  const rel = {};          // mapping id → current relative value
  const lastSend = {};     // throttle per target
  const pressed = {};      // button edge detection
  let matchers = [];

  function compile() {
    matchers = (cfg.inputs?.mappings ?? []).map((m, i) => {
      const pattern = String(m.match ?? '');
      const re = new RegExp('^' + pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '[^/]+') + '$');
      return { id: `${i}:${pattern}`, re, ...m };
    });
  }
  compile();

  function analogActive(value, deadzone) { return typeof value === 'number' && Math.abs(value) > deadzone; }

  const currentBank = () => { const s = syn?.state?.(); return s?.scene ? String(s.scene).toLowerCase().replace(/[^a-z0-9]/g, '') : null; };
  const liveValue = (bank, control) => syn?.state?.().banks?.[bank]?.[control]?.value;

  async function apply(map, address, value, now) {
    let to = map.to ?? {};
    const mode = map.mode ?? 'absolute';
    let out = value;
    // scheme slot → concrete scene control for whatever scene is playing
    let slotAction = null;
    if (to.scheme) {
      const bank = currentBank(); const r = bank ? getScheme?.(bank, to.scheme) : null;
      if (!r) return;
      to = { synesthesia: { bank: r.bank, name: r.control, sub: r.sub ?? undefined } }; slotAction = r.action;
      if (mode !== 'button') { st.mapped[address] = { id: map.id, to, value: st.mapped[address]?.value ?? null, at: now, slot: map.to.scheme, label: r.label }; }
    }
    if (mode === 'button') {
      const down = Number(value) >= 0.5;
      const was = pressed[map.id] ?? false; pressed[map.id] = down;
      if (slotAction === 'momentary') {               // bang: 1 on press, 0 on release
        if (down === was) return;
        st.mapped[address] = { id: map.id, to, value: down ? 1 : 0, at: now, slot: map.to.scheme };
        return syn?.setControl(to.synesthesia.bank, to.synesthesia.name, down ? 1 : 0);
      }
      if (!down || was) return;                      // rising edge only
      if (slotAction === 'toggle') {
        const cur = liveValue(to.synesthesia.bank, to.synesthesia.name); const next = Number(cur) >= 0.5 ? 0 : 1;
        st.mapped[address] = { id: map.id, to, value: next, at: now, slot: map.to.scheme };
        return syn?.setControl(to.synesthesia.bank, to.synesthesia.name, next);
      }
      if (to.macro) return runMacro(to.macro);
      if (to.value != null) out = to.value; else out = 1;
    } else {
      let v = Number(value); if (Number.isNaN(v)) return;
      if (map.invert) v = -v;
      if (map.curve && map.curve !== 1) v = Math.sign(v) * Math.pow(Math.abs(v), map.curve);   // expo: fine control near centre, fast at the edges
      if (map.gain) v *= map.gain;
      if (mode === 'relative') {
        // stick / spacemouse deflection changes the target over time: v * rate per second
        const dt = Math.min(0.25, (now - (rel[map.id + ':t'] ?? now)) / 1000); rel[map.id + ':t'] = now;
        // relative state is per target, so a scheme slot re-targets cleanly when the scene changes
        const relKey = to.synesthesia ? `${map.id}|${to.synesthesia.bank}/${to.synesthesia.name}/${to.synesthesia.sub ?? ''}` : map.id;
        let cur = rel[relKey];
        if (cur == null) {   // first touch: continue from where the target currently is, not from a fixed start
          const t = to.synesthesia; let live = null;
          if (t?.scene_control_index != null) live = (getSceneControls?.() ?? [])[t.scene_control_index]?.value;
          else if (t?.name) { const c = syn?.state?.().banks?.[t.bank ?? 'meta']?.[t.name]; live = t.sub && c?.xy ? c.xy[t.sub] : c?.value; }
          cur = typeof live === 'number' ? live : (map.start ?? 0.5);
        }
        out = clamp01(cur + v * (map.rate ?? 0.5) * dt);
        if (Math.abs(out - cur) < 1e-4) return;
        rel[relKey] = out;
      } else {
        const [lo, hi] = map.range ?? [-1, 1];        // input range → 0..1
        out = clamp01((v - lo) / (hi - lo || 1));
        if (map.out) out = map.out[0] + out * (map.out[1] - map.out[0]);
      }
    }
    const key = map.id; const minGap = map.throttle_ms ?? 33;
    if (now - (lastSend[key] ?? 0) < minGap) return;
    lastSend[key] = now;
    st.mapped[address] = { ...(st.mapped[address] ?? {}), id: map.id, to, value: Math.round(out * 1000) / 1000, at: now, slot: map.to?.scheme ?? undefined };
    if (to.synesthesia) {
      const t = to.synesthesia;
      if (t.scene_control_index != null) {         // Nth float control of whatever scene is playing
        const ctrls = getSceneControls?.() ?? [];
        const c = ctrls[t.scene_control_index]; if (!c) return;
        return syn?.setControl(c.bank, c.name, out);
      }
      return syn?.setControl(t.bank ?? 'meta', t.name, out, t.sub);
    }
    if (to.osc) { const tg = getOscTarget(cfg, to.osc.target); return send(tg.host, tg.port, to.osc.address, [to.osc.int ? { i: Math.round(out) } : { f: out }]); }
    if (to.music === 'volume') return runAction('music', 'volume', out);
    if (to.macro) return runMacro(to.macro);
  }

  async function handle({ address, args }) {
    const m = /^\/input\/([^/]+)\/(.+)$/.exec(address);
    if (!m) return;
    const now = Date.now();
    const [, source, control] = m; const value = args[0];
    if (source === '_devices') {   // bridge presence report: /input/_devices/<source> <0|1> <name>
      st.devices[control] = { connected: Number(value) >= 1, name: typeof args[1] === 'string' ? args[1] : '', at: now };
      return;
    }
    st.lastAt = now; st.msgCount++;
    st.values[`${source}/${control}`] = { value, at: now };
    const w = (rateWin[source] ??= []); w.push(now); while (w.length && now - w[0] > 1000) w.shift();
    st.sources[source] = { lastAt: now, count: (st.sources[source]?.count ?? 0) + 1 };
    const dz = cfg.inputs?.deadzone ?? 0.08;
    const isButton = typeof value === 'boolean' || /^(btn|button|b_|dpad)/.test(control) || (typeof value === 'number' && (value === 0 || value === 1) && !/^(lx|ly|rx|ry|lt|rt|x|y|z|roll|pitch|yaw)$/.test(control));
    if (isButton ? Number(value) >= 0.5 : analogActive(value, dz)) registerInput(source, { control, value });
    for (const map of matchers) {
      if (!map.re.test(address)) continue;
      try { await apply(map, address, value, now); } catch (e) { st.errors++; log.debug(`input map ${map.id}: ${e.message}`); }
    }
  }

  const port = cfg.inputs?.osc_port;
  let em = null;
  if (port) {
    // Only accept /input/* from these source IPs (inputs.allowed_sources; default this PC only).
    const allow = cfg.inputs?.allowed_sources ?? ['127.0.0.1'];
    em = listen(port, '0.0.0.0', { allow });
    em.on('message', handle);
    em.on('rejected', (r) => log.debug(`input OSC from ${r.address} ignored (not in inputs.allowed_sources)`));
    em.on('error', (e) => log.warn(`input OSC listener ${port}: ${e.message}`));
    em.on('listening', () => log.info(`input OSC listener on udp ${port} (/input/<source>/<control>)`));
  }

  return {
    state() {
      const now = Date.now();
      const sources = Object.fromEntries(Object.entries(st.sources).map(([s, v]) => [s, { ...v, ageMs: now - v.lastAt, rate: (rateWin[s] ?? []).filter((t) => now - t <= 1000).length }]));
      const devices = Object.fromEntries(Object.entries(st.devices).map(([s, d]) => [s, { ...d, connected: d.connected && now - d.at < 8000, stale: now - d.at >= 8000 }]));
      return { port: port ?? null, lastAt: st.lastAt, ageMs: st.lastAt ? now - st.lastAt : null, msgCount: st.msgCount, errors: st.errors, devices, sources, values: st.values, mapped: st.mapped, mappings: matchers.map((m) => ({ id: m.id, match: m.match, mode: m.mode ?? 'absolute', to: m.to })) };
    },
    inject: handle,           // for /api/input with a control name, and tests
    // compact snapshot for the live stream: only values/mappings that changed in the last 2 s
    delta() { const now = Date.now(); const values = {}; for (const [k, v] of Object.entries(st.values)) if (now - v.at < 2000) values[k] = v.value; const mapped = {}; for (const m of Object.values(st.mapped)) if (now - m.at < 2000) mapped[m.id] = m.value; return { t: now, values, mapped, engaged: undefined }; },
    reload(newCfg) { cfg = newCfg; compile(); },
    close() { em?.close(); },
  };
}
