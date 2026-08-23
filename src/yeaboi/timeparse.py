"""ISO-8601 parsing that behaves the same on every supported Python.

3.11 rewrote ``datetime.fromisoformat`` to accept most of ISO-8601; 3.10 accepts
only what ``.isoformat()`` emits. Provider timestamps sit on the wrong side of
that line — Jira sends a colonless offset (``+0000``), GitHub and Notion send a
``Z`` suffix, Azure DevOps sends seven fractional digits, and some Java stacks
use a comma as the fractional separator — so a call site fed raw provider data
parses on 3.11 and raises on 3.10.

The normaliser closes that gap for every form 3.11 accepts and 3.10 does not:
the ``Z`` suffix, colonless and minuteless offsets, over- and under-long
fractions, the comma separator, the compact (``20240115T103000``) forms, and
ISO week dates. ``tests/unit/test_timeparse.py`` asserts the two agree by
differential-testing a corpus against the stdlib.

Both functions keep the stdlib's error contract: ``ValueError`` on a malformed
string, ``TypeError`` on a non-string. Call sites already carry their own
``except (ValueError, TypeError)`` and their own fallback, so routing them
through here is a pure name substitution.

Delete this module when the floor rises to 3.11.
"""

from __future__ import annotations

import re
from datetime import date, datetime

# A trailing UTC designator, or an offset written without its colon (``+0000``,
# ``-0530``) or without minutes at all (``+05``). Anchored to the end so a date
# like ``2024-01-15`` is never mistaken for an offset.
_OFFSET = re.compile(r"(?P<sign>[+-])(?P<hours>\d{2}):?(?P<minutes>\d{2})?$")
_FRACTION = re.compile(r"[.,](?P<digits>\d+)")
# ``2024-W03-1`` / ``2024W031`` — the weekday is optional and defaults to Monday.
_WEEK_DATE = re.compile(r"^(?P<year>\d{4})-?W(?P<week>\d{2})-?(?P<weekday>\d)?$")
_COMPACT_DATE = re.compile(r"^(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})$")
_COMPACT_TIME = re.compile(r"^(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})?(?P<fraction>\.\d+)?$")


def _require_str(value: object, name: str) -> str:
    """Reject a non-string the way the stdlib constructors do.

    Without this, ``value.strip()`` raises ``AttributeError`` on ``None`` and every
    call site whose handler names ``TypeError`` silently stops catching it.
    """
    if not isinstance(value, str):
        raise TypeError(f"{name}() argument must be str, not {type(value).__name__}")
    return value.strip()


def _expand_date(text: str) -> str:
    """Rewrite the compact and week forms into the extended form 3.10 accepts."""
    week = _WEEK_DATE.match(text)
    if week:
        # %G/%V/%u are the ISO year/week/weekday directives; available since 3.6,
        # so the conversion itself needs no version gate.
        weekday = week.group("weekday") or "1"
        resolved = datetime.strptime(f"{week.group('year')}-W{week.group('week')}-{weekday}", "%G-W%V-%u")
        return resolved.date().isoformat()
    compact = _COMPACT_DATE.match(text)
    if compact:
        return f"{compact.group('year')}-{compact.group('month')}-{compact.group('day')}"
    return text


def _expand_time(text: str) -> str:
    compact = _COMPACT_TIME.match(text)
    if not compact:
        return text
    parts = [compact.group("hour"), compact.group("minute")]
    if compact.group("second"):
        parts.append(compact.group("second"))
    return ":".join(parts) + (compact.group("fraction") or "")


def _normalise(text: str) -> str:
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    # 3.10 wants exactly 3 or 6 fractional digits, and only a dot. Truncate rather
    # than round — that is what 3.11+ does with a longer fraction.
    text = _FRACTION.sub(lambda m: "." + m.group("digits")[:6].ljust(6, "0"), text, count=1)

    offset = ""
    match = _OFFSET.search(text)
    if match and ("T" in text.upper() or ":" in text):
        offset = f"{match.group('sign')}{match.group('hours')}:{match.group('minutes') or '00'}"
        text = text[: match.start()]

    separator = "T" if "T" in text else (" " if " " in text else "")
    if separator:
        head, _, tail = text.partition(separator)
        text = _expand_date(head) + separator + _expand_time(tail)
    else:
        text = _expand_date(text)
    return text + offset


def parse_datetime(value: str) -> datetime:
    """``datetime.fromisoformat`` with 3.11's tolerance, on any supported Python.

    Raises ``ValueError`` on a malformed string and ``TypeError`` on a non-string,
    exactly as the stdlib does.
    """
    return datetime.fromisoformat(_normalise(_require_str(value, "parse_datetime")))


def parse_date(value: str) -> date:
    """``date.fromisoformat``, which on every version rejects a datetime string.

    Kept alongside ``parse_datetime`` so the rule is one sentence — no call site
    in ``src/`` reaches for the stdlib constructors directly.
    """
    return date.fromisoformat(_expand_date(_require_str(value, "parse_date")))
