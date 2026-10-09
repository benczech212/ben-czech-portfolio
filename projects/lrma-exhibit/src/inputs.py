#!/usr/bin/env python3
"""
USB controller bridge: Xbox controller (pygame/SDL) and 3Dconnexion SpaceMouse (pyspacemouse, raw HID)
-> OSC  /input/xbox/<control> <float>   and   /input/spacemouse/<control> <float>
to the control server's input port (exhibit.yaml inputs.osc_port). Devices may be absent; the bridge
keeps polling for them and reports presence every 2 s as /input/_devices/<source> <0|1> <name>.

    python inputs.py --host 127.0.0.1 --port 9004 --rate 60
Xbox:       lx ly rx ry (-1..1, up = +), lt rt (0..1), btn_a btn_b btn_x btn_y btn_lb btn_rb btn_back btn_start btn_ls btn_rs, dpad_x dpad_y
SpaceMouse: x y z roll pitch yaw (-1..1), btn_0 btn_1 ...   (falls back to reading it as an SDL joystick if HID access fails)
"""
import argparse, socket, struct, time, sys, os, re
for _s in (sys.stdout, sys.stderr):   # console-less / cp1252 consoles must never kill the bridge over a stray character
    try: _s.reconfigure(errors='replace')
    except Exception: pass

ap = argparse.ArgumentParser()
ap.add_argument('--host', default='127.0.0.1'); ap.add_argument('--port', type=int, default=9004)
ap.add_argument('--rate', type=float, default=60, help='max messages per control per second')
ap.add_argument('--deadzone', type=float, default=0.06)
ap.add_argument('--verbose', action='store_true')
ap.add_argument('--hidapi-dir', default=r'C:\dev\tools\hidapi', help='folder containing hidapi.dll (needed by pyspacemouse on Windows)')
ap.add_argument('--xbox-match', default=r'xbox|x-box|gamepad|controller|wireless', help='regex (case-insensitive) a joystick name must match to be treated as the Xbox pad')
ap.add_argument('--spacemouse-match', default=r'spacemouse|spacenavigator|3dconnexion|spacepilot', help='regex for joysticks that are really the SpaceMouse')
args = ap.parse_args()
if os.path.isdir(args.hidapi_dir):
    os.environ['PATH'] = args.hidapi_dir + os.pathsep + os.environ.get('PATH', '')
    try: os.add_dll_directory(args.hidapi_dir)
    except Exception: pass
XBOX_RE = re.compile(args.xbox_match, re.I); SM_RE = re.compile(args.spacemouse_match, re.I)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
def _pad(b): return b + b'\0' * ((4 - len(b) % 4) % 4)
def osc(address, *values):
    a = _pad(address.encode() + b'\0'); tags = b','; payload = b''
    for v in values:
        if isinstance(v, bool) or (isinstance(v, int) and not isinstance(v, float)): tags += b'i'; payload += struct.pack('>i', int(v))
        elif isinstance(v, float): tags += b'f'; payload += struct.pack('>f', v)
        else: tags += b's'; payload += _pad(str(v).encode() + b'\0')
    sock.sendto(a + _pad(tags + b'\0') + payload, (args.host, args.port))

last = {}          # address -> (value, time)
def emit(address, value, force=False):
    now = time.time(); prev = last.get(address)
    if not force and prev is not None:
        pv, pt = prev
        if pv == value and now - pt < 1.0: return               # unchanged: resend at most 1/s (keeps "engaged" alive while held)
        if pv != value and now - pt < 1.0 / args.rate: return
    last[address] = (value, now); osc(address, value)
    if args.verbose: print(address, value, flush=True)
def dz(v): return 0.0 if abs(v) < args.deadzone else round(float(v), 3)
def log(m): print(f"[inputs] {m}", flush=True)

# ── SDL joysticks (pygame): Xbox pad, and the SpaceMouse as a fallback ───────────────────────
try:
    os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
    import pygame; pygame.init(); pygame.joystick.init()
except Exception as e:
    pygame = None; log(f"pygame unavailable: {e}")
XBOX_BUTTONS = ['btn_a', 'btn_b', 'btn_x', 'btn_y', 'btn_lb', 'btn_rb', 'btn_back', 'btn_start', 'btn_ls', 'btn_rs']
joys = {'xbox': None, 'spacemouse': None}; joy_names = {}; joy_check = 0

def rescan_joysticks():
    global joy_check
    if pygame is None: return
    joy_check = time.time()
    try:
        pygame.joystick.quit(); pygame.joystick.init()
        found = {'xbox': None, 'spacemouse': None}
        for i in range(pygame.joystick.get_count()):
            j = pygame.joystick.Joystick(i); name = j.get_name() or ''
            if SM_RE.search(name): found['spacemouse'] = found['spacemouse'] or j
            elif XBOX_RE.search(name): found['xbox'] = found['xbox'] or j
            elif found['xbox'] is None and not SM_RE.search(name) and j.get_numaxes() >= 4 and j.get_numbuttons() >= 8:
                found['xbox'] = j     # unknown gamepad-shaped device: assume a pad
        for k in found:
            if (found[k] is not None) != (joys[k] is not None) or (found[k] and joys[k] and found[k].get_guid() != joys[k].get_guid()):
                joys[k] = found[k]
                if found[k]: found[k].init(); joy_names[k] = found[k].get_name(); log(f"{k} (SDL): '{joy_names[k]}' axes={found[k].get_numaxes()} buttons={found[k].get_numbuttons()}")
                else: joy_names.pop(k, None); log(f"{k} (SDL): gone")
    except Exception as e:
        log(f"joystick scan failed: {e}")

def poll_xbox():
    j = joys['xbox']
    if j is None: return
    try:
        ax = [j.get_axis(i) for i in range(j.get_numaxes())]
        for i, n in enumerate(['lx', 'ly', 'rx', 'ry', 'lt', 'rt']):    # SDL Xbox layout; triggers -1..1 -> 0..1
            if i < len(ax):
                v = ax[i]
                if n in ('lt', 'rt'): v = (v + 1) / 2
                elif n in ('ly', 'ry'): v = -v
                emit(f'/input/xbox/{n}', dz(v))
        for i in range(min(j.get_numbuttons(), len(XBOX_BUTTONS))): emit(f'/input/xbox/{XBOX_BUTTONS[i]}', int(j.get_button(i)))
        if j.get_numhats() > 0:
            hx, hy = j.get_hat(0); emit('/input/xbox/dpad_x', hx); emit('/input/xbox/dpad_y', hy)
    except Exception as e:
        log(f"xbox lost: {e}"); joys['xbox'] = None

# ── SpaceMouse via pyspacemouse (raw HID), SDL fallback ──────────────────────────────────────
try:
    import pyspacemouse
except Exception as e:
    pyspacemouse = None; log(f"pyspacemouse unavailable: {e}")
sm = None; sm_check = 0; sm_name = None; sm_via = None
SM_AXES = ('x', 'y', 'z', 'roll', 'pitch', 'yaw')

def open_spacemouse():
    global sm, sm_check, sm_name, sm_via
    sm_check = time.time()
    if pyspacemouse is not None:
        try:
            conv = getattr(getattr(pyspacemouse, 'AxisConvention', None), 'HID_Z_UP', None)
            dev = pyspacemouse.open(axis_convention=conv) if conv is not None else pyspacemouse.open()
            if dev:
                sm = dev if hasattr(dev, 'read') else pyspacemouse     # new API returns a device; old API reads via the module
                sm_name = getattr(dev, 'name', None) or getattr(dev, 'product_name', None) or 'SpaceMouse'; sm_via = 'hid'
                log(f"spacemouse (HID): {sm_name}"); return
        except Exception as e:
            log(f"spacemouse HID open failed: {e}")
    if joys['spacemouse'] is not None:
        sm = 'sdl'; sm_name = joy_names.get('spacemouse', 'SpaceMouse'); sm_via = 'sdl'; log(f"spacemouse (SDL fallback): {sm_name}")

def poll_spacemouse():
    global sm
    if sm is None: return
    try:
        if sm == 'sdl':
            j = joys['spacemouse']
            if j is None: sm = None; return
            ax = [j.get_axis(i) for i in range(min(6, j.get_numaxes()))]      # HID order Tx Ty Tz Rx Ry Rz
            vals = dict(zip(('x', 'y', 'z', 'pitch', 'roll', 'yaw'), ax))
            for n in SM_AXES: emit(f'/input/spacemouse/{n}', dz(vals.get(n, 0.0)))
            for i in range(j.get_numbuttons()): emit(f'/input/spacemouse/btn_{i}', int(j.get_button(i)))
            return
        s = sm.read()
        if s is None: return
        for n in SM_AXES: emit(f'/input/spacemouse/{n}', dz(getattr(s, n, 0.0)))
        for i, b in enumerate(getattr(s, 'buttons', []) or []): emit(f'/input/spacemouse/btn_{i}', int(b))
    except Exception as e:
        log(f"spacemouse lost: {e}"); sm = None

# ── main loop ────────────────────────────────────────────────────────────────────────────────
log(f"bridging Xbox + SpaceMouse -> osc://{args.host}:{args.port}/input/...")
interval = 1.0 / max(1.0, args.rate); last_status = 0
while True:
    now = time.time()
    if now - joy_check > 3: rescan_joysticks()
    if sm is None and now - sm_check > 3: open_spacemouse()
    if pygame is not None:
        try: pygame.event.pump()
        except Exception: pass
    poll_xbox(); poll_spacemouse()
    if now - last_status > 2:
        last_status = now
        osc('/input/_devices/xbox', int(joys['xbox'] is not None), joy_names.get('xbox', ''))
        osc('/input/_devices/spacemouse', int(sm is not None), f"{sm_name} ({sm_via})" if sm is not None else '')
    time.sleep(interval)
