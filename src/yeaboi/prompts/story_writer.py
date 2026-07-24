"""Prompt template for the story_writer node.

# See docs: "Prompt Construction" — ARC framework
# See docs: "Scrum Standards" — story format, acceptance criteria, story points
#
# The story writer prompt takes the ProjectAnalysis fields + formatted feature list
# and asks the LLM to decompose each feature into 2-5 user stories with acceptance
# criteria, story points, and priorities.
#
# Same pattern as feature_generator.py:
# - Pre-formatted strings (not dataclass imports) to avoid circular imports
# - ARC framework: Actor, Rules, Context
# - Chain-of-thought reasoning steps
# - JSON schema embedded in the prompt
# - Constants for validation bounds
"""

# Bounds for the number of stories per feature.
# Minimum is 1 — small or focused features should stay as a single story rather
# than being artificially split. The 8-point cap handles the other direction.
MIN_STORIES_PER_FEATURE = 1
MAX_STORIES_PER_FEATURE = 5

# Maximum story points before a story should be split.
MAX_STORY_POINTS = 8

# Allowed Fibonacci story-point values — must match StoryPointValue IntEnum in agent/state.py.
# Hardcoded here (not imported) to avoid the same circular import issue as feature_generator.py.
_ALLOWED_STORY_POINTS = (1, 2, 3, 5, 8)

# Allowed priority values — must match Priority StrEnum in agent/state.py.
_ALLOWED_PRIORITIES = ("critical", "high", "medium", "low")

# Allowed discipline values — must match Discipline StrEnum in agent/state.py.
# Hardcoded here (not imported) to avoid the same circular import issue.
_ALLOWED_DISCIPLINES = ("frontend", "backend", "fullstack", "infrastructure", "design", "testing")

# ---------------------------------------------------------------------------
# Team-aware rule helpers — override defaults when calibration data is present
# ---------------------------------------------------------------------------


def _ac_count_rule(team_calibration: str) -> str:
    """Build the AC count rule, using team's median if available."""
    import re

    # Extract "Median acceptance criteria per story: N" from calibration text
    m = re.search(r"[Mm]edian acceptance criteria per story:\s*(\d+)", team_calibration)
    if m:
        median = int(m.group(1))
        if median <= 1:
            return (
                "7. Each story should have exactly 1 acceptance criterion "
                "(matching team's median of 1). Bundle multiple test scenarios "
                "into a single comprehensive AC rather than listing each separately. "
                "Do NOT generate 3+ ACs — the team uses concise, consolidated criteria.\n"
            )
        if median <= 2:
            return (
                f"7. Each story should have {median} acceptance criteria "
                f"(matching team's median). Keep ACs concise — bundle related "
                "scenarios together rather than listing each separately.\n"
            )
        return (
            f"7. Each story should have approximately {median} acceptance criteria "
            f"(matching team's median of {median}).\n"
        )
    return (
        "7. Each story must have at least 3 acceptance criteria:\n"
        "   - At least 1 happy-path scenario\n"
        "   - At least 1 negative/error-path scenario\n"
        "   - At least 1 edge case\n"
    )


def _ac_format_rule(team_calibration: str) -> str:
    """Build the AC format rule, respecting team's actual style."""
    # Check if team uses Given/When/Then
    if "Given/When/Then" in team_calibration or "uses_given_when_then" in team_calibration:
        return "8. Acceptance criteria must use Given/When/Then format (matching team style).\n"
    if "Writing patterns" in team_calibration:
        # Team has data but doesn't use GWT — use a flexible format
        return (
            "8. Write acceptance criteria as clear, testable statements. "
            "Use bullet points with specific expected outcomes. "
            "Do NOT use Given/When/Then format unless the team's analysis shows they use it.\n"
        )
    # Default: Given/When/Then
    return "8. Acceptance criteria must use Given/When/Then format.\n"


# ---------------------------------------------------------------------------
# JSON schema description embedded in the prompt so the LLM knows exactly
# what structure to produce. Returns a JSON *array* of story objects with
# nested acceptance_criteria arrays.
# ---------------------------------------------------------------------------


def _build_json_schema(dod_items: tuple[str, ...] | None = None) -> str:
    """Build the JSON schema string with dynamic DoD items."""
    from yeaboi.agent.state import DOD_ITEMS

    items = dod_items or DOD_ITEMS
    n = len(items)
    bools = ", ".join(["true"] * n)
    mapping = "\n".join(f"  [{i}] {item}" for i, item in enumerate(items))

    return f"""\
[
  {{
    "id": "string — sequential ID per feature: US-F1-001, US-F1-002, ...",
    "feature_id": "string — parent feature ID: F1, F2, ...",
    "title": "string — short summary of the story (3-7 words)",
    "persona": "string — the user role (e.g. 'developer', 'admin', 'end user')",
    "goal": "string — what the user wants to do (verb phrase, no 'to' prefix)",
    "benefit": "string — why this matters to the user (no 'so that' prefix)",
    "acceptance_criteria": [
      {{
        "given": "string — precondition or initial context",
        "when": "string — action or trigger",
        "then": "string — expected outcome"
      }}
    ],
    "story_points": "integer — Fibonacci value: 1, 2, 3, 5, or 8",
    "points_rationale": "string — why this size, confidence vs team data, similar stories",
    "points_confidence": "string — high, medium, or low based on team's sample count",
    "priority": "string — one of: critical, high, medium, low",
    "discipline": "string — one of: frontend, backend, fullstack, infrastructure, design, testing",
    "dod_applicable": [{bools}]
  }}
]

The {n} booleans in dod_applicable map in order to:
{mapping}
Set to false when the item clearly does not apply to this specific story."""


def get_story_writer_prompt(
    project_name: str,
    project_description: str,
    project_type: str,
    goals: str,
    end_users: str,
    tech_stack: str,
    constraints: str,
    features_block: str,
    *,
    out_of_scope: str = "",
    team_calibration: str = "",
    dod_items: tuple[str, ...] | None = None,
    is_low_code: bool = False,
    carry_over_items: tuple[str, ...] = (),
    review_feedback: str | None = None,
    review_mode: str | None = None,
    previous_output: str | None = None,
) -> str:
    """Build the story writer prompt with injected project analysis and feature fields.

    # See docs: "Prompt Construction" — ARC framework
    #
    # The prompt uses the ARC pattern:
    # - Actor: "Senior Scrum Master" with user story decomposition expertise
    # - Rules: 2-5 stories/feature, nested ACs, Fibonacci points, 8-point cap
    # - Context: Pre-formatted project analysis + feature list
    #
    # Why a function (not a string constant)?
    # All parameters are dynamic — they come from the ProjectAnalysis and Feature list
    # produced by earlier nodes. A function cleanly injects these into the template.

    Args:
        project_name: Project name from analysis.
        project_description: 1-2 sentence project summary.
        project_type: "greenfield", "existing codebase", etc.
        goals: Pre-formatted bullet list of project goals.
        end_users: Pre-formatted bullet list of target users.
        tech_stack: Pre-formatted bullet list of technologies.
        constraints: Pre-formatted bullet list of constraints.
        features_block: Pre-formatted text block of features (from _format_features_for_prompt).
        out_of_scope: Pre-formatted bullet list of out-of-scope items.
        review_feedback: User feedback from a previous review (reject/edit).
        review_mode: "reject" or "edit" — controls how feedback is framed.
        previous_output: Previous output text for edit mode reference.

    Returns:
        The complete prompt string ready to send to the LLM.
    """
    # Extract team's avg stories/epic from calibration text if available
    import re as _re

    from yeaboi.agent.state import DOD_ITEMS
    from yeaboi.prompts.feature_generator import _build_review_section

    _avg_match = _re.search(r"avg\s+(\d+\.?\d*)\s+stories.epic", team_calibration, _re.IGNORECASE)
    if _avg_match:
        _team_avg = round(float(_avg_match.group(1)))
        _team_min = max(1, _team_avg - 1)
        _team_max = max(_team_avg + 1, 3)
        task_instruction = (
            f"Decompose each feature into approximately {_team_avg} user stories "
            f"(range {_team_min}-{_team_max}, matching the team's historical average). "
            f"Consolidate related work into fewer, meatier stories rather than creating many thin ones. "
            f"Return a JSON array matching this exact schema:\n\n"
            f"```json\n{_build_json_schema(dod_items)}\n```\n\n"
        )
        count_rule = (
            f"1. Aim for ~{_team_avg} stories per feature (team avg is {_team_avg}). "
            f"Range: {_team_min}-{_team_max}. Prefer fewer consolidated stories over many small ones.\n"
        )
    else:
        task_instruction = (
            f"Decompose each feature into {MIN_STORIES_PER_FEATURE}-{MAX_STORIES_PER_FEATURE} user stories. "
            f"Return a JSON array matching this exact schema:\n\n"
            f"```json\n{_build_json_schema(dod_items)}\n```\n\n"
        )
        count_rule = f"1. Produce {MIN_STORIES_PER_FEATURE}-{MAX_STORIES_PER_FEATURE} stories per feature.\n"
    id_rule = "2. Use sequential IDs per feature: US-F1-001, US-F1-002, US-F2-001, etc.\n"

    # Low-code projects are mostly configuration / content / no-code-platform
    # work — the stories should reflect that (setup, configuration, content,
    # integration wiring) and carry SMALLER point estimates than custom builds.
    low_code_note = (
        "\n**This is a LOW-CODE project** — mostly configuration, content, and "
        "no-code / low-code platform work rather than custom engineering. Prefer "
        "configuration / setup / content / integration-wiring stories over "
        "heavy build-from-scratch stories, and bias story points SMALLER "
        "(a task that would be 5 points as custom code is often 2-3 when it is "
        "platform configuration).\n"
        if is_low_code
        else ""
    )

    # Unresolved action items carried over from the team's recent retrospectives.
    # These are real, agreed follow-ups the team hasn't closed — turn each into a
    # dedicated story (unless a feature above already covers it) so they aren't lost.
    carry_over_section = ""
    if carry_over_items:
        items = "\n".join(f"- {it}" for it in carry_over_items)
        carry_over_section = (
            "## Carry-over from Recent Retros\n\n"
            "These are open action items the team agreed in past retrospectives. "
            "For each one NOT already covered by a feature above, add ONE dedicated "
            'user story: prefix its title with "[Retro] ", attach it to the most '
            "relevant feature (or the first feature if none fits), and estimate it "
            "like any other story. Skip any that duplicate existing scope.\n\n"
            f"{items}\n\n"
        )

    base = (
        "You are a Senior Scrum Master with expertise in user story decomposition.\n\n"
        "## Project Context\n\n"
        f"**Project:** {project_name}\n"
        f"**Description:** {project_description}\n"
        f"**Type:** {project_type}\n"
        f"{low_code_note}\n"
        f"### Goals\n{goals}\n\n"
        f"### End Users\n{end_users}\n\n"
        f"### Tech Stack\n{tech_stack}\n\n"
        f"### Constraints\n{constraints}\n\n"
        f"### Out of Scope\n{out_of_scope}\n\n"
        "## Features to Decompose\n\n"
        f"{features_block}\n\n"
        + carry_over_section
        + (team_calibration + "\n" if team_calibration else "")
        + "## Task\n\n"
        f"{task_instruction}"
        "## Rules\n\n"
        f"{count_rule}"
        f"{id_rule}"
        "3. Follow the user story format: persona + goal + benefit.\n"
        f"4. Story points must be Fibonacci: {', '.join(str(v) for v in _ALLOWED_STORY_POINTS)}.\n"
        f"5. No story may exceed {MAX_STORY_POINTS} points — split larger stories.\n"
        f"6. Priority must be one of: {', '.join(_ALLOWED_PRIORITIES)}.\n"
        f"{_ac_count_rule(team_calibration)}"
        f"{_ac_format_rule(team_calibration)}"
        "9. Stories within a feature should not overlap — each covers a distinct slice.\n"
        "10. Give each story a short title (3-7 words) summarising the core deliverable.\n"
        "11. Inherit priority from the parent feature unless there's a reason to differ.\n"
        f"12. Tag each story with a discipline: {', '.join(_ALLOWED_DISCIPLINES)}. "
        "Use 'fullstack' if the story spans multiple disciplines or is unclear.\n"
        f"13. Set dod_applicable as a {len(dod_items or DOD_ITEMS)}-element boolean array. "
        "Mark false when an item clearly does not apply to this specific story.\n"
        "    Default to true when in doubt.\n"
        "14. Do NOT create stories for items listed under Out of Scope — "
        "assume these already exist or are handled elsewhere.\n"
        "15. **Prefer fewer, meatier stories over many thin ones.** Consolidate related work "
        "into a single story when the combined effort is ≤ 8 points. Examples:\n"
        "    - Multiple API connection setups (e.g. Jenkins + Slack) → one 'Configure External API Connections' story\n"
        "    - Triggering a job and monitoring its result → one story (they're the same workflow)\n"
        "    - Generating content and formatting it → one story (formatting is not standalone)\n"
        "    - Success notifications and failure escalation → one 'Implement Notification & Escalation' story\n"
        "    Only split when the work is genuinely independent and would be reviewed/deployed separately.\n"
        "16. **points_rationale** must include: (a) why this point value fits the complexity, "
        "(b) confidence level (high/medium/low) based on how well it matches the team's "
        "historical data for this size, (c) reference similar completed stories from the "
        "team's calibration data if provided (cite by ID like PROJ-123).\n"
        "17. **points_confidence** must be 'high' if the team has ≥15 samples at this point "
        "value, 'medium' if ≥5 samples, 'low' if fewer.\n\n"
        "## Story Splitting Strategies\n\n"
        "When a story feels too large (> 8 points), split by:\n"
        "- **Workflow step:** separate creation, editing, deletion, viewing\n"
        "- **Business rule:** separate validation, authorization, notification\n"
        "- **Data type:** separate handling for different entity types\n"
        "- **Interface:** separate API endpoint, UI component, background job\n\n"
        "## Chain of Thought\n\n"
        "Think step by step for each feature:\n"
        "1. Identify the key workflows and user interactions in this feature.\n"
        "2. Draft initial story candidates — start broad, then check if any need splitting.\n"
        "3. **Consolidation check:** can any candidates be merged and still fit within 8 points? "
        "If two stories share the same persona, same system boundary, or are always done together, merge them.\n"
        "4. Write acceptance criteria covering happy path, error path, and edge cases.\n"
        "5. Estimate story points based on complexity, uncertainty, and effort.\n"
        "6. If any story exceeds 8 points, split it using the strategies above.\n"
        "7. Assign priority based on the parent feature's priority and business value.\n\n"
        "Return ONLY the JSON array, no other text."
    )

    return base + _build_review_section(review_feedback, review_mode, previous_output)
