"""Chat transcript export — the planning conversation as Markdown.

A first-class artifact beside the plan exports: `scrum-chat.md` records the
conversation (greeting/size preamble + every user/assistant turn), while the
plan exports keep owning the structured artifacts — artifact cards appear
here only as one-line markers.

Security contract (see docs: "Guardrails" — output layer):
- `redaction.redact()` ALWAYS runs on the final text. Secrets pasted into
  chat must never leave the machine via export, copy, or publish.
- Tool payloads are never dumped — a tool turn collapses to
  `_(used tool: name)_` (payloads can contain repo file contents).
- Optional anonymize replacements (`anon`) mask names the same way shared
  documents do.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage, messages_from_dict

logger = logging.getLogger(__name__)


def _normalize_messages(raw: list) -> list[BaseMessage]:
    """Accept both BaseMessage lists and messages_to_dict() output.

    persistence.py serializes messages to dicts; a state handed over before
    rehydration must render identically to a live one. Unknown entries are
    skipped with a debug log rather than crashing an export.
    """
    if raw and isinstance(raw[0], dict):
        try:
            return messages_from_dict(raw)
        except Exception:
            logger.debug("messages_from_dict failed; filtering item-wise", exc_info=True)
            out: list[BaseMessage] = []
            for item in raw:
                try:
                    out.extend(messages_from_dict([item]))
                except Exception:
                    logger.debug("Skipping unrenderable message item: %r", type(item))
            return out
    return [m for m in raw if isinstance(m, BaseMessage)]


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) and block.get("type") == "text" else ""
            for block in content
        )
    return ""


def build_chat_transcript_markdown(graph_state: dict, *, anon=None) -> str:
    """Render the planning conversation as Markdown (always redacted).

    anon: optional anonymize result (with .replacements) — applied line-wise,
    mirroring sharing/documents.py's masked documents.
    """
    from yeaboi.redaction import redact

    analysis = graph_state.get("project_analysis")
    project_name = getattr(analysis, "project_name", "") or "Planning session"

    lines: list[str] = [f"# Chat Transcript — {project_name}", ""]

    preamble = graph_state.get("_chat_preamble") or []
    messages = _normalize_messages(list(graph_state.get("messages", [])))
    count = 0

    def _add(role_header: str, text: str) -> None:
        nonlocal count
        lines.append(role_header)
        lines.append("")
        lines.append(text.strip())
        lines.append("")
        count += 1

    for entry in preamble:
        if not isinstance(entry, dict):
            continue
        header = "### You" if entry.get("role") == "user" else "### yeaboi"
        text = str(entry.get("text", ""))
        if text:
            _add(header, text)

    for message in messages:
        if isinstance(message, HumanMessage):
            text = _text_of(message.content)
            if text:
                _add("### You", text)
        elif isinstance(message, AIMessage):
            if message.tool_calls:
                # Tool-call turns: name the tools, never dump arguments.
                for call in message.tool_calls:
                    lines.append(f"_(used tool: {call.get('name', 'unknown')})_")
                    lines.append("")
                continue
            text = _text_of(message.content)
            if text:
                _add("### yeaboi", text)
        elif isinstance(message, ToolMessage):
            continue  # results ride back into the next assistant turn
        else:
            logger.debug("Transcript: skipping %s", type(message).__name__)

    lines.insert(2, f"_{count} messages_")
    lines.insert(3, "")

    # Artifact markers — the plan export owns the artifact bodies.
    markers = [
        ("project_analysis", "Analysis generated"),
        ("features", "Epics generated"),
        ("stories", "Stories generated"),
        ("tasks", "Tasks generated"),
        ("sprints", "Sprint plan generated"),
    ]
    generated = [label for key, label in markers if graph_state.get(key)]
    if generated:
        lines.append("---")
        lines.append("")
        for label in generated:
            lines.append(f"- {label}")
        lines.append("")

    # Attachments section: [image #N] chips stay in the text; the recorded
    # paths let export_targets.localize_images copy files next to the .md.
    image_paths = [
        *(graph_state.get("pasted_images") or []),
        *(graph_state.get("review_feedback_images") or []),
        *(graph_state.get("chat_images") or []),
    ]
    if image_paths:
        lines.append("## Attachments")
        lines.append("")
        for path in image_paths:
            name = Path(str(path)).name
            lines.append(f"![{name}]({path})")
        lines.append("")

    text = "\n".join(lines)
    if anon is not None and getattr(anon, "replacements", None):
        from yeaboi.anonymize.apply import mask_lines

        text = "\n".join(mask_lines(text.split("\n"), anon.replacements))
    return redact(text)


def export_chat_transcript(graph_state: dict, path: Path, *, anon=None) -> Path:
    """Write the transcript to disk (fs-policy checked, images localized)."""
    from yeaboi.export_targets import localize_images
    from yeaboi.fs_policy import resolve_and_check

    output_path = resolve_and_check(path, mode="write", context="chat transcript export")
    markdown = build_chat_transcript_markdown(graph_state, anon=anon)
    output_path.write_text(localize_images(markdown, output_path.parent))
    message_count = markdown.count("### ")
    logger.info("Exported transcript: path=%s messages=%d", output_path, message_count)
    return output_path
