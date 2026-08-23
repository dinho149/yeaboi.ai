"""ISO-8601 parsing that behaves the same on every supported Python.

3.11 rewrote ``datetime.fromisoformat`` to accept most of ISO-8601; 3.10 accepts
only what ``.isoformat()`` emits. Provider timestamps sit on the wrong side of
that line — Jira sends a colonless offset (``+0000``), GitHub and Notion send a
``Z`` suffix, Azure DevOps sends seven fractional digits — so a call site fed raw
provider data parses on 3.11 and raises on 3.10.

Both functions keep the stdlib's error contract and raise ``ValueError`` on junk.
Call sites already carry their own ``except (ValueError, TypeError)`` and their own
fallback, so routing them through here is a pure name substitution.

Delete this module when the floor rises to 3.11.
"""

from __future__ import annotations

import re
from datetime import date, datetime

# A trailing UTC designator, or an offset written without its colon (``+0000``,
# ``-0530``) or without minutes at all (``+05``). Anchored to the end so a date
# like ``2024-01-15`` is never mistaken for an offset.
_OFFSET = re.compile(r"(?P<sign>[+-])(?P<hours>\d{2}):?(?P<minutes>\d{2})?$")
_FRACTION = re.compile(r"\.(?P<digits>\d+)")


def _normalise(value: str) -> str:
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    # 3.10 wants exactly 3 or 6 fractional digits. Truncate rather than round —
    # that is what 3.11+ does with a longer fraction.
    def _pad(match: re.Match[str]) -> str:
        return "." + match.group("digits")[:6].ljust(6, "0")

    text = _FRACTION.sub(_pad, text, count=1)

    match = _OFFSET.search(text)
    if match and "T" in text.upper():
        offset = f"{match.group('sign')}{match.group('hours')}:{match.group('minutes') or '00'}"
        text = text[: match.start()] + offset
    return text


def parse_datetime(value: str) -> datetime:
    """``datetime.fromisoformat`` with 3.11's tolerance, on any supported Python.

    Raises ``ValueError`` exactly as the stdlib does.
    """
    return datetime.fromisoformat(_normalise(value))


def parse_date(value: str) -> date:
    """``date.fromisoformat``, which on every version rejects a datetime string.

    Kept alongside ``parse_datetime`` so the rule is one sentence — no call site
    in ``src/`` reaches for the stdlib constructors directly.
    """
    return date.fromisoformat(value.strip())
