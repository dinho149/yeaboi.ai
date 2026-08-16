# The crew

Three sub-agents, defined in `.claude/agents/`, available in every routine run.

| Agent | Tier | Does | Never does |
|---|---|---|---|
| `cowork-scout` | `standard` (`deep` for security and integrations) | Surveys one workstream's paths, ranks finds by impact / effort / risk, classifies each `auto` or `propose` | Edits files. Posts anywhere. |
| `cowork-scribe` | `standard` | Every *authored* outbound write: Linear tickets + comments, GitHub issues + comments, Slack posts, Notion pages | Touches source code. Applies `claude-implement`. |
| `cowork-builder` | `deep` | Implements one item inside its workstream's paths, runs the DoD gate, opens the PR | Posts to Linear/Slack/Notion. Leaves its paths. |

Existing agents are reused unchanged: `code-reviewer` (`deep` — the builder spawns it before opening
a PR, exactly as `/ship` does), `test-writer`, `pr-fixer`.

Tiers are names, not models. [models.md](models.md) says what each one currently is; every agent
carries `model: inherit` so the caller decides.

## Why the scribe is separate

**One format, twenty-eight routines.** Every ticket, digest, and Notion page has one shape because
one agent writes them all. Twenty-eight routines each doing their own comms drift into twenty-eight
different ticket styles within a month.

Two writers are not the scribe, and the boundary in both cases is *authorship*: the scribe is the
only agent that **composes** comms.

`routines/cron/slack-relay.md` writes nothing it composed — marker reactions, one-line acks, audit
comments, and the label/close verbs a verified human asked for. Routing those through the scribe
would not work anyway (the scribe is itself forbidden from applying `claude-implement`), and
spawning a `standard`-tier agent from an hourly `fast` poller would defeat the relay's
read-and-exit cost model.

[check-in.md](check-in.md) is the second, and it is the same case one step further: the routine
does not compose its check-in *at all*. `scripts/cowork_checkin.py` prints the finished two lines
and the routine posts them verbatim, the way `cron/day-ahead.md` posts the lines `--agenda` hands
it. There is no wording to keep consistent, so there is nothing for a single writer to protect —
and the arithmetic runs the other way: every routine checks in, so routing it through the scribe
would spawn a `standard`-tier agent twenty-odd times a day to retype two lines it was given.

**Connector mechanics stay out of the builder's context**, where they would compete with the code.

**Comms can be retried.** A failed Slack post is re-run on its own instead of re-entering a
half-finished implementation to get at the one step that failed.

## Handoff shape

The scout returns a list; nothing else is passed between agents implicitly.

```
{ "workstream": "security",
  "finds": [ { "title", "type": "bug|feature|improvement|chore|docs|security",
               "why_it_matters", "paths", "impact": 1-5,
               "effort": "S|M|L", "risk": "low|med|high", "lane": "auto|propose" } ] }
```

`auto` finds go to the builder one at a time. `propose` finds go to the scribe as a batch.
