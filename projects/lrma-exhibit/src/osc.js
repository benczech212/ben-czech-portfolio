// Minimal dependency-free OSC 1.0: encode messages, decode messages + bundles.
// The decoder is carried over from the v1 server (Synesthesia sends bundles).
import { createSocket } from 'node:dgram';
import { EventEmitter } from 'node:events';

// ─── Encoding ─────────────────────────────────────────────────────────────────

function padString(s) {
  const raw = Buffer.from(String(s) + '\0', 'utf8');
  const padLen = (4 - (raw.length % 4)) % 4;
  return padLen ? Buffer.concat([raw, Buffer.alloc(padLen)]) : raw;
}

// Args may be plain JS values (int → i, other number → f, string → s, bool → T/F)
// or explicitly typed as { i: 1 }, { f: 0.5 }, { s: "x" } for control over the tag.
function encodeArg(arg) {
  if (arg && typeof arg === 'object' && !Array.isArray(arg)) {
    const [tag, value] = Object.entries(arg)[0];
    if (tag === 'i') { const b = Buffer.alloc(4); b.writeInt32BE(Math.trunc(value)); return ['i', b]; }
    if (tag === 'f') { const b = Buffer.alloc(4); b.writeFloatBE(Number(value));  return ['f', b]; }
    if (tag === 'd') { const b = Buffer.alloc(8); b.writeDoubleBE(Number(value)); return ['d', b]; }
    if (tag === 's') return ['s', padString(value)];
    throw new Error(`Unsupported OSC arg type "${tag}"`);
  }
  if (typeof arg === 'boolean') return [arg ? 'T' : 'F', Buffer.alloc(0)];
  if (typeof arg === 'number') {
    if (Number.isInteger(arg)) { const b = Buffer.alloc(4); b.writeInt32BE(arg); return ['i', b]; }
    const b = Buffer.alloc(4); b.writeFloatBE(arg); return ['f', b];
  }
  if (arg === null || arg === undefined) return ['N', Buffer.alloc(0)];
  return ['s', padString(arg)];
}

export function encodeMessage(address, args = []) {
  if (!address.startsWith('/')) throw new Error(`OSC address must start with "/": ${address}`);
  const parts = [padString(address)];
  let tags = ',';
  const payload = [];
  for (const a of args) {
    const [tag, buf] = encodeArg(a);
    tags += tag;
    payload.push(buf);
  }
  parts.push(padString(tags), ...payload);
  return Buffer.concat(parts);
}

// ─── Decoding ─────────────────────────────────────────────────────────────────

function readString(buf, offset) {
  const end = buf.indexOf(0, offset);
  const str = buf.toString('utf8', offset, end < 0 ? buf.length : end);
  const next = (end < 0 ? buf.length : end) + 1;
  return [str, next + ((4 - next % 4) % 4)];
}

function parseMessage(buf) {
  try {
    const [address, off1] = readString(buf, 0);
    const args = [];
    if (off1 >= buf.length) return { address, args };
    const [typeStr, off2] = readString(buf, off1);
    if (!typeStr.startsWith(',')) return { address, args };
    const types = typeStr.slice(1);   // e.g. "f", "i", "s" — lets consumers tell a float slider from an int toggle
    let pos = off2;
    for (const tag of typeStr.slice(1)) {
      if      (tag === 'i') { args.push(buf.readInt32BE(pos));  pos += 4; }
      else if (tag === 'f') { args.push(buf.readFloatBE(pos));  pos += 4; }
      else if (tag === 'd') { args.push(buf.readDoubleBE(pos)); pos += 8; }
      else if (tag === 's') { const [s, n] = readString(buf, pos); args.push(s); pos = n; }
      else if (tag === 'T') args.push(true);
      else if (tag === 'F') args.push(false);
      else if (tag === 'N') args.push(null);
      else if (tag === 'h') pos += 8;
      else                  pos += 4;
    }
    return { address, args, types };
  } catch { return null; }
}

export function* decodePacket(buf) {
  if (buf.length >= 16 && buf.toString('ascii', 0, 7) === '#bundle') {
    let offset = 16;
    while (offset + 4 <= buf.length) {
      const size = buf.readInt32BE(offset);
      offset += 4;
      if (size <= 0 || offset + size > buf.length) break;
      yield* decodePacket(buf.subarray(offset, offset + size));
      offset += size;
    }
  } else {
    const msg = parseMessage(buf);
    if (msg) yield msg;
  }
}

// ─── Sending ──────────────────────────────────────────────────────────────────

let sendSock = null;

export function send(host, port, address, args = []) {
  if (!sendSock) { sendSock = createSocket('udp4'); sendSock.unref(); }
  const buf = encodeMessage(address, args);
  return new Promise((resolve, reject) => {
    sendSock.send(buf, 0, buf.length, port, host, (err) => (err ? reject(err) : resolve()));
  });
}

export function closeSender() {
  if (sendSock) { try { sendSock.close(); } catch {} sendSock = null; }
}

// ─── Listening ────────────────────────────────────────────────────────────────

// Returns an EventEmitter that emits 'message' ({address, args}) and 'error'.
// Call .close() when done. `allow` (optional array of source IPs) drops packets from any
// other sender; omit it to accept everything (the old behaviour).
export function listen(port, host = '0.0.0.0', { allow = null } = {}) {
  const em = new EventEmitter();
  const sock = createSocket({ type: 'udp4', reuseAddr: true });
  const allowed = allow ? new Set(allow) : null;
  sock.on('error', (err) => em.emit('error', err));
  sock.on('message', (buf, rinfo) => {
    if (allowed && !allowed.has(rinfo.address)) { em.emit('rejected', rinfo); return; }
    for (const msg of decodePacket(buf)) em.emit('message', msg);
  });
  sock.bind(port, host, () => em.emit('listening'));
  em.close = () => { try { sock.close(); } catch {} };
  return em;
}

// Resolve with the first message whose address matches `match` (string prefix or RegExp),
// or reject after timeoutMs. Used as an "app is alive" readiness signal.
export function waitForMessage(port, { match = null, timeoutMs = 30000 } = {}) {
  return new Promise((resolve, reject) => {
    const em = listen(port);
    const timer = setTimeout(() => { em.close(); reject(new Error(`No OSC received on port ${port} within ${timeoutMs / 1000}s`)); }, timeoutMs);
    em.on('error', (err) => { clearTimeout(timer); em.close(); reject(new Error(`Cannot listen on UDP ${port}: ${err.message}`)); });
    em.on('message', (msg) => {
      const ok = !match || (match instanceof RegExp ? match.test(msg.address) : msg.address.startsWith(match));
      if (ok) { clearTimeout(timer); em.close(); resolve(msg); }
    });
  });
}
