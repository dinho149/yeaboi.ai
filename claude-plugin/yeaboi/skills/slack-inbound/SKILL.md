---
name: slack-inbound
description: "Read what a team said back to yeaboi in Slack — thumbs on a practice signal, ⏸ on a ceremony, a typed correction in a thread — and what became of each one, including the refusals and why. Use when the user asks whether their reaction registered, why nothing happened when they reacted or replied, whether the Slack reader is running at all, or who a Slack member id belongs to."
---

# The two-way Slack lane

Slack was write-only for yeaboi's whole life, and structurally so: an incoming
webhook answers a POST with the literal body `ok` and no message id, so yeaboi
could never identify its own message and a reaction on it was unreadable **by
construction**. Two-way is not a flag on the webhook — it is a different
credential (a bot token) and a job that reads the channel back on a cadence.

There is no daemon, no open port and no public endpoint. A short job wakes every
few minutes, reads a fixed 48-hour window, applies what is new, and exits.

## The one rule that explains every answer below

> **The anchor row is the argument list of the function the event will call.**
> Slack text never identifies a target and never selects an action. Identity is
> looked up, never parsed.

When yeaboi posts a standup, it records an *anchor* against that message: which
session, which ceremony, which stored run. Then it posts **one threaded reply per
practice signal**, each with its own anchor carrying the member and rule it is
about. So a reaction resolves to an action with nothing inferred:

| Where | Gesture | What it does |
|---|---|---|
| On the post | ⏸ `pause_button` / ▶️ `arrow_forward` / 🚫 `no_entry_sign` | pause, resume, or skip the ceremony's next occurrence |
| On a signal reply | 👍 `+1` / 👎 `-1` | answer that practice signal — a 👎 stops the detector firing for that member and rule |
| In the thread | `pause` / `resume` / `skip` / `ack`, alone | the same acts, typed |
| In the thread | anything else | an attributed note on the run the post was about |

A thumb **on the post** means nothing and is refused with a line saying so — it
cannot say *which* member's signal it means, and guessing is exactly the
inference the habit detector exists to refuse.

## Reading what happened

1. **`slack_inbound_history`** — every event the lane considered, newest first,
   with what became of it. This is the tool for "did my thumbs-down register?"

   Read the `outcome` column carefully; the words are not synonyms:

   - `applied` — done.
   - `deferred` — done, but the visible half is late. Somebody has that report
     open in an editable share, so the durable change landed and the report's own
     copy updates on the next run. Not a problem.
   - `refused` — the write said no, and `reason` is its own words ("that signal
     is no longer in the report").
   - `ignored` — not part of the grammar. A 🎉, or a reply too short to be a
     correction (`ok`, `ty`, `+1`). Nothing is wrong.
   - `unauthorized` — the actor is not in `SLACK_ALLOWED_MEMBER_IDS`. **Nobody is
     told this in the channel**, deliberately: a bot that answers unknown users
     is a bot anyone in the channel can make spam it.
   - `stale` — the post is older than 7 days.
   - empty / `in flight` — claimed but never settled. That is what a crash
     mid-apply leaves. It is reported and **deliberately never retried**, because
     every act here mutates something.

   `recent_polls` rides along in the same response, and it answers a different
   question: is the reader running at all? A `skipped_no_token`,
   `skipped_no_channel` or `skipped_no_allowlist` outcome means the poll fired
   and correctly declined. `failed` carries Slack's own error code.

2. **`slack_identities_list`** — which Slack users are bound to which team
   members. Used for exactly one thing: choosing between a roster name and a raw
   `@U…` as the author of a typed correction. **An empty list is a working
   configuration.** Everything in the lane works unbound; corrections are simply
   attributed to the id Slack attested.

## What you cannot do from here, and why

Both tools are read-only. There is no tool that applies an event, pauses a
ceremony, links an identity, installs the poll or edits the allowlist.

- **Applying** is off this surface because authorisation lives in the poller,
  against a member id **Slack's own servers attributed**. An apply tool would be
  a door where the caller asserts identity, in a lane whose entire premise is
  that identity is looked up and never parsed.
- **Linking** is safe but decides whose name goes on somebody else's report — the
  one binding in the lane Slack did not attest. It stays a human's, typed at a
  terminal.
- **Pausing, installing and credentials** write to the operating system and to
  `~/.yeaboi/.env`. Those are decisions made at the machine that will run the job.

Point the user at the terminal instead:

```bash
yeaboi slack check              # is two-way on, and can the bot see the channel?
yeaboi slack members            # the workspace's people and their member ids
yeaboi slack link U0123456789 "Ada Lovelace"
yeaboi slack history --pending  # anything claimed but never finished
yeaboi slack watch --status     # is the recurring poll installed?
```

## Setting it up (what to tell a user who has none of this)

Two-way needs a Slack **app**, not the webhook — create one, add bot scopes
`chat:write`, `channels:history` (or `groups:history`), `reactions:read` and
`users:read`, install it, and **invite it to the channel** (`not_in_channel` is
the most common real failure and its fix is `/invite @yeaboi`). Then set
`SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID` and `SLACK_ALLOWED_MEMBER_IDS` in
`~/.yeaboi/.env`, and `yeaboi slack watch --install`.

Two things soften the step up from pasting a webhook URL. **The webhook keeps
working** — with no bot token, delivery is byte-for-byte what it is today, so
two-way is opt-in rather than a migration. And `yeaboi slack check` verifies
before anything is trusted.

The allowlist is small and hand-curated on purpose, and it **fails closed in
three directions**: empty means nobody (and the poll then never calls Slack at
all), **one malformed entry voids the whole list** — a half-filled allowlist is
the more dangerous state because it looks configured — and the bot's own id is
never authorised, or its acknowledgement reaction would authorise the next round.

## What a Slack reply can and cannot reach

Worth saying plainly when a user asks whether this is safe to turn on.

**Can**: add one attributed, injection-swept annotation (≤2000 characters) to
today's run; pause, resume or skip-next a ceremony in that session's store; cast a
thumbs verdict on an anchored signal.

**Cannot**: change any generated field — a reply may *add* attributed prose, never
*change* prose, and that is enforced by a test over the package rather than by
convention; reach another session, another artifact kind, or any member and rule
other than the anchor's; delete anything; add or remove an operating-system job;
or spend money. A Slack pause writes a database flag and **never touches launchd
or crontab** — the drift shows on the Ceremonies page, and the fix is a terminal
command.
