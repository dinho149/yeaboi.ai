"""Cross-mode estimation history for the poker AI perspective.

# See docs: "Session Management" — SQLite persistence
# See docs: "Prompt Construction" — ARC framework (optional context sections)

Every yeaboi mode persists its runs to the shared ``~/.yeaboi/sessions.db``,
which means a live poker session can ground the AI's take in what this team has
actually done — instead of reasoning from the ticket text alone:

- **analysis mode** → the team's calibration profile (velocity, estimation
  accuracy, what a 5-pointer really costs here) via ``TeamProfileStore``.
- **retro + standup** → recurring pain themes and the sprint-confidence trend,
  reused from ``agent/ceremony_history.py`` (the canonical gatherer this module
  is modeled on).
- **standup** → the ticket assignee's latest blockers and progress.
- **reporting** → recently *delivered* tickets (real keys), by the assignee and
  by title similarity, so the AI can cite comparable shipped work.
- **planning** → previously sized similar stories with their points confidence.

Performance mode is deliberately **excluded**: 1:1/review data is person-scoped
and confidential, and the poker AI note is visible to every voter on the board.

Design mirrors ``gather_ceremony_context``: one graceful I/O entry point that
**never raises** (a missing DB, an empty table, or a broken store just means
less context), plus pure deterministic helpers that are trivially unit-testable.
Everything gathered here is later injected into the LLM prompt as *data* — the
prompt frames it as untrusted alongside the ticket and votes, since retro cards
and standup text originate from LAN participants.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)

# Caps keep the context block small and the prompt cheap — a few high-signal
# lines beat pages of history (see ceremony_history's _MAX_THEMES precedent).
_MAX_PATTERNS = 2  # common_patterns quoted per calibration line
_MAX_PAIN_THEMES = 3  # recurring retro pain points
_MAX_DELIVERED = 3  # delivered items per bucket (by-assignee / similar-title)
_MAX_PLANNING_MATCHES = 2  # fuzzy-matched planned stories
_MAX_SESSIONS_SCANNED = 5  # newest planning sessions searched for similar stories
_MAX_LINE = 200  # per-line truncation of free text
_MAX_MD = 3000  # hard cap on the whole summary_md block
_MIN_TOKEN_OVERLAP = 2  # shared >=4-char tokens for a "similar title"

# Tiny stopword set so "add the user login page" and "update user login flow"
# match on {user, login} rather than on filler words.
_STOPWORDS = frozenset(
    {"this", "that", "with", "from", "into", "when", "then", "should", "would", "could", "have", "will", "page"}
)


@dataclass(frozen=True)
class PokerEstimationContext:
    """Distilled cross-mode history for one poker ticket.

    Transient — never persisted. ``summary_md`` is the block injected into the
    perspective prompt; the line tuples keep the sources separable for tests and
    for the deterministic fallback note.
    """

    summary_md: str = ""
    team_lines: tuple[str, ...] = ()  # velocity/accuracy/completion, spillover, confidence trend
    calibration_lines: tuple[str, ...] = ()  # one per point value with samples
    calibration_by_value: tuple[tuple[float, str], ...] = ()  # (point value, line) for the fallback
    assignee_lines: tuple[str, ...] = ()  # blockers + progress for the ticket's assignee
    delivery_lines: tuple[str, ...] = ()  # delivered-by-assignee + similar-title items (with keys)
    retro_lines: tuple[str, ...] = ()  # recurring pain themes
    planning_lines: tuple[str, ...] = ()  # fuzzy-matched planned stories + points confidence

    @property
    def is_empty(self) -> bool:
        return not (
            self.team_lines
            or self.calibration_lines
            or self.assignee_lines
            or self.delivery_lines
            or self.retro_lines
            or self.planning_lines
        )


# ---------------------------------------------------------------------------
# Pure helpers — no I/O, deterministic.
# ---------------------------------------------------------------------------


def _clip(text: str) -> str:
    """Collapse whitespace and truncate to _MAX_LINE (ellipsis when cut)."""
    flat = " ".join((text or "").split())
    return flat[:_MAX_LINE] + "..." if len(flat) > _MAX_LINE else flat


def _fmt_pts(x: float) -> str:
    """5.0 -> "5" (matches the engine's vote formatting)."""
    return str(int(x)) if x == int(x) else str(x)


def _project_key_for(source: str, key: str) -> str:
    """The tracker project a ticket belongs to — the TeamProfile lookup key.

    Jira keys embed it ("PROJ-123" -> "PROJ"); AzDO ids don't, so the project
    comes from configuration. Unknown sources yield "" (no profile lookup).
    """
    if source == "jira":
        return key.rsplit("-", 1)[0] if "-" in key else ""
    if source == "azdevops":
        try:
            from yeaboi.config import get_azure_devops_project

            return get_azure_devops_project() or ""
        except Exception:  # noqa: BLE001 — config problems must not break the gather
            return ""
    return ""


def _names_match(a: str, b: str) -> bool:
    """Loose person-name match: casefold containment either way, non-empty."""
    a, b = (a or "").casefold().strip(), (b or "").casefold().strip()
    return bool(a) and bool(b) and (a in b or b in a)


def _title_tokens(text: str) -> set[str]:
    """Meaningful lowercase words (>=4 chars, minus stopwords) for similarity."""
    words = re.findall(r"[a-z]{4,}", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS}


def _similar_title(a: str, b: str) -> bool:
    """Two titles are "similar" when they share >= _MIN_TOKEN_OVERLAP real words."""
    return len(_title_tokens(a) & _title_tokens(b)) >= _MIN_TOKEN_OVERLAP


def _team_lines(profile) -> tuple[str, ...]:
    """Velocity / accuracy / spillover one-liners from a TeamProfile."""
    lines: list[str] = []
    if profile.velocity_avg > 0:
        bits = [f"Velocity: {_fmt_pts(profile.velocity_avg)} pts/sprint"]
        if profile.velocity_stddev > 0:
            bits[0] += f" (±{_fmt_pts(profile.velocity_stddev)})"
        if profile.estimation_accuracy_pct > 0:
            bits.append(f"estimation accuracy {round(profile.estimation_accuracy_pct)}%")
        if profile.sprint_completion_rate > 0:
            bits.append(f"sprint completion {round(profile.sprint_completion_rate)}%")
        lines.append("; ".join(bits) + f" (from {profile.sample_sprints} analysed sprint(s)).")
    sp = profile.spillover
    if sp.carried_over_pct > 0:
        line = f"Spillover: {round(sp.carried_over_pct)}% of stories slip"
        if sp.avg_spillover_pts > 0:
            line += f" (~{_fmt_pts(sp.avg_spillover_pts)} pts/sprint)"
        if sp.most_common_spillover_reason:
            line += f"; most common reason: {_clip(sp.most_common_spillover_reason)}"
        lines.append(line + ".")
    return tuple(lines)


def _calibration_lines(profile) -> tuple[tuple[float, str], ...]:
    """(point value, line) per calibration with samples — what an N really costs."""
    out: list[tuple[float, str]] = []
    for cal in profile.point_calibrations:
        if cal.sample_count <= 0:
            continue
        line = f"{cal.point_value}-pt stories: avg cycle {cal.avg_cycle_time_days:.1f} days"
        if cal.overshoot_pct > 0:
            line += f", {round(cal.overshoot_pct)}% overshoot"
        if cal.typical_task_count > 0:
            line += f", ~{round(cal.typical_task_count)} tasks"
        line += f" (n={cal.sample_count})"
        if cal.common_patterns:
            line += "; typically: " + ", ".join(cal.common_patterns[:_MAX_PATTERNS])
        out.append((float(cal.point_value), line + "."))
    return tuple(out)


def _assignee_lines(report, assignee: str) -> tuple[str, ...]:
    """The ticket assignee's blockers + progress from the latest standup."""
    if report is None or not assignee:
        return ()
    for member in report.member_updates:
        if not _names_match(member.name, assignee):
            continue
        lines: list[str] = []
        if member.blockers:
            lines.append(
                f"{member.name} (ticket assignee) reported blockers in the latest standup: {_clip(member.blockers)}"
            )
        if member.ticketing_summary:
            lines.append(f"{member.name}'s recent ticket activity: {_clip(member.ticketing_summary)}")
        return tuple(lines)
    return ()


def _delivery_lines(delivered_items, assignee: str, summary: str, current_key: str) -> tuple[str, ...]:
    """Recently delivered tickets: by the assignee, then by title similarity."""
    by_assignee: list[str] = []
    similar: list[str] = []
    listed_keys: set[str] = {current_key} if current_key else set()
    for item in list(delivered_items)[:100]:
        if not item.key or item.key in listed_keys:
            continue
        if assignee and _names_match(item.assignee, assignee) and len(by_assignee) < _MAX_DELIVERED:
            by_assignee.append(
                f"Recently delivered by {item.assignee}: {item.key} '{_clip(item.title)}' ({item.status})."
            )
            listed_keys.add(item.key)
        elif _similar_title(item.title, summary) and len(similar) < _MAX_DELIVERED:
            who = f", assignee {item.assignee}" if item.assignee else ""
            similar.append(f"Similar delivered ticket: {item.key} '{_clip(item.title)}' ({item.status}{who}).")
            listed_keys.add(item.key)
    return tuple(by_assignee + similar)


def _planning_lines(stories, summary: str) -> tuple[str, ...]:
    """Previously planned stories with similar titles + their sizing confidence.

    ``stories`` are UserStory dataclasses from saved planning sessions. They
    carry no tracker keys (planning happens pre-tracker), so the match is fuzzy
    by title — the "similar story" phrasing flags the approximation to the LLM.
    """
    out: list[str] = []
    for story in stories:
        title = getattr(story, "title", "") or getattr(story, "goal", "")
        if not title or not _similar_title(title, summary):
            continue
        pts = int(getattr(story, "story_points", 0) or 0)
        line = f"Planning mode sized a similar story '{_clip(title)}' at {pts} pts"
        confidence = getattr(story, "points_confidence", "")
        if confidence:
            line += f" (confidence: {confidence})"
        rationale = getattr(story, "points_rationale", "")
        if rationale:
            line += f": {_clip(rationale)}"
        out.append(line + ".")
        if len(out) >= _MAX_PLANNING_MATCHES:
            break
    return tuple(out)


def _bullets(items) -> str:
    return "\n".join(f"- {it}" for it in items)


def format_poker_context_md(ctx: PokerEstimationContext) -> str:
    """Render the context into the markdown block injected into the prompt.

    Only non-empty sections render (ceremony_history convention); each heading
    names the yeaboi mode the data came from so the AI can cite its source.
    """
    if ctx.is_empty:
        return ""
    parts: list[str] = []
    if ctx.team_lines:
        parts.append("**Team estimation history (analysis mode):**\n" + _bullets(ctx.team_lines))
    if ctx.calibration_lines:
        parts.append("**Point-size calibration (analysis mode):**\n" + _bullets(ctx.calibration_lines))
    if ctx.assignee_lines:
        parts.append("**Assignee signals (latest standup):**\n" + _bullets(ctx.assignee_lines))
    if ctx.delivery_lines:
        parts.append("**Delivery history (reporting mode):**\n" + _bullets(ctx.delivery_lines))
    if ctx.retro_lines:
        parts.append("**Recurring pain points (retros):**\n" + _bullets(ctx.retro_lines))
    if ctx.planning_lines:
        parts.append("**Similar planned stories (planning mode):**\n" + _bullets(ctx.planning_lines))
    return "\n\n".join(parts)[:_MAX_MD]


# ---------------------------------------------------------------------------
# I/O entry point — graceful (never raises); mirrors gather_ceremony_context.
# ---------------------------------------------------------------------------


def gather_poker_context(ticket: dict, *, project_name: str = "") -> PokerEstimationContext:
    """Read the other modes' history relevant to one poker ticket. Never raises.

    Each source sits in its own try/except so one broken store doesn't cost the
    rest; any unexpected failure yields an empty context and the perspective
    behaves exactly as before. Demo tickets skip gathering entirely — their
    fake assignees would only produce noise against real history.

    # See docs: "Session Management" — SQLite persistence
    """
    ticket = ticket or {}
    source = str(ticket.get("source", ""))
    key = str(ticket.get("key", ""))
    summary = str(ticket.get("summary", ""))
    assignee = str(ticket.get("assignee", ""))
    if source == "demo":
        return PokerEstimationContext()

    try:
        from yeaboi.paths import get_db_path

        db_path = get_db_path()
        if not db_path.exists():
            return PokerEstimationContext()
    except Exception:  # noqa: BLE001 — context is best-effort; never abort the perspective
        logger.debug("gather_poker_context: db path unavailable (non-fatal)", exc_info=True)
        return PokerEstimationContext()

    team_lines: list[str] = []
    calibration_by_value: tuple[tuple[float, str], ...] = ()
    assignee_lines: tuple[str, ...] = ()
    delivery_lines: tuple[str, ...] = ()
    retro_lines: tuple[str, ...] = ()
    planning_lines: tuple[str, ...] = ()

    # Analysis mode: the team calibration profile for this tracker project.
    try:
        project_key = _project_key_for(source, key)
        if project_key:
            from yeaboi.team_profile import TeamProfileStore

            with TeamProfileStore(db_path) as store:
                profile = store.load_by_project(project_key, source)
            if profile is not None:
                team_lines.extend(_team_lines(profile))
                calibration_by_value = _calibration_lines(profile)
    except Exception:  # noqa: BLE001
        logger.debug("gather_poker_context: team profile read failed (non-fatal)", exc_info=True)

    # Retro + standup trend: reuse the canonical ceremony gatherer.
    try:
        from yeaboi.agent.ceremony_history import gather_ceremony_context

        ceremony = gather_ceremony_context(project_name)
        if ceremony.confidence_trend:
            team_lines.append(f"Recent standup sprint confidence: {ceremony.confidence_trend}.")
        retro_lines = tuple(
            f"{theme} ({count}× across recent retros)"
            for theme, count in ceremony.didnt_go_well_themes[:_MAX_PAIN_THEMES]
        )
    except Exception:  # noqa: BLE001
        logger.debug("gather_poker_context: ceremony read failed (non-fatal)", exc_info=True)

    # Standup: the ticket assignee's latest blockers + progress.
    try:
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as sstore:
            recent = sstore.get_recent_reports(1)
        assignee_lines = _assignee_lines(recent[0] if recent else None, assignee)
    except Exception:  # noqa: BLE001
        logger.debug("gather_poker_context: standup read failed (non-fatal)", exc_info=True)

    # Reporting: recently delivered tickets (real keys) for citable comparisons.
    try:
        from yeaboi.reporting.store import ReportingStore

        with ReportingStore(db_path) as rstore:
            delivery = rstore.get_latest_report()
        if delivery is not None:
            delivery_lines = _delivery_lines(delivery.delivered_items, assignee, summary, key)
    except Exception:  # noqa: BLE001
        logger.debug("gather_poker_context: reporting read failed (non-fatal)", exc_info=True)

    # Planning: similar stories this team already sized, with confidence.
    try:
        from yeaboi.sessions import SessionStore

        stories: list = []
        with SessionStore(db_path) as sessions:
            for meta in sessions.list_sessions()[:_MAX_SESSIONS_SCANNED]:
                state = sessions.load_state(meta["session_id"]) or {}
                stories.extend(state.get("stories") or [])
        planning_lines = _planning_lines(stories, summary)
    except Exception:  # noqa: BLE001
        logger.debug("gather_poker_context: planning read failed (non-fatal)", exc_info=True)

    ctx = PokerEstimationContext(
        team_lines=tuple(team_lines),
        calibration_lines=tuple(line for _, line in calibration_by_value),
        calibration_by_value=calibration_by_value,
        assignee_lines=assignee_lines,
        delivery_lines=delivery_lines,
        retro_lines=retro_lines,
        planning_lines=planning_lines,
    )
    ctx = replace(ctx, summary_md=format_poker_context_md(ctx))
    logger.info(
        "poker context gathered: team=%d calibration=%d assignee=%d delivery=%d retro=%d planning=%d",
        len(ctx.team_lines),
        len(ctx.calibration_lines),
        len(ctx.assignee_lines),
        len(ctx.delivery_lines),
        len(ctx.retro_lines),
        len(ctx.planning_lines),
    )
    return ctx
