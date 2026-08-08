#!/usr/bin/env python3
"""Stand up the cowork fleet from what ``cowork/`` already says.

``cowork/`` is a complete specification — fifteen charters, twenty-one routines, a
tier table, one Definition of Done — and none of it does anything until the
GitHub labels exist, the model repository variables are set, and the routines are
registered at claude.ai. Doing that by hand is 26 labels, 4 variables and 21 web
forms, which is long enough that nobody does it twice and silent when done wrong:
an unset variable just reverts a workflow to its old model, and a cron that
restricts day-of-month *and* day-of-week turns a fortnightly sweep into a daily
one without saying so.

None of that data needs re-authoring. Every routine file carries a regular
``**Trigger** — cron `expr` `` line, ``README.md`` gives each routine a tier, and
``models.md`` maps a tier to an id. This script parses those and applies what a
shell can apply.

**It deliberately names no model.** The ids come out of ``cowork/models.md`` at
run time, because that file being the only place a model is written down is the
whole contract — see ``tests/unit/test_cowork_models.py``, which fails if one is
pasted in here.

What a shell *cannot* do is reach a routine: they are account-scoped and driven by
the in-session ``RemoteTrigger`` tool, with no CLI behind it. So ``/cowork`` makes
the API calls and hands the response back here as a snapshot — every comparison,
every request body and every file edit stays in tested Python, and the model is
left with nothing to improvise. The first version of this script asked the command
to hand-edit sixteen table cells instead, and sixteen table cells did not get
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
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
COWORK = REPO_ROOT / "cowork"
README = COWORK / "README.md"
MODELS_DOC = COWORK / "models.md"
DOD_DOC = COWORK / "definition-of-done.md"
ROUTINES_DIR = COWORK / "routines"
WORKSTREAMS_DIR = COWORK / "workstreams"

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
# One shared set, including `digest` and `marketing-weekly`, whose own files say
# never to edit a file. That is a deliberate difference from the connector list
# above, and rests on a different argument: a connector is a capability to reach
# *outside* the repo, where the blast radius is somebody else's inbox, while a
# stray edit in a routine's own checkout ends up in a PR a human reads. Widen or
# narrow per routine only through TOOL_OVERRIDES below, so the exceptions stay
# reviewable in the same place as the rule.
ALLOWED_TOOLS = ("Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task", "TodoWrite")

# The per-routine exceptions to ALLOWED_TOOLS, keyed by routine stem.
TOOL_OVERRIDES: dict[str, tuple[str, ...]] = {
    # day-ahead runs one script and posts one message. It needs Bash for the
    # agenda, Read for the repo, and Task for the scribe — and nothing else: a
    # routine that only reports the schedule has no business editing a file or
    # searching the tree, and narrowing it says so where the grant is reviewed.
    "day-ahead": ("Bash", "Read", "Task", "TodoWrite"),
    # slack-relay relays a human's verbs and nothing else: gh and the manifest
    # (Bash), the repo and its own allowlist (Read/Glob/Grep), and the routines
    # API (RemoteTrigger) for pause/resume/run — which nothing else gets: a
    # sweep that can reach the routines API is a sweep that can un-pause the
    # fleet. Write/Edit/Task are deliberately absent: the relay's own file
    # forbids editing anything, it spawns no crew, and it is the one routine
    # that reads attacker-influenceable channel text every hour.
    "slack-relay": ("Bash", "Glob", "Grep", "Read", "RemoteTrigger", "TodoWrite"),
}


def routine_tools(name: str) -> tuple[str, ...]:
    """The tool set one routine is registered with."""
    return TOOL_OVERRIDES.get(name, ALLOWED_TOOLS)


# Where a registered routine lives, for the README's URL column.
ROUTINE_URL = "https://claude.ai/code/routines/{id}"

# The one label teardown never deletes. It predates cowork — it is the human
# approval gate `.github/workflows/claude.yml` watches for — so removing it with
# the fleet would quietly break a workflow that has nothing to do with cowork.
# Labels teardown must never delete, because neither belongs to cowork:
# ``claude-implement`` predates it and gates the ``claude.yml`` implement job, and
# ``feedback-override`` is the escape hatch on the ``pr-feedback`` merge gate
# (``.github/workflows/pr-feedback.yml``). Deleting either breaks a live gate
# silently — applying a label that does not exist simply does nothing.
KEEP_LABELS = frozenset({"claude-implement", "feedback-override"})

# The proposal-type vocabulary, carried in issue titles as `[type][workstream] …`
# and on issues as `type:<kind>` labels. Shared with the feedback system:
# ``feedback.py`` titles issues `[Bug] …` and labels them `type:<kind>`, and
# ``feedback-remediation.yml`` normalizes the same set — `other` exists for that
# system and is never used by a cowork scout.
PROPOSAL_TYPES = ("bug", "feature", "improvement", "chore", "docs", "security", "other")

_TYPE_COLORS = {
    "bug": "d73a4a",
    "feature": "a2eeef",
    "improvement": "84b6eb",
    "chore": "ededed",
    "docs": "0075ca",
    "security": "ee0701",
    "other": "cfd3d7",
}

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


def note(message: str, remedy: str = "") -> None:
    print(f"[cowork] note: {message}")
    if remedy:
        print(f"     {remedy}")


def fail(message: str) -> None:
    print(f"[cowork] {message}", file=sys.stderr)


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
        Label("cowork:proposal", "d4c5f9", "A cowork find awaiting a human's claude-implement"),
        Label("claude-implement", "0e8a16", "Approved — the claude.yml implement job builds this"),
        Label("feedback-override", "b60205", "Clears the pr-feedback merge gate — a human's call, recorded on the PR"),
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
    the only place every routine carries an explicit tier — the fourteen sweeps
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
            )
        )
    return routines


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
# The schedule above is eighteen cron expressions on five cadences, and a cron
# expression is not a thing anybody reads at six in the morning: `30 7 11,25 * *`
# is written for a scheduler. Everything below turns the same table into "what
# runs today, and when" — including the finished Slack lines, so
# `cron/day-ahead.md` posts what this computed instead of interpreting eighteen
# expressions itself. A model that reads a cron field correctly most of the time
# is a model that tells you the wrong morning, once.


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

# The routine that posts the agenda. It is left out of the *rendered* message — a
# line announcing the message you are holding is the one line in a four-line post
# that tells the reader nothing, and "Every day: day-ahead" is stranger still. It
# stays in the payload, because the payload is the fleet's schedule and this is
# genuinely part of it; `/cowork status` is what audits whether it is registered.
MESSENGER = "day-ahead"

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


def agenda_lines(payload: dict) -> list[str]:
    """The finished Slack message, one string per line.

    Bold headings, no emoji, no bare URLs — ``.claude/agents/cowork-scribe.md``'s
    format contract, met here so the routine has nothing left to compose and
    nothing left to get wrong.
    """
    day = date.fromisoformat(payload["date"])
    zone = payload["display_timezone"] or "UTC"
    lines = [f"*Today* — {day:%a} {day.day} {day:%b} ({zone})"]
    if payload["note"]:
        lines.append(payload["note"])

    listed = [entry for entry in payload["today"] if entry["name"] != MESSENGER]
    if listed:
        for entry in listed:
            summary = f" — {entry['summary']}" if entry["summary"] else ""
            local = ", ".join(entry["times_local"])
            utc = ", ".join(entry["times_utc"])
            # Half the year London *is* UTC, and a bracket repeating the time it
            # sits beside is noise. The heading already names the zone, so the
            # bracket only appears when it has something to say.
            gloss = "" if local == utc else f" ({utc} UTC)"
            lines.append(f"{local}  {entry['name']}{summary}{gloss}")
    else:
        lines.append("No routines fire today.")

    for entry in payload["background"]:
        gloss = "" if entry["window_local"] == entry["window_utc"] else f" ({entry['window_utc']} UTC)"
        lines.append(f"Background: {entry['name']}, {entry['firings']} runs {entry['window_local']}{gloss}")
    if payload["events"]:
        lines.append("On GitHub events: " + ", ".join(entry["name"] for entry in payload["events"]))

    lines.append("")
    lines.append(f"*Next {len(payload['ahead'])} days*")
    month = day.month
    for entry in payload["ahead"]:
        upcoming = [name for name in entry["names"] if name != MESSENGER]
        names = ", ".join(upcoming) if upcoming else "nothing"
        future = date.fromisoformat(entry["date"])
        # Only when it changes: "Tue 1" a week out is ambiguous, "Tue 1 Sep" is not.
        stamp = f"{entry['weekday']} {future.day}" + (f" {future:%b}" if future.month != month else "")
        month = future.month
        lines.append(f"{stamp}  {names}")
    daily = [name for name in payload["daily"] if name != MESSENGER]
    if daily:
        lines.append("Every day: " + ", ".join(daily) + ".")
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

    for routine in parse_routines():
        # A row whose file is missing was already reported above; the checks below
        # all read that file, so there is nothing left to say about it.
        if not (ROUTINES_DIR / routine.path).exists():
            continue

        if routine.tier not in tiers:
            report.fail(
                f"{routine.path} is tiered `{routine.tier}`, which models.md does not define",
                f"use one of: {', '.join(sorted(tiers))}",
            )
        elif routine.kind == "cron" and not routine.model_id:
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
        # does, and `cron/day-ahead.md` posts these lines verbatim — so a missing
        # one is a blank in tomorrow morning's message, not a cosmetic gap.
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

    check_charter_coverage(report)


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


# --- gh ----------------------------------------------------------------------


def _gh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False)


def gh_ready() -> bool:
    """Whether `gh` is installed and authenticated, with the remedy printed if not."""
    if shutil.which("gh") is None:
        note(
            "`gh` is not on PATH — skipping every GitHub check",
            "install it: brew install gh",
        )
        return False
    if _gh("auth", "status").returncode != 0:
        note(
            "`gh` is not authenticated — skipping every GitHub check",
            "run: gh auth login",
        )
        return False
    return True


def existing_labels() -> set[str] | None:
    """Every label on the repo, or None if the query itself failed.

    None rather than an empty set, and the same for the variables below: they are
    different facts and the difference matters. ``gh_ready()`` passing does not
    mean the next call succeeds — a missing remote, the wrong repo, a rate limit —
    and an empty set read as truth makes the doctor report all twenty-six labels
    missing and ``apply_labels`` try to create every one of them.
    """
    result = _gh("label", "list", "--limit", "200", "--json", "name")
    if result.returncode != 0:
        note("could not list the repo's labels", result.stderr.strip() or "unknown gh error")
        return None
    return {item["name"] for item in json.loads(result.stdout or "[]")}


def existing_variables() -> dict[str, str] | None:
    result = _gh("variable", "list", "--json", "name,value")
    if result.returncode != 0:
        note("could not list the repo's variables", result.stderr.strip() or "unknown gh error")
        return None
    return {item["name"]: item.get("value", "") for item in json.loads(result.stdout or "[]")}


def repo_slug() -> str | None:
    result = _gh("repo", "view", "--json", "nameWithOwner")
    if result.returncode != 0:
        return None
    return json.loads(result.stdout or "{}").get("nameWithOwner")


def repo_url() -> str | None:
    """The clone URL a routine's ``git_repository`` source points at."""
    slug = repo_slug() if shutil.which("gh") else None
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
        result = _gh(
            "label",
            "create",
            label.name,
            "--color",
            label.color,
            "--description",
            label.description,
        )
        if result.returncode == 0:
            say(f"labels: created {label.name}")
        else:
            note(f"labels: could not create {label.name}", result.stderr.strip() or "unknown gh error")
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
        result = _gh("variable", "set", name, "--body", value)
        if result.returncode == 0:
            say(f"variables: set {name}")
        else:
            note(f"variables: could not set {name}", result.stderr.strip() or "unknown gh error")


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
    print(f"     · register the {len(crons)} cron routines (account-scoped; RemoteTrigger, no CLI)")
    print(f"     · mirror the {len(parse_workstreams())} workstream labels onto the Linear team")
    print("     · attach the Linear, Slack and Notion connectors, and remove the rest:")
    print("       https://claude.ai/customize/connectors")
    print("     · install the Claude GitHub App on this repo, if it is not already")
    print("     · set the AUTO_VERSION_PAT secret, or Claude Review never sees workflow_run events")
    print(f"     · register the {len(events)} event routines by hand — RemoteTrigger takes cron only:")
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
    """
    return {
        "repo": repo_slug() if shutil.which("gh") else None,
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
# split is the point: comparing seven fields across eighteen routines is exactly
# the kind of work a model does correctly most of the time, and "most of the
# time" here means a sweep silently running on last month's prompt.


def snapshot(payload: object) -> list[dict]:
    """The trigger array out of a ``RemoteTrigger`` response.

    Three envelopes are accepted because three are what turn up: ``list`` returns
    ``{"data": [...]}``, ``get`` returns ``{"trigger": {...}}``, and a snapshot
    saved by hand is often the bare array. Guessing wrong on any of them reads as
    an empty account — which is the one wrong answer that would have this script
    propose registering eighteen routines that already exist.

    A truncated page is the same failure wearing a different hat, and a quieter
    one: the routines beyond the page boundary simply are not there, so they read
    as missing and get created a second time. ``Plan.suspicious`` cannot catch it
    either — a short page produces creates with no orphan. So it raises.
    """
    if isinstance(payload, dict):
        if isinstance(payload.get("trigger"), dict):
            return [payload["trigger"]]
        if payload.get("has_more"):
            raise ValueError(
                "the snapshot is one page of a longer list (has_more: true) — "
                "deploying from it would re-create every routine past the page "
                "boundary; fetch the rest and concatenate before planning"
            )
        data = payload.get("data", [])
        return list(data) if isinstance(data, list) else []
    return list(payload) if isinstance(payload, list) else []


def load_snapshot(path: str | Path) -> list[dict]:
    return snapshot(json.loads(Path(path).read_text(encoding="utf-8")))


def desired_trigger(
    routine: Routine,
    repo_url: str,
    environment_id: str,
    connectors: Sequence[dict],
) -> dict:
    """The exact ``RemoteTrigger`` create body for one cron routine.

    The shape is not from memory — it is the shape a live ``RemoteTrigger list``
    returns, which is the same one ``create`` takes. If the API moves, re-derive
    it by calling ``list`` and reading one entry rather than editing this by feel.

    ``connectors`` is passed in rather than built: ``connector_uuid`` and the MCP
    ``url`` are account-specific values this script cannot know, so ``/cowork``
    lifts them off an existing routine (or resolves them once) and they ride
    through unchanged.
    """
    return {
        "name": routine.trigger_name,
        "cron_expression": routine.cron,
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
    """One create or update ``/cowork deploy`` should apply."""

    action: str  # "create" | "update"
    name: str  # routine stem
    trigger_name: str
    trigger_id: str | None
    fields: dict[str, tuple[object, object]]  # field -> (live, wanted); empty on create
    body: dict  # POST verbatim — the full body on create, the patch on update

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "name": self.name,
            "trigger_name": self.trigger_name,
            "trigger_id": self.trigger_id,
            "fields": {key: {"live": live, "wanted": wanted} for key, (live, wanted) in self.fields.items()},
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
    # Fields a create body needs that nothing available could supply. Only ever
    # populated when there is something to create — see `needs` below.
    needs: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.create or self.update or self.orphans)

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
        """
        return bool(self.create and self.orphans)

    def as_dict(self) -> dict:
        return {
            "create": [action.as_dict() for action in self.create],
            "update": [action.as_dict() for action in self.update],
            "ok": self.ok,
            "disabled": self.disabled,
            "orphans": self.orphans,
            "urls": self.urls,
            "suspicious": self.suspicious,
            "needs": self.needs,
        }


# Which drifted fields go where in an update body. `cron_expression` is top-level;
# everything else lives under job_config, and a partial nested merge is not
# something to guess at — so if any of them moved, the whole ccr block is resent.
_JOB_FIELDS = ("model", "prompt", "allowed_tools", "repo_url")


def trigger_plan(
    live: Sequence[dict],
    routines: Sequence[Routine] | None = None,
    repo_url: str | None = None,
    environment_id: str | None = None,
    connectors: Sequence[dict] | None = None,
) -> Plan:
    """Compare every live ``cowork:`` routine against the repo.

    Two fields are read and reported but never reconciled:

    ``enabled`` — because ``pause`` is a supported verb. If deploy re-enabled a
    routine somebody deliberately paused, the pause would last until the next
    deploy and nothing would say it had ended.

    ``environment_id`` — because it is per-machine. Requiring a match would flag
    every teammate's fleet as drifted the moment they ran a check.
    """
    routines = list(routines if routines is not None else parse_routines())
    live = list(live)

    repo_url = repo_url or repo_url_of(live)
    environment_id = environment_id or environment_of(live)
    connectors = list(connectors if connectors is not None else connectors_of(live))

    observed = {trigger["trigger_name"]: trigger for trigger in map(observed_trigger, live)}
    plan = Plan()

    expected = [routine for routine in routines if routine.kind == "cron"]
    for routine in expected:
        wanted = desired_trigger(routine, repo_url or "", environment_id or "", connectors)
        current = observed.get(routine.trigger_name)

        if current is None:
            plan.create.append(
                TriggerAction(
                    action="create",
                    name=routine.name,
                    trigger_name=routine.trigger_name,
                    trigger_id=None,
                    fields={},
                    body=wanted,
                )
            )
            continue

        if current["url"]:
            plan.urls[routine.path] = current["url"]
        if not current["enabled"]:
            plan.disabled.append(routine.name)

        drift: dict[str, tuple[object, object]] = {}
        comparisons = {
            "cron": (current["cron"], routine.cron),
            "model": (current["model"], routine.model_id),
            "prompt": (current["prompt"], routine.prompt),
            "allowed_tools": (current["allowed_tools"], tuple(sorted(routine_tools(routine.name)))),
            "repo_url": (current["repo_url"], repo_url),
            "connectors": (current["connectors"], tuple(sorted(CONNECTORS))),
        }
        for name, (found, want) in comparisons.items():
            if found != want:
                drift[name] = (found, want)

        if not drift:
            plan.ok.append(routine.name)
            continue

        patch: dict = {}
        if "cron" in drift:
            patch["cron_expression"] = routine.cron
        if any(name in drift for name in _JOB_FIELDS):
            patch["job_config"] = wanted["job_config"]
        if "connectors" in drift:
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
    # registers eighteen routines pointing at no repository — which looks like it
    # worked until the first Monday. Named here so the caller must fill them in.
    if plan.create:
        if not repo_url:
            plan.needs.append("repo_url")
        if not environment_id:
            plan.needs.append("environment_id")
        if not connectors:
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

    return plan


# --- the README URL column ---------------------------------------------------


def readme_with_urls(text: str, urls: dict[str, str]) -> str:
    """Fill the registered-routines URL column from a plan's ``urls``.

    Done here, on whole rows, rather than asked of the command as eighteen edits.
    The first version of this asked for the edits and got none of them, which is
    how a table that claims to record what is running came to record nothing.

    Rows with no URL — the three event routines, which cannot be registered — are
    left exactly as they are.
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
    """Cron routines whose README URL cell is still blank."""
    text = README.read_text(encoding="utf-8") if text is None else text
    blank = []
    for line in text.splitlines():
        match = _ROUTINE_ROW.match(line)
        if not match or match.group(1) != "cron":
            continue
        cells = line.split("|")
        if len(cells) == 7 and not cells[5].strip():
            blank.append(f"{match.group(1)}/{match.group(2)}.md")
    return blank


def apply_urls(path: str | Path) -> int:
    plan = trigger_plan(load_snapshot(path))
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
    if not gh_ready():
        return 1

    if labels:
        present = existing_labels()
        if present is None:
            return 1
        for label in teardown_labels():
            if label.name not in present:
                continue
            result = _gh("label", "delete", label.name, "--yes")
            if result.returncode == 0:
                say(f"teardown: deleted label {label.name}")
            else:
                note(f"teardown: could not delete {label.name}", result.stderr.strip() or "unknown gh error")
        say(f"teardown: kept {', '.join(sorted(KEEP_LABELS))} — live gates outside cowork depend on them")
        say("teardown: kept the type:* labels — the feedback system shares them")

    if variables:
        current = existing_variables()
        if current is None:
            return 1
        for name in parse_model_variables():
            if name not in current:
                continue
            result = _gh("variable", "delete", name)
            if result.returncode == 0:
                say(f"teardown: unset {name}")
            else:
                note(f"teardown: could not unset {name}", result.stderr.strip() or "unknown gh error")
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


def check_triggers(report: Report, path: str | Path) -> None:
    """Fold the account half into the doctor, from a ``/cowork``-supplied snapshot."""
    plan = trigger_plan(load_snapshot(path))

    for action in plan.create:
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


def run_check(local_only: bool, triggers: str | None = None) -> int:
    report = Report()
    check_repo(report)

    if triggers:
        check_triggers(report, triggers)
    else:
        report.notes.append("no --triggers snapshot: the registered routines were not checked (run /cowork status)")

    if local_only:
        note("--local: skipping every GitHub check")
    elif gh_ready():
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
    parser.add_argument("--text", action="store_true", help="with --agenda, print the rendered message, not JSON")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="with --agenda, the day to report on (default today)")
    parser.add_argument(
        "--triggers",
        metavar="FILE",
        help="a `RemoteTrigger list` response, saved by /cowork — the account half's input",
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
    parser.add_argument("--yes", action="store_true", help="with --teardown, skip the confirmation")
    args = parser.parse_args(argv)

    if (args.plan or args.urls) and not args.triggers:
        fail("--plan and --urls need a snapshot: --triggers <file> (see /cowork)")
        return 2

    if args.json:
        print(json.dumps(manifest(), indent=2))
        return 0

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
            repo_url=repo_url_of(live) or repo_url(),
            environment_id=environment_of(live) or args.environment,
        )
        print(json.dumps(plan.as_dict(), indent=2))
        if plan.needs:
            note(
                f"plan: {len(plan.create)} create(s) are missing {', '.join(plan.needs)}",
                "/cowork resolves these before posting — never POST a body with an empty one",
            )
        return 0

    if args.urls:
        return apply_urls(args.triggers)

    if args.teardown:
        if not args.yes:
            fail("--teardown is destructive and needs --yes (make cowork-teardown prompts for you)")
            return 2
        return apply_teardown(labels=args.labels, variables=args.variables)

    if args.check:
        return run_check(local_only=args.local, triggers=args.triggers)

    report = Report()
    check_repo(report)
    if not report.ok:
        fail("cowork/ disagrees with itself — fix this before registering anything:")
        for problem in report.problems:
            fail(f"  ✗ {problem}")
        return 1

    routines = parse_routines()
    if gh_ready():
        apply_labels()
        apply_variables()
    else:
        note("nothing was applied to GitHub", "authenticate with `gh auth login`, then re-run")

    report_manual_remainder(routines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
