"""The rolled-up form: bounded counts a mode can put in front of a person.

A sibling of ``SupportingSignal``, not an extension of it. Three reasons:
``signals_sentence()`` sums a closed three-set positionally, so a new kind
would fall out of the sentence while still printing in the export; "corroborated
by" claims *support*, and an incident qualifies delivery rather than
corroborating it; and ops needs ``severity`` plus an explicit window, because a
signal riding on a one-day standup that measured fourteen days must say so.

The ``PerfMetric`` rule holds verbatim: **a kind with no event is omitted, never
emitted as 0.** A team whose Sentry was never read did not have zero regressions.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from yeaboi.ops.events import SEVERITIES, OpsEvent

#: How many sample titles a signal carries. Samples are for recognition ("oh,
#: that one"), not for reading — a longer list is a log by another name.
SAMPLE_CAP = 5

#: How many service names a rendered signal names before counting the rest.
SERVICE_CAP = 3


@dataclass(frozen=True)
class OpsSignal:
    """What one source saw, of one kind, over one window."""

    kind: str = ""  # one of events.EVENT_KINDS
    family: str = ""  # the connector's family: observability | incidents | errors
    source: str = ""  # the connector key
    count: int = 0
    resolved: int = 0  # how many of them the vendor says are over
    severity: str = ""  # the worst severity seen, or ""
    services: tuple[str, ...] = ()  # bounded, sorted, deduped
    window_start: str = ""
    window_end: str = ""
    samples: tuple[str, ...] = field(default=())  # bounded titles, never bodies


def worst_severity(events: tuple[OpsEvent, ...]) -> str:
    """The most severe word any event carried, or ``""`` if none did."""
    seen = {e.severity for e in events if e.severity}
    return next((s for s in SEVERITIES if s in seen), "")


def describe(signal: OpsSignal) -> str:
    """One signal as a sentence — the phrasing every surface shares.

    Resolved is stated only when some are: "2 incidents (0 resolved)" reads as
    a reproach, and a signal that says nothing about resolution is honest about
    a vendor that says nothing about it either.
    """
    noun = signal.kind.replace("_", " ")
    parts = [f"{signal.count} {noun}{'' if signal.count == 1 else 's'} via {signal.source}"]
    if signal.resolved:
        parts.append(f"{signal.resolved} resolved")
    if signal.severity:
        parts.append(f"worst {signal.severity}")
    if signal.services:
        shown = ", ".join(signal.services[:SERVICE_CAP])
        more = f" +{len(signal.services) - SERVICE_CAP}" if len(signal.services) > SERVICE_CAP else ""
        parts.append(f"services: {shown}{more}")
    return " · ".join(parts)


def roll_up(
    events: tuple[OpsEvent, ...],
    *,
    family_of: dict[str, str] | None = None,
    window_start: str = "",
    window_end: str = "",
) -> tuple[OpsSignal, ...]:
    """Group events into signals by ``(kind, source)``.

    By source and kind, never by service: forty monitors would emit forty rows,
    which is the nag this layer exists to avoid. Ordered by count descending so
    the loudest source reads first, ties broken by name for a stable render.
    """
    families = family_of or {}
    grouped: dict[tuple[str, str], list[OpsEvent]] = {}
    for event in events:
        grouped.setdefault((event.kind, event.source), []).append(event)

    signals = []
    for (kind, source), rows in grouped.items():
        batch = tuple(rows)
        services = tuple(sorted({e.service for e in batch if e.service}))
        # The commonest titles, not the first five: a repeating monitor is what
        # a reader most wants named.
        common = Counter(e.title for e in batch if e.title).most_common(SAMPLE_CAP)
        signals.append(
            OpsSignal(
                kind=kind,
                family=families.get(source, ""),
                source=source,
                count=len(batch),
                resolved=sum(1 for e in batch if e.resolved),
                severity=worst_severity(batch),
                services=services,
                window_start=window_start,
                window_end=window_end,
                samples=tuple(title for title, _ in common),
            )
        )
    return tuple(sorted(signals, key=lambda s: (-s.count, s.source, s.kind)))
