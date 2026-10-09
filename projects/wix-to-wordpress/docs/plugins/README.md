# Czech Studios custom plugins: architecture overview

> **Portfolio note.** This is the design document for three *planned* plugins (`czs-core`, `czs-atc`,
> `czs-members`). It is published as an example of the planning work. The two companion feature specs
> and the audit documents it was based on are not included. The plugin that was built, `czs-blocks`
> (in `wordpress/plugins/`), is separate and is described in the project README.

- **Status:** design only (2026-09-29). None of these three plugins existed when this was written.
- **Decision (Ben, 2026-09-29):** build the ATC Portal and the membership/course system as **custom WordPress plugins**, not off-the-shelf LMS or membership plugins. The site owner must be able to run and extend them without code.
- **Companion specs (not included here):**
  - ATC Portal spec: the Artist Trading Card community (cards, artists, swaps, trades, challenges, the tarot deck project).
  - Membership spec: plans, packages, session credits, billing, content restriction, the drip course engine, email sequences and the member account area.
- **Sources:** an audit of the Wix back end and front end, and a read-only Wix API export (structure and counts only). These are not included.

Assumptions are marked **(A#)** and listed in [§10](#10-assumptions). Open questions are in [§11](#11-open-questions).

---

## 1. Recommendation in one paragraph

Build **three plugins in one repository: a small shared core plus two feature plugins**:
- `czs-core`: the shared plumbing.
- `czs-atc`: the ATC Portal.
- `czs-members`: memberships, courses and billing.

Both feature plugins declare `Requires Plugins: czs-core` (a WordPress 6.5+ header), so WordPress will not activate either without the core. Everything is plain PHP 8.3 plus small vanilla-JS modules, with **no Composer runtime dependencies, no Node build and no third-party plugin dependencies**. Every front-end component is one PHP render function, exposed three ways: as a **shortcode**, as a **native Elementor widget**, and later as a **server-rendered block**.

## 2. Architecture options considered

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **A. One monolithic plugin** | One install. One settings area. No version skew. | Couples a free community tool to payment code. A billing bug can take the ATC Portal down, and the reverse. Harder to ship the ATC Portal before the payment processor is chosen. The owner sees one giant menu. | No |
| **B. Two independent plugins** | Clean separation. Each can ship alone. | Both need the same things: member profiles, notifications, the email queue, settings screens, content ratings, consent records, privacy exporters, Elementor plumbing and the migration framework. Duplicating them means two copies to secure and maintain. The member "My Account" area would split in two. | No |
| **C. Shared core + two feature plugins** *(recommended)* | Shared services are written and secured once. Each feature plugin can be enabled, disabled, deployed and rolled back on its own. The ATC Portal can launch before billing is decided. The owner gets one "Czech Studios" menu that feature plugins add to. One account area with tabs contributed by each plugin. | Slightly more packaging work: three plugin headers and a version compatibility check. | **Yes** |

**Why C fits this project:**
1. **Independent timing.** The payment processor is undecided and it is unclear where money flows today (per the Wix back-end audit). The ATC Portal is free and has no such blocker.
2. **Risk isolation.** Billing code handles money and webhooks. The ATC Portal handles user uploads from the public. Keeping them in separate plugins keeps each attack surface and each failure contained.
3. **Shared member identity.** One WordPress user is both an ATC artist and a course student. The core owns the account area, notifications and consent records, so the member sees one coherent account.
4. **Lean on a 1 GB e2-micro.** Each plugin loads only its own code, and only on the requests that need it. See the performance budget in §6.

## 3. What goes where

| Concern | `czs-core` | `czs-atc` | `czs-members` |
|---|---|---|---|
| Roles and capability registry, "Studio Manager" role | ✔ | registers ATC caps | registers membership caps |
| Settings framework (tabbed admin pages, sanitised options) | ✔ | uses | uses |
| **Fields API**: meta boxes, repeaters and front-end forms with no ACF dependency | ✔ | uses | uses |
| **Field Manager**: the owner renames, reorders, hides or requires fields and adds simple custom fields | ✔ (phase 2) | card, artist and swap forms | profile and onboarding forms |
| Content ratings (Friendly / Possibly Triggering / 18+) and content notes | ✔ | cards, swaps | lessons, resources |
| Notifications: on-site bell and email, with an editable template per event | ✔ | ATC events | membership events |
| **Mail queue and SMTP sender** (batched through cron) | ✔ | | sequences engine |
| Email sequence engine (steps, delays, triggers) | ✔ engine | adds triggers | owns the UI and course triggers |
| Consent records (terms version, 18+ affirmation, disclaimers) | ✔ | portal terms | purchase terms, "not therapy" |
| Audit log (who changed what) | ✔ | writes | writes |
| Account area shell (`/account/`), branded login, registration, magic-link login | ✔ | "ATC Studio" tab | billing, courses and credits tabs |
| Privacy exporters and erasers | ✔ framework | registers | registers |
| Elementor widget category, widget base class, "Access" visibility control | ✔ base | widgets | widgets and the visibility rules |
| Migration framework (WP-CLI `wp czs migrate …`, Wix ID map, media sideload) | ✔ | ATC importers | plan, course and member importers |
| Encryption helper (libsodium) for mailing addresses and gateway secrets | ✔ | uses | uses |
| Cards, artists, swaps, trades, challenges | | ✔ | |
| Plans, access groups, credits, gateways, courses, coupons | | | ✔ |

## 4. Naming and prefixes

| Thing | Convention | Examples |
|---|---|---|
| Plugin slugs / folders | `czs-{name}` | `czs-core`, `czs-atc`, `czs-members` |
| PHP namespaces | `CzechStudios\{Plugin}\…` (PSR-4 via a tiny built-in autoloader; no Composer at runtime) | `CzechStudios\Core\Mail\Queue`, `CzechStudios\Members\Billing\Gateway` |
| Global functions (template helpers only) | `czs_` | `czs_user_has_access()`, `czs_atc_card_image()` |
| Hooks | `czs/{plugin}/{event}` | `czs/members/membership_activated`, `czs/atc/card_published` |
| Options | `czs_{plugin}_{name}`, grouped per settings tab, `autoload = no` except tiny flags | `czs_atc_moderation`, `czs_members_gateways` |
| Post meta / user meta | `_czs_{name}` (underscore hides it from the Custom Fields box) | `_czs_orientation`, `_czs_wix_id` |
| Custom post types (20-char limit) | `czs_{thing}` | `czs_atc_card`, `czs_atc_swap`, `czs_artist`, `czs_course`, `czs_lesson`, `czs_plan` |
| Taxonomies | `czs_{thing}` | `czs_atc_category`, `czs_atc_tag`, `czs_rating` |
| Custom tables | `{$wpdb->prefix}czs_{name}` | `wp_czs_swap_participants`, `wp_czs_memberships` |
| REST namespaces | `czs/v1` (core), `czs-atc/v1`, `czs-members/v1` | `GET /wp-json/czs-atc/v1/cards` |
| Capabilities | `czs_{verb}_{object}` | `czs_upload_atc`, `czs_moderate_atc`, `czs_manage_plans` |
| Shortcodes | `czs_{component}` | `[czs_atc_gallery]`, `[czs_course_outline]` |
| Elementor widgets | category "Czech Studios", names `czs-{component}` | `czs-atc-gallery` |
| Text domains | same as the plugin slug | `czs-atc` |
| Constants (secrets and config in `wp-config.php` / env) | `CZS_*` | `CZS_ENCRYPTION_KEY`, `CZS_STRIPE_SECRET_KEY`, `CZS_STRIPE_WEBHOOK_SECRET` |

## 5. Coding and security standards

### 5.1 Capabilities, not roles
- Code checks capabilities only (`current_user_can( 'czs_moderate_atc' )`), never role names.
- Object-level checks go through `map_meta_cap`. For example, `edit_czs_atc_card` resolves to "is the author, or has `czs_moderate_atc`".
- Role map (A1):

| Role | Who | Key caps |
|---|---|---|
| Administrator | Ben | everything, including `czs_manage_billing` (gateway keys, refunds) and `czs_manage_system` (tools, migrations) |
| **Studio Manager** (new) | Site owner | `edit_pages`/`edit_posts` (editor-level) plus every `czs_manage_*` / `czs_moderate_*` cap. Also `czs_refund_orders` (A2). No plugin/theme install and no file editing. |
| Moderator (optional) | trusted helpers later | `czs_moderate_atc`, `czs_moderate_discussions` |
| Member | every registered user | `read`, `czs_upload_atc`, `czs_join_swap` |
| Trusted Artist (flag, not a role) | granted automatically after N approved cards, or by the owner | `czs_publish_atc_without_review`, `czs_host_swap` |

The Wix member roles "Artist" and "Collaborator" map to Member plus the Trusted Artist flag. See the ATC spec.

### 5.2 Requests, input and output
- **Nonces:**
  - Every state-changing form carries `wp_nonce_field()`, checked with `check_admin_referer()` or `wp_verify_nonce()`.
  - REST writes from the front end use cookie auth with the `X-WP-Nonce` header (`wp_rest`).
  - Application passwords (the agent tooling) are the only other write authentication.
- **REST permission callbacks:**
  - Every route has an explicit `permission_callback`. `__return_true` is allowed **only** on read routes that return public, published data.
  - Write routes check the capability **and** object ownership inside the callback, not in the handler.
  - Every argument is declared in `args` with `type`, `sanitize_callback` and `validate_callback`.
- **Sanitise on input:**
  - `sanitize_text_field`, `sanitize_textarea_field`, `absint`, `esc_url_raw`, `sanitize_key`, and enum allow-lists.
  - Member-authored rich text goes through `wp_kses()` with a **minimal** allow-list (p, br, strong, em, a[href], ul/ol/li). Members never get `unfiltered_html`.
- **Escape late on output:** `esc_html`, `esc_attr`, `esc_url`, `wp_kses_post` for admin-authored content, and `wp_json_encode` for data sent to JS.
- **SQL:** always `$wpdb->prepare()`. Custom tables are accessed through a repository class per table. No string-built SQL.
- **Uploads:**
  - MIME allow-list (JPEG, PNG, WebP), checked with `wp_check_filetype_and_ext` plus `getimagesize`.
  - Size cap is a setting (default 8 MB).
  - Every upload is **re-encoded server-side, stripping EXIF/GPS**.
  - Uploads have a daily per-user limit.
- **Secrets:**
  - Gateway keys and the encryption key live in `wp-config.php` constants or container env vars (`CZS_*`), not in the database. (A3)
  - If a key must be entered in the UI, it is stored encrypted with libsodium and shown masked.
- **Webhooks:**
  - The signature is verified before any processing.
  - Event IDs are stored for idempotency.
  - Every change is re-fetched from the gateway API rather than trusting the payload.
  - Webhooks always return 2xx fast and process through the queue.
- **Rate limiting:** a per-user and per-IP counter (transients) on uploads, reports, swap joins, login-link requests and coupon attempts.
- **Direct access:** every PHP file starts with `defined( 'ABSPATH' ) || exit;`.
- **Privacy in logs:** logs, the audit trail and error messages never contain emails, addresses or payment data. They use user IDs only.
- **Uninstall:** `uninstall.php` removes data only if Ben ticks "Delete all Czech Studios data on uninstall". The default keeps data.

### 5.3 Quality gates (development time only; nothing runs on the server)
- PHPCS with `WordPress-Extra` plus `PHPCompatibilityWP` (8.3). PHPStan level 6 with WordPress stubs.
- PHPUnit with the WordPress test suite. Mandatory coverage for:
  - the access-check function
  - the drip-unlock calculator
  - the membership state machine
  - the webhook handler
  - the swap state machine
- GitHub Actions or a local script. The plugin zip is built by a script that copies PHP, CSS and JS. **No transpile step.**
- Front-end JS is hand-written ES modules (loaded with `type="module"`). Admin UIs use the React bundled in WordPress core (`wp.element`, `wp.components`) through `wp.element.createElement` or a vendored ~1 KB `htm` helper, so no JSX build is needed. Drag-and-drop uses jQuery UI Sortable, which ships with WordPress.

## 6. Performance budget (e2-micro, 1 GB RAM, `WP_MEMORY_LIMIT` 256M, MariaDB capped at 320 MB)

| Rule | Why |
|---|---|
| Load feature code lazily. Enqueue assets only on pages that contain a Czech Studios widget or shortcode. Admin assets only on Czech Studios screens. | Keep per-request memory low. Target < 16 MB added per request. |
| Use custom tables for relationships and ledgers: swap participants, entries, trades, memberships, grants, credits, progress and the mail queue. Use CPTs and taxonomies only for content that needs URLs, SEO or editor UX. | Avoid `wp_postmeta` joins at scale. Relationship tables have indexes on (object, user). |
| Paginate everything. Cap `per_page` at 48. Use "Load more" (REST) rather than rendering hundreds of cards. | Bounded queries |
| Limit ATC image sizes. Register only `czs_atc_thumb` 300×420, `czs_atc_medium` 600×840 and `czs_atc_large` 1200×1680, and suppress WordPress's default large sizes for ATC uploads. The browser **downsizes to ≤ 2400 px before upload** (canvas). | Prevents GD/Imagick memory spikes from 12 MP phone photos, and saves disk |
| Video is **never** uploaded to the VM. Use embeds only: YouTube unlisted, Vimeo, or Bunny Stream (A4). | Disk and bandwidth |
| Cron runs through the existing **system cron → `wp-cron.php` every 5 minutes** (`DISABLE_WP_CRON` is already true). Each job is batched: it takes a MariaDB `GET_LOCK` against overlap, has a 20-second time budget, and resumes on the next tick. | Predictable load. No overlapping runs. |
| Transients are used for computed counts (gallery facets, dashboard stats) and invalidated on write. No Redis (it costs RAM). | |
| No external requests during page render. Gateway and ESP calls happen only on checkout, webhook or cron. | Resilience |
| Full-page caching (if added later) must bypass logged-in users and all `/account/`, `/atc-portal/` and course URLs. The plugins send `nocache_headers()` on personalised pages. | Correct access control |

## 7. Elementor integration

**Principle:** one PHP component, three wrappers. The owner builds pages in Elementor by dragging widgets from the "Czech Studios" panel. Anything also works as a shortcode (Elementor's Shortcode widget, the block editor, or classic).

| Mechanism | Use | Notes |
|---|---|---|
| **Native Elementor widgets** (primary) | Galleries, forms, dashboards, course outline, pricing tables and so on | A thin `\Elementor\Widget_Base` subclass. Controls map 1:1 to shortcode attributes: a *Content* tab for data source and filters, a *Style* tab for colours, typography, spacing and card style. In the editor it renders with sample data so the owner sees a realistic preview. |
| **Shortcodes** (always available) | Any page or post: Elementor's Shortcode widget, the block editor or the classic editor. Emails don't use shortcodes; they use merge tags. | Same attributes as the widget. Documented on the plugin's Help screen with copy buttons. |
| **"Czech Studios Access" control** on every Elementor section, container and widget | Show or hide any element by access group, logged in/out, enrolled in a course, or ATC trusted artist | Uses `elementor/element/after_section_end` to add controls and `elementor/frontend/*/should_render` to enforce them. Works with free Elementor. |
| **Single templates** for plugin post types (card, swap, artist, course, lesson) | The plugin ships default templates. The owner can pick **any saved Elementor template** as the layout for each post type in Settings → Templates. | Rendered with `Plugin::$instance->frontend->get_builder_content_for_display( $id )`. Context-aware widgets (e.g. "Lesson Prompts") read the current post. **This works without Elementor Pro.** |
| **Elementor Pro dynamic tags** (only if Pro is licensed) | Bind lesson or card fields into any Pro widget | Optional. Registered only if Pro is active (A5). |
| **Blocks** (phase 2) | For the owner editing lessons in the block editor | `block.json` + `render.php` (server-side render) + a plain-JS `edit` using `wp.serverSideRender`. No build. |
| **Theme overrides** | Ben can override any template in `hello-elementor-child/czs/{plugin}/…` | Standard `locate_template` pattern |

Widgets are listed in each feature spec (ATC §7, Membership §11).

## 8. Migration approach from Wix

**Principles:**
- Migration is **scripted, repeatable and idempotent** (WP-CLI). Every imported object stores `_czs_wix_id`, so re-runs update rather than duplicate.
- Everything is rehearsed on staging (`<staging-host>`) before cutover.

| Step | What | Tool |
|---|---|---|
| 1. Export | CMS collections (already in `data/wix-export/<site-slug>/cms/`). The member and contact list with consent status comes from the dashboard CSV; it is kept **outside the repo** (`data/` is gitignored). Media comes from `static.wixstatic.com` original URLs. | `scripts/wix_export.py` (exists); dashboard CSV |
| 2. Map | Wix member ID → WordPress user, matched by email in memory at import time. A mapping file is written to `data/` only. | `wp czs migrate users --dry-run` |
| 3. Import content | ATC lookups → taxonomies. Cards → CPT. The artist profile. Swaps. Epic Journey lessons → course and lessons. Plans → plans. Only active coupons. | `wp czs migrate atc`, `wp czs migrate courses`, `wp czs migrate plans` |
| 4. Import entitlements | Existing Wix plan orders become memberships with `source = import` and matching access grants and credits | `wp czs migrate memberships` |
| 5. Media | Sideload each referenced image once, dedup by Wix media ID, re-encode and strip EXIF | part of each importer |
| 6. Redirects | Keep Wix URLs where possible (`/atc-card/{slug}`, `/atc-member/{username}`, `/atc-swap/{slug}`, `/epic-journey/{slug}`). Emit a CSV of the rest for the Redirection plugin (or the core's tiny redirect table). Carry over the 13 existing Wix 301s. | `wp czs migrate redirects` |
| 7. Verify | Counts per object type, broken-image report, access spot checks ("view as" each imported member) | `wp czs migrate report` |
| 8. Cutover | Freeze Wix: banner, and turn off portal uploads and plan sales. Final delta import. DNS switch. Then send **"claim your account" emails in batches** (passwords can't be migrated; the email carries a one-time magic link to set a password). | `wp czs members send-claim --batch=25` |

- Wix Payments card tokens and subscriptions **cannot move**. The export showed no active recurring subscriptions, so no subscriber would need to re-enter a card. Confirm with the site owner (see the questions).
- The ATC and membership specs each have a detailed migration section.

## 9. Phased roadmap

Sizes are relative (S ≈ days, M ≈ 1–2 weeks, L ≈ 3+ weeks of focused work).

### Phase 0: Foundation (`czs-core`), size M
- Roles and caps, the settings framework, the Fields API, content ratings, consent records, the audit log.
- The mail queue plus the SMTP sender through a transactional provider. GCE blocks outbound port 25, so use 587/465 (A6).
- The notification service with editable templates.
- The account-area shell: branded login and registration, **magic-link login**, profile, data export and delete.
- The Elementor base (category, widget base, Access control) and the template loader.
- The WP-CLI migration framework.

### Phase 1: MVP (launch replacement for Wix)
| Plugin | MVP scope | Size |
|---|---|---|
| `czs-atc` | Cards (upload, edit, gallery with filters, single card page), artist profiles (claimable), taxonomies the owner manages (categories, tags, types, series, trade-policy presets), content ratings with blur/hide, **moderation queue** and reporting, swaps (host, join, add cards, participant manager, host-only mailing addresses), challenges and the monthly prompt calendar with CSV import, Wix import and redirects | L |
| `czs-members` | Access groups plus restriction rules (post, term, URL prefix, Elementor, shortcode), the **Plan Builder** (one-time, recurring, free), a **Stripe** gateway (hosted Checkout + Customer Portal + webhooks), a "Manual" gateway, a free/100 %-coupon path, simple coupons, a credits ledger (grant on purchase, manual redeem, expiry), the **Course Builder** with drip-by-enrollment, progress, lesson discussion (comments) and lesson email sequences, the member account area, and Epic Journey plus plan/member migration | L |

**MVP exit criteria:**
- A new visitor can buy Epic Journey with Stripe, receive Day 0/1 emails and see lessons unlock daily.
- The owner can create a plan and a course without Ben.
- An ATC artist can upload a card, have it approved and join a swap.
- Every Wix URL in the sitemap resolves.

### Phase 2: Depth
- **ATC:**
  - 1:1 trades with offers and counters
  - collections and provenance ("traded to…")
  - reactions and follows
  - the **tarot deck and slot-based collaborative project** UI (the MVP can run it as a swap with a manual slot list)
  - the Field Manager (custom fields)
  - server-rendered blocks
- **Membership:**
  - packages/bundles with physical perks and a fulfilment export (Forge, Nurture)
  - gifting and the Patron plan
  - a scholarship pool
  - a bulk coupon generator (beta codes)
  - a booking integration for credits (a Cal.com/TidyCal webhook or a native availability picker)
  - general email sequences (onboarding, win-back, trial ending)
  - a replays and resources library
  - reports
  - a PayPal gateway (the owner already uses PayPal and Venmo)

### Phase 3: Community and polish
- Community **Spaces**: access-group discussion boards, **Healing Circles** small groups with a rotating space holder, and **Discord role sync** through the existing Czech Studios bot.
- An activity feed.
- Private journaling (opt-in, encrypted).
- Completion badges and certificates.
- An ATC "for sale" (ACEO) listing bridged to WooCommerce.
- Digital card downloads.
- GCS media offload.
- An optional AI helper. The prototype had a Gemini chat; it must be a remote API, and only if the owner wants it.

## 10. Assumptions

| # | Assumption |
|---|---|
| A1 | The site owner works as a custom "Studio Manager" role rather than Administrator, so they can't accidentally break plugins or themes. Ben stays Administrator. |
| A2 | The owner may issue refunds. If not, `czs_refund_orders` goes to Ben only. |
| A3 | Secrets can be supplied as env vars through `infra/wordpress/.env` → `WORDPRESS_CONFIG_EXTRA`, matching the existing Docker setup. |
| A4 | Course videos will be hosted externally (YouTube unlisted, Vimeo or Bunny Stream). The VM can't serve video. |
| A5 | Elementor **free** is the baseline. Elementor Pro is not assumed. |
| A6 | A transactional email provider (e.g. Brevo, Postmark, Mailgun or Amazon SES) will be used over SMTP 587/465, with SPF/DKIM/DMARC records on the site's domain. |
| A7 | WordPress ≥ 6.5 (for the `Requires Plugins` header) and PHP 8.3, matching `wordpress:php8.3-fpm-alpine`. |
| A8 | The store (paints, printables, originals) is handled separately, probably by WooCommerce. These plugins do not sell physical products, apart from membership perks that need fulfilment. |
| A9 | Scale target: hundreds to low thousands of members, ~10k cards, ~100 active swaps a year. At that scale, custom tables with indexes on a single MariaDB are enough. |

## 11. Open questions

These are the cross-cutting ones. Each spec has its own list.

1. **[Ben]** Is **Elementor Pro** licensed, or will it be? This decides whether we add Pro dynamic tags and Theme Builder conditions or rely only on the plugin's template picker.
2. **[Ben]** Which **transactional email provider**, and which sending address on the site's domain? It needs DNS records in Wix DNS now, or at the future DNS host.
3. **[Ben]** Will the store move to **WooCommerce**? If so, should one-time plans also be purchasable in the Woo cart (one checkout for paints plus course), or stay on direct Stripe Checkout? WooCommerce adds noticeable RAM on an e2-micro.
4. **[Ben]** Should the owner be an **Administrator** or the proposed **Studio Manager** role? Should they be able to issue refunds?
5. **[Ben]** Where should code live and how is it deployed: a new GitHub repo `czech-studios-plugins`, or a folder in this repo? And how does it get onto the VM (git pull, rsync, or a zip through wp-cli)?
6. **[Owner]** Plugin and menu naming: is "Czech Studios" the admin menu name, with "ATC Portal" and "Memberships & Courses" beneath it? Any preferred wording, e.g. "Studio" or "Sanctuary"?
7. **[Ben]** Media growth: should the VM disk be used for member uploads at first (current Wix media is a few GB), with Google Cloud Storage offload deferred to phase 3?
