"""Prompt template for the sprint_planner node.

# See docs: "Prompt Construction" — ARC framework
# See docs: "Scrum Standards" — sprint planning, capacity allocation
#
# The sprint planner prompt takes the ProjectAnalysis fields + formatted story list
# and asks the LLM to allocate stories to sprints without exceeding velocity capacity.
#
# Same pattern as task_decomposer.py:
# - Pre-formatted strings (not dataclass imports) to avoid circular imports
# - ARC framework: Actor, Rules, Context
# - Chain-of-thought reasoning steps
# - JSON schema embedded in the prompt
# - Constants for validation bounds
"""

# Safety cap on the number of sprints the LLM can produce.
# Projects rarely exceed 12 two-week sprints (6 months of work).
MAX_SPRINTS = 12

# ---------------------------------------------------------------------------
# JSON schema description embedded in the prompt so the LLM knows exactly
# what structure to produce. Returns a JSON *array* of sprint objects.
# ---------------------------------------------------------------------------

_JSON_SCHEMA = """\
[
  {
    "id": "string — sequential: SP-1, SP-2, ...",
    "name": "string — Sprint 1, Sprint 2, ...",
    "goal": "string — 1-2 sentence sprint focus summary",
    "capacity_points": "integer — total story points in this sprint (must not exceed velocity)",
    "story_ids": ["string — story IDs allocated to this sprint: US-E1-001, US-E2-003, ..."]
  }
]"""


def get_sprint_planner_prompt(
    project_name: str,
    project_description: str,
    velocity: int,
    target_sprints: int,
    stories_block: str,
    *,
    target_sprints_raw: str = "",
    starting_sprint_number: int = 0,
    enforce_target: bool = False,
    sprint_capacities: list[dict] | None = None,
    team_override_from: int | None = None,
    team_calibration: str = "",
    ceremony_history: str = "",
    performance_context: str = "",
    review_feedback: str | None = None,
    review_mode: str | None = None,
    previous_output: str | None = None,
) -> str:
    """Build the sprint planner prompt with injected project context and story list.

    # See docs: "Prompt Construction" — ARC framework
    #
    # The prompt uses the ARC pattern:
    # - Actor: "Senior Scrum Master" with sprint planning expertise
    # - Rules: capacity constraints, priority ordering, spike scheduling
    # - Context: Pre-formatted project analysis + story list with points
    #
    # Why a function (not a string constant)?
    # All parameters are dynamic — they come from the ProjectAnalysis and UserStory list
    # produced by earlier nodes. A function cleanly injects these into the template.

    Args:
        project_name: Project name from analysis.
        project_description: One-line project description.
        velocity: Team velocity in story points per sprint.
        target_sprints: Desired number of sprints as upper bound (0 = auto-calculate from total points).
        stories_block: Pre-formatted text block of stories with points and priorities.
        target_sprints_raw: Raw Q10 answer text (e.g. "3–5 sprints") for range-aware prompting.
        starting_sprint_number: Starting sprint number (e.g. 105). When > 0, sprints
            use real numbers (SP-105, Sprint 105). When 0, uses generic 1-based numbering.
        enforce_target: When True, the user explicitly chose to keep their target despite
            a capacity overflow warning. The prompt treats the target as a hard deadline
            constraint — sprints may exceed velocity to fit all stories.
        review_feedback: User feedback from a previous review (reject/edit).
        review_mode: "reject" or "edit" — controls how feedback is framed.
        previous_output: Previous output text for edit mode reference.

    Returns:
        The complete prompt string ready to send to the LLM.
    """
    if enforce_target and target_sprints > 0:
        # User explicitly rejected the capacity recommendation — treat target as a hard deadline.
        # The LLM must produce exactly this many sprints, even if some exceed velocity.
        target_note = (
            f"HARD DEADLINE: exactly **{target_sprints} sprint(s)**. "
            f"The user chose this target despite capacity overflow — "
            f"it is acceptable for sprints to exceed the {velocity}-point velocity cap"
        )
    elif target_sprints_raw and "no preference" not in target_sprints_raw.lower():
        # Use the raw Q10 answer (e.g. "3–5 sprints") so the LLM sees the range
        target_note = f"Target: **{target_sprints_raw}** (aim for this range; exceed only if scope demands it)"
    elif target_sprints > 0:
        target_note = f"Target: **{target_sprints} sprint(s)**"
    else:
        target_note = "Calculate the number of sprints from total story points ÷ velocity (rounded up)"

    from yeaboi.prompts.feature_generator import _build_review_section

    # When starting_sprint_number > 0, use real sprint numbers in the schema and rules.
    # e.g. SP-105, SP-106, Sprint 105, Sprint 106 instead of SP-1, SP-2.
    if starting_sprint_number > 0:
        n = starting_sprint_number
        id_example = f"SP-{n}, SP-{n + 1}, SP-{n + 2}, SP-{n + 3}"
        name_example = f"Sprint {n}, Sprint {n + 1}, Sprint {n + 2}, Sprint {n + 3}"
        first_sprint_label = f"Sprint {n}"
        naming_rule = (
            f"5. Sequential numbering starting at {n}: IDs are {id_example}. "
            f"Names are {name_example}. "
            f"NEVER reset to Sprint 1, Sprint 2 — always continue the sequence from {n}.\n"
        )
    else:
        id_example = "SP-1, SP-2, SP-3, SP-4"
        name_example = "Sprint 1, Sprint 2, Sprint 3, Sprint 4"
        first_sprint_label = "Sprint 1"
        naming_rule = f"5. Sequential IDs: {id_example}. Names: {name_example}.\n"

    # Build velocity section — per-sprint when bank holidays affect specific sprints
    has_per_sprint = sprint_capacities and any(
        sc.get("bank_holiday_days", 0) > 0 or sc.get("pto_days", 0) > 0 for sc in sprint_capacities
    )
    if has_per_sprint:
        vel_lines = ["**Velocity (per sprint, after capacity deductions):**\n"]
        sprint_label_start = starting_sprint_number if starting_sprint_number > 0 else 1
        for sc in sprint_capacities:
            label = f"Sprint {sprint_label_start + sc['sprint_index']}"
            annotations = []
            names = sc.get("bank_holiday_names", [])
            if names:
                annotations.append(", ".join(names))
            if sc.get("pto_days", 0) > 0:
                pto_names = ", ".join(f"{e['person']} {e['days']}d" for e in sc.get("pto_entries", []))
                annotations.append(f"PTO: {pto_names}")
            if annotations:
                vel_lines.append(f"- {label}: {sc['net_velocity']} pts ({'; '.join(annotations)})")
            else:
                vel_lines.append(f"- {label}: {sc['net_velocity']} pts")
        velocity_section = "\n".join(vel_lines)
        # Capacity rule uses per-sprint caps
        capacity_rule = (
            "2. Each sprint has its own velocity cap (see per-sprint velocities above). "
            "Do not exceed the cap for that sprint, unless a single story exceeds it "
            "(in which case it gets its own sprint).\n"
        )
        cot_step3 = "3. Fill each sprint up to its specific velocity cap, respecting priority order.\n"
    else:
        if team_override_from is not None:
            velocity_section = (
                f"**Velocity:** {velocity} story points per sprint "
                f"(team expanded from {team_override_from} to fit scope)"
            )
        else:
            velocity_section = f"**Velocity:** {velocity} story points per sprint (net, after capacity deductions)"
        capacity_rule = (
            "2. No sprint may exceed the velocity capacity "
            f"(capacity_points ≤ {velocity}), unless a single story exceeds velocity "
            "(in which case it gets its own sprint).\n"
        )
        cot_step3 = "3. Fill each sprint up to the velocity cap, respecting priority order.\n"

    # Recent Standup + Retro signals: sequence retro-sourced ([Retro]) stories
    # early, and stay conservative on load when standup confidence has been low.
    ceremony_section = (
        (
            "## Recent Standup & Retro Signals\n\n"
            "Use these when ordering and loading sprints — schedule stories that "
            "address open retro action items (often titled `[Retro] …`) early, and if "
            "recent standup confidence has been low or declining, keep earlier sprints "
            "a little under capacity to absorb risk.\n\n"
            f"{ceremony_history}\n\n"
        )
        if ceremony_history
        else ""
    )

    # Per-engineer Performance signal — open 1:1 action items + review growth areas.
    # Nudges assignment/loading: an engineer with a heavy stack of open 1:1 actions
    # or an active growth focus shouldn't be loaded to the brim.
    performance_section = (
        (
            "## Team Performance Signal\n\n"
            "Per-engineer open 1:1 action items and review focus areas. Factor this into "
            "loading realism — don't pack sprints to the limit when engineers carry "
            "significant open actions or active growth areas.\n\n"
            f"{performance_context}\n\n"
        )
        if performance_context
        else ""
    )

    base = (
        "You are a Senior Scrum Master with expertise in sprint planning and capacity allocation.\n\n"
        "## Project Context\n\n"
        f"**Project:** {project_name}\n"
        f"**Description:** {project_description}\n"
        f"{velocity_section}\n"
        f"**{target_note}**\n\n"
        "## Stories to Allocate\n\n"
        f"{stories_block}\n\n"
        + ceremony_section
        + performance_section
        + (team_calibration + "\n" if team_calibration else "")
        + "## Task\n\n"
        "Allocate ALL stories above into sprints. Return a JSON array matching this exact schema:\n\n"
        f"```json\n{_JSON_SCHEMA}\n```\n\n"
        "## Rules\n\n"
        "1. Allocate ALL stories across sprints — no story may be left unassigned.\n"
        + (
            f"2. You MUST produce exactly **{target_sprints}** sprints. "
            f"Sprints MAY exceed {velocity} points — distribute stories as evenly as possible.\n"
            if enforce_target
            else capacity_rule
        )
        + "3. Priority ordering: schedule Critical and High priority stories in earlier sprints.\n"
        f"4. Schedule spike/investigation/infrastructure stories in {first_sprint_label} to de-risk unknowns.\n"
        + naming_rule
        + "6. Each sprint goal summarises the sprint's theme in 1-2 sentences.\n"
        f"7. {target_note}.\n"
        "8. Every story must appear in exactly one sprint — no duplicates.\n"
        f"9. Maximum {MAX_SPRINTS} sprints.\n"
        "10. MINIMUM SPRINT LOAD: if the remaining stories after filling all previous sprints "
        "would create a final sprint with fewer points than 30% of velocity, merge them into "
        "the previous sprint instead (allow it to slightly exceed velocity). A nearly-empty "
        "sprint wastes a full sprint cycle. It is better to slightly overflow the previous "
        "sprint than to create a sprint with trivial work.\n\n"
        "## Chain of Thought\n\n"
        "Think step by step:\n"
        "1. Sort stories by priority (Critical → High → Medium → Low).\n"
        f"2. Identify any spike or infrastructure stories — they go in {first_sprint_label}.\n"
        + cot_step3
        + "4. Write a 1-2 sentence goal for each sprint summarising its theme.\n\n"
        "Return ONLY the JSON array, no other text."
    )

    return base + _build_review_section(review_feedback, review_mode, previous_output)
