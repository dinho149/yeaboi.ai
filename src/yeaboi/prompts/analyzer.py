"""Prompt template for the project_analyzer node.

# See docs: "Prompt Construction" — ARC framework
# See docs: "Scrum Standards" — project analysis
#
# The analyzer prompt takes the confirmed 30 Q&A pairs and asks the LLM to
# extract a structured ProjectAnalysis JSON object. This is a single LLM call
# with a JSON-schema prompt — same pattern as _extract_answers_from_description
# in nodes.py.
#
# Separated from node logic following the project convention: prompts/ holds
# templates, agent/ holds node functions. The system prompt docstring says
# "node-specific detail will be injected by the relevant nodes."
"""

# TOTAL_QUESTIONS is hardcoded (not imported from agent.state or intake) to avoid
# circular import: analyzer → intake → agent.state → agent/__init__ → agent.graph
# → agent.nodes → analyzer. The value matches TOTAL_QUESTIONS in agent/state.py.
TOTAL_QUESTIONS = 30

# ---------------------------------------------------------------------------
# JSON schema description embedded in the prompt so the LLM knows exactly
# what structure to produce. Each field has extraction rules so the LLM
# maps free-text answers to the right fields deterministically.
# ---------------------------------------------------------------------------

_JSON_SCHEMA = """\
{
  "project_name": "string — concise 2-4 word project name from Q1",
  "project_description": "string — 1-2 sentence summary combining Q1, Q3, Q4",
  "project_type": "string — 'greenfield' or 'existing codebase' or similar, from Q2/Q15",
  "goals": ["string array — key goals/problems from Q3"],
  "end_users": ["string array — target user types from Q3"],
  "target_state": "string — what 'done' looks like, from Q4",
  "tech_stack": ["string array — languages, frameworks, databases from Q11"],
  "integrations": ["string array — APIs, services, third-party from Q12"],
  "constraints": ["string array — architectural constraints from Q13, deadlines from Q5"],
  "sprint_length_weeks": "integer — from Q8, default 2",
  "target_sprints": "integer — from Q10, default 0 if not specified",
  "risks": ["string array — risks and uncertainties from Q21, Q22"],
  "out_of_scope": ["string array — explicit exclusions from Q23"],
  "assumptions": ["string array — any defaulted or skipped answers that required assumptions"],
  "skip_features": "boolean — true when project is small enough that feature grouping adds no value. Default false.",
  "is_low_code": "boolean — true for mostly config/content/no-code projects, not custom engineering. Default false.",
  "low_code_reason": "string — short phrase why is_low_code is true (e.g. 'Webflow + Zapier site'). Empty when false.",
  "scrum_md_contributions": "JSON field names whose values came from SCRUM.md. Empty list if no SCRUM.md was present."
}"""


def get_analyzer_prompt(
    answers_block: str,
    team_size: int,
    velocity_per_sprint: int,
    *,
    repo_context: str | None = None,
    detected_stack: list[str] | None = None,
    confluence_context: str | None = None,
    notion_context: str | None = None,
    user_context: str | None = None,
    team_profile_summary: str = "",
    ceremony_history: str = "",
    performance_context: str = "",
    review_feedback: str | None = None,
    review_mode: str | None = None,
    previous_output: str | None = None,
) -> str:
    """Build the analyzer prompt with injected Q&A pairs and team metrics.

    # See docs: "Prompt Construction" — ARC framework
    #
    # The prompt uses the ARC pattern:
    # - Actor: "You are a project analyst"
    # - Rules: JSON schema with extraction rules per field
    # - Context: The 26 Q&A pairs + team metrics
    #
    # Why a function (not a string constant)?
    # The answers_block, team_size, and velocity are dynamic — they come from
    # the confirmed questionnaire state. A function cleanly injects these into
    # the template without string-replace hacks.
    #
    # review_feedback / review_mode / previous_output follow the same pattern
    # as get_epic_generator_prompt — they are appended via _build_review_section
    # so the LLM can apply user corrections on re-runs.

    Args:
        answers_block: Formatted string of all 26 Q&A pairs with markers
            for defaulted/skipped answers.
        team_size: Number of engineers (from Q6 / state).
        velocity_per_sprint: Story points per sprint (from Q9 / state).
        repo_context: Raw string from tool scan (file tree + README). When
            provided, a "Repository Scan" section is injected so the LLM
            can ground tech_stack and project_type in real codebase data.
        confluence_context: Search results from Confluence (page titles +
            excerpts). When provided, a "Confluence Documentation" section is
            injected so the LLM can ground constraints and integrations in
            existing architecture docs, ADRs, and runbooks.
        notion_context: Search results / page content from Notion. When provided,
            a "Notion Documentation" section is injected — same purpose as
            confluence_context, for teams whose docs live in Notion.
        user_context: Content of SCRUM.md and/or scrum-docs/ files from the
            user's project root. When provided, a "User Context" section is
            injected so the LLM can incorporate PRDs, design docs, and notes.
        review_feedback: User feedback from a previous review (edit).
        review_mode: "edit" — controls how feedback is framed.
        previous_output: Serialized previous analysis for edit mode reference.

    Returns:
        The complete prompt string ready to send to the LLM.
    """
    from yeaboi.prompts.feature_generator import _build_review_section

    # Inject repo scan results when available, placed between Team Metrics and
    # Questionnaire Answers so the LLM can ground tech_stack / project_type
    # extraction in real codebase data rather than user descriptions alone.
    # A deterministic "detected stack" (languages + frameworks parsed from the
    # scan) is offered as a hint so the LLM anchors tech_stack on real signals.
    detected_stack_line = f"Detected stack (from repo scan): {', '.join(detected_stack)}\n\n" if detected_stack else ""
    repo_section = (
        (
            "\n## Repository Scan\n\n"
            "The following was retrieved directly from the repository. "
            "Use it to ground your tech_stack, project_type, and constraint extraction "
            "— prefer repo-detected data over user descriptions where they conflict.\n\n"
            f"{detected_stack_line}"
            f"{repo_context}\n"
        )
        if repo_context
        else ""
    )

    # Inject Confluence search results when available. Architecture docs, ADRs,
    # and runbooks provide ground truth for integrations, constraints, and tech
    # choices — use them to override or supplement questionnaire answers.
    confluence_section = (
        (
            "\n## Confluence Documentation\n\n"
            "The following pages were found in the team's Confluence space. "
            "Use them to identify existing architecture decisions, integrations, "
            "constraints, and tech_stack details that the questionnaire may not capture.\n\n"
            f"{confluence_context}\n"
        )
        if confluence_context
        else ""
    )

    # Inject Notion search results / page content when available. Same role as the
    # Confluence section — Notion is the doc home for teams that don't use Confluence.
    notion_section = (
        (
            "\n## Notion Documentation\n\n"
            "The following pages were found in the team's Notion workspace. "
            "Use them to identify existing architecture decisions, integrations, "
            "constraints, and tech_stack details that the questionnaire may not capture.\n\n"
            f"{notion_context}\n"
        )
        if notion_context
        else ""
    )

    # Inject SCRUM.md content when the file exists. This is the user's own project
    # context file — free-form notes, URLs, design decisions, screenshots-as-links.
    # Placed after Confluence (team docs) but before questionnaire answers, so it
    # represents the user's deliberate supplement with highest intent signal.
    user_section = (
        (
            "\n## User Context (SCRUM.md / scrum-docs)\n\n"
            "The following was provided by the user via SCRUM.md and/or scrum-docs/ files "
            "(PRDs, design docs, architecture notes). "
            "Treat it as authoritative — it represents the user's own documentation. "
            "Use it to refine all extracted fields.\n\n"
            f"{user_context}\n"
        )
        if user_context
        else ""
    )

    # Inject team profile summary when available — surfaces historical team patterns
    # so the analyzer can flag realistic constraints (e.g. "this team's velocity is
    # 18 pts/sprint, not the assumed 25").
    team_section = (
        (
            "\n## Team Historical Profile\n\n"
            "The following was computed from the team's actual sprint history. "
            "Use it to validate velocity assumptions and flag mismatches.\n\n"
            f"{team_profile_summary}\n"
        )
        if team_profile_summary
        else ""
    )

    # Inject the team's recent Standup + Retro history when available. Retro action
    # items and recurring pain points, plus recent standup confidence, are ground
    # truth about how delivery is actually going — fold them into risks,
    # assumptions, and scope so the plan carries the team's own lessons forward.
    ceremony_section = (
        (
            "\n## Standup & Retro History\n\n"
            "The following summarises the team's recent retrospectives and daily "
            "standups. Treat it as real signal about delivery: reflect open retro "
            "action items and recurring pain points in `risks` / `assumptions` / "
            "`out_of_scope`, and let a low or declining standup confidence make your "
            "scope and target_sprints more conservative.\n\n"
            f"{ceremony_history}\n"
        )
        if ceremony_history
        else ""
    )

    # Per-engineer Performance signal — open 1:1 action items and review growth
    # areas. Makes the analysis person-aware: an engineer's growth area or a stack
    # of open 1:1 actions is a real staffing / risk consideration.
    performance_section = (
        (
            "\n## Team Performance Signal\n\n"
            "Per-engineer signal from recent 1:1s and performance reviews. Treat it as "
            "context for team capability and risk: reflect notable growth areas or a "
            "backlog of open 1:1 action items in `risks` / `assumptions` where relevant. "
            "Do NOT surface individuals' names in user-facing scope; use it only to "
            "calibrate delivery realism.\n\n"
            f"{performance_context}\n"
        )
        if performance_context
        else ""
    )

    base = (
        "You are a project analyst synthesizing intake questionnaire answers into "
        "a structured project analysis.\n\n"
        f"## Team Metrics\n"
        f"- Team size: {team_size} engineer(s)\n"
        f"- Velocity: {velocity_per_sprint} story points per sprint\n"
        f"{repo_section}"
        f"{confluence_section}"
        f"{notion_section}"
        f"{user_section}"
        f"{team_section}"
        f"{ceremony_section}"
        f"{performance_section}\n"
        f"## Questionnaire Answers ({TOTAL_QUESTIONS} questions)\n\n"
        f"{answers_block}\n\n"
        "## Task\n\n"
        "Extract a JSON object matching this exact schema:\n\n"
        f"```json\n{_JSON_SCHEMA}\n```\n\n"
        "## Rules\n\n"
        "1. Extract `project_name` from Q1 — concise 2-4 word name, not a full sentence.\n"
        "2. Combine Q1 + Q3 + Q4 for `project_description` — 1-2 clear sentences.\n"
        "3. Derive `project_type` from Q2 and Q15 — use lowercase labels like "
        '"greenfield", "existing codebase", "migration".\n'
        "4. For array fields (goals, end_users, tech_stack, etc.), split compound answers "
        "into individual items. Each item should be a single concept.\n"
        "5. `sprint_length_weeks` must be an integer (default 2 if unclear).\n"
        "6. `target_sprints` must be an integer (default 0 if not specified — means scope-based planning).\n"
        "7. `assumptions` should list any answers that were marked as *(assumed default)* "
        "or skipped — flag what assumptions the plan will rely on.\n"
        "8. If an answer was skipped with no default, use an empty array or appropriate zero value.\n"
        "9. When Edit Instructions are provided below, apply them directly — override the "
        "relevant fields. Remove any assumption that the edit corrects (e.g. if the user "
        'says "we have CI/CD", remove the "No existing CI/CD pipeline" assumption).\n'
        "10. Set `skip_features` to `true` when the project is small enough that feature grouping "
        "adds no value (guideline: target_sprints ≤ 2 AND goals ≤ 3). Default `false` when in doubt.\n"
        "11. `scrum_md_contributions`: if a SCRUM.md user context section was present above, "
        "list the exact JSON field names whose values were primarily sourced from it. "
        "Leave empty if no such section was present.\n\n"
        "Return ONLY the JSON object, no other text."
    )

    return base + _build_review_section(review_feedback, review_mode, previous_output)
