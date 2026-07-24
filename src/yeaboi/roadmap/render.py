"""Rendering for the RoadmapAnalysis — plaintext lines for the TUI results view.

The Roadmap card's results view is a *selectable* project list (the screen
builder styles the cursor row itself), so this module renders the per-project
body lines plus the summary/notices blocks as plain text — one source of truth
for the layout (mirrors reporting/render.py).

# See docs: "Roadmap Intake" — TUI page
"""

from __future__ import annotations

from yeaboi.agent.state import RoadmapAnalysis, RoadmapProject


def size_badge(project: RoadmapProject) -> str:
    """Return the display badge for a project's size classification."""
    return "[Large]" if project.size == "large" else "[Small]"


def format_project_lines(project: RoadmapProject, index: int, *, selected: bool = False) -> list[str]:
    """Return one project's plain-text lines for the results list.

    ``index`` is the 1-based display position (projects arrive pre-sorted by
    priority). The screen builder styles the returned lines; ``selected`` only
    switches the cursor glyph so scrolling math stays in one place.
    """
    cursor = "▸" if selected else " "
    lines = [f"{cursor} {index}. {project.name}  {size_badge(project)}"]
    if project.quarter or project.themes:
        meta = " · ".join(x for x in (project.quarter, ", ".join(project.themes)) if x)
        lines.append(f"     {meta}")
    if project.rationale:
        lines.append(f"     {project.rationale}")
    return lines


def format_analysis_lines(analysis: RoadmapAnalysis, *, selected_idx: int = -1) -> list[str]:
    """Return the whole analysis as plain-text lines (summary, projects, notices)."""
    lines: list[str] = []
    if analysis.summary:
        lines += [analysis.summary, ""]
    if not analysis.projects:
        lines += ["No projects extracted from the roadmap.", ""]
    for i, project in enumerate(analysis.projects):
        lines += format_project_lines(project, i + 1, selected=(i == selected_idx))
        lines.append("")
    if analysis.warnings:
        lines += ["⚠ Notices:"]
        lines += [f"  • {w}" for w in analysis.warnings]
    return lines
