# The crew

Three sub-agents, defined in `.claude/agents/`, available in every routine run.

| Agent | Tier | Does | Never does |
|---|---|---|---|
| `cowork-scout` | `standard` (`deep` for security) | Surveys one workstream's paths, ranks finds by impact / effort / risk, classifies each `auto` or `propose` | Edits files. Posts anywhere. |
| `cowork-scribe` | `standard` | Every outbound write: Linear tickets + comments, GitHub issues + comments, Slack posts, Notion pages | Touches source code. Applies `claude-implement`. |
| `cowork-builder` | `deep` | Implements one item inside its workstream's paths, runs the DoD gate, opens the PR | Posts to Linear/Slack/Notion. Leaves its paths. |

Existing agents are reused unchanged: `code-reviewer` (`deep` — the builder spawns it before opening
a PR, exactly as `/ship` does), `test-writer`, `pr-fixer`.

Tiers are names, not models. [models.md](models.md) says what each one currently is; every agent
carries `model: inherit` so the caller decides.

## Why the scribe is separate

**One format, nineteen routines.** Every ticket, digest, and Notion page has one shape because one
agent writes them all. Nineteen routines each doing their own comms drift into nineteen different
ticket styles within a month.

**Connector mechanics stay out of the builder's context**, where they would compete with the code.

**Comms can be retried.** A failed Slack post is re-run on its own instead of re-entering a
half-finished implementation to get at the one step that failed.

## Handoff shape

The scout returns a list; nothing else is passed between agents implicitly.

```
{ "workstream": "security",
  "finds": [ { "title", "why_it_matters", "paths", "impact": 1-5,
               "effort": "S|M|L", "risk": "low|med|high", "lane": "auto|propose" } ] }
```

`auto` finds go to the builder one at a time. `propose` finds go to the scribe as a batch.
