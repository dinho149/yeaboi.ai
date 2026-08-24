"""Tests for the Ship TUI page loop (ui/mode_select/_ship.py).

The page loop is where two of the gate's guarantees actually live: the repo a
run is launched against is the *git toplevel* (so the consent prompt names what
will be written), and the patch pane scrolls through the shared ``_scroll``
helpers rather than a hand-rolled offset that can drift past the last line.

The loop itself is driven with a scripted ``read_key`` and a fake Live, the
same shape the other page-loop tests use — no terminal, no threads.
"""

from __future__ import annotations

import subprocess

import pytest

from yeaboi.agent.state import ShipRun, ShipValidation
from yeaboi.tools.local_git import git_subprocess_env
from yeaboi.ui.mode_select import _ship


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=git_subprocess_env())


@pytest.fixture()
def repo(tmp_path):
    """A real repo with a subdirectory and one commit."""
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    (root / "src").mkdir()
    return root


class TestResolveTarget:
    def test_a_subdirectory_resolves_to_the_toplevel(self, repo):
        # Consent is checked against what comes back, and every write lands on
        # the toplevel — so a subdirectory must not be what gets granted.
        target, problem = _ship._resolve_target(str(repo / "src"))
        assert problem == ""
        assert target == str(repo)

    def test_a_dirty_repo_is_refused_by_name(self, repo):
        (repo / "README.md").write_text("changed\n", encoding="utf-8")
        target, problem = _ship._resolve_target(str(repo))
        assert target == str(repo)
        assert "uncommitted changes" in problem

    def test_a_path_outside_any_repo_has_no_target(self, tmp_path):
        target, problem = _ship._resolve_target(str(tmp_path))
        assert target == ""
        assert problem  # git's own words; the caller shows them and stops


class _Live:
    """Captures whatever the loop renders; the last frame is the assertion."""

    def __init__(self):
        self.frames: list = []

    def update(self, renderable):
        self.frames.append(renderable)


class _Console:
    size = (84, 40)


class _Store:
    """A stand-in ShipStore that records how the gate was resolved."""

    def __init__(self):
        self.resolved: list[tuple[str, str, str]] = []

    def resolve_gate(self, run_id, resolution, comment=""):
        self.resolved.append((run_id, resolution, comment))
        return True


def _keys(*sequence):
    """A read_key that plays a script; timeout=0.0 polls drain as empty."""
    remaining = list(sequence)

    def _read(timeout=None):
        if timeout == 0.0:
            return ""  # nothing buffered — ends a coalesced scroll burst
        return remaining.pop(0) if remaining else "esc"

    return _read


def _gated_run(**overrides):
    base = {
        "run_id": "run-1",
        "item_id": "US-001",
        "branch": "ship/run-1",
        "worktree": "/tmp/wt/run-1",
        "status": "awaiting_approval",
        "diff_stat": "src/app.py | 2 +-\n1 file changed",
        "diff_text": "\n".join(f"+line {n:03d}" for n in range(200)),
        "validation": ShipValidation(configured=True, command="make test", passed=True, exit_code=0),
    }
    return ShipRun(**{**base, **overrides})


class TestGateLoop:
    def _run_loop(self, keys, store=None, run=None):
        live, cancel = _Live(), __import__("threading").Event()
        outcome = _ship._gate_loop(
            _Console(),
            live,
            keys,
            0.05,
            True,
            store or _Store(),
            run or _gated_run(),
            cancel,
        )
        return outcome, live, cancel

    def test_enter_on_approve_resolves_the_gate(self):
        store = _Store()
        outcome, _live, _cancel = self._run_loop(_keys("enter"), store=store)
        assert outcome == "resolved"
        assert store.resolved == [("run-1", "approved", "")]

    def test_esc_keeps_the_run_waiting_without_answering(self):
        store = _Store()
        outcome, _live, _cancel = self._run_loop(_keys("esc"), store=store)
        assert outcome == "resolved"  # back to the progress screen
        assert store.resolved == []  # …but nothing was approved or rejected

    def test_cancel_run_sets_the_event(self):
        outcome, _live, cancel = self._run_loop(_keys("right", "right", "enter"))
        assert outcome == "cancelled"
        assert cancel.is_set()

    @pytest.mark.parametrize("key", ["down", "pagedown", "end", "scroll_down"])
    def test_every_scroll_key_moves_the_patch(self, key):
        # pgup/pgdn used to be spelled "pgup"/"pgdn" here and simply never
        # fired — read_key emits "pageup"/"pagedown". Routing through
        # SCROLL_KEYS is what makes the whole family work at once.
        _outcome, live, _cancel = self._run_loop(_keys(key, "esc"))
        first, second = _render(live.frames[0]), _render(live.frames[1])
        assert "+line 000" in first
        assert first != second

    def test_scrolling_past_the_end_does_not_strand_the_offset(self):
        # The builder publishes the true maximum, so five "end"s land on the
        # same offset as one and a single "up" moves visibly. A loop keeping
        # its own counter would need four dead keypresses to come back.
        _outcome, live, _cancel = self._run_loop(_keys(*(["end"] * 5), "up", "esc"))
        # One frame is painted before each key is read, so the last two are
        # "parked at the bottom" and "one line back up".
        at_bottom = _render(live.frames[-2])
        after_one_up = _render(live.frames[-1])
        assert "+line 199" in at_bottom
        assert "+line 199" not in after_one_up
        assert "+line 198" in after_one_up

    def test_rejection_sends_the_typed_comment(self):
        store = _Store()
        keys = _keys("right", "enter", "n", "o", "enter")
        outcome, _live, _cancel = self._run_loop(keys, store=store)
        assert outcome == "resolved"
        assert store.resolved == [("run-1", "rejected", "no")]


def _render(panel, width: int = 84, height: int = 40) -> str:
    import io

    from rich.console import Console

    console = Console(file=io.StringIO(), width=width, height=height)
    console.print(panel)
    return console.file.getvalue()


class TestVisibleRows:
    """Expansion is recomputed from (rows, expanded) every frame, never stored."""

    @staticmethod
    def _tree():
        from yeaboi.ship.scope import OutlineRow

        return [
            OutlineRow(key="epic:F1", level="epic", id="F1", title="One", detail="", depth=0),
            OutlineRow(key="story:S1", level="story", id="S1", title="A", detail="", depth=1, parent_key="epic:F1"),
            OutlineRow(key="task:T1", level="task", id="T1", title="a", detail="", depth=2, parent_key="story:S1"),
            OutlineRow(key="epic:F2", level="epic", id="F2", title="Two", detail="", depth=0),
        ]

    def test_nothing_expanded_shows_only_the_epics(self):
        assert [r.key for r in _ship._visible_rows(self._tree(), set())] == ["epic:F1", "epic:F2"]

    def test_a_grandchild_stays_hidden_while_its_parent_is(self):
        # The task's own parent (the story) is expanded, but the story is not
        # drawn — a parent test alone would leak the task onto the screen.
        visible = _ship._visible_rows(self._tree(), {"story:S1"})
        assert [r.key for r in visible] == ["epic:F1", "epic:F2"]

    def test_expanding_the_whole_chain_shows_every_row(self):
        visible = _ship._visible_rows(self._tree(), {"epic:F1", "story:S1"})
        assert [r.key for r in visible] == ["epic:F1", "story:S1", "task:T1", "epic:F2"]


class TestBatchMessage:
    def test_a_finished_batch_counts_what_shipped(self):
        members = [_batch_member("US-1", "approved"), _batch_member("US-2", "approved")]
        assert _ship._batch_message(members) == "Batch complete — 2 of 2 stories shipped."

    def test_a_stopped_batch_names_the_reason_in_the_stopping_run_s_words(self):
        members = [
            _batch_member("US-1", "approved"),
            _batch_member("US-2", "rejected", warnings=("hourly-budget (2/2 in last hour)",)),
            _batch_member("US-3", "planned", warnings=("never started",)),
        ]
        message = _ship._batch_message(members)
        assert message.startswith("Batch stopped — 1 of 3 stories shipped.")
        assert "hourly-budget (2/2 in last hour)" in message

    def test_no_members_says_nothing(self):
        assert _ship._batch_message([]) == ""


def _batch_member(item_id: str, status: str, *, warnings: tuple[str, ...] = ()) -> ShipRun:
    return ShipRun(run_id=f"r-{item_id}", item_id=item_id, level="story", status=status, warnings=warnings)


class TestContinueBatch:
    """Continuing is the same call as launching — run_ship_batch adopts the batch."""

    def test_it_forwards_the_epic_and_the_check_command_to_a_split_launch(self, monkeypatch):
        seen: dict = {}

        def _fake_launch(*_a, **kw):
            seen.update(kw)
            return "done"

        monkeypatch.setattr(_ship, "_launch", _fake_launch)
        result = _ship.continue_batch_page(
            None,
            None,
            None,
            0.05,
            True,
            item_id="F1",
            repo="/tmp/proj",
            session_id="sess-1",
            check_command="make test",
        )
        assert result == "done"
        # A continuation that quietly stopped validating would be the worst
        # kind of surprise, so the command has to travel with it.
        assert seen["check_command"] == "make test"
        assert (seen["item_id"], seen["level"], seen["split"]) == ("F1", "epic", True)
        assert seen["session_id"] == "sess-1"
