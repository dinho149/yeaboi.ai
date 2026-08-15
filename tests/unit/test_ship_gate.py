"""Guards for the ship gate: the Makefile targets, the preflight map, the commands.

Everything here is a repo-reading guard rather than a module test, which is why it
is registered in ``scripts/test_scope.py``'s ``ALWAYS`` — nothing about a changed
source file implies it.

Three of these encode a bug that actually shipped:

* ``/ship`` handed ``code-reviewer`` ``git diff main...HEAD``. Local ``main`` in a
  worktree is routinely several commits behind ``origin/main``, so the reviewer
  received other people's already-merged PRs and none of the branch's work — and a
  review of the wrong diff reports clean.
* ``make test`` ran 10,383 unit tests single-threaded while ``make test-fast`` ran
  the identical set with ``-n auto``. 310 of the gate's 408 seconds were that.
* ``make security``'s SAST line was ``ruff check src/ tests/``, byte for byte the
  whole of ``make lint``, so the gate linted twice.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
COMMANDS = sorted((ROOT / ".claude" / "commands").glob("*.md"))
AGENTS = sorted((ROOT / ".claude" / "agents").glob("*.md"))


def _recipe(target: str) -> tuple[str, str]:
    """Return (prerequisites, recipe body) for a Makefile target."""
    text = MAKEFILE.read_text()
    match = re.search(rf"^{re.escape(target)}:(.*?)$\n((?:[\t#].*\n|\n(?=[\t#]))*)", text, re.MULTILINE)
    assert match, f"Makefile has no target {target!r}"
    prereqs = match.group(1).split("##")[0].strip()
    return prereqs, match.group(2)


class TestTheGateIsFast:
    """`make test` must not re-run the unit lane serially."""

    def test_test_delegates_to_the_parallel_and_serial_lanes(self):
        prereqs, body = _recipe("test")
        assert prereqs.split() == ["test-fast", "test-slow"], (
            "`make test` should be `test: test-fast test-slow` so the unit lane runs under "
            f"PYTEST_PARALLEL and the flags cannot drift from the lane variables. Got: {prereqs!r}"
        )
        assert "pytest" not in body, (
            "`make test` grew its own pytest invocation again — that is how the serial unit lane "
            "came back the first time. Delegate to test-fast/test-slow instead."
        )

    def test_the_unit_lane_is_parallel_and_the_slow_lane_is_not(self):
        _, fast = _recipe("test-fast")
        _, slow = _recipe("test-slow")
        assert "$(PYTEST_PARALLEL)" in fast
        # tests/integration/test_repl.py monkeypatches ten-plus names and CLAUDE.md
        # forbids editing it, so the slow lane stays serial deliberately.
        assert "$(PYTEST_PARALLEL)" not in slow

    def test_make_may_not_reorder_the_gate(self):
        assert ".NOTPARALLEL:" in MAKEFILE.read_text(), (
            "`test` and `ship-gate` order their prerequisites deliberately, and two pytest "
            "processes in one worktree invent failures. `make -j` must not reorder them."
        )

    def test_security_does_not_repeat_lint(self):
        prereqs, body = _recipe("security")
        assert "lint" in prereqs.split(), "`security` should take `lint` as a prerequisite, not repeat its command"
        assert "ruff check" not in body, (
            "`make security`'s SAST step is `make lint`, byte for byte. Running it again makes the "
            "ship gate lint twice for nothing."
        )


class TestTheGateIsComplete:
    """`make ship-gate` must cover what CI checks, including the non-pytest half."""

    def test_ship_gate_runs_every_half_of_the_gate(self):
        prereqs, _ = _recipe("ship-gate")
        assert set(prereqs.split()) >= {"lint", "format-check", "test", "security", "preflight"}, (
            f"ship-gate is missing part of the gate. Got: {prereqs!r}"
        )

    def test_format_check_asserts_rather_than_writes(self):
        """CI's `Format check (ruff)` is a required check and had no local twin."""
        _, body = _recipe("format-check")
        assert "--check" in body
        _, write = _recipe("format")
        assert "--check" not in write, "`make format` writes; `make format-check` asserts. Do not merge them."

    def test_the_wheel_assertion_is_not_duplicated_in_ci(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        assert "make package-check" in ci, "ci.yml's package job should call the same script `make package-check` does"
        assert "zipfile.ZipFile" not in ci, (
            "the wheel assertion is inlined in ci.yml again — that is the version nobody can run locally"
        )


class TestPreflightCoversEveryJob:
    def test_every_scope_job_has_a_target(self):
        """A job the selector knows about and preflight does not would simply never run locally.

        Same shape as `test_test_scope.py`'s totality checks: a selector's failure
        mode is silence, so the totality check is the feature.
        """
        sys.path.insert(0, str(ROOT / "scripts"))
        from preflight import JOB_TARGETS  # noqa: PLC0415
        from test_scope import JOBS  # noqa: PLC0415

        assert {job.name for job in JOBS} == set(JOB_TARGETS), (
            "scripts/test_scope.py's JOBS and scripts/preflight.py's JOB_TARGETS disagree. "
            "A job in one and not the other is a CI check `make preflight` silently never runs."
        )

    def test_every_preflight_target_exists_in_the_makefile(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from preflight import JOB_TARGETS  # noqa: PLC0415

        text = MAKEFILE.read_text()
        for job, targets in JOB_TARGETS.items():
            for target in targets:
                assert re.search(rf"^{re.escape(target)}:", text, re.MULTILINE), (
                    f"preflight maps job {job!r} to `make {target}`, which the Makefile does not define"
                )

    def test_preflight_reports_what_it_skipped(self):
        """Never silently narrow — a run that says nothing reads as 'covered everything'."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "preflight.py"), "--base", "HEAD", "--list"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "[preflight]" in result.stdout

    def test_an_unreadable_scope_runs_everything(self, monkeypatch):
        sys.path.insert(0, str(ROOT / "scripts"))
        import preflight  # noqa: PLC0415

        class _Broken:
            returncode = 0
            stdout = "not json at all"

        monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: _Broken())
        jobs, note = preflight.decide("origin/main")
        assert all(jobs.values()), "a selector we cannot read must run every job, not none"
        assert note, "and it must say why"


class TestNoCommandTrustsLocalMain:
    """`git diff main...HEAD` in a worktree reviews somebody else's merged PRs."""

    STALE = re.compile(r"(?<!origin/)\bmain\.\.\.|git (diff|rebase|merge) main\b|\.\.\.main\b")

    @pytest.mark.parametrize("path", COMMANDS + AGENTS, ids=lambda p: p.name)
    def test_the_base_ref_is_always_remote(self, path: Path):
        offenders = [
            f"{path.relative_to(ROOT)}:{n}: {line.strip()}"
            for n, line in enumerate(path.read_text().splitlines(), 1)
            if self.STALE.search(line)
        ]
        assert not offenders, (
            "these reference local `main`, which in a worktree is routinely several commits behind "
            "origin/main. Use `origin/main`:\n" + "\n".join(offenders)
        )


class TestShipRunsTheGate:
    SHIP = ROOT / ".claude" / "commands" / "ship.md"

    def test_ship_names_the_gate(self):
        assert "make ship-gate" in self.SHIP.read_text()

    def test_ship_fetches_and_rebases_before_verifying(self):
        text = self.SHIP.read_text()
        assert "git fetch origin" in text, "/ship must fetch — it verified stale trees for months without one"
        assert "git rebase origin/main" in text
        # The gate has to come after the rebase, or it proves something about a
        # tree that will not exist after merge.
        assert text.index("git rebase origin/main") < text.index("make ship-gate")

    def test_the_review_is_backgrounded(self):
        text = self.SHIP.read_text()
        assert "BACKGROUND" in text or "in the background" in text.lower()

    def test_sync_main_carries_the_conflict_playbook(self):
        playbook = (ROOT / ".claude" / "commands" / "sync-main.md").read_text()
        for path in ("src/yeaboi/web/static", "uv.lock", "CURRENT_SCHEMA_VERSION", "changelog_data.json"):
            assert path in playbook, f"the rebase playbook says nothing about {path}"
        assert "make web" in playbook, "a conflicted bundle is rebuilt, never chosen"
