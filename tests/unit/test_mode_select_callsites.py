"""Static check: every internal call in the TUI hub passes the right arity.

`ui/mode_select/__init__.py` is the biggest module in the repo and most of it is
one long function, so a helper's signature can change while a call site deep
inside it does not — and nothing complains. Ruff does not check arity across
functions, the module is far too big to import-and-exercise in a unit test, and
the failure only ever shows up as the app dying on the keypress that reaches it.

That is not hypothetical: `_run_subscription_sign_in` gained a `render_page`
parameter, the call site was missed, and the settings page crashed the app the
first time anyone pressed Enter on the subscription row.

So this walks the AST instead: for every module-level function in the file, every
call to it by bare name must pass an argument count its signature accepts.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_TARGETS = [
    Path("src/yeaboi/ui/mode_select/__init__.py"),
    Path("src/yeaboi/ui/mode_select/screens/_screens.py"),
    Path("src/yeaboi/ui/mode_select/screens/_screens_secondary.py"),
]


def _signatures(tree: ast.Module) -> dict[str, tuple[int, int | None]]:
    """``{name: (min positional args, max or None for *args)}`` for top-level defs.

    Only module-level defs: a nested helper is scoped to its enclosing function,
    and matching those by bare name across the module would compare unrelated
    things that happen to share a name.
    """
    # A name defined more than once anywhere in the file is ambiguous: a nested
    # def shadows the module-level one inside its enclosing scope, and a bare-name
    # call cannot be attributed to either without real scope analysis. Skipping
    # those keeps the check sound — it reports only what it is certain about.
    seen: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            seen[node.name] = seen.get(node.name, 0) + 1

    out: dict[str, tuple[int, int | None]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if seen.get(node.name, 0) > 1:
            continue
        args = node.args
        positional = args.posonlyargs + args.args
        required = len(positional) - len(args.defaults)
        maximum = None if args.vararg else len(positional)
        out[node.name] = (max(0, required), maximum)
    return out


def _calls(tree: ast.Module):
    """Every ``name(...)`` call, with its position and positional-arg count."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if any(isinstance(a, ast.Starred) for a in node.args):
            continue  # *args at the call site — arity is not knowable statically
        yield node.func.id, node.lineno, len(node.args), {k.arg for k in node.keywords if k.arg}


@pytest.mark.parametrize("path", _TARGETS, ids=lambda p: p.name)
def test_internal_calls_match_their_signatures(path: Path):
    source = path.read_text()
    tree = ast.parse(source)
    signatures = _signatures(tree)

    problems: list[str] = []
    for name, lineno, n_positional, keywords in _calls(tree):
        if name not in signatures:
            continue  # imported, builtin, or a local — not ours to check
        required, maximum = signatures[name]
        supplied = n_positional + len(keywords)
        if supplied < required:
            problems.append(f"{path}:{lineno}: {name}() takes {required} args, {supplied} passed")
        elif maximum is not None and n_positional > maximum:
            problems.append(f"{path}:{lineno}: {name}() takes at most {maximum} positional, {n_positional} passed")

    assert not problems, "call sites disagree with their function signatures:\n  " + "\n  ".join(problems)
