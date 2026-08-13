# security sweep

**Trigger** — cron `0 6 * * 1,4` (Mon + Thu 06:00 UTC)
**Summary** — Mon: dependency and SAST audit. Thu: a guardrail surface review
**Workstream** — [`workstreams/security.md`](../../workstreams/security.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = security`.

## Drain the approved disclosures

**Before the sweep, and whichever day it is.** A disclosure filed on a previous run is never
implemented by the run that found it — it waits for a ✅ in Slack, which `cron/slack-relay.md`
turns into a `security:approved` label on the Linear ticket. This step is the other half of that
hand-off, and without it the approval goes nowhere.

1. **Check for work in flight first**, exactly as [sweep-procedure.md](../../sweep-procedure.md)
   step 2 does: `gh pr list --label "workstream:security" --state open`. If one is open, drive
   *that* PR to green and stop — a drain is still a build, and the one-open-PR guardrail in
   [house-rules.md](../../house-rules.md) does not have a security exception.
2. List Linear issues carrying **both** `workstream:security` and `security:approved` that are not
   already `In Progress`, `In Review` or `Done`. Nothing to drain is the common case; exit this
   step quietly and sweep as usual.
3. Take the oldest, and build it exactly as
   [sweep-procedure.md](../../sweep-procedure.md) step 5 says — the same spawns in the same order,
   with the ticket body as the charter in place of a scout find: `cowork-scribe` moves the ticket
   to **In Progress**, `cowork-builder` implements, **you** spawn `code-reviewer` on the diff, and
   the scribe attaches the PR and moves it to **In Review**. Two things are already done for you
   and neither is a shortcut past that step: there is no proposal issue, because the human's ✅ is
   the approval and the label is the record of it, and there is no Linear ticket to open, because
   the disclosure run already filed one. **Never write to Linear yourself** — the scribe is the
   only author of outbound comms, and a state transition is one.
4. **One per run.** These are the finds judged too sensitive to describe in public, and the
   builder's PR is public the moment it opens. Two at once means two branches whose diffs must be
   read together to see what is being disclosed, which is exactly the thing the carve-out spends
   a whole ticket avoiding.
5. **The PR body names the ticket, never the finding.** `Closes YEA-<n>` links and closes it on
   merge; the *what* stays in Linear. A fix whose diff cannot be reviewed without describing the
   exploit in the PR description is one to raise in Linear and stop on, not to narrate publicly.

If the ticket carries `security:approved` but no `workstream:security`, **do not build it** — the
relay applies the first from a Slack message and only this sweep applies the second, so that pair
means something reached the label by a route the fleet does not own. Report it and stop.

## Focus

Alternate between the two weekly runs:

- **Monday — dependency and SAST.** Run `make security`. Every `pip-audit` CVE and every new ruff
  `S`-rule hit is a candidate; CVE bumps are auto lane — after
  `gh pr list --author "app/dependabot" --state open` shows nothing already raising that pin.
  Then check the CodeQL triage PR is moving —
  `gh pr list --label workstream:security --state open`. You do **not** hunt alerts yourself:
  `codeql-triage.yml` owns that, because an alert is state in the GitHub API and a scout reads
  files. One sitting red for more than a week is itself the finding.
- **Thursday — surface review.** Pick one input or output surface added or changed in the last two
  weeks (`git log --since='2 weeks' --name-only -- src/yeaboi/`) and check it against the guardrail
  layers: input validation, redaction on the way to logs, CSP on the way to a browser, `fs_policy`
  on the way to disk.

Both runs: verify the `ARTIFACT_CSP` / `EDIT_CSP` diff test still asserts the *whole* policy.

## Extra stop conditions

- A finding that requires disclosure (an exploitable path in a shipped release) is **not** filed as a
  public GitHub issue. File the Linear ticket, post to `#yeaboi-claude`, and stop. The ✅ that comes
  back is what starts the work — a later run picks it up under **Drain the approved disclosures**
  below, so this run never implements what it just found.

  **`critical: true` does not override this — it points the other way.** The critical flag exists to
  jump the proposal cap, and the fastest route past a queue is a public issue, which is precisely
  what a disclosure may not become. A find that is both critical and disclosable takes this path,
  not the cap bypass.

  The post carries three things and no fourth: that a disclosure exists, where it is written down,
  and what you are being asked to decide.

  ```slack
  🔐 **Security** — disclosure filed · [YEA-94](https://linear.app/yeaboi/issue/YEA-94) · needs your call on scope-vs-remove
  ```

  **Four things this message may never carry**: the exploit path, the module or surface it lives
  in, the version it is exploitable in, and any right-hand fact that quantifies or dates it. Those
  four *are* the disclosure, and a title line leaking one is the same disclosure through a narrower
  pipe. `#yeaboi-claude` is a public channel; it also carries attacker-influenceable text, and
  `cron/slack-relay.md` reads it while holding write credentials.

  **The Linear link is not one of the four, and it is required.** The ticket is private and its
  identifier is already in the line, so a URL for it discloses nothing the message has not already
  said — while without it, the one artefact that holds the finding is the one thing the reader
  cannot reach. This post used to be exempt from rule 3 of `cowork-scribe.md` ("every named thing
  is a link") *altogether*, reasoning that a link-what-you-name grammar pushes toward linking the
  exploit path. Naming the four forbidden things outright is the narrower rule and the one that
  holds: the blanket exemption cost the reader the ticket and bought no secrecy.

  **The decision clause is rule 4** ("one actionable last line"), and it is what makes the ping
  worth receiving. Name the call being asked for — `scope-vs-remove`, `patch-or-revert` — and
  never why it matters or what breaks without it. A message that says only that something was
  found ends in a shrug, and a reader who cannot tell what is wanted from them does nothing.
