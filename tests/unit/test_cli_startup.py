"""Startup-latency guard for the CLI entry point (src/yeaboi/cli.py).

``yeaboi --version``/``--help`` — and the fixed overhead of every subcommand —
pay for everything cli.py imports at module level before argparse even runs.
Deferring the heavy edges (rich, prompt_toolkit, the langchain/anthropic stack)
took cold start from ~0.65s to ~0.04s; these tests keep it that way. Same
rationale as test_beta.py: an eager import added here has no visible symptom
other than latency, which is exactly the kind of regression that survives
review.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from yeaboi import cli

# Module-level imports cli.py is allowed. Everything else must be imported at
# its call site. Extend deliberately — each addition slows every yeaboi
# invocation, including --version and the headless/scheduler flows.
ALLOWED_MODULE_IMPORTS = {
    "__future__",
    "argparse",
    "logging",
    "os",
    "re",
    "sys",
    "pathlib",
    "typing",
    "yeaboi",
    "yeaboi.beta",
    "yeaboi.config",
    "yeaboi.fs_policy",
    "yeaboi.paths",
    # TYPE_CHECKING-only (never imported at runtime; asserted below).
    "rich.console",
    "rich.panel",
    "rich.table",
}

# Names that must never be in sys.modules after ``import yeaboi.cli``.
HEAVY_MODULES = ("anthropic", "langchain_core", "langgraph", "rich", "prompt_toolkit")


def _module_level_imports(tree: ast.Module) -> list[tuple[str, bool]]:
    """Yield (module_name, is_type_checking_only) for every top-level import.

    Only walks module-level statements and ``if TYPE_CHECKING:`` blocks —
    function-level imports are the convention this test exists to protect.
    """
    found: list[tuple[str, bool]] = []

    def collect(stmts: list[ast.stmt], type_checking: bool) -> None:
        for node in stmts:
            if isinstance(node, ast.Import):
                found.extend((alias.name, type_checking) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.append((node.module or "", type_checking))
            elif isinstance(node, ast.If):
                # `if TYPE_CHECKING:` — imports inside are free at runtime.
                test = node.test
                is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                    isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
                )
                collect(node.body, type_checking or is_tc)
                collect(node.orelse, type_checking)

    collect(tree.body, type_checking=False)
    return found


class TestModuleImportAllowlist:
    def test_module_level_imports_stay_on_the_allowlist(self):
        source = Path(cli.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders = [
            name
            for name, type_checking in _module_level_imports(tree)
            if name not in ALLOWED_MODULE_IMPORTS and not type_checking
        ]
        assert offenders == [], (
            f"cli.py gained module-level imports outside the startup allowlist: {sorted(set(offenders))}. "
            "Import them inside the function that uses them instead (see the module docstring), "
            "or — only for genuinely cheap modules — extend ALLOWED_MODULE_IMPORTS."
        )

    def test_heavy_stacks_are_not_even_type_checking_imported_elsewhere(self):
        # The allowlist admits rich.* under TYPE_CHECKING for annotations; the
        # LLM stack has no business in cli.py's namespace at all.
        source = Path(cli.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        llm_stack = [
            name
            for name, type_checking in _module_level_imports(tree)
            if not type_checking and name.split(".")[0] in {"anthropic", "langchain_core", "langgraph", "langchain"}
        ]
        assert llm_stack == []


class TestNoRuntimeLeak:
    @pytest.mark.slow
    def test_importing_cli_pulls_none_of_the_heavy_modules(self):
        """Subprocess check — catches transitive regressions the AST can't.

        E.g. a new import of a light-looking yeaboi module that itself imports
        langchain would pass the allowlist test and still cost 200ms; this one
        fails on the sys.modules evidence. A module-set assertion is used
        instead of a wall-clock budget because timing flakes on loaded CI
        machines and module sets don't.
        """
        code = (
            "import sys; import yeaboi.cli; "
            f"leaked = sorted(set({HEAVY_MODULES!r}) & set(sys.modules)); "
            "sys.exit(f'heavy modules leaked into import yeaboi.cli: {leaked}' if leaked else 0)"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr.strip() or result.stdout.strip()


class TestModuleEntryPoint:
    """`python -m yeaboi` must be the same CLI as the console script.

    The desktop's bundled interpreter starts the backend that way — a console
    script's shebang is an absolute path written at install time, and the app
    bundle it lives in gets dragged somewhere else. There is no symptom short
    of a packaged app whose backend never comes up.
    """

    @pytest.mark.slow
    def test_it_runs_and_reports_the_same_version(self):
        from yeaboi import __version__

        result = subprocess.run([sys.executable, "-m", "yeaboi", "--version"], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr.strip()
        assert __version__ in (result.stdout + result.stderr)

    def test_it_delegates_rather_than_reimplementing(self):
        source = (Path(cli.__file__).parent / "__main__.py").read_text(encoding="utf-8")
        assert "from yeaboi.cli import main" in source
