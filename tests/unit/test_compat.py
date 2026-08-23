"""The 3.10 compatibility shim, and the guard that stops the floor rotting.

``yeaboi._compat.StrEnum`` is only a shim below 3.11, so on a modern interpreter
these tests assert against the real ``enum.StrEnum``. The shim's own semantics are
pinned by re-deriving them here: a locally defined ``(str, Enum)`` carrying the same
two dunders must behave identically, which is what the shim class is.
"""

from __future__ import annotations

import ast
import json
import sys
from enum import Enum
from pathlib import Path

import pytest

from yeaboi._compat import IntEnum, StrEnum

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRS = ("src", "tests", "scripts", "packaging")


class Sample(StrEnum):
    LOWER = "lower"
    MIXED = "Mixed Case"


class Points(IntEnum):
    THREE = 3


class HandRolled(str, Enum):
    """What ``_compat.StrEnum`` reduces to below 3.11."""

    __str__ = str.__str__
    __format__ = str.__format__

    LOWER = "lower"
    MIXED = "Mixed Case"


class TestStrEnumSemantics:
    """All four behaviours, because dropping any one corrupts a written artifact."""

    @pytest.mark.parametrize("cls", [Sample, HandRolled], ids=["shim-or-stdlib", "hand-rolled"])
    def test_str_is_the_value_not_the_qualified_name(self, cls):
        assert str(cls.MIXED) == "Mixed Case"

    @pytest.mark.parametrize("cls", [Sample, HandRolled], ids=["shim-or-stdlib", "hand-rolled"])
    def test_format_is_the_value(self, cls):
        # Assigning only __str__ leaves this rendering as "Sample.MIXED".
        assert f"{cls.MIXED}" == "Mixed Case"
        assert format(cls.MIXED) == "Mixed Case"

    @pytest.mark.parametrize("cls", [Sample, HandRolled], ids=["shim-or-stdlib", "hand-rolled"])
    def test_json_round_trips_as_the_value(self, cls):
        assert json.dumps({"k": cls.MIXED}) == '{"k": "Mixed Case"}'

    @pytest.mark.parametrize("cls", [Sample, HandRolled], ids=["shim-or-stdlib", "hand-rolled"])
    def test_compares_equal_to_its_value(self, cls):
        assert cls.LOWER == "lower"

    def test_a_plain_str_enum_would_not_pass_these(self):
        """The trap this module exists to avoid, stated as a test.

        The two dunders fail on different versions, which is why both are assigned:
        ``str()`` is wrong everywhere, and ``format()`` is additionally wrong from
        3.11 on. A shim carrying only ``__str__`` would pass its tests on 3.10 and
        corrupt every f-string on the version most people run.
        """

        class Naive(str, Enum):
            MIXED = "Mixed Case"

        assert str(Naive.MIXED) != "Mixed Case"
        if sys.version_info >= (3, 11):
            assert f"{Naive.MIXED}" != "Mixed Case"

    def test_generate_next_value_lowercases(self):
        assert StrEnum._generate_next_value_("SOME_NAME", 1, 0, []) == "some_name"


class TestIntEnumSemantics:
    """3.11 also changed IntEnum. StoryPointValue is rendered into tables and
    written into artifacts, so a member showing as its name is user-visible."""

    def test_str_is_the_number(self):
        assert str(Points.THREE) == "3"

    def test_format_is_the_number_and_honours_a_spec(self):
        assert f"{Points.THREE}" == "3"
        assert f"{Points.THREE:>5}" == "    3"

    def test_repr_still_names_the_member(self):
        assert repr(Points.THREE) == "<Points.THREE: 3>"

    def test_json_round_trips_as_the_number(self):
        assert json.dumps({"pts": Points.THREE}) == '{"pts": 3}'

    def test_int_dunder_str_would_have_been_wrong(self):
        """int defines no __str__, so int.__str__ is object.__str__ and delegates
        back to Enum's __repr__ — the trap the shim's comment names."""
        assert int.__str__ is object.__str__


def _python_files() -> list[Path]:
    return [p for d in SOURCE_DIRS for p in (ROOT / d).rglob("*.py")]


class TestFloorGuard:
    """AST scans — grep cannot tell a docstring mention from a real reference."""

    def test_no_module_imports_utc_from_datetime(self):
        offenders = []
        for path in _python_files():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module == "datetime":
                    if any(alias.name == "UTC" for alias in node.names):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        assert not offenders, "datetime.UTC is 3.11+. Use `timezone.utc`:\n  " + "\n  ".join(offenders)

    def test_no_module_reaches_utc_as_an_attribute(self):
        """Catches `dt.UTC` under `import datetime as dt`, which the import scan misses."""
        offenders = []
        for path in _python_files():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Attribute) and node.attr == "UTC":
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        assert not offenders, "datetime.UTC is 3.11+. Use `timezone.utc`:\n  " + "\n  ".join(offenders)

    def test_no_module_imports_strenum_from_enum(self):
        offenders = []
        for path in _python_files():
            if path.name == "_compat.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module == "enum":
                    if any(alias.name == "StrEnum" for alias in node.names):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        assert not offenders, "enum.StrEnum is 3.11+. Import it from `yeaboi._compat`:\n  " + "\n  ".join(offenders)

    def test_the_shim_is_the_stdlib_type_on_a_modern_interpreter(self):
        import enum

        if sys.version_info >= (3, 11):
            assert StrEnum is enum.StrEnum
        else:
            assert StrEnum is not getattr(enum, "StrEnum", None)
