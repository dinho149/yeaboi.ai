"""Team ceremony history (Daily Standup + Retro) for Planning & Analysis.

# See docs: "Session Management" — SQLite persistence
# See docs: "Prompt Construction" — ARC framework (optional context sections)

Standup and Retro modes each persist a report per run to the shared
``~/.scrum-agent/sessions.db``. This module reads that history back **team-wide**
(across all sessions, retros prioritised project-first) and distils it into
deterministic signals the Planning analyzer / sprint planner and the Analysis
report can consume:

- **action_items** — deduped, unresolved retro action-item card texts (newest wins),
  fed to the story writer to seed the backlog (badged ``[Retro]``).
- **themes** — recurring "what went well" / "what didn't go well" topics.
- **cadence** — how often retros / standups actually run (from run timestamps).
- **confidence_trend** — average sprint-confidence from standups and its direction.

Design mirrors ``agent/repo_signals.py``: a graceful I/O entry point
(``gather_ceremony_context``) that never raises, plus pure helpers that are
trivially unit-testable. Cadence is derived from the *intervals between runs*, so
it needs no notion of "now" and is fully deterministic.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# Minimum times a normalised card text must recur to count as a "theme".
_THEME_MIN_COUNT = 2
# How many themes to surface per grid.
_MAX_THEMES = 5


@dataclass
class CeremonyContext:
    """Distilled, deterministic view of the team's recent standups + retros.

    Transient — never persisted. ``summary_md`` is the block injected into the
    planning prompts; ``action_items`` seed the backlog; the rest drives the
    Analysis "Ceremony Cadence & Trends" section.
    """

    summary_md: str = ""
    action_items: tuple[str, ...] = ()
    retro_count: int = 0
    standup_count: int = 0
    retro_cadence: str = ""
    standup_cadence: str = ""
    confidence_trend: str = ""
    went_well_themes: tuple[tuple[str, int], ...] = ()
    didnt_go_well_themes: tuple[tuple[str, int], ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.retro_count == 0 and self.standup_count == 0


# ---------------------------------------------------------------------------
# Pure helpers — no I/O, deterministic (no dependence on the current time).
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Lowercase + collapse whitespace so near-identical cards group together."""
    return " ".join((text or "").lower().split())


def _parse_ts(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z'."""
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _avg_interval_days(run_ats: list[str]) -> float | None:
    """Average gap in days between consecutive runs (order-independent).

    Returns None when fewer than two parseable timestamps are present.
    """
    stamps = sorted(ts for ts in (_parse_ts(r) for r in run_ats) if ts is not None)
    if len(stamps) < 2:
        return None
    gaps = [(b - a).total_seconds() / 86400.0 for a, b in zip(stamps, stamps[1:], strict=False)]
    return sum(gaps) / len(gaps) if gaps else None


def _describe_cadence(run_ats: list[str], noun: str) -> str:
    """Human phrase for how often something runs (e.g. "~every 14 days over 4 runs")."""
    count = len([r for r in run_ats if _parse_ts(r) is not None])
    if count == 0:
        return f"no {noun}s recorded"
    if count == 1:
        return f"1 {noun} recorded (not yet a cadence)"
    avg = _avg_interval_days(run_ats)
    if avg is None:
        return f"{count} {noun}s recorded"
    if avg < 1:
        freq = "roughly daily"
    elif avg <= 10:
        freq = f"~every {round(avg)} day(s)"
    else:
        freq = f"~every {round(avg / 7)} week(s)"
    return f"{freq} ({count} {noun}s)"


def _confidence_trend(history: list[dict]) -> tuple[str, int | None]:
    """Average sprint-confidence + direction from standup history (newest first).

    Compares the recent half to the older half. Returns (phrase, avg_pct) — or
    ("", None) when there isn't enough data.
    """
    pcts = [int(h.get("confidence_pct", 0)) for h in history if h.get("status", "success") == "success"]
    pcts = [p for p in pcts if p > 0]
    if not pcts:
        return "", None
    avg = round(sum(pcts) / len(pcts))
    if len(pcts) < 4:
        return f"{avg}% average confidence", avg
    # history is newest-first: the first half is the recent window.
    mid = len(pcts) // 2
    recent = sum(pcts[:mid]) / mid
    older = sum(pcts[mid:]) / (len(pcts) - mid)
    delta = recent - older
    direction = "improving" if delta >= 5 else ("declining" if delta <= -5 else "flat")
    return f"{avg}% average confidence, {direction}", avg


def _top_themes(reports, grid: str) -> tuple[tuple[str, int], ...]:
    """Most frequent (normalised) card texts in a grid across reports.

    Returns (representative_text, count) pairs for texts recurring at least
    ``_THEME_MIN_COUNT`` times, most frequent first, capped at ``_MAX_THEMES``.
    """
    counts: Counter[str] = Counter()
    representative: dict[str, str] = {}
    for report in reports:
        for card in report.cards:
            if card.grid != grid or not card.text.strip():
                continue
            key = _normalise(card.text)
            counts[key] += 1
            representative.setdefault(key, card.text.strip())
    themes = [(representative[k], n) for k, n in counts.most_common() if n >= _THEME_MIN_COUNT]
    return tuple(themes[:_MAX_THEMES])


def _dedup_action_items(reports) -> tuple[str, ...]:
    """Distinct *open* action-item card texts across reports, newest-report first.

    ``reports`` is newest-first; the first occurrence of each normalised text
    wins so the freshest wording is kept. AI-suggested and human cards both count.

    A later retro's ``carried_action_items`` records what happened to each of the
    previous retro's actions. Items the team marked ``done`` or ``not_relevant`` are
    resolved, so they're excluded here — Planning/Analysis only see what's still open
    (mirrors the Performance 1:1 open-actions loop). Pending / in-progress /
    carried-over items remain listed.
    """
    _resolved_statuses = ("done", "not_relevant")
    _open_statuses = ("pending", "in_progress", "carried_over")
    # Texts explicitly resolved in any retro's carry-forward review.
    resolved: set[str] = set()
    for report in reports:
        for card in getattr(report, "carried_action_items", ()):
            if getattr(card, "status", "") in _resolved_statuses:
                key = _normalise(card.text)
                if key:
                    resolved.add(key)

    seen: set[str] = set()
    out: list[str] = []

    def _emit(text: str) -> None:
        key = _normalise(text)
        if not key or key in seen or key in resolved:
            return
        seen.add(key)
        out.append(text.strip())

    for report in reports:
        # Grid action items (AI-suggested + human).
        for card in report.cards:
            if card.grid == "action_items":
                _emit(card.text)
        # Items the team explicitly kept open in the review column but may never have
        # re-added to the grid (e.g. "Carried Over" without clicking Generate) — still open.
        for card in getattr(report, "carried_action_items", ()):
            if getattr(card, "status", "") in _open_statuses:
                _emit(card.text)
    return tuple(out)


def _bullets(items) -> str:
    return "\n".join(f"- {it}" for it in items)


def format_ceremony_history_md(ctx: CeremonyContext) -> str:
    """Render a CeremonyContext into the markdown block injected into prompts."""
    if ctx.is_empty:
        return ""
    parts: list[str] = []
    if ctx.action_items:
        parts.append("**Open retro action items:**\n" + _bullets(ctx.action_items))
    if ctx.didnt_go_well_themes:
        parts.append(
            "**Recurring pain points (retro 'didn't go well'):**\n"
            + _bullets(f"{t} ({n}×)" for t, n in ctx.didnt_go_well_themes)
        )
    if ctx.went_well_themes:
        parts.append(
            "**What's been working (retro 'went well'):**\n" + _bullets(f"{t} ({n}×)" for t, n in ctx.went_well_themes)
        )
    if ctx.confidence_trend:
        parts.append(f"**Recent standup confidence:** {ctx.confidence_trend}.")
    cadence_bits = [b for b in (ctx.retro_cadence, ctx.standup_cadence) if b]
    if cadence_bits:
        parts.append("**Cadence:** retros " + ctx.retro_cadence + "; standups " + ctx.standup_cadence + ".")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# I/O entry point — graceful (never raises); mirrors repo_signals.scan_*.
# ---------------------------------------------------------------------------


def gather_ceremony_context(
    project_name: str = "", *, retro_limit: int = 5, standup_limit: int = 10
) -> CeremonyContext:
    """Read the team's recent retros + standups and distil them (team-wide).

    Retros are fetched project-first (matching ``project_name`` sort ahead of
    others); standups are recency-based (their table has no project column).
    Graceful: a missing DB / empty tables / any error yields an empty context —
    planning/analysis then behave exactly as before.

    # See docs: "Session Management" — SQLite persistence
    """
    try:
        from yeaboi.config import get_sessions_db
        from yeaboi.retro.store import RetroStore
        from yeaboi.standup.store import StandupStore

        db_path = get_sessions_db()
        if not db_path.exists():
            return CeremonyContext()

        with RetroStore(db_path) as rstore:
            retros = rstore.get_recent_reports(retro_limit, project_name)
            retro_hist = rstore.get_all_history(100)
        with StandupStore(db_path) as sstore:
            standups = sstore.get_recent_reports(standup_limit)
            standup_hist = sstore.get_all_history(100)
    except Exception:  # noqa: BLE001 — ceremony history is best-effort; never abort a plan
        logger.debug("gather_ceremony_context failed (non-fatal)", exc_info=True)
        return CeremonyContext()

    ctx = CeremonyContext(
        action_items=_dedup_action_items(retros),
        retro_count=len(retros),
        standup_count=len(standups),
        retro_cadence=_describe_cadence([h["run_at"] for h in retro_hist], "retro"),
        standup_cadence=_describe_cadence([h["run_at"] for h in standup_hist], "standup"),
        confidence_trend=_confidence_trend(standup_hist)[0],
        went_well_themes=_top_themes(retros, "went_well"),
        didnt_go_well_themes=_top_themes(retros, "didnt_go_well"),
    )
    ctx.summary_md = format_ceremony_history_md(ctx)
    logger.info(
        "ceremony_history: %d retro(s), %d standup(s), %d action item(s)",
        ctx.retro_count,
        ctx.standup_count,
        len(ctx.action_items),
    )
    return ctx
