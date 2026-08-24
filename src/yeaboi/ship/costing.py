"""Cost and security posture of one finished agent run, from its transcript.

agentwatch already knows how to stream a Claude Code transcript into per-model
token usage (deduped by requestId), price it through ``pricing.py``, and flag
secrets / risky bash along the way. This module reuses that machinery for a
single known file instead of a full-tree sweep.

It deliberately lives here rather than as new public API on
``agentwatch/collector.py``: this thin adapter imports that family's internals
instead of widening its surface as a side effect of the ship mode.

Reading ``~/.claude/projects`` is already permitted (and read-only) under
``fs_policy``'s built-in rules; the transcript filename is the session id, so
``locate_transcript`` is a glob, not a database.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from yeaboi.agentwatch.collector import IngestStats, _parse_file, _source_roots
from yeaboi.agentwatch.engine import _session_cost

logger = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


@dataclass(frozen=True)
class TranscriptFinding:
    """One security finding from the run's transcript (pattern + location only)."""

    kind: str = ""  # "secret" | "risky_tool"
    severity: str = ""
    label: str = ""
    line_no: int = 0


@dataclass(frozen=True)
class RunCost:
    """What one agent run cost, and what its transcript showed."""

    usd: float = 0.0
    known_models: bool = True  # False when a model priced at the fallback tier
    session_id: str = ""
    turns: int = 0
    findings: tuple[TranscriptFinding, ...] = ()


def locate_transcript(session_id: str) -> Path | None:
    """Find the transcript for *session_id* under the agent-session roots.

    The filename is the session id — Claude Code writes one
    ``<session-id>.jsonl`` per session under ``~/.claude/projects/<project>/``.
    """
    # The id comes out of the agent's own JSON envelope and goes straight into
    # an rglob pattern, so a glob metacharacter (``*``, ``?``, ``[``) would
    # match some other run's transcript and price the wrong one. Whitelist the
    # shape rather than escaping it — session ids are uuid-shaped.
    if not _SESSION_ID_RE.fullmatch(session_id or ""):
        return None
    for _source, root in _source_roots():
        if not root.is_dir():
            continue
        for candidate in root.rglob(f"{session_id}.jsonl"):
            return candidate
    return None


def cost_transcript(path: Path) -> RunCost | None:
    """Price one transcript file; None when it cannot be read at all."""
    findings: list[TranscriptFinding] = []

    def _on_finding(kind: str, severity: str, label: str, line_no: int, _session_id: str) -> None:
        findings.append(TranscriptFinding(kind=kind, severity=severity, label=label, line_no=line_no))

    stats = IngestStats()
    try:
        rollup = _parse_file(path, stats=stats, on_finding=_on_finding)
    except OSError as exc:
        logger.warning("Could not read transcript %s: %s", path, exc)
        return None
    usd, all_known = _session_cost(rollup.model_usage)
    if stats.malformed_lines:
        logger.info("Transcript %s had %d malformed lines", path.name, stats.malformed_lines)
    return RunCost(
        usd=usd,
        known_models=all_known,
        session_id=rollup.session_id,
        turns=rollup.turns,
        findings=tuple(findings),
    )
