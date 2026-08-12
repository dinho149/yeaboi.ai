"""The registry that decides which tests CI runs must never lose a test.

`scripts/test_scope.py` is a test *selector*, and a selector's failure mode is
silence: it drops a file, the file stops running, and every check stays green.
Nothing else in the repo would notice — that is the whole reason the always-run
set exists, and it is the reason this file is stricter than it looks.

Two-way totality is the load-bearing assertion, in the style
`test_surface_parity.py` established. Every source file must be claimed by an
area or by the global set; every test file must be reachable from some area or
from `ALWAYS`. A new module with no charter, a renamed test, a glob that has
quietly stopped matching — all three fail here, in the run that introduces them,
rather than by a regression shipping months later.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Loaded by path: scripts/ is not a package, and the module must be importable
# without installing anything (CI runs it before `uv sync`).
_spec = importlib.util.spec_from_file_location("test_scope", ROOT / "scripts" / "test_scope.py")
assert _spec and _spec.loader
scope_mod = importlib.util.module_from_spec(_spec)
sys.modules["test_scope"] = scope_mod
_spec.loader.exec_module(scope_mod)


def _rel(paths):
    return sorted(str(p.relative_to(ROOT)) for p in paths)


def source_files() -> list[str]:
    return _rel(ROOT.glob("src/yeaboi/**/*.py"))


def collected_test_files() -> list[str]:
    found = list(ROOT.glob("tests/unit/**/*.py")) + list(ROOT.glob("tests/test_*.py"))
    return [p for p in _rel(found) if "__snapshots__" not in p and not p.endswith("__init__.py")]


class TestTotality:
    """Both directions, because a selector can fail by omission at either end."""

    @pytest.mark.parametrize("path", source_files())
    def test_every_source_file_is_claimed(self, path):
        claimed = scope_mod.area_for(path) is not None or scope_mod._matches(path, scope_mod.GLOBAL)
        assert claimed, (
            f"{path} is claimed by no area and is not global — add it to an Area's `src` in "
            "scripts/test_scope.py (matching its cowork/workstreams/*.md charter), or to GLOBAL "
            "if a change to it can reach everything"
        )

    @pytest.mark.parametrize("path", collected_test_files())
    def test_every_test_file_is_reachable(self, path):
        reachable = scope_mod._matches(path, scope_mod.ALWAYS) or any(
            scope_mod._matches(path, area.tests) for area in scope_mod.AREAS
        )
        assert reachable, (
            f"{path} is selected by nothing — a scoped CI run would never execute it. Add it to an "
            "Area's `tests` in scripts/test_scope.py, or to ALWAYS if it guards the repo rather "
            "than a module"
        )

    def test_the_areas_are_the_workstreams(self):
        """One vocabulary for the fleet and for CI, so neither drifts alone."""
        charters = {p.stem for p in (ROOT / "cowork" / "workstreams").glob("*.md")}
        assert {area.name for area in scope_mod.AREAS} == charters


class TestTheGlobsStillMatch:
    """A pattern that matches nothing is exactly as good as no pattern at all,
    and it looks identical in a diff."""

    @pytest.mark.parametrize("pattern", scope_mod.ALWAYS)
    def test_every_always_glob_matches_something(self, pattern):
        assert list(ROOT.glob(pattern)), f"ALWAYS pattern {pattern!r} matches no file"

    @pytest.mark.parametrize(
        "name,pattern",
        [(area.name, pattern) for area in scope_mod.AREAS for pattern in area.tests],
    )
    def test_every_area_glob_matches_something(self, name, pattern):
        assert list(ROOT.glob(pattern)), f"{name}: test pattern {pattern!r} matches no file"

    @pytest.mark.parametrize("path", scope_mod.GLOBAL)
    def test_every_global_path_exists(self, path):
        target = ROOT / path.rstrip("/")
        assert target.exists(), f"GLOBAL entry {path!r} does not exist — a rename left it dead"

    @pytest.mark.parametrize("prefix", [t for job in scope_mod.JOBS for t in job.triggers])
    def test_every_job_trigger_exists(self, prefix):
        target = ROOT / prefix.rstrip("/")
        assert target.exists(), f"job trigger {prefix!r} does not exist — a rename left it dead"


class TestFailingSafe:
    """Every path that cannot be classified must widen the run, never narrow it."""

    def test_an_unknown_path_runs_everything(self):
        assert scope_mod.resolve(["some/new/thing.txt"]).full is True

    def test_an_empty_diff_runs_everything(self):
        """An empty list is far more often a diff we failed to read than a change
        that touched nothing, and the two are indistinguishable here."""
        assert scope_mod.resolve([]).full is True

    def test_a_global_path_runs_everything(self):
        assert scope_mod.resolve(["tests/conftest.py"]).full is True
        assert scope_mod.resolve(["src/yeaboi/sessions.py"]).full is True

    def test_a_new_unclaimed_test_file_runs_everything(self):
        assert scope_mod.resolve(["tests/unit/test_brand_new_thing.py"]).full is True

    def test_the_full_scope_names_every_job(self):
        jobs = scope_mod.resolve(["some/new/thing.txt"])
        for job in scope_mod.JOBS:
            assert jobs.full or job.name in jobs.jobs


class TestTheAlwaysSetIsAlwaysThere:
    def test_a_narrow_change_still_runs_the_guards(self):
        selected = set(scope_mod.unit_paths(scope_mod.resolve(["src/yeaboi/standup/engine.py"])))
        assert "tests/unit/test_surface_parity.py" in selected
        assert "tests/unit/test_web_assets.py" in selected

    def test_a_prose_only_change_still_runs_the_guards(self):
        """`cowork/` and `.claude/` are treated as inert, which is only safe
        because the tests that read them are in ALWAYS. If they ever leave it,
        those prefixes have to move to GLOBAL in the same commit."""
        scope = scope_mod.resolve(["cowork/workstreams/standup.md"])
        selected = set(scope_mod.unit_paths(scope))
        assert not scope.full
        assert any("test_cowork" in path for path in selected)

    def test_it_does_not_drag_in_an_unrelated_area(self):
        selected = set(scope_mod.unit_paths(scope_mod.resolve(["src/yeaboi/standup/engine.py"])))
        assert not any("test_poker" in path for path in selected)
        assert not any("test_reporting" in path for path in selected)


class TestTheGoParityTriggers:
    """CLAUDE.md's dual-maintenance rule, as a selector.

    The Go twins have no import edge to their Python originals, so nothing about
    a changed Python file implies the parity suite. Miss one of these and the
    sidecar silently diverges with CI fully green — the exact failure the
    byte-parity gate exists to stop.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "src/yeaboi/agentwatch/collector.py",
            "src/yeaboi/standup/relatedness.py",
            "src/yeaboi/standup/confidence.py",
            "src/yeaboi/analysis/code_health.py",
            "src/yeaboi/analysis/ai_usage.py",
            "go/internal/standup/relatedness.go",
            "src/yeaboi/gocore/client.py",
        ],
    )
    def test_a_mirrored_change_runs_go_and_parity(self, path):
        scope = scope_mod.resolve([path])
        assert scope.full or {"go", "parity"} <= scope.jobs, f"{path} would skip the parity gate"

    def test_the_parity_fixture_source_counts_as_mirrored(self):
        """Every literal in test_code_health.py is a parity fixture, so editing
        it changes what the Go side is checked against."""
        scope = scope_mod.resolve(["tests/unit/test_code_health.py"])
        assert scope.full or {"go", "parity"} <= scope.jobs


class TestSelectionIsRunnable:
    """Whatever comes out has to be something pytest will accept.

    A glob that resolves to nothing makes pytest exit 4 — "file or directory not
    found" — which reads in CI as a broken job rather than as an empty selection.
    """

    @pytest.mark.parametrize(
        "changed",
        [
            ["src/yeaboi/standup/engine.py"],
            ["src/yeaboi/poker/board.py"],
            ["docs/index.html"],
            ["frontend/src/retro/App.tsx"],
            ["src/yeaboi/ui/mode_select/screens/_screens.py"],
        ],
    )
    def test_every_selected_path_exists(self, changed):
        for path in scope_mod.unit_paths(scope_mod.resolve(changed)):
            assert (ROOT / path).exists(), f"{path} was selected but does not exist"

    def test_a_scoped_selection_is_a_subset_of_the_full_one(self):
        scoped = set(scope_mod.unit_paths(scope_mod.resolve(["src/yeaboi/poker/board.py"])))
        every = set(collected_test_files())
        assert scoped <= every, sorted(scoped - every)

    def test_the_slow_lane_is_all_or_nothing(self):
        """572 tests and about 100s — cheap enough that splitting it would buy
        less than the risk of getting the split wrong."""
        assert scope_mod.slow_paths(scope_mod.resolve(["src/yeaboi/poker/board.py"])) == list(scope_mod.FULL_SLOW)
