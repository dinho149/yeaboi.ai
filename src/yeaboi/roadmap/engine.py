"""Roadmap engine — the quarterly-roadmap analysis pipeline.

Like the standup / reporting engines, this is a standalone pipeline (NOT a
LangGraph node): one deterministic ingest step + a single LLM call following
the same parse → fallback convention the graph nodes use (agent/nodes.py).
An LLM auth/billing failure is never re-raised — it becomes a user-facing
*warning* and a deterministic zero-project fallback, so the Roadmap card
always renders something and can offer Re-analyze.

Pipeline:
  run_roadmap_analysis(source) → ingest roadmap text (deterministic)
                               → LLM extracts + ranks + sizes projects
                               → RoadmapAnalysis → RoadmapStore.record_run

# See docs: "The ReAct Loop" — using the LLM outside the main graph
# See docs: "Prompt Construction" — the roadmap prompt
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from langchain_core.messages import HumanMessage

from yeaboi.agent.state import RoadmapAnalysis, RoadmapProject
from yeaboi.roadmap.ingest import RoadmapSource, ingest_source

logger = logging.getLogger(__name__)

# ``size`` value → run_session intake_mode. Unknown sizes coerce to "small" at
# parse time so this mapping is total.
_SIZE_TO_INTAKE_MODE = {"small": "small_project", "large": "smart"}


def intake_mode_for(project: RoadmapProject) -> str:
    """Map a project's LLM size classification to a planning intake mode."""
    return _SIZE_TO_INTAKE_MODE.get(project.size, "small_project")


# ---------------------------------------------------------------------------
# LLM helpers (parse → fallback) — mirrors reporting/engine.py
# ---------------------------------------------------------------------------


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
        logger.warning("roadmap: could not parse LLM JSON response")
        return {}


def _invoke_llm(prompt: str) -> tuple[dict, list[str]]:
    """Run one LLM call for ``prompt``; return (parsed_json, warnings).

    Returns ({}, [warning]) on any non-configured / auth / request failure so
    the caller can fall back deterministically — the engine never crashes on
    LLM issues.
    """
    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("roadmap: LLM not configured (%s)", why)
        return {}, [f"AI analysis unavailable — {why}."]

    from yeaboi.agent.llm import get_llm, track_usage
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error

    try:
        logger.info("roadmap: invoking LLM analysis pass")
        response = get_llm(temperature=0.3).invoke([HumanMessage(content=prompt)])
        track_usage(response)
        return _parse_json_response(response.content), []
    except Exception as exc:  # noqa: BLE001 — turn any LLM failure into a warning + fallback
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("roadmap: LLM auth/billing error: %s", exc)
            return {}, ["AI analysis unavailable — API key invalid or billing issue."]
        logger.warning("roadmap: LLM request failed: %s", exc)
        return {}, ["AI analysis unavailable — LLM request failed (see logs)."]


def _parse_projects(data: dict) -> tuple[RoadmapProject, ...]:
    """Coerce the LLM 'projects' field into RoadmapProject tuples (tolerant).

    Skips non-dict entries, coerces unknown sizes to "small", int-coerces
    priority, and sorts by priority with unranked (0) entries last.
    """
    raw = data.get("projects")
    if not isinstance(raw, list):
        return ()
    projects: list[RoadmapProject] = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "")).strip()
        if not name:
            continue
        size = str(p.get("size", "")).strip().lower()
        if size not in _SIZE_TO_INTAKE_MODE:
            size = "small"
        try:
            priority = int(p.get("priority", 0) or 0)
        except (TypeError, ValueError):
            priority = 0
        themes = p.get("themes")
        projects.append(
            RoadmapProject(
                name=name,
                description=str(p.get("description", "")).strip(),
                size=size,
                rationale=str(p.get("rationale", "")).strip(),
                priority=priority,
                themes=tuple(str(t).strip() for t in themes if str(t).strip()) if isinstance(themes, list) else (),
                quarter=str(p.get("quarter", "")).strip(),
            )
        )
    projects.sort(key=lambda pr: (pr.priority <= 0, pr.priority))
    return tuple(projects)


# ---------------------------------------------------------------------------
# Fallback + dry-run
# ---------------------------------------------------------------------------


def _fallback_analysis(source: RoadmapSource, label: str, warnings: list[str]) -> RoadmapAnalysis:
    """Deterministic zero-project analysis when ingestion or the LLM failed.

    Deliberately does NOT attempt heuristic project extraction — a wrong
    deterministic split would seed bad plans. The honest fallback is an empty
    list plus the warnings, so the user fixes the cause and hits Re-analyze.
    """
    return RoadmapAnalysis(
        source_type=source.source_type,
        source_locator=source.locator,
        source_label=label or source.label,
        summary="The roadmap could not be analyzed automatically — see the notices below, then Re-analyze.",
        projects=(),
        warnings=tuple(warnings),
        generated_at=datetime.now(UTC).isoformat(),
    )


def _dry_run_analysis(source: RoadmapSource) -> RoadmapAnalysis:
    """Canned analysis for --dry-run — no ingestion, no network, no LLM."""
    return RoadmapAnalysis(
        source_type=source.source_type or "local",
        source_locator=source.locator or "roadmap.md",
        source_label=source.label or "Q3 Roadmap (sample)",
        summary="Sample analysis: three initiatives targeting Q3 — one quick win, two multi-sprint epics.",
        projects=(
            RoadmapProject(
                name="Single Sign-On",
                description=(
                    "Add SSO across the web and mobile apps using the existing identity provider. "
                    "Covers SAML + OIDC, session migration for current users, and an admin toggle "
                    "per workspace. Target: end of Q3."
                ),
                size="large",
                rationale="Multi-team epic with auth migration risk — start first to leave soak time.",
                priority=1,
                themes=("Security",),
                quarter="Q3 2026",
            ),
            RoadmapProject(
                name="Checkout revamp",
                description=(
                    "Rebuild the checkout flow to reduce drop-off: one-page flow, saved payment "
                    "methods, and localized pricing. Depends on the payments API v2 already shipped."
                ),
                size="large",
                rationale="Multi-sprint frontend + backend work; start after SSO planning settles.",
                priority=2,
                themes=("Revenue",),
                quarter="Q3 2026",
            ),
            RoadmapProject(
                name="Fix onboarding emails",
                description=(
                    "Rewrite the three onboarding drip emails and fix the broken unsubscribe link. "
                    "Copy is already drafted in the marketing folder."
                ),
                size="small",
                rationale="1-2 tickets, one quick sprint — a fast win between the epics.",
                priority=3,
                themes=("Growth",),
                quarter="Q3 2026",
            ),
        ),
        warnings=(),
        generated_at=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_roadmap_analysis(
    source: RoadmapSource, *, db_path=None, dry_run: bool = False, on_progress=None
) -> RoadmapAnalysis:
    """Analyze the configured roadmap source into recommended projects.

    Ingests the roadmap text, runs one LLM call to extract/rank/size candidate
    projects, and records the result to roadmap_history (best-effort). Never
    raises on ingest/LLM failure — warnings + a deterministic fallback instead.

    on_progress: optional ``callable(str)`` invoked at each pipeline stage so a
        caller running this on a worker thread can show live progress (the
        ``run_standup(on_progress=...)`` precedent). Callback errors are ignored.
    """
    logger.info("run_roadmap_analysis: type=%s locator=%s dry_run=%s", source.source_type, source.locator, dry_run)

    def _report(msg: str) -> None:
        if on_progress is not None:
            try:
                on_progress(msg)
            except Exception:  # a progress UI bug must never break the pipeline
                logger.debug("run_roadmap_analysis: on_progress callback failed", exc_info=True)

    if dry_run:
        analysis = _dry_run_analysis(source)
        _record(analysis, db_path)
        return analysis

    _report("Reading the roadmap source…")
    text, label, warnings = ingest_source(source)
    if not text.strip():
        logger.warning("run_roadmap_analysis: no roadmap text ingested — falling back")
        analysis = _fallback_analysis(source, label, warnings)
        _record(analysis, db_path)
        return analysis

    from yeaboi.prompts.roadmap import get_roadmap_analysis_prompt

    prompt = get_roadmap_analysis_prompt(
        roadmap_text=text,
        source_label=label,
        today_iso=datetime.now(UTC).date().isoformat(),
    )
    _report("Analyzing with the AI — extracting and ranking projects…")
    parsed, llm_warnings = _invoke_llm(prompt)
    warnings = warnings + llm_warnings

    _report("Preparing recommendations…")
    projects = _parse_projects(parsed)
    if not projects:
        if not llm_warnings:
            warnings.append("The AI could not find concrete projects in the roadmap — check the document content.")
        analysis = _fallback_analysis(source, label, warnings)
        _record(analysis, db_path)
        return analysis

    analysis = RoadmapAnalysis(
        source_type=source.source_type,
        source_locator=source.locator,
        source_label=label,
        summary=str(parsed.get("summary", "")).strip(),
        projects=projects,
        warnings=tuple(warnings),
        generated_at=datetime.now(UTC).isoformat(),
    )
    logger.info("run_roadmap_analysis: %d project(s) extracted from %r", len(projects), label)
    _record(analysis, db_path)
    return analysis


def _record(analysis: RoadmapAnalysis, db_path) -> None:
    """Best-effort persist of the analysis run — a store failure never breaks the page."""
    if db_path is None:
        return
    try:
        from yeaboi.roadmap.store import RoadmapStore

        with RoadmapStore(db_path) as store:
            store.record_run(analysis)
    except Exception:
        logger.error("run_roadmap_analysis: failed to record run", exc_info=True)
