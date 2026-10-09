---
description: Daily brief — today's schedule, money alerts, and email that needs action
argument-hint: (none)
---

Produce the user's daily brief. Run these in parallel as subagents, each asked for a **short** result:

1. `calendar`: today plus tomorrow morning. Conflicts and prep needed.
2. `finance`: check data freshness and refresh if older than 3 days. Report checking balance (flag if low),
   new card charges, bills due in the next 7 days, and anything unusual.
3. `inbox`: Q1 items and anything with a deadline in the next 7 days. Note archive freshness.

Then reply with:

- **Needs you today:** at most 5 bullets, most important first
- **Schedule:** compact list
- **Money:** 2–3 lines
- **Stale data:** anything that couldn't be refreshed, and the fix

Post the "Needs you today" bullets to the household chat with `node tools/chat-post.mjs priorities --file -`
(see CLAUDE.md for the rules), append a one-line summary under `## Agent Notes` in today's daily note, and
log both in `Agent/Log.md`.
