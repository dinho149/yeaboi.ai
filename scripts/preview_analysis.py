#!/usr/bin/env python3
"""Render every Analysis screen with fake data — no credentials, no API calls.

A redesign pass needs to SEE all the screens, and most of them are several
gates deep behind a configured board and a finished run. This walks them
directly off a fixture TeamProfile instead.

    uv run python scripts/preview_analysis.py            # arrow through them
    uv run python scripts/preview_analysis.py --list     # just the names
    uv run python scripts/preview_analysis.py board      # one screen, printed
    uv run python scripts/preview_analysis.py --all      # every screen, printed

Interactive mode redraws on ←/→ (or j/k), so you can edit a builder in one
window and press a key in this one to see it. Nothing here imports from the
mode_select event loop, so a broken loop doesn't stop you previewing screens.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rich.console import Console  # noqa: E402

from yeaboi.ui.mode_select.screens._screens_secondary import (  # noqa: E402
    _build_analysis_board_setup_screen,
    _build_analysis_depth_screen,
    _build_analysis_feature_screen,
    _build_analysis_model_offer_screen,
    _build_analysis_progress_screen,
    _build_analysis_window_screen,
    _build_team_analysis_screen,
)

_JIRA_FULL = {
    "JIRA_BASE_URL": "https://acme.atlassian.net",
    "JIRA_EMAIL": "dev@acme.com",
    "JIRA_API_TOKEN": "tok_abcdefghijkl",
    "JIRA_PROJECT_KEY": "ACME",
}

_SPRINT_NAMES = ["Sprint 101", "Sprint 102", "Sprint 103", "Sprint 104"]

# Every feature runnable, so the picker shows its full set rather than dimming most.
_FEATURES_AVAILABLE = {"delivery": True, "ai_footprint": True, "code_health": True, "documentation": True}


def _profile():
    """A fully-populated TeamProfile — every result section has something to draw."""
    from yeaboi.team_profile import (
        DoDSignal,
        EpicPattern,
        SpilloverStats,
        StoryPointCalibration,
        StoryShapePattern,
        TeamProfile,
        WritingPatterns,
    )

    return TeamProfile(
        team_id="jira-ACME",
        source="jira",
        project_key="ACME",
        team_name="Platform Team",
        sample_sprints=8,
        sample_stories=64,
        velocity_avg=23.5,
        velocity_stddev=3.2,
        point_calibrations=(
            StoryPointCalibration(point_value=1, avg_cycle_time_days=0.5, sample_count=10),
            StoryPointCalibration(point_value=3, avg_cycle_time_days=2.1, sample_count=20, overshoot_pct=15.0),
            StoryPointCalibration(point_value=5, avg_cycle_time_days=4.2, sample_count=15, overshoot_pct=20.0),
            StoryPointCalibration(point_value=8, avg_cycle_time_days=7.8, sample_count=6, overshoot_pct=34.0),
        ),
        story_shapes=(
            StoryShapePattern(
                discipline="backend", avg_points=3.2, avg_ac_count=3.0, avg_task_count=2.8, sample_count=20
            ),
            StoryShapePattern(
                discipline="frontend", avg_points=2.5, avg_ac_count=2.5, avg_task_count=2.0, sample_count=12
            ),
            StoryShapePattern(discipline="qa", avg_points=1.8, avg_ac_count=2.0, avg_task_count=1.4, sample_count=7),
        ),
        epic_pattern=EpicPattern(avg_stories_per_epic=6.0, avg_points_per_epic=18.0, typical_story_count_range=(4, 9)),
        estimation_accuracy_pct=78.0,
        sprint_completion_rate=88.0,
        # Just over the 15% line that flags a spillover recommendation: the
        # Recommendations section is DERIVED, so a preview team with nothing
        # wrong with it renders an empty page for the one section whose whole
        # job is telling you what is wrong.
        spillover=SpilloverStats(
            carried_over_pct=17.5,
            avg_spillover_pts=3.2,
            most_common_spillover_reason="backend stories",
        ),
        dod_signal=DoDSignal(
            common_checklist_items=("tests passing", "PR merged", "code reviewed"),
            stories_with_comments_pct=85.0,
            stories_with_pr_link_pct=82.0,
            stories_with_review_mention_pct=76.0,
            stories_with_testing_mention_pct=61.0,
            stories_with_deploy_mention_pct=44.0,
        ),
        writing_patterns=WritingPatterns(
            median_ac_count=3.0,
            median_task_count_per_story=2.5,
            subtask_label_distribution=(("Code", 0.58), ("Testing", 0.28)),
            common_subtask_patterns=("Write unit tests", "Deploy to staging"),
            subtasks_use_consistent_naming=True,
            common_personas=("developer", "admin"),
            uses_given_when_then=True,
            stories_with_subtasks_pct=72.0,
        ),
        sprints_fully_completed=6,
        sprints_partially_completed=2,
        sprints_analysed=8,
    )


# The other half of a result. ``_profile()`` holds what the engine COMPUTES;
# everything below is what it WRITES AT ANALYSIS TIME — one LLM call per run,
# saved with the profile. Without it, half the sections legitimately render
# "run a new analysis to generate one", which is a truthful empty state and a
# useless preview.
_EXAMPLES: dict = {
    "team_size": 5,
    "analysis_depth": "deep",
    "sprint_details": [
        {"name": "Sprint 101", "points": 22, "planned": 10, "completed": 9, "rate": 90, "done": True},
        {"name": "Sprint 102", "points": 25, "planned": 12, "completed": 10, "rate": 83, "done": True},
        {"name": "Sprint 103", "points": 24, "planned": 11, "completed": 11, "rate": 100, "done": True},
        {"name": "Sprint 104", "points": 21, "planned": 12, "completed": 9, "rate": 75, "done": False},
    ],
    "scope_changes": {
        "totals": {"avg_committed_velocity": 26.0, "avg_delivered_velocity": 23.5},
        "per_sprint": [
            {
                "name": "Sprint 101",
                "committed_pts": 26,
                "final_pts": 28,
                "scope_change_total": 2,
                "scope_churn": 0.12,
            },
            {
                "name": "Sprint 102",
                "committed_pts": 27,
                "final_pts": 25,
                "scope_change_total": -2,
                "scope_churn": 0.09,
            },
        ],
    },
    "narrative": {
        "executive_summary": (
            "Delivery is steady at 23.5 points a sprint with completion holding near 88%. "
            "Estimates hold on small stories and slip on eights, and a fifth of committed "
            "scope carries over — mostly backend work that starts late in the sprint."
        ),
        "sections": {
            "velocity": "Velocity is stable sprint to sprint; variance sits at 14%, well inside a healthy band.",
            "team": "Work is spread evenly; no single contributor carries more than a third of the points.",
            "estimation": "Estimates hold to 5 points and overshoot by a third at 8 — split the big ones.",
            "workflow": "Task breakdown is consistent, but only 44% of stories mention a deploy step.",
            "writing": "Tickets are well written: Given/When/Then is in use and ACs median three.",
            "trends": "No worrying long-term trends; spillover has fallen for three sprints running.",
            "recommendations": "Two things to tighten: eight-point stories, and PR linkage.",
        },
    },
    "insights": {
        "start": [
            {
                "title": "Split eight-point stories",
                "detail": "Break anything estimated at 8 into two stories before the sprint starts.",
                "evidence": "8-pt stories overshoot by 34% against a 20% average",
            },
            {
                "title": "Link every PR to its ticket",
                "detail": "Traceability is the one gap in an otherwise strong Definition of Done.",
                "evidence": "82% PR linkage against 85% comment coverage",
            },
        ],
        "stop": [
            {
                "title": "Committing above delivered velocity",
                "detail": "Plan to the 23.5 the team actually delivers, not the 26 it commits.",
                "evidence": "12.5% carried over, avg 3.2 pts per sprint",
            }
        ],
        "keep": [
            {
                "title": "Given/When/Then acceptance criteria",
                "detail": "Structured ACs are why estimates hold on small stories.",
                "evidence": "median 3 ACs, GWT detected across the sample",
            },
            {
                "title": "Consistent subtask naming",
                "detail": "Code/Testing labels make the board readable at a glance.",
                "evidence": "72% of stories carry subtasks",
            },
        ],
        "try": [
            {
                "title": "A WIP limit on backend",
                "detail": "Backend is the most common spillover reason; cap in-progress work.",
                "evidence": "backend stories named in most spillover",
            }
        ],
    },
    "contributor_stats": [
        {
            "name": "Ada Lovelace",
            "delivery_pts": 42.0,
            "recurring_pts": 6.0,
            "stories_completed": 14,
            "sprints_active": 8,
            "avg_cycle_time": 3.1,
            "spill_rate": 8.0,
            "top_discipline": "backend",
            "top_work_type": "feature",
        },
        {
            "name": "Grace Hopper",
            "delivery_pts": 38.0,
            "recurring_pts": 4.0,
            "stories_completed": 12,
            "sprints_active": 8,
            "avg_cycle_time": 2.6,
            "spill_rate": 5.0,
            "top_discipline": "backend",
            "top_work_type": "feature",
        },
        {
            "name": "Alan Turing",
            "delivery_pts": 31.0,
            "recurring_pts": 9.0,
            "stories_completed": 11,
            "sprints_active": 7,
            "avg_cycle_time": 4.4,
            "spill_rate": 18.0,
            "top_discipline": "frontend",
            "top_work_type": "bug",
        },
        {
            "name": "Barbara Liskov",
            "delivery_pts": 27.0,
            "recurring_pts": 3.0,
            "stories_completed": 9,
            "sprints_active": 6,
            "avg_cycle_time": 2.9,
            "spill_rate": 11.0,
            "top_discipline": "frontend",
            "top_work_type": "feature",
        },
        {
            "name": "Katherine Johnson",
            "delivery_pts": 24.0,
            "recurring_pts": 2.0,
            "stories_completed": 8,
            "sprints_active": 6,
            "avg_cycle_time": 2.2,
            "spill_rate": 4.0,
            "top_discipline": "qa",
            "top_work_type": "test",
        },
    ],
    "repositories": {
        "stories_with_repos": 46,
        "detection_sources": ["PR links", "branch names"],
        "top_repos": [
            {"repo": "acme/platform", "stories": 24, "pct": 52, "repo_avg_cycle_time": 3.4},
            {"repo": "acme/web", "stories": 14, "pct": 30, "repo_avg_cycle_time": 2.7},
            {"repo": "acme/infra", "stories": 8, "pct": 18, "repo_avg_cycle_time": 4.9},
        ],
        "spillover_repos": [
            {"repo": "acme/infra", "pct": 25},
            {"repo": "acme/platform", "pct": 11},
        ],
    },
    "additional_patterns": {
        "seasonal": {
            "monthly_avg": {"Jan": 21.0, "Feb": 24.5, "Mar": 25.0, "Apr": 22.0},
            "low_months": {"Jan": 21.0},
            "high_months": {"Mar": 25.0},
        },
        "bug_rate": {"bug_count": 11, "bug_pct": 17.0},
        "ac_patterns": {"stories_with_ac_pct": 84.0, "llm_parsed": True, "parse_stats": {"parsed": 54, "total": 64}},
    },
    "ai_adoption": {
        "repository_health": {
            "files_analysed": 128,
            "findings": 9,
            "actions": [
                {
                    "title": "Add tests to hot files",
                    "detail": "Four files change every sprint and have no direct test coverage.",
                    "affected_scope": ["acme/platform", "acme/web"],
                },
                {
                    "title": "Split the settings module",
                    "detail": "One file accounts for a fifth of all churn.",
                    "affected_scope": ["acme/platform"],
                },
            ],
        }
    },
    "doc_quality": {
        "coverage": ["confluence: 42 pages scanned", "notion: NOTION_TOKEN not set"],
        "findings": [
            {"title": "Onboarding page is 14 months old", "detail": "Last edited before the current stack landed."},
            {"title": "No owner on 9 pages", "detail": "Nobody is named on nearly a quarter of the space."},
        ],
    },
}


def _signals():
    """The global code and docs scans, as a live run would pass them in."""
    from yeaboi.team_profile import AiAdoptionSignal, DocQualitySignal

    return (
        AiAdoptionSignal(scanned_commits=240, ai_commits=96, footprint_pct=40.0, scanned_prs=48),
        DocQualitySignal(pages_scanned=42),
    )


def _results(view: str):
    """One of the result views — the overview, or a focused section card."""

    def build(w: int, h: int):
        code_signal, doc_signal = _signals()
        return _build_team_analysis_screen(
            _profile(),
            examples=_EXAMPLES,
            width=w,
            height=h,
            view=view,
            team_name="Platform Team",
            sprint_names=_SPRINT_NAMES,
            code_signal=code_signal,
            code_examples={
                "enabled_features": ["ai_footprint", "code_health"],
                "repository_health": _EXAMPLES["ai_adoption"]["repository_health"],
            },
            doc_signal=doc_signal,
            doc_examples=_EXAMPLES["doc_quality"],
            analysis_features=["delivery", "ai_footprint", "code_health", "documentation"],
        )

    return build


# Ordered the way you meet them: the gate, the setup flow, the run, the results.
SCREENS: dict[str, object] = {
    "board-empty": lambda w, h: _build_analysis_board_setup_screen({}, width=w, height=h),
    "board-partial": lambda w, h: _build_analysis_board_setup_screen(
        {"JIRA_BASE_URL": _JIRA_FULL["JIRA_BASE_URL"]}, selected=1, width=w, height=h
    ),
    "board-ready": lambda w, h: _build_analysis_board_setup_screen(_JIRA_FULL, width=w, height=h),
    "board-azure": lambda w, h: _build_analysis_board_setup_screen({}, tracker=1, width=w, height=h),
    "board-editing": lambda w, h: _build_analysis_board_setup_screen(
        _JIRA_FULL, selected=3, editing=("JIRA_PROJECT_KEY", "ACME-2", 6), width=w, height=h
    ),
    "features": lambda w, h: _build_analysis_feature_screen(
        _FEATURES_AVAILABLE, {"delivery", "code_health"}, 1, width=w, height=h
    ),
    "features-none-selected": lambda w, h: _build_analysis_feature_screen(
        _FEATURES_AVAILABLE, set(), 0, width=w, height=h, message="Pick at least one area."
    ),
    "depth": lambda w, h: _build_analysis_depth_screen(1, width=w, height=h),
    "model-offer": lambda w, h: _build_analysis_model_offer_screen(
        "llama3.1:70b", "llama3.1:8b", 1740, 0, width=w, height=h
    ),
    "window": lambda w, h: _build_analysis_window_screen(2, width=w, height=h),
    "progress": lambda w, h: _build_analysis_progress_screen(
        ["Fetching sprints…", "Reading stories…"], width=w, height=h
    ),
    "results-overview": _results("overview"),
    "results-velocity": _results("velocity"),
    "results-team": _results("team"),
    "results-estimation": _results("estimation"),
    "results-workflow": _results("workflow"),
    "results-writing": _results("writing"),
    "results-trends": _results("trends"),
    "results-recommendations": _results("recommendations"),
}


def _chrome(panel):
    """Wrap a bare screen Panel in the app-wide chrome.

    The back tab, music pocket and corner duck are drawn by MusicLive over the
    finished frame, not by the screen builders — so a preview that prints the
    bare Panel shows a screen with no back button, which is not what the app
    shows. _MusicPocketFrame is the same wrapper get_renderable() applies, read
    off the same opt-out attributes.
    """
    from yeaboi.ui.shared import _music_bar

    # Pin the tab fully extended: its presence normally eases in over several
    # frames, and a preview only ever draws one.
    _music_bar._back_presence = 1.0
    frame = _music_bar._MusicPocketFrame(
        panel,
        with_duck=not getattr(panel, "_no_companion_duck", False),
        with_back=not getattr(panel, "_no_back_hint", False),
        with_copy=bool(getattr(panel, "_copy_tab", False)),
        hint_tab=getattr(panel, "_hint_tab", None),
        duck_say=str(getattr(panel, "_duck_say", "") or ""),
    )
    frame.with_next = bool(getattr(panel, "_next_tab", False))
    return frame


def _render(name: str, console: Console, *, chrome: bool = True) -> None:
    w, h = console.size
    try:
        panel = SCREENS[name](w, h - 2)
        console.print(_chrome(panel) if chrome else panel)
    except Exception as exc:  # noqa: BLE001 - a broken screen shouldn't kill the gallery
        console.print(f"[bold red]{name} failed to render:[/] {exc!r}")


def _interactive(names: list[str], start: int, *, chrome: bool = True) -> None:
    from yeaboi.ui.shared._input import enter_raw_mode, exit_raw_mode, read_key

    console = Console()
    idx = start
    enter_raw_mode()
    try:
        while True:
            console.clear()
            _render(names[idx], console, chrome=chrome)
            console.print(f"[dim]{idx + 1}/{len(names)}  {names[idx]}   ←/→ move · r redraw · q quit[/]")
            key = read_key()
            if key in ("q", "esc", "ctrl+c"):
                return
            if key in ("right", "down", "j", " ", "enter"):
                idx = (idx + 1) % len(names)
            elif key in ("left", "up", "k"):
                idx = (idx - 1) % len(names)
            elif key == "r":
                # Re-import so an edited builder shows up without restarting.
                console.print("[dim]reload: restart the script to pick up edits[/]")
    finally:
        exit_raw_mode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("screen", nargs="?", help="render just this screen and exit")
    parser.add_argument("--list", action="store_true", help="print the screen names")
    parser.add_argument("--all", action="store_true", help="print every screen and exit")
    parser.add_argument("--width", type=int, default=0, help="render at this width instead of the terminal's")
    parser.add_argument("--height", type=int, default=0, help="render at this height instead of the terminal's")
    parser.add_argument(
        "--no-chrome", action="store_true", help="draw the bare screen without the back tab / music pocket / duck"
    )
    args = parser.parse_args()

    names = list(SCREENS)
    if args.list:
        print("\n".join(names))
        return 0

    kwargs = {}
    if args.width:
        kwargs["width"] = args.width
    if args.height:
        kwargs["height"] = args.height
    console = Console(**kwargs)

    if args.all:
        for name in names:
            console.rule(f"[bold]{name}")
            _render(name, console, chrome=not args.no_chrome)
        return 0

    if args.screen:
        if args.screen not in SCREENS:
            print(f"unknown screen {args.screen!r}\n\ntry one of:\n  " + "\n  ".join(names), file=sys.stderr)
            return 2
        _render(args.screen, console, chrome=not args.no_chrome)
        return 0

    if not sys.stdin.isatty():
        # Piped or redirected — there's no terminal to read keys from, so print
        # everything rather than dying in termios.
        for name in names:
            console.rule(f"[bold]{name}")
            _render(name, console, chrome=not args.no_chrome)
        return 0

    _interactive(names, 0, chrome=not args.no_chrome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
