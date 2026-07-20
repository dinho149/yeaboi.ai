"""LLM-powered tools for the Scrum Agent ReAct loop.

# See docs: "Tools" — tool types, @tool decorator, risk levels
# See docs: "The ReAct Loop" — Thought → Action → Observation pattern
#
# These tools make a focused, single-purpose LLM call inside a @tool function.
# The agent invokes them during the ReAct loop when it needs complexity
# estimates or acceptance criteria — the tool result becomes the Observation
# that feeds the next Thought step.
#
# Why a separate LLM call inside a tool?
# The main agent loop reasons at a high level (planning, routing, summarising).
# These tools delegate narrow, structured sub-tasks to a fresh LLM call with a
# purpose-built prompt — cleaner separation of concerns than cramming everything
# into the system prompt.
#
# Risk level: low — read-only LLM inference, no filesystem or network side-effects.
"""

import logging

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from yeaboi.agent.llm import get_llm

logger = logging.getLogger(__name__)

# Allowed Fibonacci story-point values — must match StoryPointValue in agent/state.py.
# Hardcoded here (not imported) to avoid circular imports.
_FIBONACCI_POINTS = (1, 2, 3, 5, 8)


@tool
def estimate_complexity(
    description: str,
    tech_stack: str = "",
    team_calibration: str = "",
) -> str:
    """Estimate the story point complexity of a user story or requirement.

    Analyzes the description and optional tech stack context, then returns a
    Fibonacci story point estimate (1, 2, 3, 5, 8) with a brief rationale.
    Use this when you need to assign or validate story points during planning.

    description: The user story or requirement text to analyze.
    tech_stack: Optional comma-separated list of technologies (e.g. "React, FastAPI, PostgreSQL").
    team_calibration: Optional team-specific calibration data to replace generic rules.
    """
    # See docs: "Tools" — LLM-powered tool pattern
    # See docs: "Prompt Construction" — ARC framework
    stack_line = f"\nTech stack: {tech_stack}" if tech_stack.strip() else ""

    # When team calibration data is available, use team-specific rules instead
    # of generic Fibonacci descriptions. The calibration section describes what
    # each point value means for THIS team based on historical data.
    if team_calibration.strip():
        rules_section = (
            "## Rules\n\n"
            "Use the team's historical calibration data below instead of generic estimates.\n\n"
            f"{team_calibration}\n\n"
            f"Points must be one of: {', '.join(str(p) for p in _FIBONACCI_POINTS)}.\n"
            "Consider: scope, uncertainty, dependencies, testing effort, and tech stack complexity.\n"
            "If the description is too vague to estimate, say so and suggest what information is needed.\n\n"
        )
    else:
        rules_section = (
            "## Rules\n\n"
            "1. 1 pt — trivial change, no unknowns, single file.\n"
            "2. 2 pts — small, well-understood, minimal risk.\n"
            "3. 3 pts — moderate scope, some design decisions, low risk.\n"
            "4. 5 pts — significant scope, cross-cutting concerns, or moderate unknowns.\n"
            "5. 8 pts — large, high uncertainty, multiple subsystems, or research needed.\n"
            "6. Consider: scope, uncertainty, dependencies, testing effort, and tech stack complexity.\n"
            "7. If the description is too vague to estimate, say so and suggest what information is needed.\n\n"
        )

    prompt = (
        "You are a Senior Scrum Master estimating story point complexity.\n\n"
        "## Story / Requirement\n\n"
        f"{description}{stack_line}\n\n"
        "## Task\n\n"
        f"Estimate the complexity using Fibonacci points: {', '.join(str(p) for p in _FIBONACCI_POINTS)}.\n\n"
        + rules_section
        + "## Output format\n\n"
        "Respond with:\n"
        "Story Points: <number>\n\n"
        "Rationale:\n"
        "- <bullet 1>\n"
        "- <bullet 2>\n"
        "- <bullet 3 — max 4 bullets total>\n\n"
        "Return only this format, no other text."
    )

    # temperature=0.2 — slight warmth for nuanced reasoning, still mostly deterministic.
    # See docs: "Agentic Blueprint Reference" — using the LLM outside the main graph
    logger.debug("estimate_complexity called: description length=%d chars", len(description))
    try:
        response = get_llm(temperature=0.2).invoke([HumanMessage(content=prompt)])
        logger.debug("estimate_complexity completed (%d chars response)", len(response.content))
        # Local think-by-default models embed <think> blocks in prose output.
        from yeaboi.agent.llm import strip_think_tags, track_usage

        # ReAct-tool calls bill like any other — without this the Usage page
        # undercounts every conversational estimation request.
        track_usage(response)
        return strip_think_tags(response.content)
    except Exception as e:
        logger.error("Error in estimate_complexity: %s", e)
        return f"Error estimating complexity: {e}"


@tool
def generate_acceptance_criteria(
    story: str,
    context: str = "",
) -> str:
    """Generate Given/When/Then acceptance criteria for a user story.

    Produces 2-4 acceptance criteria in the standard Given/When/Then format.
    Use this when drafting or reviewing user stories that lack detailed ACs,
    or when a user asks to expand the acceptance criteria for a specific story.

    story: The user story text (e.g. "As a user, I want to reset my password...").
    context: Optional extra context — tech stack, constraints, or related stories.
    """
    # See docs: "Scrum Standards" — story format, acceptance criteria
    # See docs: "Prompt Construction" — ARC framework
    context_line = f"\n\nAdditional context:\n{context}" if context.strip() else ""
    prompt = (
        "You are a Senior Scrum Master writing acceptance criteria.\n\n"
        "## User Story\n\n"
        f"{story}{context_line}\n\n"
        "## Task\n\n"
        "Write 2-4 acceptance criteria in Given/When/Then format.\n\n"
        "## Rules\n\n"
        "1. Each criterion must have exactly one Given, one When, and one Then clause.\n"
        "2. Given — describes the precondition or system state.\n"
        "3. When — describes the user action or triggering event.\n"
        "4. Then — describes the observable outcome or system response.\n"
        "5. Cover the happy path first, then the most important edge case.\n"
        "6. Be concrete and testable — avoid vague words like 'properly' or 'correctly'.\n"
        "7. Do not reference implementation details (e.g. 'clicks a React button').\n\n"
        "## Output format\n\n"
        "Acceptance Criteria:\n\n"
        "1. Given <precondition>\n"
        "   When <action>\n"
        "   Then <outcome>\n\n"
        "2. Given <precondition>\n"
        "   When <action>\n"
        "   Then <outcome>\n\n"
        "(repeat for 3-4 if needed)\n\n"
        "Return only this format, no other text."
    )

    # temperature=0.3 — slightly warmer to allow creative but realistic AC generation.
    logger.debug("generate_acceptance_criteria called: story length=%d chars", len(story))
    try:
        response = get_llm(temperature=0.3).invoke([HumanMessage(content=prompt)])
        logger.debug("generate_acceptance_criteria completed (%d chars)", len(response.content))
        # Local think-by-default models embed <think> blocks in prose output.
        from yeaboi.agent.llm import strip_think_tags, track_usage

        # ReAct-tool calls bill like any other — see estimate_complexity.
        track_usage(response)
        return strip_think_tags(response.content)
    except Exception as e:
        logger.error("Error in generate_acceptance_criteria: %s", e)
        return f"Error generating acceptance criteria: {e}"
