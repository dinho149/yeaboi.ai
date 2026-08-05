"""Chat transcript model — messages, artifact cards, and cached wrapped lines.

The planning live chat renders one growing conversation. Per-frame cost is
the whole design problem: at 30fps the render loop cannot re-wrap the entire
history, so every finalized message caches its wrapped lines per width and
only the streaming tail is re-wrapped each frame.

Lines are column-relative: ``width`` is the chat reading column (capped and
centered by the screen on wide terminals), and every line starts at column 0
— the screen prepends the centering margin when composing viewport rows.
Visual grammar per role: the agent speaks from the left under a ``▌ yeaboi``
accent label; the user's messages are right-aligned bubbles on the theme's
card tint; system notes are centered whispers; artifacts are boxed cards.

Artifact messages hold NO data — they render lazily from graph_state via the
existing pipeline renderers (session/_renderers.py), so the chat can never
drift from what the pipeline actually produced, and "same artifacts, same
exports" stays trivially true.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

import rich.box
from rich.cells import cell_len
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

logger = logging.getLogger(__name__)

Role = Literal["user", "assistant", "system", "artifact"]

_ASSISTANT_LABEL = "▌ yeaboi"
_USER_LABEL = "you ▐"
_ASSISTANT_BODY_STYLE = "rgb(220,220,225)"
_BODY_INDENT = "  "


def _artifact_renderable(kind: str, graph_state: dict, render_w: int):
    """Build the Rich renderable for an artifact card from live graph_state.

    Mirrors the stage dispatch in _render_pipeline_artifacts (keyed by kind
    instead of pending_review) so a card always shows exactly what the
    pipeline review screen would have shown.
    """
    from yeaboi.ui.session._renderers import (
        _render_tui_analysis,
        _render_tui_epic,
        _render_tui_features,
        _render_tui_sprint_plan,
        _render_tui_stories,
        _render_tui_tasks,
    )
    from yeaboi.ui.session._utils import _render_tui_intake_summary

    if kind == "intake_summary":
        qs = graph_state.get("questionnaire")
        return _render_tui_intake_summary(qs, render_w) if qs is not None else None
    if kind == "epic" and graph_state.get("project_analysis"):
        return _render_tui_epic(graph_state["project_analysis"], render_w=render_w)
    if kind == "analysis" and graph_state.get("project_analysis"):
        return _render_tui_analysis(
            graph_state["project_analysis"],
            sprint_capacities=graph_state.get("sprint_capacities"),
            net_velocity=graph_state.get("net_velocity_per_sprint"),
            velocity_per_sprint=graph_state.get("velocity_per_sprint"),
            team_size=graph_state.get("team_size"),
            velocity_source=graph_state.get("velocity_source"),
            context_sources=graph_state.get("context_sources"),
        )
    if kind == "features" and graph_state.get("features"):
        return _render_tui_features(graph_state["features"], render_w=render_w)
    if kind == "stories" and graph_state.get("stories"):
        return _render_tui_stories(graph_state["stories"], graph_state.get("features", []), graph_state=graph_state)
    if kind == "tasks" and graph_state.get("tasks"):
        return _render_tui_tasks(graph_state["tasks"], graph_state.get("stories", []), graph_state.get("features", []))
    if kind == "recap" and graph_state.get("sprints"):
        return _render_recap(graph_state)
    if kind == "sprints" and graph_state.get("sprints"):
        team_override = graph_state.get("_capacity_team_override", 0)
        return _render_tui_sprint_plan(
            graph_state["sprints"],
            graph_state.get("stories", []),
            graph_state.get("features", []),
            graph_state.get("velocity_per_sprint", 10),
            sprint_capacities=graph_state.get("sprint_capacities"),
            team_override_from=graph_state.get("team_size", 1) if team_override > 0 else None,
            team_size=team_override if team_override > 0 else None,
        )
    return None


def _render_recap(graph_state: dict):
    """The closing recap card: what the finished plan contains, and what to do
    next. Computed from live graph_state like every other card, so a later
    edit that regenerates stories can never leave stale counts behind."""
    from rich.console import Group

    stories = graph_state.get("stories", []) or []
    points = sum(getattr(s, "story_points", 0) or 0 for s in stories)
    counts = Text()
    parts = [
        (len(graph_state.get("features", []) or []), "epics"),
        (len(stories), "stories"),
        (len(graph_state.get("tasks", []) or []), "tasks"),
        (len(graph_state.get("sprints", []) or []), "sprints"),
    ]
    for i, (n, label) in enumerate(parts):
        if i:
            counts.append("  ·  ", style="dim")
        counts.append(str(n), style="bold white")
        counts.append(f" {label}")
    counts.append("  ·  ", style="dim")
    counts.append(str(points), style="bold white")
    counts.append(" pts total")
    steps = Text()
    steps.append("Next:  ", style="dim")
    steps.append("/export", style="bold")
    steps.append(" saves it   ·   keep chatting to refine   ·   ", style="dim")
    steps.append("Esc Esc", style="bold")
    steps.append(" to leave", style="dim")
    return Group(counts, Text(""), steps)


_ARTIFACT_TITLES = {
    "intake_summary": "Your answers",
    "analysis": "Analysis",
    "epic": "Project epic",
    "features": "Epics",
    "stories": "Stories",
    "tasks": "Tasks",
    "sprints": "Sprint plan",
    "recap": "Plan complete",
}


@dataclass
class ChatMessage:
    """One transcript entry. Finalized text caches its wrapped lines per width."""

    role: Role
    text: str = ""
    artifact_kind: str = ""  # set when role == "artifact"
    # (wrap_width, lines) — rebuilt when the width changes; artifact cards are
    # also invalidated explicitly when their graph_state data changes.
    _cache: tuple[int, list[Text]] | None = field(default=None, repr=False)

    def invalidate(self) -> None:
        self._cache = None


class ChatTranscript:
    """The conversation: append-only messages plus per-width line caches."""

    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    # -- append helpers ----------------------------------------------------

    def add_user(self, text: str) -> None:
        self.messages.append(ChatMessage("user", text))

    def add_assistant(self, text: str) -> None:
        self.messages.append(ChatMessage("assistant", text))

    def add_system(self, text: str) -> None:
        self.messages.append(ChatMessage("system", text))

    def add_artifact(self, kind: str) -> None:
        # One card per kind: a regenerated artifact (edit round) replaces its
        # earlier card so the transcript always shows the current data once.
        self.messages = [m for m in self.messages if not (m.role == "artifact" and m.artifact_kind == kind)]
        self.messages.append(ChatMessage("artifact", artifact_kind=kind))

    def has_user_message(self) -> bool:
        """True once the user has said anything — hides the getting-started card."""
        return any(m.role == "user" for m in self.messages)

    def invalidate_artifacts(self) -> None:
        """Drop artifact caches after a graph turn — their data may have changed."""
        for message in self.messages:
            if message.role == "artifact":
                message.invalidate()

    # -- rendering ---------------------------------------------------------

    def lines(
        self,
        width: int,
        graph_state: dict,
        console: Console,
        *,
        theme,
        stream_text: str | None = None,
    ) -> list[Text]:
        """All transcript lines at this width, using caches for finalized messages.

        stream_text: the partial assistant reply currently arriving — rendered
        as a plain-wrapped tail bubble with a ▌ liveness cursor. Only this
        tail is re-wrapped per frame; everything above is cache hits.
        """
        out: list[Text] = []
        for message in self.messages:
            out.extend(self._message_lines(message, width, graph_state, console, theme))
            out.append(Text(""))
        if stream_text is not None:
            out.extend(_assistant_lines(stream_text + " ▌", width, theme))
            out.append(Text(""))
        return out

    def _message_lines(self, message: ChatMessage, width: int, graph_state: dict, console: Console, theme):
        if message._cache is not None and message._cache[0] == width:
            return message._cache[1]

        if message.role == "user":
            lines = _user_lines(message.text, width, theme)
        elif message.role == "assistant":
            lines = _assistant_lines(message.text, width, theme)
        elif message.role == "system":
            lines = _system_lines(message.text, width)
        else:
            lines = self._artifact_lines(message.artifact_kind, width, graph_state, console, theme)

        message._cache = (width, lines)
        return lines

    def _artifact_lines(self, kind: str, width: int, graph_state: dict, console: Console, theme) -> list[Text]:
        from yeaboi.ui.session._utils import _render_to_lines

        render_w = max(40, width - 6)
        title = _ARTIFACT_TITLES.get(kind, kind)
        try:
            renderable = _artifact_renderable(kind, graph_state, render_w)
        except Exception:
            logger.exception("Chat artifact rendering failed: kind=%s", kind)
            renderable = None
        if renderable is None:
            return [Text(f"({title} unavailable)", style="dim")]

        card = Panel(
            renderable,
            title=f"[bold {theme.accent}] {title} [/]",
            title_align="left",
            border_style=theme.sep,
            box=rich.box.ROUNDED,
            padding=(0, 1),
            width=width,
        )
        return [Text.from_ansi(line) for line in _render_to_lines(console, card, width)]


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _assistant_lines(text: str, width: int, theme) -> list[Text]:
    """The agent's message: accent-bar label, body indented underneath.

    Wrapping happens BEFORE the ** markers are converted to bold spans, so a
    marker pair split across lines simply stays literal — never a crash or a
    style leak.
    """
    from yeaboi.ui.session._utils import _wrap_text

    wrap_w = max(20, width - 6)
    out = [Text(_ASSISTANT_LABEL, style=f"bold {theme.accent}")]
    for line in _wrap_text(text, wrap_w):
        row = Text(_BODY_INDENT, style=_ASSISTANT_BODY_STYLE)
        pos = 0
        for match in _BOLD_RE.finditer(line):
            row.append(line[pos : match.start()])
            row.append(match.group(1), style=f"bold {_ASSISTANT_BODY_STYLE}")
            pos = match.end()
        row.append(line[pos:])
        out.append(row)
    return out


def _user_lines(text: str, width: int, theme) -> list[Text]:
    """The user's message: a right-aligned bubble on the theme's card tint.

    The bubble is a solid block of background colour — no box glyphs, which
    would have to be re-drawn around every re-wrap. All lines pad to one
    block width so the tint reads as a single shape.
    """
    from yeaboi.ui.session._utils import _wrap_text

    max_bubble_w = min(width - 8, max(24, (width * 2) // 3))
    wrapped = _wrap_text(text, max_bubble_w - 4) or [""]
    block_w = min(max_bubble_w, max(cell_len(line) for line in wrapped) + 4)
    body_style = f"white on {theme.card_bg}" if theme.card_bg else "white"

    label = Text(" " * max(0, width - cell_len(_USER_LABEL)))
    label.append(_USER_LABEL, style=f"bold {theme.muted}")
    out = [label]
    for line in wrapped:
        row = Text(" " * max(0, width - block_w))
        row.append("  " + line + " " * max(0, block_w - cell_len(line) - 2), style=body_style)
        out.append(row)
    return out


def _system_lines(text: str, width: int) -> list[Text]:
    """A centered dim whisper (consent grants, export confirmations, switches)."""
    from yeaboi.ui.session._utils import _wrap_text

    out = []
    for i, line in enumerate(_wrap_text(text, max(20, width - 16))):
        shown = ("· " + line) if i == 0 else line
        pad = max(0, (width - cell_len(shown)) // 2)
        out.append(Text(" " * pad + shown, style="dim italic"))
    return out
