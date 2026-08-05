# Models

**Every model choice in this system lives in this file.** Nothing else in `cowork/` or
`.claude/agents/` names a model — they name a *tier*, and this table says what a tier is.
`tests/unit/test_cowork_models.py` enforces that.

Changing what the fleet runs on is one edit to the table below (plus, for the GitHub workflows, one
repository variable — see [Workflows](#workflows)).

## Tiers

| Tier | Dropdown label | Id | For |
|---|---|---|---|
| `heavy` | Fable 5 | `claude-fable-5` | Long-running unattended implementation. **Never security** — see below. |
| `deep` | Opus 5 | `claude-opus-5` | Work the repo lives with: building, reviewing, diagnosing, marketing prose, scouting security |
| `standard` | Sonnet 5 | `claude-sonnet-5` | Bounded judgement over a known input: the other 13 scouts, the scribe, digest ranking, the DoD audit, release notes |
| `fast` | Haiku 4.5 | `claude-haiku-4-5` | Mechanical: read a field, write a field |
| `inherit` | — | — | Take the caller's model. The default for every agent. |

The **dropdown label** is what to pick when registering a routine at
[claude.ai/code/routines](https://claude.ai/code/routines). The **id** is what goes in a `--model`
flag or an agent's `model:` frontmatter.

## Rules

**Aliases, never dated ids.** `claude-sonnet-5`, not `claude-sonnet-5-20260101`. A dated id defeats
the point of one table. `.claude/skills/ci-and-release/SKILL.md` records the 2026-07-30 incident where
pinning dated ids (PR #120) was a misdiagnosis of a 401 that had nothing to do with models — the cost
of an alias moving under you is far smaller than the cost of nine files disagreeing.

**`inherit` is the safe failure.** Agents carry `model: inherit` rather than a pinned tier, so a spawn
that forgets its override degrades to the routine's own deliberately-chosen model. It never silently
drops to something cheap and wrong.

**Security never runs on `heavy`.** Fable 5 automatically reroutes cybersecurity queries to less
capable models. That is fine for a chat and unacceptable for an unattended survey: you would not know
which model actually read `fs_policy.py`, `redaction.py`, or the CSP invariants, and the run would
report as if it had. This applies to `security-sweep` and to any auto-lane item in the `security`
workstream.

## Assignments

### Routines

The 14 sweeps take their tier from [sweep-procedure.md](sweep-procedure.md), which resolves it here.
The rest do model-worthy work in their own session and carry a `**Model**` line.

| Routine | Tier | Why |
|---|---|---|
| `cron/security-sweep.md` | `deep` | A missed guardrail gap is the one finding nobody else catches |
| the other 13 `cron/*-sweep.md` | `standard` | Bounded survey of declared paths against a written charter |
| `cron/marketing-weekly.md` | `deep` | Drafts prose inline rather than delegating; the prose is the output |
| `cron/digest.md` | `standard` | Ranks ~20 issue titles and writes one message |
| `cron/slack-relay.md` | `fast` | Grammar-first matching against an allowlist, 17 times a day; it also answers free text, but its rule for anything unsure is ask-in-thread, never act — the judgement being relayed was the human's. Raise the tier if parses misfire |
| `events/pr-opened-dod-audit.md` | `standard` | A nine-item checklist against a diff |
| `events/pr-merged-close-loop.md` | `fast` | Linear → Done, one Slack line, a Notion page from a merged PR |
| `events/release-published-announce.md` | `standard` | Writes notes from commits, which needs judgement about what mattered |

### Agents

Every agent stays `model: inherit`. These are the tiers the **caller** passes on spawn.

| Agent | Tier | Why |
|---|---|---|
| `cowork-scout` | `standard`, `deep` for security | Highest-frequency step in the system; a weak scout poisons the proposal queue |
| `cowork-builder` | `deep` | Writes code that becomes a PR |
| `cowork-scribe` | `standard` | Formulaic, but it drives connector tools where a mistake files the wrong thing in a real tracker |
| `code-reviewer` | `deep` | The only thing standing between the builder and a merge |
| `test-writer` | `standard` | Writes to a convention documented in a skill |
| `migrator` | `standard` | Applies one specified mechanical change to a file list |
| `pr-fixer` | `standard` | Bounded: one red check, one failure log |

### Workflows

A YAML file cannot read this table, so the workflows centralise through **GitHub repository
variables** instead (Settings → Secrets and variables → Actions → *Variables*, not Secrets — these
are not sensitive, and masking them in logs would only make failures harder to read).

| Variable | Value |
|---|---|
| `YEABOI_MODEL_HEAVY` | `claude-fable-5` |
| `YEABOI_MODEL_DEEP` | `claude-opus-5` |
| `YEABOI_MODEL_STANDARD` | `claude-sonnet-5` |
| `YEABOI_MODEL_FAST` | `claude-haiku-4-5` |

| Workflow job | Tier | Why |
|---|---|---|
| `claude.yml` `implement` | `heavy` | The one job Fable is actually for: human-selected, unattended, up to 110 turns through Linear ticket → implement → DoD gate → code-reviewer → PR, with CI and your merge as the net |
| `claude.yml` `claude` (assist) | `deep` | Open-ended, and a person is waiting on it |
| `claude-review.yml` | `deep` | Judgement quality is the whole point; volume is already gated to green PRs |
| `ci-sentinel.yml` | `deep` | Diagnosing a red `main` from a log is the hard case |
| `dependabot-auto.yml` | `standard` | Reads a diff and a changelog, decides merge or escalate |
| `feedback-remediation.yml` | `standard` | Classifies and routes issues against a written rubric |
| `auto-version.yml` | `fast` | Reads a diff, picks patch/minor/major |
| `flaky-test-hunter.yml` | `fast` | Counts retries and files one proposal into the cowork queue |

**The `||` fallback encodes prior behaviour, not the tier.** Each job is written as:

```yaml
claude_args: >-
  --model ${{ vars.YEABOI_MODEL_STANDARD || 'claude-sonnet-5' }} --max-turns 30
```

A `>-` block scalar, not a quoted one: the expression already contains single quotes around the
fallback, and nesting those inside a single-quoted YAML scalar does not parse. Every workflow uses
the block form.

An unset variable renders empty and a bare `--model ` breaks the argument, so the fallback is
mandatory. It is deliberately pinned to **what that job ran on before this table existed** — so
forgetting to set a variable reverts one job to its old behaviour rather than breaking it or
surprising you with a bill. Fallbacks are not kept in sync with the tiers, and are not part of this
contract.

## Changing a model

1. Edit the tier row above.
2. If workflows are affected, update the repository variable to match.
3. If routines are affected, change the model on each affected routine at claude.ai/code/routines —
   the dropdown is account-side and no repo file can set it. The **Tier** column in
   [README.md](README.md#registered-routines) says which routines those are.
