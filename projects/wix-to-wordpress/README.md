Part of Ben Czech's portfolio. Built with Claude Code.

# Wix to WordPress

Tooling Ben built to move Czech Studios, his wife's art business, from Wix to a self-hosted WordPress +
Elementor site. It covers the full path: a read-only export of the Wix site, a small GCP staging server,
a custom Elementor widget plugin, and scripts that build draft pages and import media from content files.

This is a public copy. Site content, the Wix export, audit notes, planning documents and all credentials
are left out. Site names, domains and cloud identifiers are replaced by environment settings (see
[Configuration](#configuration)).

## The lesson

**The problem.** A site migration means pointing scripts, and the coding agents that run them, at two
live systems: the old site, full of customer records, and the new one, where any page you save could
go live. The fastest way to work is to let the agent run those scripts freely. The worst mistakes are
writing to the old site, copying customer data into your working files, or publishing a half-built page.

**The idea.** Make each script able to do only its one job. The exporter can only read, and it saves
customer data as counts and schemas, never as records. The builder only creates drafts, and it has a
dry run. Publishing stays a human click in WordPress. Then you can let an agent run the pipeline over
and over, because none of its steps can do the dangerous thing.

### Walkthrough

1. **The exporter refuses to write, in code.** Open [`scripts/wix_export.py`](scripts/wix_export.py),
   find `Api.call`. Every request goes through it, and anything that isn't a GET, or a POST to a
   read-shaped endpoint, is stopped before the network:

   ```python
   READ_POST = re.compile(r"/(query|search|count|query-variants)$")
   ...
       def call(self, method: str, path: str, body: dict | None = None, params=None):
           if method == "POST":
               if not READ_POST.search(path.split("?")[0]):   # raise, not assert: python -O skips asserts
                   raise ValueError(f"refusing non-read POST {path}")
           elif method != "GET":
               raise ValueError("read-only exporter")
   ```

   Notice the guard sits on the one choke point, not on each caller, and that it raises instead of using
   `assert`: Python skips asserts under `python -O`, and a guard that can be switched off isn't a guard.
2. **Personal data is dropped by key, recursively.** In the same file, `strip_pii` removes any key that
   looks like an email, phone or address field at every depth. The higher-risk collections (contacts,
   orders, bookings, form submissions) never get that far: they're saved as counts, schemas and date
   ranges only (see the module docstring).
3. **The builder can't publish pages.** In [`scripts/wp_build.py`](scripts/wp_build.py), `build_page`
   creates new pages with `status: draft` and sends no status when it updates an existing page, so a
   page someone already published stays as it is:

   ```python
   if existing:
       status, res = wp.post(f"/pages/{existing['id']}", body)  # status untouched
   else:
       body["status"] = "draft"
       status, res = wp.post("/pages", body)
   ```

   Pages are matched by slug, so re-running never makes duplicates. (The "People" bio records are set
   to `publish`, but that post type isn't public; the comment in `upsert_people` explains why.)
4. **Dry run comes first.** `--dry-run` prints the Elementor JSON and returns before `WP()` is created,
   so it needs no credentials and can't touch the site. That's the step to run, and to have an agent
   run, before every real build.
5. **Remote commands are quoted, and secrets stay in files.** [`scripts/wp_rest.py`](scripts/wp_rest.py)
   reads credentials from `.secrets/` and never prints them. `vm_wp()` runs every wp-cli argument
   through `shlex.quote` before it goes over ssh. See [Security notes](#security-notes) for the server side.

### Try it

No Wix, WordPress or GCP account is needed. From this folder, with `pip install requests pyyaml pillow`:

1. Dry-run a page build. `content/` is gitignored, so make a one-section page:

   ```bash
   mkdir -p content/pages
   printf 'slug: demo\ntitle: Demo page\nsections:\n  - widget: czs-hero\n    heading: Hello\n' > content/pages/demo.yaml
   python scripts/wp_build.py --dry-run demo
   ```

   You get the page's Elementor JSON, and no network call is made.
2. Watch the exporter's guards. It loads `.secrets/wix.env` on import, so give it fake values (the
   folder is gitignored), then call the guard and the PII filter directly:

   ```bash
   mkdir -p .secrets && printf 'WIX_API_KEY=fake\nWIX_SITE_ID=fake\n' > .secrets/wix.env
   cd scripts && python -c "
   import wix_export as w
   print(w.strip_pii({'name': 'Ada', 'email': 'a@example.com', 'bio': 'hi'}))
   for m, p in [('DELETE', '/stores/v1/products/1'), ('POST', '/contacts/v4/contacts')]:
       try: w.Api(None).call(m, p)
       except ValueError as e: print(type(e).__name__, e)
   "
   ```

   Expected: `{'name': 'Ada', 'bio': 'hi'}`, then `ValueError read-only exporter` and
   `ValueError refusing non-read POST /contacts/v4/contacts`. Both requests are refused before
   anything is sent.
3. Delete `content/pages/demo.yaml` and `.secrets/wix.env` when you're done.

### Take it to your own project

- Put the safety rule where every request has to pass: one client method, not a reminder in a prompt.
- Make the risky last step (publish, send, delete) something your scripts can't do, and leave it to a person.
- Export the shape of customer data (counts, schemas, date ranges), not the records, unless you truly need them.
- Give every write script an idempotent key (here, the slug) and a `--dry-run` that runs before credentials load.
- Use `raise`, not `assert`, for checks that must hold in production.

## What's here

| Path | What |
|---|---|
| `wordpress/plugins/czs-blocks/` | WordPress plugin: 9 Elementor widgets, a "People" bio record type, SVG sanitizing, WebP output, brand kit WP-CLI command |
| `scripts/wix_export.py` | Read-only export of a Wix site through the Wix REST API |
| `scripts/wp_build.py` | Builds WordPress pages (Elementor JSON) from YAML page specs |
| `scripts/wp_media.py` | Downloads the images a page spec uses, converts them to WebP and uploads them once |
| `scripts/wp_rest.py` | Small shared WordPress REST client (stdlib only) plus `vm_wp()` for wp-cli over IAP ssh |
| `scripts/discord_cli.py` | Discord helper for a project server: updates, questions, a feature tracker, and pulling answers back to markdown |
| `infra/gcp/` | Provisioning for the staging VM (`provision.sh`), its startup script, and shared settings (`config.sh`) |
| `infra/wordpress/` | Docker Compose stack (Caddy, PHP-FPM WordPress, MariaDB, wp-cli), low-memory tuning, one-time setup, plugin deploy |
| `docs/plugins/README.md` | Architecture design for three further plugins (shared core, ATC community portal, memberships and courses). Design only; not built |

## czs-blocks (the Elementor plugin)

- **9 widgets** in their own Elementor category: Hero, Split (image + text), Card Grid, Offering Card, People,
  CTA Band, Quote, Latest Posts and Event List. They share a base class and one stylesheet, which loads only
  on pages that use one of the widgets.
- **People records**: one bio per person (name, bio, photo, role, links), reused by the People widget on any page.
- **SVG uploads for administrators only**, and every SVG goes through an allow-list sanitizer before WordPress
  stores it. The XML is parsed with `LIBXML_NONET`, so it can't fetch external entities.
- **WebP output**: JPEG and PNG uploads get WebP sub-sizes (quality 82) when the server's image editor supports it.
- **WP-CLI**: `wp czs-blocks apply-kit` writes the brand colors and fonts into Elementor's Site Settings
  (safe to re-run; colors the site owner added are kept). `wp czs-blocks status` reports state.
- Requires WordPress 6.5+ and PHP 8.3. No build step: plain PHP and CSS.

The plugin slug, PHP namespace (`CzechStudios\Blocks`) and widget names are kept as written.

## The migration pipeline

1. **Export (read-only).** `wix_export.py` reads a Wix site through the REST API. The code refuses every
   non-GET request except POSTs to `query`, `search` and `count` endpoints, so it can't create, change or
   delete anything. Contacts, members, orders, bookings, event RSVPs and form submissions are reduced to
   counts, schemas and date ranges. Public-profile collections are kept with email, phone and address
   fields stripped.
2. **Staging server.** `infra/gcp/provision.sh` creates an e2-micro VM with a 30 GB disk, a static IP, web
   firewall rules, SSH allowed only from Google's IAP range (35.235.240.0/20), and a weekly disk snapshot
   kept for 28 days. `infra/wordpress/` runs WordPress behind Caddy (automatic HTTPS) with memory limits
   sized for 1 GB of RAM. Setup sets `blog_public` to 0 so staging isn't indexed.
3. **Media.** `wp_media.py` collects image references from the page specs, downloads the originals,
   converts them to WebP (EXIF/GPS stripped, ICC profile kept, SVG kept as SVG), uploads each once and
   records it in a manifest. Re-runs skip what's already uploaded.
4. **Pages.** `wp_build.py` turns YAML page specs into Elementor JSON. Pages are matched by slug and updated
   in place, so re-runs never duplicate. New pages are created as **drafts**; an existing page keeps its
   status, and the build never publishes a page. `--dry-run` prints the Elementor JSON and changes nothing.
5. **Deploy the plugin.** `infra/wordpress/deploy-plugin.sh` zips the plugin, copies it to the VM over IAP,
   runs `php -l` on every PHP file inside the wp-cli container (and stops on a syntax error), installs and
   activates it, flushes Elementor's CSS, and warns if the site would be indexable.

The page specs and the media manifest (`content/`) are site content and are not included.

## Discord (human in the loop)

`discord_cli.py` lets the developer and coding agents work with the site owner without a running bot:
post updates, open a question as a forum post, track features by status tag, and `pull` every question
and feature thread back into markdown so answers land next to the code. Posts are sent with mentions
disabled. The one exception: `ask --for <person>` pings that one person, and `--quiet` turns that off too.

The bot asks only for the permissions the commands use: View Channels, Send Messages, Read Message History,
Send Messages in Threads, Create Public Threads, Manage Threads (retagging posts other people opened),
Manage Channels (`setup` builds the channel layout) and Manage Roles (`setup` creates the team role and
writes channel permission overwrites). The original invite also requested Create Invite, Manage Messages,
Embed Links, Attach Files, Add Reactions and the `applications.commands` scope; those were dropped.

## Setup

- Python 3.10+ with `requests`, `PyYAML` and `Pillow` (`pip install requests pyyaml pillow`).
- The Google Cloud SDK (`gcloud`) for the VM scripts, and bash (Git Bash works on Windows).
- Secrets live in a gitignored `.secrets/` folder at the project root: `wordpress.env` (`WP_URL`,
  `WP_APP_USER`, `WP_APP_PASSWORD`), `wix.env` (`WIX_API_KEY`, `WIX_SITE_ID`) and `discord.env`
  (`DISCORD_BOT_TOKEN`). The scripts read them and never print them.
- On the VM: copy `infra/wordpress/.env.example` to `/opt/wordpress/.env`, fill it in, `chmod 600` it,
  start the stack, then run `wp-setup.sh` once over ssh.

## Configuration

| Variable | Used by | Default | Meaning |
|---|---|---|---|
| `GCP_PROJECT` | `infra/gcp/*.sh`, `deploy-plugin.sh`, `wp_rest.vm_wp()` | none (required) | GCP project id |
| `GCP_ACCOUNT` | same | gcloud's active account | gcloud account to run as |
| `GCP_REGION` / `GCP_ZONE` | same (`vm_wp()` reads only the zone) | `us-east1` / `us-east1-b` | Where the VM lives |
| `GCP_VM` / `GCP_IP_NAME` | same (`vm_wp()` reads only the VM) | `cs-wordpress` / `cs-wordpress-ip` | VM and static-IP names |
| `WIX_SITE_URL` | `wp_media.py` | none | Public Wix site URL, for `wixdoc:` file references (env or `.secrets/wordpress.env`) |
| `WIX_SITE_SLUG` | `wix_export.py` | `live-site` | Output folder name for the live site's export |
| `SITE_URL` | `discord_cli.py` | `https://example.com` | URL in the bot's User-Agent (Discord asks for one) |
| `STAGING_URL` | `discord_cli.py` | placeholder text | Staging link shown in the site channel's topic |
| `DISCORD_PROJECT_CATEGORY` | `discord_cli.py` | `Site Project` | Name of the private project category |

VM `.env` keys (`infra/wordpress/.env.example`, all empty here): `SITE_HOST`, `SITE_TITLE`,
`MARIADB_ROOT_PASSWORD`, `MARIADB_PASSWORD`, `WORDPRESS_DB_PASSWORD`, `WP_ADMIN_USER`, `WP_ADMIN_PASSWORD`,
`WP_ADMIN_EMAIL`.

## Security notes

- **No SSH to the VM except through IAP.** Port 22 is open only to Google's IAP range; all scripts use
  `gcloud compute ssh/scp --tunnel-through-iap`.
- **SSH host keys are checked.** The scripts don't pass `--strict-host-key-checking=no`. The VM is created with
  `enable-guest-attributes=TRUE`, so gcloud reads the VM's host keys from guest attributes and pins them in
  `~/.ssh/google_compute_known_hosts` on the first connection, then refuses to connect if they change. On a
  VM created without guest attributes, gcloud trusts the key on first use; check it once against the serial
  console output (`gcloud compute instances get-serial-port-output`) if that matters to you.
- **Remote commands are quoted.** `vm_wp()` passes every wp-cli argument through `shlex.quote` before it joins
  the remote command line. `deploy-plugin.sh` accepts only plugin slugs matching `^[a-z0-9-]+$`, since the
  slug ends up in paths and in the remote script.
- **No secrets on command lines.** `wp-setup.sh` waits for MariaDB using `MYSQL_PWD`, read inside the db
  container, instead of `-p<password>`, and hands the WordPress admin password to `wp core install` on
  stdin (`--prompt=admin_password`) instead of as an argument.
- **The agents' application password isn't left on disk.** `wp-setup.sh` prints it once in the ssh session for
  you to copy into your local `.secrets/wordpress.env`; it isn't written to a file on the VM. To replace it,
  delete it with `wp user application-password delete` and re-run the setup.
- **Docker comes from Docker's signed apt repository**, not a piped install script.
- **Read-only where it can be.** The Wix exporter can't write. The page builder makes drafts only.
- **Known limitation:** the Compose stack gives every container the same `.env` file, so the WordPress,
  Caddy and wp-cli containers can also see the database root password. Splitting it into per-service env
  files would tighten that.
