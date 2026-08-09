"""Local agent-session ingestion for agentwatch.

Scans Claude Code session transcripts — append-only JSONL files under
``~/.claude/projects/**`` — and rolls each one up into an ``agent_sessions``
row: per-model token totals (including the 5m/1h cache-write split Claude Code
reports), tool-use counts, turns, project path, branch, and timestamps.

Two invariants shape the design:

1. **Privacy** — nothing from a transcript's *content* is persisted. The one
   pass over raw text happens here, in the stream, and emits only
   ``(pattern label, file, line number)`` security findings.
2. **Correct token math** — Claude Code splits one assistant API message
   across multiple JSONL lines that share a ``requestId`` and repeat identical
   usage, so usage is counted **once per requestId** and tool_use blocks once
   per block id. That dedup needs whole-file context, which drives the cursor
   design: a file whose (size, mtime) is unchanged is skipped without being
   opened; any change triggers a full streaming reparse of just that file,
   whose rollup *replaces* the previous one. No partial offsets, no
   double-count.

Malformed lines are counted and skipped, and a failing file becomes a warning
— ``refresh`` never raises.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from yeaboi.agentwatch.store import AgentWatchStore
from yeaboi.analysis.progress import send_component_progress
from yeaboi.redaction import _TOKEN_PATTERNS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Security signal patterns (labels only — matched text is never stored)
# ---------------------------------------------------------------------------

# Risky shell shapes scanned over tool_use inputs that carry a "command".
# Each entry: (label, severity, compiled regex).
_RISKY_BASH_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("curl-pipe-shell", "high", re.compile(r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b")),
    ("base64-decode-pipe-shell", "high", re.compile(r"base64\s+(?:-d|--decode)[^|;&]*\|\s*(?:ba|z|da)?sh\b")),
    ("rm-rf-root", "high", re.compile(r"\brm\s+-[a-z]*rf?[a-z]*\s+/(?:\s|$)")),
    ("permission-bypass-flag", "high", re.compile(r"--dangerously-skip-permissions\b")),
    ("sudo", "medium", re.compile(r"(?:^|[;&|]\s*)sudo\s")),
)


def _pattern_label(pattern: str) -> str:
    """Derive a stable human-readable label from a token regex's literal head.

    Labels are derived rather than hand-listed so redaction._TOKEN_PATTERNS can
    grow without this module drifting out of sync.
    """
    head = re.match(r"[\w./-]+", pattern.replace("\\.", "."))
    literal = head.group(0) if head else pattern[:12]
    return f"secret-{literal.rstrip('-_').lower()}" if literal else "secret-token"


_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (_pattern_label(p), re.compile(p)) for p in _TOKEN_PATTERNS
)


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass
class IngestStats:
    """What one refresh() pass did — surfaced in logs and engine warnings."""

    files_seen: int = 0
    files_skipped: int = 0
    files_parsed: int = 0
    files_pruned: int = 0
    sessions_upserted: int = 0
    findings_added: int = 0
    malformed_lines: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Source discovery
# ---------------------------------------------------------------------------


def _source_roots() -> tuple[tuple[str, Path], ...]:
    """Return (source label, root dir) pairs to scan for session JSONL files.

    A function rather than a constant so tests point it at fixtures
    (monkeypatch) and future sources (Codex CLI, …) slot in as new pairs.

    One source today. The pair shape is not speculative generality — the store,
    the ``by_source`` breakdown and the ``--source`` filter are all keyed on the
    label, so adding a tool is one entry here rather than a schema change.
    """
    return (("claude_code", Path.home() / ".claude" / "projects"),)


def _iter_session_files(root: Path) -> Iterable[Path]:
    """Yield candidate session transcripts under one root, stable order."""
    if not root.is_dir():
        return
    yield from sorted(root.rglob("*.jsonl"))


# ---------------------------------------------------------------------------
# Per-file parse
# ---------------------------------------------------------------------------


@dataclass
class _SessionRollup:
    """Mutable accumulator for one transcript's aggregates."""

    session_id: str = ""
    project_path: str = ""
    git_branch: str = ""
    cli_version: str = ""
    started_at: str = ""
    ended_at: str = ""
    turns: int = 0
    model_usage: dict[str, dict[str, int]] = field(default_factory=dict)
    tool_counts: dict[str, int] = field(default_factory=dict)


_USAGE_KEYS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read", "calls")


def _add_usage(rollup: _SessionRollup, model: str, usage: dict) -> None:
    """Fold one deduped assistant message's usage into the per-model totals."""
    bucket = rollup.model_usage.setdefault(model, dict.fromkeys(_USAGE_KEYS, 0))
    cache_detail = usage.get("cache_creation") or {}
    write_1h = int(cache_detail.get("ephemeral_1h_input_tokens") or 0)
    write_5m = int(cache_detail.get("ephemeral_5m_input_tokens") or 0)
    if not write_1h and not write_5m:
        # Older CLI versions report only the aggregate; treat it as 5m writes.
        write_5m = int(usage.get("cache_creation_input_tokens") or 0)
    bucket["input"] += int(usage.get("input_tokens") or 0)
    bucket["output"] += int(usage.get("output_tokens") or 0)
    bucket["cache_write_5m"] += write_5m
    bucket["cache_write_1h"] += write_1h
    bucket["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
    bucket["calls"] += 1


def _scan_security(
    line: str,
    line_no: int,
    record: dict | None,
    *,
    on_finding: Callable[[str, str, str, int, str], None],
    session_id: str,
) -> None:
    """Emit security findings for one raw line. Pattern + location only."""
    for label, regex in _SECRET_PATTERNS:
        if regex.search(line):
            on_finding("secret", "critical", label, line_no, session_id)
    if record is None:
        return
    message = record.get("message") or {}
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        command = (block.get("input") or {}).get("command")
        if not isinstance(command, str):
            continue
        for label, severity, regex in _RISKY_BASH_PATTERNS:
            if regex.search(command):
                on_finding("risky_tool", severity, label, line_no, session_id)


def _parse_file(
    path: Path,
    *,
    stats: IngestStats,
    on_finding: Callable[[str, str, str, int, str], None],
) -> _SessionRollup:
    """Stream one transcript into a rollup; dedupe usage by requestId."""
    rollup = _SessionRollup(session_id=path.stem)
    counted_requests: set[str] = set()
    counted_tool_blocks: set[str] = set()
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                stats.malformed_lines += 1
                _scan_security(line, line_no, None, on_finding=on_finding, session_id=rollup.session_id)
                continue
            if not isinstance(record, dict):
                stats.malformed_lines += 1
                continue
            if sid := record.get("sessionId"):
                rollup.session_id = str(sid)
            _scan_security(line, line_no, record, on_finding=on_finding, session_id=rollup.session_id)
            if cwd := record.get("cwd"):
                rollup.project_path = str(cwd)
            if branch := record.get("gitBranch"):
                rollup.git_branch = str(branch)
            if version := record.get("version"):
                rollup.cli_version = str(version)
            if timestamp := record.get("timestamp"):
                rollup.started_at = rollup.started_at or str(timestamp)
                rollup.ended_at = str(timestamp)
            kind = record.get("type")
            # isinstance rather than `or {}`: these fields are another tool's
            # format, and a record carrying `"origin": "human"` (a string, not
            # an object) would raise AttributeError on .get — which refresh()
            # turns into "failed to ingest", dropping the WHOLE file's usage
            # over one odd line.
            message = record.get("message")
            message = message if isinstance(message, dict) else {}
            if kind == "user":
                origin_obj = record.get("origin")
                origin = origin_obj.get("kind") if isinstance(origin_obj, dict) else None
                if origin == "human" or (origin is None and isinstance(message.get("content"), str)):
                    rollup.turns += 1
            elif kind == "assistant":
                # One API response spans several lines sharing a requestId,
                # each repeating identical usage — count it exactly once.
                request_id = str(record.get("requestId") or record.get("uuid") or line_no)
                usage = message.get("usage")
                if isinstance(usage, dict) and request_id not in counted_requests:
                    counted_requests.add(request_id)
                    _add_usage(rollup, str(message.get("model") or "unknown"), usage)
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        block_id = str(block.get("id") or f"{request_id}:{line_no}")
                        if block_id in counted_tool_blocks:
                            continue
                        counted_tool_blocks.add(block_id)
                        name = str(block.get("name") or "unknown")
                        rollup.tool_counts[name] = rollup.tool_counts.get(name, 0) + 1
    return rollup


def _first_line_sha(path: Path) -> str:
    """Hash the first line so a replaced/rotated same-size file is detectable."""
    try:
        with path.open("rb") as handle:
            return hashlib.sha256(handle.readline()).hexdigest()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def refresh(
    store: AgentWatchStore,
    *,
    roots: tuple[tuple[str, Path], ...] | None = None,
    on_progress: Callable[[object], None] | None = None,
) -> IngestStats:
    """Ingest new/changed session transcripts into the store. Never raises.

    ``on_progress`` receives ``analysis_component`` lifecycle dicts (see
    yeaboi.analysis.progress) carrying an aggregate files-scanned meter —
    never per-file names, which on a cold cache is thousands of lines of
    noise no reader can act on. Cold runs still emit one meter event per
    *parsed* file (that is the live meter during the slow path; the consumer
    folds them per frame); warm runs, where nearly everything is cursor-
    skipped, are throttled to integer-percent changes.

    The skip path checks (size, mtime) first — transcripts are append-only, and
    the cursor keys on both fields because coarse filesystem mtimes (FAT's 2s)
    can hide a same-size touch but not a growth. When those match it then
    compares the first line's hash, which is the only thing that catches a file
    *replaced* at the same size with a preserved mtime (``cp -p`` of a backup,
    a restore, a rewrite): cheap, since it reads one line and only for files we
    were about to skip anyway. Anything else is fully reparsed and its rollup
    and findings replaced.
    """
    stats = IngestStats()
    scan_failed = False
    # Materialise across ALL roots before scanning so the progress meter has a
    # global denominator — a per-root total would reset the bar mid-scan when
    # a second source kicks in.
    pending: list[tuple[str, Path]] = []
    for source, root in roots if roots is not None else _source_roots():
        try:
            pending.extend((source, path) for path in _iter_session_files(root))
        except OSError as exc:
            stats.warnings.append(f"cannot scan {root}: {exc}")
            scan_failed = True

    total = len(pending)
    last_pct = -1

    def _emit_scan(current: int) -> None:
        # Throttled at the call sites: first file, a parsed (non-cached) file,
        # an integer-percent change, or the last file. Warm runs emit at most
        # ~100 events instead of one per transcript.
        nonlocal last_pct
        last_pct = (current * 100) // max(1, total)
        send_component_progress(
            on_progress,
            component_id="scan",
            label="Scan agent sessions",
            status="running",
            current=current,
            total=total,
            unit="files",
            secondary_count=stats.files_parsed,
            secondary_unit="parsed",
        )

    if on_progress is not None and total:
        _emit_scan(0)
    for index, (source, path) in enumerate(pending):
        stats.files_seen += 1
        handled = index + 1
        try:
            file_stat = path.stat()
        except OSError:
            continue
        ingested = False
        cursor = store.get_cursor(str(path))
        skip = False
        if cursor and cursor["size"] == file_stat.st_size and cursor["mtime"] == file_stat.st_mtime:
            # Same size AND same mtime — the only remaining way this file
            # differs is a same-size replacement, which the head hash
            # catches. An empty stored hash predates the check; treat it as
            # a match rather than reparsing every file once.
            stored_sha = cursor["first_line_sha"]
            skip = not stored_sha or stored_sha == _first_line_sha(path)
        if skip:
            stats.files_skipped += 1
        else:
            ingested = True
            try:
                _ingest_one(store, source, path, stats)
            except Exception as exc:  # one bad file must not sink the sweep
                # The exception TEXT never reaches the warning: an exception
                # raised while parsing an untrusted transcript can carry a
                # fragment of that transcript in its message (int('<value>')),
                # and warnings are persisted to SQLite, written to the export
                # and rendered on screen. The class name says as much for
                # triage; the detail goes to the log.
                logger.warning("agentwatch ingest failed for %s: %s", path, exc)
                stats.warnings.append(f"failed to ingest {path.name} ({type(exc).__name__} — see logs)")
            else:
                store.set_cursor(
                    str(path),
                    source=source,
                    size=file_stat.st_size,
                    mtime=file_stat.st_mtime,
                    first_line_sha=_first_line_sha(path),
                )
        if on_progress is not None and (ingested or handled == total or (handled * 100) // total != last_pct):
            _emit_scan(handled)
    # Guarantee the meter closes at N/N even when the last file's stat() failed
    # and its per-file emit was skipped — the bar must never freeze short.
    if on_progress is not None and total and last_pct != 100:
        _emit_scan(total)

    # Drop state for transcripts that are gone from disk. Deleting the
    # transcript is how a user remediates a leaked secret, and without this the
    # finding (and the session's tokens) would outlive the file for ever.
    # Skipped when a root failed to scan: an unreadable or unmounted root makes
    # every file under it look deleted, and pruning on that reading would
    # discard the whole cache over a transient mount.
    if not scan_failed:
        for known in store.known_source_paths():
            if Path(known).exists():
                continue
            store.forget_source_path(known)
            stats.files_pruned += 1

    logger.info(
        "agentwatch refresh: %d seen, %d parsed, %d skipped, %d pruned, %d sessions, %d findings, %d malformed",
        stats.files_seen,
        stats.files_parsed,
        stats.files_skipped,
        stats.files_pruned,
        stats.sessions_upserted,
        stats.findings_added,
        stats.malformed_lines,
    )
    return stats


def _ingest_one(store: AgentWatchStore, source: str, path: Path, stats: IngestStats) -> None:
    """Reparse one transcript and replace its rollup + findings."""
    findings: list[tuple[str, str, str, int, str]] = []

    def on_finding(category: str, severity: str, pattern: str, line_no: int, session_id: str) -> None:
        findings.append((category, severity, pattern, line_no, session_id))

    rollup = _parse_file(path, stats=stats, on_finding=on_finding)
    stats.files_parsed += 1
    if not rollup.model_usage and not rollup.turns and not rollup.tool_counts:
        return  # not a session transcript (some other tool's JSONL)
    if not rollup.ended_at:
        rollup.ended_at = rollup.started_at or datetime.now(UTC).isoformat()
    store.upsert_session(
        rollup.session_id,
        source=source,
        source_path=str(path),
        project_path=rollup.project_path,
        git_branch=rollup.git_branch,
        cli_version=rollup.cli_version,
        started_at=rollup.started_at,
        ended_at=rollup.ended_at,
        turns=rollup.turns,
        model_usage=rollup.model_usage,
        tool_counts=rollup.tool_counts,
    )
    stats.sessions_upserted += 1
    store.delete_findings_for_path(str(path))
    for category, severity, pattern, line_no, session_id in findings:
        store.add_finding(
            category=category,
            severity=severity,
            pattern=pattern,
            source_path=str(path),
            line_no=line_no,
            session_id=session_id,
        )
        stats.findings_added += 1
