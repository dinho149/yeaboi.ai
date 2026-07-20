"""Output guardrails — programmatic validation of LLM-generated artifacts.

# See docs: "Guardrails" — three lines of defence (Output layer)

These validators run after the LLM generates stories and sprints.
They catch structural issues that prompt enforcement alone can miss:
  - Story format: persona/goal/benefit must all be non-empty
  - AC coverage: each story should have happy + at least one negative/edge/error AC
  - Sprint capacity: no sprint should exceed team velocity
  - Scope creep: total story points vs. stated sprint count * velocity
  - Unrealistic sprint loads: individual sprints packed to the limit

Each function returns a list of warning strings (empty = all good).
Warnings are displayed to the user after artifact rendering — they
do NOT block the pipeline, since the LLM output may still be usable.
"""

from __future__ import annotations

import logging
import re

from yeaboi.agent.state import Discipline, Sprint, UserStory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Story format validation
# ---------------------------------------------------------------------------

# Minimum meaningful length for persona/goal/benefit fields.
_MIN_FIELD_LEN = 2


def validate_story_format(stories: list[UserStory]) -> list[str]:
    """Check that every story has non-trivial persona, goal, and benefit."""
    logger.debug("Validating story format for %d stories", len(stories))
    warnings: list[str] = []
    for s in stories:
        missing = []
        if len(s.persona.strip()) < _MIN_FIELD_LEN:
            missing.append("persona")
        if len(s.goal.strip()) < _MIN_FIELD_LEN:
            missing.append("goal")
        if len(s.benefit.strip()) < _MIN_FIELD_LEN:
            missing.append("benefit")
        if missing:
            warnings.append(f"{s.id}: missing or too short — {', '.join(missing)}")
    if warnings:
        logger.warning("Story format: %d issue(s) found", len(warnings))
    else:
        logger.debug("Story format: all %d stories passed", len(stories))
    return warnings


# ---------------------------------------------------------------------------
# Acceptance criteria coverage
# ---------------------------------------------------------------------------

# Keywords that suggest negative / edge / error scenarios in AC text.
_NEGATIVE_KEYWORDS = re.compile(
    r"\b(invalid|fail|error|denied|unauthorized|reject|missing|empty|exceed|timeout|unavailable|forbidden"
    r"|wrong|incorrect|expired|duplicate|overflow|malformed|corrupt)\b",
    re.IGNORECASE,
)


def validate_ac_coverage(stories: list[UserStory]) -> list[str]:
    """Check each story has >=2 ACs and at least one negative/edge case."""
    logger.debug("Validating AC coverage for %d stories", len(stories))
    warnings: list[str] = []
    for s in stories:
        acs = s.acceptance_criteria
        if len(acs) < 2:
            warnings.append(f"{s.id}: only {len(acs)} AC(s) — consider adding more scenarios")
            continue

        # Check if any AC covers a negative/edge case
        has_negative = any(_NEGATIVE_KEYWORDS.search(f"{ac.given} {ac.when} {ac.then}") for ac in acs)
        if not has_negative:
            warnings.append(f"{s.id}: all ACs appear to be happy-path — consider adding negative/edge cases")
    if warnings:
        logger.warning("AC coverage: %d issue(s) found", len(warnings))
    else:
        logger.debug("AC coverage: all %d stories passed", len(stories))
    return warnings


# ---------------------------------------------------------------------------
# Sprint capacity validation
# ---------------------------------------------------------------------------


def validate_sprint_capacity(sprints: list[Sprint], stories: list[UserStory], velocity: int) -> list[str]:
    """Check that no sprint exceeds team velocity."""
    logger.debug("Validating sprint capacity (%d sprints, velocity=%d)", len(sprints), velocity)
    warnings: list[str] = []
    if velocity <= 0:
        return warnings

    points_map = {s.id: s.story_points.value for s in stories}
    for sp in sprints:
        actual = sum(points_map.get(sid, 0) for sid in sp.story_ids)
        if actual > velocity:
            over = actual - velocity
            warnings.append(f"{sp.name}: {actual} pts exceeds velocity {velocity} by {over} pts")
    return warnings


# ---------------------------------------------------------------------------
# Scope vs. capacity check
# ---------------------------------------------------------------------------


def validate_scope_vs_capacity(
    sprints: list[Sprint],
    stories: list[UserStory],
    velocity: int,
) -> list[str]:
    """Flag when total scope significantly exceeds planned capacity."""
    warnings: list[str] = []
    if velocity <= 0 or not sprints:
        return warnings

    total_points = sum(s.story_points.value for s in stories)
    total_capacity = velocity * len(sprints)

    if total_points > total_capacity:
        over_pct = ((total_points - total_capacity) / total_capacity) * 100
        if over_pct > 10:
            warnings.append(
                f"Total scope ({total_points} pts) exceeds capacity "
                f"({len(sprints)} sprints × {velocity} pts = {total_capacity} pts) "
                f"by {over_pct:.0f}% — consider adding sprints or reducing scope"
            )
    return warnings


# ---------------------------------------------------------------------------
# Team calibration validation
# ---------------------------------------------------------------------------

# Threshold: warn when discipline avg-points deviation exceeds this factor.
# e.g. team avg backend = 3 pts, plan has backend avg = 7 pts → 2.3x → warn
_CALIBRATION_DEVIATION_FACTOR = 1.8


def validate_estimation_calibration(
    stories: list[UserStory],
    team_profile: object,
) -> list[str]:
    """Warn when generated estimates deviate significantly from team norms.

    Compares average story points per discipline against historical team patterns.
    Emits a warning (not an error) when the plan's discipline averages are more
    than _CALIBRATION_DEVIATION_FACTOR times the team's historical average.

    # See docs: "Scrum Standards" — team learning, self-calibrating estimates

    Args:
        stories: Generated user stories with story_points and discipline fields.
        team_profile: A TeamProfile instance (or None to skip this check).

    Returns:
        List of warning strings (empty = no calibration issues detected).
    """
    if not stories or team_profile is None:
        return []

    story_shapes = getattr(team_profile, "story_shapes", ())
    if not story_shapes:
        return []

    # Build a lookup: discipline → team's historical avg_points
    team_avg: dict[str, float] = {
        shape.discipline: shape.avg_points for shape in story_shapes if shape.sample_count >= 3 and shape.avg_points > 0
    }
    if not team_avg:
        return []

    # Compute plan's per-discipline averages
    by_discipline: dict[str, list[int]] = {}
    for story in stories:
        disc = story.discipline.value if isinstance(story.discipline, Discipline) else str(story.discipline)
        pts = story.story_points.value if hasattr(story.story_points, "value") else int(story.story_points)
        by_discipline.setdefault(disc, []).append(pts)

    warnings: list[str] = []
    for disc, pts_list in by_discipline.items():
        if disc not in team_avg:
            continue
        plan_avg = sum(pts_list) / len(pts_list)
        hist_avg = team_avg[disc]
        if hist_avg <= 0:
            continue
        ratio = plan_avg / hist_avg
        if ratio > _CALIBRATION_DEVIATION_FACTOR:
            warnings.append(
                f"Calibration: {disc} stories avg {plan_avg:.1f} pts in plan vs "
                f"{hist_avg:.1f} pts historically — consider splitting large {disc} stories"
            )
        elif ratio < (1 / _CALIBRATION_DEVIATION_FACTOR):
            warnings.append(
                f"Calibration: {disc} stories avg {plan_avg:.1f} pts in plan vs "
                f"{hist_avg:.1f} pts historically — stories may be too granular"
            )

    if warnings:
        logger.warning("Estimation calibration: %d issue(s) found", len(warnings))
    return warnings


# ---------------------------------------------------------------------------
# Aggregate runner
# ---------------------------------------------------------------------------


def validate_output(
    stories: list[UserStory] | None = None,
    sprints: list[Sprint] | None = None,
    velocity: int = 0,
    team_profile: object = None,
) -> list[str]:
    """Run all applicable output guardrails and return combined warnings."""
    logger.debug("Running output validation")
    warnings: list[str] = []
    if stories:
        warnings.extend(validate_story_format(stories))
        warnings.extend(validate_ac_coverage(stories))
        if team_profile is not None:
            warnings.extend(validate_estimation_calibration(stories, team_profile))
    if sprints and stories:
        warnings.extend(validate_sprint_capacity(sprints, stories, velocity))
        warnings.extend(validate_scope_vs_capacity(sprints, stories, velocity))
    if warnings:
        logger.warning("Output validation: %d total warning(s)", len(warnings))
    else:
        logger.debug("Output validation: all checks passed")
    return warnings
