"""ISO-8601 parsing, and the guard that keeps ``src/`` off the stdlib constructors.

Every case here is a real provider format. On 3.11+ these assertions also hold for
the bare stdlib call — that is the point: the module is a no-op on a modern
interpreter and only earns its keep on 3.10.
"""

from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from yeaboi.timeparse import parse_date, parse_datetime

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

UTC = timezone.utc


class TestParseDatetime:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Jira: milliseconds and a colonless offset.
            ("2024-01-15T10:30:00.000+0000", datetime(2024, 1, 15, 10, 30, tzinfo=UTC)),
            # GitHub and Notion: a bare Z.
            ("2024-01-15T10:30:00Z", datetime(2024, 1, 15, 10, 30, tzinfo=UTC)),
            ("2024-01-15T10:30:00z", datetime(2024, 1, 15, 10, 30, tzinfo=UTC)),
            # Azure DevOps: seven fractional digits, which truncate rather than round.
            ("2024-01-15T10:30:00.1234567Z", datetime(2024, 1, 15, 10, 30, 0, 123456, tzinfo=UTC)),
            ("2024-01-15T10:30:00.123456Z", datetime(2024, 1, 15, 10, 30, 0, 123456, tzinfo=UTC)),
            ("2024-01-15T10:30:00.5Z", datetime(2024, 1, 15, 10, 30, 0, 500000, tzinfo=UTC)),
            # What .isoformat() emits — the only shape 3.10 accepts unaided.
            ("2024-01-15T10:30:00+00:00", datetime(2024, 1, 15, 10, 30, tzinfo=UTC)),
            ("2024-01-15 10:30:00", datetime(2024, 1, 15, 10, 30)),
            ("2024-01-15", datetime(2024, 1, 15, 0, 0)),
            # Non-UTC offsets, with and without the colon, and hours-only.
            (
                "2024-01-15T10:30:00-0530",
                datetime(2024, 1, 15, 10, 30, tzinfo=timezone(-timedelta(hours=5, minutes=30))),
            ),
            ("2024-01-15T10:30:00+05", datetime(2024, 1, 15, 10, 30, tzinfo=timezone(timedelta(hours=5)))),
        ],
    )
    def test_provider_formats(self, raw, expected):
        assert parse_datetime(raw) == expected

    def test_surrounding_whitespace_is_tolerated(self):
        assert parse_datetime("  2024-01-15T10:30:00Z  ") == datetime(2024, 1, 15, 10, 30, tzinfo=UTC)

    @pytest.mark.parametrize("raw", ["", "not a date", "2024-13-45T99:99:99Z", "  "])
    def test_junk_raises_valueerror_like_the_stdlib(self, raw):
        """Call sites already catch ValueError and fall back; keeping the stdlib
        contract is what makes routing them through here a pure rename."""
        with pytest.raises(ValueError):
            parse_datetime(raw)

    def test_a_plain_date_is_not_mistaken_for_an_offset(self):
        """The offset pattern is anchored; without the `T` guard, `2024-01-15`
        ends in something that looks like `-01:15`."""
        assert parse_datetime("2024-01-15") == datetime(2024, 1, 15)


class TestParseDate:
    def test_parses_a_date(self):
        assert parse_date("2024-01-15") == date(2024, 1, 15)

    def test_tolerates_whitespace(self):
        assert parse_date(" 2024-01-15 ") == date(2024, 1, 15)

    def test_rejects_a_datetime_string_on_every_version(self):
        """3.11 did not widen this one, so no call site silently relied on it."""
        with pytest.raises(ValueError):
            parse_date("2024-01-15T10:30:00Z")

    @pytest.mark.parametrize("raw", ["", "nope", "15/01/2024"])
    def test_junk_raises_valueerror(self, raw):
        with pytest.raises(ValueError):
            parse_date(raw)


class TestNoBareStdlibCallsInSrc:
    """AST scan: `datetime.fromisoformat` on 3.10 rejects every format above."""

    def test_src_routes_every_call_through_this_module(self):
        offenders = []
        for path in SRC.rglob("*.py"):
            if path.name == "timeparse.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Attribute) and node.attr == "fromisoformat":
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        assert not offenders, "fromisoformat parses fewer formats on 3.10. Use yeaboi.timeparse:\n  " + "\n  ".join(
            offenders
        )

    def test_no_module_hand_rolls_the_z_workaround(self):
        """Six sites used to do this. parse_datetime is where it lives now."""
        offenders = [
            str(path.relative_to(ROOT))
            for path in SRC.rglob("*.py")
            if path.name != "timeparse.py" and '"Z", "+00:00"' in path.read_text(encoding="utf-8")
        ]
        assert not offenders, "use yeaboi.timeparse.parse_datetime instead:\n  " + "\n  ".join(offenders)
