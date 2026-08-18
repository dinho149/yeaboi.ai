"""Freeze + parity tests for the W8 foundations gate.

Phase 1 pins the Python side and the corpus: every fixture's live dump must
equal its committed golden (the drift detector for anyone editing
``yeaboi/paths.py``), the goldens and fixtures must stay in one-to-one
correspondence, and the corpus must keep exercising the traps the spec
names. ``go/internal/home``'s golden test replays the same committed files
against the Go port, so a golden regenerated here re-gates Go automatically.

W8 phase 3 armed the subprocess-vs-binary arms: ``cmd/yeaboi`` serves
``__dump-foundations`` and ``__dump-args``, and ``make parity`` builds it
and exports ``YEABOI_CLI_BIN``. Without the binary those tests skip, the
same way the RPC parity suites skip without ``YEABOI_CORE_BIN``.

Phase 3 also added the argv gate: every ``argvectors.VECTORS`` argv runs
through ``cli.build_parser()`` (``argdump.py``), the outcomes freeze into
``tests/parity/goldens/cli/args.json``, and both the Go parse tree
(``go/cmd/yeaboi/args_golden_test.go``) and the binary replay them.

To regenerate after a deliberate behaviour change:
``uv run python -m tests.parity.foundations.regen``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.parity.foundations import argdump, argvectors, matrix
from tests.parity.foundations import dump as dump_mod

CLI_BINARY = os.environ.get("YEABOI_CLI_BIN")

needs_cli_binary = pytest.mark.skipif(
    not CLI_BINARY or not os.path.isfile(CLI_BINARY or ""),
    reason="yeaboi CLI binary not available (run `make parity`)",
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


class TestConfigCorpusSelfGuards:
    """W8 phase 2: the config surface's own discovery and trap guards."""

    def test_the_dump_covers_every_config_getter(self):
        """Discovery, not a list to hand-maintain: every public zero-arg
        callable in config.py must join the dump (or the exempt table, with
        a reason); key-taking getters must join CONFIG_KEYED_GETTERS.
        Setters take arguments and fall outside both by construction."""
        import inspect

        import yeaboi.config as config

        zero_arg, keyed = set(), set()
        for name in dir(config):
            if name.startswith("_"):
                continue
            fn = getattr(config, name)
            if not inspect.isfunction(fn) or inspect.getmodule(fn) is not config:
                continue
            params = [
                p
                for p in inspect.signature(fn).parameters.values()
                if p.default is inspect.Parameter.empty and p.kind is not inspect.Parameter.KEYWORD_ONLY
            ]
            if not params:
                zero_arg.add(name)
            elif name.startswith(("is_", "get_")) and len(params) == 1:
                keyed.add(name)

        dumped = set(dump_mod.CONFIG_GETTERS) | set(dump_mod.CONFIG_DUMP_EXEMPT)
        assert zero_arg == dumped, (
            "config.py getters and the foundations dump diverged — add the new getter to "
            f"dump.CONFIG_GETTERS (or CONFIG_DUMP_EXEMPT, with a reason): {sorted(zero_arg ^ dumped)}"
        )
        assert keyed == set(dump_mod.CONFIG_KEYED_GETTERS), (
            f"key-taking config getters diverged from CONFIG_KEYED_GETTERS: "
            f"{sorted(keyed ^ set(dump_mod.CONFIG_KEYED_GETTERS))}"
        )

    def test_sanitize_list_covers_every_env_read(self):
        """matrix.CONFIG_ENV_VARS is what keeps a developer's real
        credentials out of regenerated goldens — scan config.py's source so
        a new os.getenv read cannot land without joining it."""
        import inspect
        import re

        import yeaboi.config as config

        src = inspect.getsource(config)
        read = set()
        # Direct os.getenv reads, plus the ones routed through the
        # name-taking helpers — by string literal or module-level constant.
        pattern = r'(?:os\.(?:getenv|environ\.get)|_csv_config|_env_truthy)\(\s*([A-Za-z_]\w*|"[^"]+")'
        for match in re.finditer(pattern, src):
            token = match.group(1)
            if token.startswith('"'):
                read.add(token.strip('"'))
            else:
                value = getattr(config, token, None)
                if isinstance(value, str):
                    read.add(value)
        read.update(config._PROXY_ENV_VARS)

        sanitized = set(matrix.CONFIG_ENV_VARS) | {"YEABOI_HOME", "HOME"}  # popped separately by launch_env
        missing = read - sanitized
        assert not missing, f"config.py reads env vars launch_env does not sanitize: {sorted(missing)}"
        stale = set(matrix.CONFIG_ENV_VARS) - read - {"PYTHON_DOTENV_DISABLED"}
        assert not stale, f"CONFIG_ENV_VARS names vars config.py no longer reads: {sorted(stale)}"

    def test_fixtures_still_exercise_the_config_traps(self):
        values: dict[str, str] = {}
        for fixture in matrix.FIXTURES:
            values.update(fixture.env)
        # The two truthy conventions, kept distinct on purpose.
        assert values.get("BETA_NOTICES_ENABLED") not in (None, "false"), (
            "the opt-out-gate trap (a non-'false' value that still means on) left the corpus"
        )
        assert any(
            v.strip().lower() in ("1", "true", "yes", "on") for k, v in values.items() if "TEAM_ANALYSIS" in k
        ), "the opt-in truthy convention left the corpus"
        # Clamps: out of range on both sides, plus the two int() parse traps.
        assert any(v.strip().lstrip("-").isdigit() and v.strip().startswith("-") for v in values.values()), (
            "the below-minimum clamp vector left the corpus"
        )
        assert "5.0" in values.values(), 'the int("5.0")-raises trap left the corpus'
        assert any(v != v.strip() and v.strip().isdigit() for v in values.values()), (
            "the int-with-whitespace tolerance trap left the corpus"
        )
        # CSV dedup vs the recipient list that keeps duplicates.
        csv_fixture = {f.name: f for f in matrix.FIXTURES}["config-csv-and-lists"]
        assert "STANDUP_EMAIL_RECIPIENTS" in csv_fixture.env and "YEABOI_ALLOWED_PATHS" in csv_fixture.env

    def test_fixture_files_still_exercise_the_dotenv_and_aws_traps(self):
        entries = [(rel, text) for f in matrix.FIXTURES for rel, text in f.files.items()]
        project_envs = [text for rel, text in entries if rel == ".env"]
        assert project_envs, "the project-.env fixture left the corpus"
        joined = "\n".join(project_envs)
        assert "${" in joined, "interpolation left the dotenv corpus"
        assert ":-" in joined, "the ${VAR:-default} form left the dotenv corpus"
        assert "export " in joined, "the export prefix left the dotenv corpus"
        assert "\\'" in joined, "the escaped-quote trap left the dotenv corpus"
        assert "\r\n" in joined, "the CRLF trap left the dotenv corpus"
        assert any(line.startswith("=") for line in joined.splitlines()), (
            "the unparseable-line trap left the dotenv corpus"
        )
        assert any(rel == "home/.yeaboi/.env" for rel, _ in entries), "the user-.env precedence fixture left the corpus"
        aws = [text for rel, text in entries if rel == "home/.aws/config"]
        assert any("role_arn" in text for text in aws), "the AWS autodetect fixture left the corpus"
        assert any(text.count("[profile dup]") == 2 for text in aws), (
            "the unparseable-AWS-config fixture left the corpus"
        )

    def test_set_key_scenarios_still_exercise_the_writer_traps(self):
        scenarios = {s["name"]: s for s in dump_mod.SET_KEY_SCENARIOS}
        assert any(s["initial"] is None for s in scenarios.values()), "the create-missing-file scenario left"
        assert any(s["initial"] is not None and not s["initial"].endswith("\n") for s in scenarios.values()), (
            "the newline-less-tail append scenario left"
        )
        ops = [value for s in scenarios.values() for _, value in s["ops"]]
        assert any("'" in value for value in ops), "the quote-escaping scenario left"
        assert any("\n" in value for value in ops), "the multiline-value scenario left"
        assert "" in ops, "the empty-value scenario left"
        assert any(s["initial"] and "export " in s["initial"] for s in scenarios.values()), (
            "the export-line rewrite scenario left"
        )


def test_args_golden_matches_live():
    """The Python side of the argv gate is frozen: a build_parser() change
    must regenerate the golden deliberately, never drift silently."""
    assert argdump.GOLDEN_PATH.exists(), (
        f"missing golden {argdump.GOLDEN_PATH} — run `uv run python -m tests.parity.foundations.regen`"
    )
    expected = json.loads(argdump.GOLDEN_PATH.read_text(encoding="utf-8"))
    got = {"vectors": argdump.build_results()}
    assert got == expected, (
        "live argparse outcomes disagree with the committed golden — if the cli.py change is "
        "deliberate, regenerate (and mirror go/cmd/yeaboi/parser.go first)"
    )


class TestArgVectorSelfGuards:
    """The corpus must keep exercising the traps the W8 spec names."""

    def test_vector_names_are_unique(self):
        names = [name for name, _ in argvectors.VECTORS]
        assert len(names) == len(set(names))

    def test_no_help_or_version_vectors(self):
        """Their outputs belong to the phase-4 help goldens (and --version
        embeds the product version, which would rot this golden)."""
        for name, argv in argvectors.VECTORS:
            assert not set(argv) & {"-h", "--help", "--version"}, name

    def test_vectors_still_exercise_the_argparse_traps(self):
        results = [entry["result"] for entry in argdump.build_results()]
        messages = [r["message"] for r in results if r["status"] == "error"]
        args_dumps = [r["args"] for r in results if r["status"] == "ok"]
        assert any(m.startswith("ambiguous option: --export") for m in messages), (
            "the --export abbreviation collision left the corpus"
        )
        assert any("invalid choice:" in m and "choose from 1, 2, 3, 4" in m for m in messages), (
            "the int-choices trap left the corpus"
        )
        assert any("invalid int value:" in m for m in messages), "the int() rejection trap left the corpus"
        assert any("invalid float value:" in m for m in messages), "the float() rejection trap left the corpus"
        assert any("expected at least one argument" in m for m in messages), 'the empty nargs="+" trap left'
        assert any("ignored explicit argument" in m for m in messages), "the --flag=value trap left the corpus"
        assert any(m.startswith("the following arguments are required:") for m in messages), (
            "the required-arguments trap left the corpus"
        )
        assert any(m.startswith("unrecognized arguments:") for m in messages), "the extras trap left the corpus"
        assert any(a.get("resume") == "__pick__" for a in args_dumps), 'the nargs="?" const trap left the corpus'
        assert any(a.get("team_size") == 5 for a in args_dumps), "the int-whitespace tolerance trap left the corpus"
        assert any(
            a.get("review_transcripts") is False or a.get("include_local_sessions") is False for a in args_dumps
        ), "the store_false dest inversion left the corpus"
        assert any("--" in argv for _, argv in argvectors.VECTORS), "the -- separator left the corpus"
        assert any(any("=" in tok for tok in argv) for _, argv in argvectors.VECTORS), (
            "the --opt=value form left the corpus"
        )

    def test_error_vectors_pin_the_erroring_parser(self):
        """Sub-parser errors carry the sub prog — the difference between
        `yeaboi: error:` and `yeaboi perf prep: error:` is contractual."""
        progs = {r["prog"] for r in (e["result"] for e in argdump.build_results()) if r["status"] == "error"}
        assert any(p != "yeaboi" for p in progs), "every error vector collapsed to the top-level parser"
        assert "yeaboi" in progs, "no error vector exercises the top-level parser"


@needs_cli_binary
@pytest.mark.parametrize("entry", argdump.build_results() if CLI_BINARY else [], ids=lambda e: e["name"])
def test_go_binary_matches_python_argdump(entry):
    """The argv gate's subprocess arm: `yeaboi __dump-args ARGS...` must
    reproduce the Python outcome for every vector."""
    out = subprocess.run(
        [CLI_BINARY, "__dump-args", *entry["argv"]],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.stdout) == entry["result"], f"vector {entry['name']}: Go and Python outcomes disagree"


@needs_cli_binary
@pytest.mark.parametrize("fixture", matrix.FIXTURES, ids=lambda f: f.name)
def test_go_binary_matches_python_dump(fixture, tmp_path):
    """The W8 gate proper: `yeaboi __dump-foundations` under an identical
    environment must reproduce the Python dump byte-for-byte (each side in
    its own sandbox, each normalised against its own)."""
    py_tmp = tmp_path / "py"
    go_tmp = tmp_path / "go"
    py = matrix.normalize(matrix.run_dump(fixture, py_tmp), py_tmp)
    # Materialise the Go sandbox exactly the way run_dump does the Python
    # one: realized HOME pre-created, fixture files written.
    go_tmp.mkdir(parents=True, exist_ok=True)
    env = matrix.launch_env(fixture, go_tmp)
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    matrix.write_files(fixture, go_tmp)
    out = subprocess.run(
        [CLI_BINARY, "__dump-foundations"],
        cwd=go_tmp,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    go = matrix.normalize(json.loads(out.stdout), go_tmp)
    assert go == py, f"fixture {fixture.name}: Go and Python foundations dumps disagree"
