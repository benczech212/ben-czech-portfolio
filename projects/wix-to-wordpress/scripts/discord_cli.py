"""Project Discord helper — REST only, stdlib only.

Lets agents (and the developer) post updates, open questions for the site owner, track features,
and pull answers back into docs/discord/ without running a persistent bot.

Secrets: .secrets/discord.env  ->  DISCORD_BOT_TOKEN=...
Config:  infra/discord/server.json (guild/channel/tag/people ids, no secrets; written by the CLI)
Env:     SITE_URL (bot User-Agent), STAGING_URL (channel topic), DISCORD_PROJECT_CATEGORY

Usage:
  python scripts/discord_cli.py whoami                  # verify token, record guild id
  python scripts/discord_cli.py invite                  # print the bot invite URL
  python scripts/discord_cli.py setup                   # build public + private layout, roles, tags (idempotent)
  python scripts/discord_cli.py edit welcome - < text   # replace the bot's #welcome or #rules post
  python scripts/discord_cli.py members                 # list server members + ids
  python scripts/discord_cli.py person owner <user_id>  # map a person key to a Discord user
  python scripts/discord_cli.py post updates "text"     # post to a text channel by key
  python scripts/discord_cli.py ask "Title" "body" --for owner --tags Pricing Content [--quiet]
  python scripts/discord_cli.py feature "Title" "body" --status Idea --tags Owner Shop
  python scripts/discord_cli.py status <thread_id> "In Progress"
  python scripts/discord_cli.py pull                    # export forums -> docs/discord/*.md
"""
import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".secrets" / "discord.env"
CONFIG_FILE = ROOT / "infra" / "discord" / "server.json"
DOCS_DIR = ROOT / "docs" / "discord"
API = "https://discord.com/api/v10"
UA = f"DiscordBot ({os.environ.get('SITE_URL', 'https://example.com')}, 1.0)"
STAGING_URL = os.environ.get("STAGING_URL", "(staging URL not set)")

# Only what the commands below use:
#   View Channels, Send Messages, Read Message History      post, ask, feature, pull
#   Send Messages in Threads, Create Public Threads          forum posts and their follow-up chunks
#   Manage Threads                                           `status` retags posts other people opened
#   Manage Channels                                          `setup` creates, moves and renames channels
#   Manage Roles                                             `setup` creates the team role, assigns it, and
#                                                            writes channel permission overwrites
# Dropped from the original invite: Create Invite, Manage Messages, Embed Links, Attach Files, Add Reactions,
# and the applications.commands scope (the CLI registers no slash commands).
PERMISSIONS = 326686018576

VIEW, SEND, CREATE_THREADS = 1 << 10, 1 << 11, 1 << 35
TEAM_ROLE = "Studio Team"
TEXT, VOICE, FORUM = 0, 2, 15

# Server layout, top to bottom. Channel options: readonly (only the team posts; members can still reply
# in threads), tags (forum tags), adopt (name of an existing channel to move in), tracker (private forum).
PRIVATE_CATEGORY = os.environ.get("DISCORD_PROJECT_CATEGORY", "Site Project")
LAYOUT = [
    {"name": "Welcome", "channels": [
        {"key": "welcome", "type": TEXT, "readonly": True, "topic": "Start here: who we are and where to find things"},
        {"key": "rules", "type": TEXT, "readonly": True, "topic": "How we treat each other here"},
        {"key": "announcements", "type": TEXT, "readonly": True, "topic": "Studio news, new paints and releases"},
        {"key": "events", "type": TEXT, "readonly": True, "topic": "Upcoming workshops, Studio Hours and events"},
    ]},
    {"name": "Community", "channels": [
        {"key": "general", "type": TEXT, "adopt": "general", "topic": "Chat about anything creative"},
        {"key": "introductions", "type": TEXT, "topic": "Say hi: who you are and what you like to make"},
        {"key": "share-your-art", "type": FORUM, "topic": "One post per piece or series. Tag it so others can find it.",
         "tags": ["Watercolor", "Artist Trading Card", "Glow / UV", "Mixed Media", "Work in Progress", "Feedback Welcome"]},
        {"key": "ask-the-studio", "type": TEXT, "topic": "Questions about paints, workshops, orders or the website"},
    ]},
    {"name": "Artist Trading Cards", "channels": [
        {"key": "atc-chat", "type": TEXT, "topic": "Everything artist trading cards"},
        {"key": "weekly-challenge", "type": TEXT, "readonly": True,
         "topic": "A new prompt each week. Reply in the prompt's thread with your card."},
        {"key": "atc-swaps", "type": FORUM, "topic": "One post per swap: theme, deadline, how to join.",
         "tags": ["Open", "Full", "Closed", "Mail Swap", "In Person", "Tarot Deck"]},
    ]},
    {"name": "Studio Hours", "channels": [
        {"key": "studio-hours", "type": VOICE, "adopt": "General", "name": "Studio Hours"},
    ]},
    {"name": PRIVATE_CATEGORY, "private": True, "channels": [
        {"key": "updates", "type": TEXT, "topic": "Progress updates from the developer and the agents"},
        {"key": "agent-log", "type": TEXT, "topic": "Detailed automated agent activity (noisy)"},
        {"key": "wordpress-site", "type": TEXT,
         "topic": f"New site (staging): {STAGING_URL} — previews, feedback, build notes"},
        {"key": "questions", "type": FORUM, "tracker": True},
        {"key": "features", "type": FORUM, "tracker": True},
    ]},
]
DEFAULT_CATEGORIES = ["Text Channels", "Voice Channels"]  # removed once empty

# Tracker forums (private): status tags drive `ask`, `feature`, `status` and `pull`.
FORUMS = {
    "questions": {
        "topic": "One post per question. Reply in the thread; the Answered tag closes it out.",
        "status": ["Open", "Answered"],
        "tags": ["For Owner", "For Dev", "Urgent", "Offerings", "Pricing", "Content", "Legal", "Tech"],
    },
    "features": {
        "topic": "Feature & task tracker. One post per item; status lives in the tags.",
        "status": ["Idea", "Planned", "In Progress", "Review", "Done", "Blocked"],
        "tags": ["Owner", "Dev", "Shop", "Workshops", "Infra", "Content"],
    },
}


def load_token():
    if not ENV_FILE.exists():
        sys.exit(f"Missing {ENV_FILE.relative_to(ROOT)} (DISCORD_BOT_TOKEN=...)")
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("DISCORD_BOT_TOKEN not found in .secrets/discord.env")


TOKEN = None


def api(method, path, body=None, query=None):
    url = API + path + ("?" + urlencode(query) if query else "")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bot {TOKEN}", "User-Agent": UA}
    if data is not None:
        headers["Content-Type"] = "application/json"
    for _ in range(6):
        try:
            with urlopen(Request(url, data=data, method=method, headers=headers)) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except HTTPError as e:
            detail = e.read().decode(errors="replace")
            if e.code == 429:
                time.sleep(float(json.loads(detail).get("retry_after", 1)) + 0.2)
                continue
            sys.exit(f"Discord API {method} {path} -> {e.code}: {detail[:500]}")
    sys.exit(f"Discord API {method} {path}: rate limited too many times")


def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {"guild_id": None, "channels": {}, "tags": {}, "people": {}}


def save_config(cfg):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def need(cfg, key):
    if not cfg.get("guild_id") or (key and key not in cfg["channels"]):
        sys.exit("Run `whoami` and `setup` first.")
    return cfg["channels"][key] if key else cfg["guild_id"]


def chunks(text, size=1900):
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def mention(cfg, who):
    uid = cfg["people"].get(who) if who else None
    return (f"<@{uid}> ", [uid]) if uid else ("", [])


def tag_ids(cfg, forum, names):
    table = cfg["tags"][forum]
    missing = [n for n in names if n not in table]
    if missing:
        sys.exit(f"Unknown {forum} tags {missing}; valid: {sorted(table)}")
    return [table[n] for n in names]


# ---- commands -------------------------------------------------------------

def cmd_whoami(args, cfg):
    me = api("GET", "/users/@me")
    guilds = api("GET", "/users/@me/guilds")
    print(f"Bot: {me['username']} (id {me['id']})")
    for g in guilds:
        print(f"Guild: {g['name']} (id {g['id']})")
    if len(guilds) == 1 and cfg.get("guild_id") != guilds[0]["id"]:
        cfg["guild_id"] = guilds[0]["id"]
        save_config(cfg)
        print("Recorded guild id in infra/discord/server.json")
    elif not guilds:
        print("Bot is not in any server yet — use `invite`.")


def cmd_invite(args, cfg):
    app_id = base64.b64decode(TOKEN.split(".")[0] + "==").decode()
    print("https://discord.com/oauth2/authorize?" + urlencode(
        {"client_id": app_id, "scope": "bot", "permissions": PERMISSIONS}))


def ensure_team_role(gid, cfg):
    roles = api("GET", f"/guilds/{gid}/roles")
    role = next((r for r in roles if r["name"] == TEAM_ROLE), None) or api(
        "POST", f"/guilds/{gid}/roles",
        {"name": TEAM_ROLE, "color": 0x834575, "hoist": True, "mentionable": True, "permissions": "0"})
    cfg["team_role"] = role["id"]
    for who, uid in cfg["people"].items():
        api("PUT", f"/guilds/{gid}/members/{uid}/roles/{role['id']}")
    return role["id"]


def overwrites(gid, cfg, team, private=False, readonly=False):
    """Permission overwrites: private = team-only; readonly = everyone reads, team posts."""
    me = api("GET", "/users/@me")["id"]
    if private:
        return [{"id": gid, "type": 0, "allow": "0", "deny": str(VIEW)},
                {"id": team, "type": 0, "allow": str(VIEW), "deny": "0"},
                {"id": me, "type": 1, "allow": str(VIEW), "deny": "0"}]
    if readonly:
        deny = SEND | CREATE_THREADS
        return [{"id": gid, "type": 0, "allow": "0", "deny": str(deny)},
                {"id": team, "type": 0, "allow": str(deny), "deny": "0"},
                {"id": me, "type": 1, "allow": str(deny), "deny": "0"}]
    return []


def cmd_setup(args, cfg):
    gid = need(cfg, None)
    team = ensure_team_role(gid, cfg)
    existing = api("GET", f"/guilds/{gid}/channels")
    by_id = {c["id"]: c for c in existing}

    def find(name, ctype, parent=None):
        return next((c for c in existing if c["type"] == ctype and c["name"] == name
                     and (parent is None or c.get("parent_id") == parent)), None)

    positions = []
    for cpos, cat_spec in enumerate(LAYOUT):
        private = cat_spec.get("private", False)
        cat_ow = overwrites(gid, cfg, team, private=private)
        cat = find(cat_spec["name"], 4) or api("POST", f"/guilds/{gid}/channels",
                                                {"name": cat_spec["name"], "type": 4})
        api("PATCH", f"/channels/{cat['id']}", {"permission_overwrites": cat_ow})
        positions.append({"id": cat["id"], "position": cpos})
        for pos, spec in enumerate(cat_spec["channels"]):
            key, ctype = spec["key"], spec["type"]
            name = spec.get("name", key)
            ch = (cfg["channels"].get(key) and by_id.get(cfg["channels"][key])) \
                or find(name, ctype, cat["id"]) or (spec.get("adopt") and find(spec["adopt"], ctype))
            # Read-only still lets members reply inside threads the team starts (Send Messages in Threads).
            ow = cat_ow if private else overwrites(gid, cfg, team, readonly=spec.get("readonly", False))
            body = {"name": name, "parent_id": cat["id"], "permission_overwrites": ow, "position": pos}
            if ctype != VOICE and spec.get("topic"):
                body["topic"] = spec["topic"]
            tracker = FORUMS.get(key) if spec.get("tracker") else None
            if tracker:
                body["topic"] = tracker["topic"]
            tags = (tracker["status"] + tracker["tags"]) if tracker else spec.get("tags")
            if not ch:
                create = dict(body, type=ctype)
                if tags:
                    create["available_tags"] = [{"name": t} for t in tags]
                    create["default_sort_order"] = 0  # latest activity
                ch = api("POST", f"/guilds/{gid}/channels", create)
                print(f"created #{name}")
            elif tags:
                have = {t["name"] for t in ch.get("available_tags", [])}
                if set(tags) - have:
                    body["available_tags"] = ch.get("available_tags", []) + [{"name": t} for t in tags if t not in have]
            ch = api("PATCH", f"/channels/{ch['id']}", body)
            cfg["channels"][key] = ch["id"]
            if tracker:
                cfg["tags"][key] = {t["name"]: t["id"] for t in ch["available_tags"]}
            print(f"{cat_spec['name']} / {name}: {ch['id']}{' (private)' if private else ''}"
                  f"{' (read-only)' if spec.get('readonly') else ''}")
    api("PATCH", f"/guilds/{gid}/channels", positions)

    # Drop Discord's default categories once they're empty.
    current = api("GET", f"/guilds/{gid}/channels")
    for c in current:
        if c["type"] == 4 and c["name"] in DEFAULT_CATEGORIES and not any(x.get("parent_id") == c["id"] for x in current):
            api("DELETE", f"/channels/{c['id']}")
            print(f"removed empty category {c['name']}")
    save_config(cfg)


def cmd_members(args, cfg):
    gid = need(cfg, None)
    for m in api("GET", f"/guilds/{gid}/members", query={"limit": 1000}):
        u = m["user"]
        print(f"{u['id']}  {u['username']}  {u.get('global_name') or ''}{'  [bot]' if u.get('bot') else ''}")


def cmd_person(args, cfg):
    cfg["people"][args.key] = args.user_id
    save_config(cfg)
    print(f"{args.key} -> {args.user_id}")


def cmd_post(args, cfg):
    cid = need(cfg, args.channel)
    text = sys.stdin.read() if args.text == "-" else args.text
    for part in chunks(text):
        api("POST", f"/channels/{cid}/messages", {"content": part, "allowed_mentions": {"parse": []}})
    print(f"posted to #{args.channel}")


def cmd_edit(args, cfg):
    """Replace the text of a bot message recorded in server.json (e.g. welcome, rules)."""
    mid = cfg.get("messages", {}).get(args.message)
    if not mid:
        sys.exit(f"No recorded message '{args.message}'; known: {sorted(cfg.get('messages', {}))}")
    text = sys.stdin.read() if args.text == "-" else args.text
    api("PATCH", f"/channels/{cfg['channels'][args.message]}/messages/{mid}",
        {"content": text[:2000], "allowed_mentions": {"parse": []}})
    print(f"updated #{args.message} message")


def forum_post(cfg, forum, title, body, tags, who, quiet=False):
    fid = need(cfg, forum)
    prefix, users = mention(cfg, None if quiet else who)
    parts = chunks(prefix + body)
    thread = api("POST", f"/channels/{fid}/threads", {
        "name": title[:100],
        "applied_tags": tag_ids(cfg, forum, tags)[:5],  # Discord allows 5 tags per post
        "message": {"content": parts[0], "allowed_mentions": {"users": users}},
    })
    for part in parts[1:]:
        api("POST", f"/channels/{thread['id']}/messages", {"content": part, "allowed_mentions": {"parse": []}})
    print(f"{thread['id']}  https://discord.com/channels/{cfg['guild_id']}/{thread['id']}")


def cmd_ask(args, cfg):
    tags = ["Open"] + ([f"For {args.who.title()}"] if args.who else []) + args.tags
    forum_post(cfg, "questions", args.title, args.body, tags, args.who, args.quiet)


def cmd_feature(args, cfg):
    forum_post(cfg, "features", args.title, args.body, [args.status] + args.tags, None)


def cmd_status(args, cfg):
    thread = api("GET", f"/channels/{args.thread_id}")
    forum = next((k for k in FORUMS if cfg["channels"].get(k) == thread.get("parent_id")), None)
    if not forum:
        sys.exit("That thread is not in a tracked forum.")
    by_id = {v: k for k, v in cfg["tags"][forum].items()}
    keep = [t for t in thread.get("applied_tags", []) if by_id.get(t) not in FORUMS[forum]["status"]]
    new = tag_ids(cfg, forum, [args.status]) + keep
    api("PATCH", f"/channels/{args.thread_id}", {"applied_tags": new[:5]})
    print(f"{thread['name']}: -> {args.status}")


def forum_threads(cfg, forum):
    fid = cfg["channels"][forum]
    threads = [t for t in api("GET", f"/guilds/{cfg['guild_id']}/threads/active")["threads"]
               if t.get("parent_id") == fid]
    before = None
    while True:
        page = api("GET", f"/channels/{fid}/threads/archived/public",
                   query={"limit": 100, **({"before": before} if before else {})})
        threads += page["threads"]
        if not page.get("has_more") or not page["threads"]:
            break
        before = page["threads"][-1]["thread_metadata"]["archive_timestamp"]
    return threads


def thread_messages(tid):
    out, before = [], None
    while True:
        page = api("GET", f"/channels/{tid}/messages", query={"limit": 100, **({"before": before} if before else {})})
        out += page
        if len(page) < 100:
            break
        before = page[-1]["id"]
    return list(reversed(out))


def cmd_pull(args, cfg):
    need(cfg, "questions")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for forum, spec in FORUMS.items():
        by_id = {v: k for k, v in cfg["tags"][forum].items()}
        threads = sorted(forum_threads(cfg, forum), key=lambda t: int(t["id"]))
        lines = [f"# Discord #{forum}", "", f"_Pulled {time.strftime('%Y-%m-%d %H:%M')} — {len(threads)} posts._", ""]
        for t in threads:
            tags = [by_id.get(x, "?") for x in t.get("applied_tags", [])]
            status = next((x for x in tags if x in spec["status"]), "—")
            lines += [f"## {t['name']}", "",
                      f"- Status: **{status}**  Tags: {', '.join(x for x in tags if x != status) or '—'}",
                      f"- Link: https://discord.com/channels/{cfg['guild_id']}/{t['id']}", ""]
            for m in thread_messages(t["id"]):
                who = m["author"].get("global_name") or m["author"]["username"]
                body = (m.get("content") or "").strip() or "_(no text)_"
                files = "".join(f"\n  - attachment: {a['url']}" for a in m.get("attachments", []))
                lines.append(f"**{who}** ({m['timestamp'][:16].replace('T', ' ')}): " + body.replace("\n", "\n  ") + files)
                lines.append("")
        (DOCS_DIR / f"{forum}.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"docs/discord/{forum}.md: {len(threads)} posts")


def main():
    global TOKEN
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami")
    sub.add_parser("invite")
    sub.add_parser("setup")
    sub.add_parser("members")
    sp = sub.add_parser("person"); sp.add_argument("key"); sp.add_argument("user_id")
    sp = sub.add_parser("post"); sp.add_argument("channel"); sp.add_argument("text", help="text, or - for stdin")
    sp = sub.add_parser("ask"); sp.add_argument("title"); sp.add_argument("body")
    sp.add_argument("--for", dest="who", choices=["owner", "dev"]); sp.add_argument("--tags", nargs="*", default=[])
    sp.add_argument("--quiet", action="store_true", help="tag For Owner/Dev but don't @mention")
    sp = sub.add_parser("feature"); sp.add_argument("title"); sp.add_argument("body")
    sp.add_argument("--status", default="Idea", choices=FORUMS["features"]["status"])
    sp.add_argument("--tags", nargs="*", default=[])
    sp = sub.add_parser("status"); sp.add_argument("thread_id"); sp.add_argument("status")
    sub.add_parser("pull")
    sp = sub.add_parser("edit"); sp.add_argument("message", help="recorded message key, e.g. welcome"); sp.add_argument("text", help="text, or - for stdin")
    args = p.parse_args()
    TOKEN = load_token()
    globals()["cmd_" + args.cmd](args, load_config())


if __name__ == "__main__":
    main()
