"""Prompt template for the feature_generator node.

# See docs: "Prompt Construction" — ARC framework
# See docs: "Scrum Standards" — feature decomposition
#
# The feature generator prompt takes the structured ProjectAnalysis fields and asks
# the LLM to decompose the project scope into 3-6 features. Same pattern as the
# analyzer prompt: single LLM call with JSON-schema prompt.
#
# Why pre-formatted strings (not ProjectAnalysis)?
# Accepting strings avoids importing ProjectAnalysis from agent.state, which
# would create a circular import: feature_generator → agent.state → agent/__init__
# → agent.graph → agent.nodes → feature_generator. The node function formats the
# ProjectAnalysis fields into strings before calling this prompt.
"""

# Bounds for the number of features the LLM should produce.
MIN_FEATURES = 3
MAX_FEATURES = 6

# Allowed priority values — must match Priority StrEnum in agent/state.py.
# Hardcoded here (not imported) to avoid the same circular import issue.
_ALLOWED_PRIORITIES = ("critical", "high", "medium", "low")

# ---------------------------------------------------------------------------
# JSON schema description embedded in the prompt so the LLM knows exactly
# what structure to produce. Returns a JSON *array* of feature objects (not
# wrapped in an envelope object) for simpler parsing.
# ---------------------------------------------------------------------------

_JSON_SCHEMA = """\
[
  {
    "id": "string — sequential ID: F1, F2, F3, ...",
    "title": "string — concise feature title (3-8 words)",
    "description": "string — 1-2 sentence scope description",
    "priority": "string — one of: critical, high, medium, low"
  }
]"""


def get_feature_generator_prompt(
    project_name: str,
    project_description: str,
    project_type: str,
    goals: str,
    end_users: str,
    target_state: str,
    tech_stack: str,
    constraints: str,
    risks: str,
    target_sprints: str,
    *,
    out_of_scope: str = "",
    repo_context: str | None = None,
    review_feedback: str | None = None,
    review_mode: str | None = None,
    previous_output: str | None = None,
) -> str:
    """Build the feature generator prompt with injected project analysis fields.

    # See docs: "Prompt Construction" — ARC framework
    #
    # The prompt uses the ARC pattern:
    # - Actor: "Senior Scrum Master" with feature decomposition expertise
    # - Rules: 3-6 features, JSON array schema, priority levels
    # - Context: Pre-formatted project analysis fields
    #
    # Why a function (not a string constant)?
    # All parameters are dynamic — they come from the ProjectAnalysis produced
    # by the project_analyzer node. A function cleanly injects these into the
    # template without string-replace hacks.

    Args:
        project_name: Project name from analysis.
        project_description: 1-2 sentence project summary.
        project_type: "greenfield", "existing codebase", etc.
        goals: Pre-formatted bullet list of project goals.
        end_users: Pre-formatted bullet list of target users.
        target_state: What "done" looks like.
        tech_stack: Pre-formatted bullet list of technologies.
        constraints: Pre-formatted bullet list of constraints.
        risks: Pre-formatted bullet list of risks.
        target_sprints: Number of target sprints (or "scope-based").
        out_of_scope: Pre-formatted bullet list of out-of-scope items.
        repo_context: Raw string from tool scan (file tree + README). When
            provided, a "Repository Context" section is injected so the LLM
            can scope features around what is already built vs. what needs creating.
        review_feedback: User feedback from a previous review (reject/edit).
        review_mode: "reject" or "edit" — controls how feedback is framed.
        previous_output: Previous output text for edit mode reference.

    Returns:
        The complete prompt string ready to send to the LLM.
    """
    # Inject repo scan results when available, placed after Tech Stack so the
    # LLM can see existing directory structure alongside the detected tech stack.
    repo_section = (
        (
            "\n### Repository Context\n\n"
            "Use the following repository data to shape feature scope — "
            "existing directories, key files, and README goals indicate what is "
            "already built vs. what needs to be created.\n\n"
            f"{repo_context}\n"
        )
        if repo_context
        else ""
    )

    base = (
        "You are a Senior Scrum Master with expertise in project decomposition.\n\n"
        "## Project Context\n\n"
        f"**Project:** {project_name}\n"
        f"**Description:** {project_description}\n"
        f"**Type:** {project_type}\n"
        f"**Target sprints:** {target_sprints}\n\n"
        f"### Goals\n{goals}\n\n"
        f"### End Users\n{end_users}\n\n"
        f"### Target State\n{target_state}\n\n"
        f"### Tech Stack\n{tech_stack}\n"
        f"{repo_section}\n"
        f"### Constraints\n{constraints}\n\n"
        f"### Out of Scope\n{out_of_scope}\n\n"
        f"### Risks\n{risks}\n\n"
        "## Task\n\n"
        f"Decompose this project into {MIN_FEATURES}-{MAX_FEATURES} features. "
        "Return a JSON array matching this exact schema:\n\n"
        f"```json\n{_JSON_SCHEMA}\n```\n\n"
        "## Rules\n\n"
        f"1. Produce exactly {MIN_FEATURES}-{MAX_FEATURES} features — no more, no fewer.\n"
        "2. Use sequential IDs: F1, F2, F3, etc.\n"
        f"3. Priority must be one of: {', '.join(_ALLOWED_PRIORITIES)}.\n"
        "4. Each feature should cover a distinct functional area — no overlap.\n"
        "5. Include infrastructure/setup as a feature if this is a greenfield project.\n"
        "6. Tag risk-related items with higher priority.\n"
        "7. Do NOT create features for items listed under Out of Scope — these are explicitly excluded.\n\n"
        "## Chain of Thought\n\n"
        "Think step by step:\n"
        "1. Identify the major functional areas from the goals and target state.\n"
        "2. Identify infrastructure and setup needs from the tech stack and project type.\n"
        "3. Identify risk-tagged items from the risks and constraints.\n"
        "4. Group related concerns into cohesive features.\n"
        "5. Assign priorities based on dependencies and risk.\n\n"
        "Return ONLY the JSON array, no other text."
    )

    # See docs: "Guardrails" — human-in-the-loop pattern
    # Append review feedback section when the user rejected or requested edits.
    # This gives the LLM explicit instructions about what to change.
    return base + _build_review_section(review_feedback, review_mode, previous_output)


def _build_review_section(
    review_feedback: str | None,
    review_mode: str | None,
    previous_output: str | None,
) -> str:
    """Build the review feedback section appended to generation prompts.

    Shared by all 4 prompt functions — keeps the feedback framing consistent.

    Args:
        review_feedback: User feedback text (or None for first run).
        review_mode: "reject" or "edit" (or None for first run).
        previous_output: Previous output for edit mode reference (or None).

    Returns:
        A feedback section string, or "" if no feedback.
    """
    if not review_feedback or not review_mode:
        return ""

    if review_mode == "reject":
        return (
            "\n\n## User Feedback (IMPORTANT)\n\n"
            f"The user rejected the previous output: {review_feedback}\n\n"
            "Generate a completely new set addressing this feedback."
        )

    if review_mode == "edit":
        section = f"\n\n## Edit Instructions (IMPORTANT)\n\nThe user wants these changes: {review_feedback}\n\n"
        if previous_output:
            section += f"Previous output for reference:\n{previous_output}\n\n"
        section += "Modify according to instructions while keeping the same JSON schema."
        return section

    return ""
