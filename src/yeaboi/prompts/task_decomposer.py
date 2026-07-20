"""Prompt template for the task_decomposer node.

# See docs: "Prompt Construction" — ARC framework
# See docs: "Scrum Standards" — task decomposition
#
# The task decomposer prompt takes the ProjectAnalysis fields + formatted story list
# and asks the LLM to break each user story into 2-5 concrete implementation tasks.
#
# Same pattern as story_writer.py:
# - Pre-formatted strings (not dataclass imports) to avoid circular imports
# - ARC framework: Actor, Rules, Context
# - Chain-of-thought reasoning steps
# - JSON schema embedded in the prompt
# - Constants for validation bounds
"""

# Bounds for the number of tasks per story.
MIN_TASKS_PER_STORY = 2
MAX_TASKS_PER_STORY = 5

# ---------------------------------------------------------------------------
# JSON schema description embedded in the prompt so the LLM knows exactly
# what structure to produce. Returns a JSON *array* of task objects.
# ---------------------------------------------------------------------------

_JSON_SCHEMA = """\
[
  {
    "id": "string — sequential ID per story: T-US-E1-001-01, T-US-E1-001-02, ...",
    "story_id": "string — parent story ID: US-E1-001, US-E1-002, ...",
    "title": "string — short imperative task title (e.g. 'Create user registration API endpoint')",
    "description": "string — implementation detail: what to build, key technical decisions, files/components to touch",
    "label": "string — one of: Code, Documentation, Infrastructure, Testing",
    "test_plan": "string — Code/Infrastructure: what to test. Empty for Documentation/Testing.",
    "ai_prompt": "string — ARC-structured instruction for an AI coding assistant (Actor, Rules, Context)."
  }
]"""


def get_task_decomposer_prompt(
    project_name: str,
    project_type: str,
    tech_stack: str,
    stories_block: str,
    *,
    doc_context: str | None = None,
    team_calibration: str = "",
    is_low_code: bool = False,
    review_feedback: str | None = None,
    review_mode: str | None = None,
    previous_output: str | None = None,
) -> str:
    """Build the task decomposer prompt with injected project context and story list.

    # See docs: "Prompt Construction" — ARC framework
    #
    # The prompt uses the ARC pattern:
    # - Actor: "Senior Technical Lead" with task decomposition expertise
    # - Rules: 2-5 tasks/story, imperative titles, concrete descriptions
    # - Context: Pre-formatted project analysis + story list
    #
    # Why a function (not a string constant)?
    # All parameters are dynamic — they come from the ProjectAnalysis and UserStory list
    # produced by earlier nodes. A function cleanly injects these into the template.

    Args:
        project_name: Project name from analysis.
        project_type: "greenfield", "existing codebase", etc.
        tech_stack: Pre-formatted bullet list of technologies.
        stories_block: Pre-formatted text block of stories (from _format_stories_for_prompt).
        doc_context: Optional documentation context (Confluence URLs, README links,
            existing docs from Q14). Injected into the prompt so the LLM can
            reference specific documentation locations in the documentation sub-task.
        review_feedback: User feedback from a previous review (reject/edit).
        review_mode: "reject" or "edit" — controls how feedback is framed.
        previous_output: Previous output text for edit mode reference.

    Returns:
        The complete prompt string ready to send to the LLM.
    """
    from yeaboi.prompts.feature_generator import _build_review_section

    # Build optional documentation context section.
    # When the user provided documentation references during intake (Q14, Confluence
    # URLs, README links), this section tells the LLM where documentation should live
    # so the dedicated documentation sub-task can reference specific locations.
    doc_context_section = ""
    if doc_context:
        doc_context_section = f"\n### Documentation References\n{doc_context}\n"

    # Extract team task patterns from calibration text
    import re as _re

    _avg_tasks_match = _re.search(r"[Aa]vg\s+(\d+\.?\d*)\s+tasks.(?:per.)?story", team_calibration)
    if _avg_tasks_match:
        _team_avg_tasks = round(float(_avg_tasks_match.group(1)))
        _t_min = max(2, _team_avg_tasks - 1)
        _t_max = max(_team_avg_tasks + 1, 3)
        _task_count_instruction = (
            f"Break each user story into approximately {_team_avg_tasks} tasks "
            f"(range {_t_min}-{_t_max}, matching the team's historical average)."
        )
        _task_count_rule = f"1. Aim for ~{_team_avg_tasks} tasks per story (team avg). Range: {_t_min}-{_t_max}.\n"
    else:
        _task_count_instruction = (
            f"Break each user story into {MIN_TASKS_PER_STORY}-{MAX_TASKS_PER_STORY} concrete implementation tasks."
        )
        _task_count_rule = f"1. Produce {MIN_TASKS_PER_STORY}-{MAX_TASKS_PER_STORY} tasks per story.\n"

    # Extract type distribution to match team patterns
    _dist_match = _re.search(
        r"(?:Type distribution|Development)\s+(\d+)%.*?Testing\s+(\d+)%",
        team_calibration,
    )
    _dist_rule = ""
    if _dist_match:
        _dev_pct = _dist_match.group(1)
        _test_pct = _dist_match.group(2)
        _deploy_match = _re.search(r"Deploy\s+(\d+)%", team_calibration)
        _deploy_pct = _deploy_match.group(1) if _deploy_match else "0"
        _dist_rule = (
            f"   Match the team's task type distribution: ~{_dev_pct}% Code, "
            f"~{_test_pct}% Testing, ~{_deploy_pct}% Infrastructure/Deploy. "
            "Include Testing and Documentation tasks — not just Code.\n"
        )

    # Low-code projects need configuration / setup / content tasks rather than
    # build-from-scratch coding — nudge the decomposition accordingly.
    low_code_note = (
        "\n**This is a LOW-CODE project** — favour configuration, setup, content, "
        "and integration-wiring tasks over custom coding. Keep tasks lightweight; "
        "many will be `Infrastructure` (platform/config) or `Documentation` rather "
        "than `Code`.\n"
        if is_low_code
        else ""
    )

    base = (
        "You are a Senior Technical Lead with expertise in task decomposition.\n\n"
        "## Project Context\n\n"
        f"**Project:** {project_name}\n"
        f"**Type:** {project_type}\n"
        f"{low_code_note}\n"
        f"### Tech Stack\n{tech_stack}\n"
        f"{doc_context_section}\n"
        + (team_calibration + "\n" if team_calibration else "")
        + "## Stories to Decompose\n\n"
        f"{stories_block}\n\n"
        "## Task\n\n"
        f"{_task_count_instruction} "
        "Return a JSON array matching this exact schema:\n\n"
        f"```json\n{_JSON_SCHEMA}\n```\n\n"
        "## Rules\n\n"
        f"{_task_count_rule}"
        f"{_dist_rule}"
        "2. Use sequential IDs per story: T-US-E1-001-01, T-US-E1-001-02, etc.\n"
        '3. Task titles must be imperative (verb-first: "Create...", "Implement...", "Configure...").\n'
        "4. Descriptions should reference concrete technical artifacts "
        "(files, endpoints, components, DB tables).\n"
        "5. Higher-point stories should have more tasks; 1-2 point stories need fewer tasks.\n"
        "6. Tasks within a story should not overlap — each covers a distinct piece of work.\n"
        "7. Every task MUST have a `label` field — exactly one of: "
        "`Code`, `Documentation`, `Infrastructure`, `Testing`.\n"
        "   - **Code** — feature implementation, API endpoints, UI components, business logic.\n"
        "   - **Documentation** — writing docs, README updates, API reference, runbooks.\n"
        "   - **Infrastructure** — CI/CD, deployment, Docker, cloud config, environment setup.\n"
        "   - **Testing** — writing tests, test plans, test data setup, QA verification.\n"
        "8. Tasks labelled `Code` or `Infrastructure` MUST include a `test_plan` field describing "
        "what to test: unit tests, integration tests, edge cases, error scenarios. "
        "Be specific to the task (e.g. 'Unit test: POST /register returns 201 with valid data, "
        "409 for duplicate email. Integration test: full registration → login flow.').\n"
        "   - Tasks labelled `Documentation` or `Testing` should have an empty string for `test_plan`.\n"
        "9. Every task MUST include an `ai_prompt` field — a self-contained instruction that a developer\n"
        "   can paste into an AI coding tool (Cursor, Claude Code, GitHub Copilot). Structure it using\n"
        "   the ARC framework (Actor, Rules, Context):\n"
        "   - **Actor** — one sentence: the role the AI should adopt (e.g. 'You are a backend engineer\n"
        "     working on {project_name} ({tech_stack})').\n"
        "   - **Rules** — 2-4 bullet constraints: files/components to create or modify, patterns to follow,\n"
        "     the parent story's acceptance criteria this task addresses, and label-specific guidance:\n"
        "     * Code: specify files, endpoints, components, DB tables.\n"
        "     * Documentation: specify what to document, audience, format/location.\n"
        "     * Infrastructure: specify tools, config files, setup steps.\n"
        "     * Testing: specify what to test, edge cases, framework, coverage.\n"
        "   - **Context** — one sentence of surrounding context: relevant existing code, dependencies,\n"
        "     or decisions the AI should be aware of.\n"
        "   - Keep the total prompt 3-6 sentences — enough context to be useful, not so long it overwhelms.\n\n"
        "## Documentation Sub-Task Rule\n\n"
        "Stories in the stories block are annotated with `[Documentation in DoD]` when documentation "
        "is part of their Definition of Done. For each such story:\n\n"
        "- Generate **exactly one** documentation sub-task — the **last** task for that story.\n"
        '- Title it "Document <feature/component>" (imperative, verb-first).\n'
        "- The description MUST include:\n"
        "  1. Key elements to document (API contracts, configuration, architecture decisions, "
        "user-facing behaviour, setup/installation steps — whichever apply).\n"
        "  2. Links to documentation locations if provided in the Documentation References "
        "section above (Confluence space/page, README path, wiki URL).\n"
        "- **No other task** in the story should cover documentation — all documentation "
        "work is consolidated into this single sub-task.\n"
        "- Stories WITHOUT the `[Documentation in DoD]` annotation must NOT have a documentation task.\n\n"
        "## Chain of Thought\n\n"
        "Think step by step for each story:\n"
        "1. Identify the key implementation steps needed to deliver this story.\n"
        "2. Order tasks by dependency — what must be built first.\n"
        "3. Describe each task with enough detail for a developer to start working.\n"
        "4. Ensure tasks collectively cover all acceptance criteria.\n"
        "5. If the story has `[Documentation in DoD]`, add the documentation sub-task last.\n"
        "6. Write a self-contained AI prompt for each task using the ARC framework.\n\n"
        "Return ONLY the JSON array, no other text."
    )

    return base + _build_review_section(review_feedback, review_mode, previous_output)
