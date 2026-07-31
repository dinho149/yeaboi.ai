"""Self-contained HTML export for Scrum plan artifacts.

Generates a single-file HTML report (no external dependencies) from whatever
artifacts are available in graph_state — works at any pipeline checkpoint, so a
section whose artifact does not exist yet is simply absent.

This module builds a **payload**, not markup: ``frontend/src/export`` draws it.
That is what let ~500 lines of f-string HTML go, and with them the escaping
discipline every one of those strings carried.

# See docs: "Export Formats" — Markdown, HTML
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

#: Human-readable label for each pipeline node.
_STAGE_LABELS: dict[str, str] = {
    "project_analyzer": "Project Analysis",
    "feature_generator": "Features",
    "story_writer": "User Stories",
    "task_decomposer": "Tasks",
    "sprint_planner": "Sprint Plan",
    "questionnaire": "Questionnaire",
    "complete": "Complete Plan",
}

#: The analysis fields, in reading order. ``Assumptions`` is last and rendered
#: differently by the bundle — it is the section a reader is most likely to
#: disagree with, and it should not look like a finding.
_ANALYSIS_FIELDS = (
    ("Goals", "goals"),
    ("End Users", "end_users"),
    ("Tech Stack", "tech_stack"),
    ("Integrations", "integrations"),
    ("Constraints", "constraints"),
    ("Risks", "risks"),
    ("Out of Scope", "out_of_scope"),
    ("Assumptions", "assumptions"),
)


def _enum(value) -> str:
    """The string behind a plan enum, which may already be one."""
    return value.value if hasattr(value, "value") else str(value)


def _points(value) -> int:
    return int(value.value if hasattr(value, "value") else value)


# ---------------------------------------------------------------------------
# Section payloads
# ---------------------------------------------------------------------------


def _questionnaire_payload(graph_state: dict) -> list[list[str]]:
    """``[label, question, answer]`` rows, in question order."""
    from yeaboi.prompts.intake import INTAKE_QUESTIONS

    qs = graph_state.get("questionnaire")
    if qs is None or not qs.answers:
        return []
    return [
        [f"Q{num}", INTAKE_QUESTIONS.get(num, f"Question {num}"), str(qs.answers[num])] for num in sorted(qs.answers)
    ]


def _capacity_payload(graph_state: dict, analysis) -> dict | None:
    """Gross velocity, what was deducted from it, and what survives.

    ``None`` when the numbers to show it are not all there — a capacity block
    that says "0 pts/sprint" reads as a finding rather than as a gap.
    """
    team_size = graph_state.get("team_size", 0)
    velocity = graph_state.get("velocity_per_sprint", 0)
    target_sprints = analysis.target_sprints if analysis else 0
    if not team_size or not velocity or not target_sprints:
        return None

    deductions: list[str] = []
    for value, label in (
        (graph_state.get("capacity_bank_holiday_days", 0), "bank holidays: {}d"),
        (graph_state.get("capacity_planned_leave_days", 0), "planned leave: {}d"),
        (graph_state.get("capacity_unplanned_leave_pct", 0), "unplanned: {}%"),
        (graph_state.get("capacity_onboarding_engineer_sprints", 0), "onboarding: {} eng-sprint(s)"),
        (graph_state.get("capacity_ktlo_engineers", 0), "KTLO: {} eng"),
        (graph_state.get("capacity_discovery_pct", 5), "discovery: {}%"),
    ):
        if value > 0:
            deductions.append(label.format(value))

    return {
        "teamSize": team_size,
        "sprintWeeks": graph_state.get("sprint_length_weeks", 2),
        "targetSprints": target_sprints,
        "velocity": velocity,
        "netVelocity": graph_state.get("net_velocity_per_sprint", 0),
        "deductions": deductions,
    }


def _analysis_payload(graph_state: dict) -> dict | None:
    """The project analysis: what it is, and the eight lists that qualify it."""
    analysis = graph_state.get("project_analysis")
    if not analysis:
        return None
    return {
        "name": analysis.project_name,
        "description": analysis.project_description,
        "targetState": analysis.target_state,
        "projectType": analysis.project_type,
        "sprintWeeks": analysis.sprint_length_weeks,
        "targetSprints": analysis.target_sprints,
        "fields": [
            {"label": label, "items": list(items)}
            for label, attr in _ANALYSIS_FIELDS
            if (items := getattr(analysis, attr, ()) or ())
        ],
    }


def _story_payload(story, dod_items: list[str]) -> dict:
    """One user story, with its acceptance criteria and Definition of Done."""
    out: dict = {
        "id": story.id,
        "title": story.title or story.text,
        "text": story.text,
        "priority": _enum(story.priority),
        "discipline": _enum(story.discipline),
        "points": _points(story.story_points),
        "acceptanceCriteria": [
            {"given": ac.given, "when": ac.when, "then": ac.then} for ac in story.acceptance_criteria
        ],
        # Paired with its flag rather than sent as two lists: the old renderer
        # zipped them and length-checked first, and a mismatch silently dropped
        # the whole block. Pairing here makes the mismatch impossible.
        "dod": [],
    }
    flags = story.dod_applicable
    if len(flags) == len(dod_items):
        out["dod"] = [[item, bool(applicable)] for item, applicable in zip(dod_items, flags)]
    if story.points_rationale:
        out["rationale"] = story.points_rationale
        if confidence := getattr(story, "points_confidence", ""):
            out["confidence"] = confidence
    return out


def _stories_payload(graph_state: dict) -> tuple[list[dict], list[list]]:
    """Stories grouped by their feature, plus the story-points mix by discipline."""
    from yeaboi.agent.state import resolve_dod_items

    stories = graph_state.get("stories", [])
    if not stories:
        return [], []

    feature_titles = {f.id: f.title for f in graph_state.get("features", [])}
    dod_items = resolve_dod_items(graph_state)

    groups: dict[str, dict] = {}
    for story in stories:
        group = groups.setdefault(
            story.feature_id,
            {
                "featureId": story.feature_id,
                "featureTitle": feature_titles.get(story.feature_id, story.feature_id),
                "stories": [],
            },
        )
        group["stories"].append(_story_payload(story, dod_items))

    by_discipline: dict[str, int] = {}
    for story in stories:
        discipline = _enum(story.discipline)
        by_discipline[discipline] = by_discipline.get(discipline, 0) + _points(story.story_points)
    return list(groups.values()), [[label, count] for label, count in sorted(by_discipline.items())]


def _tasks_payload(graph_state: dict) -> list[dict]:
    """Tasks grouped by the story they decompose."""
    tasks = graph_state.get("tasks", [])
    if not tasks:
        return []

    story_text = {s.id: s.text for s in graph_state.get("stories", [])}
    groups: dict[str, dict] = {}
    for task in tasks:
        group = groups.setdefault(
            task.story_id,
            {"storyId": task.story_id, "storyText": story_text.get(task.story_id, task.story_id), "tasks": []},
        )
        row: dict = {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "label": _enum(task.label),
        }
        if task.test_plan:
            row["testPlan"] = task.test_plan
        if task.ai_prompt:
            row["aiPrompt"] = task.ai_prompt
        group["tasks"].append(row)
    return list(groups.values())


def _sprints_payload(graph_state: dict) -> list[dict]:
    """Each sprint with the points it actually holds against the points it can."""
    sprints = graph_state.get("sprints", [])
    if not sprints:
        return []

    story_pts = {s.id: _points(s.story_points) for s in graph_state.get("stories", [])}
    return [
        {
            "name": sprint.name,
            "goal": sprint.goal,
            "capacity": sprint.capacity_points,
            "used": sum(story_pts.get(sid, 0) for sid in sprint.story_ids),
            "storyIds": list(sprint.story_ids),
        }
        for sprint in sprints
    ]


def _images_payload(graph_state: dict) -> list[str]:
    """Screenshots pasted into the session, embedded so the file stays offline."""
    from yeaboi.html_theme import image_data_uri

    seen: list[str] = []
    for key in ("pasted_images", "review_feedback_images", "chat_images"):
        for path in graph_state.get(key) or []:
            if path not in seen:
                seen.append(path)
    return [uri for path in seen if (uri := image_data_uri(path))]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_export_html(graph_state: dict, stage: str = "complete") -> str:
    """Build a self-contained HTML report from available graph state artifacts.

    Works at any pipeline checkpoint — sections for missing artifacts are
    simply omitted. The ``stage`` parameter sets the label in the header.
    """
    from yeaboi.html_theme import export_page

    stage_label = _STAGE_LABELS.get(stage, stage.replace("_", " ").title())
    analysis = _analysis_payload(graph_state)
    project_name = analysis["name"] if analysis else "Scrum Plan"

    questionnaire = _questionnaire_payload(graph_state)
    features = [
        {
            "id": feature.id,
            "title": feature.title,
            "description": feature.description,
            "priority": _enum(feature.priority),
        }
        for feature in graph_state.get("features", [])
    ]
    story_groups, points_by_discipline = _stories_payload(graph_state)
    task_groups = _tasks_payload(graph_state)
    sprints = _sprints_payload(graph_state)
    images = _images_payload(graph_state)

    nav: list[tuple[str, str]] = []
    for section_id, label, present in (
        ("questionnaire", "Questionnaire", bool(questionnaire)),
        ("analysis", "Analysis", bool(analysis)),
        ("features", "Features", bool(features)),
        ("stories", "Stories", bool(story_groups)),
        ("tasks", "Tasks", bool(task_groups)),
        ("sprints", "Sprint Plan", bool(sprints)),
    ):
        if present:
            nav.append((section_id, label))

    facts = [("STAGE", stage_label), ("EXPORTED", datetime.now().strftime("%Y-%m-%d %H:%M"))]
    if profile_id := graph_state.get("analysis_profile_id", ""):
        # "jira-acme-web" → calibrated with "acme-web", from "jira".
        source, _, display = profile_id.partition("-")
        facts.append(("CALIBRATED", f"{display or profile_id} ({source})" if display else profile_id))

    return export_page(
        mode="planning",
        title=project_name,
        wordmark="plan",
        subtitle=analysis["description"] if analysis else "",
        facts=facts,
        # Which artifacts exist, at a glance — a plan exported mid-pipeline
        # should say so in the header rather than only by what is missing.
        badges=[
            label
            for label, rows in (
                ("Analysis", analysis),
                ("Features", features),
                ("Stories", story_groups),
                ("Tasks", task_groups),
                ("Sprints", sprints),
            )
            if rows
        ],
        nav=nav,
        report={
            "kind": "plan",
            "questionnaire": questionnaire,
            "analysis": analysis,
            "capacity": _capacity_payload(graph_state, graph_state.get("project_analysis")),
            "epicKey": graph_state.get("jira_epic_key", "") or graph_state.get("azdevops_epic_id", ""),
            "features": features,
            "storyGroups": story_groups,
            "pointsByDiscipline": points_by_discipline,
            "taskGroups": task_groups,
            "sprints": sprints,
            "velocity": graph_state.get("velocity_per_sprint", 0),
            "images": images,
        },
        footer=f"Generated by yeaboi.ai • {datetime.now().strftime('%Y-%m-%d')}",
    )


def export_plan_html(graph_state: dict, stage: str = "complete", path: Path | None = None) -> Path:
    """Write the HTML report to disk and return the path.

    Args:
        graph_state: The current graph state dict.
        stage: Pipeline stage label for the header.
        path: Optional output path. Defaults to ``scrum-plan.html`` in cwd.

    Returns:
        The path the file was written to.
    """
    from yeaboi.fs_policy import resolve_and_check

    output_path = resolve_and_check(path or Path("scrum-plan.html"), mode="write", context="HTML plan export")
    output_path.write_text(build_export_html(graph_state, stage=stage), encoding="utf-8")
    sections = sum(
        1
        for k in ("questionnaire", "project_analysis", "features", "stories", "tasks", "sprints")
        if graph_state.get(k)
    )
    logger.info("HTML exported to %s (%d section(s), stage=%s)", output_path, sections, stage)
    return output_path
