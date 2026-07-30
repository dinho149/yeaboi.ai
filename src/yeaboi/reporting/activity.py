"""Gather the team's *delivered* (completed) work over a reporting period.

Deterministic (no LLM): reuses the same team-wide recent-activity helpers the Daily
Standup collector uses (``jira_recent_activity`` / ``azdevops_recent_activity``) plus
the standup's ``sprint_context.gather`` for the active-sprint name and length, then
keeps only the tickets whose status means *done*. These DeliveredItem rows are the
concrete evidence the business-report prompt reasons over.

Tool helpers are imported lazily (optional SDKs), same convention as
performance/activity.py. Degrades to an empty list on missing config — the engine
turns that into a warning, never a crash.

# See docs: "Daily Standup" — recent-activity collection, sprint context
"""

from __future__ import annotations

import logging
from datetime import date

from yeaboi.agent.state import DeliveredItem

logger = logging.getLogger(__name__)

# Reporting periods the TUI/engine understand.
PERIOD_LAST_WEEK = "last_week"
PERIOD_LAST_SPRINT = "last_sprint"
PERIOD_LAST_MONTH = "last_month"
PERIOD_QUARTER = "quarter"  # label is set per-quarter at runtime (e.g. "Q3 2026")
PERIOD_WINDOW = "window"  # explicit start/end dates; label is derived from the range

PERIOD_LABELS = {
    PERIOD_LAST_WEEK: "Last week",
    PERIOD_LAST_SPRINT: "Last sprint",
    PERIOD_LAST_MONTH: "Last month (~2 sprints)",
    PERIOD_QUARTER: "Whole quarter",
    PERIOD_WINDOW: "Custom range",
}

# ── Report data sources ──────────────────────────────────────────────────────
# Canonical source tokens, per component. "azuredevops" is canonical here
# because it is what DeliveredItem.source has always persisted; analysis says
# "azdevops" and standup "azure_devops", so the normalizer accepts those (and
# "azdo") as aliases rather than forcing every caller to agree.
DELIVERY_JIRA = "jira"
DELIVERY_AZDO = "azuredevops"
SOURCE_COMPONENTS = {
    "delivery": (DELIVERY_JIRA, DELIVERY_AZDO),  # where completed tickets come from
    "code": ("github", DELIVERY_AZDO),  # merged PRs/commits — supporting context
    "docs": ("confluence", "notion"),  # doc updates — supporting context
}
_AZDO_ALIASES = frozenset({"azuredevops", "azdevops", "azure_devops", "azdo"})


def _canonical_source(token: str) -> str:
    """Map a source token to its canonical spelling ('' when unknown)."""
    t = (token or "").strip().lower()
    return DELIVERY_AZDO if t in _AZDO_ALIASES else t


def normalize_sources(sources: dict | None) -> tuple[set[str] | None, list[str], list[str]]:
    """Normalize a ``{"delivery": [...], "code": [...], "docs": [...]}`` selection.

    Returns ``(delivery, code, docs)``. A missing component key (or ``sources``
    itself None) means "auto": delivery becomes ``None`` (every configured
    tracker) and code/docs become the full allowed list — the engine intersects
    them with what is actually configured. An explicitly present key — even an
    empty list — is an exact selection, NOT auto. Alias spellings (azdevops /
    azure_devops / azdo) map to the canonical token; unknown tokens are
    dropped. Never raises.
    """
    if not isinstance(sources, dict):
        return None, list(SOURCE_COMPONENTS["code"]), list(SOURCE_COMPONENTS["docs"])

    def _clean(component: str) -> list[str]:
        allowed = SOURCE_COMPONENTS[component]
        if sources.get(component) is None:  # key absent → auto (everything allowed)
            return list(allowed)
        cleaned = [c for c in (_canonical_source(t) for t in sources[component]) if c in allowed]
        return list(dict.fromkeys(cleaned))  # de-dupe, keep canonical order stable

    delivery = None if sources.get("delivery") is None else set(_clean("delivery"))
    return delivery, _clean("code"), _clean("docs")


def available_report_sources() -> dict[str, list[str]]:
    """Which sources are configured, per component — best-effort, never raises.

    Delivery mirrors ``gather_delivered_work``'s own gates (a project id is what
    it fetches by); code/docs mirror the standup collector's requirements.
    """
    out: dict[str, list[str]] = {"delivery": [], "code": [], "docs": []}
    try:
        from yeaboi.config import get_azure_devops_project, get_jira_project_key

        if get_jira_project_key():
            out["delivery"].append(DELIVERY_JIRA)
        if get_azure_devops_project():
            out["delivery"].append(DELIVERY_AZDO)
    except Exception:  # noqa: BLE001 — availability probing is cosmetic
        logger.debug("reporting: delivery source probe failed", exc_info=True)
    try:
        from yeaboi.config import get_github_token, get_standup_github_repo

        if get_standup_github_repo() and get_github_token():
            out["code"].append("github")
    except Exception:  # noqa: BLE001
        logger.debug("reporting: github source probe failed", exc_info=True)
    try:
        from yeaboi.config import get_azure_devops_project, get_azure_devops_token

        if get_azure_devops_project() and get_azure_devops_token():
            out["code"].append(DELIVERY_AZDO)
    except Exception:  # noqa: BLE001
        logger.debug("reporting: azdo code source probe failed", exc_info=True)
    try:
        from yeaboi.config import get_confluence_base_url, get_confluence_token

        if get_confluence_base_url() and get_confluence_token():
            out["docs"].append("confluence")
    except Exception:  # noqa: BLE001
        logger.debug("reporting: confluence source probe failed", exc_info=True)
    try:
        from yeaboi.config import get_notion_token

        if get_notion_token():
            out["docs"].append("notion")
    except Exception:  # noqa: BLE001
        logger.debug("reporting: notion source probe failed", exc_info=True)
    return out


# Statuses that mean a ticket actually shipped. Compared case-insensitively; the
# tracker's raw status label is preserved on the DeliveredItem for display.
_COMPLETED_STATUSES = frozenset(
    {"done", "closed", "resolved", "released", "completed", "shipped", "accepted", "deployed"}
)


def _is_completed(status: str) -> bool:
    """Return True when a tracker status label means the work is delivered."""
    return (status or "").strip().lower() in _COMPLETED_STATUSES


# Canonical ticket rows in the activity feeds — jira emits kind="issue", azdo
# kind="work_item"; everything else (update/comment/wip/…) is derived activity.
_TICKET_KINDS = ("issue", "work_item")


def _emit(on_progress, message: str) -> None:
    """Best-effort progress callback — a broken callback must never kill the gather."""
    if on_progress is None:
        return
    try:
        on_progress(message)
    except Exception:  # noqa: BLE001 — progress is cosmetic
        logger.debug("reporting: on_progress callback failed", exc_info=True)


def _collect_items(jira_project: str, azdo_project: str, days: int, *, on_progress=None) -> list[dict]:
    """Fetch recent activity from Jira + AzDO over ``days``; tag each with its source."""
    items: list[dict] = []
    if jira_project:
        try:
            from yeaboi.tools.jira import jira_recent_activity

            _emit(on_progress, "Fetching completed work from Jira…")
            for it in jira_recent_activity(jira_project, days=days):
                it = dict(it)
                it["source"] = "jira"
                items.append(it)
        except ImportError:
            logger.warning("Jira SDK not installed — skipping Jira activity")
        except Exception as e:  # noqa: BLE001 — activity is best-effort
            logger.warning("Jira activity failed: %s", e)
    if azdo_project:
        try:
            from yeaboi.tools.azure_devops import azdevops_recent_activity

            _emit(on_progress, "Fetching completed work from Azure DevOps…")
            for it in azdevops_recent_activity(azdo_project, days=days):
                it = dict(it)
                it["source"] = "azuredevops"
                items.append(it)
        except ImportError:
            logger.warning("Azure DevOps SDK not installed — skipping AzDO activity")
        except Exception as e:  # noqa: BLE001 — activity is best-effort
            logger.warning("Azure DevOps activity failed: %s", e)
    return items


def period_days(period: str, *, sprint_length_weeks: int = 2) -> int:
    """Return the look-back window in days for a reporting ``period``.

    "Last sprint" = one sprint length; "Last month" = ~2 sprints (min 28 days) so a
    one-week-sprint team still gets a sensible month-ish window.
    """
    try:
        weeks = int(sprint_length_weeks or 2)
    except (TypeError, ValueError):
        weeks = 2
    weeks = max(1, weeks)
    if period == PERIOD_LAST_WEEK:
        return 7
    if period == PERIOD_LAST_MONTH:
        return max(28, 2 * weeks * 7)
    return max(7, weeks * 7)


def _within_window(timestamp: str, window_start: str, window_end: str) -> bool:
    """Best-effort ISO-date clamp for activity rows against an explicit window.

    The fetchers are lookback-from-today, so a window that ends in the past needs
    items completed after ``window_end`` dropped. Undated/unparseable rows are
    kept — losing real delivered work silently is worse than slight date fuzz.
    """
    day = (timestamp or "").strip()[:10]
    try:
        date.fromisoformat(day)
    except ValueError:
        return True
    return (not window_start or day >= window_start) and (not window_end or day <= window_end)


def gather_delivered_work(
    period: str,
    *,
    state: dict | None = None,
    jira_project: str = "",
    azdo_project: str = "",
    days_override: int | None = None,
    delivery_sources: set[str] | None = None,
    window_start: str = "",
    window_end: str = "",
    on_progress=None,
) -> tuple[list[DeliveredItem], list[str], list[str]]:
    """Return the team's completed tickets over ``period``.

    Args:
        period: one of the PERIOD_* constants (last_week / last_sprint / last_month /
            quarter / window).
        state: saved session state (for sprint length); may be None.
        jira_project / azdo_project: tracker identifiers (resolved from config if unset).
        days_override: when set (quarter / custom-window report), use this exact
            look-back window in days instead of deriving it from ``period``, and skip
            the active-sprint probe (the caller already knows the window).
        delivery_sources: canonical tracker tokens ("jira" / "azuredevops") to
            actually fetch from; ``None`` means every configured tracker. An
            unselected tracker is never fetched even when configured.
        window_start / window_end: explicit ISO date bounds (custom-window report).
            The fetch itself is lookback-from-today, so completed rows outside the
            labelled window (e.g. finished after a past ``window_end``) are dropped
            here; undated rows are kept.
        on_progress: optional callable(str) for live status lines (best-effort; a
            failing callback is swallowed).

    Returns:
        ``(items, sprint_names, warnings)`` — the completed DeliveredItems, the
        active sprint name(s) seen (best-effort; empty when ``days_override`` is set),
        and any warnings (e.g. no tracker configured) to surface on the report.
    """
    state = state or {}
    warnings: list[str] = []
    if not jira_project and not azdo_project:
        from yeaboi.config import get_azure_devops_project, get_jira_project_key

        jira_project = get_jira_project_key() or ""
        azdo_project = get_azure_devops_project() or ""

    if not jira_project and not azdo_project:
        logger.info("gather_delivered_work: no tracker configured")
        return [], [], ["No Jira or Azure DevOps board configured — connect one in Settings to report delivered work."]

    if delivery_sources is not None:
        # Blank the project id for any unselected tracker so it is never fetched.
        if DELIVERY_JIRA not in delivery_sources:
            jira_project = ""
        if DELIVERY_AZDO not in delivery_sources:
            azdo_project = ""
        if not jira_project and not azdo_project:
            logger.info("gather_delivered_work: selection excludes every configured tracker")
            return [], [], ["Selected ticketing source(s) are not configured — nothing to report."]

    sprint_names: list[str] = []
    if days_override is not None:
        days = max(1, days_override)
        logger.info("gather_delivered_work: period=%s window=%dd (explicit)", period, days)
    else:
        try:
            length_weeks = int(state.get("sprint_length_weeks") or 2)
        except (TypeError, ValueError):
            length_weeks = 2
        days = period_days(period, sprint_length_weeks=length_weeks)
        logger.info("gather_delivered_work: period=%s window=%dd", period, days)

        # Best-effort active-sprint name(s) for framing (reuses the standup helper).
        try:
            from yeaboi.standup import sprint_context

            _emit(on_progress, "Reading sprint context…")
            ctx = sprint_context.gather(state, jira_project=jira_project, azdo_project=azdo_project)
            if ctx.sprint_name:
                sprint_names.append(ctx.sprint_name)
        except Exception as e:  # noqa: BLE001 — sprint context is best-effort
            logger.warning("sprint_context gather failed (non-fatal): %s", e)

    raw = _collect_items(jira_project, azdo_project, days, on_progress=on_progress)
    completed = [it for it in raw if _is_completed(it.get("status", ""))]
    if window_start or window_end:
        in_window = [it for it in completed if _within_window(str(it.get("timestamp", "")), window_start, window_end)]
        if len(in_window) < len(completed):
            logger.info(
                "gather_delivered_work: dropped %d completed row(s) outside %s..%s",
                len(completed) - len(in_window),
                window_start or "…",
                window_end or "…",
            )
        completed = in_window
    # The activity feeds emit BOTH the ticket row (kind issue/work_item) and changelog
    # rows like "moved KEY '…' to Done" (kind update) for the same completed ticket —
    # collapse to one row per key, preferring the clean ticket row, so the report
    # (and the "Items delivered" metric) doesn't double-count every ticket.
    best: dict[str, dict] = {}
    order: list[str] = []
    for it in completed:
        k = it.get("key") or it.get("title", "")
        if k not in best:
            best[k] = it
            order.append(k)
        elif it.get("kind") in _TICKET_KINDS and best[k].get("kind") not in _TICKET_KINDS:
            best[k] = it  # upgrade a changelog row to the real ticket row
    if len(order) < len(completed):
        logger.info("gather_delivered_work: collapsed %d duplicate activity row(s)", len(completed) - len(order))
    items = [
        DeliveredItem(
            key=best[k].get("key", ""),
            title=best[k].get("title", ""),
            status=best[k].get("status", ""),
            source=best[k].get("source", ""),
            assignee=(best[k].get("author", "") or "").strip(),
        )
        for k in order
    ]
    if raw and not items:
        warnings.append("Recent activity was found, but nothing is marked Done/Closed in this window yet.")
    _emit(on_progress, f"Found {len(items)} delivered item(s)")
    logger.info("gather_delivered_work: %d delivered item(s) of %d touched", len(items), len(raw))
    return items, sprint_names, warnings
