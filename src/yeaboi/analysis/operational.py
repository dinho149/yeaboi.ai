"""What production did over the analysis window — counts, and a rate.

Analysis is the only mode whose window is wide enough for ops to say something
other than an anecdote: three incidents in a day is weather, three incidents in
120 days is a rate. So this component reports both the raw roll-up and a
normalised per-30-day figure, and nothing else.

**Deterministic, and no LLM.** There is no baseline for "is 2.4 incidents a
month good", and a model asked anyway will confidently invent one. The numbers
are stated; the reader knows their own product.

Nothing here is per-person. An ``OpsEvent`` has no author field to attribute
from, and this module adds none.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

#: The period a rate is normalised to. Thirty days is what a team already thinks
#: in ("about once a month"), and it divides every offered analysis window.
RATE_DAYS = 30


def run_operational(window_days: int, *, sub_sources=None, progress=None, now=None) -> tuple[dict | None, dict | None]:
    """Return ``(signal, blob)`` for the Operations component, ``(None, None)`` on failure.

    Shaped like the code and docs components' return so the engine's job pool
    treats all three the same. ``sub_sources`` narrows to a chosen subset of the
    connected connectors; empty means every one of them.
    """
    from yeaboi.connectors.fetching import gather
    from yeaboi.ops.signals import describe

    end = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = end - timedelta(days=int(window_days))
    chosen = set(sub_sources or ())

    try:
        result = gather(window=(start, end))
    except Exception as exc:
        logger.warning("operational analysis: gather failed: %s", exc, exc_info=True)
        return None, None

    sources = [s for s in result.sources if not chosen or s.key in chosen]
    signals = [s for s in result.signals if not chosen or s.source in chosen]
    scale = RATE_DAYS / max(1, int(window_days))

    rows = [
        {
            "kind": sig.kind,
            "family": sig.family,
            "source": sig.source,
            "count": sig.count,
            "resolved": sig.resolved,
            "severity": sig.severity,
            "services": list(sig.services),
            "samples": list(sig.samples),
            # Stated to one decimal because the window is long enough to earn
            # one and nowhere near long enough to earn two.
            "per_30_days": round(sig.count * scale, 1),
            "line": describe(sig),
        }
        for sig in signals
    ]
    totals: dict[str, int] = {}
    for sig in signals:
        totals[sig.kind] = totals.get(sig.kind, 0) + sig.count

    failed = [{"source": s.key, "label": s.label, "error": s.error} for s in sources if s.error]
    coverage = {
        "status": "complete" if not failed else ("failed" if not signals else "partial"),
        "has_data": bool(signals),
        "eligible": len(sources),
        "completed": sum(1 for s in sources if s.ok),
        "failed": len(failed),
        "inaccessible": 0,
        "truncated": 0,
        "grouped_errors": [{"detail": f"{f['label']}: {f['error']}"} for f in failed],
    }
    blob = {
        "signals": rows,
        "totals": totals,
        "per_30_days": {kind: round(n * scale, 1) for kind, n in totals.items()},
        "window": {"start": result.window_start, "end": result.window_end, "days": int(window_days)},
        "rate_days": RATE_DAYS,
        "sources": [{"key": s.key, "label": s.label, "ok": s.ok, "count": s.count, "error": s.error} for s in sources],
        "coverage_report": coverage,
        # No action plan: an action drawn from an incident count without a
        # baseline is a guess wearing a recommendation's clothes.
        "action_plan": [],
    }
    if progress is not None:
        total = sum(totals.values())
        progress.append(f"Operations: {total} event(s) across {len(rows)} signal(s) over {window_days}d")
    logger.info("operational analysis: %d signal(s) over %dd", len(rows), window_days)
    return {"kind": "operational", "signals": len(rows), "events": sum(totals.values())}, blob
