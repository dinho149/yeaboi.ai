# PR opened — DoD audit

**Trigger** — GitHub event, pull request `opened` and `synchronized`
**Filters** — skip drafts; skip authors `dependabot[bot]` and `github-actions[bot]`
**Model** — `standard` ([models.md](../../models.md))

`claude-review.yml` already reviews the *code*. This checks the *process* — whether the nine items in
[definition-of-done.md](../../definition-of-done.md) are actually met.

## Run

1. `gh pr view <n> --json title,body,files,labels,author` and `gh pr diff <n>`.
2. Walk the DoD. For each item, decide **met / unmet / not applicable**, using evidence from the diff
   and not from the PR description's claims:
   - **1 Linear** — a Linear link in the body or an attachment on the ticket
   - **2 Tests** — new/changed functions in the diff that have no corresponding test change
   - **3–4 Lint + security** — the CI `lint` and `security` job conclusions
   - **5 Surface parity** — a new capability with no `CAPABILITIES` row, or a card/tool/flag added
     without one
   - **6 Observability** — a new user action with no `logger.info()`, or a hardcoded `~/.yeaboi`
   - **7 Web bundles** — `frontend/` changed but `src/yeaboi/web/static/` did not
   - **8–9 Notion + Slack** — **always "pending merge"**, never unmet. They are done by
     `pr-merged-close-loop`.
3. Post **one** comment via `cowork-scribe`: a checklist with a one-line reason for every unmet item
   and the exact file or command that would settle it. If everything is met or pending, post
   `DoD: all items met or pending merge.` and nothing more.
4. On `synchronized`, **edit the existing comment** rather than adding another. Find it by its
   `<!-- cowork-dod -->` marker.

## Stop conditions

- This routine is advisory. Never request changes, never block, never fail a check, never merge.
- Never label a PR `claude-implement` or apply any label at all.
- Judge the diff, not the author. No commentary on style or approach — that is `claude-review.yml`'s job.
