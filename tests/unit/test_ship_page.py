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
