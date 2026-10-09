/**
 * chat-post — lets an agent post summaries to a household Discord server, with guardrails.
 *
 *   node tools/chat-post.mjs <channel-key> (--text "..." | --file note.md | --file -) [--title "..."]
 *
 * Guardrails (the reason this exists instead of a raw webhook):
 *   - Only known channel keys; the agent can't pick an arbitrary channel id.
 *   - Refuses text that looks like a card/account number, SSN, or credential.
 *   - Mentions are disabled, so a post can never ping @everyone or a person.
 *   - Long posts are split on line boundaries under Discord's limit.
 *
 * Env: DISCORD_BOT_TOKEN, plus CHANNEL_<KEY> ids (e.g. CHANNEL_PRIORITIES). Load them from a .env that the
 * agent's settings.json denies it from reading.
 */
import fs from 'node:fs';

const CHANNELS = ['priorities', 'finances', 'events', 'plans', 'household', 'agent-log'];
const SENSITIVE = [
  /\b(?:\d[ -]?){13,19}\b/,                       // card / account numbers
  /\b\d{3}-\d{2}-\d{4}\b/,                         // SSN
  /(api[_-]?key|token|password|secret)\s*[:=]/i,   // credentials
];
const LIMIT = 1900;

function die(msg) { console.error(msg); process.exit(1); }

function parseArgs(argv) {
  const [key, ...rest] = argv;
  const opts = {};
  for (let i = 0; i < rest.length; i++) {
    if (rest[i].startsWith('--')) opts[rest[i].slice(2)] = rest[i + 1]?.startsWith('--') ? true : rest[++i];
  }
  return { key, opts };
}

function chunks(text) {
  const out = [];
  let cur = '';
  for (const line of text.split('\n')) {
    if ((cur + '\n' + line).length > LIMIT) { if (cur) out.push(cur); cur = line.slice(0, LIMIT); }
    else cur = cur ? cur + '\n' + line : line;
  }
  if (cur) out.push(cur);
  return out;
}

const { key, opts } = parseArgs(process.argv.slice(2));
if (!CHANNELS.includes(key)) die(`Unknown channel "${key}". Use one of: ${CHANNELS.join(', ')}`);

const token = process.env.DISCORD_BOT_TOKEN;
const channelId = process.env[`CHANNEL_${key.toUpperCase().replace(/-/g, '_')}`];
if (!token || !channelId) die('Set DISCORD_BOT_TOKEN and the CHANNEL_<KEY> id in the environment.');

let text = opts.text ?? (opts.file === '-' ? fs.readFileSync(0, 'utf8') : opts.file ? fs.readFileSync(opts.file, 'utf8') : '');
if (!text.trim()) die('Nothing to post.');
if (opts.title) text = `**${opts.title}**\n${text}`;
const hit = SENSITIVE.find(re => re.test(text));
if (hit) die(`Refused: the post matches a sensitive pattern (${hit}). Mask numbers like …1234 and remove credentials.`);

for (const content of chunks(text)) {
  const res = await fetch(`https://discord.com/api/v10/channels/${channelId}/messages`, {
    method: 'POST',
    headers: { Authorization: `Bot ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, allowed_mentions: { parse: [] } }),
  });
  if (!res.ok) die(`Discord returned ${res.status}: ${await res.text()}`);
}
console.log(`Posted to ${key}.`);
