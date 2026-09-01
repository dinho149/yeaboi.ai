"""What production did, held beside the standup rather than inside it.

An ``OpsBundle`` is a sibling of ``ActivityBundle``, never a field on it. Two
reasons, both structural rather than stylistic: ``_rebuild_bundle``'s filters
key on ``author``, which an ops event deliberately lacks, so carrying an
unfilterable list through them is how a future edit starts filtering it; and
``collect_recent_activity`` has three other callers who would each pay for ops
fetches nobody asked for.

**There is no ``skipped`` list.** A vendor nobody connected cannot appear in
this bundle, so no renderer downstream can name it — the "hidden until
connected" rule is enforced by the type rather than by a filter each surface
has to remember. ``errors`` is a different thing and is kept: it can only ever
hold a vendor the user *did* connect, which is news on exactly the terms
``ActivityBundle.errors`` already is.

**There is no window setting.** A knob is another thing to see, and the point of
a wider window is that the mode asking does not choose it: incident load over
one working day is noise. The window travels on every signal, so a count always
says what it measured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from yeaboi.ops.events import OpsEvent
from yeaboi.ops.signals import OpsSignal

logger = logging.getLogger(__name__)

#: How far back ops reads, regardless of the standup's own window. Two weeks is
#: the shortest span over which "three incidents" means anything.
WINDOW_DAYS = 14


@dataclass(frozen=True)
class OpsBundle:
    """Production over a window, as one standup run saw it."""

    signals: tuple[OpsSignal, ...] = ()
    events: tuple[OpsEvent, ...] = ()
    window_start: str = ""  # ISO 8601, UTC
    window_end: str = ""
    window_days: int = 0
    #: (label, message) for a CONNECTED vendor that could not be read.
    errors: tuple[tuple[str, str], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.signals)


def connected() -> bool:
    """Whether any ops connector has both credentials and something to gather.

    Asked before the progress step is announced, not after: a user with no ops
    vendor must not see a phase go by for work that is not happening.
    """
    from yeaboi.connectors import registry

    return registry.any_fetchable()


def collect(*, window_days: int = WINDOW_DAYS, now=None) -> OpsBundle:
    """Read every connected ops connector, or return an empty bundle.

    Never raises: an ops read failing must not cost the standup that was going
    to run anyway. Nothing connected is the common case and costs one registry
    walk with no network at all.
    """
    from yeaboi.connectors.fetching import gather

    try:
        result = gather(since=f"{window_days}d", now=now)
    except Exception:
        logger.warning("standup: ops gather failed — the report runs without it", exc_info=True)
        return OpsBundle()

    if not result.sources:
        return OpsBundle()
    logger.info(
        "standup ops: %d signal(s) from %d source(s) over %dd",
        len(result.signals),
        len(result.sources),
        window_days,
    )
    return OpsBundle(
        signals=result.signals,
        events=result.events,
        window_start=result.window_start,
        window_end=result.window_end,
        window_days=window_days,
        errors=tuple((s.label, s.error) for s in result.failures if s.error),
    )


def window_label(bundle: OpsBundle) -> str:
    """ "the last 14 days" — the phrase every surface uses for the ops window."""
    days = bundle.window_days or WINDOW_DAYS
    return f"the last {days} day{'s' if days != 1 else ''}"


def signal_line(signal: OpsSignal) -> str:
    """One signal as a sentence, for a plaintext surface.

    The phrasing is shared with every other mode that renders one — an incident
    described two ways in two places is two products.
    """
    from yeaboi.ops.signals import describe

    return describe(signal)


def for_prompt(bundle: OpsBundle) -> list[dict]:
    """The signals as the model sees them: counts, words and services.

    Samples are deliberately included — a name like "checkout p99 latency" is
    what lets the model say something specific instead of "there were alerts" —
    and they are titles, which is the one thing an ``OpsEvent`` carries that a
    person wrote and the boundary already bounds.
    """
    return [
        {
            "kind": s.kind,
            "source": s.source,
            "count": s.count,
            "resolved": s.resolved,
            "worst_severity": s.severity,
            "services": list(s.services),
            "examples": list(s.samples),
        }
        for s in bundle.signals
    ]
