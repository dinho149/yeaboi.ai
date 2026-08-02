#!/usr/bin/env python3
"""Walk the REAL Analysis flow with dummy data — no credentials, no API calls.

`preview_analysis.py` renders screens one at a time; this launches the actual
TUI so you navigate the real thing — animations, transitions, the music pocket,
Esc handling, the lot. Every outbound seam is stubbed:

  * the board looks configured (Jira/ACME), so the gate doesn't stop you
  * the roster returns a fixed cast, so member select has names
  * run_team_analysis returns a fixture profile after a short fake fetch, so the
    progress screen animates and the results screens have real content to draw

    uv run python scripts/demo_analysis.py           # the whole app, Analysis wired up
    uv run python scripts/demo_analysis.py --fast    # skip the fake fetch delay

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
        return {
            "delivery": {
                "jira": {
                    "profile": profile,
                    "examples": {},
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

    from rich.console import Console

    from yeaboi.ui.mode_select import select_mode

    print(f"demo data dir: {tmp}\nPick Analysis. Ctrl+C to quit.")
    time.sleep(1.2)
    try:
        select_mode(Console())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
