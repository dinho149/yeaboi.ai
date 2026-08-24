"""The decisions a Ceremonies page makes, with no screen attached.

The TUI page and the desktop route both need the same four answers — what is
declared, what the OS will actually fire, whether the Slack lane can read back,
and what a run just did — and each was worked out once inside the TUI's loop.
They live here so both surfaces read them from one place.

``declare`` and ``set_enabled`` write in the order the CLI already writes in:
the store first, the scheduler second. A job installed for a ceremony the store
refused is the one failure that leaves the machine firing something nobody can
find.
"""

from __future__ import annotations

import logging
from pathlib import Path

from yeaboi.agent.state import Ceremony, CeremonyRun
from yeaboi.ceremonies import catalog, scheduler
from yeaboi.ceremonies.store import CeremonyStore

logger = logging.getLogger(__name__)

ACTIONS = ("Run now", "Pause", "Back")

NO_SESSION_MESSAGE = "No saved session yet — plan a project first."
NOTHING_SCHEDULED_MESSAGE = "Nothing scheduled — add one with `yeaboi ceremonies add`."

#: What the TUI's `l` prints. A command rather than a form, because this decides
#: whose name goes on a teammate's report and the surface that writes it should
#: be the one you can read it back from.
LINK_HINT = 'Find ids with `yeaboi slack members`, then: yeaboi slack link U0123456789 "Their Name"'
NO_TWO_WAY_MESSAGE = "Slack is write-only here — set SLACK_BOT_TOKEN to read replies back."


def add_hint(mode_key: str = "") -> str:
    """The terminal command that declares a ceremony, for a surface without a form."""
    return f"yeaboi ceremonies add <name> --mode {mode_key or catalog.CATALOG[0].key} --at 09:00"


def current_session() -> str:
    """The session a Ceremonies page is scoped to, or '' when there is none."""
    try:
        from yeaboi.mcp.tools_sessions import resolve_session_id

        return resolve_session_id("")
    except Exception:  # noqa: BLE001 — no saved sessions is a normal empty state
        logger.info("ceremonies: no session to scope to")
        return ""


def slack_status(session_id: str, *, db_path: Path | None = None) -> dict:
    """Whether the inbound lane is live, who is linked, and how often it polls.

    The interval comes off the **installed job**, not a config key: the job is
    the storage for it, so a plist and a stored number can never disagree about
    how often this actually happens.
    """
    from yeaboi import config

    ready, why = config.slack_two_way_ready()
    if not ready:
        return {"two_way": False, "why": why, "identities": [], "linked": 0, "interval_min": 0}
    identities: list[dict] = []
    interval = 0
    try:
        from yeaboi.slack import identity

        identities = identity.listing(session_id, db_path=db_path)
    except Exception:  # noqa: BLE001 — an unreadable mapping must not take the page down
        logger.warning("ceremonies: could not read the Slack identities", exc_info=True)
    try:
        interval = int(scheduler.slack_poll_status().get("interval_min", 0) or 0)
    except Exception:  # noqa: BLE001 — an unreadable job reads as "not installed"
        logger.warning("ceremonies: could not read the Slack poll job", exc_info=True)
    return {
        "two_way": True,
        "why": "",
        "identities": identities,
        "linked": len(identities),
        "interval_min": interval,
    }


def drift_lines(declared: list[Ceremony], installed: set[str]) -> list[str]:
    """Where the store and the operating system disagree.

    The store says what is declared, the OS says what will fire, and nothing
    else in the app would ever mention the gap.
    """
    known = {c.name for c in declared}
    enabled = {c.name for c in declared if c.enabled}
    lines = [f"a job is installed for {name!r}, which is not declared here" for name in sorted(installed - known)]
    lines += [f"{name!r} is paused but its job is still installed" for name in sorted((installed & known) - enabled)]
    lines += [f"{name!r} is declared but has no scheduled job — re-add it" for name in sorted(enabled - installed)]
    return lines


def load_page(session_id: str, *, db_path: Path | None = None) -> tuple[list[Ceremony], dict, dict, list[str]]:
    """(ceremonies, last runs, month spend, drift) — one read, one screen."""
    if not session_id:
        return [], {}, {}, []
    with CeremonyStore(db_path) as store:
        declared = store.list(session_id)
        last = {c.name: store.last_run(session_id, c.name) for c in declared}
        spend = {c.name: store.month_spend(session_id, c.name) for c in declared}
    return declared, last, spend, drift_lines(declared, set(scheduler.installed_ceremonies(session_id)))


def mode_options() -> list[dict]:
    """The schedulable catalog, as a form would offer it."""
    return [
        {
            "key": mode.key,
            "label": mode.label,
            "blurb": mode.blurb,
            "est_cost_usd": mode.est_cost_usd,
            "default_at": mode.default_at,
            "default_weekdays": mode.default_weekdays,
            "params": [
                {"name": p.name, "kind": p.kind, "default": p.default, "label": p.label or p.name, "help": p.help}
                for p in mode.params
            ],
        }
        for mode in catalog.schedulable_modes()
    ]


def channel_options() -> list[str]:
    from yeaboi.ceremonies.delivery import ALL_CHANNELS

    return list(ALL_CHANNELS)


def declare(
    session_id: str,
    *,
    name: str,
    mode: str,
    at: str = "",
    weekdays: str = "",
    channels: tuple[str, ...] = ("terminal",),
    args: tuple[tuple[str, str], ...] = (),
    stale_after_min: int = 120,
    monthly_cap_usd: float = 0.0,
    db_path: Path | None = None,
) -> tuple[Ceremony, str]:
    """Save a ceremony and install its job. Returns (stored, scheduler message).

    Raises :class:`ValueError` for anything the store refuses — the one place
    every surface's validation lives.
    """
    if not session_id:
        raise ValueError(NO_SESSION_MESSAGE)
    found = catalog.lookup(mode)
    if found is None:
        raise ValueError(catalog.refuse_reason(mode))
    with CeremonyStore(db_path) as store:
        stored = store.save(
            Ceremony(
                session_id=session_id,
                name=name.strip(),
                mode=found.key,
                args=args,
                weekdays=weekdays or found.default_weekdays,
                at=at or found.default_at,
                channels=channels,
                stale_after_min=stale_after_min,
                monthly_cap_usd=monthly_cap_usd,
            )
        )
    message = scheduler.install_ceremony(session_id, stored.name, stored.at, stored.weekdays)
    logger.info("ceremony declared: %s (%s) — %s", stored.name, stored.mode, message)
    return stored, message


def set_enabled(
    session_id: str, name: str, enabled: bool, *, db_path: Path | None = None
) -> tuple[Ceremony | None, str]:
    """Pause or resume one ceremony, moving its OS job with it.

    Pause removes the **job** and keeps the declaration; a paused ceremony that
    still fires is the thing users report as a bug.
    """
    with CeremonyStore(db_path) as store:
        ceremony = store.set_enabled(session_id, name, enabled)
    if ceremony is None:
        return None, f"no ceremony named {name!r}"
    if enabled:
        message = scheduler.install_ceremony(session_id, ceremony.name, ceremony.at, ceremony.weekdays)
    else:
        message = scheduler.remove_ceremony(session_id, ceremony.name)
    logger.info("ceremony %s %s: %s", name, "resumed" if enabled else "paused", message)
    return ceremony, message


def run_summary(run: CeremonyRun | None) -> str:
    """What a finished run says on a one-line notice."""
    if run is None:
        return "the run produced nothing"
    if run.outcome == "ok":
        delivered = ", ".join(channel for channel, ok in run.delivery if ok) or "nowhere"
        return f"{run.ceremony} ran (${run.cost_usd:.2f}) → {delivered}"
    return f"{run.ceremony}: {run.outcome} — {run.error or run.detail}"
