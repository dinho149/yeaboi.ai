"""What a Reporting surface must decide before ``run_delivery_report`` is called.

Which periods exist, which extra step a period earns (the quarter's sprint
multi-select, the custom range's two dates), what this machine is configured to
report from, and how a set of checked sprints becomes one window — none of that
is rendering, and all of it was spelled inline in the terminal page. The deck
style and the palettes were already surface-neutral (``style.py``,
``themes.py``); this module is the missing half.

Deliberately ``setup.py`` and not ``engine.py``: the engine glob in the parity
test registers every public name in ``engine.py`` as a capability of its own.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import date, timedelta

from yeaboi.reporting.activity import (
    PERIOD_LABELS,
    PERIOD_LAST_MONTH,
    PERIOD_LAST_SPRINT,
    PERIOD_LAST_WEEK,
    PERIOD_QUARTER,
    PERIOD_WINDOW,
    available_report_sources,
)
from yeaboi.reporting.sprints import list_sprints, mark_in_quarter, quarter_bounds
from yeaboi.timeparse import parse_date

logger = logging.getLogger(__name__)

#: The components a report may draw on, in the order a picker shows them.
COMPONENTS = ("delivery", "code", "docs")

COMPONENT_DESCRIPTIONS = {
    "delivery": "where completed tickets come from",
    "code": "merged PRs/commits as supporting context",
    "docs": "doc updates as supporting context",
}

SOURCE_TITLES = {
    "jira": "Jira",
    "azuredevops": "Azure DevOps",
    "github": "GitHub",
    "confluence": "Confluence",
    "notion": "Notion",
}

NO_SOURCES_MESSAGE = "No data sources configured — connect a tracker in Settings."
NO_DELIVERY_MESSAGE = "Select at least one ticketing source."
NO_SPRINTS_CHECKED_MESSAGE = "Select at least one sprint (Space to toggle)."

#: How far back a custom range reaches by default — ~2 sprints, like last_month.
DEFAULT_WINDOW_DAYS = 28


def period_options(*, today: date | None = None) -> list[dict]:
    """The five periods, in picker order, with the quarter labelled for today.

    The quarter's label is per-quarter at runtime ("Q3 2026"), which is why this
    is a function and not a table.
    """
    q_label, _start, _end = quarter_bounds(today)
    return [
        {
            "key": PERIOD_LAST_WEEK,
            "label": PERIOD_LABELS[PERIOD_LAST_WEEK],
            "description": "The last 7 days of completed work",
        },
        {
            "key": PERIOD_LAST_SPRINT,
            "label": PERIOD_LABELS[PERIOD_LAST_SPRINT],
            "description": "The most recent sprint's completed work",
        },
        {
            "key": PERIOD_LAST_MONTH,
            "label": PERIOD_LABELS[PERIOD_LAST_MONTH],
            "description": "The last ~4 weeks across ~2 sprints",
        },
        {
            "key": PERIOD_QUARTER,
            "label": f"Whole quarter ({q_label})",
            "description": "Pick the sprints that make up the quarter",
        },
        {
            "key": PERIOD_WINDOW,
            "label": PERIOD_LABELS[PERIOD_WINDOW],
            "description": "Pick explicit start and end dates",
        },
    ]


def needs_sprints(period: str) -> bool:
    """True when Generate must offer the quarter's sprint multi-select first."""
    return period == PERIOD_QUARTER


def needs_window(period: str) -> bool:
    """True when Generate must collect explicit start/end dates first."""
    return period == PERIOD_WINDOW


def default_window(*, today: date | None = None) -> tuple[str, str]:
    """The dates a custom-range prompt starts from."""
    today = today or date.today()
    return (today - timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat(), today.isoformat()


def validate_window(window_start: str, window_end: str) -> tuple[str, str]:
    """Canonicalise a start/end pair, or raise ``ValueError`` with a plain reason.

    Everything downstream compares these as *strings* — the ordering check here,
    the ``day >= period_start`` filter in ``context.py``, the period label — and
    ISO-8601 has several spellings of the same day, so a valid ``20260818``
    would validate and then order wrongly against the ``2026-08-18`` it equals.
    """
    canonical = {}
    for name, value in (("window_start", window_start), ("window_end", window_end)):
        if not value:
            canonical[name] = value
            continue
        try:
            canonical[name] = parse_date(value).isoformat()
        except ValueError:
            raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD) — got {value!r}") from None
    start, end = canonical["window_start"], canonical["window_end"]
    if start and end and end < start:
        raise ValueError(f"window_end ({end}) is before window_start ({start})")
    return start, end


def source_grid() -> dict[str, list[str]]:
    """``{component: [source, …]}`` for whatever this machine has credentials for."""
    return available_report_sources()


def offerable_grid(grid: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """The grid with empty components dropped — what a picker actually shows."""
    grid = source_grid() if grid is None else grid
    return {component: list(found) for component, found in grid.items() if found}


def sources_step_applies(grid: dict[str, list[str]] | None = None) -> bool:
    """True only when there is a real choice: two or more configured sources.

    With one (or none) the single configuration is confirmed silently — asking
    a question whose answer is forced is not a step.
    """
    return sum(len(v) for v in offerable_grid(grid).values()) > 1


def normalize_selection(selection: dict | None, grid: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """Fill in every offered component, explicitly empty where nothing is checked.

    A component absent from a picker result means "auto" to
    ``activity.normalize_sources``, which would silently re-enable exactly what
    the user just deselected.
    """
    offered = offerable_grid(grid)
    selection = selection or {}
    return {component: list(selection.get(component, ())) for component in offered}


def sources_summary(selection: dict | None, grid: dict[str, list[str]] | None = None) -> str:
    """One status line: what the next Generate will consult."""
    chosen = selection if selection is not None else (source_grid() if grid is None else grid)

    def _fmt(component: str) -> str:
        names = [SOURCE_TITLES.get(s, s) for s in chosen.get(component, ())]
        return " + ".join(names) if names else "—"

    return f"Sources: {_fmt('delivery')}  ·  Code: {_fmt('code')}  ·  Docs: {_fmt('docs')}"


def sprint_options(session_id: str = "", *, db_path=None, today: date | None = None) -> list:
    """The quarter's sprint list, each marked whether it falls in the quarter.

    Live tracker first, else the saved plan's sprints. An empty list means the
    caller should report over the calendar quarter instead of asking.
    """
    _label, q_start, q_end = quarter_bounds(today)
    plan_state: dict = {}
    if session_id:
        try:
            from yeaboi.paths import get_db_path
            from yeaboi.sessions import SessionStore

            with SessionStore(db_path or get_db_path()) as store:
                plan_state = store.load_state(session_id) or {}
        except Exception:  # noqa: BLE001 — plan state is only the fallback source
            logger.warning("reporting setup: could not load plan state for the sprint list", exc_info=True)
    return mark_in_quarter(list_sprints(plan_state), q_start, q_end)


def default_checked(sprints) -> list[int]:
    """Indices pre-checked when the multi-select opens — the detected quarter."""
    return [i for i, sprint in enumerate(sprints) if sprint.in_quarter]


def window_from_sprints(sprints, checked, *, today: date | None = None) -> dict:
    """Turn the checked sprints into the window ``run_delivery_report`` wants.

    Returns ``{window_start, window_end, sprint_names, period_label_override}``,
    or ``{}`` when nothing is checked. The end never runs past today — a quarter
    still in progress must not claim to report on days that have not happened.
    """
    q_label, q_start, q_end = quarter_bounds(today)
    indices = sorted(i for i in set(checked) if 0 <= i < len(sprints))
    picked = [sprints[i] for i in indices]
    if not picked:
        return {}
    today_iso = (today or date.today()).isoformat()
    starts = [s.start_date for s in picked if s.start_date]
    ends = [s.end_date for s in picked if s.end_date]
    detected = {i for i, s in enumerate(sprints) if s.in_quarter}
    label = q_label if set(indices) == detected else f"{q_label} (custom)"
    return {
        "window_start": min(starts) if starts else q_start,
        "window_end": min(max(ends) if ends else q_end, today_iso),
        "sprint_names": tuple(s.name for s in picked),
        "period_label_override": label,
    }


def calendar_quarter_window(*, today: date | None = None) -> dict:
    """The fallback window when no sprint list exists at all."""
    q_label, q_start, q_end = quarter_bounds(today)
    today_iso = (today or date.today()).isoformat()
    return {
        "window_start": q_start,
        "window_end": min(q_end, today_iso),
        "sprint_names": (),
        "period_label_override": q_label,
    }


def resolve_fit(report, style):
    """Resolve a ``content_fit="ask"`` style for one export.

    Returns ``(style, extra_slides)``. ``extra_slides`` is non-zero only when
    expanding actually costs slides — that is the only case worth asking about,
    and the deck builders themselves can never prompt. The returned style is
    already decided when ``extra_slides`` is 0; otherwise the caller asks and
    calls :func:`apply_fit` with the answer.
    """
    if style.content_fit != "ask" or report is None:
        return style, 0
    from yeaboi.reporting.layout import count_fit_slides

    tight_n, expand_n = count_fit_slides(report, style)
    if expand_n <= tight_n:  # everything fits without extra slides — nothing to ask
        return dataclasses.replace(style, content_fit="expand"), 0
    return style, expand_n - tight_n


def apply_fit(style, expand: bool):
    """The style to export with once the fit question has an answer.

    The saved preference stays ``"ask"`` — the answer applies to this export.
    """
    return dataclasses.replace(style, content_fit="expand" if expand else "tight")
