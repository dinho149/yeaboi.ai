"""Canonical wording for features that ship in beta.

A feature is "beta" here when it works end to end but its *output* has not been
validated against real-world data yet. That is a different claim from "coming
soon" (not usable) and from "new" (usable and verified, just recent), and it
deserves its own vocabulary so every surface says the same thing.

This module is deliberately **import-free**. Every surface pulls from it —
``cli.py``, ``mcp/tools_performance.py``, the TUI screens, the tip registry —
and two of those are startup-latency sensitive:

* The constants cannot live in ``performance/``. ``mcp/tools_performance.py``
  reaches the engines through function-level imports precisely so the MCP
  server boots without them; a module-scope ``from yeaboi.performance.beta
  import …`` would execute ``performance/__init__.py``, which eagerly imports
  ``context``/``roster``/``store`` and through them ``langchain_core`` — about
  0.2s onto every server start. (Note ``performance/__init__.py``'s own
  docstring still claims importing the package "never drags in langchain";
  that is stale — ``import yeaboi.performance`` does pull ``langchain_core``
  today. Its lazy ``__getattr__`` defers only the three engine entry points.)
* ``ui/shared/`` is out too — ``mcp/`` importing a UI module inverts the layering.

``tests/unit/test_beta.py`` AST-scans this file and fails if an import ever
appears, because the cost would be silent.

HTML, Markdown and the plugin ``SKILL.md`` cannot import Python, so their copies
are hand-written; ``tests/unit/test_beta_surfaces.py`` pins them to these values.
"""

# The badge/pill/chip text. Short, uppercase, rendered as an inverse-video chip
# in the TUI and a rounded pill on the docs site.
BETA_LABEL = "BETA"

# The inline qualifier for running prose and one-line help strings, matching the
# lowercase-parenthetical house style of the other CLI subcommand help lines.
BETA_TAG = "(beta)"

# The load-bearing claim, kept short enough to survive HTML re-wrapping — this
# is the token the cross-surface sync test greps for in the docs and SKILL.md.
PERFORMANCE_BETA_PHRASE = "not yet verified against real delivery data"

# The full caveat. One sentence of status, one of instruction. "a draft to edit,
# not a verdict" is lifted from the performance plugin skill's existing voice so
# the caveat reads as part of the product rather than bolted on.
PERFORMANCE_BETA_NOTICE = (
    "Performance mode is in beta — its output is not yet verified against real "
    "delivery data. Treat every 1:1 prep, summary and review as a draft to edit, "
    "not a verdict."
)

# The Agents family's load-bearing claim + full caveat, mirroring the
# performance pair above: costs are local estimates, detection is a lower
# bound, and none of it has been validated against a real team's bill yet.
AGENTWATCH_BETA_PHRASE = "estimates from local session logs"
AGENTWATCH_BETA_NOTICE = (
    "The Agents modes are in beta — costs and activity are estimates from local "
    "session logs and public rate tables, not your provider's bill. Treat every "
    "number as an estimate to verify, not an invoice."
)

# Ship's pair: the claim is about what it DOES (spends quota, writes branches),
# not about output quality — the approval gate is the mitigation, so the
# instruction names it.
SHIP_BETA_PHRASE = "drives a real coding agent against your repository"
SHIP_BETA_NOTICE = (
    "Ship mode is in beta — it drives a real coding agent against your repository "
    "and spends real API quota. Nothing is pushed without your approval at the "
    "gate; review the diff like a stranger wrote it."
)

WEEKLY_REVIEW_BETA_NOTICE = (
    "Weekly Review is in beta — it drafts a review of your own week from your "
    "standups, shipped work and sprint plan. Read it as a draft about your week, "
    "not a verdict on it."
)

# ── The one-time entry gate ──────────────────────────────────────────────────
# A mode card's BETA chip says *that* a mode is unverified; this is the copy
# that says how, shown once the first time the mode is opened. It lives here,
# beside the notices, because two surfaces now render it — the TUI's full-screen
# gate and the desktop's modal — and a caveat that differs between them is
# worse than no caveat.
#
# Copy rule: name what can actually go wrong and what stays local. A generic
# "this feature is experimental" tells the user nothing they can act on, and
# reads as liability cover rather than information.
#
# Plain dicts and tuples, because this module must stay import-free (see above);
# each surface wraps them in whatever shape it renders.

BETA_GATE_SUBTITLE = "Beta — worth thirty seconds"

BETA_GATE_FOOTER = "You'll only see this once — the BETA tag stays on the page."

BETA_GATE_COPY: dict[str, dict] = {
    "weekly-review": {
        "headline": "Weekly Review is in beta.",
        "body": (
            "The review is drafted from your own standups, the tickets you closed and",
            "your sprint plan — a draft about your week, not a verdict on it.",
            "",
            "The on-track line is computed from the numbers, never by the model; the",
            "prose is. A week with no standups produces a thin review, and says so.",
            "",
            "Nothing is sent to anyone. Exports stay on this machine under",
            "~/.yeaboi/exports/solo.",
        ),
    },
    "performance": {
        "headline": "Performance is in beta.",
        "body": (
            "1:1 preps, completions and 6-month reviews are drafted from your tracker",
            "data — read them as a starting point, not an assessment.",
            "",
            "Coverage depends on how much of the work is actually on the board; sparse",
            "boards produce thin, sometimes misleading signals.",
            "",
            "Nothing is sent to anyone automatically. Exports stay on this machine",
            "under ~/.yeaboi/exports/performance.",
        ),
    },
    "ship": {
        "headline": "Ship is in beta.",
        "body": (
            "Ship launches a real coding agent (Claude Code) against a repository you",
            "name, on an isolated branch cut from a clean tree. It spends real API",
            "quota — a launch budget caps runs at 2 per hour, 12 per day.",
            "",
            "Nothing merges by itself: the branch is pushed and the pull request is",
            "opened only after you approve the diff at the gate.",
            "",
            "Review the diff like a stranger wrote it, because one did.",
        ),
    },
    "agent-usage": {
        "headline": "Agent Usage is in beta.",
        "body": (
            "Costs are estimates: token counts come from your local agent session logs",
            "(Claude Code), priced from a dated public rate table — not your",
            "provider's bill. Unknown models are priced at a mid-tier guess and flagged.",
            "",
            "Only aggregates are stored. Session transcripts are read on this machine",
            "and never copied, uploaded, or persisted.",
        ),
    },
    "agent-advisor": {
        "headline": "Agent Advisor is in beta.",
        "body": (
            "Recoverable-spend figures are estimates of opportunity, not promised",
            "savings: tokens are approximated from bytes and priced at your window's",
            "blended input rate, and every mechanism count is a floor.",
            "",
            "Transcripts and CLAUDE.md files are read on this machine only. The report",
            "keeps counts, byte totals and file paths — never their content.",
        ),
    },
    "agent-standup": {
        "headline": "Agent Standup is in beta.",
        "body": (
            "The digest combines local agent sessions with agent-authored commits and",
            "PRs found in your trackers. Detection is a lower bound — agents that leave",
            "no marker are invisible, so absence of activity is not proof of idleness.",
            "",
            "Nothing is sent to anyone unless you deliver it. Exports stay on this",
            "machine under ~/.yeaboi/exports/agentwatch.",
        ),
    },
    "agent-security": {
        "headline": "Agent Security is in beta.",
        "body": (
            "Checks are deterministic pattern scans over your agent configs and session",
            "logs — an indicator, not a security audit. A clean report means no known",
            "pattern matched, not that your setup is safe.",
            "",
            "Findings reference file and line only; matched secrets are never stored",
            "or displayed. Everything stays on this machine.",
        ),
    },
}

# Amber caution. Deliberately *not* the warm gold (226,186,96) used by the NEW
# badge: beta is a warning, new is a freshness cue, and the two appear side by
# side in the tips gallery where they must not read as the same thing. The docs
# site's ``.beta-pill`` uses the same rgb() triple.
BETA_RGB = (224, 138, 72)
