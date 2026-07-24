"""Token budget assertions — catch prompt size regressions that inflate LLM cost.

# See docs: "Prompt Construction" — ARC framework, chain-of-thought, few-shot

Why character budgets and not token budgets?
--------------------------------------------
Counting tokens requires the tiktoken/Anthropic tokeniser library, which is
not a project dependency. Character counts are a reliable proxy: Claude uses
roughly 3.5-4 characters per token for English prose. The char limits below
correspond to approximately the token limits in the TODO comments.

Why assert at all?
------------------
Prompt builders are pure functions — they contain no LLM calls. A typo that
accidentally duplicates a large section, or a new optional context argument
that always injects a large block, will silently triple the prompt size and
cost without these assertions.

Sizing reference (measured on a typical 4-feature / 15-story project):
  System prompt    ~3 100 chars  (~780 tokens)   — budget 5 000 chars
  Analyzer prompt  ~7 200 chars  (~1 800 tokens)  — budget 20 000 chars
  Feature prompt   ~1 550 chars  (~390 tokens)    — budget 15 000 chars
  Story prompt     ~4 200 chars  (~1 050 tokens)  — budget 20 000 chars
  Sprint prompt    ~3 000 chars  (~750 tokens)    — budget 15 000 chars

The budgets are intentionally permissive — roughly 3-4× the typical size —
so that optional context blocks (repo_context, confluence_context, SCRUM.md)
can be injected without tripping the limit, while still catching runaway
regressions.

How to see live token counts in CI / locally:
  make budget-report          — runs this file with -s to print all sizes
  pytest tests/unit/test_token_budgets.py -s   — same, manually
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Character-budget constants (one source of truth for the assert messages)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_CHAR_BUDGET = 5_000  # ~1 250 tokens
ANALYZER_PROMPT_CHAR_BUDGET = 20_000  # ~5 000 tokens
FEATURE_PROMPT_CHAR_BUDGET = 15_000  # ~3 750 tokens
STORY_PROMPT_CHAR_BUDGET = 20_000  # ~5 000 tokens
SPRINT_PROMPT_CHAR_BUDGET = 15_000  # ~3 750 tokens

# Rough chars-per-token ratio for Claude/GPT-4 English prose.
_CHARS_PER_TOKEN = 4


def _token_estimate(char_count: int) -> int:
    return char_count // _CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# Shared sample data
# ---------------------------------------------------------------------------

# Realistic 26-question answers block (~7 200 chars with full questions + answers)
# Built in-line so the test file has no import-time dependency on nodes.py.


def _make_answers_block() -> str:
    """Build a realistic questionnaire answers_block for the analyzer prompt.

    Uses INTAKE_QUESTIONS so the format exactly matches what nodes.py produces.
    Each answer is a 90-char string representative of a real user response.
    """
    from yeaboi.prompts.analyzer import TOTAL_QUESTIONS
    from yeaboi.prompts.intake import INTAKE_QUESTIONS

    answer = (
        "Build a full-stack task management app with authentication, real-time notifications, and team collaboration."
    )
    lines: list[str] = []
    for q_num in range(1, TOTAL_QUESTIONS + 1):
        lines.append(f"Q{q_num}. {INTAKE_QUESTIONS[q_num]}\nA: {answer}\n")
    return "\n".join(lines)


# Pre-formatted features block (4 features, ~400 chars) — mirrors _format_features_for_prompt output.
_FEATURES_BLOCK = (
    "**F1: User Authentication & Authorization** (Priority: high)\n"
    "  OAuth2, JWT, registration, login, logout, password reset\n\n"
    "**F2: Task Management Core** (Priority: high)\n"
    "  Create, read, update, delete tasks with due dates, priorities, labels\n\n"
    "**F3: Team Collaboration** (Priority: medium)\n"
    "  Shared workspaces, task assignment, comments, @mentions, notifications\n\n"
    "**F4: Infrastructure & DevOps** (Priority: medium)\n"
    "  CI/CD pipeline, containerization, monitoring, deployment automation\n"
)

# Pre-formatted stories block (16 stories, ~1 600 chars) — mirrors
# _format_stories_for_sprint_planner output.  Used by sprint planner.
_STORIES_BLOCK = "\n".join(
    [
        f"### F{(i // 4) + 1}: Feature {(i // 4) + 1} (high)"
        if i % 4 == 0
        else f"- US-F{(i // 4) + 1}-00{(i % 4) + 1} | 3 pts | High | backend | implement feature"
        for i in range(16)
    ]
)

# Shared project-level strings
_PROJECT_NAME = "TaskFlow"
_PROJECT_DESC = "A full-stack task management application with real-time collaboration."
_TECH_STACK = "- React (frontend)\n- FastAPI (backend)\n- PostgreSQL (database)"
_GOALS = "- Ship MVP in 6 weeks\n- Support 100 concurrent users\n- Zero-downtime deployments"
_END_USERS = "- Individual developers\n- Small engineering teams (2-10 people)"
_CONSTRAINTS = "- Must integrate with existing GitHub Actions CI pipeline"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_budget(name: str, prompt: str, budget: int) -> None:
    """Assert prompt stays under budget and print its size for trend tracking.

    The print() is visible when running with -s (``make budget-report``) and
    is always shown in the CI test failure message when the budget is exceeded.
    """
    size = len(prompt)
    tokens = _token_estimate(size)
    # Always print for trend monitoring (shown with pytest -s / make budget-report)
    print(f"\n  [budget] {name}: {size:,} chars (~{tokens:,} tokens)  budget={budget:,}")
    assert size < budget, (
        f"{name} is {size:,} chars (~{tokens:,} tokens), "
        f"over the {budget:,}-char budget. "
        f"This means a prompt change has increased LLM cost by "
        f"~{size / max(1, _token_estimate(budget)):.1f}× — review the diff."
    )


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestSystemPromptBudget:
    """System prompt is injected in every single LLM call — even 1 KB extra
    multiplies across thousands of turns.

    # See docs: "Prompt Construction" — ARC framework, persona
    """

    def test_system_prompt_under_budget(self):
        """get_system_prompt() stays under 5 000 chars."""
        from yeaboi.prompts.system import get_system_prompt

        _assert_budget("system_prompt", get_system_prompt(), SYSTEM_PROMPT_CHAR_BUDGET)


class TestAnalyzerPromptBudget:
    """Analyzer prompt contains all 26 Q&A pairs — the largest non-optional block
    in the pipeline.

    # See docs: "Prompt Construction" — ARC framework
    """

    def test_typical_questionnaire_under_budget(self):
        """Analyzer prompt with a typical 26-question answers_block stays under 20 000 chars."""
        from yeaboi.prompts.analyzer import get_analyzer_prompt

        prompt = get_analyzer_prompt(
            _make_answers_block(),
            team_size=5,
            velocity_per_sprint=25,
        )
        _assert_budget("analyzer_prompt", prompt, ANALYZER_PROMPT_CHAR_BUDGET)

    def test_prompt_with_all_optional_context_under_budget(self):
        """Analyzer prompt with repo + Confluence + SCRUM.md context stays under budget.

        Optional contexts can add up to several thousand chars. This test
        ensures the worst-case (all three present) still fits the budget.
        """
        from yeaboi.prompts.analyzer import get_analyzer_prompt

        # Simulate moderately-sized optional contexts (~2 000 chars each)
        repo_ctx = "README.md: TaskFlow — task management app\n" + "src/api/ — FastAPI backend\n" * 50
        confluence_ctx = "Architecture Decision Record: Use PostgreSQL\n" * 30
        user_ctx = "SCRUM.md: This project uses trunk-based development.\n" * 30

        prompt = get_analyzer_prompt(
            _make_answers_block(),
            team_size=5,
            velocity_per_sprint=25,
            repo_context=repo_ctx,
            confluence_context=confluence_ctx,
            user_context=user_ctx,
        )
        _assert_budget("analyzer_prompt_with_full_context", prompt, ANALYZER_PROMPT_CHAR_BUDGET)


class TestFeatureGeneratorPromptBudget:
    """Feature generator receives only project analysis fields — the smallest pipeline prompt.

    # See docs: "Scrum Standards" — feature decomposition
    """

    def test_typical_project_under_budget(self):
        """Feature generator prompt with typical project analysis stays under 15 000 chars."""
        from yeaboi.prompts.feature_generator import get_feature_generator_prompt

        prompt = get_feature_generator_prompt(
            project_name=_PROJECT_NAME,
            project_description=_PROJECT_DESC,
            project_type="greenfield",
            goals=_GOALS,
            end_users=_END_USERS,
            target_state="Production on AWS with 99.9% uptime SLA",
            tech_stack=_TECH_STACK,
            constraints=_CONSTRAINTS,
            risks="- Third-party OAuth provider downtime\n- PostgreSQL migration complexity",
            target_sprints="3",
        )
        _assert_budget("feature_generator_prompt", prompt, FEATURE_PROMPT_CHAR_BUDGET)


class TestStoryWriterPromptBudget:
    """Story writer receives project analysis + all features — grows with feature count.

    # See docs: "Scrum Standards" — user story format, acceptance criteria
    """

    def test_typical_features_under_budget(self):
        """Story writer prompt with 4 typical features stays under 20 000 chars."""
        from yeaboi.prompts.story_writer import get_story_writer_prompt

        prompt = get_story_writer_prompt(
            project_name=_PROJECT_NAME,
            project_description=_PROJECT_DESC,
            project_type="greenfield",
            goals=_GOALS,
            end_users=_END_USERS,
            tech_stack=_TECH_STACK,
            constraints=_CONSTRAINTS,
            features_block=_FEATURES_BLOCK,
        )
        _assert_budget("story_writer_prompt", prompt, STORY_PROMPT_CHAR_BUDGET)

    def test_max_features_under_budget(self):
        """Story writer prompt with 6 features (the maximum) stays under budget."""
        from yeaboi.prompts.story_writer import get_story_writer_prompt

        # 6 features at ~130 chars each — the maximum allowed by feature_generator rules.
        max_features_block = "".join(
            f"**F{i}: Feature {i} Title — Descriptive Scope** (Priority: high)\n"
            f"  Implementation scope: services, APIs, data models, and UI components for feature {i}.\n\n"
            for i in range(1, 7)
        )
        prompt = get_story_writer_prompt(
            project_name=_PROJECT_NAME,
            project_description=_PROJECT_DESC,
            project_type="greenfield",
            goals=_GOALS,
            end_users=_END_USERS,
            tech_stack=_TECH_STACK,
            constraints=_CONSTRAINTS,
            features_block=max_features_block,
        )
        _assert_budget("story_writer_prompt_max_features", prompt, STORY_PROMPT_CHAR_BUDGET)


class TestSprintPlannerPromptBudget:
    """Sprint planner receives all stories — grows O(n) with story count.

    # See docs: "Scrum Standards" — sprint planning, capacity allocation
    """

    def test_typical_stories_under_budget(self):
        """Sprint planner prompt with ~16 stories stays under 15 000 chars."""
        from yeaboi.prompts.sprint_planner import get_sprint_planner_prompt

        prompt = get_sprint_planner_prompt(
            project_name=_PROJECT_NAME,
            project_description=_PROJECT_DESC,
            velocity=25,
            target_sprints=3,
            stories_block=_STORIES_BLOCK,
        )
        _assert_budget("sprint_planner_prompt", prompt, SPRINT_PROMPT_CHAR_BUDGET)

    def test_large_backlog_under_budget(self):
        """Sprint planner prompt with 30 stories (a large project) stays under budget.

        30 stories × 2-3 sprints × 10 pts/sprint is realistic for a 3-month
        project. This ensures the budget holds for larger-than-average backlogs.
        """
        from yeaboi.prompts.sprint_planner import get_sprint_planner_prompt

        pts = [1, 2, 3, 5]
        prios = ["High", "Medium", "High", "Low"]
        large_backlog = "\n".join(
            f"- US-F{(i // 6) + 1}-0{(i % 6) + 1:02d} | {pts[i % 4]} pts | {prios[i % 4]} | backend | implement feature"
            for i in range(30)
        )
        prompt = get_sprint_planner_prompt(
            project_name=_PROJECT_NAME,
            project_description=_PROJECT_DESC,
            velocity=20,
            target_sprints=5,
            stories_block=large_backlog,
        )
        _assert_budget("sprint_planner_prompt_30_stories", prompt, SPRINT_PROMPT_CHAR_BUDGET)
