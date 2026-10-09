// Minimal Chrome DevTools Protocol client (no dependencies; Node >= 22 has WebSocket).
// Used to drive a Chrome we launched with --remote-debugging-port: evaluate JS in a
// page, navigate it, and close the browser cleanly.

const CALL_TIMEOUT_MS = 10000;

export async function listTargets(port) {
  const res = await fetch(`http://127.0.0.1:${port}/json`, { signal: AbortSignal.timeout(2000) });
  if (!res.ok) throw new Error(`CDP /json → HTTP ${res.status}`);
  return res.json();
}

export async function browserInfo(port) {
  const res = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(2000) });
  if (!res.ok) throw new Error(`CDP /json/version → HTTP ${res.status}`);
  return res.json();
}

class Session {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    ws.addEventListener('message', (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      const p = this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      clearTimeout(p.timer);
      if (msg.error) p.reject(new Error(`${p.method}: ${msg.error.message}`));
      else p.resolve(msg.result);
    });
    ws.addEventListener('close', () => {
      for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(new Error(`${p.method}: connection closed`)); }
      this.pending.clear();
    });
  }

  call(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`${method}: timed out`)); }, CALL_TIMEOUT_MS);
      this.pending.set(id, { resolve, reject, timer, method });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  // Evaluate an expression in the page; awaits promises; returns the JSON value.
  async evaluate(expression) {
    const r = await this.call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) {
      const d = r.exceptionDetails;
      throw new Error(`page script error: ${d.exception?.description ?? d.text}`);
    }
    return r.result?.value;
  }

  navigate(url) { return this.call('Page.navigate', { url }); }

  close() { try { this.ws.close(); } catch {} }
}

function open(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const timer = setTimeout(() => { ws.close(); reject(new Error('CDP websocket connect timed out')); }, 5000);
    ws.addEventListener('open', () => { clearTimeout(timer); resolve(new Session(ws)); });
    ws.addEventListener('error', () => { clearTimeout(timer); reject(new Error(`CDP websocket failed: ${wsUrl}`)); });
  });
}

// Connect to the page whose URL starts with urlPrefix; falls back to the first page target.
export async function connectPage(port, urlPrefix = null) {
  const targets = (await listTargets(port)).filter((t) => t.type === 'page');
  if (!targets.length) throw new Error('Chrome has no open page');
  const target = (urlPrefix && targets.find((t) => t.url.startsWith(urlPrefix))) ?? targets[0];
  const session = await open(target.webSocketDebuggerUrl);
  session.targetUrl = target.url;
  session.matched = !urlPrefix || target.url.startsWith(urlPrefix);
  return session;
}

export async function closeBrowser(port) {
  const { webSocketDebuggerUrl } = await browserInfo(port);
  const s = await open(webSocketDebuggerUrl);
  try { await s.call('Browser.close'); } finally { s.close(); }
}
