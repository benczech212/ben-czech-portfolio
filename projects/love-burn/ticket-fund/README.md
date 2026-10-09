Part of Ben Czech's portfolio. Built with Claude Code.

# AWEtisticats Ticket Fund app

A realtime web app a Love Burn theme camp uses to rank its art projects, pool donated gear, and hand out
ticket redemption codes.

## The lesson

**The problem.** A static web app talking straight to Firestore has no server of its own. Anyone signed in
can open the browser console and call the database API directly, so hiding a button or checking a price in
JavaScript protects nothing.

**The idea.** Treat [`firestore.rules`](firestore.rules) as the backend. Every permission the app needs (who
can read the roster, who can change which field, what a ticket costs) is written as a rule the database
enforces on every request. The UI only mirrors those rules, and a small CLI lets you test them by acting as
any user.

### Walkthrough

1. **Name the roles once.** The top of [`firestore.rules`](firestore.rules) defines `verified()`,
   `isAdmin()` and `isMember()`. A member is a verified sign-in whose `memberUids/{uid}` link points at a
   member record that points back, so neither half of the link can be forged alone.
2. **Let members touch only their own slice.** A project's `helpers` and an item's `donations` are maps
   keyed by uid. This helper makes any other change fail:
   ```
   function onlyOwnMapEntry(field) {
     return request.resource.data.get(field, {}).diff(resource.data.get(field, {})).affectedKeys().hasOnly([request.auth.uid]);
   }
   ```
   The project rule combines it with `onlyKeys(['helpers'])`, so a member can't also edit the cost or status.
3. **Check values against server data.** A ticket request is only accepted at the configured price, read
   from `config/main` inside the rule:
   ```
   && request.resource.data.status == 'pending'
   && request.resource.data.code == null
   && request.resource.data.price == cfg().prices[request.resource.data.tier]);
   ```
4. **Keep secrets in a collection members can't read.** `match /codes/{code}` allows reads and writes only
   to admins. Approving a request copies one code onto that member's own request document.
5. **Close the bootstrap hole and default to deny.** `config/main` has `allow create, delete: if false`
   (it's seeded server-side), and the last rule, `match /{other=**}`, refuses anything not listed.
6. **Still escape on the client.** The rules decide who may write; [`public/index.html`](public/index.html)
   still runs every Firestore string through `esc()` before it reaches `innerHTML`.
7. **Test as a real user.** [`tools/fund.mjs`](tools/fund.mjs) talks to the emulator as the rules-bypassing
   owner by default. With `--as <email>` it signs in as an emulator user and every call goes **through the
   rules**.

### Try it

This needs the Firebase CLI (`npm i -g firebase-tools`) and Java for the emulators; no cloud project or
real account is used. On localhost, [`public/firebase-config.js`](public/firebase-config.js) connects to
the emulators with the `demo-awetisticats` project. Follow **Local testing** below to start the emulators,
seed data and link a test member. Then try a write the rules should refuse:

```
node tools/fund.mjs --as tester@example.com set projects/dance-floor '{"status":"active"}'
```

A member may change only their own `helpers` entry on a project, so this update should be denied. The same
command without `--as` succeeds, because the owner bypasses the rules. (This walkthrough wasn't run for the
portfolio copy: the emulators need a global install. `node --check` passes on both Node scripts.)

### Take it to your own project

- With a client-only app, write the security model in the rules first and build the UI to match.
- Express "only your own entry" as a map keyed by uid plus a `diff().affectedKeys().hasOnly(...)` check.
- Never trust a client-sent value the server already knows (prices, owner ids, names); compare it in the rule.
- End with a deny-all match, and seed privileged config server-side instead of letting the first user create it.
- Keep a CLI that can act as any test user, so you can show a rule refusing a write and not just assume it.

## Highlights

- **Single-file SPA** (`public/index.html`, about 1,150 lines of HTML/CSS/JS, no build step) with **Firebase
  Google Auth** and **realtime Firestore** listeners: every open phone sees the same state.
- **Ranked-choice vote:** an STV-style sequential elimination with a **Borda-count tie-break**, computed
  client-side, plus a sub-vote for each parent project's pieces and a round-by-round table.
- **Owner-only edits via map diffs:** members change only their own entry in a project's `helpers` map or an
  item's `donations` map. The rules check `diff().affectedKeys().hasOnly([uid])` and validate the entry.
- **Server-checked ticket price:** a request's `price` must equal `config.prices[tier]`, so the client can't
  spoof it.
- **Admin-only redemption codes:** the `codes` collection is unreadable to members. An approved code is
  copied onto that member's own request doc.
- **Emulator workflow + Node CLI:** the whole app runs offline against the Firebase emulators, and
  `tools/fund.mjs` reads and writes the data either as the owner or **through the security rules** as any
  test user.
- **Idempotent seeder:** `seed/seed.mjs` upserts the project list from `seed/projects.json` by stable slug and
  never overwrites decisions (`status`) or member input (`helpers`, `donations`).

## Concepts
- **Needs page**: every line item across all projects in one flat list: search, filter (still needed / everything / donated), sort (by project / biggest need / cheapest), include-shelved toggle, running total of what's still uncovered, and a 🎁 donate button on each row.
- **Quick look drawer**: an ℹ️ button on every project card and ranking row slides in the description, cost/need, votes, helpers, sub-projects and line items without leaving the page.
- **Projects** have **line items** (`qty × unit cost`). Every project has its own **linkable page** (`#/project/<id>`) with a description, helpers, line items and (for top-level projects) sub-projects. Admins edit everything inline.
- **Required projects** (`required: true`, e.g. Logistics, Camp Infrastructure) are funded first and are **not on the ballot**. The budget panel takes them off the top of the camp budget (`config.goal`).
- **Sub-projects** (`parentId`) live under a parent. A parent's cost includes its non-shelved children; each parent with 2+ voteable children gets its own sub-vote.
- **Ranked-choice vote**: every linked member who is not a **guest** ranks the top-level projects (partial rankings fine; saved instantly). Ballots are public by default and a member can mark theirs **secret**. The **Camp Ranking** is STV-style sequential elimination: each round counts every ballot's top surviving project, eliminates the lowest, and transfers those ballots to their next choice; ties go to fewer Borda points.
- **Activation**: once the ranking is in, admins set each project to **active** or **shelved**. Shelved projects drop out of the budget and the ballot; line items that depend on them (`dependsOn`) are crossed off.
- **Donations**, **Willing to help**, **Discussion** (threaded comments with hearts) for linked members.
- **Ticket requests and approval**: a member requests N tickets of a tier; an admin approves, which assigns the next free redemption code of that tier and shows it only to that member. Reject or revoke returns the code to the pool.

## Joining: how a sign-in becomes a member
An admin adds each person on the Members page, optionally with the Google email they will sign in with.
- **Pre-registered email:** when that person signs in (Google, so the email is verified), the app finds the
  record registered to their email and they claim it in one click. The claim sets `members/{id}.uid` and
  creates `memberUids/{uid}` in one batch, and the rules check each write against the other.
- **No pre-registered email:** the person sends an **access request** (`claims/{uid}`) from the My Vote tab.
  An admin links it to the right member record from the Members page.

Until a sign-in is linked, it can see the project list and its own requests only. The roster, the ballots
and the discussion stay hidden.

## Firestore collections
`config/main` (`goal`, `prices`, `payNote`, `admins`) · `projects/{id}` (`parentId`, `required`, `status`, `helpers:{uid:{memberId,name}}`) + `projects/{id}/items` (`cost`, `qty`, `dependsOn`, `donations:{uid:{memberId,name,qty}}`) + `projects/{id}/comments` (`{projectId, uid, memberId, name, text, parentId, hearts:{uid:true}, createdAt}`, read through a collection-group listener) · `members` (`{name, role, uid, email}`) · `memberUids/{uid}` (`{memberId}`) · `claims/{uid}` (access requests) · `ballots/{memberId}` · `requests` (one doc per requested ticket; owner-readable) · `codes` (admin-only)

## Security notes
The rules (`firestore.rules`) are the security boundary. The UI only mirrors them.
- **Verified identity:** every rule that grants anything beyond reading the project list requires
  `request.auth.token.email_verified == true`. Google sign-in provides that.
- **No first-user bootstrap:** `config/main` has `create: false`. It is seeded server-side with the admin list
  (`seed/seed.mjs --admin …`), so being the first person to sign in grants nothing.
- **Member-gated reads:** `members`, `ballots` and `comments` are readable only by a linked member
  (`memberUids/{uid}` exists and that member's `uid` points back) or an admin. Before linking, a user can read
  only member records pre-registered to their own verified email.
- **Claiming:** a self-claim needs an unclaimed record whose pre-registered `email` equals the verified token
  email, and must create the `memberUids` link in the same batch. Every other claim goes through an admin.
- **Validated writes:** comments must use an auto-generated id, carry the author's own `uid`, `memberId` and
  member `name`, point at the project they're stored under (which must exist), and have 1–2000 characters of
  text and an empty `hearts` map. Hearts can only be added or removed for your own uid. Helper and donation
  entries must carry the member's real id and name, and a donation quantity must be positive and no larger than
  the item's quantity. Ballots and requests are restricted to a fixed set of keys.
- **Ticket price and codes:** see Highlights. Requests must also carry the requester's own member id and name.
- **Client-side escaping:** all Firestore strings go through `esc()` (which escapes quotes too) before reaching
  `innerHTML`. Values passed into inline handlers go through `jsq()`, a JSON string literal that is then
  HTML-escaped. The avatar URL must be `https:`.
- **Privacy caveat:** a linked member can read every ballot (that's how the live tally works). A "secret"
  ballot is hidden in the UI, but a determined member could read it through the API. For true secrecy,
  restrict ballot reads to admins and publish a results snapshot instead.

## Local testing (emulators, fully offline)
```
firebase emulators:start --project demo-awetisticats       # app :5000, emulator UI :4000
node seed/seed.mjs --admin you@example.com                 # config + admin, then the project list
```
Sign in on http://localhost:5000 with the emulator's fake Google account using the same email to get the
admin view. To test as a member through the rules:
```
node tools/fund.mjs add members '{"name":"Test Member","role":"member","uid":null,"email":null}'
node tools/fund.mjs link tester@example.com <memberId>     # creates a verified emulator user and links it
node tools/fund.mjs --as tester@example.com summary        # everything below now runs THROUGH the rules
```

## `tools/fund.mjs`: agent / dev CLI
A CLI over the Firestore REST API, so an agent (or you) can inspect and change state without the UI:
```
node tools/fund.mjs summary            # derived state: budget, members, projects (cost/donated/need/rank/items), STV results, ballots
node tools/fund.mjs dump               # raw snapshot of every collection
node tools/fund.mjs results            # the STV tally only
node tools/fund.mjs list members       # any collection, incl. projects/<id>/items, projects/<id>/comments
node tools/fund.mjs get projects/dance-floor
node tools/fund.mjs set projects/dance-floor '{"status":"active"}'      # merge fields (creates if missing)
node tools/fund.mjs del ballots/<memberId>
node tools/fund.mjs link <email> <memberId>                              # emulator only
node tools/fund.mjs wipe ballots       # emulator only
```
By default it targets the emulator as the rules-bypassing owner. Add `--as someone@example.com` to act
**through the security rules** as that emulator user: it is created if needed and marked email-verified. For
production, pass `--project <id> --token "$(gcloud auth print-access-token)"`.

## Seeding the project list
`seed/projects.json` is the source of truth for projects and line items, derived from the camp's grant budget
and finance notes. Project ids are stable slugs.
- Emulator: `node seed/seed.mjs --admin you@example.com` (add `--wipe` to clear projects/items/ballots first).
- Production: `node seed/seed.mjs --project <firebase-project-id> --token "$(gcloud auth print-access-token)" --admin you@example.com`.

A re-run only updates planning fields. `status`, `helpers` and `donations` are never overwritten, and an
existing `config/main` is left alone.

## Deploy
1. `npm i -g firebase-tools`, then `firebase login`.
2. Create a Firebase project, add a **Web app**, and copy its config into `public/firebase-config.js`. Enable
   **Firestore** (production mode; the rules come from this folder) and **Authentication → Google**.
3. From this folder, run `firebase use <project-id> && firebase deploy`.
4. Seed it: `node seed/seed.mjs --project <project-id> --token "$(gcloud auth print-access-token)" --admin you@example.com`.
