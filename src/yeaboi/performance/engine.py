"""Performance engine — 1:1 prep, 1:1 completion, and 6-month review pipelines.

Like the standup engine, these are standalone pipelines (NOT LangGraph nodes):
each is one deterministic gather step + a single LLM call following the same
parse → fallback → format convention the graph nodes use (agent/nodes.py). An LLM
auth/billing failure is never re-raised — it becomes a user-facing *warning* and a
deterministic fallback artifact, so the page always renders something useful.

Pipelines:
  run_one_on_one_prep(engineer)   → gather sprint activity + carried actions → LLM → OneOnOnePrep
  complete_one_on_one(engineer, transcript) → LLM → OneOnOneRecord → email (SMTP) → store
  run_six_month_review(engineer)  → gather 1:1s + delivery + ceremony + notes → LLM → SixMonthReview

# See docs: "The ReAct Loop" — using the LLM outside the main graph
# See docs: "Prompt Construction" — the performance prompts
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import date, timedelta

from yeaboi.agent.state import (
    EngineerActivity,
    OneOnOnePrep,
    OneOnOneRecord,
    SixMonthReview,
)
from yeaboi.performance import evidence as evidence_mod
from yeaboi.performance.evidence import (
    COVERED,
    FAILED,
    NOT_CONFIGURED,
    PARTIAL,
    SOURCE_ANALYSIS,
    SOURCE_CODE,
    SOURCE_DELIVERY,
    SOURCE_STANDUP,
    SOURCE_TICKETS,
)
from yeaboi.performance.store import PerformanceStore

logger = logging.getLogger(__name__)

# How far back a 1:1 prep reads the other modes' history. Wider than the two
# sprints of tickets it quotes, so a practice signal or a retro action item from
# last month still surfaces in the conversation.
_PREP_EVIDENCE_DAYS = 60
# How old the latest prep may be and still count as *this* 1:1's prep. Wide
# enough for a monthly cadence that slipped, narrow enough that last quarter's
# scan never becomes this meeting's evidence.
_CARRY_PREP_DAYS = 45

# Phase ids the engine owns, on top of the per-source ones evidence.py emits.
# A caller's checklist keys on these (see the performance page); the labels ride
# along for consumers that render one event standalone.
PHASE_MODEL = "model"
PHASE_SAVE = "save"
PHASE_PRIOR = "prior"
PHASE_EMAIL = "email"
_PHASE_LABELS = {
    PHASE_MODEL: "Ask the model",
    PHASE_SAVE: "Save & export",
    PHASE_PRIOR: "Read the prior prep",
    PHASE_EMAIL: "Send the summary",
}


def _emit(on_progress, phase: str, status: str, *, detail: str = "") -> None:
    """Send one engine-phase lifecycle event. None-safe, so pipelines emit freely."""
    from yeaboi.analysis.progress import send_component_progress

    send_component_progress(
        on_progress,
        component_id=phase,
        label=_PHASE_LABELS.get(phase, phase),
        status=status,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Shared LLM helpers (parse → fallback)
# ---------------------------------------------------------------------------


# The chain write is best-effort, but never silently absent (same contract as
# the standup's audit trail): a failed write becomes a warning on the artifact.
_AUDIT_WARNING = "Audit trail not recorded for this run — see logs; the artifact itself is unaffected."


def _audit(artifact, record_fn, *args, **kwargs):
    """Append one decision record; on failure return the artifact re-warned."""
    try:
        record_fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 — an audit write must never fail the run
        logger.warning("performance: provenance recording failed", exc_info=True)
        from dataclasses import replace

        return replace(artifact, warnings=artifact.warnings + (_AUDIT_WARNING,))
    return artifact


def _parse_json_response(raw: str) -> dict:
    """Extract a JSON object from an LLM response, tolerating markdown fences."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("performance: could not parse LLM JSON response")
        return {}


def _str_list(value) -> tuple[str, ...]:
    """Coerce an LLM field into a tuple of clean strings (tolerant of bad shapes)."""
    if not isinstance(value, list):
        return ()
    return tuple(str(v).strip() for v in value if str(v).strip())


def _invoke_llm(prompt: str, *, what: str, images: Sequence[str] = ()) -> tuple[dict, list[str]]:
    """Run one LLM call for ``prompt``; return (parsed_json, warnings).

    Returns ({}, [warning]) on any non-configured / auth / request failure so the
    caller can fall back deterministically — the engine never crashes on LLM issues.

    images: optional pasted-screenshot file paths, attached as multimodal image
        blocks (see agent/llm.py:invoke_with_images — degrades to text-only if
        the model rejects them or the files are gone).
    """
    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("performance[%s]: LLM not configured (%s)", what, why)
        return {}, [f"AI output unavailable — {why}."]

    # invoke_json tracks usage + turns on JSON mode + re-asks once on bad JSON.
    # See docs: "Local Mode (Ollama)" — reliability layer.
    from yeaboi.agent.llm import invoke_json
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error, _local_llm_hint

    try:
        logger.info("performance[%s]: invoking LLM (%d image(s))", what, len(images))
        response = invoke_json(prompt, temperature=0.2, image_paths=images)
        return _parse_json_response(response.content), []
    except Exception as exc:  # noqa: BLE001 — turn any LLM failure into a warning + fallback
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("performance[%s]: LLM auth/billing error: %s", what, exc)
            return {}, ["AI output unavailable — API key invalid or billing issue."]
        local_hint = _local_llm_hint(exc)
        if local_hint:
            logger.warning("performance[%s]: local Ollama failure: %s", what, exc)
            return {}, [f"AI output unavailable — {local_hint}"]
        logger.warning("performance[%s]: LLM request failed: %s", what, exc)
        return {}, ["AI output unavailable — LLM request failed (see logs)."]


def _load_state(session_id: str, db_path) -> dict:
    """Best-effort load of a session's ScrumState (for sprint length/context)."""
    if not session_id:
        return {}
    try:
        from yeaboi.sessions import SessionStore

        with SessionStore(db_path) as sessions:
            return sessions.load_state(session_id) or {}
    except Exception as e:  # noqa: BLE001 — state is optional
        logger.warning("performance: could not load session state: %s", e)
        return {}


def _resolve_db_path(db_path):
    if db_path is not None:
        return db_path
    from yeaboi.paths import get_db_path

    return get_db_path()


# ---------------------------------------------------------------------------
# 1:1 Prep
# ---------------------------------------------------------------------------


def _fallback_prep(
    engineer: str, today: str, activity: EngineerActivity, carried: tuple[str, ...], warnings: list[str]
) -> OneOnOnePrep:
    """Deterministic 1:1 prep when the LLM is unavailable — evidence, not analysis."""
    titles = [f"{s.key} {s.title}".strip() for s in activity.stories[:8]]
    points = [f"Discuss progress on: {t}" for t in titles[:4]] or ["Review recent work and blockers."]
    summary = (
        f"Worked on {activity.total_items} ticket(s) this sprint window."
        if activity.total_items
        else "No tracked tickets found for this engineer in the recent window."
    )
    return OneOnOnePrep(
        engineer=engineer,
        date=today,
        talking_points=tuple(points + list(carried)),
        carried_action_items=carried,
        activity_summary=summary,
        warnings=tuple(warnings),
    )


def run_one_on_one_prep(
    engineer: str,
    *,
    session_id: str = "",
    jira_project: str = "",
    azdo_project: str = "",
    deep_scan: bool = False,
    db_path=None,
    today: date | None = None,
    on_progress=None,
) -> OneOnOnePrep:
    """Generate 1:1 prep for ``engineer`` from every source that knows them.

    Gathers their tickets, the per-member code/documentation/self-report evidence
    saved by standup, their practice signals, the team analysis metrics, and their
    retro and poker history, pulls the open action items from their most recent
    completed 1:1, and asks the LLM for structured talking points / feedback /
    goals / gaps / improvements. Persists the prep.

    ``deep_scan`` permits one capped live scan for the stretch no saved standup
    covered; it costs API calls, so it is off by default. ``on_progress`` takes
    one lifecycle event per phase (see ``analysis/progress.py``) so a caller can
    draw a live checklist; it is an injection seam, never a behaviour switch.
    """
    today = today or date.today()
    date_str = today.isoformat()
    db_path = _resolve_db_path(db_path)
    logger.info("run_one_on_one_prep: engineer=%s session=%s deep_scan=%s", engineer, session_id, deep_scan)

    state = _load_state(session_id, db_path)
    evidence = evidence_mod.gather_engineer_evidence(
        engineer,
        period_start=(today - timedelta(days=_PREP_EVIDENCE_DAYS)).isoformat(),
        period_end=date_str,
        state=state,
        jira_project=jira_project,
        azdo_project=azdo_project,
        deep_scan=deep_scan,
        db_path=db_path,
        on_progress=on_progress,
    )
    activity = evidence.activity

    with PerformanceStore(db_path) as store:
        carried = store.get_open_action_items(engineer)
        notes = [n["note_text"] for n in store.get_notes(engineer)]

    from yeaboi.prompts.performance import get_one_on_one_prep_prompt

    prompt = get_one_on_one_prep_prompt(
        engineer=engineer,
        activity=asdict(activity),
        open_action_items=list(carried),
        notes=notes,
        evidence_md=evidence.summary_md,
        coverage_md=evidence_mod.format_coverage_md(evidence),
    )
    _emit(on_progress, PHASE_MODEL, "running")
    parsed, warnings = _invoke_llm(prompt, what="1:1 prep")
    _emit(
        on_progress,
        PHASE_MODEL,
        "completed" if parsed else "fallback",
        detail="" if parsed else warnings[0] if warnings else "",
    )

    if not parsed:
        prep = _fallback_prep(engineer, date_str, activity, carried, warnings)
    else:
        talking = _str_list(parsed.get("talking_points"))
        # Guarantee carried actions surface even if the LLM dropped them.
        for a in carried:
            if a not in talking:
                talking = talking + (a,)
        prep = OneOnOnePrep(
            engineer=engineer,
            date=date_str,
            talking_points=talking,
            feedback=_str_list(parsed.get("feedback")),
            goals=_str_list(parsed.get("goals")),
            gaps=_str_list(parsed.get("gaps")),
            improvements=_str_list(parsed.get("improvements")),
            carried_action_items=carried,
            activity_summary=(parsed.get("activity_summary") or "").strip(),
            warnings=tuple(warnings),
        )

    # Applied after the branch so the fallback artifact says what was scanned too
    # — a run with no LLM is exactly when the lead most needs to know.
    prep = _with_evidence(prep, evidence)

    _emit(on_progress, PHASE_SAVE, "running")
    with PerformanceStore(db_path) as store:
        store.record_prep(prep, session_id=session_id)

    from yeaboi.performance import provenance_log

    prep = _audit(prep, provenance_log.record_prep, db_path, prep, activity=activity, used_llm=bool(parsed))

    _export(prep, engineer, kind="prep")
    _emit(on_progress, PHASE_SAVE, "completed")
    logger.info("run_one_on_one_prep complete: engineer=%s points=%d", engineer, len(prep.talking_points))
    return prep


# ---------------------------------------------------------------------------
# 1:1 Completion
# ---------------------------------------------------------------------------


def _email_phase(delivery_state: str) -> tuple[str, str]:
    """The summary email's delivery_state as a (progress status, detail) pair."""
    return {
        "sent": ("completed", "Summary emailed."),
        "not_configured": ("no_data", "SMTP not configured — nothing was sent."),
    }.get(delivery_state, ("failed", "The summary email could not be sent."))


def _fallback_completion(engineer: str, today: str, transcript: str, warnings: list[str]) -> OneOnOneRecord:
    """Deterministic completion when the LLM is unavailable — keep the transcript."""
    return OneOnOneRecord(
        engineer=engineer,
        date=today,
        transcript=transcript,
        email_subject=f"1:1 follow-up — {today}",
        email_summary=(
            f"Hi {engineer},\n\nThanks for the 1:1 today. (An AI summary could not be generated — "
            "the raw notes are saved.)\n\nBest,\nYour manager"
        ),
        warnings=tuple(warnings),
    )


def complete_one_on_one(
    engineer: str,
    transcript: str,
    *,
    session_id: str = "",
    deliver: bool = True,
    recipients: list[str] | None = None,
    db_path=None,
    today: date | None = None,
    images: Sequence[str] = (),
    on_progress=None,
) -> OneOnOneRecord:
    """Turn a 1:1 transcript into an email summary + tracked action items.

    Runs one LLM call to produce the email + actions, records the completion (so
    the action items flow into the next prep), and — when ``deliver`` is set —
    emails the summary via SMTP. Delivery is best-effort; an SMTP failure becomes a
    warning, never a crash.

    images: screenshots the lead pasted (Ctrl+V) alongside the transcript —
        attached to the summarising LLM call as multimodal image blocks.
        Consumed immediately; not persisted with the record.
    """
    today = today or date.today()
    date_str = today.isoformat()
    db_path = _resolve_db_path(db_path)
    logger.info("complete_one_on_one: engineer=%s session=%s deliver=%s", engineer, session_id, deliver)

    if not (transcript or "").strip():
        logger.warning("complete_one_on_one: empty transcript for %s", engineer)
        return _fallback_completion(engineer, date_str, transcript, ["No transcript provided."])

    _emit(on_progress, PHASE_PRIOR, "running")
    with PerformanceStore(db_path) as store:
        prior_prep = store.get_latest_prep(engineer)
    _emit(
        on_progress,
        PHASE_PRIOR,
        "completed" if prior_prep else "no_data",
        detail=f"Prep from {prior_prep.date}." if prior_prep else "No earlier prep to carry actions from.",
    )

    from yeaboi.prompts.performance import get_one_on_one_completion_prompt

    prompt = get_one_on_one_completion_prompt(
        engineer=engineer,
        transcript=transcript,
        prior_prep=asdict(prior_prep) if prior_prep else None,
    )
    _emit(on_progress, PHASE_MODEL, "running")
    parsed, warnings = _invoke_llm(prompt, what="1:1 completion", images=images)
    _emit(
        on_progress,
        PHASE_MODEL,
        "completed" if parsed else "fallback",
        detail="" if parsed else warnings[0] if warnings else "",
    )

    if not parsed:
        record = _fallback_completion(engineer, date_str, transcript, warnings)
    else:
        record = OneOnOneRecord(
            engineer=engineer,
            date=date_str,
            transcript=transcript,
            email_subject=(parsed.get("email_subject") or f"1:1 follow-up — {date_str}").strip(),
            email_summary=(parsed.get("email_summary") or "").strip(),
            action_items=_str_list(parsed.get("action_items")),
            highlights=_str_list(parsed.get("highlights")),
            warnings=tuple(warnings),
        )

    # This is the artifact that gets emailed to the engineer, so it is the one
    # that most needs to say what it was based on. The prep already gathered it;
    # carrying it costs nothing and is empty when there was no prior prep.
    record = _carry_evidence(record, prior_prep)

    # Deliver the summary email (best-effort). A missing SMTP config is surfaced as
    # a warning on the returned record so the lead knows it wasn't sent.
    if deliver:
        _emit(on_progress, PHASE_EMAIL, "running")
        try:
            from yeaboi.performance.delivery import deliver_completion_email

            state = deliver_completion_email(record, recipients=recipients)
            if state == "not_configured":
                record = _with_warning(record, "Summary email not sent — SMTP not configured (see .env).")
            elif state == "failed":
                record = _with_warning(record, "Summary email failed to send (see logs).")
            record = replace(record, delivery_state=state)
        except Exception as e:  # noqa: BLE001 — delivery never crashes the run
            logger.error("complete_one_on_one: email delivery raised: %s", e)
            record = _with_warning(record, "Summary email failed to send (see logs).")
            record = replace(record, delivery_state="failed")
        status, detail = _email_phase(record.delivery_state)
        _emit(on_progress, PHASE_EMAIL, status, detail=detail)
    else:
        _emit(on_progress, PHASE_EMAIL, "no_data", detail="Delivery was not requested.")

    _emit(on_progress, PHASE_SAVE, "running")
    with PerformanceStore(db_path) as store:
        store.record_completion(record, session_id=session_id)

    from yeaboi.performance import provenance_log

    record = _audit(record, provenance_log.record_completion, db_path, record, used_llm=bool(parsed))

    _export(record, engineer, kind="completion")
    _emit(on_progress, PHASE_SAVE, "completed")
    logger.info("complete_one_on_one complete: engineer=%s actions=%d", engineer, len(record.action_items))
    return record


def _carry_evidence(record: OneOnOneRecord, prior_prep) -> OneOnOneRecord:
    """Copy a recent prep's evidence onto the completion it was run off.

    Not re-gathered: the prep for this 1:1 already paid for it, and a summary
    that cites a different scan from the prep it followed would be worse than
    one that cites none.

    Bounded, because ``get_latest_prep`` knows nothing about which meeting a prep
    was for. Unbounded, a 1:1 run months after the last prep silently inherits
    that prep's numbers and evidence rows and prints them as facts about today —
    in the one artifact that gets emailed to the engineer. The prep's date rides
    along so the page can say where the numbers came from.

    ``section_states`` is deliberately not carried: its keys are the prep's
    sections, and nothing on a record ever looks them up.
    """
    if prior_prep is None:
        return record
    prep_date = str(getattr(prior_prep, "date", "") or "")
    if not _within_days(prep_date, record.date, _CARRY_PREP_DAYS):
        logger.info(
            "complete_one_on_one: prep %s is not within %d days of %s — evidence not carried",
            prep_date or "(undated)",
            _CARRY_PREP_DAYS,
            record.date,
        )
        return record
    return replace(
        record,
        evidence_date=prep_date,
        evidence_sources=getattr(prior_prep, "evidence_sources", ()),
        evidence_coverage=getattr(prior_prep, "evidence_coverage", ()),
        metrics=getattr(prior_prep, "metrics", ()),
        evidence_items=getattr(prior_prep, "evidence_items", ()),
    )


def _within_days(earlier: str, later: str, days: int) -> bool:
    """True when two ISO dates are in order and no more than ``days`` apart.

    An unparseable date is False, not zero days: a prep whose date we cannot read
    is a prep we cannot say is this meeting's.
    """
    from yeaboi.timeparse import parse_date

    try:
        start = parse_date(earlier)
        end = parse_date(later)
    except (TypeError, ValueError):
        return False
    return 0 <= (end - start).days <= days


def _with_evidence(artifact, evidence):
    """Stamp an artifact with what fed it: the sources, the numbers, and the rows.

    Applied after the LLM branch so a fallback artifact carries it too — a run
    with no model is exactly when the lead most needs to know what was scanned.
    """
    return replace(
        artifact,
        evidence_sources=evidence.contributing_sources,
        evidence_coverage=tuple((c.source, c.state, c.detail) for c in evidence.coverage),
        metrics=evidence.metrics,
        evidence_items=evidence.groups,
        section_states=_section_states(artifact, evidence),
        activity=evidence.activity,
    )


# Which coverage source each narrative section is grounded in. A section with no
# items reads its state from here, so "the model found nothing" and "nobody
# looked" stop looking identical.
_SECTION_SOURCES: dict[str, tuple[str, ...]] = {
    "talking_points": (SOURCE_STANDUP, SOURCE_TICKETS),
    "feedback": (SOURCE_STANDUP, SOURCE_CODE),
    "goals": (SOURCE_TICKETS,),
    "gaps": (SOURCE_ANALYSIS, SOURCE_CODE),
    "improvements": (SOURCE_ANALYSIS, SOURCE_CODE),
    "carried_action_items": (),
    "strengths": (SOURCE_ANALYSIS, SOURCE_DELIVERY),
    "achievements": (SOURCE_DELIVERY, SOURCE_TICKETS),
    "areas_for_improvement": (SOURCE_ANALYSIS, SOURCE_CODE),
}


def _section_states(artifact, evidence) -> tuple[tuple[str, str, str], ...]:
    """(section, state, reason) for each narrative section the artifact carries.

    A section that produced items is ``covered``; an empty one inherits the
    weakest coverage of the sources that would have fed it.
    """
    by_source = {c.source: c for c in evidence.coverage}
    rank = {COVERED: 0, PARTIAL: 1, FAILED: 2, NOT_CONFIGURED: 3}
    rows: list[tuple[str, str, str]] = []
    for section, sources in _SECTION_SOURCES.items():
        if not hasattr(artifact, section):
            continue
        if getattr(artifact, section):
            rows.append((section, COVERED, ""))
            continue
        candidates = [by_source[s] for s in sources if s in by_source]
        if not candidates:
            rows.append((section, COVERED, ""))
            continue
        worst = max(candidates, key=lambda c: rank.get(c.state, 3))
        rows.append((section, worst.state, worst.detail if worst.state != COVERED else ""))
    return tuple(rows)


def _with_warning(record: OneOnOneRecord, warning: str) -> OneOnOneRecord:
    """Return a copy of ``record`` with ``warning`` appended (records are frozen)."""
    from dataclasses import replace

    return replace(record, warnings=record.warnings + (warning,))


# ---------------------------------------------------------------------------
# 6-month Review
# ---------------------------------------------------------------------------


def _distill_one_on_ones(records: list[OneOnOneRecord]) -> str:
    """Compact the engineer's recorded 1:1s into a prompt-friendly text block."""
    if not records:
        return ""
    blocks: list[str] = []
    for r in records:
        highlights = "; ".join(r.highlights) if r.highlights else ""
        actions = "; ".join(r.action_items) if r.action_items else ""
        summary = highlights or r.email_summary[:200]
        tail = f" | actions: {actions}" if actions else ""
        blocks.append(f"- {r.date}: {summary}{tail}")
    return "\n".join(blocks)


def _distill_delivery(activity: EngineerActivity) -> str:
    """Compact a long-window EngineerActivity into a delivery-history summary."""
    if not activity.total_items:
        return ""
    by_status: dict[str, int] = {}
    for s in activity.stories:
        by_status[s.status or "unknown"] = by_status.get(s.status or "unknown", 0) + 1
    status_str = ", ".join(f"{k}: {v}" for k, v in sorted(by_status.items()))
    sample = "; ".join(f"{s.key} {s.title}" for s in activity.stories[:10])
    return f"{activity.total_items} tickets touched ({status_str}). Examples: {sample}"


def _load_framework() -> tuple[str, str, bool]:
    """Return (framework_text, framework_label, is_custom_template).

    A ``PERFORMANCE_FRAMEWORK_PATH`` override is treated as a custom template to
    fill in; otherwise the bundled default competency framework is used.
    """
    from yeaboi.config import get_performance_framework_path

    custom_path = get_performance_framework_path()
    if custom_path:
        try:
            from pathlib import Path

            from yeaboi.fs_policy import resolve_and_check

            resolved = resolve_and_check(custom_path, mode="read", context="PERFORMANCE_FRAMEWORK_PATH")
            text = resolved.read_text(encoding="utf-8")
            return text, f"custom ({Path(custom_path).name})", True
        except Exception as e:  # noqa: BLE001 — fall back to bundled default (incl. sandbox denials)
            logger.warning("performance: could not read custom framework %s: %s", custom_path, e)
    try:
        from importlib.resources import files

        text = (files("yeaboi.performance.references") / "competency_framework.md").read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — framework is optional context
        logger.warning("performance: could not load bundled framework: %s", e)
        text = ""
    return text, "default", False


def _fallback_review(
    engineer: str, period_start: str, period_end: str, framework_label: str, warnings: list[str]
) -> SixMonthReview:
    """Deterministic review shell when the LLM is unavailable."""
    return SixMonthReview(
        engineer=engineer,
        period_start=period_start,
        period_end=period_end,
        overall="An AI-generated review could not be produced. The evidence has been gathered and saved; "
        "re-run once the LLM is configured.",
        framework_used=framework_label,
        warnings=tuple(warnings),
    )


def run_six_month_review(
    engineer: str,
    *,
    session_id: str = "",
    jira_project: str = "",
    azdo_project: str = "",
    period_months: int = 6,
    deep_scan: bool = False,
    db_path=None,
    today: date | None = None,
    on_progress=None,
) -> SixMonthReview:
    """Synthesize a performance review for ``engineer`` over the last ``period_months``.

    Pulls together: past 1:1 records, long-window Jira/AzDO delivery history, the
    per-member code / documentation / self-report evidence standup saved over the
    period, their practice signals, the team analysis metrics, their retro and
    poker history, team ceremony history, the lead's notes, and a competency
    framework, then asks the LLM for a structured review. Persists the review.

    ``deep_scan`` permits one capped live scan for the stretch no saved standup
    covered; it costs API calls, so it is off by default.
    """
    today = today or date.today()
    period_end = today.isoformat()
    period_start = (today - timedelta(days=period_months * 30)).isoformat()
    db_path = _resolve_db_path(db_path)
    logger.info("run_six_month_review: engineer=%s period=%s..%s", engineer, period_start, period_end)

    state = _load_state(session_id, db_path)

    with PerformanceStore(db_path) as store:
        completions = store.get_recent_completions(engineer, limit=20)
        notes = [n["note_text"] for n in store.get_notes(engineer)]

    # ~2 sprints/month over the period → enough look-back for delivery signal.
    evidence = evidence_mod.gather_engineer_evidence(
        engineer,
        period_start=period_start,
        period_end=period_end,
        state=state,
        jira_project=jira_project,
        azdo_project=azdo_project,
        sprints=max(2, period_months * 2),
        deep_scan=deep_scan,
        db_path=db_path,
        on_progress=on_progress,
    )
    delivery = evidence.activity

    ceremony_summary = ""
    try:
        from yeaboi.agent.ceremony_history import gather_ceremony_context

        ceremony_summary = gather_ceremony_context(state.get("project_name", "")).summary_md
    except Exception as e:  # noqa: BLE001 — ceremony context is best-effort
        logger.warning("run_six_month_review: ceremony context failed: %s", e)

    framework_text, framework_label, is_custom = _load_framework()

    from yeaboi.prompts.performance import get_six_month_review_prompt

    prompt = get_six_month_review_prompt(
        engineer=engineer,
        period_start=period_start,
        period_end=period_end,
        one_on_one_history=_distill_one_on_ones(completions),
        delivery_history=_distill_delivery(delivery),
        ceremony_summary=ceremony_summary,
        notes=notes,
        framework_text=framework_text,
        custom_template=is_custom,
        evidence_md=evidence.summary_md,
        coverage_md=evidence_mod.format_coverage_md(evidence),
    )
    _emit(on_progress, PHASE_MODEL, "running")
    parsed, warnings = _invoke_llm(prompt, what="6-month review")
    _emit(
        on_progress,
        PHASE_MODEL,
        "completed" if parsed else "fallback",
        detail="" if parsed else warnings[0] if warnings else "",
    )

    if not parsed:
        review = _fallback_review(engineer, period_start, period_end, framework_label, warnings)
    else:
        review = SixMonthReview(
            engineer=engineer,
            period_start=period_start,
            period_end=period_end,
            strengths=_str_list(parsed.get("strengths")),
            areas_for_improvement=_str_list(parsed.get("areas_for_improvement")),
            achievements=_str_list(parsed.get("achievements")),
            goals=_str_list(parsed.get("goals")),
            overall=(parsed.get("overall") or "").strip(),
            framework_used=framework_label,
            warnings=tuple(warnings),
        )

    review = _with_evidence(review, evidence)

    _emit(on_progress, PHASE_SAVE, "running")
    with PerformanceStore(db_path) as store:
        store.record_review(review, session_id=session_id)

    from yeaboi.performance import provenance_log

    review = _audit(
        review,
        provenance_log.record_review,
        db_path,
        review,
        delivery=delivery,
        one_on_one_dates=tuple(r.date for r in completions),
        used_llm=bool(parsed),
    )

    _export(review, engineer, kind="review")
    _emit(on_progress, PHASE_SAVE, "completed")
    logger.info("run_six_month_review complete: engineer=%s strengths=%d", engineer, len(review.strengths))
    return review


# ---------------------------------------------------------------------------
# Export (best-effort — never fails the run)
# ---------------------------------------------------------------------------


def _export(artifact, engineer: str, *, kind: str) -> None:
    """Auto-export an artifact to Markdown + HTML; log and swallow any I/O error."""
    try:
        from yeaboi.performance import export

        export.export_artifact(artifact, engineer=engineer, kind=kind)
    except Exception as e:  # noqa: BLE001 — export is best-effort
        logger.warning("performance export failed (%s): %s", kind, e)
