"""The console every node serves about itself.

Two lines in any Pi-class node and it hosts a page with its own health, what it is
subscribed to, the last messages in and out, and whatever buttons that device wants:

    from campbus import Node, console
    node = Node("crystal_03", fw="crystal-0.1", role="responder")
    console.serve(node, port=8102, actions={"Identify": lambda: flash_white()})
    node.connect()

The port is published in the node's retained `node/<id>/status` as `ui`, so the Devices
page (sim/nodes.html) can link straight to it without being told anything in advance.

Why it exists: at 2 a.m., in sand, on a phone, "that crystal is dark" and "that crystal
is online, hearing cmd/all/pulse, and its LED driver is the problem" are different
problems, and only one of them is worth waking someone up for. The page answers that
question and nothing else.

Deliberately: no CDN, no fonts, no framework, no build step. The camp LAN has no
internet, and a phone that loads a blank page is worse than no page at all. No auth
either, like everything else on this LAN - do not put it on one that is not closed.
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

Action = Callable[[], Any]

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>__TITLE__</title>
<style>
  :root {
    --ground:#0b0f12; --panel:#131a1f; --rule:#1f2a31; --ink:#e6edf1; --mute:#7d8f9a;
    --live:#4dffa6; --stale:#ffb300; --down:#ff5252; --act:#4de0ff;
    --mono: ui-monospace, "Cascadia Mono", "SF Mono", Consolas, monospace;
  }
  * { box-sizing:border-box; }
  body { margin:0 auto; background:var(--ground); color:var(--ink);
         font:15px/1.5 ui-sans-serif, system-ui, sans-serif; padding:16px 16px 48px; max-width:900px; }
  h1 { font-size:26px; letter-spacing:-.02em; margin:0; font-weight:650; }
  h2 { font-size:14px; font-weight:600; color:var(--ink); margin:0 0 10px; letter-spacing:-.01em; }
  .head { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:4px; }
  .role { color:var(--mute); font-size:14px; }
  .verdict { font-size:15px; margin:0 0 20px; color:var(--mute); }
  .verdict b { color:var(--ink); font-weight:600; }
  .lamp { width:11px; height:11px; border-radius:50%; display:inline-block; background:var(--down);
          box-shadow:0 0 10px currentColor; color:var(--down); flex:none; }
  .lamp.live { background:var(--live); color:var(--live); }
  .lamp.stale { background:var(--stale); color:var(--stale); }
  section { background:var(--panel); border:1px solid var(--rule); border-radius:12px; padding:14px 16px; margin-bottom:12px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px 20px; }
  .field .k { color:var(--mute); font-size:12px; }
  .field .v { font-family:var(--mono); font-size:16px; font-variant-numeric:tabular-nums; word-break:break-all; }
  ul.subs { list-style:none; margin:0; padding:0; font-family:var(--mono); font-size:13px; }
  ul.subs li { padding:3px 0; border-bottom:1px solid var(--rule); color:var(--act); }
  ul.subs li:last-child { border-bottom:0; }
  .acts { display:flex; gap:8px; flex-wrap:wrap; }
  button { font:inherit; font-size:14px; background:transparent; color:var(--act); border:1px solid var(--act);
           border-radius:8px; padding:7px 14px; cursor:pointer; }
  button:hover, button:focus-visible { background:var(--act); color:var(--ground); }
  button:disabled { opacity:.5; cursor:default; }
  .said { color:var(--mute); font-size:13px; min-height:1.4em; margin-top:8px; font-family:var(--mono); }
  table { width:100%; border-collapse:collapse; font-family:var(--mono); font-size:12.5px; }
  td { padding:4px 8px 4px 0; border-bottom:1px solid var(--rule); vertical-align:top; }
  td.dir { width:2.2em; color:var(--mute); }
  tr.out td.dir { color:var(--stale); }
  tr.in td.dir { color:var(--act); }
  td.top { width:15em; color:var(--ink); word-break:break-all; }
  td.pay { color:var(--mute); word-break:break-all; }
  td.at { width:5.5em; color:var(--mute); font-variant-numeric:tabular-nums; }
  .empty { color:var(--mute); font-size:13.5px; }
  @media (prefers-reduced-motion:no-preference) { .lamp { transition:background .3s, color .3s; } }
</style>
</head><body>
  <div class="head"><span class="lamp" id="lamp"></span><h1 id="id">__NODE__</h1><span class="role" id="role"></span></div>
  <p class="verdict" id="verdict">Reading&hellip;</p>

  <section>
    <h2>Health</h2>
    <div class="grid">
      <div class="field"><div class="k">Broker</div><div class="v" id="broker">&ndash;</div></div>
      <div class="field"><div class="k">Uptime</div><div class="v" id="uptime">&ndash;</div></div>
      <div class="field"><div class="k">Interactions</div><div class="v" id="count">&ndash;</div></div>
      <div class="field"><div class="k">Connects</div><div class="v" id="connects">&ndash;</div></div>
      <div class="field"><div class="k">Firmware</div><div class="v" id="fw">&ndash;</div></div>
      <div class="field"><div class="k">Address</div><div class="v" id="ip">&ndash;</div></div>
    </div>
  </section>

  <section>
    <h2>Listening to</h2>
    <ul class="subs" id="subs"></ul>
  </section>

  <section id="actions" hidden>
    <h2>Controls</h2>
    <div class="acts" id="acts"></div>
    <div class="said" id="said"></div>
  </section>

  <section>
    <h2>Traffic</h2>
    <table><tbody id="traffic"></tbody></table>
    <p class="empty" id="quiet" hidden>Nothing yet. Anything this node sends or hears shows up here.</p>
  </section>

<script>
const $ = (id) => document.getElementById(id);
const ago = (s) => s < 1 ? 'just now' : s < 60 ? Math.round(s) + 's ago' : s < 3600 ? Math.round(s / 60) + 'm ago' : Math.round(s / 3600) + 'h ago';
const dur = (s) => { s = Math.round(s); const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : m ? `${m}m ${s % 60}s` : `${s}s`; };
const clock = (t) => new Date(t * 1000).toLocaleTimeString();

async function tick() {
  let s;
  try { s = await (await fetch('api/state', { cache: 'no-store' })).json(); }
  catch (e) { $('verdict').innerHTML = 'This page cannot reach the node it belongs to. The process is probably gone.'; $('lamp').className = 'lamp'; return; }
  $('id').textContent = s.node_id;
  $('role').textContent = s.role || '';
  $('lamp').className = 'lamp ' + (s.connected ? 'live' : 'down');
  $('verdict').innerHTML = s.connected
    ? `Connected to the bus, <b>${dur(s.uptime)}</b> up, last heard traffic ${s.last_traffic == null ? '<b>never</b>' : '<b>' + ago(s.now - s.last_traffic) + '</b>'}.`
    : `<b>Not connected to the broker.</b> The node is running, so this is the bus or the network, not the device.`;
  $('broker').textContent = s.broker;
  $('uptime').textContent = dur(s.uptime);
  $('count').textContent = s.interactions.toLocaleString();
  $('connects').textContent = s.connects;
  $('fw').textContent = s.fw;
  $('ip').textContent = s.ip;
  $('subs').innerHTML = s.subscriptions.length
    ? s.subscriptions.map((t) => `<li>${t}</li>`).join('')
    : '<li class="empty">Nothing. This node only publishes.</li>';
  const rows = s.recent.slice().reverse();
  $('quiet').hidden = rows.length > 0;
  $('traffic').innerHTML = rows.map((r) => `<tr class="${r[1]}"><td class="at">${clock(r[0])}</td><td class="dir">${r[1] === 'out' ? 'out' : 'in'}</td>`
    + `<td class="top">${r[2]}</td><td class="pay">${JSON.stringify(r[3])}</td></tr>`).join('');
}

async function run(name, btn) {
  btn.disabled = true; $('said').textContent = name + '…';
  try {
    const r = await (await fetch('api/action?name=' + encodeURIComponent(name), { method: 'POST' })).json();
    $('said').textContent = r.ok ? (r.said || name + ' done') : ('failed: ' + r.error);
  } catch (e) { $('said').textContent = 'failed: ' + e.message; }
  btn.disabled = false; tick();
}

(async () => {
  const s = await (await fetch('api/state', { cache: 'no-store' })).json();
  if (s.actions.length) {
    $('actions').hidden = false;
    $('acts').innerHTML = s.actions.map((a) => `<button type="button">${a}</button>`).join('');
    [...$('acts').children].forEach((b) => b.addEventListener('click', () => run(b.textContent, b)));
  }
  tick(); setInterval(tick, 1000);
})();
</script>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    node = None           # set per-server below
    actions: Dict[str, Action] = {}
    title = "node"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:          # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/":
            page = PAGE.replace("__TITLE__", self.title).replace("__NODE__", self.node.node_id)
            self._send(200, page.encode(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(self._state())
        else:
            self._json({"error": "no such thing here"}, 404)

    def do_POST(self) -> None:         # noqa: N802
        u = urlparse(self.path)
        if u.path.rstrip("/") != "/api/action":
            self._json({"error": "no such thing here"}, 404)
            return
        name = (parse_qs(u.query).get("name") or [""])[0]
        fn = self.actions.get(name)
        if fn is None:
            self._json({"ok": False, "error": f"no control called {name!r}"}, 404)
            return
        try:
            said = fn()
        except Exception as e:                      # a broken control must not take the console down
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            return
        self._json({"ok": True, "said": None if said is None else str(said)})

    def _state(self) -> dict:
        n = self.node
        with n._lock:
            recent = [(ts, d, t, p) for ts, d, t, p in n.recent]
        return {
            "node_id": n.node_id, "role": n.role, "fw": n.fw, "ip": n._status_payload()["ip"],
            "connected": n.connected, "connects": n.connects,
            "broker": f"{n.host}:{n.port}",
            "uptime": time.monotonic() - n._started,
            "interactions": n._interactions,
            "subscriptions": n.subscriptions,
            "actions": list(self.actions),
            "recent": recent[-60:],
            "last_traffic": recent[-1][0] if recent else None,
            "now": time.time(),
        }

    def log_message(self, fmt: str, *args: Any) -> None:
        pass                                        # the node's own stdout is the log that matters


def serve(node, port: int = 8080, actions: Optional[Dict[str, Action]] = None,
          title: Optional[str] = None, host: str = "0.0.0.0",
          advertise: Optional[str] = None) -> ThreadingHTTPServer:
    """Start this node's console in a background thread and advertise it as `ui`.

    `actions` are the buttons: name -> callable. Return a short string and the page shows it.
    `advertise` overrides what goes on the bus - pass a full URL when the reader cannot reach
    you at your own IP (a container with a published port, say). `$CAMPBUS_UI_HOST` does the
    same for every node in a process: with it set, nodes advertise http://<that host>:<port>/.
    """
    handler = type("_NodeConsole", (_Handler,), {
        "node": node, "actions": dict(actions or {}), "title": title or f"{node.node_id} console",
    })
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name=f"console:{node.node_id}", daemon=True).start()

    ui_host = os.environ.get("CAMPBUS_UI_HOST")
    node.console_url = advertise if advertise else (f"http://{ui_host}:{httpd.server_port}/" if ui_host else httpd.server_port)
    node.publish_status()                           # if we are already connected, say so now
    return httpd


__all__ = ["serve"]
