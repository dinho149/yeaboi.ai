# integrations

**Owns** — `src/yeaboi/tools/` (jira, azure_devops, github, confluence, notion, calendar_tools,
local_git, codebase, risk, llm_tools), `src/yeaboi/jira_sync.py`, `azdevops_sync.py`,
`export_targets.py`, `ticket_text.py`, `markdown_convert.py` (Markdown → Notion blocks / Confluence
XHTML), `tests/contract/` and its cassettes

**Skills** — `.claude/skills/agent-and-state/SKILL.md` (tool conventions)

**Cadence** — Tue 06:30 UTC

## Standing concerns

- **Contract-test drift.** `tests/contract/` replays recorded responses. When a provider changes a
  field shape, the cassette still passes and production breaks. Compare cassettes against current
  provider docs; a stale cassette is a real finding.
- **Rate limits and pagination.** `azure_devops.py` is 2,750 LOC and has had truncation bugs before
  (the AzDO refetch path). Any list call without explicit paging is suspect.
- **Auth failure paths** must log at `warning`/`error` with enough context to diagnose, and must
  never log the credential.
- **Write-back symmetry** — `jira_sync.py` and `azdevops_sync.py` should stay behaviourally paired.
  A capability that exists on one and not the other is a proposal.
- **`tools/team_learning.py` is not yours** despite living in `tools/` — it belongs to **analysis**.

## Auto lane, in practice

A broken or flaky contract test, a missing pagination guard with a cassette to prove it, dead code in
a retired provider path. New provider capabilities always propose.

## Opportunity space

Where a `[feature]`/`[improvement]` find is most likely to be real here: third-party edges a user
hits silently (truncated lists, rate limits swallowed, auth that expires without a message), setup
steps that could self-verify instead of failing on first use, and capabilities one provider has that
its sibling lacks for no recorded reason. The evidence bar in `cowork-scout.md` applies — name the
friction, the gap, or the repeated step.

## Out of scope

`tools/team_learning.py` (analysis). Slack/email delivery, which lives in `standup/delivery.py` and
`performance/delivery.py` and belongs to those modes.
