// YouTube Music driver: Chrome driver + page control via DevTools.
// Actions (the `music` step / `exhibit music <action>`): play, pause, next, prev,
// volume <0-1>, status, dismiss, navigate <url>.
// A local-file player can later reuse chrome.js + cdp.js the same way.
import * as chrome from './chrome.js';
import { connectPage } from '../cdp.js';

export const prepare = chrome.prepare;
export const launchSpec = chrome.launchSpec;
export const isRunning = chrome.isRunning;
export const kill = chrome.kill;
export const readyDefaults = chrome.readyDefaults;

export function check(app) {
  const issues = chrome.check(app);
  if (app.url && !/^https:\/\/music\.youtube\.com\//.test(app.url)) issues.push({ level: 'warn', msg: `url does not look like a YouTube Music URL: ${app.url}` });
  return issues;
}

const PAGE_PREFIX = 'https://music.youtube.com/';

// Runs inside the page. Kept as one self-contained function so selectors live in one place.
const PAGE_LIB = `
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const text = (el) => (el?.textContent ?? '').trim();
  const video = () => $('video');
  const dismiss = () => {
    let n = 0;
    const ok = /^(yes|ok|okay|continue|got it|accept all|i agree|no thanks|dismiss|close|skip)$/i;
    for (const b of document.querySelectorAll('tp-yt-paper-dialog button, ytmusic-you-there-renderer button, ytmusic-popup-container button')) {
      const label = text(b) || b.getAttribute('aria-label') || '';
      if (ok.test(label.trim())) { b.click(); n++; }
    }
    return n;
  };
  const status = () => {
    const v = video();
    return {
      url: location.href,
      signedIn: !!$('ytmusic-nav-bar #right-content img, ytmusic-nav-bar #right-content tp-yt-paper-icon-button[aria-label*="account" i]'),
      hasMedia: !!v && v.readyState > 0,
      paused: v ? v.paused : null,
      currentTime: v ? Math.round(v.currentTime) : null,
      duration: v ? Math.round(v.duration || 0) : null,
      volume: v ? Math.round(v.volume * 100) / 100 : null,
      muted: v ? v.muted : null,
      title: text($('ytmusic-player-bar .title')) || document.title,
      artist: text($('ytmusic-player-bar .byline')),
    };
  };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const clickFirst = (sels) => { for (const s of sels) { const el = $(s); if (el) { el.click(); return s; } } return null; };
  const play = async () => {
    dismiss();
    let v = video();
    if (v && v.readyState > 0) { await v.play().catch(() => {}); if (v.muted) v.muted = false; return { via: 'video.play', ...status() }; }
    // Nothing loaded yet (playlist/album/home page): press the big play button, then wait for media.
    const via = clickFirst([
      'ytmusic-responsive-header-renderer ytmusic-play-button-renderer',
      'ytmusic-detail-header-renderer ytmusic-play-button-renderer',
      'ytmusic-immersive-header-renderer ytmusic-play-button-renderer',
      'ytmusic-play-button-renderer',
      'ytmusic-player-bar #play-pause-button',
    ]);
    for (let i = 0; i < 40; i++) { await sleep(250); v = video(); if (v && v.readyState > 0 && !v.paused) break; }
    v = video();
    if (v && v.paused) await v.play().catch(() => {});
    return { via: via ?? 'none', ...status() };
  };
  const pause = () => { const v = video(); if (v) v.pause(); return status(); };
  // After a skip the player sometimes lands paused (notably when skipping out of an ad),
  // so wait for the new media and make sure it is actually playing.
  const skip = async (sels) => {
    const before = status().title;
    clickFirst(sels);
    for (let i = 0; i < 20; i++) { await sleep(250); const s = status(); if (s.hasMedia && s.title !== before) break; }
    await sleep(500);
    const v = video();
    if (v && v.paused) await v.play().catch(() => {});
    return status();
  };
  const next = () => skip(['ytmusic-player-bar .next-button', 'ytmusic-player-bar [aria-label="Next"]']);
  const prev = () => skip(['ytmusic-player-bar .previous-button', 'ytmusic-player-bar [aria-label="Previous"]']);
  const volume = (x) => { const v = video(); if (v) { v.volume = Math.max(0, Math.min(1, x)); v.muted = false; } return status(); };
  return { play, pause, next, prev, volume, status, dismiss };
})()`;

export const ACTIONS = ['play', 'pause', 'next', 'prev', 'volume', 'status', 'dismiss', 'navigate'];

export async function action(app, cfg, log, name, value) {
  if (!ACTIONS.includes(name)) throw new Error(`music: unknown action "${name}" (${ACTIONS.join(', ')})`);
  const page = await connectPage(chrome.debugPort(app), PAGE_PREFIX);
  try {
    if (name === 'navigate') {
      if (!value) throw new Error('music navigate needs a url');
      await page.navigate(value);
      return { navigated: value };
    }
    if (!page.matched) {
      // The tab is somewhere else (login page, error page). Report rather than poke around.
      if (name === 'status') return { url: page.targetUrl, onYouTubeMusic: false };
      throw new Error(`YouTube Music tab not found; the page is at ${page.targetUrl}. Sign in / navigate there first (music navigate <url>).`);
    }
    const call = name === 'volume' ? `${PAGE_LIB}.volume(${Number(value)})` : `${PAGE_LIB}.${name}()`;
    const result = await page.evaluate(call);
    if (name === 'play' && result && result.hasMedia === false) {
      throw new Error(`play did not start any media (page: ${result.url}). Is the account signed in and the url a playable playlist/track?`);
    }
    if (name === 'play' && result && result.paused) log.warn('music: play was requested but the player reports paused');
    return result;
  } finally {
    page.close();
  }
}
