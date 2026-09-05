"""Replay the transcript turns around one security signal.

A finding says "line 392 of this session matched curl-pipe-shell". The replay
answers the question that follows — *what was the agent doing?* — by reading
the turns before and after that line from the JSONL and returning them as
plain text: who spoke, when, which tool, what it said. Nothing here writes,
and every string goes through :func:`yeaboi.redaction.redact` and then has
the finding's own matched span masked, so a replay can be shown on any
surface without leaking the thing the finding is about.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

TEXT_CAP = 600
DEFAULT_BEFORE = 6
DEFAULT_AFTER = 4
_MAX_LINE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class ReplayTurn:
    index: int = 0
    line_no: int = 0
    at: str = ""  # HH:MM:SS from the message timestamp
    role: str = ""  # you | agent | result | system
    kind: str = ""  # text | tool_use | tool_result
    tool: str = ""
    text: str = ""  # redacted, ≤ TEXT_CAP characters
    truncated: bool = False
    flagged: bool = False


@dataclass(frozen=True)
class Replay:
    session_id: str = ""
    source_path: str = ""
    project_path: str = ""
    started_at: str = ""
    line_no: int = 0
    pattern: str = ""
    turns: tuple[ReplayTurn, ...] = ()
    focus: int = -1  # index into turns of the flagged turn, -1 when the line was not found
    warnings: tuple[str, ...] = field(default_factory=tuple)


class ReplayError(ValueError):
    """The replay cannot be produced; the message is safe to show."""


def _under_roots(path: Path) -> bool:
    from yeaboi.agentwatch.collector import _source_roots

    resolved = path.resolve()
    return any(resolved.is_relative_to(root.resolve()) for _label, root in _source_roots() if root.exists())


def _clock(timestamp: str) -> str:
    return timestamp[11:19] if len(timestamp) >= 19 and timestamp[10] == "T" else ""


def _mask(text: str, regex: re.Pattern[str] | None, label: str) -> str:
    from yeaboi.redaction import redact

    if regex is not None:
        text = regex.sub(f"[REDACTED {label}]", text)
    return redact(text)


def _cap(text: str) -> tuple[str, bool]:
    text = text.strip()
    if len(text) > TEXT_CAP:
        return text[:TEXT_CAP].rstrip() + "…", True
    return text, False


def _turns_for(record: dict, line_no: int, regex: re.Pattern[str] | None, label: str) -> list[ReplayTurn]:
    """Every renderable turn on one transcript line (a line can carry several blocks)."""
    kind = str(record.get("type") or "")
    if kind not in ("user", "assistant"):
        return []
    message = record.get("message")
    message = message if isinstance(message, dict) else {}
    at = _clock(str(record.get("timestamp") or ""))
    content = message.get("content")
    turns: list[ReplayTurn] = []
    if isinstance(content, str):
        text, truncated = _cap(_mask(content, regex, label))
        role = "you" if kind == "user" else "agent"
        turns.append(ReplayTurn(line_no=line_no, at=at, role=role, kind="text", text=text, truncated=truncated))
        return turns
    if not isinstance(content, list):
        return []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text, truncated = _cap(_mask(str(block.get("text") or ""), regex, label))
            if not text:
                continue
            role = "you" if kind == "user" else "agent"
            turns.append(ReplayTurn(line_no=line_no, at=at, role=role, kind="text", text=text, truncated=truncated))
        elif btype == "tool_use":
            name = str(block.get("name") or "tool")
            payload = block.get("input") if isinstance(block.get("input"), dict) else {}
            if isinstance(payload.get("command"), str):
                raw = payload["command"]
            elif isinstance(payload.get("file_path"), str) and len(payload) <= 2:
                raw = str(payload.get("file_path"))
            else:
                raw = json.dumps(payload, ensure_ascii=False)
            text, truncated = _cap(_mask(raw, regex, label))
            turns.append(
                ReplayTurn(
                    line_no=line_no, at=at, role="agent", kind="tool_use", tool=name, text=text, truncated=truncated
                )
            )
        elif btype == "tool_result":
            inner = block.get("content")
            if isinstance(inner, list):
                raw = "\n".join(str(c.get("text") or "") for c in inner if isinstance(c, dict))
            else:
                raw = str(inner or "")
            text, truncated = _cap(_mask(raw, regex, label))
            turns.append(
                ReplayTurn(
                    line_no=line_no,
                    at=at,
                    role="result",
                    kind="tool_result",
                    text=text or "(no output)",
                    truncated=truncated,
                )
            )
    return turns


def _pattern_regex(pattern: str) -> re.Pattern[str] | None:
    """The compiled detector behind a finding's pattern label, if it is a transcript one."""
    from yeaboi.agentwatch import collector, security_checks

    for label, regex, _guard in collector._SECRET_PATTERNS:
        if label == pattern:
            return regex
    for label, regex in collector._RISKY_BASH_PATTERNS:
        if label == pattern:
            return regex
    for raw, (label, _sev, _generic) in security_checks.SECRET_CLASSES.items():
        if label == pattern:
            return re.compile(raw)
    return None


def replay(
    source_path: str,
    line_no: int,
    *,
    pattern: str = "",
    before: int = DEFAULT_BEFORE,
    after: int = DEFAULT_AFTER,
) -> Replay:
    """The turns around ``line_no`` of one transcript. Raises ReplayError on a bad request."""
    path = Path(source_path).expanduser()
    if not path.is_file():
        raise ReplayError("that transcript is no longer on disk")
    if not _under_roots(path):
        raise ReplayError("that path is not an agent transcript this machine scans")
    if line_no < 1:
        raise ReplayError("line must be 1 or more")
    regex = _pattern_regex(pattern) if pattern else None
    label = pattern or "match"
    logger.info("agent security replay: %s line %d (%s)", path.name, line_no, pattern or "-")

    window: list[tuple[int, dict]] = []  # the last `before` renderable records
    turns: list[ReplayTurn] = []
    focus = -1
    session_id = path.stem
    project_path = ""
    started_at = ""
    warnings: list[str] = []
    remaining_after = after
    found = False
    with path.open("rb") as handle:
        for number, raw in enumerate(handle, 1):
            if len(raw) > _MAX_LINE_BYTES:
                continue
            try:
                record = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            if sid := record.get("sessionId"):
                session_id = str(sid)
            if cwd := record.get("cwd"):
                project_path = project_path or str(cwd)
            if ts := record.get("timestamp"):
                started_at = started_at or str(ts)
            if not found:
                if number < line_no:
                    if record.get("type") in ("user", "assistant"):
                        window.append((number, record))
                        if len(window) > before:
                            window.pop(0)
                    continue
                if number == line_no:
                    found = True
                    for prior_no, prior in window:
                        turns.extend(_turns_for(prior, prior_no, regex, label))
                    flagged = _turns_for(record, number, regex, label)
                    if not flagged:
                        warnings.append("the flagged line carries no message turn")
                    for turn in flagged:
                        is_hit = regex is None or regex.search(turn.text) is not None or "[REDACTED" in turn.text
                        if focus < 0 and is_hit:
                            focus = len(turns)
                            turn = ReplayTurn(**{**turn.__dict__, "flagged": True})
                        turns.append(turn)
                    if focus < 0 and flagged:
                        focus = len(turns) - len(flagged)
                        first = turns[focus]
                        turns[focus] = ReplayTurn(**{**first.__dict__, "flagged": True})
                    continue
            if found and remaining_after > 0 and record.get("type") in ("user", "assistant"):
                more = _turns_for(record, number, regex, label)
                if more:
                    turns.extend(more)
                    remaining_after -= 1
            if found and remaining_after <= 0:
                break
    if not found:
        raise ReplayError(f"line {line_no} is past the end of this transcript")
    return Replay(
        session_id=session_id,
        source_path=str(path),
        project_path=project_path,
        started_at=started_at,
        line_no=line_no,
        pattern=pattern,
        turns=tuple(ReplayTurn(**{**t.__dict__, "index": i}) for i, t in enumerate(turns)),
        focus=focus,
        warnings=tuple(warnings),
    )
