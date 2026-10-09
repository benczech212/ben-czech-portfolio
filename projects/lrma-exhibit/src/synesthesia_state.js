// Live model of Synesthesia built from its OSC output (needs OSC_OUTPUT_CONTROLS=1):
//   /scenes/{name}                       scene launched (controls of the previous scene are dropped)
//   /controls/{bank}/{name} v            control value; bank = "meta" (global look) or the scene's own bank
//   /controls/{bank}/{name}/r|g|b v      colour component
//   /controls/{bank}/{name}/x|y v        xy pad component
//   /controls/{bank}/{name}/numoptions n dropdown: option count, followed by /label per option (in order)
//   /audio/...                           analysis stream, ~30 msgs/frame; a few keys are kept for meters
// Float args → slider (0..1 normalized), int args → toggle or dropdown index.
import fs from 'node:fs';
import path from 'node:path';
import { listen, send } from './osc.js';

const AUDIO_KEYS = {
  '/audio/level/all': 'level', '/audio/level/bass': 'bass', '/audio/level/mid': 'mid', '/audio/level/high': 'high',
  '/audio/bpm/bpm': 'bpm', '/audio/beat/onbeat': 'onbeat', '/audio/hits/all': 'hit', '/audio/energy/intensity': 'intensity',
};

export function createSynState({ listenPort, sendHost, sendPort, log, persistFile }) {
  const st = {
    scene: null, lastAt: 0, msgCount: 0, audio: {}, audioAt: 0,
    banks: {},          // bank → { name → control }
    raw: [],            // last non-audio messages for the debug view
  };
  // Synesthesia only broadcasts controls when a scene loads, so remember the last model on disk
  // and restore it on start; it is corrected at the next scene change.
  if (persistFile) {
    try {
      const saved = JSON.parse(fs.readFileSync(persistFile, 'utf8'));
      if (saved?.banks) { st.banks = saved.banks; st.scene = saved.scene ?? null; st.restored = true; log?.info?.(`synesthesia: restored ${Object.keys(st.banks).length} control banks from ${path.basename(persistFile)}`); }
    } catch { /* first run */ }
  }
  let saveTimer = null;
  const scheduleSave = () => {
    if (!persistFile || saveTimer) return;
    saveTimer = setTimeout(() => {
      saveTimer = null;
      try { fs.mkdirSync(path.dirname(persistFile), { recursive: true }); fs.writeFileSync(persistFile, JSON.stringify({ scene: st.scene, banks: st.banks, savedAt: Date.now() })); } catch (e) { log?.debug?.(`synesthesia: persist failed: ${e.message}`); }
    }, 2000);
  };
  const control = (bank, name) => {
    st.banks[bank] ??= {};
    return (st.banks[bank][name] ??= { name, bank, type: 'float', value: null, updatedAt: 0 });
  };

  function handle({ address, args, types }) {
    st.lastAt = Date.now(); st.msgCount++;
    if (address.startsWith('/audio/')) {
      const k = AUDIO_KEYS[address];
      if (k) { st.audio[k] = args[0]; st.audioAt = st.lastAt; }
      return;
    }
    st.raw.push({ t: st.lastAt, address, args }); if (st.raw.length > 80) st.raw.shift();
    scheduleSave();

    let m;
    if ((m = /^\/scenes\/([^/]+)$/.exec(address))) {
      const name = m[1];
      if (name !== st.scene) {
        // keep meta (global) bank, drop the old scene bank
        for (const b of Object.keys(st.banks)) if (b !== 'meta') delete st.banks[b];
        st.scene = name;
        log?.info?.(`synesthesia: scene → ${name}`);
      }
      return;
    }
    if (address.endsWith('/raw')) return;
    if ((m = /^\/controls\/global\/(\w+)\/(\d+)\/name$/.exec(address))) { control('global', `${m[1]}_${m[2]}`).label = args[0]; return; }
    if ((m = /^\/controls\/([^/]+)\/([^/]+)\/(r|g|b)$/.exec(address))) {
      const c = control(m[1], m[2]); c.type = 'color'; c.rgb ??= { r: 0, g: 0, b: 0 }; c.rgb[m[3]] = args[0]; c.updatedAt = st.lastAt; return;
    }
    if ((m = /^\/controls\/([^/]+)\/([^/]+)\/(x|y)$/.exec(address))) {
      const c = control(m[1], m[2]); c.type = 'xy'; c.xy ??= { x: 0, y: 0 }; c.xy[m[3]] = args[0]; c.updatedAt = st.lastAt; return;
    }
    if ((m = /^\/controls\/([^/]+)\/([^/]+)\/numoptions$/.exec(address))) {
      const c = control(m[1], m[2]); c.type = 'dropdown'; c.numoptions = args[0]; c.options = []; return;
    }
    if ((m = /^\/controls\/([^/]+)\/([^/]+)\/label(?:\/(\d+))?$/.exec(address))) {
      const c = control(m[1], m[2]); c.type = 'dropdown'; c.options ??= [];
      if (m[3] != null) c.options[Number(m[3])] = args[0]; else if (c.options.length < (c.numoptions ?? 99)) c.options.push(args[0]);
      return;
    }
    if ((m = /^\/controls\/([^/]+)\/([^/]+)$/.exec(address))) {
      const [, bank, name] = m;
      if (args.length === 2 && typeof args[0] === 'number' && typeof args[1] === 'number') {
        const c = control(bank, name); c.type = 'xy'; c.xy = { x: args[0], y: args[1] }; c.updatedAt = st.lastAt; return;
      }
      const c = control(bank, name);
      const v = args[0];
      if (c.type !== 'dropdown' && c.type !== 'color' && c.type !== 'xy') {
        // Synesthesia currently sends everything as 'f'; an 'i' tag (if it ever appears) is a sure toggle.
        c.type = types?.[0] === 'i' ? 'toggle' : 'float';
      }
      c.value = v; c.updatedAt = st.lastAt;
      return;
    }
  }

  const em = listen(listenPort);
  em.on('message', handle);
  em.on('error', (e) => log?.warn?.(`synesthesia listener on ${listenPort}: ${e.message}`));
  em.on('listening', () => log?.info?.(`synesthesia OSC listener on udp ${listenPort}`));

  const tx = (address, args = []) => send(sendHost, sendPort, address, args);

  return {
    state() {
      return { scene: st.scene, restored: !!st.restored, lastAt: st.lastAt, ageMs: st.lastAt ? Date.now() - st.lastAt : null, msgCount: st.msgCount, audio: st.audio, audioAt: st.audioAt, banks: st.banks, raw: st.raw.slice(-40) };
    },
    heartbeatAge() { return st.lastAt ? Date.now() - st.lastAt : null; },
    // ── senders ────────────────────────────────────────────────────────────────
    setControl(bank, name, value, sub) {
      const addr = `/controls/${bank}/${name}${sub ? `/${sub}` : ''}`;
      const c = st.banks[bank]?.[name];
      const v = (c?.type === 'toggle' || c?.type === 'dropdown') && !sub ? { i: Math.round(Number(value)) } : { f: Number(value) };
      // optimistic local update so the UI does not flicker before the echo
      if (c) { if (sub && c.rgb && 'rgb'.includes(sub)) c.rgb[sub] = Number(value); else if (sub && c.xy && 'xy'.includes(sub)) c.xy[sub] = Number(value); else if (!sub) c.value = Number(value); c.updatedAt = Date.now(); }
      return tx(addr, [v]);
    },
    // Controls changed since `since` (ms) plus scene/audio — feeds the panel's live stream.
    delta(since) {
      const controls = {};
      for (const [bank, ctrls] of Object.entries(st.banks)) for (const c of Object.values(ctrls)) if ((c.updatedAt ?? 0) > since) (controls[bank] ??= {})[c.name] = { value: c.value, rgb: c.rgb, xy: c.xy, type: c.type };
      return { t: Date.now(), scene: st.scene, audio: st.audio, controls };
    },
    controlCommand(bank, name, cmd, on) { // default | random | lock
      if (cmd === 'lock') return tx(`/controls/${bank}/${name}/lock`, [on ? 1 : 0]);
      return tx(`/controls/${bank}/${name}/${cmd}`);
    },
    bankCommand(bank, cmd) { return tx(`/controls/banks/${bank}/${cmd}`); },   // default | random | undo
    playlist(action, value) {
      switch (action) {
        case 'next': return tx('/playlist/next', [1]);
        case 'previous': return tx('/playlist/previous', [1]);
        case 'toggleplay': return tx('/playlist/toggleplay', [1]);
        case 'play': return tx('/playlist/play', [value ? 1 : 0]);
        case 'shuffle': return tx('/playlist/shuffle', [value ? 1 : 0]);
        case 'auto': return tx('/playlist/auto', [value ? 1 : 0]);
        case 'select': return tx('/playlist/select', [String(value)]);
        case 'position': return tx('/playlist/position', [Number(value)]);
        default: throw new Error(`unknown playlist action ${action}`);
      }
    },
    scene(name, preset) { return preset ? tx(`/scenes/${name}`, [String(preset)]) : tx(`/scenes/${name}`); },
    favslot(n) { return tx(`/favslots/${n}`); },
    preset(name) { return tx('/presets', [String(name)]); },
    render(on) { return tx('/render/enabled', [on ? 1 : 0]); },
    media(name) { return tx('/media/name', [String(name)]); },
    raw(address, args) { return tx(address, args ?? []); },
    close() { em.close(); },
  };
}
