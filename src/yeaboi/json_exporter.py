"""JSON export for Scrum plan artifacts.

Serializes plan artifacts from graph_state into a clean, user-facing JSON
schema suitable for piping into other tools or CI workflows.

Uses dataclasses.asdict() — same proven pattern as sessions.py and
persistence.py for serializing frozen dataclasses.

# See docs: "Architecture" — export layer
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict

from yeaboi.agent.state import (
    Feature,
    ProjectAnalysis,
    QuestionnaireState,
    Sprint,
    Task,
    UserStory,
)

logger = logging.getLogger(__name__)


def export_plan_json(graph_state: dict) -> str:
    """Serialize plan artifacts to a clean JSON string.

    Produces a user-facing schema with no internal fields (messages,
    pending_review, _intake_mode, etc.). Only includes populated sections.

    Args:
        graph_state: The final graph state dict containing all artifacts.

    Returns:
        Pretty-printed JSON string.
    """
    output: dict = {"version": "1.0.0"}

    # Analysis profile provenance
    profile_id = graph_state.get("analysis_profile_id", "")
    if profile_id:
        output["calibration"] = {
            "profile_id": profile_id,
            "source": profile_id.split("-", 1)[0] if "-" in profile_id else "",
            "board": profile_id.split("-", 1)[1] if "-" in profile_id else profile_id,
        }

    # Project metadata from analysis + questionnaire
    analysis = graph_state.get("project_analysis")
    questionnaire = graph_state.get("questionnaire")
    if isinstance(analysis, ProjectAnalysis):
        project: dict = {
            "name": analysis.project_name,
            "description": analysis.project_description,
            "type": analysis.project_type,
            "goals": list(analysis.goals),
            "tech_stack": list(analysis.tech_stack),
            "sprint_length_weeks": analysis.sprint_length_weeks,
        }
        # Pull team_size from questionnaire Q6 if available
        if isinstance(questionnaire, QuestionnaireState):
            team_size = questionnaire.answers.get(6)
            if team_size:
                project["team_size"] = team_size
        output["project"] = project

    # Features
    features = graph_state.get("features", [])
    if features:
        output["features"] = [asdict(f) for f in features if isinstance(f, Feature)]

    # Stories
    stories = graph_state.get("stories", [])
    if stories:
        output["stories"] = [_serialize_story(s) for s in stories if isinstance(s, UserStory)]

    # Tasks
    tasks = graph_state.get("tasks", [])
    if tasks:
        output["tasks"] = [asdict(t) for t in tasks if isinstance(t, Task)]

    # Sprints
    sprints = graph_state.get("sprints", [])
    if sprints:
        output["sprints"] = [_serialize_sprint(s) for s in sprints if isinstance(s, Sprint)]

    logger.info(
        "export_plan_json: %d features, %d stories, %d tasks, %d sprints",
        len(output.get("features", [])),
        len(output.get("stories", [])),
        len(output.get("tasks", [])),
        len(output.get("sprints", [])),
    )

    return json.dumps(output, indent=2, default=str)


def _serialize_story(story: UserStory) -> dict:
    """Serialize a UserStory, converting enums and nested dataclasses."""
    d = asdict(story)
    # Convert story_points IntEnum to plain int
    d["story_points"] = int(story.story_points)
    return d


def _serialize_sprint(sprint: Sprint) -> dict:
    """Serialize a Sprint, converting tuples to lists."""
    d = asdict(sprint)
    d["story_ids"] = list(sprint.story_ids)
    return d
