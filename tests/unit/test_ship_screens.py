"""Render tests for the Ship mode screens (_screens_ship.py).

Every ``_build_*_screen`` renders through a real Console — the assertion is
that the load-bearing facts (story ids, validation verdicts, findings, the
gate's warning states) are actually visible, and that masking rules hold
(the diff stat and validation tail are capped, never dumped whole).
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from yeaboi.agent.state import (
    AcceptanceCriterion,
    Priority,
    ShipPhase,
    ShipRun,
    ShipValidation,
    StoryPointValue,
    UserStory,
)
from yeaboi.ship.scope import OutlineRow
from yeaboi.ui.mode_select.screens._screens_ship import (
    SCOPE_SPLIT,
    _build_ship_gate_screen,
    _build_ship_pick_screen,
    _build_ship_progress_screen,
    _build_ship_result_screen,
)


def _render(panel, width: int = 100, height: int = 40) -> str:
    console = Console(file=io.StringIO(), width=width, height=height)
    console.print(panel)
    return console.file.getvalue()


def _story(story_id="US-001", title="Ship pipeline"):
    return UserStory(
        id=story_id,
        feature_id="F-1",
        persona="dev",
        goal="ship",
        benefit="speed",
        acceptance_criteria=(AcceptanceCriterion(given="g", when="w", then="t"),),
        story_points=StoryPointValue.THREE,
        priority=Priority.HIGH,
        title=title,
    )


def _row(level, item_id, title, *, detail="", depth=0, parent=""):
    return OutlineRow(
        key=f"{level}:{item_id}", level=level, id=item_id, title=title, detail=detail, depth=depth, parent_key=parent
    )


def _tree(epics=2, stories_each=2, tasks_each=0):
    """A rendered-order outline, the shape the page loop hands the screen."""
    rows = []
    for e in range(epics):
        epic = _row("epic", f"F{e + 1}", f"Epic number {e + 1}", detail=f"{stories_each} stories")
        rows.append(epic)
        for n in range(stories_each):
            sid = f"US-F{e + 1}-{n + 1:03d}"
            rows.append(_row("story", sid, f"Story {n + 1}", detail="3 pts", depth=1, parent=epic.key))
            for t in range(tasks_each):
                rows.append(
                    _row("task", f"T-{sid}-{t + 1:02d}", f"Task {t + 1}", detail="Code", depth=2, parent=f"story:{sid}")
                )
    return rows


class TestPickScreen:
    def test_lists_every_level_with_its_detail(self):
        rows = _tree(epics=1, stories_each=1, tasks_each=1)
        out = _render(
            _build_ship_pick_screen(
                rows,
                0,
                expanded={"epic:F1", "story:US-F1-001"},
                has_children={"epic:F1", "story:US-F1-001"},
                repo="/home/dev/proj",
                check_command="make test",
            )
        )
        assert "F1" in out
        assert "US-F1-001" in out
        assert "T-US-F1-001-01" in out
        assert "3 pts" in out
        assert "/home/dev/proj" in out
        assert "make test" in out

    def test_empty_plan_names_the_next_step(self):
        out = _render(_build_ship_pick_screen([], 0, repo="/p", check_command=""))
        assert "No plan found" in out
        assert "Planning" in out

    def test_a_collapsed_parent_shows_a_closed_chevron_after_its_row(self):
        rows = [_row("epic", "F1", "Only epic", detail="2 stories")]
        out = _render(_build_ship_pick_screen(rows, 0, has_children={"epic:F1"}, repo="/p", check_command=""))
        line = next(line for line in out.splitlines() if "Only epic" in line)
        assert line.rstrip().rstrip("\u2502 ").endswith("\u25b8")
        assert "\u25be" not in out

    def test_a_leaf_row_carries_no_chevron_at_all(self):
        rows = [_row("task", "T-1", "A task", depth=2)]
        out = _render(_build_ship_pick_screen(rows, 0, repo="/p", check_command=""))
        line = next(line for line in out.splitlines() if "A task" in line)
        assert "\u25be" not in line
        # The only ▸ on the row is the selection cursor at its head.
        assert line.count("\u25b8") == 1

    def test_an_expanded_parent_shows_an_open_chevron(self):
        rows = _tree(epics=1, stories_each=1)
        out = _render(
            _build_ship_pick_screen(
                rows, 0, expanded={"epic:F1"}, has_children={"epic:F1"}, repo="/p", check_command=""
            )
        )
        assert "\u25be" in out

    def test_overflow_is_counted_not_dumped(self):
        rows = _tree(epics=30, stories_each=0)
        out = _render(_build_ship_pick_screen(rows, 0, repo="/p", check_command=""))
        assert "F1" in out
        assert "F30" not in out
        assert "more" in out

    def test_the_window_follows_a_late_selection(self):
        # A row past the fold must be reachable, not just counted — the window slides.
        rows = _tree(epics=30, stories_each=0)
        out = _render(_build_ship_pick_screen(rows, 29, repo="/p", check_command=""))
        assert "F30" in out
        assert "above" in out

    def test_edit_mode_shows_the_live_buffer(self):
        out = _render(
            _build_ship_pick_screen(
                _tree(epics=1, stories_each=0),
                0,
                repo="/old",
                check_command="",
                edit_field="repo",
                edit_buf="/typed/so/far",
            )
        )
        assert "/typed/so/far" in out

    def test_scope_offers_the_split_only_on_an_epic(self):
        rows = _tree(epics=1, stories_each=2)
        epic = _render(
            _build_ship_pick_screen(
                rows,
                0,
                expanded={"epic:F1"},
                has_children={"epic:F1"},
                scope_mode=SCOPE_SPLIT,
                split_count=2,
                repo="/p",
                check_command="",
            )
        )
        assert "Separately" in epic
        assert "2 runs" in epic
        story = _render(
            _build_ship_pick_screen(
                rows,
                1,
                expanded={"epic:F1"},
                has_children={"epic:F1"},
                scope_mode=SCOPE_SPLIT,
                repo="/p",
                check_command="",
            )
        )
        # Scope is a per-launch choice, and a story has no child stories to split
        # over: the field must say so rather than promise a fan-out that cannot happen.
        assert "Separately" not in story
        assert "single unit" in story

    @pytest.mark.parametrize("size", [(84, 40), (100, 40), (120, 24)])
    def test_the_buttons_survive_a_crowded_tree(self, size):
        width, height = size
        rows = _tree(epics=8, stories_each=4, tasks_each=3)
        out = _render(
            _build_ship_pick_screen(
                rows,
                len(rows) // 2,
                expanded={r.key for r in rows},
                has_children={r.parent_key for r in rows if r.parent_key},
                repo="/home/dev/a-fairly-long-repository-path/project",
                check_command="make test && make lint",
                width=width,
                height=height,
            ),
            width=width,
            height=height,
        )
        assert "Launch" in out
        assert "Back" in out

    def test_no_outline_row_wraps(self):
        # The window counts rows; one wrapped row pushes the buttons off a
        # fixed-height Panel, which is the layout's single invariant.
        rows = [_row("story", "US-1", "A title far longer than any terminal will ever be " * 6, depth=1)]
        out = _render(_build_ship_pick_screen(rows, 0, repo="/p", check_command="", width=84), width=84)
        body = [line for line in out.splitlines() if "US-1" in line]
        assert len(body) == 1


class TestProgressScreen:
    def test_checklist_shows_pending_and_running_phases(self):
        events = [
            {
                "kind": "analysis_component",
                "component_id": "ship-setup",
                "label": "Preparing isolated worktree",
                "status": "completed",
                "detail": "",
            },
            {
                "kind": "analysis_component",
                "component_id": "ship-implement",
                "label": "Implementing",
                "status": "running",
                "detail": "",
            },
        ]
        out = _render(_build_ship_progress_screen(events, tick=3.0))
        assert "Prepare isolated worktree" in out
        assert "Run the coding agent" in out
        assert "Await your approval" in out  # pending rows render too
        assert "esc cancels the run" in out

    def test_share_link_and_join_code_render_together(self):
        # A teammate opening the link lands on the join gate; without the code
        # shown here the feature is unusable (the code otherwise lives only in
        # the log file). The code rides a *separate* line from the token-free URL.
        out = _render(
            _build_ship_progress_screen(
                [], tick=1.0, board_link="https://abc.trycloudflare.com", board_code="WXYZ-1234"
            )
        )
        assert "abc.trycloudflare.com" in out
        assert "WXYZ-1234" in out

    def test_no_code_line_before_the_tunnel_is_up(self):
        # Loopback-only (no share_url yet): no code line, nothing half-shown.
        out = _render(_build_ship_progress_screen([], tick=1.0, board_link="", board_code=""))
        assert "join code" not in out


class TestGateScreen:
    # The TUI refuses to run below 84x40 (`_screens.py` _MIN_WIDTH/_MIN_HEIGHT),
    # so the gate is asserted at the smallest window a user can actually be in
    # — the builder's own 24-row default is a size the app never renders.
    def _gate(self, run, *, width: int = 84, height: int = 40, **kwargs) -> str:
        return _render(_build_ship_gate_screen(run, width=width, height=height, **kwargs), width=width, height=height)

    def _run(self, **overrides):
        base = ShipRun(
            run_id="run-1",
            item_id="US-001",
            branch="ship/run-1",
            status="awaiting_approval",
            diff_stat="src/app.py | 10 ++++\n2 files changed, 12 insertions(+)",
            validation=ShipValidation(configured=True, command="make test", passed=True, exit_code=0),
            cost_usd=0.42,
        )
        return ShipRun(**{**base.__dict__, **overrides})

    def test_shows_diff_validation_and_cost(self):
        out = self._gate(self._run())
        assert "US-001" in out
        assert "ship/run-1" in out
        assert "2 files changed" in out
        assert "make test" in out
        assert "passed" in out
        assert "$0.42" in out

    def test_failed_validation_is_loud_with_its_tail(self):
        run = self._run(
            validation=ShipValidation(
                configured=True, command="make test", passed=False, exit_code=2, output_tail="FAILED test_x"
            )
        )
        out = self._gate(run)
        assert "FAILED" in out
        assert "FAILED test_x" in out

    def test_no_validation_is_a_visible_warning_not_silence(self):
        run = self._run(validation=ShipValidation())
        out = self._gate(run)
        assert "nothing was proven" in out

    def test_transcript_findings_surface_as_labels_only(self):
        run = self._run(transcript_findings=(("secret", "critical", "anthropic api key"),))
        out = self._gate(run)
        assert "anthropic api key" in out
        assert "1 transcript finding" in out

    def test_rejection_comment_editor_renders(self):
        out = self._gate(self._run(), comment_edit="wrong file")
        assert "Why reject?" in out
        assert "wrong file" in out

    def test_the_patch_itself_is_on_the_screen_not_just_a_file_count(self):
        # The gate is the only control before a push; approving on a --stat
        # summary is not review.
        patch = "diff --git a/app.py b/app.py\n@@ -1,2 +1,2 @@\n-old = 1\n+new = 2\n"
        out = self._gate(self._run(diff_text=patch, worktree="/tmp/wt/run-1"))
        assert "+new = 2" in out
        assert "-old = 1" in out
        assert "/tmp/wt/run-1" in out  # where to read the rest out of band

    def test_a_long_patch_scrolls_rather_than_truncating_silently(self):
        patch = "\n".join(f"+line {n:03d}" for n in range(200))
        top = self._gate(self._run(diff_text=patch), diff_offset=0)
        scrolled = self._gate(self._run(diff_text=patch), diff_offset=100)
        assert "+line 000" in top
        assert "+line 000" not in scrolled
        assert "+line 100" in scrolled

    def test_an_unreadable_patch_says_so_instead_of_looking_clean(self):
        out = self._gate(self._run(diff_text=""))
        assert "could not be read" in out

    def test_the_buttons_survive_a_crowded_gate_at_the_minimum_terminal_size(self):
        # The pane takes what is left and no more. A Panel of fixed height
        # crops from the bottom, and a cropped button row still answers Enter
        # with "Approve" — which is a push nobody could see the buttons for.
        run = self._run(
            diff_stat="\n".join(f"src/f{n}.py | 4 ++--" for n in range(8)) + "\n8 files changed",
            diff_text="\n".join(f"+line {n:03d}" for n in range(500)),
            validation=ShipValidation(
                configured=True,
                command="make test",
                passed=False,
                exit_code=1,
                output_tail="\n".join(f"FAILED test_{n}" for n in range(8)),
            ),
            transcript_findings=(
                ("secret", "critical", "api key"),
                ("risky_tool", "high", "curl | sh"),
                ("secret", "high", "token"),
                ("risky_tool", "medium", "rm -rf"),
            ),
            rejection_count=1,
        )
        for width, height in ((84, 40), (100, 40), (120, 24)):
            out = self._gate(run, width=width, height=height)
            assert "Approve" in out, f"buttons cropped at {width}x{height}"
            assert "Reject" in out, f"buttons cropped at {width}x{height}"

    def test_full_width_patch_lines_and_a_real_worktree_path_do_not_crop_the_buttons(self):
        # A wrapped row is a row the layout did not count. Patch lines at this
        # repo's own 120-column limit and a real ~/.yeaboi worktree path are
        # the normal case, not the edge one.
        run = self._run(
            worktree="/Users/somebody/.yeaboi/ship/worktrees/yeaboi-ai/us-001-20260817083000-a1b2c3",
            branch="ship/us-001-20260817083000-a1b2c3",
            diff_stat="src/some/deeply/nested/module_with_a_very_long_name.py | 40 +++++++++-----\n1 file changed",
            diff_text="\n".join("+" + "x" * 118 for _ in range(200)),
            validation=ShipValidation(
                configured=True,
                command="make test && make lint && make parity",
                passed=False,
                exit_code=1,
                output_tail="\n".join("E   " + "y" * 116 for _ in range(6)),
            ),
        )
        for width in (84, 100, 120):
            out = self._gate(run, width=width)
            assert "Approve" in out, f"buttons cropped at {width} columns"
            body_rows = [line for line in out.splitlines() if line.startswith("│")]
            assert len(body_rows) <= 40, f"panel overflowed its height at {width} columns"

    def test_control_characters_in_agent_text_never_reach_the_terminal(self):
        # The patch is written by the agent and this gate is the only control
        # before a push: an escape sequence could repaint over what the
        # reviewer is reading, so they approve what they saw and not what is
        # on disk. Rich strips BEL/BS/VT/FF/CR but NOT ESC.
        run = self._run(
            diff_text="+innocent\n+\x1b[2J\x1b[Hwiped the screen\n",
            validation=ShipValidation(
                configured=True, command="make test", passed=False, exit_code=1, output_tail="\x1b[31mFAILED\x07"
            ),
        )
        out = self._gate(run)
        assert "\x1b[2J" not in out
        assert "\x1b[31m" not in out
        assert "wiped the screen" in out  # the text survives; only the controls go

    def test_a_window_too_short_for_both_drops_the_pane_and_says_where_to_look(self):
        run = self._run(diff_text="\n".join(f"+line {n}" for n in range(200)), worktree="/tmp/wt/run-1")
        out = self._gate(run, height=22)
        assert "Approve" in out
        assert "patch hidden" in out
        assert "/tmp/wt/run-1" in out

    def test_the_builder_publishes_the_panes_geometry_to_the_loop(self):
        # The loop clamps with these numbers; a builder that clamps privately
        # is the "scrolling sometimes does nothing" bug _scroll.py exists for.
        meta: dict = {}
        run = self._run(diff_text="\n".join(f"+line {n}" for n in range(200)))
        self._gate(run, scroll_meta=meta)
        assert meta["max_offset"] == 200 - meta["viewport_h"]
        assert meta["viewport_h"] >= 3


class TestResultScreen:
    def test_approved_run_shows_pr_and_phases(self):
        run = ShipRun(
            run_id="run-1",
            item_id="US-001",
            branch="ship/run-1",
            status="approved",
            pr_url="https://github.com/o/r/pull/7",
            cost_usd=1.2,
            phases=(
                ShipPhase(name="setup", status="completed", duration_s=2.0),
                ShipPhase(name="implement", status="completed", duration_s=120.0),
            ),
        )
        out = _render(_build_ship_result_screen(run))
        assert "Shipped" in out
        assert "github.com/o/r/pull/7" in out
        assert "implement" in out
        assert "$1.20" in out

    def test_failed_run_carries_its_warnings(self):
        run = ShipRun(run_id="run-1", status="failed", warnings=("the agent produced no changes",))
        out = _render(_build_ship_result_screen(run))
        assert "Run failed" in out
        assert "produced no changes" in out


class TestSnapshotScreen:
    """The saved run in the hub — the gate screen, read-only.

    Same builder, so every invariant the live gate is held to (buttons survive,
    no row wraps, control characters never escape) applies unchanged; what is
    asserted here is the difference: the header states an outcome instead of
    asking for a decision, and the caller owns the button row.
    """

    def _snap(self, run, *, actions=None, width: int = 84, height: int = 40, **kwargs) -> str:
        panel = _build_ship_gate_screen(
            run,
            width=width,
            height=height,
            snapshot=True,
            actions=actions or ["Export", "Delete", "Back"],
            **kwargs,
        )
        return _render(panel, width=width, height=height)

    def _run(self, **overrides):
        base = ShipRun(
            run_id="run-1",
            item_id="US-001",
            branch="ship/run-1",
            status="approved",
            diff_stat="src/app.py | 10 ++++\n2 files changed, 12 insertions(+)",
            diff_text="diff --git a/src/app.py b/src/app.py\n+added\n",
            validation=ShipValidation(configured=True, command="make test", passed=True, exit_code=0),
            cost_usd=0.42,
        )
        return ShipRun(**{**base.__dict__, **overrides})

    def test_it_renders_the_callers_buttons_not_the_gates(self):
        out = self._snap(self._run())
        assert "Export" in out and "Back" in out
        assert "Approve" not in out and "Reject" not in out

    def test_a_resumable_run_can_offer_resume(self):
        out = self._snap(self._run(status="awaiting_approval"), actions=["Resume", "Export", "Delete", "Back"])
        assert "Resume" in out

    def test_the_header_states_the_outcome_instead_of_asking_for_one(self):
        assert "approved and pushed" in self._snap(self._run())
        assert "like a stranger wrote it" not in self._snap(self._run())

    def test_a_stranded_run_says_it_can_be_resumed(self):
        assert "resume to finish it" in self._snap(self._run(status="awaiting_approval"))

    def test_an_unknown_status_still_gets_a_header(self):
        assert "planned" in self._snap(self._run(status="planned"))

    def test_the_patch_is_still_shown(self):
        assert "added" in self._snap(self._run())

    @pytest.mark.parametrize("size", [(84, 40), (100, 40), (120, 24)])
    def test_the_buttons_always_render(self, size):
        width, height = size
        crowded = self._run(
            worktree="/Users/someone/.yeaboi/ship/worktrees/proj/run-1",
            diff_text="\n".join(f"+ line {i} of a long patch" for i in range(200)),
            validation=ShipValidation(
                configured=True,
                command="make test",
                passed=False,
                exit_code=1,
                output_tail="\n".join(f"FAILED test_{i}" for i in range(40)),
            ),
            transcript_findings=(("secret", "high", "a key in a fixture"),),
            rejection_count=2,
        )
        out = self._snap(crowded, width=width, height=height)
        assert "Export" in out and "Back" in out

    def test_no_row_wraps_the_panel_past_its_height(self):
        crowded = self._run(
            worktree="/Users/someone/.yeaboi/ship/worktrees/proj/run-1",
            diff_text="\n".join("+" + "x" * 200 for _ in range(50)),
        )
        out = self._snap(crowded, width=84, height=40)
        assert len([line for line in out.splitlines() if line.startswith("│")]) <= 40

    def test_control_characters_never_reach_the_terminal(self):
        out = self._snap(self._run(diff_text="\x1b[2Jclobber\n\x1b[31mred\n"))
        assert "\x1b[2J" not in out
        assert "clobber" in out

    def test_it_publishes_geometry_so_the_hub_can_clamp_scroll(self):
        meta: dict = {}
        self._snap(
            self._run(diff_text="\n".join(f"+ line {i}" for i in range(200))),
            scroll_meta=meta,
        )
        assert meta.get("viewport_h", 0) >= 3
        assert meta["max_offset"] == 200 - meta["viewport_h"]

    def test_geometry_is_published_even_when_the_patch_pane_is_dropped(self):
        meta: dict = {}
        self._snap(self._run(), height=22, scroll_meta=meta)
        assert "max_offset" in meta and "viewport_h" in meta
