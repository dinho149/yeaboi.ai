"""The single hardest line in the two-way design, asserted rather than trusted.

**A Slack reply can add attributed prose. It can never change generated prose.**

Everything else about free text — the injection sweep, the length caps, the
allowlist, the daily cap — bounds *how much* an authorised human can add. This
is the one that bounds *what kind of thing* they can do at all, and it is worth
a test of its own because the failure mode of a convention is silence: a future
correction path reaching for ``OP_SET`` would look entirely reasonable in review
and would quietly turn "add a note" into "rewrite the report".

Two checks, in the spirit of the registry checks this repo asserts rather than
assumes. The AST walk is the real one; the name scan is the cheap belt that also
catches a helper built one refactor before it is used.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from yeaboi.artifacts.edits import EDIT_OPS, OP_NOTE

SLACK = Path(__file__).resolve().parents[2] / "src" / "yeaboi" / "slack"

#: Every op but the one a Slack reply is allowed to produce.
FORBIDDEN = tuple(f"OP_{op.upper()}" for op in EDIT_OPS if op != OP_NOTE)


def _modules() -> list[Path]:
    files = sorted(SLACK.glob("*.py"))
    assert files, "the slack package moved — this guard is now asserting nothing"
    return files


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_module_imports_or_names_an_op_other_than_note(path: Path):
    """The cheap belt: an op helper built one refactor before it is used.

    Read out of the AST rather than out of the text, so that *writing down* the
    rule — "``OP_SET`` is unreachable from here" — does not itself trip it. A
    docstring naming the thing it forbids is the shape a guard should encourage.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    named = {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute))
    }
    named |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    offenders = sorted(named & set(FORBIDDEN))
    assert not offenders, f"{path.name} reaches for {offenders}; only OP_NOTE may be reachable from Slack"


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_every_edit_this_package_builds_is_a_note(path: Path):
    """Walk each dict literal carrying an ``"op"`` key and read what it is."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=False):
            if not (isinstance(key, ast.Constant) and key.value == "op"):
                continue
            spelled = value.id if isinstance(value, ast.Name) else getattr(value, "value", None)
            assert spelled in ("OP_NOTE", OP_NOTE), (
                f"{path.name}:{node.lineno} builds an edit with op={spelled!r} — Slack may only write notes"
            )


def test_the_guard_would_notice() -> None:
    # A guard whose own detector is broken passes forever. This is the shape it
    # is looking for, and it must fail the same assertion the modules pass.
    tree = ast.parse('edit = {"op": OP_SET, "path": "summary"}')
    dicts = [n for n in ast.walk(tree) if isinstance(n, ast.Dict)]
    spelled = dicts[0].values[0].id
    assert spelled not in ("OP_NOTE", OP_NOTE)
