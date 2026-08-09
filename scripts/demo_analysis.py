#!/usr/bin/env python3
"""Walk the REAL Analysis flow with dummy data — no credentials, no API calls.

`preview_analysis.py` renders screens one at a time; this launches the actual
TUI so you navigate the real thing — animations, transitions, the music pocket,
Esc handling, the lot. Every outbound seam is stubbed:

  * the board looks configured (Jira/ACME), so the gate doesn't stop you
  * the roster returns a fixed cast, so member select has names
  * run_team_analysis returns a fixture profile after a short fake fetch — and
    saves it, as the real one does — so the progress screen animates, the
    results screens have real content, and the next visit to Analysis opens on
    the dashboard rather than the setup wizard

    uv run python scripts/demo_analysis.py           # the whole app, Analysis wired up
    uv run python scripts/demo_analysis.py --fast    # skip the fake fetch delay
    uv run python scripts/demo_analysis.py --seeded  # open straight on the dashboard

Nothing is written to your real ~/.yeaboi: the session DB is redirected to a
temp file, so a demo run can't pollute saved profiles.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from preview_analysis import _SPRINT_NAMES, _profile  # noqa: E402

_CAST = ["Ada Lovelace", "Grace Hopper", "Alan Turing", "Katherine Johnson", "Barbara Liskov"]

# Coaching insights, saved with the fixture profile. The real ones come from an
# LLM at the end of a run; shipping them here keeps the demo's Insights page
# populated without _ensure_insights reaching for a model that isn't there.
_INSIGHTS = {
    "start": [
        {
            "title": "Split stories over eight points",
            "detail": "Large stories are the ones spilling. Break them at the seam you already "
            "find mid-sprint, during refinement instead.",
            "evidence": "4 of 5 spilled stories were 8+ points",
        },
        {
            "title": "Name an owner at planning",
            "detail": "Unowned tickets sat three days longer before anyone started them.",
            "evidence": "3.1 day median pickup on unowned work",
        },
    ],
    "stop": [
        {
            "title": "Adding scope after day two",
            "detail": "Mid-sprint additions displaced committed work rather than joining it.",
            "evidence": "9 tickets added after sprint start",
        },
    ],
    "keep": [
        {
            "title": "Writing acceptance criteria up front",
            "detail": "Stories that arrived with criteria finished inside the sprint far more often.",
            "evidence": "92% completion where criteria existed",
        },
    ],
    "try": [
        {
            "title": "A one-week sprint for a cycle",
            "detail": "Velocity swings by half between sprints — a shorter loop makes the swing "
            "cheaper to read and to correct.",
            "evidence": "velocity 18→34 across 4 sprints",
        },
    ],
}


def _demo_db():
    """The demo's DB path — YEABOI_HOME is already redirected to a temp dir."""
    from yeaboi.paths import get_db_path

    return get_db_path()


# What the fake fetch narrates, so the progress screen has something to animate.
_STEPS = [
    "Connecting to Jira · ACME…",
    "Reading sprints 101–104…",
    "Fetching 64 stories…",
    "Measuring velocity and spillover…",
    "Reading story shapes and estimates…",
    "Scanning definition-of-done signals…",
    "Composing the summary…",
]


def _install_stubs(*, fast: bool) -> None:
    """Point every outbound call at a fixture."""
    import yeaboi.analysis as analysis
    import yeaboi.analysis.engine as engine
    import yeaboi.azdevops_sync as azdevops_sync
    import yeaboi.jira_sync as jira_sync
    from yeaboi.team_roster import RosterMember, RosterResult, RosterSourceResult

    # ── the board gate ──────────────────────────────────────────────────────
    jira_sync.is_jira_configured = lambda: True
    azdevops_sync.is_azdevops_board_configured = lambda: False

    # ── which sub-sources the component grid offers ─────────────────────────
    engine._available_sources = lambda: ["jira"]
    engine._available_code_sources = lambda: ["github"]
    engine._available_doc_sources = lambda: ["confluence"]
    engine._detect_source = lambda: "jira"

    # ── the roster (member select) ──────────────────────────────────────────
    members = tuple(RosterMember(name=n, source="jira", identity=n.lower().replace(" ", ".")) for n in _CAST)
    roster = RosterResult(
        members=members,
        status="ok",
        sources=(RosterSourceResult(provider="jira", project="ACME", status="ok", members=members),),
    )
    engine.get_team_roster_result = lambda *a, **k: roster
    engine.get_team_roster = lambda *a, **k: list(_CAST)
    analysis.get_team_roster_result = engine.get_team_roster_result
    analysis.get_team_roster = engine.get_team_roster

    # ── the run itself ──────────────────────────────────────────────────────
    def _fake_run(*_a, progress=None, cancel_event=None, **_k):
        """Narrate the fixture into `progress`, honouring Esc-to-cancel."""
        for step in _STEPS:
            if cancel_event is not None and cancel_event.is_set():
                from yeaboi.analysis.engine import AnalysisCancelledError

                raise AnalysisCancelledError
            if progress is not None:
                progress.append(step)
            if not fast:
                time.sleep(0.45)
        profile = _profile()
        examples = {"insights": _INSIGHTS}
        # Save it, like the real run does inside _persist_delivery. Without this
        # the demo never has a stored analysis, so Analysis opened on the setup
        # wizard forever and the dashboard — the thing the mode opens on once
        # you have one — was unreachable in the demo.
        try:
            from yeaboi.team_profile import TeamProfileStore

            with TeamProfileStore(_k.get("db_path") or _demo_db()) as store:
                store.save(profile, examples=examples)
        except Exception as exc:  # noqa: BLE001 - a demo must still run unsaved
            print(f"demo: profile not saved ({exc})", file=sys.stderr)
        return {
            "delivery": {
                "jira": {
                    "profile": profile,
                    "examples": examples,
                    "sprint_names": list(_SPRINT_NAMES),
                    "source": "jira",
                    "project_key": "ACME",
                    "team_name": "Platform Team",
                }
            },
            "code": {},
            "docs": {},
            "comparison": [],
            "warnings": [],
        }

    engine.run_team_analysis = _fake_run
    analysis.run_team_analysis = _fake_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fast", action="store_true", help="skip the fake fetch delay")
    parser.add_argument(
        "--seeded",
        action="store_true",
        help="start with the fixture analysis already saved, so Analysis opens on the dashboard",
    )
    args = parser.parse_args()

    # Redirect the whole data tree so a demo can't touch real saved profiles.
    tmp = tempfile.mkdtemp(prefix="yeaboi-demo-")
    import os

    os.environ["YEABOI_HOME"] = tmp
    os.environ.setdefault("JIRA_BASE_URL", "https://acme.atlassian.net")
    os.environ.setdefault("JIRA_EMAIL", "dev@acme.com")
    os.environ.setdefault("JIRA_API_TOKEN", "demo-token")
    os.environ.setdefault("JIRA_PROJECT_KEY", "ACME")

    _install_stubs(fast=args.fast)

    # A fresh temp home means every launch is a first-time user, which only ever
    # shows the setup wizard. Seed one so the OTHER branch — the dashboard — is
    # reachable without walking the wizard first.
    if args.seeded:
        from yeaboi.team_profile import TeamProfileStore

        with TeamProfileStore(_demo_db()) as store:
            store.save(_profile(), examples={"insights": _INSIGHTS})

    import atexit

    from rich.console import Console

    from yeaboi.ui.mode_select import select_mode
    from yeaboi.ui.shared._input import (
        disable_mouse_tracking,
        enable_mouse_tracking,
        enter_raw_mode,
        exit_raw_mode,
    )

    # cli.py does this around select_mode, not select_mode itself — so calling
    # the TUI directly (as this script does) left mouse reporting off and EVERY
    # click dead, which reads as "the buttons don't work" rather than "the demo
    # harness never turned the mouse on". Mirror the CLI exactly.
    def _cleanup() -> None:
        for fn in (disable_mouse_tracking, exit_raw_mode):
            try:
                fn()
            except Exception:  # noqa: BLE001 - best-effort terminal restore
                pass

    atexit.register(_cleanup)
    print(f"demo data dir: {tmp}\nPick Analysis. Ctrl+C to quit.")
    time.sleep(1.2)
    enter_raw_mode()
    enable_mouse_tracking()
    try:
        select_mode(Console())
    except KeyboardInterrupt:
        pass
    finally:
        _cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
