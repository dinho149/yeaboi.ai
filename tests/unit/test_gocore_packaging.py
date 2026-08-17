"""Constants the Go sidecar must keep in lockstep with the Python side.

Two couplings, neither compared at build or run time. Versioning: three
parties name the sidecar's version or its wheel — ``binaryVersion`` in the Go
entrypoint, the packaging pyproject under ``packaging/yeaboi-core/``, and the
``core`` extra's range in the root pyproject — and drift ships a wheel whose
version lies about the binary inside it. Schema: the Go store's
``currentSchemaVersion`` ceiling must equal ``sessions.CURRENT_SCHEMA_VERSION``
or the sidecar refuses every upgraded database (see ``TestSchemaGuardLockstep``).
Both drifts fail the unit suite here instead.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _core_wheel_version() -> str:
    pyproject = tomllib.loads((REPO / "packaging" / "yeaboi-core" / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["project"]["version"]


class TestCoreVersionLockstep:
    def test_go_binary_version_matches_the_wheel_version(self):
        main_go = (REPO / "go" / "cmd" / "yeaboi-core" / "main.go").read_text(encoding="utf-8")
        match = re.search(r'const binaryVersion = "([^"]+)"', main_go)
        assert match, "binaryVersion const not found in go/cmd/yeaboi-core/main.go"
        assert match.group(1) == _core_wheel_version(), (
            "go/cmd/yeaboi-core/main.go binaryVersion and packaging/yeaboi-core/pyproject.toml version "
            "must be bumped together — the wheel's version claims to describe the binary inside it"
        )

    def test_core_extra_range_covers_the_current_wheel(self):
        """The ``yeaboi[core]`` extra must resolve to the wheel this repo builds."""
        from packaging.requirements import Requirement
        from packaging.version import Version

        root = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        (raw,) = root["project"]["optional-dependencies"]["core"]
        requirement = Requirement(raw)
        assert requirement.name == "yeaboi-core"
        assert Version(_core_wheel_version()) in requirement.specifier, (
            f"the core extra ({raw!r}) excludes the wheel version this repo builds "
            f"({_core_wheel_version()}) — widen the range or bump it with the contract line"
        )

    def test_wheel_targets_include_every_tier_one_platform(self):
        """The release workflow loops WHEEL_TAGS; a dropped key silently stops shipping a platform."""
        # Parsed with ast, not imported: hatch_build.py imports hatchling, which
        # is a build-backend dependency and not installed in the test venv.
        import ast

        tree = ast.parse((REPO / "packaging" / "yeaboi-core" / "hatch_build.py").read_text(encoding="utf-8"))
        tags = next(
            ast.literal_eval(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "WHEEL_TAGS" for t in node.targets)
        )
        assert set(tags) == {"linux/amd64", "linux/arm64", "darwin/amd64", "darwin/arm64", "windows/amd64"}


class TestMethodSetLockstep:
    """The RPC method set exists in three hand-maintained copies.

    ``var methods`` in the Go entrypoint, the ``core.hello`` line in
    ``contracts/v1/rpc.md``, and the per-method schema files — nothing at
    build or run time compares them, and every new method edits all three by
    hand. (``core.hello`` itself has no schema file; ``progress.json`` is not
    a method.)
    """

    def _main_go_methods(self) -> set[str]:
        main_go = (REPO / "go" / "cmd" / "yeaboi-core" / "main.go").read_text(encoding="utf-8")
        match = re.search(r"var methods = \[\]string\{(.*?)\}", main_go, re.DOTALL)
        assert match, "var methods block not found in go/cmd/yeaboi-core/main.go"
        return set(re.findall(r'"([^"]+)"', match.group(1)))

    def test_rpc_md_hello_line_matches_main_go(self):
        rpc_md = (REPO / "contracts" / "v1" / "rpc.md").read_text(encoding="utf-8")
        match = re.search(r'"methods": \[([^\]]+)\]', rpc_md)
        assert match, "core.hello methods list not found in contracts/v1/rpc.md"
        documented = set(re.findall(r'"([^"]+)"', match.group(1)))
        assert documented == self._main_go_methods(), (
            "contracts/v1/rpc.md's core.hello line and main.go's methods list disagree — "
            "a method was added or removed in one copy only"
        )

    def test_every_method_has_a_schema_and_every_schema_a_method(self):
        schemas = {
            path.name.removesuffix(".json")
            for path in (REPO / "contracts" / "v1").glob("*.json")
            if path.name != "progress.json"
        }
        assert schemas == self._main_go_methods(), (
            "contracts/v1/*.json and main.go's methods list disagree — every method needs a "
            "schema file named after it (progress.json excepted), and every schema a method"
        )


class TestAnalysisPrivacyInvariant:
    """The Go analysis package must never log.

    Page BODIES and item titles/bodies cross the wire as analysis.* params,
    and the privacy invariant says input content never reaches a log line.
    The package upholds it by importing no logging facility at all — a
    convention every file header states but nothing enforced until here.
    """

    def test_go_analysis_package_imports_no_logger(self):
        for path in sorted((REPO / "go" / "internal" / "analysis").glob("*.go")):
            imports = re.findall(r'^\s*(?:import\s+)?(?:\w+\s+|\.\s+|_\s+)?"([^"]+)"', path.read_text("utf-8"), re.M)
            offenders = [name for name in imports if name == "log" or name.startswith("log/")]
            assert not offenders, (
                f"{path.name} imports {offenders} — the analysis package receives page bodies as "
                "params and must not log; counts and rule labels only, and never in error strings"
            )


class TestExportsPrivacyInvariant:
    """The Go exports package must never log.

    Card text, ticket summaries, voter names and duel transcripts cross the
    wire as retro.build_export / poker.build_export params, and the privacy
    invariant (rpc.md rule 13) says input content never reaches a log line —
    Python's ``safe_url`` warning on a dropped URL is an accepted
    Python-only deviation, so the Go side must drop silently. The package
    upholds it by importing no logging facility at all.
    """

    def test_go_exports_package_imports_no_logger(self):
        for path in sorted((REPO / "go" / "internal" / "exports").glob("*.go")):
            imports = re.findall(r'^\s*(?:import\s+)?(?:\w+\s+|\.\s+|_\s+)?"([^"]+)"', path.read_text("utf-8"), re.M)
            offenders = [name for name in imports if name == "log" or name.startswith("log/")]
            assert not offenders, (
                f"{path.name} imports {offenders} — the exports package receives document text as "
                "params and must not log; fixed messages only, and never input content in an error"
            )


class TestSchemaGuardLockstep:
    """The Go schema guard's ceiling must track ``sessions.CURRENT_SCHEMA_VERSION``.

    Nothing else can catch this drift. The guard only fires on a database that
    has a ``schema_info`` table, and the parity fixtures build their databases
    through ``AgentWatchStore(db_path)``, which runs the agentwatch DDL alone
    and never writes that table — so Go falls back to ``PRAGMA user_version``,
    reads 0, and passes. Meanwhile every *real* ``sessions.db`` carries the
    Python version, so a bump to 28 against a Go constant still at 27 refuses
    the database, returns 1001, and drops the whole agentwatch family back to
    the Python path: the cold scan silently returns to 15-30s, the wheel still
    installs, and CI is entirely green.
    """

    def test_go_schema_ceiling_matches_python(self):
        from yeaboi.sessions import CURRENT_SCHEMA_VERSION

        store_go = (REPO / "go" / "internal" / "agentwatch" / "store.go").read_text(encoding="utf-8")
        match = re.search(r"const currentSchemaVersion = (\d+)", store_go)
        assert match, "currentSchemaVersion const not found in go/internal/agentwatch/store.go"
        assert int(match.group(1)) == CURRENT_SCHEMA_VERSION, (
            f"go/internal/agentwatch/store.go pins currentSchemaVersion = {match.group(1)} but "
            f"sessions.CURRENT_SCHEMA_VERSION is {CURRENT_SCHEMA_VERSION}. Bump the Go constant "
            "after mirroring whatever the new migration changed in the agentwatch tables — or, if "
            "the migration is one the sidecar genuinely must not write behind, leave it and say so "
            "here, because the sidecar will refuse every upgraded database until it is raised."
        )
