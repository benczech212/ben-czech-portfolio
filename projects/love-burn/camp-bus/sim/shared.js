// Shared bits for every sim page: navigation, settings (localStorage), and a small WebAudio synth for test sounds.
// Include with <script src="shared.js"></script> and put <nav id="campnav"></nav> where the links go.
(() => {
  // ------------------------------------------------------------ NAV
  // One place to add a page. Groups become sections in the header; the current page is highlighted by filename.
  const PAGES = [
    { group: 'Play',    href: 'camp.html',        label: 'Virtual Camp',      hint: 'crystals, hallway, map, entrance screen, props, game, vibes' },
    { group: 'Play',    href: 'instruments.html', label: 'Instruments',       hint: 'Sonic Mycelium Cluster, Crystal Geode Synth, Monstera Piezo Plant, Water Chord Tubes, activity, MIDI mapping' },
    { group: 'Control', href: 'routes.html',      label: 'Routes',            hint: 'map inputs to outputs; edits go live in the mapping engine' },
    { group: 'Control', href: 'settings.html',    label: 'Settings',          hint: 'sound, broker, page options' },
    { group: 'Booth',   href: 'nodes.html',       label: 'Devices',           hint: 'every node on the bus: health, what it is hearing, and a link to its own console' },
    { group: 'Booth',   href: 'devices.html',     label: 'Booth Devices',     hint: 'Resolume output, kitchen bell, check-in' },
    { group: 'Debug',   href: 'gps.html',         label: 'GPS',               hint: 'live position plot in metres: scatter, drift, where casts were thrown from' },
    { group: 'Debug',   href: 'dashboard.html',   label: 'Console',           hint: 'nodes, loaded rules, raw bus log' },
  ];
  const here = (location.pathname.split('/').pop() || 'index.html').toLowerCase();
  function renderNav() {
    const el = document.getElementById('campnav'); if (!el) return;
    const groups = [];
    for (const p of PAGES) { let g = groups.find((x) => x.name === p.group); if (!g) { g = { name: p.group, pages: [] }; groups.push(g); } g.pages.push(p); }
    el.className = 'campnav';
    el.innerHTML = `<a class="brand" href="index.html">Camp Bus</a>` + groups.map((g) => `<span class="grp">${g.pages.map((p) =>
      `<a href="${p.href}" class="${p.href === here ? 'on' : ''}" title="${p.hint}">${p.label}</a>`).join('')}</span>`).join('');
    if (!document.querySelector('link[href$="ui.css"]')) {        // a page that forgot the stylesheet still gets readable nav
      const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = 'ui.css'; document.head.appendChild(l);
    }
  }

  // ------------------------------------------------------------ WHO IS A DEVICE
  // Browser pages and CLI one-shots join the bus with a random client id and leave a retained
  // last-will behind when they close, so the broker accumulates hundreds of corpses that are not
  // devices. This is the one place that decides which is which, and it matches on the client-id
  // prefixes this repo's own tools use - nothing else. Shape rules do not work: a six-character
  // random suffix is indistinguishable from `midi_bridge`, `mapping_engine` or `toy_big_red_button`,
  // all of which end in a six-letter word. A tool with an unknown prefix shows up as unexpected,
  // which is the right way to be wrong.
  const TOOL_PREFIXES = ['sim_', 'console_', 'spy_', 'probe_', 'test_', 'mapping_set', 'mapping_reload',
                         'mapping_engine_test', 'display_late'];
  const isEphemeralNode = (id) => TOOL_PREFIXES.some((p) => String(id || '').startsWith(p));

  // ------------------------------------------------------------ SETTINGS (per browser, shared by every page; live-updates across open tabs)
  const KEY = 'campbus.sim.settings.v1';
  const DEFAULTS = { sound: true, volume: 0.6, broker: '', logs: true, person: 'adult_shoes' };
  const listeners = new Set();
  function load() { try { return { ...DEFAULTS, ...(JSON.parse(localStorage.getItem(KEY) || 'null') || {}) }; } catch (e) { return { ...DEFAULTS }; } }
  let cur = load();
  const Settings = {
    DEFAULTS,
    get: (k) => (k == null ? { ...cur } : cur[k]),
    set(patch) { cur = { ...cur, ...patch }; try { localStorage.setItem(KEY, JSON.stringify(cur)); } catch (e) {} listeners.forEach((f) => f(cur)); },
    reset() { cur = { ...DEFAULTS }; try { localStorage.removeItem(KEY); } catch (e) {} listeners.forEach((f) => f(cur)); },
    onChange(f) { listeners.add(f); return () => listeners.delete(f); },
    brokerUrl() { return cur.broker || `ws://${location.hostname || 'localhost'}:9001`; },
  };
  window.addEventListener('storage', (e) => { if (e.key === KEY) { cur = load(); listeners.forEach((f) => f(cur)); } });

  // ------------------------------------------------------------ SOUND (test-only; the real instruments play through Ableton)
  // Drums are synthesised (no samples to ship); geodes get a held pad voice whose filter follows the touch value.
  let ac = null, master = null;
  function ctx() {
    if (!ac) { ac = new (window.AudioContext || window.webkitAudioContext)(); master = ac.createGain(); master.connect(ac.destination); }
    if (ac.state === 'suspended') ac.resume();
    master.gain.value = cur.volume;
    return ac;
  }
  const on = () => cur.sound;
  function noise(a, dur) { const b = a.createBuffer(1, a.sampleRate * dur, a.sampleRate), d = b.getChannelData(0); for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1; const s = a.createBufferSource(); s.buffer = b; return s; }
  function env(a, g, peak, decay, t0 = a.currentTime) { g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), t0 + 0.004); g.gain.exponentialRampToValueAtTime(0.0001, t0 + decay); }
  const DRUMS = {
    kick(a, v) { const o = a.createOscillator(), g = a.createGain(); o.frequency.setValueAtTime(150 + 60 * v, a.currentTime); o.frequency.exponentialRampToValueAtTime(40, a.currentTime + 0.25); env(a, g, 0.9 * v, 0.4); o.connect(g).connect(master); o.start(); o.stop(a.currentTime + 0.45); },
    snare(a, v) { const n = noise(a, 0.3), f = a.createBiquadFilter(), g = a.createGain(); f.type = 'highpass'; f.frequency.value = 1500; env(a, g, 0.6 * v, 0.22); n.connect(f).connect(g).connect(master); n.start(); const o = a.createOscillator(), g2 = a.createGain(); o.frequency.setValueAtTime(220, a.currentTime); o.frequency.exponentialRampToValueAtTime(120, a.currentTime + 0.1); env(a, g2, 0.5 * v, 0.15); o.connect(g2).connect(master); o.start(); o.stop(a.currentTime + 0.2); },
    hat(a, v) { const n = noise(a, 0.08), f = a.createBiquadFilter(), g = a.createGain(); f.type = 'highpass'; f.frequency.value = 7000; env(a, g, 0.35 * v, 0.06); n.connect(f).connect(g).connect(master); n.start(); },
    tom(a, v) { const o = a.createOscillator(), g = a.createGain(); o.frequency.setValueAtTime(180, a.currentTime); o.frequency.exponentialRampToValueAtTime(90, a.currentTime + 0.3); env(a, g, 0.8 * v, 0.35); o.connect(g).connect(master); o.start(); o.stop(a.currentTime + 0.4); },
    clap(a, v) { for (let i = 0; i < 3; i++) { const n = noise(a, 0.12), f = a.createBiquadFilter(), g = a.createGain(); f.type = 'bandpass'; f.frequency.value = 1800; f.Q.value = 0.8; env(a, g, 0.5 * v, 0.1 + i * 0.03, a.currentTime + i * 0.012); n.connect(f).connect(g).connect(master); n.start(a.currentTime + i * 0.012); } },
    ride(a, v) { const g = a.createGain(); env(a, g, 0.25 * v, 0.9); [3.1, 4.7, 6.3, 8.9].forEach((k) => { const o = a.createOscillator(); o.type = 'square'; o.frequency.value = 400 * k; const gg = a.createGain(); gg.gain.value = 0.25; o.connect(gg).connect(g); o.start(); o.stop(a.currentTime + 1); }); const f = a.createBiquadFilter(); f.type = 'highpass'; f.frequency.value = 3000; g.connect(f).connect(master); },
  };
  const voices = new Map();
  const Sound = {
    enabled: on,
    unlock() { if (on()) try { ctx(); } catch (e) {} },
    drum(kind, vel = 0.8) { if (!on()) return; try { const a = ctx(); (DRUMS[kind.replace('closed ', '')] || DRUMS.tom)(a, Math.max(0.05, Math.min(1, vel))); } catch (e) {} },
    // held pad voice: id keeps it addressable; value 0..1 opens the filter (capacitive "how much of you is touching")
    voiceOn(id, freq, vel = 0.8, value = 0.5) {
      if (!on()) return; try {
        const a = ctx(); this.voiceOff(id, 0.02);
        const g = a.createGain(), f = a.createBiquadFilter(); f.type = 'lowpass'; f.Q.value = 4; f.frequency.value = 200 + 2600 * value;
        g.gain.setValueAtTime(0.0001, a.currentTime); g.gain.exponentialRampToValueAtTime(0.35 * vel, a.currentTime + 0.05);
        const os = [0, 7, -7].map((c) => { const o = a.createOscillator(); o.type = 'sawtooth'; o.frequency.value = freq; o.detune.value = c; o.connect(f); o.start(); return o; });
        const lfo = a.createOscillator(), lg = a.createGain(); lfo.frequency.value = 5; lg.gain.value = 3; lfo.connect(lg).connect(os[0].detune); lfo.start();
        f.connect(g).connect(master); voices.set(id, { g, f, os: [...os, lfo] });
      } catch (e) {}
    },
    voiceSet(id, value) { const v = voices.get(id); if (v && ac) v.f.frequency.setTargetAtTime(200 + 2600 * value, ac.currentTime, 0.05); },
    voiceOff(id, release = 0.4) { const v = voices.get(id); if (!v || !ac) return; voices.delete(id); v.g.gain.setTargetAtTime(0.0001, ac.currentTime, release / 4); v.os.forEach((o) => o.stop(ac.currentTime + release + 0.1)); },
    // "nature" voice for the plant leaves: wind through leaves (noise through a resonant bandpass that opens with the bend)
    // over a soft bass drone with slow vibrato; releasing gives a rustle and a plucked harmonic scaled by the snap velocity
    natureOn(id, freq, value = 0) {
      if (!on()) return; try {
        const a = ctx(); this.voiceOff('n:' + id, 0.05);
        const g = a.createGain(); g.gain.setValueAtTime(0.0001, a.currentTime); g.gain.exponentialRampToValueAtTime(0.5, a.currentTime + 0.4);
        const n = noise(a, 4); n.loop = true; const bp = a.createBiquadFilter(); bp.type = 'bandpass'; bp.Q.value = 5; bp.frequency.value = 350 + 2200 * value;
        const ng = a.createGain(); ng.gain.value = 0.35; n.connect(bp).connect(ng).connect(g); n.start();
        const o = a.createOscillator(), og = a.createGain(); o.type = 'sine'; o.frequency.value = freq; og.gain.value = 0.22; o.connect(og).connect(g); o.start();
        const o2 = a.createOscillator(), og2 = a.createGain(); o2.type = 'triangle'; o2.frequency.value = freq * 2.003; og2.gain.value = 0.05 + 0.1 * value; o2.connect(og2).connect(g); o2.start();
        const lfo = a.createOscillator(), lg = a.createGain(); lfo.frequency.value = 0.35; lg.gain.value = 4; lfo.connect(lg).connect(o.detune); lfo.start();
        g.connect(master); voices.set('n:' + id, { g, f: bp, og2, os: [n, o, o2, lfo], freq });
      } catch (e) {}
    },
    natureSet(id, value) { const v = voices.get('n:' + id); if (v && ac) { v.f.frequency.setTargetAtTime(350 + 2200 * value, ac.currentTime, 0.08); v.og2.gain.setTargetAtTime(0.05 + 0.15 * value, ac.currentTime, 0.1); } },
    natureOff(id, vel = 0.5) {
      const v = voices.get('n:' + id); if (!v || !ac) return; this.voiceOff('n:' + id, 0.7);
      try { const a = ac; const r = noise(a, 0.25), hp = a.createBiquadFilter(), rg = a.createGain(); hp.type = 'highpass'; hp.frequency.value = 2500; env(a, rg, 0.35 * vel, 0.22); r.connect(hp).connect(rg).connect(master); r.start();
        const p = a.createOscillator(), pg = a.createGain(); p.type = 'triangle'; p.frequency.value = v.freq * 3; env(a, pg, 0.3 * vel, 0.9); p.connect(pg).connect(master); p.start(); p.stop(a.currentTime + 1); } catch (e) {}
    },
    // "water" voice for the chord tubes: a glass-harmonica sine stack over a resonant body, with a drip
    // of bubbles on contact; the reading opens the body filter, so deeper contact is rounder and louder
    waterOn(id, freq, vel = 0.8, value = 0.5) {
      if (!on()) return; try {
        const a = ctx(); this.voiceOff('w:' + id, 0.05);
        const g = a.createGain(), f = a.createBiquadFilter(); f.type = 'lowpass'; f.Q.value = 2; f.frequency.value = 400 + 3200 * value;
        g.gain.setValueAtTime(0.0001, a.currentTime); g.gain.exponentialRampToValueAtTime(0.3 * vel, a.currentTime + 0.12);
        const os = [[1, 0.5], [2, 0.22], [3.01, 0.1], [4.98, 0.05]].map(([m, amp]) => {
          const o = a.createOscillator(), og = a.createGain(); o.type = 'sine'; o.frequency.value = freq * m; og.gain.value = amp; o.connect(og).connect(f); o.start(); return o;
        });
        const lfo = a.createOscillator(), lg = a.createGain(); lfo.frequency.value = 0.6; lg.gain.value = 5; lfo.connect(lg).connect(os[0].detune); lfo.start();
        f.connect(g).connect(master); voices.set('w:' + id, { g, f, os: [...os, lfo], freq });
        const b = noise(a, 0.12), bp = a.createBiquadFilter(), bg = a.createGain();      // the plop of breaking the surface
        bp.type = 'bandpass'; bp.frequency.value = 900 + 700 * vel; bp.Q.value = 3; env(a, bg, 0.25 * vel, 0.1);
        b.connect(bp).connect(bg).connect(master); b.start();
      } catch (e) {}
    },
    waterSet(id, value) { const v = voices.get('w:' + id); if (v && ac) v.f.frequency.setTargetAtTime(400 + 3200 * value, ac.currentTime, 0.06); },
    waterOff(id) { this.voiceOff('w:' + id, 0.6); },
    allOff() { for (const id of [...voices.keys()]) this.voiceOff(id, 0.1); },
  };
  Settings.onChange((s) => { if (!s.sound) Sound.allOff(); if (master) master.gain.value = s.volume; });

  window.Camp = { PAGES, Settings, Sound, renderNav, isEphemeralNode };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', renderNav); else renderNav();
})();
