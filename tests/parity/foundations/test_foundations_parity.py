"""Freeze + parity tests for the W8 foundations gate.

Phase 1 pins the Python side and the corpus: every fixture's live dump must
equal its committed golden (the drift detector for anyone editing
``yeaboi/paths.py``), the goldens and fixtures must stay in one-to-one
correspondence, and the corpus must keep exercising the traps the spec
names. ``go/internal/home``'s golden test replays the same committed files
against the Go port, so a golden regenerated here re-gates Go automatically.

The subprocess-vs-binary diff arms in W8 phase 3, when ``cmd/yeaboi`` gains
``__dump-foundations``: set ``YEABOI_CLI_BIN`` (the ``make parity`` wiring
lands with it). Until then that test skips, the same way the RPC parity
suites skip without ``YEABOI_CORE_BIN``.

To regenerate after a deliberate behaviour change:
``uv run python -m tests.parity.foundations.regen``.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tests.parity.foundations import dump as dump_mod
from tests.parity.foundations import matrix

CLI_BINARY = os.environ.get("YEABOI_CLI_BIN")

needs_cli_binary = pytest.mark.skipif(
    not CLI_BINARY or not os.path.isfile(CLI_BINARY or ""),
    reason="yeaboi CLI binary not available (arrives in W8 phase 3; run `make parity`)",
)


@pytest.mark.parametrize("fixture", matrix.FIXTURES, ids=lambda f: f.name)
def test_dump_matches_committed_golden(fixture, tmp_path):
    """The Python side is frozen: a paths.py behaviour change must regenerate
    the goldens deliberately, never drift silently."""
    golden_file = matrix.golden_path(fixture)
    assert golden_file.exists(), f"missing golden {golden_file} — run `uv run python -m tests.parity.foundations.regen`"
    expected = json.loads(golden_file.read_text(encoding="utf-8"))
    got = matrix.golden_for(fixture, tmp_path / "sandbox")
    assert got == expected, (
        f"fixture {fixture.name}: live dump disagrees with the committed golden — if the "
        "paths.py change is deliberate, regenerate (and mirror go/internal/home first)"
    )


class TestCorpusSelfGuards:
    """Pure-Python guards that the corpus still exercises what this gate
    claims — they run in the ordinary suite, binary or not."""

    def test_fixture_names_are_unique(self):
        names = [f.name for f in matrix.FIXTURES]
        assert len(names) == len(set(names))

    def test_goldens_and_fixtures_correspond_one_to_one(self):
        committed = {p.stem for p in matrix.GOLDENS_DIR.glob("*.json")}
        assert committed == {f.name for f in matrix.FIXTURES}, (
            "goldens and fixtures diverged — regenerate (stale files must be deleted, new fixtures must be dumped)"
        )

    def test_fixtures_still_exercise_the_env_traps(self):
        values = [v for f in matrix.FIXTURES for v in f.env.values()]
        assert any(v.startswith("~") for v in values), "tilde expansion left the corpus"
        assert any("\u00a0" in v for v in values), "unicode-whitespace strip left the corpus"
        assert any("//" in v for v in values), "slash collapsing left the corpus"
        assert any(".." in v for v in values), "lexical '..' preservation left the corpus"
        assert any(not v.startswith(("~", "/", "{tmp}")) and v.strip() for v in values), (
            "the relative-root fixture left the corpus"
        )
        assert any("HOME" in f.env for f in matrix.FIXTURES), "the Path.home() normalisation fixtures left the corpus"

    def test_safe_key_vectors_still_exercise_the_traps(self):
        vectors = dump_mod.SAFE_KEY_VECTORS
        assert any("İ" in v for v in vectors), "the Turkish-İ lower() trap left the corpus"
        assert any("\\" in v for v in vectors), "backslash normalisation left the corpus"
        assert any(".." in v for v in vectors), "traversal filtering left the corpus"
        assert any("\u00a0" in v for v in vectors), "unicode strip() left the corpus"
        assert "" in vectors, "the fallback vector left the corpus"

    def test_the_dump_covers_every_public_paths_helper(self):
        """Discovery, not a list to hand-maintain: a new get_* helper in
        paths.py must join the dump (or this fails naming it)."""
        import yeaboi.paths as paths

        public = {name for name in dir(paths) if name.startswith("get_") and callable(getattr(paths, name))}
        dumped = set(dump_mod.ZERO_ARG_HELPERS) | set(dump_mod.KEYED_HELPERS)
        assert public == dumped, (
            "paths.py helpers and the foundations dump diverged — add the new getter to "
            f"dump.py (or drop the stale one): {sorted(public ^ dumped)}"
        )

    def test_the_dump_covers_every_paths_constant(self):
        from pathlib import PurePath

        import yeaboi.paths as paths

        module_constants = {
            name for name in dir(paths) if name.isupper() and isinstance(getattr(paths, name), PurePath)
        }
        assert module_constants == set(dump_mod.CONSTANT_NAMES), (
            "paths.py constants and the foundations dump diverged: "
            f"{sorted(module_constants ^ set(dump_mod.CONSTANT_NAMES))}"
        )


@needs_cli_binary
@pytest.mark.parametrize("fixture", matrix.FIXTURES, ids=lambda f: f.name)
def test_go_binary_matches_python_dump(fixture, tmp_path):
    """The W8 gate proper: `yeaboi __dump-foundations` under an identical
    environment must reproduce the Python dump byte-for-byte (each side in
    its own sandbox, each normalised against its own)."""
    py_tmp = tmp_path / "py"
    go_tmp = tmp_path / "go"
    py = matrix.normalize(matrix.run_dump(fixture, py_tmp), py_tmp)
    go_tmp.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        [CLI_BINARY, "__dump-foundations"],
        cwd=go_tmp,
        env=matrix.launch_env(fixture, go_tmp),
        capture_output=True,
        text=True,
        check=True,
    )
    go = matrix.normalize(json.loads(out.stdout), go_tmp)
    assert go == py, f"fixture {fixture.name}: Go and Python foundations dumps disagree"
