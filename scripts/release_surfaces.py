#!/usr/bin/env python3
"""Which surfaces a batch of commits touched, and what only a human can check.

The beta channel can prove a lot without anyone looking: `make test`, `make lint`,
`make parity` and `make web-check` all run before an rc reaches PyPI. What it
cannot prove is the set of things this repository has repeatedly broken *below*
the test suite — a CSP that only fails for the remote teammate, a launchd plist
whose shell quoting only fails at fire time, a Go sidecar that silently reverts to
Python with CI fully green. Those are the items below.

So this is not a generic QA checklist. Every row exists because the repository
already documents that failure as invisible to automation, and every row carries
the *why* — a checklist item without one gets skipped the second week.

The table is here rather than in ``release_channel.py`` for two reasons. It is
product knowledge, not release arithmetic, and it changes on a different clock.
And keeping it separate is what lets both ``release_channel.py`` (which renders
the checklist into the promotion ask) and ``beta_signoff.py`` (which prints it in
a terminal) read it without importing each other.

Stdlib only, deliberately — its callers run before ``uv sync``.

    python3 scripts/release_surfaces.py src/yeaboi/cli.py frontend/src/app.tsx
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from dataclasses import dataclass, replace

# The two hand-test sessions a week is split into. They are a partition of the
# CHECKLIST, not of the commits: what a human has to exercise depends on which
# surfaces moved, not on who wrote them or why. A `tools/jira.py` fix and a Jira
# campaign angle need the same person to drive Jira through the modes.
TRACKS: tuple[str, ...] = ("maintenance", "integration")


@dataclass(frozen=True)
class Item:
    """One thing to exercise by hand, and why automation cannot."""

    label: str
    what: str
    why: str
    # Defaulted so every BASELINE and SURFACES row below is unchanged by the split.
    track: str = "maintenance"
    # False means "this angle was part of the campaign and this batch did not
    # reach it". It still renders — an angle that silently vanishes from the
    # checklist is indistinguishable from one that was never needed, which is the
    # failure this module exists to prevent.
    reached: bool = True


# Checked on every sign-off, whatever changed. `installable` is interpolated by the
# caller — this module never guesses a version.
BASELINE: tuple[Item, ...] = (
    Item(
        label="install",
        what="install the exact rc into a clean environment "
        "(`uv tool install --pre yeaboi==<rc>`), then `yeaboi --version`",
        why="the wheel is the artefact users get; the repo checkout proves nothing about it",
    ),
    Item(
        label="bootstrap",
        what="in a container with no uv and a Python below the floor "
        "(`docker run --rm -it python:3.9-slim`), run "
        "`curl -fsSL https://yeaboi.ai/install.sh | sh`, then `yeaboi --version`",
        why="the installer is served straight off main by GitHub Pages — no build, no deploy job — "
        "so a broken script is invisible to every test, and it is the first thing a new user runs",
    ),
    Item(
        label="boot",
        what="`yeaboi --dry-run` — splash, landing split, a mode card, then `q`",
        why="`q` must restore the terminal: raw mode, alt-screen and mouse tracking are only exercised by a real tty",
    ),
)


# Ordered most-specific first: `_match` stops at the first row whose globs hit, per
# row, so a broad pattern below a narrow one cannot swallow it.
SURFACES: tuple[tuple[tuple[str, ...], Item], ...] = (
    (
        ("frontend/**", "src/yeaboi/web/**"),
        Item(
            label="browser",
            what="open Retro or Poker from the TUI and load the tunnel URL **from a device off "
            "your LAN** (phone on cellular); then open a written export from "
            "`~/.yeaboi/exports/**` over `file://`",
            why="CSP breakage is invisible on localhost and on a LAN, and shows up only for the "
            "remote teammate; exports run with `connect-src 'none'`",
        ),
    ),
    (
        # Both paths: the installer moved to ceremonies/ and standup/scheduler.py
        # is now a re-export shim. Keying on the shim alone would have quietly
        # stopped asking for this hand-test the moment a change touched the real
        # installer and not the shim — which is every change from here on.
        ("src/yeaboi/standup/scheduler.py", "src/yeaboi/ceremonies/scheduler.py"),
        Item(
            label="schedule",
            what="`yeaboi standup --schedule install`, set a fire two minutes out, confirm a "
            "Terminal window actually opens, then `--schedule remove`",
            why="run.sh lives under a path containing a space; the missing shell quoting is "
            "exactly what used to break every scheduled fire",
        ),
    ),
    (
        (
            "go/**",
            "src/yeaboi/agentwatch/**",
            "src/yeaboi/analysis/**",
            "src/yeaboi/standup/aggregate.py",
            "src/yeaboi/standup/references.py",
            "src/yeaboi/standup/relatedness.py",
            "src/yeaboi/standup/habits.py",
            "src/yeaboi/standup/automation.py",
            "src/yeaboi/standup/insights.py",
            "src/yeaboi/standup/confidence.py",
            "src/yeaboi/standup/categories.py",
            "src/yeaboi/sessions.py",
        ),
        Item(
            label="sidecar",
            what="run the Agents pages once with the Go sidecar installed and once with "
            "`YEABOI_GO=0`; the numbers must be identical",
            why="a schema-version drift makes the sidecar refuse every upgraded database and "
            "silently revert to Python, with CI fully green",
        ),
    ),
    (
        ("src/yeaboi/update_check.py", "src/yeaboi/cli.py"),
        Item(
            label="upgrade",
            what="from a genuinely older installed version, press Ctrl+U and let it relaunch",
            why="nothing automated covers the `os.execv` restart — it can only run against a "
            "real older release on PyPI",
        ),
    ),
    (
        ("src/yeaboi/sessions.py", "src/yeaboi/persistence.py"),
        Item(
            label="migration",
            what="resume a session created by the **previous** release (`yeaboi --resume latest`)",
            why="migrations only run against a database that already exists; a fresh install never executes them",
        ),
    ),
    (
        ("src/yeaboi/retro/tunnel.py", "src/yeaboi/sharing/**"),
        Item(
            label="tunnel",
            what="bring a board up and confirm the public URL resolves; check the gate page names only the mode word",
            why="error 1033, blocked QUIC and filtering DNS are all environment-specific and unreachable from CI",
        ),
    ),
    (
        ("src/yeaboi/voice/**",),
        Item(
            label="voice",
            what="double-tap Space in a text field and follow the dictation install",
            why="platform-gated and hardware-dependent; the refusal path matters as much as the happy one",
        ),
    ),
    (
        ("src/yeaboi/ui/**",),
        Item(
            label="tui",
            what="walk the affected mode card: open it, `esc` back, `q` quits, and resize the terminal mid-screen",
            why="viewport maths and layout fallbacks are render-tested but not *watched*",
        ),
    ),
    (
        ("src/yeaboi/tools/**", "src/yeaboi/*_sync.py", "src/yeaboi/config.py"),
        Item(
            label="integrations",
            what="run the affected integration with real credentials — or with none, and "
            "confirm it degrades to a named Notice rather than a traceback",
            why="every integration is best-effort by design, and 'skipped silently' looks "
            "identical to 'worked' in a test",
        ),
    ),
    (
        ("src/yeaboi/cli.py", "src/yeaboi/mcp/**"),
        Item(
            label="cli",
            what="run the changed flag or subcommand end to end, and check `--strict` exits 3 on a degraded run",
            why="exit codes are a contract for schedulers and scripts, and nothing downstream reports a wrong one",
        ),
    ),
    (
        ("pyproject.toml", "src/yeaboi/pricing.py", "uv.lock"),
        Item(
            label="packaging",
            what="install into a clean environment and confirm the optional extras still "
            "resolve; sanity-check a cost estimate",
            why="dependency and rate-table changes are invisible until someone installs the "
            "wheel rather than the checkout",
        ),
    ),
)


# `tools/` modules that are not providers: no credential, no external service, so
# no integration angle hangs off them.
_NOT_PROVIDERS = frozenset({"__init__", "risk", "llm_tools", "codebase", "team_learning", "calendar_tools"})

_PROVIDER_DIR = "src/yeaboi/tools/"


# One row per angle a campaign has to reach, mirroring the reach matrix in
# `cowork/integrations-map.md`. Unlike SURFACES these are NOT gated on paths to
# decide whether they appear: for a provider in the batch, every angle is listed
# and the untouched ones are marked unreached. The patterns say which the batch
# *did* move.
INTEGRATION_ANGLES: tuple[tuple[tuple[str, ...], Item], ...] = (
    (
        ("src/yeaboi/config.py", "src/yeaboi/tools/**"),
        Item(
            label="credential",
            what="set the provider's credentials in `~/.yeaboi/.env`, then unset one and re-run — "
            "the second run must name what is missing, not traceback",
            why="every integration is best-effort by design, and a half-configured provider is the "
            "state a real user is actually in",
            track="integration",
        ),
    ),
    (
        ("src/yeaboi/ui/provider_select/**", "src/yeaboi/setup_wizard.py"),
        Item(
            label="connect",
            what="walk the setup wizard's step for it, once with a good credential and once with a "
            "deliberately wrong one",
            why="the verification probe is the only thing standing between a typo and a provider "
            "that silently returns nothing for a week",
            track="integration",
        ),
    ),
    (
        ("src/yeaboi/ui/mode_select/screens/_screens_secondary.py",),
        Item(
            label="settings",
            what="open Settings → Credentials and confirm the provider has a section, its token is "
            "masked, and an edit round-trips",
            why="the settings page is where a user goes when something stopped working, and a "
            "provider missing from it is a provider they cannot fix",
            track="integration",
        ),
    ),
    (
        (
            "src/yeaboi/standup/collector.py",
            "src/yeaboi/standup/code_scope.py",
            "src/yeaboi/standup/documentation_scope.py",
        ),
        Item(
            label="standup",
            what="run a standup over a window you know has activity in this provider, and find that "
            "activity in the output",
            why="a source that fails silently reads as a zero, and a zero is a number someone will believe",
            track="integration",
        ),
    ),
    (
        ("src/yeaboi/analysis/**", "src/yeaboi/tools/team_learning.py"),
        Item(
            label="analysis",
            what="run a team analysis with the provider connected and check it appears in the "
            "coverage notes, not only in the totals",
            why="`CoverageTracker` is what turns an unreachable source into a reported gap rather "
            "than a silent zero; a new fetch path that skips it looks identical to success",
            track="integration",
        ),
    ),
    (
        ("src/yeaboi/reporting/**",),
        Item(
            label="reporting",
            what="generate a delivery report over a range with activity in this provider",
            why="an export is a file — a dropped source surfaces months later as a blank section "
            "with no server and no log to look at",
            track="integration",
        ),
    ),
    (
        ("src/yeaboi/agent/**", "src/yeaboi/prompts/**"),
        Item(
            label="planning",
            what="run a plan against a project the provider knows about and confirm it reaches the "
            "sizing and sprint context",
            why="planning reads providers through the graph's tool calls, which are chosen at run "
            "time — registration is not evidence the model ever calls it",
            track="integration",
        ),
    ),
    (
        ("src/yeaboi/poker/**",),
        Item(
            label="poker",
            what="estimate one ticket and confirm the points write back to the provider, then "
            "re-finalize and confirm it does not double-write",
            why="write-back is the only place a mode mutates someone's source of record, and "
            "idempotency there is untestable without a real tracker",
            track="integration",
        ),
    ),
    (
        ("src/yeaboi/performance/**",),
        Item(
            label="performance",
            what="run a 1:1 prep for a member with activity in this provider",
            why="performance writes about named people from thin samples; a mis-attributed source "
            "is a wrong statement about a colleague",
            track="integration",
        ),
    ),
    (
        ("src/yeaboi/roadmap/**",),
        Item(
            label="roadmap",
            what="ingest a document from this provider through roadmap intake",
            why="ingest degrades to a fallback rather than failing, so a broken source produces a "
            "plausible empty roadmap instead of an error",
            track="integration",
        ),
    ),
)


def campaign_providers(changed_paths: list[str]) -> tuple[str, ...]:
    """Which providers this batch touched, from `src/yeaboi/tools/<provider>.py`.

    A tuple rather than a single name on purpose. Two providers in one batch is
    unusual but legal — a campaign angle beside a maintenance fix — and returning
    the first would silently drop the second from the checklist, which is the
    "looks covered, wasn't" failure every row in this file exists to prevent.

    This is the *primary* track signal. The corroborating one is the PR title
    prefix `integration(<provider>):`, read in `release_channel.py`; a forgotten
    prefix costs a redundant checklist row and never a wrong release, which is the
    right way round.
    """
    found = set()
    for path in changed_paths:
        path = path.strip()
        if not path.startswith(_PROVIDER_DIR):
            continue
        rest = path[len(_PROVIDER_DIR) :]
        if "/" in rest or not rest.endswith(".py"):
            continue
        stem = rest[:-3]
        if stem not in _NOT_PROVIDERS:
            found.add(stem)
    return tuple(sorted(found))


def integration_checklist(providers: tuple[str, ...], changed_paths: list[str]) -> list[Item]:
    """Every angle, for the providers in this batch, marked reached or not.

    Empty when no provider is in the batch — that is the no-integration-work week,
    and it must produce an empty list rather than a baseline-only one, because
    `beta_signoff.batch_view` reads emptiness as "this track was never asked for".
    """
    if not providers:
        return []
    paths = [path.strip() for path in changed_paths if path.strip()]
    named = ", ".join(providers)
    items = []
    for patterns, item in INTEGRATION_ANGLES:
        reached = any(_match(path, patterns) for path in paths)
        items.append(replace(item, what=f"{named}: {item.what}", reached=reached))
    return items


def tracked_checklists(
    changed_paths: list[str], providers: tuple[str, ...] | None = None
) -> tuple[list[Item], dict[str, list[Item]]]:
    """``(shared_baseline, {track: items})`` — the two sessions, plus what both share.

    BASELINE is lifted out rather than repeated. In both sections it gets done
    twice; in one section only, the other track can be signed off without anyone
    ever installing the wheel.

    ``providers`` overrides what the paths alone can see. `release_channel.pending`
    passes the union of these paths and the `integration(<provider>):` commit
    subjects, because a campaign's reach angle touches only other workstreams'
    files — no `tools/<provider>.py` in the diff at all — and would otherwise land
    in the maintenance session with nobody asked to drive the provider anywhere.
    """
    paths = [path.strip() for path in changed_paths if path.strip()]
    surfaces = [item for patterns, item in SURFACES if any(_match(path, patterns) for path in paths)]
    integration = integration_checklist(campaign_providers(paths) if providers is None else providers, paths)
    if integration:
        # The generic `integrations` row ("run the affected integration, or run it
        # with no credentials and check it degrades") is what the per-angle list
        # says in more detail, provider by provider. Keeping both asks for the same
        # work twice, and a checklist that repeats itself is one nobody finishes.
        surfaces = [item for item in surfaces if item.label != "integrations"]
    return list(BASELINE), {"maintenance": surfaces, "integration": integration}


def _match(path: str, patterns: tuple[str, ...]) -> bool:
    """Whether ``path`` is covered, with ``**`` meaning "this prefix, at any depth"."""
    for pattern in patterns:
        if pattern.endswith("/**"):
            if path == pattern[:-3] or path.startswith(pattern[:-2]):
                return True
        elif fnmatch.fnmatch(path, pattern):
            return True
    return False


def checklist(changed_paths: list[str]) -> list[Item]:
    """The baseline, plus one item per surface the batch actually touched.

    Deduplicated by construction: a row fires once no matter how many of its paths
    changed, because a checklist that repeats itself is one nobody finishes. Order
    follows ``SURFACES``, which is ordered by how expensive the failure is to find
    later rather than by how likely it is.
    """
    paths = [path.strip() for path in changed_paths if path.strip()]
    found = [item for patterns, item in SURFACES if any(_match(path, patterns) for path in paths)]
    return [*BASELINE, *found]


def render(items: list[Item], *, markdown: bool) -> str:
    """The checklist, for a terminal or for the promotion issue body.

    Markdown uses GitHub task-list syntax so the ask issue is tickable from a
    phone; the terminal form is plain `[ ]` for the same reason it is not a table
    — it has to survive being read in an 80-column window.
    """
    lines: list[str] = []
    for item in items:
        if not item.reached:
            # Not a box, because there is nothing to tick — the angle exists and
            # this batch did not reach it. Listed anyway: an angle that vanishes
            # reads as an angle that was not needed.
            if markdown:
                lines.append(f"- **{item.label}** — *not wired in this batch*")
            else:
                lines.append(f"  ··· {item.label} — not wired in this batch")
            continue
        if markdown:
            lines.append(f"- [ ] **{item.label}** — {item.what}")
            lines.append(f"      <sub>{item.why}</sub>")
        else:
            lines.append(f"  [ ] {item.label} — {item.what}")
            lines.append(f"        why: {item.why}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="What a batch of changed paths needs checked by hand")
    parser.add_argument("paths", nargs="*", help="changed paths (default: read from stdin, one per line)")
    parser.add_argument("--markdown", action="store_true", help="render as a GitHub task list")
    parser.add_argument("--tracks", action="store_true", help="split into the two hand-test sessions")
    parser.add_argument("--provider", action="append", default=[], help="force an integration provider (repeatable)")
    args = parser.parse_args(argv)
    paths = args.paths or sys.stdin.read().splitlines()
    if not args.tracks and not args.provider:
        print(render(checklist(paths), markdown=args.markdown))
        return 0
    forced = tuple(sorted(set(args.provider))) or None
    baseline, tracks = tracked_checklists(paths, forced)
    print(render(baseline, markdown=args.markdown))
    for track in TRACKS:
        items = tracks[track]
        if items:
            print(f"\n{track}:")
            print(render(items, markdown=args.markdown))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
