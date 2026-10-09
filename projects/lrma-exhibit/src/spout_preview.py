#!/usr/bin/env python3
"""
Spout receiver service for the exhibit control panel.

Receives every Spout sender on the machine (or a configured list), measures each sender's
real output frame rate from the Spout frame counter, and serves:

    GET /senders                 JSON: [{name, width, height, fps, frames, connected, lastFrameAt}]
    GET /frame.jpg?sender=NAME   latest JPEG (downscaled) of that sender
    GET /stream?sender=NAME      MJPEG stream (multipart/x-mixed-replace)
    GET /health                  200 once the GL context is up

Needs (per-user Python): pip install SpoutGL PyOpenGL glfw numpy Pillow
Frame counting must be enabled in Spout (HKCU\\Software\\Leading Edge\\Spout\\Framecount = 1,
also set by SpoutSettings); senders read that setting when they start.

    python spout_preview.py --port 9877 --senders auto --fps 8 --width 640
"""
import argparse, io, json, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

ap = argparse.ArgumentParser()
ap.add_argument('--port', type=int, default=9877)
ap.add_argument('--host', default='127.0.0.1')
ap.add_argument('--senders', default='auto', help='comma-separated sender names, or "auto" for every sender present')
ap.add_argument('--fps', type=float, default=8, help='max JPEG frames per second per sender')
ap.add_argument('--width', type=int, default=640, help='JPEG width (height keeps aspect)')
ap.add_argument('--quality', type=int, default=70)
args = ap.parse_args()

state = {'ready': False, 'error': None, 'frameCount': None}
senders = {}     # name -> dict(receiver, tex, w, h, jpeg, fps, frames, lastFrameAt, connected, lock)
lock = threading.Lock()

def log(m):
    print(f"[spout_preview] {m}", flush=True)

# ─── capture thread ─────────────────────────────────────────────────────────────────

def capture_loop():
    try:
        import glfw, SpoutGL, numpy as np
        from OpenGL import GL
        from PIL import Image
    except ImportError as e:
        state['error'] = f"missing dependency: {e}"; log(state['error']); return
    if not glfw.init():
        state['error'] = 'glfw init failed'; log(state['error']); return
    glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
    win = glfw.create_window(64, 64, 'spout_preview', None, None)
    glfw.make_context_current(win)
    probe = SpoutGL.SpoutReceiver()
    state['frameCount'] = bool(probe.isFrameCountEnabled())
    state['ready'] = True
    log(f"GL context up; spout frame counting {'enabled' if state['frameCount'] else 'DISABLED (fps unavailable)'}")

    wanted = None if args.senders == 'auto' else [s.strip() for s in args.senders.split(',') if s.strip()]
    jpeg_interval = 1.0 / max(0.1, args.fps)
    last_list = 0

    # Content-change FPS fallback for senders without frame counting (Synesthesia): blit the shared
    # texture down to a 48x27 thumbnail each poll, hash it, count changes per second.
    THUMB_W, THUMB_H = 48, 27
    def make_thumb_fbo():
        t = int(GL.glGenTextures(1)); GL.glBindTexture(GL.GL_TEXTURE_2D, t)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, THUMB_W, THUMB_H, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR); GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        f = int(GL.glGenFramebuffers(1)); GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, f)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, t, 0); GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        return t, f
    src_fbo = int(GL.glGenFramebuffers(1))
    def thumb_hash(s):
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, src_fbo)
        GL.glFramebufferTexture2D(GL.GL_READ_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, s['tex'], 0)
        GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, s['thumbFbo'])
        GL.glBlitFramebuffer(0, 0, s['w'], s['h'], 0, 0, THUMB_W, THUMB_H, GL.GL_COLOR_BUFFER_BIT, GL.GL_LINEAR)
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, s['thumbFbo'])
        px = GL.glReadPixels(0, 0, THUMB_W, THUMB_H, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        return hash(bytes(px))

    while True:
        now = time.time()
        # (re)discover senders every 2 s
        if now - last_list > 2:
            last_list = now
            # getSenderList() is unreliable with some senders (empty even while Synesthesia is sending),
            # so also take the active sender and any configured names; "present" is decided by receiving.
            present = set()
            try: present.update(probe.getSenderList())
            except Exception: pass
            try:
                a = probe.getActiveSender()
                if a: present.add(a)
            except Exception: pass
            if wanted: present.update(wanted)
            names = sorted(present)
            for n in names:
                if n not in senders:
                    r = SpoutGL.SpoutReceiver(); r.setReceiverName(n)
                    tex = int(GL.glGenTextures(1))
                    ttex, tfbo = make_thumb_fbo()
                    with lock:
                        senders[n] = dict(receiver=r, tex=tex, w=0, h=0, jpeg=None, fps=None, fpsSource=None, frames=0, lastFrameAt=0,
                                          connected=False, present=n in present, f0=None, t0=now, lastJpegAt=0,
                                          thumbTex=ttex, thumbFbo=tfbo, lastHash=None, changes=0, changesT=now, counterMoved=False, createdAt=now)
                    log(f"receiving '{n}'")
                else:
                    senders[n]['present'] = n in present
            for n in list(senders):
                s = senders[n]
                gone_for = now - (s['lastFrameAt'] or s.get('createdAt', now))
                if not s['connected'] and gone_for > 15 and (wanted is None or n not in wanted):
                    with lock:
                        senders.pop(n)
                    try: s['receiver'].releaseReceiver()
                    except Exception: pass
                    log(f"sender '{n}' gone")

        for n, s in list(senders.items()):
            r = s['receiver']
            try:
                got = r.receiveTexture(s['tex'], GL.GL_TEXTURE_2D, False, 0)
            except Exception as e:
                got = False
            if not got:
                s['connected'] = False
                continue
            s['connected'] = True
            if r.isUpdated():
                s['w'], s['h'] = r.getSenderWidth(), r.getSenderHeight()
                GL.glBindTexture(GL.GL_TEXTURE_2D, s['tex'])
                GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, s['w'], s['h'], 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
                GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
                s['f0'] = None
            # 1) frame counter (Spout 2.007 senders such as Resolume) — sampled once a second
            if state['frameCount']:
                fc = r.getSenderFrame()
                if s['f0'] is None:
                    s['f0'], s['t0'] = fc, now
                elif now - s['t0'] >= 1.0:
                    delta = fc - s['f0']
                    if delta > 0:
                        s['counterMoved'] = True; s['frames'] = fc
                        s['fps'] = round(delta / (now - s['t0']), 1); s['fpsSource'] = 'counter'; s['lastFrameAt'] = now
                    s['f0'], s['t0'] = fc, now
            # 2) content changes (senders whose counter never moves, e.g. Synesthesia)
            new = False
            if s['w'] and s['h'] and not s['counterMoved']:
                try:
                    hsh = thumb_hash(s)
                    if hsh != s['lastHash']:
                        s['lastHash'] = hsh; s['changes'] += 1; new = True; s['lastFrameAt'] = now
                    if now - s['changesT'] >= 1.0:
                        s['fps'] = round(s['changes'] / (now - s['changesT']), 1); s['fpsSource'] = 'content'
                        s['changes'] = 0; s['changesT'] = now
                except Exception as e:
                    s['lastHash'] = None
            # JPEG readback at the configured rate
            if s['w'] and s['h'] and now - s['lastJpegAt'] >= jpeg_interval:
                s['lastJpegAt'] = now
                try:
                    GL.glBindTexture(GL.GL_TEXTURE_2D, s['tex'])
                    data = GL.glGetTexImage(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
                    GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
                    arr = np.frombuffer(data, dtype=np.uint8).reshape(s['h'], s['w'], 4)
                    img = Image.fromarray(arr, 'RGBA').convert('RGB')
                    if args.width and s['w'] > args.width:
                        img = img.resize((args.width, max(1, int(s['h'] * args.width / s['w']))), Image.BILINEAR)
                    buf = io.BytesIO(); img.save(buf, format='JPEG', quality=args.quality)
                    with lock:
                        s['jpeg'] = buf.getvalue()
                except Exception as e:
                    log(f"readback failed for '{n}': {e}")
        glfw.poll_events()
        time.sleep(0.002)

# ─── http ───────────────────────────────────────────────────────────────────────────

def sender_json():
    out = []
    with lock:
        for n, s in senders.items():
            out.append({'name': n, 'width': s['w'], 'height': s['h'], 'fps': s['fps'], 'fpsSource': s['fpsSource'], 'frames': s['frames'],
                        'connected': s['connected'], 'present': s.get('present', True), 'hasFrame': s['jpeg'] is not None,
                        'lastFrameAt': s['lastFrameAt']})
    return {'ready': state['ready'], 'error': state['error'], 'frameCountEnabled': state['frameCount'], 'senders': out}

class H(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def _hdr(self, code, ctype):
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path); q = parse_qs(u.query); name = (q.get('sender') or [None])[0]
        if u.path == '/health':
            self._hdr(200 if state['ready'] else 503, 'text/plain'); self.wfile.write(b'ok' if state['ready'] else b'starting'); return
        if u.path in ('/', '/senders', '/stats'):
            self._hdr(200, 'application/json'); self.wfile.write(json.dumps(sender_json()).encode()); return
        if u.path in ('/frame.jpg', '/stream'):
            with lock:
                if not name:
                    name = next((n for n, s in senders.items() if s['jpeg']), None)
                s = senders.get(name) if name else None
                jpeg = s['jpeg'] if s else None
            if not jpeg:
                self._hdr(404, 'text/plain'); self.wfile.write(b'no frame yet'); return
            if u.path == '/frame.jpg':
                self._hdr(200, 'image/jpeg'); self.wfile.write(jpeg); return
            self._hdr(200, 'multipart/x-mixed-replace; boundary=frame')
            try:
                last = None
                while True:
                    with lock:
                        cur = senders.get(name, {}).get('jpeg')
                    if cur and cur is not last:
                        last = cur
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(cur)).encode() + b'\r\n\r\n' + cur + b'\r\n'); self.wfile.flush()
                    time.sleep(1.0 / max(0.5, args.fps))
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
        self._hdr(404, 'text/plain'); self.wfile.write(b'not found')

# SpoutGL's sender enumeration only works from the thread that owns the process' first GL context,
# so capture runs on the main thread and the HTTP server on a daemon thread.
srv = ThreadingHTTPServer((args.host, args.port), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
log(f"serving http://{args.host}:{args.port}/  (senders: {args.senders}, jpeg {args.fps} fps @ {args.width}px)")
try:
    capture_loop()
except KeyboardInterrupt:
    pass
if state['error']:
    sys.exit(1)
