"""Firing one ceremony: guard it, run it, deliver it, record it.

One code path serves both callers. The OS job runs
``yeaboi ceremonies run <name> --scheduled``; a human pressing "Run now" in the
TUI runs the same function without the flag. What ``scheduled`` changes is only
the guards, because they are answers to questions an unattended run raises and
a deliberate one does not:

- **Staleness.** launchd coalesces missed calendar intervals and fires once at
  wake, so a 09:00 standup can arrive at 14:00 when the laptop lid opens; cron
  does not fire at all. A five-hour-old standup posted to the team channel is
  worse than no standup, so a late fire is skipped and recorded as skipped.
- **The monthly cap.** Unattended spend is the thing nobody is watching. A human
  who explicitly asks for a run gets it.
- **Pause.** A paused ceremony has no job, so a fire means the store and the
  operating system have drifted; the store wins and the drift is recorded.

Whatever happens, exactly one ledger row is written — including for the runs the
guards decline. A scheduled run that fails at 06:00 with nobody watching is how
this feature dies quietly, and a row saying *why* is the difference between
"nothing happened" and "something stopped it".

# See docs: "Architecture" — the engines; a ceremony is a scheduled call into one
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from yeaboi.agent.state import Ceremony, CeremonyRun, Dispatch
from yeaboi.ceremonies import catalog
from yeaboi.ceremonies.scheduler import parse_time
from yeaboi.ceremonies.store import CeremonyStore
from yeaboi.logging_setup import mode_log

logger = logging.getLogger(__name__)


class CeremonyNotFoundError(LookupError):
    """No ceremony by that name in this session."""


def _spend_probe() -> Callable[[], float]:
    """Return a callable giving the LLM spend since this moment, in USD.

    Measured as the delta in yeaboi's own token counters, priced by the shared
    table — deliberately **not** read off the artifact. Three of the catalogued
    modes report a ``total_cost_usd`` that is what the *coding agents* spent,
    and billing a ceremony for the spend it is reporting on would blow any cap
    on the first run.

    An estimate, and labelled one: the counters are process-global totals with
    no per-model split, so a run that switched models mid-way is priced at the
    configured one.
    """
    from yeaboi.agent.llm import get_llm_model, get_llm_provider, get_usage_stats
    from yeaboi.pricing import estimate_cost

    before = get_usage_stats()

    def _spent() -> float:
        after = get_usage_stats()
        try:
            return estimate_cost(
                get_llm_model() or "",
                input_tokens=max(0, after.get("input_tokens", 0) - before.get("input_tokens", 0)),
                output_tokens=max(0, after.get("output_tokens", 0) - before.get("output_tokens", 0)),
                provider=get_llm_provider() or "",
            ).usd
        except Exception:  # noqa: BLE001 — a costing failure must not fail the run
            logger.warning("ceremony: could not price this run", exc_info=True)
            return 0.0

    return _spent


def _minutes_late(ceremony: Ceremony, now: datetime) -> float:
    """How far past its declared slot this fire is, in minutes (negative = early).

    Local time throughout, because that is what launchd and cron fire in.
    """
    try:
        hour, minute = parse_time(ceremony.at)
    except ValueError:
        logger.warning("ceremony %s has an unparseable time %r", ceremony.name, ceremony.at)
        return 0.0
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (now - slot).total_seconds() / 60.0


def _guard(ceremony: Ceremony, now: datetime, store: CeremonyStore) -> tuple[str, str]:
    """(outcome, reason) when a scheduled fire should be declined, else ("", "")."""
    if not ceremony.enabled:
        return "skipped_paused", "the ceremony is paused but its job still fired"

    late = _minutes_late(ceremony, now)
    if ceremony.stale_after_min and late > ceremony.stale_after_min:
        return (
            "skipped_stale",
            f"fired {late:.0f} min after its {ceremony.at} slot (limit {ceremony.stale_after_min}) — "
            "the machine was probably asleep, and a report this late misleads",
        )

    if ceremony.monthly_cap_usd:
        try:
            spent = store.month_spend(ceremony.session_id, ceremony.name)
        except Exception:  # noqa: BLE001
            # A ceremony that cannot account for its spend does not spend.
            logger.error("ceremony %s: ledger unreadable, declining to run", ceremony.name, exc_info=True)
            return "skipped_over_cap", "the run ledger could not be read, so this month's spend is unknown"
        if spent >= ceremony.monthly_cap_usd:
            return (
                "skipped_over_cap",
                f"${spent:.2f} already spent this month against a ${ceremony.monthly_cap_usd:.2f} cap",
            )
    return "", ""


def _notify_skip(ceremony: Ceremony, reason: str) -> None:
    """Tell somebody. A guard that declines silently is indistinguishable from
    a feature that quietly stopped working."""
    try:
        from yeaboi.ceremonies.delivery import notify_desktop

        notify_desktop(f"yeaboi: {ceremony.name} skipped", reason)
    except Exception:  # noqa: BLE001
        logger.warning("ceremony %s: could not post the skip notification", ceremony.name, exc_info=True)


def run_ceremony(
    name: str,
    *,
    session_id: str = "",
    scheduled: bool = False,
    db_path: Path | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> CeremonyRun:
    """Fire one declared ceremony. Returns the ledger row it wrote.

    Never raises for anything that happens *during* the run — a failing engine,
    a dead webhook and an unparseable artifact all become a recorded ``failed``
    row, because the alternative is a traceback in a log file nobody opens.
    ``CeremonyNotFoundError`` is the one exception, and it is raised before anything
    has happened: there is nothing to record a run against.
    """
    moment = now or datetime.now()

    def _report(step: str) -> None:
        logger.info("ceremony %s: %s", name, step)
        if on_progress is not None:
            try:
                on_progress(step)
            except Exception:  # noqa: BLE001 — a UI callback must not kill the run
                logger.debug("ceremony progress callback raised", exc_info=True)

    # Its own log file rather than the fired mode's: a scheduled run's log is
    # the only trace of a fire nobody watched, and burying it among the runs a
    # human started is how "did it fire at all?" becomes unanswerable.
    with mode_log("ceremonies"), CeremonyStore(db_path) as store:
        ceremony = store.get(session_id, name)
        if ceremony is None:
            raise CeremonyNotFoundError(f"no ceremony {name!r} in session {session_id!r}")

        if scheduled:
            outcome, reason = _guard(ceremony, moment, store)
            if outcome:
                logger.warning("ceremony %s declined: %s", name, reason)
                _notify_skip(ceremony, reason)
                return store.record_run(
                    CeremonyRun(
                        ceremony=name,
                        session_id=session_id,
                        outcome=outcome,
                        scheduled=True,
                        detail=reason,
                    )
                )

        mode = catalog.lookup(ceremony.mode)
        if mode is None:
            # Only reachable if the catalog dropped a mode a stored ceremony
            # still names — a yeaboi downgrade, or a mode that was withdrawn.
            # `or` because a recorded failure with a blank reason is the exact
            # thing the ledger exists to prevent.
            reason = catalog.refuse_reason(ceremony.mode) or f"mode {ceremony.mode!r} is no longer schedulable"
            logger.error("ceremony %s: %s", name, reason)
            return store.record_run(
                CeremonyRun(ceremony=name, session_id=session_id, outcome="failed", scheduled=scheduled, error=reason)
            )

        started = time.monotonic()
        spent = _spend_probe()
        error = ""
        dispatch: Dispatch | None = None
        try:
            _report(f"running {mode.label}")
            kwargs = catalog.engine_kwargs(mode, ceremony.args, session_id=ceremony.session_id)
            if dry_run:
                kwargs["dry_run"] = True
            artifact = catalog.engine_callable(mode)(**kwargs)
            dispatch = catalog.renderer_callable(mode)(artifact)
        except Exception as exc:  # noqa: BLE001 — every failure becomes a row
            error = f"{type(exc).__name__}: {exc}"
            logger.error("ceremony %s failed: %s", name, error, exc_info=True)

        delivery_results: tuple[tuple[str, bool], ...] = ()
        if dispatch is not None and not dry_run:
            _report("delivering")
            from yeaboi.ceremonies.delivery import deliver

            delivery_results = tuple(deliver(dispatch, list(ceremony.channels)).items())

        # Delivery failing does not make the run a failure: the report exists and
        # is in its mode's own history. Which channels took it is a column, so a
        # dead webhook is visible without being fatal.
        run = store.record_run(
            CeremonyRun(
                ceremony=name,
                session_id=session_id,
                outcome="failed" if error else "ok",
                scheduled=scheduled,
                duration_s=round(time.monotonic() - started, 2),
                cost_usd=round(spent(), 4),
                delivery=delivery_results,
                detail=dispatch.summary[:200] if dispatch else "",
                error=error,
            )
        )
        if not error:
            store.mark_fired(session_id, name, run.fired_at)
        return run
