"""Artifact editors for tasks, sprints, epics, and project analysis.

# See docs: "Architecture" — each editor converts a frozen dataclass to
# editable text, opens the generic buffer editor loop, then parses the
# result back into a new dataclass instance.
"""

from __future__ import annotations

import logging
import re
import time

from rich.console import Console
from rich.live import Live

from yeaboi.agent.state import Feature, Priority, ProjectAnalysis, Sprint, Task
from yeaboi.ui.session.editor._editor_core import edit_buffer_loop, render_editor_panel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Valid priority values (shared by feature + story editors)
# ---------------------------------------------------------------------------

_VALID_PRIORITIES = {p.value for p in Priority}


# ---------------------------------------------------------------------------
# Task editor
# ---------------------------------------------------------------------------


def _tasks_to_text(task_list: list[Task]) -> str:
    """Convert a list of Tasks to structured editable text."""
    w = 13  # len("Description: ")
    lines: list[str] = []
    for i, task in enumerate(task_list):
        if i > 0:
            lines.append("")
        lines.append(f"\u2500\u2500 {task.id} \u2500\u2500")
        lines.append("")
        lines.append(f"{'Title:':<{w}}{task.title}")
        lines.append("")
        lines.append(f"{'Description:':<{w}}{task.description}")
    return "\n".join(lines)


def _parse_edited_tasks(text: str, originals: list[Task]) -> list[Task]:
    """Parse structured editor text back into a list of Tasks."""
    blocks = _split_section_blocks(text)
    results: list[Task] = []
    for idx, original in enumerate(originals):
        if idx < len(blocks):
            fields = _extract_fields(blocks[idx], ("Title", "Description"))
            results.append(
                Task(
                    id=original.id,
                    story_id=original.story_id,
                    title=fields.get("title", original.title) or original.title,
                    description=fields.get("description", original.description),
                    label=original.label,
                    test_plan=original.test_plan,
                    ai_prompt=original.ai_prompt,
                )
            )
        else:
            results.append(original)
    return results


def _task_editable_start(line: str) -> int | None:
    """Return column where editable value starts, or None if non-editable."""
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith("\u2500\u2500") and stripped.endswith("\u2500\u2500"):
        return None
    m = re.match(r"^(Title|Description)\s*:\s*", line)
    if m:
        return m.end()
    return 0


def edit_task(
    live: Live,
    console: Console,
    task_list: list[Task],
    _key,
    *,
    width: int = 80,
    height: int = 24,
    story_id: str = "",
) -> list[Task] | None:
    """Open the text editor for a list of Tasks belonging to one story.

    Returns a list of edited Tasks, or None if cancelled (Esc).
    """
    text = _tasks_to_text(task_list)
    buffer = text.split("\n")
    cursor_row, cursor_col = _find_first_editable(buffer, _task_editable_start)
    display_id = story_id or (task_list[0].story_id if task_list else "")
    logger.info("editor: task editor opened: story=%s count=%d", display_id, len(task_list))

    _ed_anim0 = time.monotonic()  # shimmer title clock

    def _render(buf, cr, cc, so, w, h):
        return render_editor_panel(
            buf,
            cr,
            cc,
            so,
            width=w,
            height=h,
            editor_label=f"tasks for {display_id}",
            shimmer_tick=time.monotonic() - _ed_anim0,
        )

    result = edit_buffer_loop(
        live,
        console,
        buffer,
        cursor_row,
        cursor_col,
        _key,
        editable_start_fn=_task_editable_start,
        render_fn=_render,
    )
    if result is None:
        logger.info("editor: task edit cancelled: story=%s", display_id)
        return None
    logger.info("editor: task edit saved: story=%s", display_id)
    return _parse_edited_tasks("\n".join(result), task_list)


# ---------------------------------------------------------------------------
# Sprint editor
# ---------------------------------------------------------------------------


def _sprint_to_text(sprint: Sprint) -> str:
    """Convert a Sprint to structured editable text."""
    w = 10  # len("Capacity: ")
    lines: list[str] = []
    lines.append(f"{'Name:':<{w}}{sprint.name}")
    lines.append("")
    lines.append(f"{'Goal:':<{w}}{sprint.goal}")
    lines.append("")
    lines.append(f"{'Capacity:':<{w}}{sprint.capacity_points}")
    return "\n".join(lines)


def _parse_edited_sprint(text: str, original: Sprint) -> Sprint:
    """Parse structured editor text back into a Sprint."""
    fields: dict[str, str] = {}
    for line in text.split("\n"):
        m = re.match(r"^(Name|Goal|Capacity)\s*:\s*(.*)$", line.strip())
        if m:
            fields[m.group(1).lower()] = m.group(2).strip()
    try:
        capacity = int(fields.get("capacity", str(original.capacity_points)))
    except ValueError:
        capacity = original.capacity_points
    return Sprint(
        id=original.id,
        name=fields.get("name", original.name) or original.name,
        goal=fields.get("goal", original.goal) or original.goal,
        capacity_points=capacity,
        story_ids=original.story_ids,
    )


def _sprint_editable_start(line: str) -> int | None:
    """Return column where editable value starts, or None if non-editable."""
    stripped = line.strip()
    if not stripped:
        return None
    m = re.match(r"^(Name|Goal|Capacity)\s*:\s*", line)
    if m:
        return m.end()
    return 0


def edit_sprint(
    live: Live,
    console: Console,
    sprint: Sprint,
    _key,
    *,
    width: int = 80,
    height: int = 24,
) -> Sprint | None:
    """Open the text editor for a Sprint.

    Returns a new Sprint with edited fields, or None if cancelled (Esc).
    """
    text = _sprint_to_text(sprint)
    buffer = text.split("\n")
    cursor_row, cursor_col = _find_first_editable(buffer, _sprint_editable_start)
    logger.info("editor: sprint editor opened: %s", sprint.id)

    _ed_anim0 = time.monotonic()  # shimmer title clock

    def _render(buf, cr, cc, so, w, h):
        return render_editor_panel(
            buf,
            cr,
            cc,
            so,
            width=w,
            height=h,
            editor_label=sprint.name,
            shimmer_tick=time.monotonic() - _ed_anim0,
        )

    result = edit_buffer_loop(
        live,
        console,
        buffer,
        cursor_row,
        cursor_col,
        _key,
        editable_start_fn=_sprint_editable_start,
        render_fn=_render,
    )
    if result is None:
        logger.info("editor: sprint edit cancelled: %s", sprint.id)
        return None
    logger.info("editor: sprint edit saved: %s", sprint.id)
    return _parse_edited_sprint("\n".join(result), sprint)


# ---------------------------------------------------------------------------
# Analysis editor
# ---------------------------------------------------------------------------

_ANALYSIS_KV_LABELS = ("Description", "Type", "Target State", "Sprint Length", "Target Sprints")
_ANALYSIS_LIST_LABELS = (
    "Goals",
    "End Users",
    "Tech Stack",
    "Integrations",
    "Constraints",
    "Risks",
    "Out of Scope",
    "Assumptions",
)
_ANALYSIS_FIELD_RE = re.compile(r"^(" + "|".join(_ANALYSIS_KV_LABELS) + r")\s*:\s*")
_ANALYSIS_SECTION_RE = re.compile(r"^(" + "|".join(_ANALYSIS_LIST_LABELS) + r")$")


def _analysis_to_text(analysis: ProjectAnalysis) -> str:
    """Convert a ProjectAnalysis to structured editable text."""
    w = 16  # widest label "Target Sprints: "
    lines: list[str] = []
    lines.append(f"{'Description:':<{w}}{analysis.project_description}")
    lines.append(f"{'Type:':<{w}}{analysis.project_type}")
    lines.append(f"{'Target State:':<{w}}{analysis.target_state}")
    lines.append(f"{'Sprint Length:':<{w}}{analysis.sprint_length_weeks}")
    lines.append(f"{'Target Sprints:':<{w}}{analysis.target_sprints}")
    lines.append("")

    def _list_section(label: str, items: tuple[str, ...]) -> None:
        lines.append(label)
        if items:
            for item in items:
                lines.append(f"  \u2013 {item}")
        else:
            lines.append("  \u2013 ")
        lines.append("")

    _list_section("Goals", analysis.goals)
    _list_section("End Users", analysis.end_users)
    _list_section("Tech Stack", analysis.tech_stack)
    _list_section("Integrations", analysis.integrations)
    _list_section("Constraints", analysis.constraints)
    _list_section("Risks", analysis.risks)
    _list_section("Out of Scope", analysis.out_of_scope)
    _list_section("Assumptions", analysis.assumptions)
    return "\n".join(lines)


def _parse_edited_analysis(text: str, original: ProjectAnalysis) -> ProjectAnalysis:
    """Parse structured editor text back into a ProjectAnalysis."""
    kv: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    current_section: str | None = None

    for line in text.split("\n"):
        stripped = line.strip()
        m = _ANALYSIS_FIELD_RE.match(stripped)
        if m:
            kv[m.group(1)] = stripped[m.end() :].strip()
            current_section = None
            continue
        if stripped in _ANALYSIS_LIST_LABELS:
            current_section = stripped
            lists.setdefault(current_section, [])
            continue
        if current_section and stripped.startswith("\u2013"):
            item = stripped.lstrip("\u2013").strip()
            if item:
                lists.setdefault(current_section, []).append(item)
            continue

    try:
        sprint_len = int(kv.get("Sprint Length", str(original.sprint_length_weeks)))
    except ValueError:
        sprint_len = original.sprint_length_weeks
    try:
        target_spr = int(kv.get("Target Sprints", str(original.target_sprints)))
    except ValueError:
        target_spr = original.target_sprints

    return ProjectAnalysis(
        project_name=original.project_name,
        project_description=kv.get("Description", original.project_description),
        project_type=kv.get("Type", original.project_type),
        goals=tuple(lists.get("Goals", list(original.goals))),
        end_users=tuple(lists.get("End Users", list(original.end_users))),
        target_state=kv.get("Target State", original.target_state),
        tech_stack=tuple(lists.get("Tech Stack", list(original.tech_stack))),
        integrations=tuple(lists.get("Integrations", list(original.integrations))),
        constraints=tuple(lists.get("Constraints", list(original.constraints))),
        sprint_length_weeks=sprint_len,
        target_sprints=target_spr,
        risks=tuple(lists.get("Risks", list(original.risks))),
        out_of_scope=tuple(lists.get("Out of Scope", list(original.out_of_scope))),
        assumptions=tuple(lists.get("Assumptions", list(original.assumptions))),
        scrum_md_contributions=original.scrum_md_contributions,
    )


def _analysis_editable_start(line: str) -> int | None:
    """Return column where editable value starts, or None if non-editable."""
    stripped = line.strip()
    if not stripped:
        return None
    if _ANALYSIS_SECTION_RE.match(stripped):
        return None
    m = _ANALYSIS_FIELD_RE.match(line)
    if m:
        return m.end()
    bm = re.match(r"^(\s*\u2013\s*)", line)
    if bm:
        return bm.end()
    return 0


def edit_analysis(
    live: Live,
    console: Console,
    analysis: ProjectAnalysis,
    _key,
    *,
    width: int = 80,
    height: int = 24,
) -> ProjectAnalysis | None:
    """Open the text editor for a ProjectAnalysis.

    Returns a new ProjectAnalysis with edited fields, or None if cancelled (Esc).
    """
    text = _analysis_to_text(analysis)
    buffer = text.split("\n")
    cursor_row, cursor_col = _find_first_editable(buffer, _analysis_editable_start)
    logger.info("editor: analysis editor opened")

    _ed_anim0 = time.monotonic()  # shimmer title clock

    def _render(buf, cr, cc, so, w, h):
        return render_editor_panel(
            buf,
            cr,
            cc,
            so,
            width=w,
            height=h,
            editor_label=analysis.project_name,
            shimmer_tick=time.monotonic() - _ed_anim0,
        )

    result = edit_buffer_loop(
        live,
        console,
        buffer,
        cursor_row,
        cursor_col,
        _key,
        editable_start_fn=_analysis_editable_start,
        render_fn=_render,
    )
    if result is None:
        logger.info("editor: analysis edit cancelled")
        return None
    logger.info("editor: analysis edit saved")
    return _parse_edited_analysis("\n".join(result), analysis)


# ---------------------------------------------------------------------------
# Feature editor
# ---------------------------------------------------------------------------


def _features_to_text(features: list[Feature]) -> str:
    """Convert a list of Features to structured editable text."""
    w = 13  # len("Description: ")
    lines: list[str] = []
    for i, feature in enumerate(features):
        if i > 0:
            lines.append("")
        lines.append(f"\u2500\u2500 {feature.id} \u2500\u2500")
        lines.append("")
        lines.append(f"{'Title:':<{w}}{feature.title}")
        lines.append("")
        lines.append(f"{'Description:':<{w}}{feature.description}")
        lines.append("")
        lines.append(f"{'Priority:':<{w}}{feature.priority.value}")
    return "\n".join(lines)


def _parse_edited_features(text: str, originals: list[Feature]) -> list[Feature]:
    """Parse structured editor text back into a list of Features."""
    blocks = _split_section_blocks(text)
    results: list[Feature] = []
    for idx, original in enumerate(originals):
        if idx < len(blocks):
            fields = _extract_fields(blocks[idx], ("Title", "Description", "Priority"))
            pri_str = fields.get("priority", original.priority.value).lower()
            priority = Priority(pri_str) if pri_str in _VALID_PRIORITIES else original.priority
            results.append(
                Feature(
                    id=original.id,
                    title=fields.get("title", original.title) or original.title,
                    description=fields.get("description", original.description),
                    priority=priority,
                )
            )
        else:
            results.append(original)
    return results


def _feature_editable_start(line: str) -> int | None:
    """Return column where editable value starts, or None if non-editable."""
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith("\u2500\u2500") and stripped.endswith("\u2500\u2500"):
        return None
    m = re.match(r"^(Title|Description|Priority)\s*:\s*", line)
    if m:
        return m.end()
    return 0


def edit_feature(
    live: Live,
    console: Console,
    features: list[Feature],
    _key,
    *,
    width: int = 80,
    height: int = 24,
) -> list[Feature] | None:
    """Open the text editor for all Features at once.

    Returns a list of edited Features, or None if cancelled (Esc).
    """
    text = _features_to_text(features)
    buffer = text.split("\n")
    cursor_row, cursor_col = _find_first_editable(buffer, _feature_editable_start)
    logger.info("editor: feature editor opened: count=%d", len(features))

    _ed_anim0 = time.monotonic()  # shimmer title clock

    def _render(buf, cr, cc, so, w, h):
        return render_editor_panel(
            buf,
            cr,
            cc,
            so,
            width=w,
            height=h,
            editor_label="Features",
            shimmer_tick=time.monotonic() - _ed_anim0,
        )

    result = edit_buffer_loop(
        live,
        console,
        buffer,
        cursor_row,
        cursor_col,
        _key,
        editable_start_fn=_feature_editable_start,
        render_fn=_render,
    )
    if result is None:
        logger.info("editor: feature edit cancelled")
        return None
    logger.info("editor: feature edit saved: count=%d", len(features))
    return _parse_edited_features("\n".join(result), features)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _find_first_editable(buffer: list[str], editable_fn) -> tuple[int, int]:
    """Find the first editable row and its editable start column."""
    for i, line in enumerate(buffer):
        col = editable_fn(line)
        if col is not None:
            return i, col
    return 0, 0


def _split_section_blocks(text: str) -> list[list[str]]:
    """Split editor text into blocks by section headers (\u2500\u2500 ... \u2500\u2500)."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("\u2500\u2500") and stripped.endswith("\u2500\u2500"):
            if current:
                blocks.append(current)
            current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _extract_fields(block: list[str], field_names: tuple[str, ...]) -> dict[str, str]:
    """Extract field values from a text block by matching "Label: value" lines."""
    pattern = re.compile(r"^(" + "|".join(field_names) + r")\s*:\s*(.*)$")
    fields: dict[str, str] = {}
    for line in block:
        m = pattern.match(line.strip())
        if m:
            fields[m.group(1).lower()] = m.group(2).strip()
    return fields
