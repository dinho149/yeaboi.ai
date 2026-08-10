"""The yeaboi-core platform wheel stays in lockstep with the Go sidecar.

Three parties name the sidecar's version or its wheel, and nothing at build or
run time compares them: ``binaryVersion`` in the Go entrypoint, the packaging
pyproject under ``packaging/yeaboi-core/``, and the ``core`` extra's range in
the root pyproject. Drift ships a wheel whose version lies about the binary
inside it — so the drift fails the unit suite instead.
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
