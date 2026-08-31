"""Epic review step for the chat driver — team-style reformat before epics.

The card pipeline runs this intercept inline in _phase_pipeline: when a team
profile with examples exists, the project epic (analysis name + description)
is reformatted to the team's naming/template conventions BEFORE
feature_generator runs — which means it feeds the generated stories and the
tracker sync. Skipping it in chat would change planning results, so the chat
driver calls this blocking helper on its worker thread instead.

Mirrors _phases.py's intercept (profile load → calibration → quarter label →
one JSON LLM call → analysis rebuild); the inline copy is deleted with the
card pipeline at the end of the refactor.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import fields as dataclass_fields
from datetime import datetime, timedelta

from yeaboi.timeparse import parse_datetime

logger = logging.getLogger(__name__)


def load_epic_profile(graph_state: dict) -> tuple[str, dict | None]:
    """Resolve the analysis profile for the epic card banner and reformat.

    Returns (profile_id, examples). Explicit selection wins; otherwise
    auto-detect from configured trackers (resumed sessions). With the run's
    ``analysis`` toggle off, nothing loads — auto-detect included.
    """
    profile = None
    examples = None
    try:
        from yeaboi.agent.nodes import _wants_dep

        if not _wants_dep(graph_state, "analysis"):
            graph_state["_epic_profile"] = None
            return "", None
    except Exception:
        logger.debug("Epic profile toggle check failed", exc_info=True)
    profile_id = graph_state.get("analysis_profile_id", "")
    try:
        from yeaboi.agent.nodes import _load_profile_by_id, _load_team_examples, _load_team_profile

        if profile_id:
            profile, examples = _load_profile_by_id(profile_id)
        else:
            profile = _load_team_profile()
            examples = _load_team_examples()
            if profile:
                profile_id = getattr(profile, "team_id", "")
                logger.info("Epic review: auto-detected profile %s", profile_id)
    except Exception:
        logger.debug("Epic profile load failed", exc_info=True)
    graph_state["_epic_profile"] = profile
    return profile_id, examples


def _quarter_label_for(graph_state: dict, analysis) -> str:
    """Compute the Q{n}|{year} label when the team quarter-scopes epic names."""
    sprint_start = graph_state.get("sprint_start_date", "")
    target_sprints = getattr(analysis, "target_sprints", 0)
    sprint_weeks = getattr(analysis, "sprint_length_weeks", 2)
    try:
        start_dt = parse_datetime(sprint_start) if sprint_start else datetime.now()
    except Exception:
        start_dt = datetime.now()
    start_q = ((start_dt.month - 1) // 3) + 1
    end_dt = start_dt + timedelta(weeks=target_sprints * sprint_weeks) if target_sprints else start_dt
    end_q = ((end_dt.month - 1) // 3) + 1
    if start_q == end_q and start_dt.year == end_dt.year:
        return f"Q{start_q}|{start_dt.year}"
    return f"Q{start_q}|{start_dt.year}-Q{end_q}|{end_dt.year}"


def reformat_epic_to_team_style(graph_state: dict, *, dry_run: bool = False) -> tuple[str, dict | None]:
    """Reformat the epic to the team's conventions (blocking; call off-thread).

    Mutates graph_state["project_analysis"] in place on success — same effect
    as the card pipeline's intercept, so downstream stories/sync see the same
    epic either way. Failures are non-fatal: the original analysis stands.

    Returns (profile_id, examples) for the epic card's calibration banner.
    """
    analysis = graph_state.get("project_analysis")
    if dry_run or analysis is None:
        # Dry-run makes no LLM *or* tracker calls — profile loading can hit
        # the network, so skip it entirely, not just the reformat.
        return graph_state.get("analysis_profile_id", ""), None
    profile_id, examples = load_epic_profile(graph_state)
    profile = graph_state.pop("_epic_profile", None)
    if not profile or not examples:
        return profile_id, examples

    try:
        from yeaboi.agent.nodes import _format_team_calibration
        from yeaboi.tools.team_learning import _llm_invoke

        calibration = _format_team_calibration(profile, examples=examples)
        if not calibration:
            return profile_id, examples

        naming = examples.get("naming_conventions", {})
        epic_style = naming.get("epic_naming_style", "") if isinstance(naming, dict) else ""
        quarter_label = _quarter_label_for(graph_state, analysis) if "quarter" in epic_style.lower() else ""
        sections = naming.get("template_sections", []) if isinstance(naming, dict) else []
        section_names = [s[0] if isinstance(s, (list, tuple)) else str(s) for s in sections[:5]]

        prompt = (
            f"Reformat this project epic to match the team's style.\n\n"
            f"Project: {getattr(analysis, 'project_name', '')}\n"
            f"Description: {getattr(analysis, 'project_description', '')}\n\n"
        )
        if quarter_label:
            prompt += (
                f"IMPORTANT: The team uses quarter-scoped naming. "
                f"The correct quarter is: {quarter_label}\n"
                f"Use this EXACT quarter/year in the title.\n\n"
            )
        prompt += f"{calibration}\n\nRequirements:\n"
        if quarter_label:
            prompt += f"1. Use the team's naming convention with {quarter_label}\n"
        else:
            prompt += "1. Use the team's naming convention for the title\n"
        if section_names:
            prompt += f"2. Structure the description with these sections: {', '.join(section_names)}\n"
        prompt += (
            "3. Keep the project scope — don't change what the epic is about\n"
            "4. Match the team's writing style and level of detail\n\n"
            "Return ONLY a JSON object:\n"
            '{"title": "...", "description": "...", "stories_estimate": N, '
            '"points_estimate": N, "rationale": "..."}'
        )

        response = _llm_invoke(prompt, temperature=0.2)
        text = (response.content if hasattr(response, "content") else str(response)).strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        payload = json.loads(text)
        if isinstance(payload, dict):
            kwargs = {f.name: getattr(analysis, f.name) for f in dataclass_fields(analysis)}
            kwargs["project_name"] = payload.get("title", kwargs["project_name"])
            kwargs["project_description"] = payload.get("description", kwargs["project_description"])
            graph_state["project_analysis"] = type(analysis)(**kwargs)
            logger.info("Epic reformatted to team style: %s", kwargs["project_name"])
    except Exception as exc:
        logger.warning("Epic reformat failed (keeping original): %s", exc)
    return profile_id, examples
