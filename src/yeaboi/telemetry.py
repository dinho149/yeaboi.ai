"""Opt-in anonymous telemetry for improving planning quality.

Collects anonymized, structural data from completed sessions and uploads
to S3 for later analysis. NO code, NO project names, NO PII — only
patterns (tech stacks, story counts, point distributions, accept/edit rates).

Disabled by default. Enable via:
  YEABOI_TELEMETRY=true   (legacy SCRUM_AGENT_TELEMETRY still honoured)

# See docs: "Architecture" — telemetry layer (opt-in)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import uuid
from dataclasses import asdict
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Prefer the yeaboi-branded var; fall back to the pre-rebrand SCRUM_AGENT_* name.
TELEMETRY_ENABLED = os.getenv("YEABOI_TELEMETRY", os.getenv("SCRUM_AGENT_TELEMETRY", "")).lower() in (
    "true",
    "1",
    "yes",
)
TELEMETRY_ENDPOINT = os.getenv(
    "YEABOI_TELEMETRY_URL",
    os.getenv(
        "SCRUM_AGENT_TELEMETRY_URL",
        "https://ykauzind6vf3vtlvwz5kru7ajq0cpgyd.lambda-url.eu-west-1.on.aws/",
    ),
)


def is_enabled() -> bool:
    """Check if telemetry is opted in."""
    return TELEMETRY_ENABLED


# ---------------------------------------------------------------------------
# Anonymization helpers
# ---------------------------------------------------------------------------


def _hash(value: str) -> str:
    """One-way hash for anonymization. Same input → same hash (for grouping)."""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _anonymize_text(text: str | None) -> str | None:
    """Replace actual text with length indicator. Preserves structure, removes content."""
    if not text:
        return None
    return f"[{len(text)} chars]"


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------


def build_telemetry_payload(graph_state: dict) -> dict | None:
    """Build an anonymized telemetry payload from a completed session.

    Returns None if there's nothing useful to send (e.g. session was abandoned).
    """
    from yeaboi import __version__
    from yeaboi.agent.state import (
        Feature,
        ProjectAnalysis,
        QuestionnaireState,
        Sprint,
        Task,
        UserStory,
    )

    analysis: ProjectAnalysis | None = graph_state.get("analysis")
    features: list[Feature] = graph_state.get("features", [])
    stories: list[UserStory] = graph_state.get("stories", [])
    tasks: list[Task] = graph_state.get("tasks", [])
    sprints: list[Sprint] = graph_state.get("sprints", [])
    questionnaire: QuestionnaireState | None = graph_state.get("questionnaire")

    # Nothing to report if no analysis was generated
    if not analysis:
        return None

    # --- Anonymized project metadata ---
    project = {
        "type": analysis.project_type,
        "tech_stack": list(analysis.tech_stack),
        "integrations": list(analysis.integrations),
        "sprint_length_weeks": analysis.sprint_length_weeks,
        "target_sprints": analysis.target_sprints,
        "goal_count": len(analysis.goals),
        "constraint_count": len(analysis.constraints),
        "risk_count": len(analysis.risks),
        "out_of_scope_count": len(analysis.out_of_scope),
        "skip_features": analysis.skip_features,
    }

    # Prompt quality if available
    if analysis.prompt_quality:
        project["prompt_quality_grade"] = analysis.prompt_quality.grade
        project["prompt_quality_score"] = analysis.prompt_quality.score

    # --- Questionnaire patterns ---
    intake = {}
    if questionnaire:
        q = asdict(questionnaire) if hasattr(questionnaire, "__dataclass_fields__") else questionnaire
        # Only capture which questions were answered vs skipped/defaulted
        answered = sum(1 for v in q.get("answers", {}).values() if v and v.strip())
        intake = {
            "questions_answered": answered,
            "total_questions": len(q.get("answers", {})),
            "mode": q.get("mode", "unknown"),
        }
        # Team size (useful for velocity calibration, not PII)
        team_size = q.get("answers", {}).get("team_size")
        if team_size:
            intake["team_size"] = team_size

    # --- Feature patterns ---
    feature_data = [
        {
            "priority": f.priority.value if hasattr(f.priority, "value") else str(f.priority),
            "title_length": len(f.title),
            "description_length": len(f.description),
        }
        for f in features
    ]

    # --- Story patterns ---
    story_data = [
        {
            "story_points": s.story_points.value if hasattr(s.story_points, "value") else s.story_points,
            "priority": s.priority.value if hasattr(s.priority, "value") else str(s.priority),
            "discipline": s.discipline.value if hasattr(s.discipline, "value") else str(s.discipline),
            "ac_count": len(s.acceptance_criteria),
            "feature_id": s.feature_id,
            "dod_count": sum(1 for d in s.dod_applicable if d) if s.dod_applicable else 0,
        }
        for s in stories
    ]

    # --- Task patterns ---
    task_data = [
        {
            "label": t.label.value if hasattr(t.label, "value") else str(t.label),
            "story_id": t.story_id,
            "has_test_plan": bool(t.test_plan and t.test_plan.strip()),
            "has_ai_prompt": bool(t.ai_prompt and t.ai_prompt.strip()),
        }
        for t in tasks
    ]

    # --- Sprint patterns ---
    sprint_data = [
        {
            "capacity_points": sp.capacity_points,
            "story_count": len(sp.story_ids),
            "total_points": sum(
                s.story_points.value if hasattr(s.story_points, "value") else 0 for s in stories if s.id in sp.story_ids
            ),
        }
        for sp in sprints
    ]

    # --- Review decisions (accept/edit/reject rates) ---
    review_decisions = graph_state.get("review_decisions", {})

    # --- Aggregate stats ---
    total_points = sum(s.story_points.value if hasattr(s.story_points, "value") else 0 for s in stories)

    point_distribution = {}
    for s in stories:
        pts = s.story_points.value if hasattr(s.story_points, "value") else str(s.story_points)
        point_distribution[str(pts)] = point_distribution.get(str(pts), 0) + 1

    discipline_distribution = {}
    for s in stories:
        disc = s.discipline.value if hasattr(s.discipline, "value") else str(s.discipline)
        discipline_distribution[disc] = discipline_distribution.get(disc, 0) + 1

    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
        "agent_version": __version__,
        "platform": platform.system(),
        "python_version": platform.python_version(),
        "llm_provider": os.getenv("LLM_PROVIDER", "anthropic"),
        # Anonymized project
        "project": project,
        "intake": intake,
        # Artifact counts
        "counts": {
            "features": len(features),
            "stories": len(stories),
            "tasks": len(tasks),
            "sprints": len(sprints),
            "total_story_points": total_points,
        },
        # Distributions (for training better estimation)
        "point_distribution": point_distribution,
        "discipline_distribution": discipline_distribution,
        # Structural patterns (no content, just shapes)
        "features": feature_data,
        "stories": story_data,
        "tasks": task_data,
        "sprints": sprint_data,
        # Human feedback signal
        "review_decisions": review_decisions,
    }


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def send_telemetry(graph_state: dict) -> None:
    """Build and send telemetry payload. Fails silently — never blocks the user."""
    if not is_enabled():
        return

    try:
        payload = build_telemetry_payload(graph_state)
        if not payload:
            return

        import urllib.request

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 - fixed https TELEMETRY_ENDPOINT constant
            TELEMETRY_ENDPOINT,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        # Fire and forget — 3 second timeout, swallow all errors
        urllib.request.urlopen(req, timeout=3)  # noqa: S310 - fixed https endpoint constant
        logger.info("Telemetry sent: %s", payload["event_id"])
    except Exception:
        # Never let telemetry crash the app
        logger.debug("Telemetry send failed (this is fine)", exc_info=True)
