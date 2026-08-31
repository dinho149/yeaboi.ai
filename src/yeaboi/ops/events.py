"""What a connector returns: one thing production did, at one moment.

Raw events rather than rolled-up counts, because a count cannot be conflicted
with a ticket — the per-event ``service``, ``status`` and ``started_at`` are
exactly what conflict detection needs, and rolling up at the connector edge
would destroy them. Roll-up is a mode-layer concern (:mod:`yeaboi.ops.signals`).

**There is deliberately no ``author`` field, and no field that can carry a
body.** Nobody is credited or blamed for an alert firing, and a type with no
place to put a stack trace, a log line or a metric series cannot leak one — the
guarantee is the dataclass, not a rule a fetcher has to remember.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: The closed vocabulary. A kind outside this set is a design decision, not a
#: string a fetcher gets to invent.
EVENT_KINDS: tuple[str, ...] = ("incident", "alert", "error_spike", "deploy", "spend_change")

#: Severity as a word, in descending order. Vendors each spell this their own
#: way; :func:`clean_severity` maps onto this one so a mode compares like with
#: like. "" means the vendor said nothing, and is never rendered as "none".
SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low", "info")

#: Titles are identifiers, not content. Long enough to name a monitor, short
#: enough that a paragraph pasted into an alert name cannot ride along.
TITLE_MAX = 120

_WINDOW_RE = re.compile(r"^(\d{1,4})\s*([dhw])?$", re.IGNORECASE)

_SEVERITY_ALIASES: dict[str, str] = {
    "critical": "critical",
    "crit": "critical",
    "p1": "critical",
    "sev1": "critical",
    "fatal": "critical",
    "error": "high",
    "high": "high",
    "p2": "high",
    "sev2": "high",
    "major": "high",
    "warning": "medium",
    "warn": "medium",
    "medium": "medium",
    "p3": "medium",
    "sev3": "medium",
    "minor": "medium",
    "low": "low",
    "p4": "low",
    "sev4": "low",
    "info": "info",
    "informational": "info",
    "success": "info",
    "debug": "info",
}


@dataclass(frozen=True)
class OpsEvent:
    """One incident, alert or error window, as every mode sees it.

    Every field is an identifier, a word, a timestamp or a URL. Nothing here
    holds free text a user typed into your product.
    """

    kind: str  # one of EVENT_KINDS
    source: str  # the connector key that produced it
    ref: str  # the vendor's own handle, e.g. "PD-4821" — a provenance input
    title: str  # bounded; the monitor/incident NAME, never its body
    service: str = ""  # what it fired against, when the vendor says
    severity: str = ""  # one of SEVERITIES, or "" when unstated
    status: str = ""  # "firing" | "resolved" | "triggered" | …
    started_at: str = ""  # ISO 8601, UTC
    ended_at: str = ""  # ISO 8601, UTC; "" while still open
    url: str = ""  # where a human goes to see it

    @property
    def resolved(self) -> bool:
        """Whether the vendor says this one is over."""
        return self.status in ("resolved", "closed", "ok", "recovered")


def clean_title(raw: str) -> str:
    """A single bounded line: no newlines, no runs of space, truncated."""
    collapsed = " ".join((raw or "").split())
    if len(collapsed) <= TITLE_MAX:
        return collapsed
    return collapsed[: TITLE_MAX - 1].rstrip() + "…"


def clean_severity(raw: str) -> str:
    """Map a vendor's severity word onto :data:`SEVERITIES`, else ``""``.

    An unrecognised word becomes "" rather than passing through: a severity a
    mode cannot order is worse than no severity at all.
    """
    return _SEVERITY_ALIASES.get((raw or "").strip().lower(), "")


def iso(moment: datetime | None) -> str:
    """UTC ISO 8601, or ``""``. The one place a timestamp is formatted."""
    if moment is None:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    text = moment.astimezone(timezone.utc).isoformat()
    return text[:-6] + "Z" if text.endswith("+00:00") else text


def parse_ts(raw: str) -> datetime | None:
    """A vendor timestamp as an aware UTC datetime, or ``None``.

    Every vendor's ISO form goes through :mod:`yeaboi.timeparse`, which is what
    makes the ``Z`` suffix and a colonless offset parse the same on 3.10 as on
    3.11; a bare unix seconds value is Datadog's spelling. Anything else is None
    rather than an exception, because one malformed row must not lose the other
    ninety-nine.
    """
    from yeaboi.timeparse import parse_datetime

    text = (raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    try:
        parsed = parse_datetime(text)
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_window(spec: str, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    """``"14d"`` / ``"48h"`` / ``"2w"`` / ``"14"`` → ``(start, end)`` in UTC.

    Ops windows are deliberately allowed to be wider than the mode that asks
    for them — incident load over one working day is noise — so the window
    travels with the result rather than being assumed from the caller.
    """
    end = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    match = _WINDOW_RE.match((spec or "").strip())
    if not match:
        raise ValueError(f"invalid window {spec!r} — use e.g. 14d, 48h or 2w")
    amount, unit = int(match.group(1)), (match.group(2) or "d").lower()
    if amount < 1:
        raise ValueError("window must be at least 1")
    delta = {"h": timedelta(hours=amount), "d": timedelta(days=amount), "w": timedelta(weeks=amount)}[unit]
    return end - delta, end


def within(event: OpsEvent, start: datetime, end: datetime) -> bool:
    """Whether an event started inside the window. Undated events are kept.

    A vendor that returns no start for a row has still told us the row exists,
    and dropping it would silently undercount.
    """
    moment = parse_ts(event.started_at)
    return moment is None or start <= moment <= end
