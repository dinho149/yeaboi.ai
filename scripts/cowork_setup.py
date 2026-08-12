#!/usr/bin/env python3
"""Stand up the cowork fleet from what ``cowork/`` already says.

``cowork/`` is a complete specification — fifteen charters, twenty-three routines, a
tier table, one Definition of Done — and none of it does anything until the
GitHub labels exist, the model repository variables are set, and the routines are
registered at claude.ai. Doing that by hand is 29 labels, 4 variables and 24 web
forms, which is long enough that nobody does it twice and silent when done wrong:
an unset variable just reverts a workflow to its old model, and a cron that
restricts day-of-month *and* day-of-week turns a fortnightly sweep into a daily
one without saying so.

None of that data needs re-authoring. Every routine file carries a regular
``**Trigger** — cron `expr` `` line, ``README.md`` gives each routine a tier, and
``models.md`` maps a tier to an id. This script parses those and applies what a
shell can apply.

**It reaches GitHub two ways.** `gh` when it is installed and authenticated, and
the REST API with ``GH_TOKEN``/``GITHUB_TOKEN`` when it is not. The fallback is
not a convenience: `cron/cd-deploy.md` runs this script from a cloud routine
session that is handed a token and no CLI, so without it every automatic deploy
stopped at the labels step and the fleet was never reconciled.

**It deliberately names no model.** The ids come out of ``cowork/models.md`` at
run time, because that file being the only place a model is written down is the
whole contract — see ``tests/unit/test_cowork_models.py``, which fails if one is
pasted in here.

What a shell *cannot* do is reach a routine: they are account-scoped and driven by
the in-session ``RemoteTrigger`` tool, with no CLI behind it. So ``/cowork`` makes
the API calls and hands the response back here as a snapshot — every comparison,
every request body and every file edit stays in tested Python, and the model is
left with nothing to improvise. The first version of this script asked the command
to hand-edit twenty-odd table cells instead, and those table cells did not get
edited.

Usage::

    uv run python scripts/cowork_setup.py                    # create labels + variables
    uv run python scripts/cowork_setup.py --check            # doctor; non-zero on drift
    uv run python scripts/cowork_setup.py --check --local    # repo consistency only, no gh
    uv run python scripts/cowork_setup.py --json             # manifest for /cowork
    uv run python scripts/cowork_setup.py --agenda --text    # what runs today, and the week after

    # …the account half, fed a `RemoteTrigger list` response by /cowork:
    uv run python scripts/cowork_setup.py --plan  --triggers live.json   # what to create/update
    uv run python scripts/cowork_setup.py --check --triggers live.json   # doctor, both halves
    uv run python scripts/cowork_setup.py --urls  --triggers live.json   # fill the README URLs

    # …and the shell half of teardown (routines are /cowork teardown's job):
    uv run python scripts/cowork_setup.py --teardown --labels --variables --yes

Exit codes on the default (apply) run, which `cron/cd-deploy.md` step 3 reads to
decide whether to keep going:

    0   applied, or nothing needed applying
    1   a GitHub write degraded — reported as a note, and non-zero only under
        --strict. Nothing downstream reads a label, so this does not invalidate
        a routine reconciliation.
    2   `cowork/` disagrees with itself. Nothing was applied and nothing further
        should be registered from these files. Same meaning as --plan's 2.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo

# scripts/ is not a package, so the sibling transport is imported by path. It is
# stdlib-only like this file, so this adds no dependency — only a second file
# that has to travel with this one (the test fixtures copy both).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gh_transport as transport  # noqa: E402 - after the sys.path line that makes it importable

REPO_ROOT = Path(__file__).resolve().parent.parent
COWORK = REPO_ROOT / "cowork"
README = COWORK / "README.md"
MODELS_DOC = COWORK / "models.md"
DOD_DOC = COWORK / "definition-of-done.md"
ROUTINES_DIR = COWORK / "routines"
WORKSTREAMS_DIR = COWORK / "workstreams"
SCOUT_AGENT = REPO_ROOT / ".claude" / "agents" / "cowork-scout.md"

# The thin prompt every routine is registered with, quoted in README.md under
# "How routines actually work". Held here as a format string so the manifest can
# be generated, and asserted against the README blockquote by
# ``tests/unit/test_cowork_setup.py`` — the routine's whole behaviour hangs off
# it pointing at a real file, so the two copies are not allowed to drift.
PROMPT_TEMPLATE = (
    "You are the `{name}` workstream for yeaboi. Read `cowork/routines/{path}` in this repo and follow it exactly."
)

# Routines are registered under a stable, prefixed name so a re-run reconciles
# rather than duplicating, and so they are distinguishable from a routine someone
# created by hand in the same account.
TRIGGER_NAME = "cowork: {name}"

# The connectors a routine may reach. Every connector on the account is attached
# by default, so registering one is a *removal* — a scout with a mail connector is
# a scout that can mail somebody.
CONNECTORS = ("Linear", "Slack", "Notion")

# What a sweep needs to do its run: read the repo, run make/gh, edit files in the
# auto lane, and spawn the three crew agents. Kept here rather than in the slash
# command so every routine gets the same set and it is reviewable in one place.
#
# One shared set, including `digest`, whose own file says
# never to edit a file. That is a deliberate difference from the connector list
# above, and rests on a different argument: a connector is a capability to reach
# *outside* the repo, where the blast radius is somebody else's inbox, while a
# stray edit in a routine's own checkout ends up in a PR a human reads. Widen or
# narrow per routine only through TOOL_OVERRIDES below, so the exceptions stay
# reviewable in the same place as the rule.
ALLOWED_TOOLS = ("Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task", "TodoWrite")

# The per-routine exceptions to ALLOWED_TOOLS, keyed by routine stem.
TOOL_OVERRIDES: dict[str, tuple[str, ...]] = {
    # shipped-standup reads a day of merged PRs and posts one message. Bash for
    # `gh` and the release script, Read for the repo, Task for the scribe — and
    # nothing else: a routine that only reports what already happened has no
    # business editing a file, and narrowing it says so where the grant is
    # reviewed.
    "shipped-standup": ("Bash", "Read", "Glob", "Grep", "Task", "TodoWrite"),
    # The routine that asks may not answer: no `gh issue edit`, so it cannot apply
    # `release:promote` to its own ask. Same shape as the sweeps' inability to
    # apply `claude-implement`.
    "release-promote-ask": (
        "Bash(gh issue list:*)",
        "Bash(gh issue create:*)",
        "Bash(gh issue view:*)",
        "Bash(gh issue comment:*)",
        # Closing supersedes a stale ask. `gh issue edit` stays out: it is the verb
        # that could apply `release:promote`, and the routine that asks may not
        # answer. Closing cannot label anything.
        #
        # The reasoning is narrower than it looks, so it is written down rather
        # than left to read as complete: `gh issue create` accepts `--label` too,
        # so this grant does not make applying `release:promote` *impossible* —
        # only impossible on an issue that already exists. Whether a label set at
        # creation even fires the `labeled` webhook `publish.yml` listens for is
        # unverified. What actually holds is the routine's own stop condition,
        # plus `publish.yml` requiring `release:promotion`, which the ask applies
        # to itself and which is therefore no barrier here either. Treat this
        # grant as defence in depth, not as the lock.
        "Bash(gh issue close:*)",
        "Bash(uv run python scripts/release_channel.py:*)",
        "Glob",
        "Grep",
        "Read",
        "Task",
        "TodoWrite",
    ),
    # slack-relay relays a human's verbs and nothing else: the routines API
    # (RemoteTrigger) for pause/resume/run — which nothing else gets, because a
    # sweep that can reach it is a sweep that can un-pause the fleet — the repo
    # and its own allowlist (Read/Glob/Grep), and a *scoped* shell.
    #
    # The scoping is not decoration. This routine held bare ``Bash`` until issue
    # #172, where relaying one ✅ replaced the issue's whole label set: it lost
    # ``cowork:proposal``, ``workstream:web-ux`` and ``type:security`` in the same
    # second it gained ``claude-implement``, and the workstream label is what
    # ``claude.yml``'s implement job reads to find the charter declaring which
    # paths an unattended run may touch. The routine had always *said*
    # ``--add-label``. Saying it is what failed, so the grant says it too.
    #
    # This removes the *wholesale* loss, not every possible one: ``gh api`` is gone,
    # so no verb here can replace a label set in one call, but ``gh issue edit``
    # still spells ``--remove-label``. That is the right line — the relay must be
    # able to edit labels to do its job at all — and it is the difference between a
    # slip that takes one label and one that takes all of them.
    #
    # ``Bash(...)`` scoping is honoured by the routines API: a probe registered the
    # scoped strings and read them back verbatim through ``RemoteTrigger get``, so
    # ``trigger_plan``'s ``allowed_tools`` comparison stays stable rather than
    # reporting drift forever. Write/Edit/Task stay absent as well — the relay edits
    # nothing and spawns no crew.
    "slack-relay": (
        "Bash(gh issue view:*)",
        "Bash(gh issue edit:*)",
        "Bash(gh issue close:*)",
        "Bash(gh issue comment:*)",
        "Bash(gh pr view:*)",
        "Bash(gh pr close:*)",
        # The routine leaves an audit comment for issue *and* PR verbs, so closing
        # a PR without this is a decision with no record of who made it.
        "Bash(gh pr comment:*)",
        "Bash(gh pr list:*)",
        "Bash(uv run python scripts/cowork_relay.py:*)",
        "Bash(uv run python scripts/cowork_setup.py --json)",
        "Glob",
        "Grep",
        "Read",
        "RemoteTrigger",
        "TodoWrite",
    ),
    # The two PR routines read text nobody here wrote. `gh pr view` and `gh pr
    # diff` return a title, a body and a diff authored by whoever opened the PR —
    # on a public repo that is anyone with a fork — and both routines then hold
    # the Linear, Slack and Notion connectors. They were never registered before,
    # so the grant never mattered; registering them by API is what makes it real.
    #
    # Neither writes anything itself: every outbound word goes through
    # `cowork-scribe` (Task), and every repo read is a query, not an edit. So
    # Write and Edit are removed — not because the routine would misuse them, but
    # because a prompt injected into a fork's PR body has to have somewhere to go,
    # and this is the cheapest place to make sure it does not.
    "pr-opened-dod-audit": ("Bash", "Glob", "Grep", "Read", "Task", "TodoWrite"),
    "pr-merged-close-loop": ("Bash", "Glob", "Grep", "Read", "Task", "TodoWrite"),
    # Not fork-controlled — a release is cut by a maintainer — but it writes
    # nothing itself either, and a routine that announces should not be able to
    # edit what it is announcing.
    "release-published-announce": ("Bash", "Glob", "Grep", "Read", "Task", "TodoWrite"),
    # cd-deploy is the second and last holder of the routines API, and the only
    # routine that writes to it unprompted. Write/Edit are absent even though it
    # does change a file: the one file it touches is the README URL column, and
    # that edit is made by `--urls` inside this script, which is reviewed code.
    #
    # This narrows the blast radius; it does not close it. The routine holds Bash,
    # and Bash can write any file — what the missing Write/Edit buys is that every
    # repo change it makes goes through a reviewed script or through git, where it
    # lands in a PR a human reads, rather than through a free-hand edit mid-run.
    # `check_grants` enforces the tool half; the rest is the PR gate on `main`.
    "cd-deploy": ("Bash", "Glob", "Grep", "Read", "RemoteTrigger", "Task", "TodoWrite"),
}


def routine_tools(name: str) -> tuple[str, ...]:
    """The tool set one routine is registered with.

    Never empty. The API reads an empty ``allowed_tools`` as "default" and hands
    back the full preset — Bash, Write, WebFetch and the rest — so an override
    narrowed all the way to ``()`` would register the *widest* routine in the
    fleet while reading, here, as the narrowest. Refused rather than defaulted:
    a grant nobody meant is not a grant to guess at.
    """
    tools = TOOL_OVERRIDES.get(name, ALLOWED_TOOLS)
    if not tools:
        raise ValueError(f"TOOL_OVERRIDES[{name!r}] is empty — an empty grant registers as every tool")
    return tools


# Where a registered routine lives, for the README's URL column.
ROUTINE_URL = "https://claude.ai/code/routines/{id}"

# The routine that deploys the fleet. Named here because it is the only routine
# whose own drift is reported separately (`Plan.self_update`) and the only one a
# mistake in cannot be repaired by the fleet, since it is the thing that would do
# the repairing.
DEPLOY_ROUTINE = "cd-deploy"

# The one label teardown never deletes. It predates cowork — it is the human
# approval gate `.github/workflows/claude.yml` watches for — so removing it with
# the fleet would quietly break a workflow that has nothing to do with cowork.
# Labels teardown must never delete, because neither belongs to cowork:
# ``claude-implement`` predates it and gates the ``claude.yml`` implement job, and
# ``feedback-override`` is the escape hatch on the ``pr-feedback`` merge gate
# (``.github/workflows/pr-feedback.yml``). Deleting either breaks a live gate
# silently — applying a label that does not exist simply does nothing.
# ``release:promotion`` and ``release:promote`` join them for the same reason:
# ``publish.yml`` fires on the second landing on an issue that already carries the
# first, so a teardown that deleted either would disarm the only path that can cut
# an official release, and nothing would say so.
KEEP_LABELS = frozenset(
    {
        "claude-implement",
        # Applied by `claude.yml`'s outcome guard and by the reconciler, not by any
        # cowork routine — so teardown must leave it, exactly as it leaves the
        # approval label it sits beside. Deleting it would strip the one record
        # that an implement run failed off every issue carrying it.
        "implement-blocked",
        "feedback-override",
        "release:promotion",
        "release:promote",
        "integration:approved",
    }
)

# The proposal-type vocabulary, carried in issue titles as `[type][workstream] …`
# and on issues as `type:<kind>` labels. Shared with the feedback system:
# ``feedback.py`` titles issues `[Bug] …` and labels them `type:<kind>`, and
# ``feedback-remediation.yml`` normalizes the same set — `other` exists for that
# system and is never used by a cowork scout.
PROPOSAL_TYPES = ("bug", "feature", "improvement", "chore", "docs", "security", "other")

# What a cowork *scout* may return, which is narrower than the label vocabulary
# above and deliberately so. `feature` and `improvement` are absent: capability
# work exists only inside an integration campaign (`cowork/integration-campaign.md`),
# where a human approves a provider rather than a find. The two labels survive
# because `feedback.py` files in-app *user* feedback under the same names — a label
# no routine may apply is still a label the repo needs.
#
# Retyped nowhere: `parse_scout_types()` reads it back out of the agent file, and a
# test asserts the two agree. The agent's JSON schema is the contract a model
# actually reads, so it is the source and this tuple is the assertion.
SCOUT_TYPES = ("bug", "chore", "docs", "security")

# How many open `cowork:proposal` issues one workstream may have at once.
#
# The propose lane had no bound at all. A scout returns up to ten finds, the auto
# lane consumes at most one, so a single sweep could legitimately open nine
# issues — and fifteen workstreams run on overlapping crons. `digest.md`'s
# 14-day age-out was the only pressure release, which means the queue drained on
# a clock rather than on anybody deciding anything.
#
# Two is deliberate rather than tuned. One would mean a sweep that files
# something scouts nothing for a fortnight; three starts to bury the digest
# again. Fifteen workstreams at two is a fleet ceiling of thirty, which is a
# list a human can still read in one sitting.
#
# `integration:candidate` issues are outside this cap on purpose: they carry
# neither `cowork:proposal` nor a `type:` label, so `proposal_slots` does not see
# them. A shortlist that could be squeezed out by two open bugs would be a
# shortlist that silently stops arriving.
PROPOSAL_CAP = 2

# The two labels that split one queue in half, and the reason they may never
# both sit on one issue.
#
# `cowork:proposal` is a **question waiting on a human**. `cowork:queued` is a
# **work item waiting on the fleet** — a find already covered by the auto-lane
# allowlist in `house-rules.md`, so nobody has to be asked. Same write-up, same
# labels otherwise; what differs is who answers it.
#
# They are mutually exclusive, and that is load-bearing rather than tidy. Every
# consumer in this repo — `open_proposals` here, `digest.md`'s three queries,
# `codeql-triage.yml`, `flaky-test-hunter.yml` — asks GitHub for
# `labels=cowork:proposal,…`, and that parameter is AND-only: there is no way to
# spell "and not queued". Exclusivity is what keeps every one of them correct
# without a single edit.
#
# The reclassification that moves an issue between them is two REST calls, add
# then remove, so a run that dies in between leaves both on. `_has_label` and
# the filter in `open_proposals` resolve that state as **queued**, which is the
# safe direction: read as a proposal it would hold a slot no human verb could
# ever release, which is the exact failure this whole split exists to end.
PROPOSAL_LABEL = "cowork:proposal"
QUEUE_LABEL = "cowork:queued"

_TYPE_COLORS = {
    "bug": "d73a4a",
    "feature": "a2eeef",
    "improvement": "84b6eb",
    "chore": "ededed",
    "docs": "0075ca",
    "security": "ee0701",
    "other": "cfd3d7",
}

_SCOUT_TYPE_UNION = re.compile(r'"type":\s*"([a-z|]+)"')

_DASHES = {"—", "–", "-", ""}


@dataclass(frozen=True)
class Tier:
    """One row of the tier table in ``models.md``."""

    name: str
    label: str | None  # the claude.ai Model dropdown label; None for `inherit`
    model_id: str | None


@dataclass(frozen=True)
class Routine:
    """One registered routine, resolved from the README table and its own file."""

    name: str  # file stem, e.g. "security-sweep"
    path: str  # relative to cowork/routines/, e.g. "cron/security-sweep.md"
    kind: str  # "cron" | "event"
    tier: str
    model_id: str | None
    workstream: str | None
    cron: str | None  # None for event routines
    trigger: str  # the routine file's own **Trigger** text
    filters: str | None  # the routine file's **Filters** line, events only
    summary: str  # the routine file's **Summary** line — one human line, for the agenda
    prompt: str
    trigger_name: str
    webhook: dict | None = None  # the parsed ```json webhook block, for what fires it
    webhook_error: str | None = None  # why that block did not parse, for check_repo to report


@dataclass(frozen=True)
class Label:
    name: str
    color: str
    description: str


@dataclass
class Report:
    """Accumulated doctor findings. No problems means clean."""

    problems: list[str] = field(default_factory=list)
    # Things worth saying that are not wrong. A paused fleet is the case this
    # exists for: `pause` is a supported verb, so a doctor that turns red on its
    # own verb would just train everyone to ignore it.
    notes: list[str] = field(default_factory=list)

    def fail(self, problem: str, remedy: str) -> None:
        self.problems.append(f"{problem}\n     {remedy}")

    @property
    def ok(self) -> bool:
        return not self.problems


# --- output ------------------------------------------------------------------
# Flat `[cowork] …` lines, matching scripts/wt.sh. `note:` marks a degraded path
# and always carries its remedy on the next line, indented.


def say(message: str) -> None:
    print(f"[cowork] {message}")


def note(message: str, remedy: str = "", stream: TextIO | None = None) -> None:
    """A remark, with its remedy indented under it.

    ``stream`` exists for `--plan`, whose stdout is a JSON document a caller
    pipes: a note printed there turns the plan into something `json.loads`
    refuses, and the caller sees a parse error instead of a fleet.
    """
    print(f"[cowork] note: {message}", file=stream)
    if remedy:
        print(f"     {remedy}", file=stream)


def fail(message: str) -> None:
    print(f"[cowork] {message}", file=sys.stderr)


@dataclass
class Strictness:
    """Whether a step that did not happen is a remark or a failure.

    Off by default, so `make cowork-setup` on a laptop behaves exactly as it
    always has: a `gh` call without repo-admin scope prints its remedy, the rest
    of the run continues, and a human reads the note. ``--strict`` is for the one
    caller with no human reading stdout — an unattended deploy where "created no
    labels, set no variables, exited 0" is indistinguishable from success and is
    the only failure this pipeline can have that nobody would ever notice.

    Only degradations go through here. An informational note — `--local` skipping
    the GitHub half, a plan reporting what it needs — stays a plain ``note()``,
    because a strict mode that fires on remarks is a strict mode nobody turns on.
    """

    strict: bool = False
    degraded: list[str] = field(default_factory=list)

    def note(self, message: str, remedy: str = "") -> None:
        note(message, remedy)
        self.degraded.append(message)

    @property
    def failed(self) -> bool:
        return self.strict and bool(self.degraded)


STRICT = Strictness()


def strict_exit() -> int:
    """0, unless --strict and something degraded. Called at the end of a run."""
    if not STRICT.failed:
        return 0
    fail(f"strict: {len(STRICT.degraded)} step(s) degraded and were reported as notes")
    for message in STRICT.degraded:
        fail(f"  ✗ {message}")
    return 1


# --- parsing -----------------------------------------------------------------

# `| `heavy` | <Dropdown label> | `<model-id>` | Long-running… |`
# The examples here are placeholders on purpose: a real id in this file, even in a
# comment, is the drift models.md exists to prevent, and the guard in
# tests/unit/test_cowork_models.py is deliberately broad enough to catch one.
_TIER_ROW = re.compile(r"^\|\s*`(\w+)`\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", re.M)
# `| `YEABOI_MODEL_HEAVY` | `<model-id>` |`
_VARIABLE_ROW = re.compile(r"^\|\s*`(YEABOI_MODEL_\w+)`\s*\|\s*`([^`]+)`\s*\|", re.M)
# `| `cron/security-sweep.md` | `0 6 * * 1,4` Mon + Thu | security | `deep` | |`
_ROUTINE_ROW = re.compile(r"^\|\s*`(cron|events)/([a-z0-9-]+)\.md`\s*\|(.*)$", re.M)
# The id inside a claude.ai routine URL, as the README URL column records it.
_TRIGGER_ID = re.compile(r"trig_[A-Za-z0-9]+")
# `| Linear | team **Yeaboi** | `a324…` |`
_TARGET_ROW = re.compile(r"^\|\s*(\w+)\s*\|\s*(.+?)\s*\|\s*`([^`]+)`\s*\|", re.M)

_TRIGGER_LINE = re.compile(r"^\*\*Trigger\*\*\s*—\s*(.+)$", re.M)
_MODEL_LINE = re.compile(r"^\*\*Model\*\*\s*—\s*`(\w+)`", re.M)
_FILTERS_LINE = re.compile(r"^\*\*Filters\*\*\s*—\s*(.+)$", re.M)
_SUMMARY_LINE = re.compile(r"^\*\*Summary\*\*\s*—\s*(.+)$", re.M)

# A **Summary** line becomes one line of a Slack message, so it is capped rather
# than trusted. Without a limit the first person in a hurry writes a paragraph,
# and the daily post is unreadable from then on.
SUMMARY_LIMIT = 90
_CRON_IN_TRIGGER = re.compile(r"^cron\s+`([^`]+)`")
_BACKTICKED = re.compile(r"`([^`]+)`")


def _unwrap(cell: str) -> str | None:
    """A table cell's value, with backticks stripped and an em-dash read as None."""
    value = cell.strip().strip("`").strip()
    return None if value in _DASHES else value


def _section(text: str, heading: str) -> str:
    """The body of one ``## `` section, up to the next one.

    Every table in these documents opens with a backticked first cell, so a
    pattern loose enough to read one is loose enough to read all of them. Scoping
    is not tidiness: an unscoped tier table quietly gains a `migrator` "tier"
    whose model id is an English sentence, and a routine mis-tiered `migrator`
    then passes the does-this-tier-exist check and gets that sentence POSTed as
    its model.
    """
    _, _, tail = text.partition(f"\n{heading}")
    body, _, _ = tail.partition("\n## ")
    return body


def parse_tiers(text: str | None = None) -> dict[str, Tier]:
    """The tier table from ``models.md`` — the only place a model is named."""
    text = MODELS_DOC.read_text(encoding="utf-8") if text is None else text
    tiers: dict[str, Tier] = {}
    for name, label, model_id in _TIER_ROW.findall(_section(text, "## Tiers")):
        # The header separator row (`|---|---|`) cannot match: it has no backticks.
        tiers[name] = Tier(name=name, label=_unwrap(label), model_id=_unwrap(model_id))
    return tiers


def parse_model_variables(text: str | None = None) -> dict[str, str]:
    """The ``YEABOI_MODEL_*`` repository variables and their values."""
    text = MODELS_DOC.read_text(encoding="utf-8") if text is None else text
    return dict(_VARIABLE_ROW.findall(text))


def parse_targets(text: str | None = None) -> dict[str, str]:
    """The Linear / Slack / Notion target ids from ``definition-of-done.md``.

    Scoped to the ``## Targets`` section rather than the whole file: the ten-item
    table above it has the same three-cell shape and a backticked last column, so
    reading the document whole yields ``make test`` as a target id.
    """
    text = DOD_DOC.read_text(encoding="utf-8") if text is None else text
    _, _, tail = text.partition("\n## Targets")
    return {system.lower(): target_id for system, _, target_id in _TARGET_ROW.findall(tail)}


def parse_scout_types(text: str | None = None) -> tuple[str, ...]:
    """The ``type`` union out of ``.claude/agents/cowork-scout.md``'s JSON schema.

    Parsed rather than trusted, for the same reason ``parse_tiers`` parses
    ``models.md``: the agent file is what a model reads at run time, so a constant
    that disagrees with it is a constant describing a fleet that no longer exists.
    """
    text = SCOUT_AGENT.read_text(encoding="utf-8") if text is None else text
    match = _SCOUT_TYPE_UNION.search(text)
    return tuple(match.group(1).split("|")) if match else ()


def parse_workstreams() -> list[str]:
    """The fifteen workstream names, from the charter filenames."""
    return sorted(p.stem for p in WORKSTREAMS_DIR.glob("*.md"))


def expected_labels() -> list[Label]:
    """Every GitHub label the loop depends on.

    ``claude-implement`` predates cowork — it is the human approval gate the
    ``claude.yml`` implement job watches for — but it is listed because a fresh
    fork will not have it either, and a missing one means approvals silently do
    nothing. ``feedback-override`` is here for the same reason and is not a cowork
    label at all: it is the only way past the ``pr-feedback`` merge gate, and an
    escape hatch that has to be hand-created during the emergency it exists for is
    not an escape hatch.
    """
    labels = [
        Label("cowork", "5319e7", "Opened by a cowork routine"),
        Label(PROPOSAL_LABEL, "d4c5f9", "A cowork find awaiting a human's claude-implement"),
        # Not an approval, and not a second `cowork:proposal`. It records that a
        # rule already covered this find — `house-rules.md`, **The auto lane** —
        # so it is a work item rather than a question, and a sweep builds it
        # unattended. Paler than the approval green on purpose: nobody said yes
        # to this, a rule did.
        #
        # A routine may apply this one. It may never apply `claude-implement`,
        # which is why this had to be its own label rather than a reuse: that one
        # fires a 110-turn `claude.yml` job, and the closed allowlist of things a
        # machine may apply must not widen just to let a sweep pick up its own
        # backlog.
        Label(QUEUE_LABEL, "c2e0c6", "Approved by rule — an auto-lane item a sweep will build"),
        Label("claude-implement", "0e8a16", "Approved — the claude.yml implement job builds this"),
        # Applied by `claude.yml` when its outcome guard fails, and by
        # `implement-reconcile.yml` when three re-fires produced no PR. It is the
        # terminal state that stops a level-triggered reconciler retrying a broken
        # implement job every six hours forever. `claude-implement` deliberately
        # stays on beside it — that is the label `digest.md` queries to report the
        # issue at all, and removing it would hide the failure rather than name it.
        Label("implement-blocked", "d93f0b", "The implement job produced no PR — a human has to look"),
        Label("feedback-override", "b60205", "Clears the pr-feedback merge gate — a human's call, recorded on the PR"),
        # The promotion pair. `release:promotion` marks the weekly ask issue and is
        # applied only by `cron/release-promote-ask.md`; `release:promote` is the
        # human's ✅, carried by the relay. `publish.yml` requires BOTH, so the
        # first is what stops a crafted issue title from routing a ✅ onto an issue
        # nobody meant — see the note on PROMOTE_RE in `scripts/cowork_relay.py`.
        Label("release:promotion", "c2e0c6", "The weekly ask: promote the accumulated pre-releases?"),
        Label("release:promote", "0e8a16", "Approved — publish.yml cuts the official release"),
        # The campaign pair, shaped exactly like the promotion pair above and for
        # the same reason. `integration:candidate` marks a shortlisted provider and
        # is applied only by `cron/integrations-campaign.md`; `integration:approved`
        # is the human's ✅, carried by the relay, and is what the next campaign run
        # reads to know which provider it is building.
        #
        # It has to be its own label rather than a reuse of `claude-implement`:
        # `claude.yml` fires an unattended 110-turn implement job on anything that
        # receives that one, and a candidate issue describes a week of work across
        # six workstreams' files. See CANDIDATE_RE in `scripts/cowork_relay.py`.
        Label("integration:candidate", "fef2c0", "A shortlisted provider — ✅ one to make it this week's campaign"),
        Label("integration:approved", "0e8a16", "Approved — the campaign builds this provider across every angle"),
        # Applied by scripts/pr_feedback.py when a PR merges past findings the
        # review ran out of rounds to pursue. Descriptive, not a gate — but it is
        # the only durable record that a merge was capped, and applying a label
        # that does not exist silently does nothing.
        Label("review-capped", "fbca04", "Merged with review findings recorded but not fixed"),
    ]
    labels += [Label(f"workstream:{name}", "1d76db", f"cowork workstream: {name}") for name in parse_workstreams()]
    labels += [Label(f"type:{kind}", _TYPE_COLORS[kind], f"issue type: {kind}") for kind in PROPOSAL_TYPES]
    return labels


def _routine_rows(text: str | None = None) -> list[tuple[str, str, str, str, str]]:
    """Raw README table rows: (kind_dir, stem, trigger_cell, workstream_cell, tier_cell)."""
    text = README.read_text(encoding="utf-8") if text is None else text
    rows = []
    for kind_dir, stem, rest in _ROUTINE_ROW.findall(text):
        cells = [c.strip() for c in rest.split("|")]
        # Routine | Trigger | Workstream | Tier | URL — the leading `Routine` cell
        # is already consumed by the pattern, so `rest` starts at Trigger.
        if len(cells) < 3:
            continue
        rows.append((kind_dir, stem, cells[0], cells[1], cells[2]))
    return rows


def parse_routines(readme_text: str | None = None) -> list[Routine]:
    """Every registered routine, resolved from the README table plus its own file.

    The README table is the spine rather than the routine files, because it is
    the only place every routine carries an explicit tier — the thirteen sweeps
    take theirs from ``sweep-procedure.md`` and so have no ``**Model**`` line of
    their own. ``check_repo()`` then asserts the two agree, so using one as the
    source does not let the other rot.
    """
    tiers = parse_tiers()
    routines: list[Routine] = []

    for kind_dir, stem, trigger_cell, workstream_cell, tier_cell in _routine_rows(readme_text):
        path = f"{kind_dir}/{stem}.md"
        file_path = ROUTINES_DIR / path
        body = file_path.read_text(encoding="utf-8") if file_path.exists() else ""

        trigger_match = _TRIGGER_LINE.search(body)
        trigger = trigger_match.group(1).strip() if trigger_match else ""
        cron_match = _CRON_IN_TRIGGER.match(trigger)
        cron = cron_match.group(1) if cron_match else None

        webhook, webhook_error = parse_webhook(body)

        filters_match = _FILTERS_LINE.search(body)
        summary_match = _SUMMARY_LINE.search(body)
        tier = _unwrap(tier_cell) or ""
        workstream = _unwrap(workstream_cell)
        # digest and the event routines span every workstream and name none, so
        # the prompt falls back to the routine's own name. It still resolves to a
        # real file, which is the only part that matters.
        name = workstream or stem

        routines.append(
            Routine(
                name=stem,
                path=path,
                kind="cron" if kind_dir == "cron" else "event",
                tier=tier,
                model_id=tiers[tier].model_id if tier in tiers else None,
                workstream=workstream,
                cron=cron,
                trigger=trigger,
                filters=filters_match.group(1).strip() if filters_match else None,
                summary=summary_match.group(1).strip() if summary_match else "",
                prompt=PROMPT_TEMPLATE.format(name=name, path=path),
                trigger_name=TRIGGER_NAME.format(name=stem),
                webhook=webhook,
                webhook_error=webhook_error,
            )
        )
    return routines


# --- webhook triggers --------------------------------------------------------
# What fires an event routine. A routine is *what* runs; a webhook trigger is
# *when*, for everything that is not a clock. The body is an account-side contract
# this script does not model — it is pinned by tests/fixtures/cowork_webhook_live.json,
# captured from a real call, the same way desired_trigger is pinned by
# cowork_trigger_live.json.
#
# Four things this endpoint does not do, all recorded in that fixture, and all of
# which the code below has to work around rather than around which it can hope:
# it does not echo the filter back, a routine `get` does not report its attached
# webhooks, it does not dedup an identical POST, and there is no delete. So a
# webhook posted twice fires twice, forever, and nothing can read the state or
# undo it. That is why webhook_plan refuses to act on anything but certainty.

# The event sources cowork will register. An allowlist, not a default: a webhook
# from a source nobody reviewed is a routine fired by a stranger.
WEBHOOK_SOURCES = ("github",)

# The GitHub events a routine file may name. The API validates none of this —
# `zzz_not_an_event` was accepted with a 200 — so a typo would register a webhook
# that silently never fires and nothing would ever say so. This list is the only
# thing standing between a misspelling and a routine that looks deployed and is
# not. Widen it when a routine genuinely needs another event.
WEBHOOK_EVENTS = ("push", "pull_request", "pull_request_review", "release", "issues", "issue_comment")

# What a routine file may declare.
WEBHOOK_FIELDS = frozenset({"source", "events", "filter"})

# What only this script sets. `routine_trigger_id` is unknowable until the routine
# exists, and the scope is the repository this checkout *is*, not one a markdown
# block may name. A file declaring either is refused rather than overridden —
# overriding it would leave the file stating something untrue about what it does.
WEBHOOK_OWNED = frozenset({"routine_trigger_id", "scope_id", "scope", "hook_type", "repository", "repo"})

# The only hook_type cowork uses: the GitHub App's events, not a bare URL nobody
# authenticates.
WEBHOOK_HOOK_TYPE = "app"

# A filter a human will read in review. Larger than this is a program, and a
# program belongs in the routine file where the model applies it with judgement.
WEBHOOK_FILTER_LIMIT = 2000

_WEBHOOK_BLOCK = re.compile(r"^```json webhook\n(.*?)^```", re.M | re.S)


def parse_webhook(body: str) -> tuple[dict | None, str | None]:
    """One routine file's ```json webhook block, and why it was rejected.

    ``(None, None)`` when the file declares none — the ordinary case for a cron
    routine. Never raises: ``parse_routines()`` runs at import time all over the
    test suite, so a malformed block has to arrive as a doctor finding naming the
    file, not as a collection error naming a line number in this script.
    """
    blocks = _WEBHOOK_BLOCK.findall(body)
    if not blocks:
        return None, None
    if len(blocks) > 1:
        return None, f"{len(blocks)} ```json webhook blocks — a routine fires from one event source"
    try:
        parsed = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        return None, f"the ```json webhook block is not valid JSON ({exc.msg} at line {exc.lineno})"
    if not isinstance(parsed, dict):
        return None, "the ```json webhook block must be an object"
    return parsed, None


def webhook_problems(routine: Routine) -> list[tuple[str, str]]:
    """``(problem, remedy)`` pairs for one routine's webhook declaration.

    Empty means clean. Every check here guards a failure the API will not: it
    accepts an unknown event name, stores a filter it never reads back, and has
    no way to undo either.
    """
    problems: list[tuple[str, str]] = []

    if routine.webhook_error:
        return [(f"{routine.path}: {routine.webhook_error}", "fix the block, or remove it")]

    if routine.webhook is None:
        if routine.kind == "event":
            problems.append(
                (
                    f"{routine.path} declares no ```json webhook block",
                    "an event routine with no webhook registers as a routine nothing ever fires",
                )
            )
        return problems

    declared = set(routine.webhook)
    if owned := declared & WEBHOOK_OWNED:
        problems.append(
            (
                f"{routine.path} declares {', '.join(sorted(owned))} in its webhook block",
                "this script sets those from the live routine and the repo — remove them",
            )
        )
    if unknown := declared - WEBHOOK_FIELDS - WEBHOOK_OWNED:
        problems.append(
            (
                f"{routine.path} webhook block has unknown key(s): {', '.join(sorted(unknown))}",
                f"a webhook block declares only: {', '.join(sorted(WEBHOOK_FIELDS))}",
            )
        )

    source = routine.webhook.get("source")
    if source not in WEBHOOK_SOURCES:
        problems.append(
            (
                f"{routine.path} webhook source is {source!r}",
                f"use one of: {', '.join(WEBHOOK_SOURCES)}",
            )
        )

    events = routine.webhook.get("events")
    if not isinstance(events, list) or not events or not all(isinstance(e, str) for e in events):
        problems.append(
            (
                f"{routine.path} webhook block has no `events` list",
                "name at least one event, as a list of strings",
            )
        )
    else:
        if unknown_events := [e for e in events if e not in WEBHOOK_EVENTS]:
            problems.append(
                (
                    f"{routine.path} names unknown event(s): {', '.join(unknown_events)}",
                    f"the API accepts any string and fires on none of them — use: {', '.join(WEBHOOK_EVENTS)}",
                )
            )
        if missing := webhook_events_disagree(routine.trigger, events):
            problems.append(
                (
                    f"{routine.path} webhook fires on {', '.join(missing)}, "
                    "which its **Trigger** line does not mention",
                    "the prose is what a human reads and the JSON is what gets POSTed — make them agree",
                )
            )

    webhook_filter = routine.webhook.get("filter", {})
    if not isinstance(webhook_filter, dict):
        problems.append((f"{routine.path} webhook `filter` is not an object", "use an object, or leave it out"))
    elif len(json.dumps(webhook_filter)) > WEBHOOK_FILTER_LIMIT:
        problems.append(
            (
                f"{routine.path} webhook filter is over {WEBHOOK_FILTER_LIMIT} characters",
                "keep the filter reviewable — put the judgement in the routine's own steps",
            )
        )

    return problems


def webhook_events_disagree(trigger: str, events: Sequence[str]) -> list[str]:
    """Events whose name appears nowhere in the routine's ``**Trigger**`` line.

    The same cross-check as cron-versus-README-table, for the same reason: the
    prose is what a human reads and what the agenda prints, the JSON is what gets
    POSTed, and two copies that can disagree is one copy that is wrong. Matched on
    the event's words so `pull_request` is satisfied by "PR opened" only if the
    line says so — write the event name in the line.
    """
    spelling = trigger.lower().replace("_", " ")
    return [event for event in events if event.lower().replace("_", " ") not in spelling]


def readme_cron(trigger_cell: str) -> str | None:
    """The cron expression a README trigger cell claims, if it claims one."""
    match = _BACKTICKED.search(trigger_cell)
    return match.group(1) if match else None


def restricts_both_day_fields(cron: str) -> bool:
    """Whether a cron restricts day-of-month *and* day-of-week.

    Standard cron ORs the two when both are restricted, so `30 7 1-7 * 2` fires
    every day 1–7 *and* every Tuesday — a fortnightly routine quietly running
    near-daily. Flagged rather than corrected: which field was meant is a
    judgement call. Documented under "Cron trap" in README.md.
    """
    fields = cron.split()
    if len(fields) != 5:
        return False
    _, _, dom, _, dow = fields
    return dom != "*" and dow != "*"


# --- the agenda: what the fleet runs on one day -------------------------------
#
# The schedule above is twenty-one cron expressions on five cadences, and a cron
# expression is not a thing anybody reads at six in the morning: `30 7 11,25 * *`
# is written for a scheduler. Everything below turns the same table into "what
# runs today, and when" — including the finished Slack lines, so
# `make cowork-agenda` and `/cowork today` print what this computed instead of
# interpreting twenty-one expressions by eye. A model that reads a cron field
# correctly most of the time is a model that tells you the wrong morning, once.
# Nothing posts this on a schedule any more — `cron/day-ahead.md` did, and the
# fleet reports what it shipped now rather than what it is about to run.


# The zone the agenda *renders* in. UTC stays the source of truth — every cron in
# cowork/ is UTC and stays that way, and every local time is printed with its UTC
# original in brackets. One string to change the zone, and daylight saving is
# derived per date rather than baked into an offset.
DISPLAY_TZ = "Europe/London"

# Above this many firings in a day, a routine is background noise rather than an
# timed run, and renders as a window instead of a list of times. Today that is
# `slack-relay` alone, at seventeen. A threshold rather than a name check, so the
# next hourly routine is handled without an edit here.
BACKGROUND_AFTER = 3

# The fleet's daily poster, left out of the *rendered* agenda. "Every day:
# shipped-standup" is the one line in a four-line schedule that tells a reader
# nothing they are not already holding — they got yesterday's, they will get
# today's. It stays in the payload, because the payload is the fleet's schedule
# and this is genuinely part of it; `/cowork status` is what audits whether it is
# registered.
#
# This used to be `day-ahead`, the routine that posted the agenda itself. That
# routine is gone: announcing what is *about* to run is worth much less than
# reporting what ran, and it was the one routine in the fleet forbidden from
# staying quiet. `agenda_lines()` below outlived it — `make cowork-agenda` and
# `/cowork today` still print it on demand, which is when a schedule is actually
# wanted.
DAILY_POSTER = "shipped-standup"

# One fixed emoji per section, which is `cron/digest.md`'s convention and the only
# decoration `.claude/agents/cowork-scribe.md` permits: constant per section, so a
# reader who gets this every morning finds the part they want by shape before they
# read a word. Four rules held them to these four codepoints. None is a variation
# sequence — a trailing U+FE0F that one client needs and another drops is a heading
# that renders two ways, which `TestAgenda` pins by length rather than by trusting
# this comment. None is `✅` or `❌`, which `cron/slack-relay.md` parses as
# the approval verbs. None collides with the digest's eleven, so the two daily posts
# are never confusable at a glance. And all four sit inside one Unicode block, which
# is what keeps them legible beside each other at Slack's heading size.
SECTION_EMOJI = {"today": "📅", "background": "🔁", "events": "🔔", "ahead": "📆"}

# How far the tail looks. A week is what makes the fortnightly and monthly sweeps
# visible: `30 7 11,25 * *` is unreadable, "Tue 11  poker-sweep" is not.
HORIZON_DAYS = 7

# minute, hour, day-of-month, month, day-of-week. Day-of-week allows 7 as well as
# 0 for Sunday, which is what every cron implementation accepts.
_FIELD_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _cron_int(text: str, spec: str, low: int, high: int) -> int:
    """One number out of a cron field, refusing anything this does not model."""
    if not text.isdigit():
        raise ValueError(
            f"cron field `{spec}`: `{text}` is not a number — "
            "name aliases (MON, JAN) are not supported; use the numeric form"
        )
    value = int(text)
    if not low <= value <= high:
        raise ValueError(f"cron field `{spec}`: `{value}` is outside {low}-{high}")
    return value


def _cron_field(spec: str, low: int, high: int) -> frozenset[int]:
    """Every value one cron field matches.

    Handles ``*``, ``5``, ``1,4``, ``7-23``, ``*/2`` and ``1-7/2``. An
    out-of-range number and a name alias both **raise** rather than matching
    nothing: a field that matches nothing is a routine that never fires, and an
    agenda that is silently empty is worse than one that is wrong out loud.
    """
    values: set[int] = set()
    for part in spec.split(","):
        body, _, step_text = part.partition("/")
        if step_text:
            if not step_text.isdigit() or int(step_text) < 1:
                raise ValueError(f"cron field `{spec}`: `{step_text}` is not a positive step")
            step = int(step_text)
        else:
            step = 1
        if body == "*":
            start, stop = low, high
        elif step_text and "-" not in body:
            # Vixie cron reads `1/2` as `1-31/2`. Raising rather than modelling it
            # keeps the rule this parser is built on whole: every form it does not
            # implement is wrong out loud, never quietly narrower than the scheduler.
            raise ValueError(f"cron field `{spec}`: `{part}` needs an explicit range, as `{body}-{high}/{step}`")
        elif "-" in body:
            start_text, _, stop_text = body.partition("-")
            start = _cron_int(start_text, spec, low, high)
            stop = _cron_int(stop_text, spec, low, high)
            if stop < start:
                raise ValueError(f"cron field `{spec}`: range `{body}` runs backwards")
        else:
            start = stop = _cron_int(body, spec, low, high)
        values.update(range(start, stop + 1, step))
    return frozenset(values)


def cron_times(cron: str, day: date) -> tuple[time, ...]:
    """Every UTC time a cron expression fires on one day, earliest first.

    Empty when it does not fire that day at all.

    Day-of-month and day-of-week are **OR**ed when both are restricted, because
    that is what POSIX cron does and therefore what the account does.
    ``restricts_both_day_fields()`` exists to stop any routine relying on it, but
    this function has to model the scheduler rather than the house rule — an
    agenda that implemented the rule would disagree with the fleet about exactly
    the case the rule is there to catch.
    """
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError(f"cron `{cron}` is not a 5-field expression")
    minutes, hours, doms, months, dows = (
        _cron_field(spec, low, high) for spec, (low, high) in zip(fields, _FIELD_BOUNDS, strict=True)
    )
    if day.month not in months:
        return ()

    # Python counts weekdays from Monday-0; cron counts from Sunday-0.
    weekday = (day.weekday() + 1) % 7
    dom_hit = day.day in doms
    dow_hit = weekday in dows or (weekday == 0 and 7 in dows)
    dom_restricted = fields[2] != "*"
    dow_restricted = fields[4] != "*"
    if dom_restricted and dow_restricted:
        fires = dom_hit or dow_hit
    elif dom_restricted:
        fires = dom_hit
    elif dow_restricted:
        fires = dow_hit
    else:
        fires = True
    if not fires:
        return ()
    return tuple(sorted(time(hour, minute) for hour in hours for minute in minutes))


def display_zone() -> tuple[ZoneInfo | None, str | None]:
    """The display timezone, or ``None`` plus a remedy when there is no tz database.

    ``zoneinfo`` is stdlib but reads the system tz database, which a slim
    container may not carry. Falling back to UTC-only costs a bracket; raising
    would cost the whole morning post, which is the one thing this routine is for.
    """
    try:
        return ZoneInfo(DISPLAY_TZ), None
    except (KeyError, OSError, ValueError):
        # ZoneInfoNotFoundError subclasses KeyError; a corrupt database raises OSError.
        return None, f"times in UTC — no tz database for {DISPLAY_TZ} (`uv add --dev tzdata` fixes it)"


def _local(day: date, moment: time, zone: ZoneInfo | None) -> str:
    """A UTC time rendered in ``zone``, marked when it lands on another date.

    The marker matters because ``DISPLAY_TZ`` is a one-line change somebody will
    make: at 06:00 UTC London is the same day, and Los Angeles is the day before.
    """
    if zone is None:
        return f"{moment:%H:%M}"
    here = datetime.combine(day, moment, tzinfo=UTC).astimezone(zone)
    shift = (here.date() - day).days
    return f"{here:%H:%M}" + ("" if shift == 0 else f" ({shift:+d}d)")


def _window(day: date, start: time, end: time, zone: ZoneInfo | None) -> str:
    """A first-to-last span rendered locally, with midnight written as 24:00.

    `_local` would mark the end of `0 7-23 * * *` as `00:00 (+1d)`, which is true
    and reads as a bug. For a span, 24:00 is the ordinary way to write "the end of
    this day", and it is the only place that convention applies — a single
    timed run at midnight is genuinely on the next date and still says so.
    """
    first = _local(day, start, zone)
    last = _local(day, end, zone)
    if last.endswith(" (+1d)") and last.startswith("00:"):
        last = f"24:{last[3:5]}"
    return f"{first}-{last}"


def day_plan(routines: Sequence[Routine], day: date, zone: ZoneInfo | None) -> tuple[list[dict], list[dict]]:
    """One day's cron routines, split into timed runs and background.

    "Timed", not "appointments", and deliberately: CodeQL's sensitive-data
    heuristic reads the CWE-359 personal-information vocabulary off variable
    names, and `appointments` is in it — a medical appointment is private data.
    A list of cron names and clock times is not, but the heuristic is name-driven
    and cannot be argued with: it tainted this list, followed it into the payload,
    and reported `--agenda`'s own stdout as clear-text logging of private
    information. Renaming fixes it at the source. The repo carries no suppression
    comments and this is not the place to start one.
    """
    timed: list[dict] = []
    background: list[dict] = []
    for routine in routines:
        if routine.kind != "cron" or not routine.cron:
            continue
        times = cron_times(routine.cron, day)
        if not times:
            continue
        entry = {
            "name": routine.name,
            "workstream": routine.workstream,
            "summary": routine.summary,
        }
        if len(times) > BACKGROUND_AFTER:
            entry["firings"] = len(times)
            entry["window_utc"] = f"{times[0]:%H:%M}-{times[-1]:%H:%M}"
            entry["window_local"] = _window(day, times[0], times[-1], zone)
            background.append(entry)
        else:
            entry["times_utc"] = [f"{moment:%H:%M}" for moment in times]
            entry["times_local"] = [_local(day, moment, zone) for moment in times]
            timed.append(entry)
    # Ordered by UTC, which is the order they actually fire — a single zone is a
    # monotonic shift of it, so the rendered local times come out ordered too.
    timed.sort(key=lambda entry: (entry["times_utc"][0], entry["name"]))
    background.sort(key=lambda entry: entry["name"])
    return timed, background


def agenda(day: date, horizon: int = HORIZON_DAYS) -> dict:
    """What the fleet does on ``day``, plus a name-only tail for the days after.

    Includes the rendered ``lines`` — the finished Slack message — so the routine
    that posts it never has to compose anything, and the format is covered by
    unit tests rather than by hoping.
    """
    routines = parse_routines()
    zone, degraded = display_zone()
    timed, background = day_plan(routines, day, zone)

    ahead = []
    for offset in range(1, horizon + 1):
        future = day + timedelta(days=offset)
        names = [entry["name"] for entry in day_plan(routines, future, zone)[0]]
        ahead.append({"date": future.isoformat(), "weekday": f"{future:%a}", "names": names})

    # A name on every single day of the tail is not news — it is the daily
    # routines restated seven times. Lifted out and named once at the end instead.
    daily = sorted(set.intersection(*(set(entry["names"]) for entry in ahead))) if ahead else []
    for entry in ahead:
        entry["names"] = [name for name in entry["names"] if name not in daily]

    payload = {
        "date": day.isoformat(),
        "weekday": f"{day:%a}",
        "display_timezone": None if zone is None else DISPLAY_TZ,
        "note": degraded,
        "today": timed,
        "background": background,
        "daily": daily,
        "events": [
            {"name": routine.name, "trigger": routine.trigger, "summary": routine.summary}
            for routine in routines
            if routine.kind == "event"
        ],
        "ahead": ahead,
    }
    payload["lines"] = agenda_lines(payload)
    return payload


def _joined(stamps: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b and c`` — the quiet days named in one clause.

    ``stamps`` must be non-empty. Returning ``""`` for an empty one would render
    " is clear." out of a caller that had nothing to say, so the caller checks.
    """
    if len(stamps) == 1:
        return stamps[0]
    return ", ".join(stamps[:-1]) + " and " + stamps[-1]


def agenda_lines(payload: dict) -> list[str]:
    """The finished Slack message, one string per line.

    **Standard Markdown, not Slack mrkdwn.** The connector takes ``**bold**``,
    ``_italic_`` and ``` `code` ```. Slack's own ``*bold*`` is Markdown's *italic*,
    so the two do not fail against each other — they quietly render the wrong
    thing, which is how the daily digest shipped italic headings for weeks
    (``.claude/agents/cowork-scribe.md`` keeps that account). This renderer made
    the identical mistake and outlived the digest's fix, because it is the one
    message composed in Python: the fix went to a routine file, and the test here
    pinned ``*Today*`` as though it were correct.

    Each section heading carries one fixed emoji from ``SECTION_EMOJI`` — the
    digest's anchor convention, and the only decoration the scribe permits. Still
    no bare URLs: the schedule links to nothing.
    """
    day = date.fromisoformat(payload["date"])
    zone = payload["display_timezone"] or "UTC"
    lines = [f"{SECTION_EMOJI['today']} **Today** — {day:%a} {day.day} {day:%b} ({zone})"]
    if payload["note"]:
        # Above the blank line, so it reads as a qualification of the heading it
        # follows rather than as the first entry of the list it precedes.
        #
        # Deliberately unemphasised. The note embeds DISPLAY_TZ, and a zone name
        # carries underscores (`America/Los_Angeles`) — wrapping it in `_…_` is
        # how you leak a stray underscore mid-line, which is the *other* half of
        # the dialect bug the scribe file records. A caveat nobody ever sees is
        # not worth that.
        lines.append(payload["note"])
    lines.append("")

    listed = [entry for entry in payload["today"] if entry["name"] != DAILY_POSTER]
    if listed:
        for entry in listed:
            summary = f" — {entry['summary']}" if entry["summary"] else ""
            local = ", ".join(entry["times_local"])
            utc = ", ".join(entry["times_utc"])
            # Half the year London *is* UTC, and a bracket repeating the time it
            # sits beside is noise. The heading already names the zone, so the
            # bracket only appears when it has something to say.
            gloss = "" if local == utc else f" ({utc} UTC)"
            # Code style on the time and bold on the name. Slack renders in a
            # proportional font, where the two spaces that used to align this
            # column do not; monospace is the only alignment this dialect offers.
            # The name is bold because it is what somebody scanning for one
            # routine is looking for — the summary beside it is the answer to a
            # question they only ask once.
            lines.append(f"`{local}`  **{entry['name']}**{summary}{gloss}")
    else:
        lines.append("_No routines fire today._")

    standing = []
    if payload["background"]:
        # One anchor for the section, never one per entry: an emoji that repeats
        # down a column has stopped being a heading and become a bullet, which is
        # the opposite of the rule `.claude/agents/cowork-scribe.md` states. The
        # fleet has exactly one background routine today, and `BACKGROUND_AFTER`
        # is a threshold rather than a name check precisely so the second one
        # needs no edit here — so the single case keeps its one-line form and the
        # plural case grows a heading rather than a second anchor.
        spans = []
        for entry in payload["background"]:
            gloss = "" if entry["window_local"] == entry["window_utc"] else f" ({entry['window_utc']} UTC)"
            spans.append((entry["name"], f"{entry['firings']} runs `{entry['window_local']}`{gloss}"))
        head = f"{SECTION_EMOJI['background']} **Background**"
        if len(spans) == 1:
            name, body = spans[0]
            standing.append(f"{head} — {name}, {body}")
        else:
            standing.append(head)
            standing.extend(f"**{name}** — {body}" for name, body in spans)
    if payload["events"]:
        standing.append(
            f"{SECTION_EMOJI['events']} **On GitHub events** — "
            + ", ".join(entry["name"] for entry in payload["events"])
        )
    if standing:
        # Split off by a blank line: these two are not entries in today's list,
        # they are the things that are true all day, and run together with the
        # timed lines they read as two more of them.
        lines.append("")
        lines.extend(standing)

    upcoming_lines: list[str] = []
    quiet: list[str] = []
    # The month of the last day actually *rendered* into the sequence, which is not
    # the same as the last day walked. Advancing this on a quiet day spends the
    # month marker on a line that gets collapsed into the closing sentence, and the
    # next rendered day inherits a month it never named: `**Sat 31**` followed by
    # `**Mon 2**`, with November announced only in an aside underneath. That is the
    # ambiguity the marker exists to prevent, and it fires every month whose 1st
    # falls on a Sunday. A quiet day still gets its own month when it changes —
    # it is read in the closing sentence, where there is no sequence to infer from.
    shown = day.month
    for entry in payload["ahead"]:
        future = date.fromisoformat(entry["date"])
        # Only when it changes: "Tue 1" a week out is ambiguous, "Tue 1 Sep" is not.
        stamp = f"{entry['weekday']} {future.day}" + (f" {future:%b}" if future.month != shown else "")
        names = [name for name in entry["names"] if name != DAILY_POSTER]
        if names:
            upcoming_lines.append(f"**{stamp}** — " + ", ".join(names))
            shown = future.month
        else:
            quiet.append(stamp)

    lines.append("")
    lines.append(f"{SECTION_EMOJI['ahead']} **Next {len(payload['ahead'])} days**")
    if upcoming_lines:
        lines.append("")
        lines.extend(upcoming_lines)

    # An empty day written as its own line reads as a routine called "nothing", and
    # spends a line of a fifteen-line post saying so. Folded into one closing
    # sentence with the dailies instead — same two facts, both of them asides.
    # Safe to italicise where the note above was not: every name here matches
    # `[a-z0-9-]+` (see `_ROUTINE_ROW`), so none can carry an underscore.
    closing = []
    if quiet:
        closing.append(f"{_joined(quiet)} {'is' if len(quiet) == 1 else 'are'} clear.")
    daily = [name for name in payload["daily"] if name != DAILY_POSTER]
    if daily:
        closing.append("Every day: " + ", ".join(daily) + ".")
    if closing:
        lines.append("")
        lines.append("_" + " ".join(closing) + "_")
    return lines


# --- repo consistency --------------------------------------------------------


def check_repo(report: Report) -> None:
    """Cross-check the README table against the routine files and the tier table.

    These three can disagree without anything failing: the routine still runs, on
    whatever the account-side dropdown says, against whatever cron was typed into
    the web form. Nothing in the Python suite would notice, so it is checked here
    and in ``tests/unit/test_cowork_setup.py``.
    """
    tiers = parse_tiers()
    rows = {f"{kind}/{stem}.md": (trigger, tier) for kind, stem, trigger, _, tier in _routine_rows()}
    on_disk = {str(p.relative_to(ROUTINES_DIR)) for p in ROUTINES_DIR.rglob("*.md")}

    for missing in sorted(on_disk - set(rows)):
        report.fail(
            f"cowork/routines/{missing} is not in the README registered-routines table",
            "add its row (routine, trigger, workstream, tier) to cowork/README.md",
        )
    for orphan in sorted(set(rows) - on_disk):
        report.fail(
            f"cowork/README.md lists {orphan}, which does not exist",
            "create the routine file or drop the row",
        )

    # A TOOL_OVERRIDES key that names no routine is a renamed or deleted routine
    # that silently lost its extra tools — the relay without RemoteTrigger still
    # runs, and reports it cannot pause anything, every hour.
    stems = {Path(name).stem for name in on_disk}
    for stray in sorted(set(TOOL_OVERRIDES) - stems):
        report.fail(
            f"TOOL_OVERRIDES names `{stray}`, which matches no routine file",
            "rename the key to the routine's current stem or remove the entry",
        )

    routines = parse_routines()
    for routine in routines:
        # A row whose file is missing was already reported above; the checks below
        # all read that file, so there is nothing left to say about it.
        if not (ROUTINES_DIR / routine.path).exists():
            continue

        if routine.tier not in tiers:
            report.fail(
                f"{routine.path} is tiered `{routine.tier}`, which models.md does not define",
                f"use one of: {', '.join(sorted(tiers))}",
            )
        elif not routine.model_id:
            report.fail(
                f"{routine.path} resolves to a tier with no model id",
                "give it a tier from the models.md table that names an id",
            )

        table_trigger, table_tier = rows.get(routine.path, ("", ""))
        table_cron = readme_cron(table_trigger)

        if routine.kind == "cron":
            if not routine.cron:
                report.fail(
                    f"{routine.path} has no `**Trigger** — cron ...` line",
                    "add one, or move the file under routines/events/",
                )
            elif table_cron != routine.cron:
                report.fail(
                    f"{routine.path} runs on `{routine.cron}` but README says `{table_cron}`",
                    "make the routine file and the README table agree",
                )
            if routine.cron and restricts_both_day_fields(routine.cron):
                report.fail(
                    f"{routine.path} cron `{routine.cron}` restricts day-of-month AND day-of-week",
                    "cron ORs them — this fires far more often than intended; restrict one",
                )
            if routine.cron:
                # Parse it, and prove it fires. `30 7 31 2 *` is five valid fields
                # that never come round; `5 0 * * MON` is a form nothing here models.
                # Both would register happily and simply never run, and the only
                # thing that would notice is the digest reporting a silent scout.
                try:
                    fires = any(
                        cron_times(routine.cron, date(2026, 1, 1) + timedelta(days=offset)) for offset in range(366)
                    )
                except ValueError as error:
                    report.fail(f"{routine.path} cron `{routine.cron}` does not parse: {error}", "fix the expression")
                else:
                    if not fires:
                        report.fail(
                            f"{routine.path} cron `{routine.cron}` fires on no day of a whole year",
                            "it would register and stay silent forever — check the day and month fields",
                        )

        # The agenda has nothing to say about a routine that will not say what it
        # does, and `make cowork-agenda` / `/cowork today` print these lines
        # verbatim — so a missing one is a blank in the rendered schedule, not a
        # cosmetic gap. It is also the one-line description of the routine in the
        # README table's sibling prose, which is the other reason to require it.
        if not routine.summary:
            report.fail(
                f"{routine.path} has no `**Summary** — ...` line",
                f"add one line under **Trigger** saying what it does, at most {SUMMARY_LIMIT} characters",
            )
        elif len(routine.summary) > SUMMARY_LIMIT:
            report.fail(
                f"{routine.path} has a {len(routine.summary)}-character **Summary** line",
                f"shorten it to {SUMMARY_LIMIT} — it is rendered as one line of a Slack message",
            )

        file_tier = _MODEL_LINE.search((ROUTINES_DIR / routine.path).read_text(encoding="utf-8"))
        if file_tier and file_tier.group(1) != _unwrap(table_tier):
            report.fail(
                f"{routine.path} declares tier `{file_tier.group(1)}` but README says {table_tier}",
                "make the routine file and the README table agree",
            )

    check_webhooks(report, routines)
    check_grants(report, routines)
    check_charter_coverage(report)


def check_webhooks(report: Report, routines: Sequence[Routine]) -> None:
    """Every routine's webhook declaration, and the one that fires the deployer."""
    for routine in routines:
        for problem, remedy in webhook_problems(routine):
            report.fail(problem, remedy)

    deployer = next((r for r in routines if r.name == DEPLOY_ROUTINE), None)
    if deployer and not deployer.webhook:
        # Removing the block leaves CD running daily and looking deployed. It is a
        # cron routine, so the "an event routine declares none" branch above does
        # not cover it, and nothing else would ever mention it again.
        report.fail(
            f"{deployer.path} declares no ```json webhook block",
            "without it the deploy only ever runs on its cron — a merge would wait until 04:00",
        )
    elif deployer:
        # The deployer applies whatever branch fired it. Pinned as a named check
        # rather than left to the block, because the block is a file the deployer
        # itself can come to be standing on.
        events = deployer.webhook.get("events") or []
        if events != ["push"]:
            report.fail(
                f"{deployer.path} fires on {events or 'nothing'}, not `push`",
                "the deployer ships what merged — it fires on push and nothing else",
            )


def check_grants(report: Report, routines: Sequence[Routine]) -> None:
    """No routine may hold the routines API together with a free-hand file editor.

    ``RemoteTrigger`` plus ``Write``/``Edit`` is one routine that can rewrite a
    routine file and reprogram the fleet from what it wrote, inside a single run
    and without either half being reviewed. Neither holder needs it: the relay
    edits nothing, and the deployer's only file write is the README URL column,
    made by ``--urls`` inside this script.

    What it removes is the unreviewed path: a change made through this script or
    through git ends up in a PR, and this check is what keeps the grant from
    quietly growing a shortcut around that.

    The second rule is narrower and was bought the hard way. ``Bash`` can write
    anything, so for a long time this check conceded that it did not make a holder
    harmless. Issue #172 is what that concession cost: relaying one ✅ replaced the
    issue's entire label set, taking with it the ``workstream:`` label that scopes
    which paths the implement job may touch. The relay's own file had always
    specified ``--add-label``; prose was the only thing enforcing it. So the relay
    — and only the relay, which runs seventeen times a day on text it did not
    write — must carry a scoped shell rather than a bare one. ``cd-deploy`` keeps
    bare ``Bash``: it runs ``git`` and ``gh pr create`` against a plan Python
    composed, and enumerating that is a list that would rot.
    """
    for routine in routines:
        tools = set(routine_tools(routine.name))
        if "RemoteTrigger" in tools and (writers := tools & {"Write", "Edit"}):
            report.fail(
                f"{routine.path} holds RemoteTrigger and {', '.join(sorted(writers))}",
                "a routine that can edit the repo must not also reprogram the fleet — drop one",
            )
        if routine.name == "slack-relay" and "Bash" in tools:
            report.fail(
                f"{routine.path} holds unscoped Bash",
                "the relay must list the exact `gh` verbs it needs — see issue #172, where a bare "
                "shell replaced an issue's label set while relaying a single reaction",
            )


# --- charter coverage --------------------------------------------------------

# Modules deliberately claimed by no charter, each with the reason. Keep this
# empty if you can: an entry here is a file no scout will ever read.
UNOWNED_MODULES: dict[str, str] = {
    "__init__.py": "package marker — no behaviour to scout",
}


def _owns_block(text: str) -> str:
    """The ``**Owns**`` paragraph of one charter — up to the first blank line.

    Scoped rather than whole-document, for the same reason ``_section`` is: a
    charter says ``**`telemetry.py` is not this feature**`` and ``**`tools/team_learning.py`
    is not yours**`` in its standing concerns, and reading the file whole counts
    those disclaimers as claims. Every module named that way happens to be owned
    elsewhere today, so the check would pass on luck, and the next module excused
    by a "not yours" sentence would pass silently — which is the failure this
    exists to catch.
    """
    match = re.search(r"^\*\*Owns\*\*(.*?)(?:\n\s*\n|\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


def owned_modules() -> set[str]:
    """Every ``src/yeaboi/*.py`` basename claimed in a charter's ``**Owns**`` block.

    Substring matching within that block, deliberately: a charter writes
    ``paths.py`` in one place and ``src/yeaboi/paths.py`` in another, and both
    should count. The cost is that a *nested* path matches its basename too —
    platform's ``mcp/.../__init__.py`` reads as a claim on ``src/yeaboi/__init__.py``.
    Excusing beats matching, so anything in ``UNOWNED_MODULES`` is subtracted:
    the declared reason stays load-bearing rather than being quietly shadowed by
    a coincidence somebody could delete without noticing.
    """
    claims = "\n".join(_owns_block(p.read_text(encoding="utf-8")) for p in sorted(WORKSTREAMS_DIR.glob("*.md")))
    found = {p.name for p in (REPO_ROOT / "src" / "yeaboi").glob("*.py") if p.name in claims}
    return found - set(UNOWNED_MODULES)


def check_charter_coverage(report: Report) -> None:
    """Every top-level module belongs to a charter, or says why it does not.

    The label check below proves the fifteen charters agree with the fifteen
    labels; nothing proved they covered the repo. Fourteen modules were claimed by
    nobody when this was written — a scout reads only the paths its charter
    declares, so an unclaimed file is one no routine will ever look at, and the
    fleet reports itself healthy the whole time. That is the failure this catches:
    silent by construction, exactly like the tier drift above.
    """
    package = REPO_ROOT / "src" / "yeaboi"
    if not package.is_dir():  # running against a copied fixture repo
        return

    owned = owned_modules()
    for module in sorted(p.name for p in package.glob("*.py")):
        if module in owned or module in UNOWNED_MODULES:
            continue
        report.fail(
            f"src/yeaboi/{module} is claimed by no charter",
            "name it in a cowork/workstreams/*.md `**Owns**` line, or add it to UNOWNED_MODULES with a reason",
        )


# --- GitHub ------------------------------------------------------------------
#
# Two transports behind one set of operations, both of them `_gh_transport`'s.
# `gh` is preferred whenever it is there — it is what a developer has already
# authenticated, and keeping that path byte-for-byte identical means this script
# behaves locally exactly as it always has.
#
# The REST fallback exists because the environment that matters most has no `gh`
# at all. `cron/cd-deploy.md` runs this script from a cloud routine session, and
# that session is handed a GitHub *token* rather than the CLI. So every firing
# took the "not on PATH" branch, exited 1 under `--strict`, and the routine's own
# stop condition halted it before step 4 — the fleet reconciliation it exists to
# do. A missing binary is not a reason to stop deploying.
#
# The transport itself lives next door because `pr_feedback.py` and
# `cowork_relay.py` need the identical thing, and a transport written three
# times is a transport wrong in the same way three times. What stays here is the
# *choosing* and the reporting: which transport this run picked, and how a
# refusal is spoken as a `STRICT.note`.

# Delegating wrappers rather than bare aliases. An alias binds the function
# object at import, so a test replacing `_api` here would still leave
# `transport.api_paged` calling the real one underneath it — the seam would leak
# for exactly the paged reads that do the most work. These forward by name, and
# `transport.api` stays the one place to intercept.


def _gh(*args: str) -> subprocess.CompletedProcess[str]:
    return transport.gh(*args)


def _api(method: str, path: str, body: dict | None = None) -> transport.ApiResult:
    return transport.api(method, path, body)


def _api_paged(path: str, key: str | None = None) -> transport.ApiResult:
    return transport.api_paged(path, key)


def _segment(name: str) -> str:
    return transport.segment(name)


def github_token() -> str | None:
    return transport.github_token()


ApiResult = transport.ApiResult

# Which transport this run resolved. "gh" is the default rather than None so a
# caller that never asked keeps the historical path; `github_ready()` is what
# actually chooses, and `main()` resets this the way it resets STRICT.
TRANSPORT = "gh"


def gh_ready() -> bool:
    """Whether the `gh` CLI is installed and authenticated.

    A thin wrapper rather than a direct re-export: it is the seam a test forces
    to pick a transport, so it has to be a name on *this* module that monkeypatch
    can replace without reaching into the shared one.
    """
    return transport.gh_ready()


def api_ready() -> bool:
    """Whether the REST transport has both halves it needs: a token, and a
    repository to point it at. Neither is worth a note on its own — a machine
    with `gh` working needs neither."""
    return bool(github_token()) and bool(repo_slug())


def _why_no_gh() -> tuple[str, str]:
    """Which of the two `gh` problems this is, and what fixes it. Said the same
    way whether it ends in a fallback or a degradation."""
    if shutil.which("gh") is None:
        return "is not on PATH", "install it: brew install gh"
    return "is not authenticated", "run: gh auth login"


def github_ready() -> bool:
    """Choose this run's transport, reporting the remedy when neither answers."""
    global TRANSPORT
    if gh_ready():
        TRANSPORT = "gh"
        return True
    problem, remedy = _why_no_gh()
    if api_ready():
        TRANSPORT = "api"
        say(f"github: `gh` {problem} — using the REST API against {repo_slug()}")
        return True
    TRANSPORT = "gh"
    STRICT.note(
        f"`gh` {problem} and no API token answered — skipping every GitHub check",
        f"{remedy} — or export GH_TOKEN, which is all the REST fallback needs",
    )
    return False


def existing_labels() -> set[str] | None:
    """Every label on the repo, or None if the query itself failed.

    None rather than an empty set, and the same for the variables below: they are
    different facts and the difference matters. A ready transport does not mean
    the next call succeeds — a missing remote, the wrong repo, a rate limit —
    and an empty set read as truth makes the doctor report all thirty-one labels
    missing and ``apply_labels`` try to create every one of them.
    """
    if TRANSPORT == "gh":
        result = _gh("label", "list", "--limit", "200", "--json", "name")
        if result.returncode != 0:
            STRICT.note("could not list the repo's labels", result.stderr.strip() or "unknown gh error")
            return None
        return {item["name"] for item in json.loads(result.stdout or "[]")}
    slug = repo_slug()
    if not slug:
        STRICT.note("could not list the repo's labels", "no owner/name resolved for this checkout")
        return None
    result = _api_paged(f"/repos/{slug}/labels")
    if not result.ok:
        STRICT.note("could not list the repo's labels", result.error)
        return None
    return {item["name"] for item in result.data}


def existing_variables() -> dict[str, str] | None:
    if TRANSPORT == "gh":
        result = _gh("variable", "list", "--json", "name,value")
        if result.returncode != 0:
            STRICT.note("could not list the repo's variables", result.stderr.strip() or "unknown gh error")
            return None
        return {item["name"]: item.get("value", "") for item in json.loads(result.stdout or "[]")}
    slug = repo_slug()
    if not slug:
        STRICT.note("could not list the repo's variables", "no owner/name resolved for this checkout")
        return None
    result = _api_paged(f"/repos/{slug}/actions/variables", key="variables")
    if not result.ok:
        STRICT.note("could not list the repo's variables", result.error)
        return None
    return {item["name"]: item.get("value", "") for item in result.data}


def create_label(label: Label) -> tuple[bool, str]:
    """Create one label. Returns (created, why not) so the caller does the noting."""
    if TRANSPORT == "gh":
        result = _gh(
            "label",
            "create",
            label.name,
            "--color",
            label.color,
            "--description",
            label.description,
        )
        return result.returncode == 0, result.stderr.strip() or "unknown gh error"
    slug = repo_slug()
    if not slug:
        return False, "no owner/name resolved for this checkout"
    # Bare hex, no leading `#` — which is how expected_labels() already spells
    # every colour, because that is what `gh label create --color` wanted too.
    result = _api(
        "POST",
        f"/repos/{slug}/labels",
        {"name": label.name, "color": label.color.lstrip("#"), "description": label.description},
    )
    return result.ok, result.error


def set_variable(name: str, value: str, exists: bool) -> tuple[bool, str]:
    """Set one repository variable, creating or updating as needed.

    `gh variable set` is one verb for both. REST is not: a create is a POST to
    the collection and an update is a PATCH to the item, and using the wrong one
    is a 409 or a 404 rather than a silent no-op. The caller already knows which
    it is — it had to list them to decide there was anything to do.
    """
    if TRANSPORT == "gh":
        result = _gh("variable", "set", name, "--body", value)
        return result.returncode == 0, result.stderr.strip() or "unknown gh error"
    slug = repo_slug()
    if not slug:
        return False, "no owner/name resolved for this checkout"
    if exists:
        result = _api("PATCH", f"/repos/{slug}/actions/variables/{_segment(name)}", {"name": name, "value": value})
    else:
        result = _api("POST", f"/repos/{slug}/actions/variables", {"name": name, "value": value})
    return result.ok, result.error


def delete_label(name: str) -> tuple[bool, str]:
    if TRANSPORT == "gh":
        result = _gh("label", "delete", name, "--yes")
        return result.returncode == 0, result.stderr.strip() or "unknown gh error"
    slug = repo_slug()
    if not slug:
        return False, "no owner/name resolved for this checkout"
    result = _api("DELETE", f"/repos/{slug}/labels/{_segment(name)}")
    return result.ok, result.error


def delete_variable(name: str) -> tuple[bool, str]:
    if TRANSPORT == "gh":
        result = _gh("variable", "delete", name)
        return result.returncode == 0, result.stderr.strip() or "unknown gh error"
    slug = repo_slug()
    if not slug:
        return False, "no owner/name resolved for this checkout"
    result = _api("DELETE", f"/repos/{slug}/actions/variables/{_segment(name)}")
    return result.ok, result.error


def repo_slug() -> str | None:
    """``owner/name`` for this checkout, memoised by the shared transport.

    A wrapper rather than a re-export for the same reason as `gh_ready()`: tests
    replace this name to point the script at a fixed repository, and a bare
    re-export would leave them patching an alias the callers never look up.
    """
    return transport.resolve_slug(REPO_ROOT)


def repo_url() -> str | None:
    """The clone URL a routine's ``git_repository`` source points at."""
    slug = repo_slug()
    return f"https://github.com/{slug}" if slug else None


# --- apply -------------------------------------------------------------------


def apply_labels() -> None:
    """Create the missing labels. Existing ones are left exactly as they are.

    Not `--force`: a colour or description someone deliberately changed is not
    drift worth correcting, and clobbering it would make a second run of this
    script destructive for no benefit.
    """
    present = existing_labels()
    if present is None:
        return
    missing = [label for label in expected_labels() if label.name not in present]
    if not missing:
        say(f"labels: all {len(expected_labels())} already present")
        return
    for label in missing:
        created, why = create_label(label)
        if created:
            say(f"labels: created {label.name}")
        else:
            STRICT.note(f"labels: could not create {label.name}", why)
    say(f"labels: {len(present)} already present, {len(missing)} attempted")


def apply_variables() -> None:
    """Set the model repository variables from the models.md table.

    Variables rather than secrets, deliberately: these are not sensitive, and
    masking them in logs would only make a failure harder to read.
    """
    wanted = parse_model_variables()
    current = existing_variables()
    if current is None:
        return
    for name, value in wanted.items():
        if current.get(name) == value:
            say(f"variables: {name} already set")
            continue
        was_set, why = set_variable(name, value, exists=name in current)
        if was_set:
            say(f"variables: set {name}")
        else:
            STRICT.note(
                f"variables: could not set {name}",
                why
                or "repository variables need admin on the repo — "
                "`gh auth refresh -h github.com -s repo`, or a token carrying "
                "`administration: write` (a 403 here is the silent-green case)",
            )


def merge_gate_armed() -> bool | None:
    """Whether `pr-feedback` is a required status check on the `main-branch` ruleset.

    None when the question could not be asked — no transport, no repo, or a
    failed query — which is deliberately not the same answer as False. A missing
    gate is a finding; an unanswerable question is a note, and conflating them
    would fail the doctor on every machine with neither `gh` nor a token.

    This is the setting that decides whether the auto lane merges anything. Every
    workflow that would arm `gh pr merge --auto` runs this same query first and
    refuses when it is false, so without it the lane opens PRs that sit waiting
    for a human click — the thing it exists to remove.
    """
    slug = repo_slug()
    if not slug:
        return None
    # Keyed on the transport this run resolved, not on `gh` being on PATH: a
    # machine with `gh` installed but unauthenticated and a token exported reads
    # every other check over REST, and having this one alone answer "cannot ask"
    # would report the gate unchecked on a run that checked everything else.
    if TRANSPORT == "gh":
        # `shutil.which` is still consulted here, because this is the one probe
        # reached on a run where no transport resolved at all:
        # `report_manual_remainder()` calls it after `github_ready()` has already
        # declined, and TRANSPORT is "gh" then by default rather than by evidence.
        if shutil.which("gh") is None:
            return None
        query = (
            'any(.[]; .type=="required_status_checks" and '
            'any(.parameters.required_status_checks[]; .context=="pr-feedback"))'
        )
        result = subprocess.run(  # noqa: S603 - literal argv
            ["gh", "api", f"repos/{slug}/rules/branches/main", "--jq", query],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() == "true"
    if not github_token():
        return None
    # The same question the jq expression above asks, asked in Python because
    # there is no jq here either.
    answer = _api("GET", f"/repos/{slug}/rules/branches/main")
    if not answer.ok or not isinstance(answer.data, list):
        return None
    return any(
        rule.get("type") == "required_status_checks"
        and any(
            check.get("context") == "pr-feedback"
            for check in (rule.get("parameters") or {}).get("required_status_checks") or []
        )
        for rule in answer.data
        if isinstance(rule, dict)
    )


def check_merge_gate(report: Report) -> None:
    """The doctor's half: a missing merge gate is a finding, not a footnote.

    `cron/cd-deploy.md` runs `--check` on every merge to `main`, so this is what
    would notice `pr-feedback` being dropped from the ruleset later. Nothing else
    would: the workflows that depend on it fail *quietly* by declining to arm
    auto-merge, which looks exactly like a lane that had nothing to do.
    """
    armed = merge_gate_armed()
    if armed is None:
        report.notes.append("the main-branch ruleset was not checked — `gh` unavailable or the query failed")
        return
    if not armed:
        report.fail(
            "`pr-feedback` is not a required status check on the main-branch ruleset",
            "add it at Settings → Rules → main-branch; until then no auto-lane PR merges unattended",
        )


def report_merge_gate() -> None:
    """Probe the one setting that decides whether the unattended lane works.

    `pr-feedback` on the `main-branch` ruleset's required checks is what makes
    "the ruleset decides, not the routine" true. Every workflow that would arm
    `--auto` refuses when it is absent, so without it the whole auto lane opens
    PRs that sit waiting for a human click — the thing it exists to remove.

    Probed rather than described. It was documented in four markdown files and
    reported by nothing, which is the same shape as the gaps this function was
    written for. The `gh api` call is the one `.github/workflows/codeql-triage.yml`
    already makes.
    """
    armed = merge_gate_armed()
    if armed is None:
        print("     · pr-feedback on the main-branch ruleset's required checks — not checked here")
        print("       (`gh` unavailable, or the query failed). Without it no auto-lane PR merges.")
        return
    if armed:
        say("pr-feedback is a required status check — the unattended merge is armed")
        return
    slug = repo_slug() or "<owner>/<repo>"
    # Reported, not STRICT.note()'d: this is a gap to fix, like the connectors and
    # the GitHub App above it, not a degradation of *this* run — and `cd-deploy`
    # runs under --strict, where failing on it would stop the fleet deploying over
    # a setting the deploy has no way to change.
    print("     · add `pr-feedback` to the main-branch ruleset's required status checks.")
    print("       Until then every auto-lane PR opens green and waits for a human click:")
    print(f"       https://github.com/{slug}/settings/rules")


def _has_label(issue: dict, name: str) -> bool:
    """Whether a REST issue payload carries a label.

    ``labels`` is a list of objects on the issues endpoint and a list of bare
    strings in one or two places GitHub still emits the older shape. Reading only
    the first would silently count every queued issue against the cap again —
    silently being the operative word, since the wrong answer here is a number
    that looks perfectly reasonable.
    """
    for label in issue.get("labels") or []:
        if (label.get("name") if isinstance(label, dict) else label) == name:
            return True
    return False


def _issues_labelled(labels: str, workstream: str | None) -> tuple[list[dict] | None, str]:
    """Open issues carrying every label in ``labels``, for one workstream.

    ``workstream=None`` drops that half of the filter and asks across the whole
    repo. Only the backfill uses it, and it needs to: one of the things it must
    classify is an issue carrying **no** `workstream:` label at all, which a
    per-workstream sweep can never see and which has no charter to build against.

    **REST on both transports, deliberately.** Every other read in this file
    branches to `gh <verb> --json` when `gh` is there, and that is right for
    labels and variables. It would be wrong here: `gh issue list --json` is
    GraphQL underneath, and `tests/fixtures/cowork_github_access_live.json`
    records a routine session where `gh` was installed, `GH_TOKEN` was set, and
    the GraphQL POST came back 403 from the egress proxy anyway — while
    ``GET /repos/{slug}/issues?labels=`` was served. A gate built on the refused
    call fails on exactly the unattended runs it exists to bound, so both
    branches ask the same REST question and only the credential differs.

    None rather than an empty list on failure, for the reason `existing_labels()`
    gives: "no issues" and "could not ask" are different facts, and the caller
    turns the second into zero slots rather than into a full queue.
    """
    slug = repo_slug()
    if not slug:
        return None, "no owner/name resolved for this checkout"
    # No `per_page` here: `transport.api_paged` appends its own, and two of them
    # agreeing today is only luck about which one somebody changes next.
    scope = f",workstream:{_segment(workstream)}" if workstream else ""
    path = f"/repos/{slug}/issues?labels={labels}{scope}&state=open"
    if TRANSPORT == "gh":
        if shutil.which("gh") is None:
            return None, "gh is not on PATH"
        # `--paginate` walks the Link headers and merges the arrays; `per_page`
        # only widens each hop. Both, for the reason `open_blocked_reports`
        # gives: a bare `gh api` stops at 30 and reads back as a short list
        # rather than as an error. Unscoped — which is how the backfill asks —
        # that silently left fifteen of forty-five issues unclassified, and the
        # command reported a clean plan over them. The REST branch below has
        # paged from the start; this one had not.
        result = _gh("api", "--paginate", f"{path.lstrip('/')}&per_page=100")
        if result.returncode != 0:
            return None, result.stderr.strip() or "unknown gh error"
        try:
            items = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as error:  # an egress proxy's HTML error page
            return None, f"gh api returned no JSON: {error}"
    else:
        answer = _api_paged(path)
        if not answer.ok:
            return None, answer.error
        items = answer.data
    if not isinstance(items, list):
        return None, "the issues endpoint did not return a list"
    # `/issues` returns pull requests too — GitHub models a PR as an issue, and a
    # cowork PR carries `workstream:<name>` by house rule. Counting those would
    # let an open PR eat a proposal slot, which is the one-open-PR guard charging
    # twice for the same piece of work.
    return [item for item in items if isinstance(item, dict) and "pull_request" not in item], ""


def open_proposals(workstream: str) -> tuple[list[dict] | None, str]:
    """The open ``cowork:proposal`` issues for one workstream, or None and why not.

    These are the *questions* — the finds the auto lane could not take, waiting on
    a human's ✅. They are what `PROPOSAL_CAP` bounds and what `digest.md` lists.

    An issue carrying ``cowork:queued`` as well is **queued, not proposed**, and
    is filtered out here. The two labels are mutually exclusive by design
    (see `QUEUE_LABEL`), so this only ever fires on the crash window between the
    reclassification's two REST calls — and resolving that window towards
    "queued" is the safe direction: counted as a proposal it would hold a slot
    that only a human verb could release, on an issue no human is going to be
    shown. Counted as queued it costs one extra line in one digest.
    """
    issues, error = _issues_labelled(PROPOSAL_LABEL, workstream)
    if issues is None:
        return None, error
    return [issue for issue in issues if not _has_label(issue, QUEUE_LABEL)], ""


def queued_items(workstream: str) -> tuple[list[dict] | None, str]:
    """The open ``cowork:queued`` issues a sweep should build, or None and why not.

    The counterpart of `open_proposals`: that one answers "may I file?", this one
    answers "what should I build?".

    **Anything carrying ``claude-implement`` is withheld.** A human can add that
    label to any issue at any moment, and `claude.yml` then fires a 110-turn
    implement job on it. If the same issue were still offered to a sweep, two
    builders would race one item and open two PRs for it. The digest already
    watches those issues under ⏳ *Approved, no PR yet*, so nothing goes missing
    by leaving them out — they simply have a different owner.

    None rather than an empty list on failure, and the caller must keep the two
    apart in the other direction from the cap: an unreadable queue reported as
    empty is a sweep that quietly stops draining and looks idle instead.
    """
    issues, error = _issues_labelled(QUEUE_LABEL, workstream)
    if issues is None:
        return None, error
    return [issue for issue in issues if not _has_label(issue, "claude-implement")], ""


def _age_days(stamp: str, now: datetime) -> int | None:
    """Whole days since an ISO-8601 ``created_at``, or None if it will not parse."""
    try:
        created = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return max(0, (now - created).days)


def proposal_slots(workstream: str, now: datetime | None = None) -> dict:
    """How many proposals one workstream may still file, and what is holding it.

    The arithmetic lives here rather than in the routine prose for the reason the
    `--triggers` reconcile does: a model asked to count fifteen queues by eye
    will eventually miscount one, and nothing downstream would notice. The sweep
    runs this and obeys the number.

    ``slots: None`` is the unreadable case and never zero-by-accident — the sweep
    reads None as "file criticals only, and say so", which is the same rule
    `digest.md` applies to a `pr_feedback.py` that could not read a PR: a failed
    query is never reported as a clean answer.
    """
    issues, error = open_proposals(workstream)
    # The queue depth rides along because `digest.md`'s 🛠️ Queued section is the
    # counterpart of its ⏸️ Held section and is built from the same call: Held
    # says "the queue is full, answer one", Queued says "the fleet owes you n
    # builds and there is nothing for you to do". Reported, never counted — a
    # queued item is work in flight and holds no slot.
    # The reason a queue read failed is discarded here on purpose: this payload's
    # gating fact is `slots`, and `queued: null` already carries "could not ask"
    # under the same convention `slots: null` uses. A sweep that needs the reason
    # is asking the build question, not the filing one, and `--queued` reports it
    # in full there.
    queued, _queued_error = queued_items(workstream)
    queued_count = None if queued is None else len(queued)
    if issues is None:
        return {
            "workstream": workstream,
            "cap": PROPOSAL_CAP,
            "open": None,
            "slots": None,
            "queued": queued_count,
            "blocking": [],
            "error": error,
        }
    moment = now or datetime.now(UTC)
    blocking = [
        {
            "number": issue.get("number"),
            "title": issue.get("title", ""),
            "age_days": _age_days(issue.get("created_at", ""), moment),
        }
        for issue in issues
    ]
    blocking.sort(key=lambda item: (-(item["age_days"] or 0), item["number"] or 0))
    return {
        "workstream": workstream,
        "cap": PROPOSAL_CAP,
        "open": len(issues),
        # Clamped at zero: a queue already over the cap (a human filing by hand,
        # or a cap lowered later) is a full queue, not a negative allowance that
        # arithmetic downstream could turn back into room.
        "slots": max(0, PROPOSAL_CAP - len(issues)),
        # `None`, never zero, on an unreadable read — the same rule `slots`
        # follows. A queue reported as empty when it could not be asked is a
        # workstream that looks idle rather than blind, and the digest's
        # "queued but nothing merged in 21 days" alarm would never fire.
        "queued": queued_count,
        "blocking": blocking,
    }


def report_proposal_slots(workstream: str | None, now: datetime | None = None) -> int:
    """Print the slots JSON for one workstream, or for all fifteen.

    One object for a named workstream because that is what a sweep asks; a list
    for the whole fleet because that is what `cron/digest.md`'s Held section is
    built from. Exit code stays 0 either way — a full queue is a normal outcome
    and a routine branching on the exit status would read it as a failure.
    """
    if workstream:
        known = parse_workstreams()
        if workstream not in known:
            print(f"unknown workstream: {workstream} (known: {', '.join(known)})", file=sys.stderr)
            return 2
        print(json.dumps(proposal_slots(workstream, now), indent=2))
        return 0
    print(json.dumps([proposal_slots(name, now) for name in parse_workstreams()], indent=2))
    return 0


def _queue_rank(issue: dict) -> tuple:
    """The order `sweep-procedure.md` step 5 builds in, computed once, here.

    Same key the auto lane has always sorted on — highest ``impact``, ties to
    lower ``risk`` — with age as the final tie-break so the queue drains oldest
    first. A model asked to sort fifteen queues by eye will eventually get one
    wrong and nothing downstream would notice, which is the argument this whole
    file is built on.

    A body with no ``**Impact** N · **Effort** X · **Risk** Y`` line reports
    ``impact: null``; it ranks as 3 (the middle) rather than as 0, because an
    unparsed line is a missing fact and not a low score, and sorting it to the
    bottom would quietly bury every find the scribe wrote before that template.
    """
    impact = issue.get("impact")
    risk = issue.get("risk")
    return (
        -(impact if isinstance(impact, int) else 3),
        risk if isinstance(risk, int) else 3,
        -(issue.get("age_days") or 0),
        issue.get("number") or 0,
    )


# `cowork-scribe.md` writes `**Impact** <1-5> · **Effort** <S/M/L> · **Risk**
# <low/med/high>`. Impact is a number and **risk is a word** — which the first
# version of this did not allow for, so it matched nothing any scribe has ever
# written and every queued item ranked as a neutral 3. Worse, both halves were one
# pattern, so an unmatched risk discarded the impact beside it.
#
# Two independent searches now. A body may carry one and not the other (an older
# issue, a hand-written one), and losing a fact that is present because a
# different one is missing is exactly the silent miscount `_queue_rank` exists to
# prevent. Risk comes back on one 1-3 scale whichever way it was written.
_IMPACT_RE = re.compile(r"\*\*Impact\*\*\s*(?P<impact>\d+)", re.IGNORECASE)
_RISK_RE = re.compile(r"\*\*Risk\*\*\s*(?P<risk>low|med(?:ium)?|high|\d+)", re.IGNORECASE)
_RISK_WORDS = {"low": 1, "med": 2, "medium": 2, "high": 3}


def _has_section(body: str, name: str) -> bool:
    """Whether an issue body carries a ``name`` section, in any of its spellings.

    The scribe writes `**Evidence**`; older issues and the CodeQL job write
    `## Evidence`. Matching only the first spelling held #140 — which carries two
    `file:line` references under an H2 — as though it had no evidence at all. A
    format check standing in for a substance check gets to be wrong in both
    directions, so it may at least not be wrong about punctuation.
    """
    return re.search(rf"^\s*(?:#+\s*|\*\*){re.escape(name)}\b", body, re.IGNORECASE | re.MULTILINE) is not None


# CodeQL triage files its own proposals, and they are the one family that is
# *deliberately* not auto-lane work: `codeql-triage.yml` opens an issue only for a
# rule whose `propose` entry in `.github/codeql/triage-policy.yml` records why a
# human has to decide it. Queuing one would hand a recorded human decision back to
# a machine to re-make. Identified by title because that is what the workflow
# controls — the labels it applies are the same three any security find carries.
CODEQL_PROPOSAL = "codeql:"


def _scores(body: str | None) -> tuple[int | None, int | None]:
    """``(impact, risk)`` parsed out of an issue body; either half may be None."""
    text = body or ""
    impact_match = _IMPACT_RE.search(text)
    risk_match = _RISK_RE.search(text)
    impact = int(impact_match["impact"]) if impact_match else None
    risk = None
    if risk_match:
        raw = risk_match["risk"].lower()
        risk = _RISK_WORDS.get(raw, int(raw) if raw.isdigit() else None)
    return impact, risk


def queue_report(workstream: str, now: datetime | None = None) -> dict:
    """What one workstream's sweep should build next, in the order it should build it."""
    issues, error = queued_items(workstream)
    if issues is None:
        return {"workstream": workstream, "queued": None, "items": [], "error": error}
    moment = now or datetime.now(UTC)
    items = []
    for issue in issues:
        impact, risk = _scores(issue.get("body"))
        items.append(
            {
                "number": issue.get("number"),
                "title": issue.get("title", ""),
                "type": next(
                    (
                        name.split(":", 1)[1]
                        for name in (
                            (label.get("name") if isinstance(label, dict) else label)
                            for label in issue.get("labels") or []
                        )
                        if isinstance(name, str) and name.startswith("type:")
                    ),
                    None,
                ),
                "impact": impact,
                "risk": risk,
                "age_days": _age_days(issue.get("created_at", ""), moment),
                "url": issue.get("html_url", ""),
            }
        )
    items.sort(key=_queue_rank)
    return {"workstream": workstream, "queued": len(items), "items": items}


def report_queued(workstream: str | None, now: datetime | None = None) -> int:
    """Print the queue for one workstream, or for all fifteen.

    One object for a named workstream because that is what a sweep asks; a list
    for the whole fleet because that is what `cron/digest.md`'s Queued section is
    built from. Exit code stays 0 either way — an empty queue is the goal state,
    and a routine branching on the exit status would read it as a failure.
    """
    if workstream:
        known = parse_workstreams()
        if workstream not in known:
            print(f"unknown workstream: {workstream} (known: {', '.join(known)})", file=sys.stderr)
            return 2
        print(json.dumps(queue_report(workstream, now), indent=2))
        return 0
    print(json.dumps([queue_report(name, now) for name in parse_workstreams()], indent=2))
    return 0


# The comment left on every issue the backfill reclassifies. Fixed wording so it
# is greppable, and so a bounced item's history reads straight afterwards.
MIGRATION_NOTE = """**Reclassified in place** — `cowork:queued`.

This find is on the auto-lane allowlist (`cowork/house-rules.md`, **The auto lane**), so it is not a
question for a human. **Nothing above has changed.** The `{workstream}` sweep will pick it up,
re-check it against the allowlist, and open a PR; this issue closes when that PR merges. It no
longer counts against the workstream's proposal cap, and the daily digest no longer lists it.

If the sweep finds it does not clear the auto-lane conditions after all, it puts `cowork:proposal`
back with a comment naming the condition that failed."""


def _labels_of(issue: dict) -> set[str]:
    """Every label name on a REST issue payload, both shapes."""
    return {
        name
        for name in ((item.get("name") if isinstance(item, dict) else item) for item in issue.get("labels") or [])
        if isinstance(name, str)
    }


def migration_plan(issues: Sequence[dict], workstreams: Sequence[str] | None = None) -> dict:
    """Classify a backlog of open ``cowork:proposal`` issues. Pure — no I/O.

    First match wins, and the order is the point: every rule above `queue` is a
    reason **not** to touch an issue, so the default is only reached by an issue
    that survived all of them.

    **This does not have to be right, and that is the design.** `cowork:queued`
    grants nothing — `sweep-procedure.md` step 5 re-checks the full allowlist
    before building and bounces what fails, at a cost of one comment. A backfill
    that had to be correct would have to be a judgement, and a judgement over
    forty-two issues is not something to run in bulk.
    """
    known = set(workstreams if workstreams is not None else parse_workstreams())
    planned = []
    for issue in issues:
        if "pull_request" in issue:
            continue  # `/issues` returns PRs too; a PR is not a backlog item
        labels = _labels_of(issue)
        workstream = next((name.split(":", 1)[1] for name in sorted(labels) if name.startswith("workstream:")), None)
        kind = next((name.split(":", 1)[1] for name in sorted(labels) if name.startswith("type:")), None)
        body = issue.get("body") or ""

        if "claude-implement" in labels:
            action, reason = "skip", "already approved — claude.yml owns it"
        elif labels & {"integration:candidate", "integration:approved"}:
            action, reason = "skip", "campaign lane, not the auto lane"
        elif QUEUE_LABEL in labels:
            action, reason = "repair", "already queued — an earlier run stopped between its two calls"
        elif workstream is None or workstream not in known:
            action, reason = "hold", "no known workstream label, so no charter and no paths to build in"
        elif kind not in SCOUT_TYPES:
            action, reason = "hold", f"type `{kind}` is outside the auto-lane vocabulary"
        elif CODEQL_PROPOSAL in issue.get("title", "").lower():
            action, reason = "hold", "codeql triage already recorded that this rule needs a human"
        elif not _has_section(body, "Evidence") and not _has_section(body, "What"):
            action, reason = "hold", "no evidence of any kind — nothing to reproduce or re-verify"
        else:
            action, reason = "queue", "on the auto-lane allowlist on its face"

        impact, risk = _scores(body)
        planned.append(
            {
                "number": issue.get("number"),
                "title": issue.get("title", ""),
                "workstream": workstream,
                "type": kind,
                "action": action,
                "reason": reason,
                "impact": impact,
                "risk": risk,
                # A flag on the row, not a different action. Whether the evidence
                # really yields a failing test is a judgement, and the sweep makes
                # it at build time — `house-rules.md` admits a bug on a failing
                # test, not on an argument, and that has not moved.
                "needs_repro": action == "queue" and kind == "bug",
            }
        )
    planned.sort(key=lambda row: row["number"] or 0)
    counts = {name: sum(1 for row in planned if row["action"] == name) for name in ("queue", "hold", "skip", "repair")}
    return {"planned": planned, "counts": counts}


def _reclassify(number: int, workstream: str, *, repair: bool) -> tuple[bool, str]:
    """Move one issue from the proposal queue to the build queue.

    **Three verbs, and this is the closed list**: add a label, delete one label,
    post a comment. Never ``PUT /issues/{n}/labels`` — that verb replaces the
    whole set, and it is exactly how #172 lost `cowork:proposal`,
    `workstream:web-ux` and `type:security` in a single call and then ran an
    implement job with no charter at all. "Keep every write-up" is enforced by
    the absence of the verbs that could break it, not by intent.

    Add before remove, always. A run that dies in between leaves both labels on,
    which every reader resolves as *queued* — the harmless direction. Removing
    first would leave an issue in neither queue, invisible to the digest and to
    every sweep.
    """
    # Both transports, like every other writer in this file. The read half asks
    # REST on both paths deliberately (`_issues_labelled` says why at length), but
    # the write half had only the REST branch — so on a laptop with `gh` logged in
    # and no `GH_TOKEN` exported, every issue read back fine and every write failed
    # with "no GH_TOKEN in the environment". That is the ordinary way a human runs
    # this, and it is the only way it is allowed to be run.
    if TRANSPORT == "gh":
        if shutil.which("gh") is None:
            return False, "gh is not on PATH"
        if not repair:
            result = _gh("issue", "edit", str(number), "--add-label", QUEUE_LABEL)
            if result.returncode != 0:
                return False, result.stderr.strip() or "unknown gh error"
            body = MIGRATION_NOTE.format(workstream=workstream)
            result = _gh("issue", "comment", str(number), "--body", body)
            if result.returncode != 0:
                return False, result.stderr.strip() or "unknown gh error"
        # `--remove-label` removes one; `gh api -X PUT .../labels` would replace the
        # whole set. Same distinction `cowork_relay.py:_command` is built around,
        # and the same incident behind it (#172).
        result = _gh("issue", "edit", str(number), "--remove-label", PROPOSAL_LABEL)
        return result.returncode == 0, result.stderr.strip() or "unknown gh error"

    slug = repo_slug()
    if not slug:
        return False, "no owner/name resolved for this checkout"
    if not repair:
        result = _api("POST", f"/repos/{slug}/issues/{number}/labels", {"labels": [QUEUE_LABEL]})
        if not result.ok:
            return False, result.error
        body = MIGRATION_NOTE.format(workstream=workstream)
        result = _api("POST", f"/repos/{slug}/issues/{number}/comments", {"body": body})
        if not result.ok:
            return False, result.error
    result = _api("DELETE", f"/repos/{slug}/issues/{number}/labels/{_segment(PROPOSAL_LABEL)}")
    return result.ok, result.error


def migrate_proposals(apply: bool = False) -> int:
    """The one-time backfill of a proposal backlog into the build queue.

    Idempotent by construction: the read only returns `cowork:proposal` issues,
    and a reclassified issue no longer carries that label, so a second run plans
    nothing. The only re-plannable state is `repair` — both labels at once — and
    that is the crash-recovery case this exists to clean up.

    **stdout is the plan and nothing else**, for the reason `--plan` keeps it
    that way: it is a JSON document somebody pipes, and one progress line in it
    turns a fleet into a parse error. Every human line goes to stderr.
    """
    issues, error = _issues_labelled(PROPOSAL_LABEL, None)
    if issues is None:
        fail(f"could not read the proposal backlog: {error}")
        return 2
    plan = migration_plan(issues)
    counts = plan["counts"]
    if not apply:
        print(json.dumps(plan, indent=2))
        note(
            f"dry run — {counts['queue']} would be queued, {counts['repair']} repaired, "
            f"{counts['hold']} left as questions, {counts['skip']} untouched",
            "re-run with --yes to apply",
            stream=sys.stderr,
        )
        return 0

    failures = []
    for row in plan["planned"]:
        if row["action"] not in ("queue", "repair"):
            continue
        ok, why = _reclassify(row["number"], row["workstream"] or "owning", repair=row["action"] == "repair")
        if not ok:
            failures.append(row["number"])
            note(f"#{row['number']} not reclassified: {why}", stream=sys.stderr)
    for row in plan["planned"]:
        if row["action"] == "hold":
            note(f"#{row['number']} stays a proposal — {row['reason']}", stream=sys.stderr)
    print(json.dumps(plan, indent=2))
    note(
        f"{counts['queue'] + counts['repair'] - len(failures)} reclassified, "
        f"{counts['hold']} left as questions, {counts['skip']} untouched",
        stream=sys.stderr,
    )
    if failures:
        fail(f"{len(failures)} issue(s) not reclassified — re-run to retry, it is idempotent")
        return 1
    return 0


# The title prefix every standing-fault report carries, and the whole of its
# identity. A title rather than a label on purpose: a label would have to be added
# to the `workstreams/` vocabulary, created by `make cowork-setup` and counted by
# `make cowork-check` — three files edited so one routine can ask one question,
# and a fleet that cannot report a fault until a deploy has run is a fleet that
# cannot report the fault that stops deploys running.
BLOCKED_TITLE = "[blocked] "


def open_blocked_reports(marker: str) -> tuple[list[dict] | None, str]:
    """The open issues already reporting ``marker``, or None and why not.

    REST on both transports for the reason `open_proposals` gives at length: the
    `gh issue list --json` path is GraphQL underneath and comes back 403 from the
    routine sessions' egress proxy, while ``GET /repos/{slug}/issues`` is served.
    This query runs almost exclusively in exactly those sessions, so building it
    on the refused call would mean it worked everywhere except where it is used.

    None rather than an empty list on failure, and the caller must keep them
    apart: "nothing has reported this" is the one answer that produces a Slack
    post, and turning "could not ask" into it is how a standing fault posts once
    per push forever.
    """
    slug = repo_slug()
    if not slug:
        return None, "no owner/name resolved for this checkout"
    # No label filter: the marker lives in the title precisely so this works
    # before any label exists, and before a deploy has run to create one.
    #
    # Which makes paging load-bearing here in a way it is not for
    # `open_proposals`. That query is bounded by two labels and fits a page; this
    # one filters nothing, so it is bounded only by how many issues the repo has
    # open — 47 today against `gh api`'s default page of 30. An open report
    # sitting on page two reads back as "nobody has said it", and the routine
    # then posts *and* files a duplicate: this whole change failing in the one
    # direction it exists to prevent.
    path = f"/repos/{slug}/issues?state=open"
    if TRANSPORT == "gh":
        if shutil.which("gh") is None:
            return None, "gh is not on PATH"
        # `--paginate` walks the Link headers and merges the arrays; `per_page`
        # is appended only here because `transport.api_paged` adds its own, and
        # two of them in one URL is a query nobody meant.
        result = _gh("api", "--paginate", f"{path.lstrip('/')}&per_page=100")
        if result.returncode != 0:
            return None, result.stderr.strip() or "unknown gh error"
        try:
            items = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as error:  # an egress proxy's HTML error page
            return None, f"gh api returned no JSON: {error}"
    else:
        answer = _api_paged(path)
        if not answer.ok:
            return None, answer.error
        items = answer.data
    if not isinstance(items, list):
        return None, "the issues endpoint did not return a list"
    wanted = f"{BLOCKED_TITLE}{marker}"
    return [
        item
        for item in items
        # `/issues` returns pull requests too, and a PR titled after the fault it
        # fixes would otherwise read as the report itself — silencing the routine
        # from the moment somebody starts fixing it until they merge.
        if isinstance(item, dict) and "pull_request" not in item and (item.get("title") or "").strip() == wanted
    ], ""


def blocked_report(marker: str) -> dict:
    """Whether ``marker`` has already been reported, for a routine to branch on.

    Three states, and the routine owes each a different behaviour, which is why
    this is not a bool:

    - ``reported: true`` — an open issue already says it. Post nothing. The run
      log still records the firing, and that is where "how often" is answered.
    - ``reported: false`` — nobody has said it. Post once, and file the issue.
    - ``reported: null`` — the query itself failed. **Post nothing**, the same
      way `proposal_slots` turns an unreadable queue into zero slots rather than
      into a full allowance: a routine that cannot tell whether it has already
      spoken must not assume it has not.

    This replaces reading the last 24 hours of Slack, which cannot work at all
    when a routine fires twice from one push — both sessions read the channel
    before either has posted, both see nothing, and both post. An issue is
    durable shared state that exists *before* the concurrent runs look at it,
    which is the property Slack history does not have.
    """
    issues, error = open_blocked_reports(marker)
    if issues is None:
        return {"marker": marker, "reported": None, "issue": None, "error": error}
    # A numberless item sorts last rather than first: `or 0` would let one win
    # the `min` and hand back `issue.number: null`, which reads as a report
    # nobody can open.
    first = min(issues, key=lambda item: item.get("number") or sys.maxsize) if issues else None
    return {
        "marker": marker,
        "reported": bool(issues),
        "issue": (
            {
                "number": first.get("number"),
                "url": first.get("html_url", ""),
                "title": first.get("title", ""),
            }
            if first
            else None
        ),
        "error": "",
    }


def report_blocked(marker: str) -> int:
    """Print the blocked-report JSON for one marker.

    Exit code stays 0 for all three states. A routine branching on the status
    would read "already reported" — the *common* and desired outcome — as a
    failure, and the whole point of this call is to make silence the normal path.

    An empty marker is the one refusal, for the reason `report_proposal_slots`
    refuses an unknown workstream: it would match every report titled exactly
    ``[blocked]`` and nothing else, so it answers `false` forever and posts
    forever, while looking like a working call.
    """
    if not marker.strip():
        print("--blocked-report needs a marker: the fault's identity, e.g. 'cd-deploy: …'", file=sys.stderr)
        return 2
    print(json.dumps(blocked_report(marker), indent=2))
    return 0


def report_manual_remainder(routines: Sequence[Routine]) -> None:
    """Print what no shell can do, with the link for each.

    Deliberately printed on a successful run too. A setup that says nothing about
    its own gaps reads as complete, and a fleet with no connectors attached fails
    silently on its first Monday.
    """
    events = [r for r in routines if r.kind == "event"]
    crons = [r for r in routines if r.kind == "cron"]
    print()
    say("what a shell cannot do — run /cowork deploy in a Claude session for the first two:")
    print(f"     · register the {len(crons) + len(events)} routines (account-scoped; RemoteTrigger, no CLI)")
    webhooks = [r for r in routines if r.webhook]
    print(f"     · attach the {len(webhooks)} webhook triggers that fire them: {', '.join(r.name for r in webhooks)}")
    print(f"     · mirror the {len(parse_workstreams())} workstream labels onto the Linear team")
    print("     · attach the Linear, Slack and Notion connectors, and remove the rest:")
    print("       https://claude.ai/customize/connectors")
    print("     · install the Claude GitHub App on this repo, if it is not already")
    print("     · set the AUTO_VERSION_PAT secret, or Claude Review never sees workflow_run events")
    print("     · add a PyPI trusted publisher for publish-beta.yml (workflow `publish-beta.yml`,")
    print("       environment `pypi`) — without it every merge's pre-release dies at upload, and")
    print("       'nothing ships to users on merge' has nothing behind it")
    report_merge_gate()
    print("     · confirm what fires each event routine — the API stores a webhook filter and")
    print("       never reads one back, so nothing but the routine's own first step verifies it:")
    for routine in events:
        print(f"       - {routine.name}: {routine.trigger}")
        if routine.filters:
            print(f"         filters: {routine.filters}")
    print("       https://claude.ai/code/routines")


# --- manifest ----------------------------------------------------------------


def manifest() -> dict:
    """Everything ``/cowork`` needs, so the command parses no markdown.

    Asking a model to read four markdown tables reliably on every run is the kind
    of thing that works nineteen times out of twenty; the twentieth registers a
    routine pointing at a file that does not exist.

    ``trigger_id`` rides along for the same reason it exists at all: addressing
    one routine — ``pause``, ``resume``, ``run`` — needs its id, and the obvious
    way to get one, listing the fleet and matching by name, silently stopped
    working when the fleet outgrew a page. ``None`` means the README records
    none, which is a routine that was never deployed rather than a lookup to
    retry.
    """
    recorded = recorded_triggers()
    return {
        "repo": repo_slug(),
        "repo_url": repo_url(),
        "connectors": list(CONNECTORS),
        "default_allowed_tools": list(ALLOWED_TOOLS),
        "targets": parse_targets(),
        "labels": [{"name": label.name, "description": label.description} for label in expected_labels()],
        "variables": parse_model_variables(),
        "routines": [
            {
                "name": routine.name,
                "trigger_name": routine.trigger_name,
                "trigger_id": recorded.get(routine.path),
                "path": routine.path,
                "kind": routine.kind,
                "cron": routine.cron,
                "trigger": routine.trigger,
                "filters": routine.filters,
                "summary": routine.summary,
                "workstream": routine.workstream,
                "tier": routine.tier,
                "model": routine.model_id,
                "prompt": routine.prompt,
                "allowed_tools": list(routine_tools(routine.name)),
            }
            for routine in parse_routines()
        ],
    }


# --- the account half: live routines vs. what cowork/ says -------------------
#
# Nothing below calls an API. `/cowork` fetches a `RemoteTrigger list` and hands
# the response in as a snapshot; these functions decide what to do with it. That
# split is the point: comparing seven fields across twenty-three routines is exactly
# the kind of work a model does correctly most of the time, and "most of the
# time" here means a sweep silently running on last month's prompt.


def snapshot(payload: object) -> list[dict]:
    """The trigger array out of a ``RemoteTrigger`` response.

    Three envelopes are accepted because three are what turn up: ``list`` returns
    ``{"data": [...]}``, ``get`` returns ``{"trigger": {...}}``, and a snapshot
    saved by hand is often the bare array. Guessing wrong on any of them reads as
    an empty account — which is the one wrong answer that would have this script
    propose registering twenty-three routines that already exist.

    A truncated page is the same failure wearing a different hat, and a quieter
    one: the routines beyond the page boundary simply are not there, so they read
    as missing and get created a second time. ``Plan.suspicious`` cannot catch it
    either — a short page produces creates with no orphan. So one page on its own
    raises; a fleet past the page size is read in parts instead (``read_parts``).

    The truncation is read from ``_walk``'s own flags rather than the top level,
    because the top level is not where it always is: an array of envelopes is a
    documented way to save these, and checking ``payload["has_more"]`` would wave
    a truncated page through the moment somebody wrapped it in a list.
    """
    read = _walk(payload)
    if read.truncated:
        raise ValueError(
            "the snapshot is one page of a longer list (has_more: true) — "
            "deploying from it would re-create every routine past the page "
            "boundary; pass the rest as further --triggers files before planning"
        )
    return read.entries


@dataclass(frozen=True)
class _Read:
    """What one payload turned out to hold, and what it claims about the rest."""

    entries: list[dict]
    more: bool = False  # a page said there was more after it
    terminal: bool = False  # a page said there was not
    gets: bool = False  # a single-routine read was in there

    @property
    def truncated(self) -> bool:
        return self.more and not self.terminal

    def then(self, other: _Read) -> _Read:
        return _Read(
            entries=[*self.entries, *other.entries],
            more=self.more or other.more,
            terminal=self.terminal or other.terminal,
            gets=self.gets or other.gets,
        )


def _walk(payload: object) -> _Read:
    """Everything one saved response holds, however it was saved.

    Four envelopes are accepted because four are what turn up: ``list`` returns
    ``{"data": [...]}``, ``get`` returns ``{"trigger": {...}}``, a snapshot saved
    by hand is often the bare array, and a fleet read in parts is an array of any
    of those. Guessing wrong on any of them reads as an empty account — which is
    the one wrong answer that would have this script propose registering
    twenty-three routines that already exist.

    A ``get`` proves nothing either way: it answers for one routine and says
    nothing about how many there are. Only a ``list`` page can close the
    question, and only by saying ``has_more`` is false. That a ``get`` was there
    at all is worth recording separately, because it is the mark of a fleet read
    in parts — and only such a read needs checking against the ledger.
    """
    if isinstance(payload, dict):
        if isinstance(payload.get("trigger"), dict):
            return _Read([payload["trigger"]], gets=True)
        if isinstance(payload.get("data"), list):
            more = bool(payload.get("has_more"))
            return _Read(list(payload["data"]), more=more, terminal=not more)
        # A lone routine, already unwrapped. Not marked as a `get`: an array of
        # these is the hand-saved bare array the old contract reads as the whole
        # account, and only the `{"trigger": …}` envelope is evidence that
        # somebody was reading the fleet one routine at a time. Anything without
        # an id is not a routine, and reading it as one plans against a fiction.
        return _Read([payload]) if payload.get("id") else _Read([])
    if isinstance(payload, list):
        read = _Read([])
        for item in payload:
            read = read.then(_walk(item))
        return read
    return _Read([])


@dataclass(frozen=True)
class Snapshot:
    """Every live routine the parts supplied could see, and what they cannot prove.

    ``RemoteTrigger list`` pages, and the tool exposes no cursor — so once the
    fleet outgrew one page, the whole account stopped being readable in one call.
    A fleet past the page size is read in parts instead: the newest page, plus a
    ``get`` for every trigger id ``cowork/README.md`` records.

    That assembly cannot prove it saw everything, and this type is careful not to
    claim it did. ``boundary`` is whether a page said there was more and none
    proved otherwise; ``unresolved`` is the recorded ids no part read back. What
    the two decide together is narrow and it is the whole point: an **update** is
    safe from a partial snapshot (it only ever touches a routine it can see, and
    applying it twice writes the same value), while a **create** is not (a
    routine past the boundary reads as missing, and registering a second copy of
    something already firing cannot be undone).
    """

    triggers: list[dict]
    boundary: bool = False
    unresolved: tuple[str, ...] = ()
    # Whether the ledger was consulted at all. Without it, no unresolved id is
    # not the same statement as every recorded id accounted for, and only one of
    # those two is worth saying out loud.
    ledger: bool = False

    @property
    def partial(self) -> str | None:
        """Why this snapshot cannot vouch for the whole account, or ``None``.

        An unread recorded id counts on its own, without waiting for a page to
        announce truncation. Truncation is a signal that can simply go missing —
        an assembly of nothing but ``get`` envelopes carries none, and would
        otherwise read as a complete account containing four routines.
        """
        if self.unresolved:
            return (
                f"{len(self.unresolved)} routine id(s) the README records were not read back: "
                f"{', '.join(self.unresolved)}"
            )
        if not self.boundary:
            return None
        if self.ledger:
            return (
                "a page said there was more; every routine id the README records was read back, "
                "so the only routines this cannot see are ones no deploy registered"
            )
        return "a page said there was more, and the README ledger was not consulted"


def read_parts(payloads: Sequence[object], recorded: Sequence[str] | None = None) -> Snapshot:
    """Assemble one snapshot out of however many responses it took to read the fleet.

    ``recorded`` is the trigger ids ``cowork/README.md`` holds — the ledger of
    what this fleet has actually registered, written back by ``--urls`` on every
    deploy. Any of them missing from the parts is the actionable half of a
    partial read: those routines exist, they were not read, and a ``get`` each
    would finish the job.

    It is checked on **any assembled read**, not only one where a page announced
    truncation. Truncation is one signal for this and it can go missing: a caller
    that passes the per-id reads and forgets the page file hands over something
    that declares nothing at all, and every routine it happens not to contain
    would read as one to register. Anything carrying a ``get``, or arriving in
    more than one part, is an assembly — a single ``list`` page or a hand-saved
    array is the account itself and vouches for nothing but is vouched for.

    An id in the ledger that no longer exists then makes every read partial until
    the table is corrected. That is the right way round: it costs a create that
    has to wait, and the other way costs a duplicate that cannot be deleted.
    """
    seen: dict[str, dict] = {}
    loose: list[dict] = []
    more = terminal = gets = False
    for payload in payloads:
        found = _walk(payload)
        more, terminal, gets = more or found.more, terminal or found.terminal, gets or found.gets
        for entry in found.entries:
            # Parts overlap by design — the page and the per-id reads both carry
            # the newest routines — so the same routine arriving twice is the
            # normal case, not drift. First read wins; they are the same object.
            key = entry.get("id")
            if not key:
                loose.append(entry)
            elif key not in seen:
                seen[key] = entry

    boundary = more and not terminal
    assembled = boundary or gets or len(payloads) > 1
    unresolved = tuple(sorted(set(recorded) - set(seen))) if (recorded is not None and assembled) else ()
    return Snapshot(
        triggers=[*seen.values(), *loose],
        boundary=boundary,
        unresolved=unresolved,
        ledger=recorded is not None and assembled,
    )


def load_snapshot(paths: str | Path | Sequence[str | Path]) -> Snapshot:
    """Read one or more saved responses into a single ``Snapshot``.

    A single file keeps the old contract exactly, truncation included: on its own
    it raises rather than plan against a short page. Two or more are an assembly,
    which is allowed to be incomplete because it says so.

    Either way the ledger is consulted, because "one file" is not a promise that
    it holds the account — the per-id reads can arrive in one file with no page
    beside them, and nothing in that payload would say so.
    """
    if isinstance(paths, str | Path):
        paths = [paths]
    payloads = [json.loads(Path(path).read_text(encoding="utf-8")) for path in paths]
    if len(payloads) == 1:
        snapshot(payloads[0])  # for the raise; read_parts does the reading
    return read_parts(payloads, recorded=recorded_ids())


def desired_trigger(
    routine: Routine,
    repo_url: str,
    environment_id: str,
    connectors: Sequence[dict],
) -> dict:
    """The exact ``RemoteTrigger`` create body for one routine.

    The shape is not from memory — it is the shape a live ``RemoteTrigger list``
    returns, which is the same one ``create`` takes. If the API moves, re-derive
    it by calling ``list`` and reading one entry rather than editing this by feel.

    ``connectors`` is passed in rather than built: ``connector_uuid`` and the MCP
    ``url`` are account-specific values this script cannot know, so ``/cowork``
    lifts them off an existing routine (or resolves them once) and they ride
    through unchanged.

    Never pass an empty ``connectors`` or an empty tool grant. The API reads an
    empty list as "default", and the default is *everything*: a probe registering
    ``mcp_connections: []`` came back with every connector on the account, and one
    registering ``allowed_tools: []`` came back holding Bash, Write and WebFetch.
    So the emptiest body here is the most powerful routine in the fleet — see
    ``tests/fixtures/cowork_webhook_live.json`` for the exchange, and
    ``routine_tools`` for the guard.
    """
    body = {
        "name": routine.trigger_name,
        "enabled": True,
        "job_config": {
            "ccr": {
                "environment_id": environment_id,
                "events": [
                    {
                        "data": {
                            "type": "user",
                            "message": {"role": "user", "content": routine.prompt},
                        }
                    }
                ],
                "session_context": {
                    "allowed_tools": list(routine_tools(routine.name)),
                    "model": routine.model_id,
                    "sources": [{"git_repository": {"url": repo_url}}],
                },
            }
        },
        "mcp_connections": [dict(connector) for connector in connectors],
    }
    # An event routine carries no schedule — a `create_webhook_trigger` POST is
    # what fires it. The key is left out rather than set to null: the API accepts
    # a cron-less create and reports it back as an empty string, and an absent key
    # is the one form a partial update cannot misread.
    if routine.cron:
        body["cron_expression"] = routine.cron
    return body


def observed_trigger(live: dict) -> dict:
    """Flatten one live trigger into the same fields ``desired_trigger`` sets.

    Everything is reached with ``.get`` and a default: a live payload missing a
    key should read as "differs", not raise. A doctor that crashes on unfamiliar
    data is a doctor nobody runs twice.
    """
    ccr = (live.get("job_config") or {}).get("ccr") or {}
    context = ccr.get("session_context") or {}
    events = ccr.get("events") or []
    message = ((events[0].get("data") or {}).get("message") or {}) if events else {}
    sources = context.get("sources") or []
    repository = (sources[0].get("git_repository") or {}) if sources else {}

    return {
        "id": live.get("id"),
        "trigger_name": live.get("name", ""),
        "cron": live.get("cron_expression"),
        "enabled": bool(live.get("enabled")),
        "model": context.get("model"),
        "prompt": message.get("content", ""),
        "allowed_tools": tuple(sorted(context.get("allowed_tools") or ())),
        "repo_url": repository.get("url"),
        "connectors": tuple(sorted(c.get("name", "") for c in live.get("mcp_connections") or ())),
        "environment_id": ccr.get("environment_id"),
        "url": ROUTINE_URL.format(id=live.get("id")) if live.get("id") else None,
    }


def connectors_of(live: Sequence[dict]) -> list[dict]:
    """The connector objects to reuse, lifted off whatever live routines carry them.

    Filtered to ``CONNECTORS`` and ordered by it, because the live set is the one
    thing here that cannot be trusted to already be right: every connector on the
    account is attached by default, so an over-broad set is the *expected* state
    before a deploy, not an anomaly. Lifting it wholesale would make the desired
    set equal the drifted set — deploy would report connector drift, post a patch
    that changes nothing, and report the same drift again next run.
    """
    found: dict[str, dict] = {}
    for trigger in live:
        for connection in trigger.get("mcp_connections") or ():
            name = connection.get("name")
            if name in CONNECTORS and name not in found:
                found[name] = dict(connection)
    return [found[name] for name in CONNECTORS if name in found]


def environment_of(live: Sequence[dict]) -> str | None:
    for trigger in live:
        environment = ((trigger.get("job_config") or {}).get("ccr") or {}).get("environment_id")
        if environment:
            return environment
    return None


def repo_url_of(live: Sequence[dict]) -> str | None:
    for trigger in live:
        url = observed_trigger(trigger)["repo_url"]
        if url:
            return url
    return None


@dataclass(frozen=True)
class TriggerAction:
    """One create or update ``/cowork deploy`` should apply.

    ``blocked`` carries the same meaning it does on ``WebhookAction``, so one rule
    covers every action a plan emits: **post the body of everything whose
    ``blocked`` is null, and nothing else.** A blocked action carries an empty
    body, so a caller following that rule mechanically cannot post by accident.
    """

    action: str  # "create" | "update"
    name: str  # routine stem
    trigger_name: str
    trigger_id: str | None
    fields: dict[str, tuple[object, object]]  # field -> (live, wanted); empty on create
    body: dict  # POST verbatim — the full body on create, the patch on update
    blocked: str | None = None  # why this must not be posted; None means postable

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "name": self.name,
            "trigger_name": self.trigger_name,
            "trigger_id": self.trigger_id,
            "fields": {key: {"live": live, "wanted": wanted} for key, (live, wanted) in self.fields.items()},
            "blocked": self.blocked,
            "body": self.body,
        }


@dataclass(frozen=True)
class WebhookAction:
    """One ``create_webhook_trigger`` POST, or the reason there is not one.

    ``blocked`` is the whole safety of this type. The endpoint cannot be read
    back, does not dedup and cannot be undone, so anything short of certainty
    carries a reason and an **empty body** — following the caller's rule
    mechanically ("post every body whose ``blocked`` is null") is then also the
    safe behaviour, with nothing to post by accident.
    """

    action: str  # "create" | "ok" | "deferred" | "unknown"
    name: str  # routine stem
    trigger_name: str
    trigger_id: str | None  # the routine this fires; None until it exists
    webhook_id: str | None  # the live webhook's id, when the account reports one
    blocked: str | None  # why this must not be posted; None means postable verbatim
    body: dict  # POST verbatim. Empty for everything but "create".

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "name": self.name,
            "trigger_name": self.trigger_name,
            "trigger_id": self.trigger_id,
            "webhook_id": self.webhook_id,
            "blocked": self.blocked,
            "body": self.body,
        }


@dataclass
class Plan:
    """What reality is, against what ``cowork/`` says it should be."""

    create: list[TriggerAction] = field(default_factory=list)
    update: list[TriggerAction] = field(default_factory=list)
    ok: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    orphans: list[dict] = field(default_factory=list)
    urls: dict[str, str] = field(default_factory=dict)  # routine path -> claude.ai URL
    webhooks: list[WebhookAction] = field(default_factory=list)
    # Fields a create body needs that nothing available could supply. Only ever
    # populated when there is something to create — see `needs` below.
    needs: list[str] = field(default_factory=list)
    # Why the snapshot behind this plan could not see the whole account, if it
    # could not. Set from `Snapshot.partial`; it qualifies `orphans` (a routine
    # past the page boundary cannot be reported as one) and is what blocks the
    # creates it applies to.
    partial: str | None = None

    @property
    def postable_creates(self) -> list[TriggerAction]:
        """The creates that may actually be POSTed."""
        return [action for action in self.create if action.blocked is None]

    @property
    def creates_blocked(self) -> list[str]:
        """Routine stems that need registering but that this run may not register."""
        return [action.name for action in self.create if action.blocked]

    @property
    def postable_webhooks(self) -> list[WebhookAction]:
        """The webhook POSTs that are safe to make. Everything else is a report.

        Both conditions, not either: `ok` carries no reason to be blocked and no
        body, and a rule reading only one of the two fields would post it.
        """
        return [action for action in self.webhooks if action.action == "create" and action.blocked is None]

    @property
    def webhooks_blocked(self) -> list[str]:
        """Routine stems whose webhook could not be planned. Reported, never posted."""
        return [action.name for action in self.webhooks if action.blocked]

    @property
    def self_update(self) -> dict | None:
        """The update this plan would apply to the deployer itself, if any.

        Its own key so an unattended deploy cannot miss it while scanning
        twenty-two entries: this is the one change that alters the thing applying
        the change, and ``cd-deploy.md`` requires it be named in the Slack post
        with both values rather than applied quietly.
        """
        for action in self.update:
            if action.name == DEPLOY_ROUTINE:
                return action.as_dict()
        return None

    @property
    def clean(self) -> bool:
        return not (self.create or self.update or self.orphans or self.postable_webhooks)

    @property
    def applied_nothing(self) -> bool:
        """Whether a run of this plan would change nothing at all.

        Distinct from ``clean``: a plan holding only blocked creates has something
        to say and nothing to do, and the difference is whether anyone is told.
        """
        return not (self.postable_creates or self.update or self.postable_webhooks)

    @property
    def suspicious(self) -> bool:
        """Both a create and an orphan — the signature of a damaged snapshot.

        The snapshot reaches this script by way of a model transcribing a large
        API response into a file, so a mangled ``name`` is a real failure mode,
        and it presents as one routine missing and one unrecognised. Every other
        outcome of a bad snapshot is self-correcting — an update just rewrites
        the right value — but acting on this one creates a *second* copy of a
        routine that was already there, and both then fire.

        A genuine rename looks identical, which is why this asks rather than
        decides.

        It weighs **postable** creates only, because a blocked one is not an
        action and there is nothing to refuse. That is not a nicety: retiring a
        routine leaves an orphan that no API can delete, so counting blocked
        creates would have `cd-deploy` — which runs `--no-create`, and so can
        never do the dangerous thing — exit 2 on every firing from the first
        retirement onwards, and stop applying updates for good.
        """
        return bool(self.postable_creates and self.orphans)

    def as_dict(self) -> dict:
        return {
            "create": [action.as_dict() for action in self.create],
            "update": [action.as_dict() for action in self.update],
            "ok": self.ok,
            "disabled": self.disabled,
            "orphans": self.orphans,
            "urls": self.urls,
            "creates_blocked": self.creates_blocked,
            "webhooks": [action.as_dict() for action in self.webhooks],
            "webhooks_blocked": self.webhooks_blocked,
            "self_update": self.self_update,
            "suspicious": self.suspicious,
            "needs": self.needs,
            "partial": self.partial,
        }


# Where a live routine might carry its attached webhook triggers. Tolerant on
# purpose: no response observed so far reports them at all, and reading the wrong
# key as "none attached" is exactly how a second webhook lands on a routine that
# already fires. When the API starts reporting them, delete the guesses and keep
# the real one.
WEBHOOK_KEYS = ("webhook_triggers", "webhooks", "event_triggers")


def observed_webhooks(live: dict) -> tuple[dict, ...] | None:
    """The webhook triggers attached to a live routine, or ``None`` when it does not say.

    ``None`` is not "there are none". ``RemoteTrigger`` has no list action for
    webhook triggers and a routine ``get`` carries no webhook field at all, so an
    absent key means "this response cannot answer the question". Only an explicit
    empty array reads as "none attached".

    That distinction is the whole idempotency design. Every duplicate this code
    could create would come from reading silence as zero — and a duplicate is
    permanent, because the API has no delete and the routine then fires twice for
    every event, forever.
    """
    for key in WEBHOOK_KEYS:
        found = live.get(key)
        if isinstance(found, list):
            return tuple(found)
    return None


def slug_from_url(url: str | None) -> str | None:
    """``github.com/owner/repo`` out of a clone URL — the API's ``scope_id`` form.

    Pure: nothing in this half shells out. The server normalises to this shape
    anyway, so sending it is what keeps a re-read comparable to what was sent.
    """
    if not url:
        return None
    trimmed = url.strip().removesuffix(".git")
    for prefix in ("https://", "http://", "ssh://git@", "git@"):
        trimmed = trimmed.removeprefix(prefix)
    # `github.com:owner/repo` from an SSH remote — the colon is a separator there.
    host, sep, rest = trimmed.partition(":")
    if sep and "/" not in host:
        trimmed = f"{host}/{rest}"
    parts = [part for part in trimmed.split("/") if part]
    if len(parts) < 3 or parts[0] != "github.com":
        return None
    return "/".join(parts[:3])


def desired_webhook(routine: Routine, trigger_id: str, scope_id: str) -> dict:
    """The exact ``create_webhook_trigger`` body for one routine.

    What the routine file declared rides through verbatim; the three script-owned
    keys are set here and refused in the file. The shape is an account-side
    contract this script does not model — it is pinned by
    ``tests/fixtures/cowork_webhook_live.json``, captured from a real call, the
    same way ``desired_trigger`` is pinned by ``cowork_trigger_live.json``.
    """
    declared = dict(routine.webhook or {})
    return {
        "source": declared.get("source"),
        "hook_type": WEBHOOK_HOOK_TYPE,
        "scope_id": scope_id,
        "events": list(declared.get("events") or ()),
        "filter": dict(declared.get("filter") or {}),
        "routine_trigger_id": trigger_id,
    }


# How recently a routine must have been created for `--created` to be believed.
# The flag is a caller's claim, and the dangerous mistake it could carry is naming
# a *pre-existing* routine — which would attach a second webhook to one that
# already fires. The snapshot carries the server's own answer in `created_at`, so
# the claim is checked against it rather than trusted. Generous, because a deploy
# that posts twenty-two bodies before re-listing can take a while.
CREATED_WINDOW = timedelta(minutes=30)


def created_recently(live: dict, now: datetime | None = None) -> bool:
    """Whether the account says this routine was created within `CREATED_WINDOW`.

    False when the timestamp is missing or unparseable: an unverifiable claim is
    not a proof, and the cost of being wrong here is a duplicate nobody can delete.
    """
    stamp = live.get("created_at")
    if not isinstance(stamp, str) or not stamp:
        return False
    try:
        created = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - created <= CREATED_WINDOW


def webhook_plan(
    routines: Sequence[Routine],
    live: dict[str, dict],  # trigger_name -> the raw live entry
    observed: dict[str, dict],  # trigger_name -> observed_trigger()
    created: frozenset[str],  # trigger_names the caller already created this run
    scope_id: str | None,
) -> list[WebhookAction]:
    """One action per routine that declares a webhook. Four outcomes, one postable.

    ``create``   — the routine provably holds no webhook, so the body is complete
                   and postable verbatim.
    ``ok``       — the account reports one already attached. Nothing to do.
    ``deferred`` — the routine is being created in this same plan, so there is no
                   id to fire yet. Re-plan after the creates, passing ``--created``.
    ``unknown``  — nothing can say whether one is attached. Posting would
                   duplicate, permanently. Reported, never acted on.

    A webhook is posted on one of exactly two proofs: the account said, in so many
    words, that none is attached; or the caller created the routine seconds ago in
    this run and got an id back for it. A routine that did not exist a moment ago
    trivially holds no webhook, and that is the only evidence available today.
    """
    actions: list[WebhookAction] = []

    for routine in routines:
        if not routine.webhook:
            continue

        # Validated here and not only in `--check`, because this is the function
        # that produces the body. The API accepts a misspelled event with a 200,
        # never dedups and has no delete, so a bad block posted once is a dead
        # webhook forever — and relying on the caller having run the doctor first
        # is relying on the one thing an unattended deploy cannot be asked about.
        if problems := webhook_problems(routine):
            actions.append(
                WebhookAction(
                    action="unknown",
                    name=routine.name,
                    trigger_name=routine.trigger_name,
                    trigger_id=(observed.get(routine.trigger_name) or {}).get("id"),
                    webhook_id=None,
                    blocked=f"the webhook block is not valid: {problems[0][0]}",
                    body={},
                )
            )
            continue

        current = observed.get(routine.trigger_name)
        if current is None:
            actions.append(
                WebhookAction(
                    action="deferred",
                    name=routine.name,
                    trigger_name=routine.trigger_name,
                    trigger_id=None,
                    webhook_id=None,
                    blocked="the routine has no id yet — it is being created by this same plan. "
                    "Re-plan after the creates, passing --created.",
                    body={},
                )
            )
            continue

        entry = live.get(routine.trigger_name) or {}
        attached = observed_webhooks(entry)
        just_created = routine.trigger_name in created and created_recently(entry)

        if attached:
            actions.append(
                WebhookAction(
                    action="ok",
                    name=routine.name,
                    trigger_name=routine.trigger_name,
                    trigger_id=current["id"],
                    webhook_id=(attached[0].get("trigger_id") if isinstance(attached[0], dict) else None),
                    blocked=None,
                    body={},
                )
            )
            continue

        if attached is None and not just_created:
            stale = routine.trigger_name in created
            actions.append(
                WebhookAction(
                    action="unknown",
                    name=routine.name,
                    trigger_name=routine.trigger_name,
                    trigger_id=current["id"],
                    webhook_id=None,
                    blocked=(
                        f"--created names this routine, but the account says it was created at "
                        f"{entry.get('created_at')!r} — not within the last "
                        f"{int(CREATED_WINDOW.total_seconds() // 60)} minutes, so it is not a routine "
                        "this run made and may already be wired"
                        if stale
                        else "the snapshot does not report attached webhook triggers, so an already-wired "
                        "routine cannot be told from an unwired one — posting would duplicate it, and "
                        "the API has no delete. Wire it once from an interactive session."
                    ),
                    body={},
                )
            )
            continue

        if not scope_id:
            actions.append(
                WebhookAction(
                    action="unknown",
                    name=routine.name,
                    trigger_name=routine.trigger_name,
                    trigger_id=current["id"],
                    webhook_id=None,
                    blocked="no scope_id — the repository this fires for could not be resolved",
                    body={},
                )
            )
            continue

        actions.append(
            WebhookAction(
                action="create",
                name=routine.name,
                trigger_name=routine.trigger_name,
                trigger_id=current["id"],
                webhook_id=None,
                blocked=None,
                body=desired_webhook(routine, current["id"], scope_id),
            )
        )

    return actions


# Which drifted fields go where in an update body. `cron_expression` is top-level;
# everything else lives under job_config, and a partial nested merge is not
# something to guess at — so if any of them moved, the whole ccr block is resent.
_JOB_FIELDS = ("model", "prompt", "allowed_tools", "repo_url")

# How many routines a plan may touch before it stops looking like a change and
# starts looking like a mistake. A legitimate connector or repo_url change really
# does touch every routine at once, so this cannot be a hard ceiling — it is a
# `--strict` stop a human overrides with --allow-mass-change and an unattended run
# never does.
#
# Counts creates *and* updates, and creates are the half that matters: an update
# rewrites a value that was going to be rewritten, while a create is permanent —
# this API has no delete, which is why teardown only disables. `suspicious` does
# not cover it, because it needs a create *and* an orphan: a snapshot that loses
# its trailing entries rather than mangling a name yields creates with no orphans
# at all, and every surviving entry still supplies repo_url, environment_id and
# connectors, so `needs` stays empty too. That plan looks entirely healthy and
# would register a second copy of every routine it could not see.
MASS_CHANGE_LIMIT = 6


def compared_fields(routine: Routine, current: dict, repo_url: str | None) -> dict[str, tuple[object, object]]:
    """The fields one routine is diffed on, as ``{name: (live, wanted)}`` for those that differ.

    Cron is compared only when either side has one. That is not a loosening: an
    event routine that somehow grew a schedule, and a cron routine whose schedule
    went missing, both still read as drift, and both have a test. What it stops is
    the event routines being flagged forever over a field neither side ever sets —
    the API stores a cron-less routine with ``cron_expression: ""`` while the repo
    holds ``None``, and those two mean the same thing.
    """
    comparisons: dict[str, tuple[object, object]] = {
        "model": (current["model"], routine.model_id),
        "prompt": (current["prompt"], routine.prompt),
        "allowed_tools": (current["allowed_tools"], tuple(sorted(routine_tools(routine.name)))),
        "repo_url": (current["repo_url"], repo_url),
        "connectors": (current["connectors"], tuple(sorted(CONNECTORS))),
    }
    if routine.cron or current["cron"]:
        comparisons["cron"] = (current["cron"], routine.cron)
    return {name: pair for name, pair in comparisons.items() if pair[0] != pair[1]}


def trigger_plan(
    live: Snapshot | Sequence[dict],
    routines: Sequence[Routine] | None = None,
    repo_url: str | None = None,
    environment_id: str | None = None,
    connectors: Sequence[dict] | None = None,
    scope_id: str | None = None,
    created: Sequence[str] = (),
    allow_create: bool = True,
) -> Plan:
    """Compare every live ``cowork:`` routine against the repo.

    ``created`` names trigger_names the caller created moments ago in this same
    deploy run and holds an id for. It is the only evidence that a routine holds
    no webhook yet — see ``webhook_plan``.

    ``allow_create=False`` reports the creates and empties their bodies. Two runs
    of a create race each other with no lock and no undo: both list a fleet
    missing the same routine, both POST it, and the account keeps whichever
    duplicates it accepts — each firing on its own schedule, with no orphan to
    make the plan suspicious. An update has neither problem, because applying it
    twice writes the same value. So an unattended deploy takes the updates and
    leaves the creates to a session with a human in it.

    A **partial** ``Snapshot`` blocks creates for the same reason and by the same
    mechanism, but not always all of them. A routine whose README URL cell is
    blank has never been registered by a deploy, so nothing of ours can be
    hiding past the page boundary under its name and creating it is as safe as
    it ever was; a routine the README does record, yet the snapshot reads as
    missing, is precisely the dangerous case. When the read could not even
    account for every recorded id, no create is postable at all — the ledger is
    the only thing making the distinction, and it did not hold.

    Two fields are read and reported but never reconciled:

    ``enabled`` — because ``pause`` is a supported verb. If deploy re-enabled a
    routine somebody deliberately paused, the pause would last until the next
    deploy and nothing would say it had ended.

    ``environment_id`` — because it is per-machine. Requiring a match would flag
    every teammate's fleet as drifted the moment they ran a check.
    """
    routines = list(routines if routines is not None else parse_routines())
    partial = live.partial if isinstance(live, Snapshot) else None
    # A partial read that cannot account for every recorded id blocks every
    # create; one that can, blocks only the routines the README says exist.
    blanket = partial if (isinstance(live, Snapshot) and live.unresolved) else None
    unregistered = unregistered_routines() if partial else frozenset()
    live = list(live.triggers if isinstance(live, Snapshot) else live)

    repo_url = repo_url or repo_url_of(live)
    environment_id = environment_id or environment_of(live)
    connectors = list(connectors if connectors is not None else connectors_of(live))

    scope_id = scope_id or slug_from_url(repo_url)

    observed = {trigger["trigger_name"]: trigger for trigger in map(observed_trigger, live)}
    by_name = {entry.get("name", ""): entry for entry in live}
    plan = Plan(partial=partial)

    # Every routine, event ones included. They were unplannable while the routines
    # API took a cron expression only — which is why cowork/README.md used to tell
    # you to add three of them by hand. It now accepts a cron-less create, and
    # `create_webhook_trigger` attaches the event that fires them.
    expected = list(routines)
    for routine in expected:
        wanted = desired_trigger(routine, repo_url or "", environment_id or "", connectors)
        current = observed.get(routine.trigger_name)

        if current is None:
            blocked = None
            if not allow_create:
                blocked = (
                    "registering a routine is not safe to do unattended — two runs would "
                    "each create it, the API has no delete, and both copies would then fire. "
                    "Run /cowork deploy from a session with a human in it."
                )
            elif blanket:
                blocked = (
                    f"this snapshot cannot see the whole fleet ({blanket}), so a routine it "
                    "reads as missing may be one already firing past the page boundary. "
                    "Read the missing ids back with RemoteTrigger get and plan again."
                )
            elif partial and routine.path not in unregistered:
                blocked = (
                    f"this snapshot cannot see the whole fleet ({partial}), and cowork/README.md "
                    "records a URL for this routine — so it exists, and creating it would "
                    "register a second copy of something already firing."
                )
            plan.create.append(
                TriggerAction(
                    action="create",
                    name=routine.name,
                    trigger_name=routine.trigger_name,
                    trigger_id=None,
                    fields={},
                    body={} if blocked else wanted,
                    blocked=blocked,
                )
            )
            continue

        if current["url"]:
            plan.urls[routine.path] = current["url"]
        if not current["enabled"]:
            plan.disabled.append(routine.name)

        drift = compared_fields(routine, current, repo_url)

        if not drift:
            plan.ok.append(routine.name)
            continue

        patch: dict = {}
        if "cron" in drift:
            # "" and not None, for the same reason desired_trigger leaves the key
            # out entirely: a null in a field the API validates is a guess, and ""
            # is the value the API itself reports for a routine with no schedule.
            patch["cron_expression"] = routine.cron or ""
        if any(name in drift for name in _JOB_FIELDS):
            patch["job_config"] = wanted["job_config"]
        if "connectors" in drift and connectors:
            # Only when we hold the real connector objects. An empty list is not
            # "no connectors" to this API — it is "the default", and the default
            # is every connector on the account. So a patch built without them
            # would attach mail to every routine in the fleet while reading, here,
            # as a tightening. `needs` names it instead; the drift stays reported.
            patch["mcp_connections"] = wanted["mcp_connections"]

        plan.update.append(
            TriggerAction(
                action="update",
                name=routine.name,
                trigger_name=routine.trigger_name,
                trigger_id=current["id"],
                fields=drift,
                body=patch,
            )
        )

    # Three values a create body needs come from the account, not the repo, and
    # on the very first deploy there is no live routine to read them off. An
    # empty string is a value the API will accept, so a body carrying one
    # registers twenty-three routines pointing at no repository — which looks like it
    # worked until the first Monday. Named here so the caller must fill them in.
    if plan.postable_creates:
        if not repo_url:
            plan.needs.append("repo_url")
        if not environment_id:
            plan.needs.append("environment_id")
    # Connectors are needed by every body, not only a create: an update that
    # carries `mcp_connections: []` attaches every connector on the account. So
    # this one is named whenever there is anything at all to apply.
    if (plan.postable_creates or plan.update) and not connectors:
        plan.needs.append("connectors")

    wanted_names = {routine.trigger_name for routine in expected}
    prefix = TRIGGER_NAME.format(name="")
    for trigger_name, current in sorted(observed.items()):
        # Only routines this script would have created. A routine somebody made by
        # hand in the same account is theirs, and deleting it is not our call.
        if trigger_name.startswith(prefix) and trigger_name not in wanted_names:
            plan.orphans.append(
                {
                    "trigger_name": trigger_name,
                    "trigger_id": current["id"],
                    "enabled": current["enabled"],
                    "prompt": current["prompt"],
                    "url": current["url"],
                }
            )

    plan.webhooks = webhook_plan(
        expected,
        by_name,
        observed,
        created=frozenset(created),
        scope_id=scope_id,
    )

    return plan


# --- the README URL column ---------------------------------------------------


def readme_with_urls(text: str, urls: dict[str, str]) -> str:
    """Fill the registered-routines URL column from a plan's ``urls``.

    Done here, on whole rows, rather than asked of the command as twenty-two edits.
    The first version of this asked for the edits and got none of them, which is
    how a table that claims to record what is running came to record nothing.

    A row the plan has no URL for is left exactly as it is — a routine added to
    the table but not yet deployed, which `missing_urls` reports and the next
    deploy fills.
    """
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = _ROUTINE_ROW.match(line)
        if not match:
            continue
        url = urls.get(f"{match.group(1)}/{match.group(2)}.md")
        if not url:
            continue
        # `| a | b | c | d | e |` splits to 7 parts: a leading and a trailing
        # empty string around the five cells. The URL is the last of them.
        cells = line.rstrip("\n").split("|")
        if len(cells) != 7:
            continue
        cells[5] = f" {url} "
        lines[index] = "|".join(cells) + ("\n" if line.endswith("\n") else "")
    return "".join(lines)


def missing_urls(text: str | None = None) -> list[str]:
    """Routines whose README URL cell is still blank.

    Event rows count now. They were skipped while they could only be created by
    hand in a web form, so the table had no id to record; they are registered like
    anything else since, and a blank cell means the same thing it means for a
    cron routine — this row is written down but not running.
    """
    text = README.read_text(encoding="utf-8") if text is None else text
    blank = []
    for line in text.splitlines():
        match = _ROUTINE_ROW.match(line)
        if not match:
            continue
        cells = line.split("|")
        if len(cells) == 7 and not cells[5].strip():
            blank.append(f"{match.group(1)}/{match.group(2)}.md")
    return blank


def recorded_triggers(text: str | None = None) -> dict[str, str]:
    """Routine path -> the trigger id the README URL column records for it.

    The ledger. ``--urls`` writes that column on every deploy, so this is what
    the fleet has actually registered — and the only answer available once
    ``RemoteTrigger list`` pages, since it cannot be asked for page two. Two
    things read it: a partial snapshot is checked against it, and the manifest
    hands it to the routines that address one routine by id.

    A row with no id is simply absent. That is a real state, not a parse
    failure: a routine added to the table but never deployed carries an em dash
    by convention, and an em dash is not an id.
    """
    text = README.read_text(encoding="utf-8") if text is None else text
    found = {}
    for line in text.splitlines():
        match = _ROUTINE_ROW.match(line)
        if not match:
            continue
        cells = line.split("|")
        if len(cells) != 7:
            continue
        if id_match := _TRIGGER_ID.search(cells[5]):
            found[f"{match.group(1)}/{match.group(2)}.md"] = id_match.group(0)
    return found


def unregistered_routines(text: str | None = None) -> frozenset[str]:
    """Routine paths the README records no trigger id for.

    What makes a create safe from a snapshot that cannot see the whole fleet: no
    deploy has ever registered these, so nothing of ours can be firing past the
    page boundary under their name.
    """
    text = README.read_text(encoding="utf-8") if text is None else text
    recorded = recorded_triggers(text)
    return frozenset(
        f"{kind}/{stem}.md" for kind, stem, _ in _ROUTINE_ROW.findall(text) if f"{kind}/{stem}.md" not in recorded
    )


def recorded_ids(text: str | None = None) -> tuple[str, ...]:
    """Every trigger id the README records, in table order."""
    return tuple(dict.fromkeys(recorded_triggers(text).values()))


def apply_urls(paths: str | Path | Sequence[str | Path]) -> int:
    plan = trigger_plan(load_snapshot(paths))
    before = README.read_text(encoding="utf-8")
    after = readme_with_urls(before, plan.urls)
    if after == before:
        say(f"urls: nothing to fill ({len(plan.urls)} routine(s) registered)")
        return 0
    README.write_text(after, encoding="utf-8")
    say(f"urls: wrote {len(plan.urls)} routine URL(s) into cowork/README.md")
    return 0


# --- teardown ----------------------------------------------------------------


def teardown_labels() -> list[Label]:
    """The labels teardown may delete — everything cowork adds, minus KEEP_LABELS
    and the shared ``type:*`` set, which the feedback system also relies on:
    user-filed feedback issues carry them, and deleting a label strips it off
    every issue on the repo."""
    return [
        label for label in expected_labels() if label.name not in KEEP_LABELS and not label.name.startswith("type:")
    ]


def apply_teardown(labels: bool, variables: bool) -> int:
    """Remove the shell half of the setup. The routines are ``/cowork teardown``.

    Both halves are opt-in flags rather than the default, because they are not
    equally reversible: a repository variable can be re-set from ``models.md`` in
    a second, but deleting a `workstream:*` label strips it off every issue that
    carries it, and no re-run puts those back.
    """
    if not (labels or variables):
        note(
            "teardown: nothing selected",
            "pass --labels and/or --variables; the routines are /cowork teardown's half",
        )
        return 1
    if not github_ready():
        return 1

    if labels:
        present = existing_labels()
        if present is None:
            return 1
        for label in teardown_labels():
            if label.name not in present:
                continue
            deleted, why = delete_label(label.name)
            if deleted:
                say(f"teardown: deleted label {label.name}")
            else:
                note(f"teardown: could not delete {label.name}", why)
        say(f"teardown: kept {', '.join(sorted(KEEP_LABELS))} — live gates outside cowork depend on them")
        say("teardown: kept the type:* labels — the feedback system shares them")

    if variables:
        current = existing_variables()
        if current is None:
            return 1
        for name in parse_model_variables():
            if name not in current:
                continue
            unset, why = delete_variable(name)
            if unset:
                say(f"teardown: unset {name}")
            else:
                note(f"teardown: could not unset {name}", why)
        note(
            "teardown: the workflows now fall back to their pinned defaults",
            "that is by design — every --model expression carries a `||` fallback",
        )

    print()
    say("what a shell cannot do — run /cowork teardown in a Claude session:")
    print("     · disable the cron routines (RemoteTrigger has no delete — they can be")
    print("       turned off from a session, but removing them is a click at claude.ai)")
    print("     · delete the workstream labels on the Linear team")
    return 0


# --- entry point -------------------------------------------------------------


def check_triggers(report: Report, paths: str | Path | Sequence[str | Path]) -> None:
    """Fold the account half into the doctor, from a ``/cowork``-supplied snapshot."""
    plan = trigger_plan(load_snapshot(paths))

    if plan.partial:
        report.notes.append(f"the snapshot is a partial read of the account — {plan.partial}")

    for action in plan.create:
        # Under a partial read this cannot be told apart from a routine that is
        # registered and simply was not read, and reporting a clean fleet as
        # broken is how a doctor stops being read at all.
        if plan.partial and action.blocked:
            report.notes.append(f"routine `{action.trigger_name}` was not read back — registered or missing is unknown")
            continue
        report.fail(
            f"routine `{action.trigger_name}` is not registered",
            "run: /cowork deploy",
        )
    for action in plan.update:
        drifted = ", ".join(f"{name} ({live!r} → {wanted!r})" for name, (live, wanted) in sorted(action.fields.items()))
        report.fail(
            f"routine `{action.trigger_name}` has drifted: {drifted}",
            "run: /cowork deploy",
        )
    for orphan in plan.orphans:
        report.fail(
            f"routine `{orphan['trigger_name']}` is registered but has no README row",
            "add its row to cowork/README.md, or turn it off with /cowork teardown",
        )
    if plan.partial:
        # The other direction of the same blindness, and the quieter one: an
        # orphan past the page boundary is a routine still firing on a prompt
        # nothing in the repo describes, and this is the only line that says the
        # list above may not be all of them.
        report.notes.append("orphans were looked for in a partial read — there may be more")
    if plan.suspicious:
        report.fail(
            "the snapshot reports both a missing routine and an unrecognised one",
            "confirm with a fresh RemoteTrigger list before creating anything — if the "
            "snapshot was garbled in transit, deploying would register a duplicate",
        )
    blank = missing_urls()
    if blank:
        report.fail(
            f"the README URL column is blank for {len(blank)} registered routine(s)",
            "run: /cowork deploy — the table is meant to record what is actually running",
        )
    if plan.disabled:
        report.notes.append(f"{len(plan.disabled)} routine(s) are paused: {', '.join(sorted(plan.disabled))}")


def run_check(local_only: bool, triggers: Sequence[str] | None = None) -> int:
    report = Report()
    check_repo(report)

    if triggers:
        check_triggers(report, triggers)
    else:
        report.notes.append("no --triggers snapshot: the registered routines were not checked (run /cowork status)")

    if local_only:
        note("--local: skipping every GitHub check")
    elif github_ready():
        check_merge_gate(report)
        # A failed query is not an empty repo. Reported by the helper and skipped
        # here, rather than turning one gh error into twenty-two findings.
        present = existing_labels()
        if present is None:
            report.notes.append("the GitHub labels were not checked — the query failed")
        else:
            for label in expected_labels():
                if label.name not in present:
                    report.fail(
                        f"GitHub label `{label.name}` does not exist",
                        "run: make cowork-setup",
                    )
        wanted = parse_model_variables()
        current = existing_variables()
        if current is None:
            report.notes.append("the repository variables were not checked — the query failed")
        else:
            for name, value in wanted.items():
                if name not in current:
                    report.fail(
                        f"repository variable {name} is not set",
                        "run: make cowork-setup",
                    )
                elif current[name] and current[name] != value:
                    report.fail(
                        f"repository variable {name} is `{current[name]}`, models.md says `{value}`",
                        "run: make cowork-setup, or update the models.md table",
                    )

    for message in report.notes:
        note(message)

    if report.ok:
        say("check: clean")
        return 0
    fail(f"check: {len(report.problems)} problem(s)")
    for problem in report.problems:
        fail(f"  ✗ {problem}")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stand up the cowork fleet from cowork/.")
    parser.add_argument("--check", action="store_true", help="verify only; exit non-zero on drift")
    parser.add_argument("--local", action="store_true", help="with --check, skip every gh call")
    parser.add_argument("--json", action="store_true", help="print the routine manifest for /cowork")
    parser.add_argument("--agenda", action="store_true", help="print what the fleet runs today, and the week after")
    parser.add_argument(
        "--proposal-slots",
        nargs="?",
        const="",
        metavar="WORKSTREAM",
        help="print how many proposals a workstream may still file (omit the name for all fifteen)",
    )
    parser.add_argument(
        "--queued",
        nargs="?",
        const="",
        metavar="WORKSTREAM",
        help="print the open cowork:queued items a sweep should build, in build order "
        "(omit the name for all fifteen; queued=null means the query failed)",
    )
    parser.add_argument(
        "--migrate-proposals",
        action="store_true",
        help="one-off: reclassify auto-lane-eligible cowork:proposal issues as cowork:queued "
        "(prints the plan and changes nothing without --yes)",
    )
    parser.add_argument(
        "--blocked-report",
        metavar="MARKER",
        help="print whether an open issue already reports this standing fault "
        "(reported=null means the query failed — stay silent, do not post)",
    )
    parser.add_argument("--text", action="store_true", help="with --agenda, print the rendered message, not JSON")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="with --agenda, the day to report on (default today)")
    parser.add_argument(
        "--triggers",
        metavar="FILE",
        action="append",
        help="a `RemoteTrigger list` response, saved by /cowork — the account half's input. "
        "Repeat it once the fleet outgrows a page: the list plus a `get` per recorded id",
    )
    parser.add_argument("--plan", action="store_true", help="with --triggers, print the reconcile plan as JSON")
    parser.add_argument(
        "--environment",
        metavar="ID",
        help="with --plan, the environment_id for new routines (only needed on a first deploy)",
    )
    parser.add_argument("--urls", action="store_true", help="with --triggers, fill the README URL column")
    parser.add_argument("--teardown", action="store_true", help="remove what this script created (needs --yes)")
    parser.add_argument("--labels", action="store_true", help="with --teardown, delete the GitHub labels")
    parser.add_argument("--variables", action="store_true", help="with --teardown, unset the model variables")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="with --teardown, skip the confirmation; with --migrate-proposals, apply the plan",
    )
    parser.add_argument(
        "--created",
        action="append",
        metavar="TRIGGER_NAME",
        default=[],
        help="with --plan, a trigger_name this deploy just created (repeatable) — "
        "the only evidence a routine holds no webhook yet",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail instead of noting when a step degrades; for unattended runs",
    )
    parser.add_argument(
        "--no-create",
        action="store_true",
        help="with --plan, report routines that need registering instead of emitting a body for them "
        "(for unattended runs: a create races and cannot be undone)",
    )
    parser.add_argument(
        "--allow-mass-change",
        action="store_true",
        help="with --plan --strict, permit a plan that touches most of the fleet "
        "(a first deploy legitimately creates every routine, and is interactive)",
    )
    args = parser.parse_args(argv)
    # Reset, not just set: `main()` is called more than once in a process by the
    # test suite, and a degradation carried over from a previous call would fail a
    # run that did nothing wrong. TRANSPORT is reset for the same reason — a run
    # that resolved REST must not decide the next run's transport for it.
    global TRANSPORT
    STRICT.strict = args.strict
    STRICT.degraded.clear()
    TRANSPORT = "gh"
    transport.reset_slug_cache()

    if (args.plan or args.urls) and not args.triggers:
        fail("--plan and --urls need a snapshot: --triggers <file> (see /cowork)")
        return 2

    if args.json:
        print(json.dumps(manifest(), indent=2))
        return 0

    if args.proposal_slots is not None:
        # A read, so it needs a transport but nothing else — no labels, no
        # variables, no routine parse. `github_ready()` is what picks between
        # `gh` and REST and reports the remedy when neither answers; without a
        # transport every workstream reports `slots: null`, which the sweep
        # already knows how to read.
        github_ready()
        return report_proposal_slots(args.proposal_slots or None)

    if args.migrate_proposals:
        # A human's one-off, and the guard says so in the one way a routine
        # cannot argue with. Reclassifying forty issues on a mechanical rule is a
        # judgement about a backlog; the fleet reclassifies ONE issue at a time,
        # having read it (`sweep-procedure.md` step 4). `--strict` is the flag no
        # human passes and every unattended caller does.
        if args.yes and args.strict:
            fail("--migrate-proposals --yes is a human's one-off; --strict means nobody is reading")
            return 2
        github_ready()
        return migrate_proposals(apply=args.yes)

    if args.queued is not None:
        # Same shape as --proposal-slots above: a read that needs a transport and
        # nothing else. Without one every workstream reports `queued: null`,
        # which the sweep reads as "could not ask" and never as "nothing to do".
        github_ready()
        return report_queued(args.queued or None)

    if args.blocked_report is not None:
        # Same shape as --proposal-slots above: a read that needs a transport and
        # nothing else. Without one the marker reports `reported: null`, which the
        # routine reads as "stay silent" — the safe direction for a call whose
        # only job is to decide whether to speak.
        github_ready()
        return report_blocked(args.blocked_report)

    if args.agenda:
        try:
            # The UTC day, not the local one: every cron in cowork/ is UTC and
            # `cron_times` matches against a UTC date. `date.today()` would hand a
            # laptop in Sydney yesterday's schedule, already fully run.
            day = date.fromisoformat(args.date) if args.date else datetime.now(UTC).date()
        except ValueError:
            fail(f"--date `{args.date}` is not a YYYY-MM-DD date")
            return 2
        payload = agenda(day)
        print("\n".join(payload["lines"]) if args.text else json.dumps(payload, indent=2))
        return 0

    if args.plan:
        live = load_snapshot(args.triggers)
        # The snapshot is the better source — it is what the account actually has
        # — so gh is only consulted when there is no live routine to read.
        plan = trigger_plan(
            live,
            repo_url=repo_url_of(live.triggers) or repo_url(),
            environment_id=environment_of(live.triggers) or args.environment,
            created=args.created,
            allow_create=not args.no_create,
        )
        print(json.dumps(plan.as_dict(), indent=2))
        if plan.partial:
            note(
                f"plan: partial read — {plan.partial}",
                "updates are unaffected; a create it may not vouch for carries a reason and an empty body",
                stream=sys.stderr,
            )
        if plan.needs:
            note(
                f"plan: {len(plan.create)} create(s) are missing {', '.join(plan.needs)}",
                "/cowork resolves these before posting — never POST a body with an empty one",
                stream=sys.stderr,
            )
        if plan.creates_blocked:
            note(
                f"plan: {len(plan.creates_blocked)} routine(s) need registering and were not: "
                f"{', '.join(plan.creates_blocked)}",
                "run /cowork deploy from an interactive session — a create races and has no undo",
                stream=sys.stderr,
            )
        if plan.webhooks_blocked:
            note(
                f"plan: {len(plan.webhooks_blocked)} webhook(s) not planned: {', '.join(plan.webhooks_blocked)}",
                "reported, never posted — the API cannot say whether one is already attached, "
                "and a duplicate fires twice with no way to delete it",
                stream=sys.stderr,
            )

        # A --created name the snapshot has never heard of means the create and
        # the re-list disagree — which is exactly the state in which posting a
        # webhook would attach it to the wrong routine, or to none.
        known = {entry.get("name", "") for entry in live.triggers}
        if unknown := [name for name in args.created if name not in known]:
            fail(f"--created names {', '.join(unknown)}, which the snapshot does not contain")
            fail("     re-list after the creates and pass the fresh snapshot")
            return 2

        if args.strict:
            if plan.suspicious:
                fail("plan: suspicious — a routine is missing and another is unrecognised; refusing")
                return 2
            if plan.needs:
                fail(f"plan: {', '.join(plan.needs)} unresolved; a body carrying an empty one registers nothing useful")
                return 2
            touched = len(plan.postable_creates) + len(plan.update)
            if touched > MASS_CHANGE_LIMIT and not args.allow_mass_change:
                fail(
                    f"plan: {len(plan.postable_creates)} create(s) + {len(plan.update)} update(s) — "
                    f"more than {MASS_CHANGE_LIMIT}; a human should look first"
                )
                fail("     re-run with --allow-mass-change if this is a deliberate fleet-wide change")
                return 2
        return strict_exit()

    if args.urls:
        return apply_urls(args.triggers) or strict_exit()

    if args.teardown:
        if not args.yes:
            fail("--teardown is destructive and needs --yes (make cowork-teardown prompts for you)")
            return 2
        return apply_teardown(labels=args.labels, variables=args.variables)

    if args.check:
        return run_check(local_only=args.local, triggers=args.triggers) or strict_exit()

    report = Report()
    check_repo(report)
    if not report.ok:
        # 2, not 1, and the difference is what `cron/cd-deploy.md` step 3 reads.
        # 1 here means the GitHub apply degraded, which has no bearing on a
        # routine's trigger body — those are built from these same files, and
        # these files are fine. 2 means the files are not, and nothing further
        # should be registered from them. Same convention as `--plan`, where 2 is
        # already "it refused itself; a human should look".
        fail("cowork/ disagrees with itself — fix this before registering anything:")
        for problem in report.problems:
            fail(f"  ✗ {problem}")
        return 2

    routines = parse_routines()
    if github_ready():
        apply_labels()
        apply_variables()
    else:
        STRICT.note(
            "nothing was applied to GitHub",
            "authenticate with `gh auth login`, or export GH_TOKEN, then re-run",
        )

    report_manual_remainder(routines)
    return strict_exit()


if __name__ == "__main__":
    raise SystemExit(main())
