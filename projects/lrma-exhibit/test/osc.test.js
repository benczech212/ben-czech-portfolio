import { test } from 'node:test';
import assert from 'node:assert/strict';
import { encodeMessage, decodePacket, send, listen, closeSender } from '../src/osc.js';

test('encode/decode roundtrip with auto-typed args', () => {
  const buf = encodeMessage('/playlist/select', ['Exhibit', 1, 0.5, true, false]);
  assert.equal(buf.length % 4, 0);
  const [msg] = [...decodePacket(buf)];
  assert.equal(msg.address, '/playlist/select');
  assert.equal(msg.args[0], 'Exhibit');
  assert.equal(msg.args[1], 1);
  assert.ok(Math.abs(msg.args[2] - 0.5) < 1e-6);
  assert.equal(msg.args[3], true);
  assert.equal(msg.args[4], false);
});

test('explicit type tags', () => {
  const buf = encodeMessage('/x', [{ f: 1 }, { i: 2.9 }, { s: 'a' }]);
  const tags = buf.toString('ascii', 4, 9);
  assert.equal(tags, ',fis\0');
  const [msg] = [...decodePacket(buf)];
  assert.deepEqual(msg.args, [1, 2, 'a']);
});

test('decodes bundles', () => {
  const m1 = encodeMessage('/a', [1]);
  const m2 = encodeMessage('/b', ['x']);
  const size = (b) => { const s = Buffer.alloc(4); s.writeInt32BE(b.length); return s; };
  const bundle = Buffer.concat([Buffer.from('#bundle\0'), Buffer.alloc(8), size(m1), m1, size(m2), m2]);
  const msgs = [...decodePacket(bundle)];
  assert.deepEqual(msgs.map((m) => m.address), ['/a', '/b']);
});

test('send reaches a local listener', async () => {
  const port = 39871;
  const em = listen(port, '127.0.0.1');
  await new Promise((r) => em.once('listening', r));
  const got = new Promise((r) => em.once('message', r));
  await send('127.0.0.1', port, '/ping', [42]);
  const msg = await got;
  em.close(); closeSender();
  assert.equal(msg.address, '/ping');
  assert.deepEqual(msg.args, [42]);
});
