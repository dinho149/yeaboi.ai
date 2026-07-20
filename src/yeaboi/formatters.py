"""Rich formatters for structured pipeline output.

# See docs: "Architecture" — REPL-side formatter layer
#
# Nodes produce markdown AIMessages (needed for LLM conversation history).
# This module provides Rich Table/Panel renderers that the REPL uses when
# structured artifacts (features, stories, tasks, sprints, project analysis)
# are available in the graph result — giving scannable, colour-coded output
# instead of raw streamed markdown.

Each public function takes artifact dataclasses and returns a Rich renderable
(Panel, Table, or Group) that the REPL prints via console.print().
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from yeaboi.agent.state import (
    PHASE_QUESTION_RANGES,
    Feature,
    Priority,
    ProjectAnalysis,
    QuestionnaireState,
    Sprint,
    Task,
    UserStory,
)
from yeaboi.prompts.intake import PHASE_LABELS, QUESTION_SHORT_LABELS

# ── Colour Vocabulary (project-wide convention) ─────────────────
#   [cyan]       commands, menu numbers, IDs, interactive elements
#   [dim]        hints, secondary text, timestamps, default markers
#   [green]      success, affirmations, user-answered tags
#   [yellow]     warnings, prompts, assumptions, caution
#   [red]        errors, critical priority, destructive actions
#   [blue]       medium priority, section borders, info panels
#   [bold]       emphasis, headings, labels
#   [magenta]    AI question label (intake questionnaire)
# ────────────────────────────────────────────────────────────────

# ── Theme Definitions ────────────────────────────────────────────
# Semantic style names that map to different colors per theme.
# Dark theme = current colors (no visual change for existing users).
# Light theme swaps to colors readable on white/cream backgrounds.
#
# See docs: "Architecture" — REPL-side UI layer

DARK_STYLES: dict[str, str] = {
    "command": "cyan",
    "hint": "dim",
    "success": "green",
    "warning": "yellow",
    "error": "red",
    "info": "blue",
    "ai.label": "bold cyan",
    "ai.question": "bold magenta",
    "user.label": "bold green",
    "priority.critical": "bold red",
    "priority.high": "yellow",
    "priority.medium": "blue",
    "priority.low": "dim",
}

LIGHT_STYLES: dict[str, str] = {
    "command": "dark_blue",
    "hint": "grey50",
    "success": "green4",
    "warning": "dark_orange",
    "error": "red3",
    "info": "navy_blue",
    "ai.label": "bold dark_blue",
    "ai.question": "bold dark_magenta",
    "user.label": "bold green4",
    "priority.critical": "bold red3",
    "priority.high": "dark_orange",
    "priority.medium": "navy_blue",
    "priority.low": "grey50",
}

# All semantic style names that must be present in every theme.
REQUIRED_THEME_KEYS: frozenset[str] = frozenset(DARK_STYLES.keys())


def build_theme(mode: str = "dark") -> Theme:
    """Build a Rich Theme from the given mode name.

    Args:
        mode: "dark" or "light". Defaults to "dark".

    Returns:
        A Rich Theme with semantic style names mapped to the
        appropriate colors for the given mode.
    """
    styles = LIGHT_STYLES if mode == "light" else DARK_STYLES
    return Theme(styles)


# ---------------------------------------------------------------------------
# Priority colour map
# ---------------------------------------------------------------------------
# Maps Priority enum values to semantic style names that are resolved via
# the active Rich Theme. This allows the same code to produce different
# colors for dark vs. light terminals.

PRIORITY_STYLES: dict[Priority, str] = {
    Priority.CRITICAL: "priority.critical",
    Priority.HIGH: "priority.high",
    Priority.MEDIUM: "priority.medium",
    Priority.LOW: "priority.low",
}


def _styled_priority(priority: Priority) -> Text:
    """Return a Rich Text object with the priority value colour-coded.

    Args:
        priority: The Priority enum value to style.

    Returns:
        A Rich Text with the appropriate colour from PRIORITY_STYLES.
    """
    style = PRIORITY_STYLES.get(priority, "")
    return Text(str(priority.value), style=style)


# Task label colour map — visual distinction for task types in REPL tables.
# Uses the same "consistent colour vocabulary" as priority colours.
# See docs: "Scrum Standards" — task decomposition, task labels
_TASK_LABEL_STYLES: dict[str, str] = {
    "Code": "bold cyan",
    "Documentation": "bold magenta",
    "Infrastructure": "bold yellow",
    "Testing": "bold green",
}


def _task_label_style(label: str) -> str:
    """Return the Rich style string for a task label value."""
    return _TASK_LABEL_STYLES.get(label, "")


# ---------------------------------------------------------------------------
# Project Analysis Panel
# ---------------------------------------------------------------------------


def _bullet_list(items: tuple[str, ...] | list[str]) -> str:
    """Format a tuple/list of strings as a bullet list, or '(none)' if empty."""
    if not items:
        return "(none)"
    return "\n".join(f"  - {item}" for item in items)


def render_analysis_panel(analysis: ProjectAnalysis, *, compact: bool = False) -> Panel:
    """Render a ProjectAnalysis as a bordered Rich Panel.

    Shows key-value pairs and bullet-list sections for goals, users,
    tech stack, constraints, risks, etc. Assumptions are highlighted
    in yellow since they represent defaults/skipped answers.

    Args:
        analysis: The ProjectAnalysis dataclass from the analyzer node.
        compact: When True, omit secondary sections (integrations,
            out-of-scope, constraints, risks, end-users).

    Returns:
        A Rich Panel ready for console.print().
    """
    sections: list[str] = []

    sections.append(f"[bold]Description:[/bold] {analysis.project_description}")
    sections.append(f"[bold]Type:[/bold] {analysis.project_type}")

    # Low-code advisory — mostly configuration/content/no-code work. Estimation
    # and task decomposition are scaled lighter downstream (see story_writer /
    # task_decomposer prompts). Shown prominently near the top of the panel.
    if getattr(analysis, "is_low_code", False):
        reason = f" — {analysis.low_code_reason}" if analysis.low_code_reason else ""
        sections.append(
            f"[yellow]⚙ [bold]Low-code project[/bold]{reason} · estimates and tasks scaled lighter.[/yellow]"
        )

    sections.append(f"\n[bold]Goals:[/bold]\n{_bullet_list(analysis.goals)}")
    if not compact:
        sections.append(f"[bold]End Users:[/bold]\n{_bullet_list(analysis.end_users)}")
    sections.append(f"[bold]Tech Stack:[/bold]\n{_bullet_list(analysis.tech_stack)}")
    if not compact:
        sections.append(f"[bold]Integrations:[/bold]\n{_bullet_list(analysis.integrations)}")
        sections.append(f"[bold]Constraints:[/bold]\n{_bullet_list(analysis.constraints)}")
        sections.append(f"[bold]Risks:[/bold]\n{_bullet_list(analysis.risks)}")
        sections.append(f"[bold]Out of Scope:[/bold]\n{_bullet_list(analysis.out_of_scope)}")

    sections.append(f"\n[bold]Target State:[/bold] {analysis.target_state}")
    sections.append(
        f"[bold]Sprint Planning:[/bold] {analysis.sprint_length_weeks}-week sprints x {analysis.target_sprints} sprints"
    )

    # Prompt quality rating — deterministic score from questionnaire tracking sets.
    # Grade colour: green for A/B, yellow for C, red for D.
    if analysis.prompt_quality:
        pq = analysis.prompt_quality
        grade_colour = "green" if pq.grade in ("A", "B") else ("yellow" if pq.grade == "C" else "red")
        total = pq.answered_count + pq.extracted_count + pq.defaulted_count + pq.skipped_count
        quality_lines = [
            f"\n[{grade_colour}][bold]Input Quality: {pq.grade} ({pq.score_pct}%)[/bold][/{grade_colour}]",
            f"  {total} questions: [green]{pq.answered_count} answered[/green]"
            f" · [cyan]{pq.extracted_count} extracted[/cyan]"
            f" · [yellow]{pq.defaulted_count} defaults[/yellow]"
            f" · [dim]{pq.skipped_count} skipped[/dim]",
        ]
        if pq.suggestions:
            quality_lines.append("  [bold]Suggestions:[/bold]")
            for suggestion in pq.suggestions:
                quality_lines.append(f"    – {suggestion}")
        sections.extend(quality_lines)

    if analysis.assumptions:
        sections.append(f"\n[yellow][bold]Assumptions:[/bold]\n{_bullet_list(analysis.assumptions)}[/yellow]")

    if analysis.scrum_md_contributions:
        fields = " · ".join(analysis.scrum_md_contributions)
        sections.append(f"\n[dim cyan]SCRUM.md enriched: {fields}[/dim cyan]")

    body = "\n".join(sections)

    return Panel(
        body,
        title=f"[bold]Project Analysis: {analysis.project_name}[/bold]",
        border_style="blue",
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Features Table
# ---------------------------------------------------------------------------


def render_features_table(features: list[Feature], *, compact: bool = False) -> Table:
    """Render a list of Features as a Rich Table with colour-coded priorities.

    Args:
        features: List of Feature dataclasses.
        compact: When True, omit the Description column.

    Returns:
        A Rich Table with columns: ID, Title, Priority, (Description).
    """
    table = Table(
        title="Features",
        show_lines=True,
        caption=f"{len(features)} feature(s)",
    )
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", style="bold")
    table.add_column("Priority")
    if not compact:
        table.add_column("Description")

    for feature in features:
        row = [feature.id, feature.title, _styled_priority(feature.priority)]
        if not compact:
            row.append(feature.description)
        table.add_row(*row)

    return table


# ---------------------------------------------------------------------------
# Stories Table (grouped by feature)
# ---------------------------------------------------------------------------


def render_stories_table(stories: list[UserStory], features: list[Feature], *, compact: bool = False) -> Group:
    """Render user stories grouped by feature, each as a Rich Table.

    Each story row shows the story sentence on the first line, followed by
    dim Given/When/Then acceptance criteria beneath it.

    Args:
        stories: List of UserStory dataclasses.
        features: List of Feature dataclasses (for grouping headers).
        compact: When True, omit acceptance criteria and Discipline column.

    Returns:
        A Rich Group containing one Table per feature.
    """
    # Build a lookup for feature titles
    feature_titles = {e.id: e.title for e in features}

    # Group stories by feature_id, preserving order
    grouped: dict[str, list[UserStory]] = {}
    for story in stories:
        grouped.setdefault(story.feature_id, []).append(story)

    tables: list[Table] = []
    for feature_id, feature_stories in grouped.items():
        feature_label = feature_titles.get(feature_id, feature_id)
        table = Table(
            title=f"Stories — {feature_label}",
            show_lines=True,
        )
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Story", ratio=2)
        table.add_column("Pts", justify="center")
        table.add_column("Priority")
        if not compact:
            table.add_column("Discipline")

        for i, story in enumerate(feature_stories):
            # Build multi-line story cell: sentence + dim acceptance criteria + DoD
            story_text = Text(story.text)
            if not compact:
                story_text.append("\n")
                for ac in story.acceptance_criteria:
                    story_text.append(f"\n  Given {ac.given}", style="dim")
                    story_text.append(f"\n  When {ac.when}", style="dim")
                    story_text.append(f"\n  Then {ac.then}", style="dim")

                # Definition of Done — applicable items shown normally,
                # non-applicable items struck through so the reader can see
                # at a glance which standards apply to this story.
                from yeaboi.agent.state import DOD_ITEMS

                # Short display labels to keep the DoD line compact in the table.
                dod_short = ("AC Met", "Docs", "Testing", "Code Merged", "SDLC", "Sign-off", "Know. Sharing")
                dod_flags = story.dod_applicable
                if len(dod_flags) == len(DOD_ITEMS):
                    story_text.append("\n\n  DoD: ", style="dim bold")
                    for j, (short, applicable) in enumerate(zip(dod_short, dod_flags)):
                        sep = "" if j == 0 else "  "
                        if applicable:
                            story_text.append(f"{sep}✓ {short}", style="dim green")
                        else:
                            story_text.append(f"{sep}✗ {short}", style="dim strike")

            if story.points_rationale:
                story_text.append("\n\n  Points rationale: ", style="dim bold")
                story_text.append(story.points_rationale, style="dim italic")

            row: list = [story.id, story_text, str(story.story_points), _styled_priority(story.priority)]
            if not compact:
                row.append(str(story.discipline.value))
            table.add_row(*row, end_section=(i < len(feature_stories) - 1))

        tables.append(table)

    return Group(*tables)


# ---------------------------------------------------------------------------
# Tasks Table (grouped by feature, with story sub-headers)
# ---------------------------------------------------------------------------


def render_tasks_table(
    tasks: list[Task], stories: list[UserStory], features: list[Feature], *, compact: bool = False
) -> Group:
    """Render tasks grouped by feature with story sub-headers.

    Within each feature table, story header rows (bold, underlined) separate
    the tasks belonging to each story.

    Args:
        tasks: List of Task dataclasses.
        stories: List of UserStory dataclasses (for grouping).
        features: List of Feature dataclasses (for table titles).
        compact: When True, omit the Description column.

    Returns:
        A Rich Group containing one Table per feature.
    """
    # Build lookups
    feature_titles = {e.id: e.title for e in features}
    story_texts = {s.id: s.text for s in stories}

    # Group tasks by story_id
    tasks_by_story: dict[str, list[Task]] = {}
    for task in tasks:
        tasks_by_story.setdefault(task.story_id, []).append(task)

    # Group stories by feature_id (only those that have tasks)
    stories_by_feature: dict[str, list[str]] = {}
    for story in stories:
        if story.id in tasks_by_story:
            stories_by_feature.setdefault(story.feature_id, []).append(story.id)

    tables: list[Table] = []
    for feature_id, story_ids in stories_by_feature.items():
        feature_label = feature_titles.get(feature_id, feature_id)
        table = Table(
            title=f"Tasks — {feature_label}",
            show_lines=True,
        )
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Label", no_wrap=True)
        table.add_column("Title", style="bold")
        if not compact:
            table.add_column("Description")

        for i, story_id in enumerate(story_ids):
            # Story header row
            story_label = story_texts.get(story_id, story_id)
            header_row = ["", "", Text(f"[{story_id}] {story_label}", style="bold underline")]
            if not compact:
                header_row.append("")
            table.add_row(*header_row)

            for task in tasks_by_story.get(story_id, []):
                label_val = task.label.value if hasattr(task.label, "value") else str(task.label)
                label_style = _task_label_style(label_val)
                row = [task.id, Text(label_val, style=label_style), task.title]
                if not compact:
                    desc = task.description
                    if task.test_plan:
                        desc += f"\n[bold]Test plan:[/bold] {task.test_plan}"
                    if task.ai_prompt:
                        desc += f"\n[bold dim]AI prompt:[/bold dim] [dim]{task.ai_prompt}[/dim]"
                    row.append(desc)
                table.add_row(*row)

            # Add section separator between stories (not after the last one)
            if i < len(story_ids) - 1:
                table.add_section()

        tables.append(table)

    return Group(*tables)


# ---------------------------------------------------------------------------
# Sprint Plan
# ---------------------------------------------------------------------------


def render_sprint_plan(
    sprints: list[Sprint],
    stories: list[UserStory],
    features: list[Feature],
    velocity: int,
    *,
    compact: bool = False,
) -> Group:
    """Render sprint plan as per-sprint Panels with capacity bars and story tables.

    Args:
        sprints: List of Sprint dataclasses.
        stories: List of UserStory dataclasses (for point/priority lookup).
        features: List of Feature dataclasses (for feature name lookup).
        velocity: Team velocity (points per sprint).
        compact: When True, omit story detail tables inside sprint panels;
            show only goal + capacity bar.

    Returns:
        A Rich Group containing a summary line and one Panel per sprint.
    """
    # Build lookups
    story_map = {s.id: s for s in stories}

    total_points = sum(
        story_map[sid].story_points for sprint in sprints for sid in sprint.story_ids if sid in story_map
    )

    summary = Text(f"{len(sprints)} sprint(s) | Velocity: {velocity} pts | Total: {total_points} pts")

    panels: list[Panel | Text] = [summary]

    for sprint in sprints:
        # Calculate sprint load
        sprint_points = sum(story_map[sid].story_points for sid in sprint.story_ids if sid in story_map)
        ratio = sprint_points / velocity if velocity > 0 else 0

        # Capacity bar: 20 chars, filled/empty
        filled = min(int(ratio * 20), 20)
        empty = 20 - filled
        if ratio > 1.0:
            bar_style = "red"
        elif ratio > 0.8:
            bar_style = "yellow"
        else:
            bar_style = "green"

        bar = Text()
        bar.append("━" * filled, style=bar_style)
        bar.append("─" * empty, style="dim")
        bar.append(f" {sprint_points}/{velocity} pts", style=bar_style)

        # Sprint border colour based on capacity
        border_style = "red" if sprint_points > velocity else "green"

        if compact:
            # Compact mode: goal + capacity bar only, no story table
            content = Group(
                Text(f"Goal: {sprint.goal}"),
                bar,
            )
        else:
            # Story table inside the panel
            story_table = Table(show_header=True, show_lines=False, pad_edge=False)
            story_table.add_column("ID", style="cyan", no_wrap=True)
            story_table.add_column("Pts", justify="center")
            story_table.add_column("Priority")
            story_table.add_column("Story")

            for sid in sprint.story_ids:
                story = story_map.get(sid)
                if story:
                    story_table.add_row(
                        sid,
                        str(story.story_points),
                        _styled_priority(story.priority),
                        story.title or story.goal,
                    )

            content = Group(
                Text(f"Goal: {sprint.goal}"),
                bar,
                Text(""),
                story_table,
            )

        panel = Panel(
            content,
            title=f"[bold]{sprint.name}[/bold]",
            border_style=border_style,
            padding=(0, 1),
        )
        panels.append(panel)

    return Group(*panels)


# ---------------------------------------------------------------------------
# Intake Summary (questionnaire answers as compact tables)
# ---------------------------------------------------------------------------
# See docs: "Architecture" — REPL-side formatter layer
#
# The markdown summary (_build_intake_summary in nodes.py) stays in the
# message history for the LLM. This function provides a REPL-only Rich
# display: one compact table per phase with short labels and colour-coded
# source tags, replacing the 150-line raw markdown stream.

# Maximum answer length before truncation — keeps the table scannable.
_ANSWER_MAX_LEN = 60

# Source tag styles — matches the project's colour vocabulary:
# green = user action, cyan = AI extraction, yellow = assumption, dim = inactive.
_SOURCE_STYLES: dict[str, str] = {
    "answered": "green",
    "from description": "cyan",
    "default": "yellow",
    "skipped": "dim",
}


def _truncate(text: str, max_len: int = _ANSWER_MAX_LEN) -> str:
    """Truncate text to max_len, appending '…' if shortened."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


def _source_tag(q_num: int, qs: QuestionnaireState) -> Text:
    """Return a colour-coded source tag for a question's answer provenance.

    Determines how the answer was obtained (user-answered, extracted from
    the initial description, defaulted, or skipped) and returns a Rich Text
    with the appropriate style.
    """
    if q_num in qs.extracted_questions:
        label, style = "from description", _SOURCE_STYLES["from description"]
    elif q_num in qs.defaulted_questions:
        label, style = "default", _SOURCE_STYLES["default"]
    elif q_num in qs.answers:
        label, style = "answered", _SOURCE_STYLES["answered"]
    else:
        label, style = "skipped", _SOURCE_STYLES["skipped"]
    return Text(label, style=style)


def render_intake_summary(qs: QuestionnaireState, *, compact: bool = False) -> Group:
    """Render the intake questionnaire answers as compact Rich Tables.

    Produces one table per phase with columns: Q#, Question (short label),
    Answer (truncated), and Source (colour-coded provenance tag). A stats
    line at the top summarises how answers were collected.

    Args:
        qs: The completed QuestionnaireState with answers populated.
        compact: When True, omit the Source column.

    Returns:
        A Rich Group containing a stats line and one Table per phase.
    """
    # Count answer sources for the stats line
    n_answered = len(qs.answers) - len(qs.extracted_questions) - len(qs.defaulted_questions)
    n_extracted = len(qs.extracted_questions)
    n_defaulted = len(qs.defaulted_questions)

    stats = Text()
    stats.append(f"{n_answered} answered", style="green")
    stats.append(" \u00b7 ")
    stats.append(f"{n_extracted} extracted", style="cyan")
    stats.append(" \u00b7 ")
    stats.append(f"{n_defaulted} defaults", style="yellow")

    tables: list[Table | Text] = [stats]

    for phase, (start, end) in PHASE_QUESTION_RANGES.items():
        phase_label = PHASE_LABELS.get(phase, str(phase))
        table = Table(title=phase_label, show_lines=False, padding=(0, 1))
        table.add_column("Q#", style="dim", justify="right", no_wrap=True)
        table.add_column("Question", style="bold")
        table.add_column("Answer", ratio=2)
        if not compact:
            table.add_column("Source", no_wrap=True)

        for q_num in range(start, end + 1):
            answer = qs.answers.get(q_num, "")
            short_label = QUESTION_SHORT_LABELS.get(q_num, f"Q{q_num}")
            row: list = [
                str(q_num),
                short_label,
                _truncate(answer) if answer else Text("(not answered)", style="dim"),
            ]
            if not compact:
                row.append(_source_tag(q_num, qs))
            table.add_row(*row)

        tables.append(table)

    return Group(*tables)
