"""Local agent-session ingestion for agentwatch.

Scans Claude Code session transcripts — append-only JSONL files under
``~/.claude/projects/**`` — and rolls each one up into an ``agent_sessions``
row (per-model token totals with the 5m/1h cache-write split, tool-use counts,
turns, project path, branch, timestamps) plus ``agent_session_days`` rows, the
same usage split per UTC day so a window and the daily trend see tokens on the
day they were spent.

Three invariants shape the design:

1. **Privacy** — nothing from a transcript's *content* is persisted. The one
   pass over raw text happens here, in the stream, and emits only
   ``(pattern label, severity, file, line number)`` security findings.
2. **Correct token math** — Claude Code writes one assistant API message
   across several JSONL lines sharing a ``requestId``, and the *first* of
   them can be a placeholder (``output_tokens: 1``) that the last line
   finalises. Usage is therefore kept per request with the last line winning
   (output as the max seen), tool_use blocks are counted once per block id,
   and a ``(message id, request id)`` already counted from another file is
   skipped — a copied or restored transcript must not double a bill.
3. **Never raise** — malformed lines are counted and skipped, and a failing
   file becomes a warning.

The cursor keys on (size, mtime, first-line hash). An unchanged file is not
opened; a file that only *grew* is parsed from the byte offset the last parse
stopped at and merged; anything else is fully reparsed and its rows replaced.
"""

from __future__ import annotations

import hashlib
import json
import logging
import multiprocessing
import os
import re
import sqlite3
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from yeaboi.agentwatch import security_checks
from yeaboi.agentwatch.store import AgentWatchStore
from yeaboi.analysis.progress import send_component_progress
from yeaboi.pricing import LONG_CONTEXT_THRESHOLD
from yeaboi.redaction import _TOKEN_PATTERNS

logger = logging.getLogger(__name__)

# Parsed files per write transaction on a cold sweep. Bounds how long the
# shared sessions.db write lock is held (SessionStore saves must not starve)
# and how much work an interrupted sweep re-does, while still collapsing
# ~20 autocommit fsyncs per file into one commit per batch.
_INGEST_BATCH_SIZE = 64

# Files-to-parse count at which refresh() switches from inline parsing to a
# process pool. Below this, spawn cost (~100-300ms/worker on macOS spawn)
# outweighs the win — the warm path (0-2 changed files) must never pay it.
_PARALLEL_THRESHOLD = 16

# Worker-count ceiling: parse throughput saturates well before high core
# counts (SQLite writes are serialized in the parent anyway), and unbounded
# pools hurt on shared CI machines.
_MAX_PARSE_WORKERS = 8

# Lines Claude Code writes that carry usage but bill nothing: a synthetic
# assistant turn (a local stand-in, no API call) and an API error echo.
_NON_BILLABLE_MODELS = frozenset({"<synthetic>"})

# ---------------------------------------------------------------------------
# Security signal patterns (labels only — matched text is never stored)
# ---------------------------------------------------------------------------

# Risky shell shapes scanned over tool_use inputs that carry a "command".
# Each entry: (label, compiled regex); severity comes from security_checks.
_RISKY_BASH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("curl-pipe-shell", re.compile(r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b")),
    ("base64-decode-pipe-shell", re.compile(r"base64\s+(?:-d|--decode)[^|;&]*\|\s*(?:ba|z|da)?sh\b")),
    ("rm-rf-root", re.compile(r"\brm\s+-[a-z]*rf?[a-z]*\s+/(?:\s|$)")),
    ("permission-bypass-flag", re.compile(r"--dangerously-skip-permissions\b")),
    ("sudo", re.compile(r"(?:^|[;&|]\s*)sudo\s")),
)


_BEARER_GUARD_RE = re.compile(r"(?i:bearer|basic)")


def _guard_bearer(line: str) -> bool:
    """Any match of the bearer/basic pattern must contain one of the two words.

    A regex, not a ``line.lower()`` substring check: ``(?i:…)`` case-folds
    U+017F (ſ) to "s" where ``str.lower()`` does not, and a guard that is not
    exactly implied by the pattern it gates silently drops a finding.
    """
    return _BEARER_GUARD_RE.search(line) is not None


def _guard_url_credentials(line: str) -> bool:
    """Any match of the ``://user:pass@`` pattern needs both mandatory chars."""
    return "://" in line and "@" in line


# Cheap substring pre-checks for the two patterns that dominate scan cost
# (~70% of the 718MB-corpus regex time, measured): neither has a literal
# prefix, so `re` probes every position of every line. Keyed on the EXACT
# pattern text from redaction._TOKEN_PATTERNS — a pattern that changes there
# simply loses its guard and runs unguarded (slow but never skipped), so a
# stale key can only cost speed, not findings. Each guard must be logically
# implied by its regex; test_agentwatch_collector.py proves gated == ungated
# over a differential corpus. Do NOT collapse the patterns into one
# alternation instead — these two defeat re's literal-prefix optimizer and
# make the combined scan ~10x slower (measured).
_PATTERN_GUARDS: dict[str, Callable[[str], bool]] = {
    r"(?i:bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}": _guard_bearer,
    r"(?<=://)[^/\s:@]+:[^/\s@]{4,}(?=@)": _guard_url_credentials,
}

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str], Callable[[str], bool] | None], ...] = tuple(
    (security_checks.secret_label(p), re.compile(p), _PATTERN_GUARDS.get(p)) for p in _TOKEN_PATTERNS
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
    files_resumed: int = 0
    files_pruned: int = 0
    sessions_upserted: int = 0
    findings_added: int = 0
    malformed_lines: int = 0
    duplicates: int = 0  # requests already counted from another transcript
    no_request_id: int = 0  # assistant lines with neither requestId nor message id
    priced_from_log: int = 0  # requests that carried their own costUSD
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
    """Mutable accumulator for one transcript (or one appended chunk of it)."""

    session_id: str = ""
    project_path: str = ""
    git_branch: str = ""
    cli_version: str = ""
    started_at: str = ""
    ended_at: str = ""
    turns: int = 0
    # Per-request usage in file order: {key: {"global", "model", "day", "usage"}}.
    # Folded into day/model totals by the parent after global dedup.
    requests: dict[str, dict] = field(default_factory=dict)
    tool_counts: dict[str, int] = field(default_factory=dict)
    no_request_id: int = 0
    priced_from_log: int = 0
    byte_offset: int = 0  # end of the last complete line consumed
    line_count: int = 0
    # Filled by the parent from ``requests``:
    model_usage: dict[str, dict[str, int]] = field(default_factory=dict)
    day_usage: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)


_USAGE_KEYS = (
    "input",
    "output",
    "cache_write_5m",
    "cache_write_1h",
    "cache_read",
    "calls",
    "web_search_calls",
    "web_fetch_calls",
    "premium_input",
    "premium_output",
    "recorded_cost_usd",
)


def _usage_bucket(usage: dict, recorded_cost: float) -> dict:
    """One API message's usage in the store's vocabulary."""
    cache_detail = usage.get("cache_creation") or {}
    write_1h = int(cache_detail.get("ephemeral_1h_input_tokens") or 0)
    write_5m = int(cache_detail.get("ephemeral_5m_input_tokens") or 0)
    if not write_1h and not write_5m:
        # Older CLI versions report only the aggregate; treat it as 5m writes.
        write_5m = int(usage.get("cache_creation_input_tokens") or 0)
    server = usage.get("server_tool_use") or {}
    server = server if isinstance(server, dict) else {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    # The prompt this request carried; past the threshold Anthropic bills the
    # request's own input and output at the long-context premium.
    prompt_tokens = input_tokens + cache_read + write_5m + write_1h
    premium = prompt_tokens > LONG_CONTEXT_THRESHOLD
    return {
        "input": input_tokens,
        "output": output_tokens,
        "cache_write_5m": write_5m,
        "cache_write_1h": write_1h,
        "cache_read": cache_read,
        "calls": 1,
        "web_search_calls": int(server.get("web_search_requests") or 0),
        "web_fetch_calls": int(server.get("web_fetch_requests") or 0),
        "premium_input": input_tokens if premium else 0,
        "premium_output": output_tokens if premium else 0,
        "recorded_cost_usd": float(recorded_cost or 0.0),
    }


def _merge_request(prior: dict | None, fresh: dict) -> dict:
    """Last line wins, except output tokens, where the largest count is the final one."""
    if prior is None:
        return dict(fresh)
    merged = dict(fresh)
    merged["output"] = max(int(prior.get("output", 0)), int(fresh.get("output", 0)))
    return merged


def _add_bucket(target: dict[str, int], usage: dict, sign: int = 1) -> None:
    for key in _USAGE_KEYS:
        target[key] = target.get(key, 0) + sign * usage.get(key, 0)


def fold_requests(rollup: _SessionRollup, *, skip_keys: set[str] | None = None) -> None:
    """Fill ``model_usage``/``day_usage`` from ``requests``, skipping duplicate keys."""
    skip_keys = skip_keys or set()
    rollup.model_usage = {}
    rollup.day_usage = {}
    for key, entry in rollup.requests.items():
        if entry["global"] and key in skip_keys:
            continue
        model, day, usage = entry["model"], entry["day"], entry["usage"]
        _add_bucket(rollup.model_usage.setdefault(model, {}), usage)
        _add_bucket(rollup.day_usage.setdefault(day, {}).setdefault(model, {}), usage)


# Contexts a transcript match can sit in. The verdict engine reads these; a
# heredoc body is text an agent wrote into a file, a command is text it ran.
CONTEXT_COMMAND = "command"
CONTEXT_HEREDOC = "heredoc"
CONTEXT_INLINE_SCRIPT = "inline-script"
CONTEXT_WRITE_INPUT = "write-input"
CONTEXT_TOOL_RESULT = "tool-result"
CONTEXT_PROSE = "prose"
CONTEXT_USER_PROMPT = "user-prompt"
CONTEXT_TOOL_INPUT = "tool-input"  # the argument of a tool other than Bash (a Grep pattern, a fetch URL)

_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_HEREDOC_OPEN = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][\w-]*)\1")
_INLINE_SCRIPT = re.compile(r"(?:python3?|node|ruby|perl|bash|sh)\s+-[ce]\s+(['\"])(.*?)\1", re.S)
_SNIPPET_RADIUS = 56
_SNIPPET_LIMIT = 120


def split_heredocs(command: str) -> tuple[str, list[str]]:
    """``(the command with its heredoc bodies removed, the bodies)``.

    A body starts on the line after ``<<TAG`` and ends at the line that is
    exactly ``TAG``. Text inside is what the agent wrote into a file or fed to
    an interpreter, not what the shell ran — the distinction every verdict
    over a risky-looking command rests on.
    """
    lines = command.split("\n")
    kept: list[str] = []
    bodies: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        for match in _HEREDOC_OPEN.finditer(line):
            tag = match.group(2)
            body: list[str] = []
            while index < len(lines) and lines[index].strip() != tag:
                body.append(lines[index])
                index += 1
            index += 1  # the closing tag line
            bodies.append("\n".join(body))
    return "\n".join(kept), bodies


def _mask_snippet(text: str, start: int, end: int, label: str) -> str:
    """≤120 redacted characters around ``text[start:end]`` with that span masked."""
    from yeaboi.redaction import redact

    before = text[max(0, start - _SNIPPET_RADIUS) : start]
    after = text[end : end + _SNIPPET_RADIUS]
    joined = " ".join(f"{before}[REDACTED {label}]{after}".split())
    return redact(joined)[:_SNIPPET_LIMIT]


_HEREDOC_TARGET = re.compile(r"(?:>>?|\btee\s+(?:-a\s+)?)\s*[\"']?([^\s\"'<>|;&]+)")


def heredoc_bodies(command: str) -> list[tuple[str, str]]:
    """``(body, target file)`` per heredoc, in order; the target is "" when the body feeds a program.

    Walks the same way :func:`split_heredocs` does, so an opener inside a body
    is body text, not a second heredoc.
    """
    lines = command.split("\n")
    out: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        for match in _HEREDOC_OPEN.finditer(line):
            tag = match.group(2)
            body: list[str] = []
            while index < len(lines) and lines[index].strip() != tag:
                body.append(lines[index])
                index += 1
            index += 1
            redirect = _HEREDOC_TARGET.search(line)
            out.append(("\n".join(body), redirect.group(1) if redirect else ""))
    return out


def _command_context(command: str, regex: re.Pattern[str], label: str) -> tuple[str, str, str] | None:
    """``(context, snippet, target)`` for the first place ``regex`` matches in a Bash command."""
    stripped, _bodies = split_heredocs(command)
    match = regex.search(stripped)
    if match:
        inline = _INLINE_SCRIPT.search(stripped)
        if inline and inline.start(2) <= match.start() < inline.end(2):
            return CONTEXT_INLINE_SCRIPT, _mask_snippet(stripped, match.start(), match.end(), label), ""
        return CONTEXT_COMMAND, _mask_snippet(stripped, match.start(), match.end(), label), ""
    for body, target in heredoc_bodies(command):
        match = regex.search(body)
        if match:
            return CONTEXT_HEREDOC, _mask_snippet(body, match.start(), match.end(), label), target
    return None


def _result_target(record: dict) -> str:
    """The file a tool_result came from, when the transcript says so."""
    result = record.get("toolUseResult")
    if isinstance(result, dict):
        if isinstance(result.get("filePath"), str):
            return result["filePath"]
        file_info = result.get("file")
        if isinstance(file_info, dict) and isinstance(file_info.get("filePath"), str):
            return file_info["filePath"]
    return ""


def _block_strings(block: dict) -> list[tuple[str, str, str]]:
    """``(context, target, text)`` candidates a content block can carry a match in."""
    kind = block.get("type")
    if kind == "text":
        return [(CONTEXT_PROSE, "", str(block.get("text") or ""))]
    if kind == "tool_result":
        content = block.get("content")
        if isinstance(content, list):
            text = "\n".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
        else:
            text = str(content or "")
        return [(CONTEXT_TOOL_RESULT, "", text)]
    if kind == "tool_use":
        name = str(block.get("name") or "")
        payload = block.get("input") if isinstance(block.get("input"), dict) else {}
        if name in _WRITE_TOOLS:
            target = str(payload.get("file_path") or payload.get("notebook_path") or "")
            text = "\n".join(str(v) for k, v in payload.items() if k not in ("file_path", "notebook_path"))
            return [(CONTEXT_WRITE_INPUT, target, text)]
        if isinstance(payload.get("command"), str):
            return [(CONTEXT_COMMAND, "", payload["command"])]
        return [(CONTEXT_TOOL_INPUT, "", json.dumps(payload, ensure_ascii=False))]
    return []


def _locate_secret(record: dict | None, regex: re.Pattern[str], label: str) -> tuple[str, str, str]:
    """``(context, target, snippet)`` for a secret regex that hit this line."""
    if record is None:
        return CONTEXT_PROSE, "", ""
    message = record.get("message")
    message = message if isinstance(message, dict) else {}
    kind = record.get("type")
    content = message.get("content")
    if isinstance(content, str):
        context = CONTEXT_USER_PROMPT if kind == "user" else CONTEXT_PROSE
        match = regex.search(content)
        return context, "", _mask_snippet(content, match.start(), match.end(), label) if match else ""
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            for context, target, text in _block_strings(block):
                if block.get("type") == "tool_use" and context == CONTEXT_COMMAND:
                    located = _command_context(text, regex, label)
                    if located:
                        return located[0], located[2], located[1]
                    continue
                match = regex.search(text)
                if match:
                    if context == CONTEXT_TOOL_RESULT:
                        target = _result_target(record)
                    if context == CONTEXT_PROSE and kind == "user":
                        context = CONTEXT_USER_PROMPT
                    return context, target, _mask_snippet(text, match.start(), match.end(), label)
    # Claude Code keeps a tool's full output on the record itself, beside the
    # (often truncated) content block — a Read's file text lives here.
    result = record.get("toolUseResult")
    if result is not None:
        text = json.dumps(result, ensure_ascii=False)
        match = regex.search(text)
        if match:
            return CONTEXT_TOOL_RESULT, _result_target(record), _mask_snippet(text, match.start(), match.end(), label)
    return CONTEXT_PROSE, "", ""


def _scan_security(
    line: str,
    line_no: int,
    record: dict | None,
    *,
    on_finding: Callable[[dict], None],
    session_id: str,
) -> None:
    """Emit security findings for one raw line.

    Each finding is a plain dict: pattern, severity, line, session, where the
    match sat (context + target) and a redacted snippet with the matched span
    masked. The span itself is classified for a severity and dropped.
    """
    timestamp = str(record.get("timestamp") or "") if record else ""
    seen: set[str] = set()
    for label, regex, guard in _SECRET_PATTERNS:
        if guard is not None and not guard(line):
            continue
        match = regex.search(line)
        if not match or label in seen:
            continue
        seen.add(label)
        context, target, snippet = _locate_secret(record, regex, label)
        on_finding(
            {
                "category": "secret",
                "severity": security_checks.classify_secret(label, match.group(0)),
                "pattern": label,
                "line_no": line_no,
                "session_id": session_id,
                "context": context,
                "target": target,
                "at": timestamp,
                "tool": "",
                "snippet": snippet,
            }
        )
    if record is None:
        return
    message = record.get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        command = (block.get("input") or {}).get("command")
        if not isinstance(command, str):
            continue
        for label, regex in _RISKY_BASH_PATTERNS:
            located = _command_context(command, regex, label)
            if located is None:
                continue
            severity = security_checks.RISKY_TOOL_SEVERITY.get(label, "medium")
            on_finding(
                {
                    "category": "risky_tool",
                    "severity": severity,
                    "pattern": label,
                    "line_no": line_no,
                    "session_id": session_id,
                    "context": located[0],
                    "target": located[2],
                    "at": timestamp,
                    "tool": str(block.get("name") or "Bash"),
                    "snippet": located[1],
                }
            )


def _day_of(timestamp: str, fallback: str) -> str:
    """The UTC calendar day of an ISO timestamp, or ``fallback`` when it has none."""
    return timestamp[:10] if len(timestamp) >= 10 and timestamp[4] == "-" and timestamp[7] == "-" else fallback


def _parse_file(
    path: Path,
    *,
    stats: IngestStats,
    on_finding: Callable[[dict], None],
    scan_security: bool = True,
    start_offset: int = 0,
    start_line: int = 0,
) -> _SessionRollup:
    """Stream one transcript (from ``start_offset``) into a rollup.

    Lines are read as bytes so the byte offset of the last *complete* line is
    known; a trailing partial line (still being written) is left for the next
    run rather than counted as malformed. Usage is kept per request key with
    the last line winning; tool_use blocks are deduped by block id.
    """
    rollup = _SessionRollup(session_id=path.stem, byte_offset=start_offset, line_count=start_line)
    counted_tool_blocks: set[str] = set()
    last_day = ""
    line_no = start_line
    with path.open("rb") as handle:
        if start_offset:
            handle.seek(start_offset)
        for raw in handle:
            if not raw.endswith(b"\n"):
                break  # a partial trailing line: consumed next run
            line_no += 1
            rollup.byte_offset += len(raw)
            rollup.line_count = line_no
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                stats.malformed_lines += 1
                if scan_security:
                    _scan_security(line, line_no, None, on_finding=on_finding, session_id=rollup.session_id)
                continue
            if not isinstance(record, dict):
                stats.malformed_lines += 1
                continue
            if sid := record.get("sessionId"):
                rollup.session_id = str(sid)
            if scan_security:
                _scan_security(line, line_no, record, on_finding=on_finding, session_id=rollup.session_id)
            if cwd := record.get("cwd"):
                rollup.project_path = str(cwd)
            if branch := record.get("gitBranch"):
                rollup.git_branch = str(branch)
            if version := record.get("version"):
                rollup.cli_version = str(version)
            timestamp = str(record.get("timestamp") or "")
            if timestamp:
                rollup.started_at = rollup.started_at or timestamp
                rollup.ended_at = timestamp
                last_day = _day_of(timestamp, last_day)
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
                request_id = str(record.get("requestId") or "")
                message_id = str(message.get("id") or "")
                usage = message.get("usage")
                model = str(message.get("model") or "unknown")
                billable = model not in _NON_BILLABLE_MODELS and not record.get("isApiErrorMessage")
                if request_id and message_id:
                    key, is_global = f"{message_id}:{request_id}", True
                else:
                    key, is_global = request_id or message_id or str(record.get("uuid") or line_no), False
                    if not request_id and not message_id:
                        rollup.no_request_id += 1
                if isinstance(usage, dict) and billable:
                    recorded = record.get("costUSD")
                    recorded = float(recorded) if isinstance(recorded, (int, float)) else 0.0
                    if recorded:
                        rollup.priced_from_log += 1
                    prior = rollup.requests.get(key)
                    rollup.requests[key] = {
                        "global": is_global,
                        "model": model,
                        "day": _day_of(timestamp, last_day),
                        "usage": _merge_request(prior["usage"] if prior else None, _usage_bucket(usage, recorded)),
                    }
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        block_id = str(block.get("id") or f"{key}:{line_no}")
                        if block_id in counted_tool_blocks:
                            continue
                        counted_tool_blocks.add(block_id)
                        name = str(block.get("name") or "unknown")
                        rollup.tool_counts[name] = rollup.tool_counts.get(name, 0) + 1
    fold_requests(rollup)
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
    scan_security: bool = False,
) -> IngestStats:
    """Ingest new/changed session transcripts into the store. Never raises.

    ``scan_security`` runs the secret/risky-command regexes over the lines it
    parses; it is what the security pass asks for and roughly three quarters
    of a cold parse's time, so the cost pages leave it off. A file parsed
    without the scan keeps whatever findings it already had.

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
    were about to skip anyway. A file that grew with its first line intact is
    parsed from the stored byte offset and merged; anything else is fully
    reparsed and its rollup and findings replaced.
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

    # ── Pass 1: disposition every file via the cursor (cheap stat, and a
    # head hash only when size and mtime already match or the file grew).
    # Changed files queue for parsing WITH their pre-parse stat, so the cursor
    # later records the size/mtime the parse saw — an append landing mid-parse
    # makes the next run reparse rather than being silently absorbed.
    # Each queue entry: (source, path, stat, first_line_sha, resume cursor|None).
    to_parse: list[tuple[str, Path, os.stat_result, str, dict | None]] = []
    handled = 0
    for source, path in pending:
        stats.files_seen += 1
        try:
            file_stat = path.stat()
        except OSError:
            handled += 1
            continue
        cursor = store.get_cursor(str(path))
        sha = ""
        resume: dict | None = None
        skip = False
        # A security pass cannot trust a cursor written by a cost-only parse:
        # those lines were never scanned. Full reparse, findings replaced.
        needs_scan = scan_security and cursor is not None and not cursor.get("security_scanned")
        if needs_scan:
            pass
        elif cursor and cursor["size"] == file_stat.st_size and cursor["mtime"] == file_stat.st_mtime:
            # Same size AND same mtime — the only remaining way this file
            # differs is a same-size replacement, which the head hash
            # catches. An empty stored hash predates the check; treat it as
            # a match rather than reparsing every file once.
            stored_sha = cursor["first_line_sha"]
            sha = _first_line_sha(path) if stored_sha else stored_sha
            skip = not stored_sha or stored_sha == sha
        elif cursor and cursor.get("byte_offset") and file_stat.st_size > cursor["size"]:
            sha = _first_line_sha(path)
            if cursor["first_line_sha"] and cursor["first_line_sha"] == sha:
                resume = cursor
        if not skip:
            to_parse.append((source, path, file_stat, sha, resume))
            continue
        stats.files_skipped += 1
        handled += 1
        if on_progress is not None and (handled == total or (handled * 100) // total != last_pct):
            _emit_scan(handled)

    # ── Pass 2: parse the queue — in a process pool when it is deep enough to
    # repay spawn cost, inline otherwise (the warm path, 0–2 files, never pays
    # pool spin-up). Processes, not threads: the cold cost is json.loads +
    # regex matching and neither releases the GIL. Each file is fully
    # independent, and the parent keeps the ONLY SQLite connection — workers
    # return plain data and every write happens here.
    #
    # Writes are batched into explicit transactions: the store's connection is
    # autocommit, so per-statement commits made a cold sweep pay ~1,500
    # fsyncs. Batches are BOUNDED (not one sweep-long transaction) because
    # sessions.db is shared with SessionStore — a minutes-long write lock
    # would block TUI session saves. ExitStack holds the open batch across
    # iterations; on an unwinding exception (Ctrl-C) the finally COMMITS the
    # completed files rather than rolling back: a file is cursored in the same
    # batch as its rollup, so committed work is consistent and the interrupted
    # file simply reparses next run.
    batch = ExitStack()
    in_batch = 0

    def _apply(source: str, path: Path, file_stat: os.stat_result, sha: str, resume: dict | None, result) -> None:
        nonlocal in_batch, handled
        handled += 1
        if in_batch == 0:
            batch.enter_context(store.transaction())
        in_batch += 1
        if result[0] == "error":
            # Only the exception CLASS NAME reaches the warning: an exception
            # raised while parsing an untrusted transcript can carry a
            # fragment of that transcript in its message (int('<value>')),
            # and warnings are persisted to SQLite, written to the export
            # and rendered on screen. The detail goes to the local log.
            _tag, exc_type, detail = result
            logger.warning("agentwatch ingest failed for %s: %s", path, detail)
            stats.warnings.append(f"failed to ingest {path.name} ({exc_type} — see logs)")
            # No cursor either — the file reparses next run, as before.
        else:
            _tag, rollup, findings, malformed = result
            stats.malformed_lines += malformed
            _store_parsed(store, source, path, rollup, findings, stats, scan_security=scan_security, resume=resume)
            tail_key = next(reversed(rollup.requests), "") if rollup.requests else ""
            tail = rollup.requests.get(tail_key) if tail_key else None
            if tail and "cumulative" in tail:
                tail = {**tail, "usage": tail["cumulative"]}
                tail.pop("cumulative", None)
            store.set_cursor(
                str(path),
                source=source,
                size=file_stat.st_size,
                mtime=file_stat.st_mtime,
                first_line_sha=sha or _first_line_sha(path),
                byte_offset=rollup.byte_offset,
                line_count=rollup.line_count,
                tail_request={"key": tail_key, **tail} if tail else (resume or {}).get("tail_request") or None,
                security_scanned=scan_security and (not resume or bool(resume.get("security_scanned"))),
            )
        if in_batch >= _INGEST_BATCH_SIZE:
            batch.close()  # COMMIT
            in_batch = 0
        if on_progress is not None:
            # Every parsed file emits — this is the live meter on a cold run;
            # the consumer folds events per frame.
            _emit_scan(handled)

    def _job(path: Path, resume: dict | None) -> tuple[str, bool, int, int]:
        offset = int(resume["byte_offset"]) if resume else 0
        line = int(resume["line_count"]) if resume else 0
        return (str(path), scan_security, offset, line)

    try:
        use_pool = len(to_parse) >= _PARALLEL_THRESHOLD
        if use_pool:
            try:
                # spawn, never the platform default: refresh() runs on a TUI
                # worker thread, and fork() from a multi-threaded process can
                # deadlock the child on a lock the parent held at fork time
                # (Linux's default through 3.13; macOS already spawns, which is
                # why it never reproduces here).
                pool = ProcessPoolExecutor(
                    max_workers=min(os.cpu_count() or 4, _MAX_PARSE_WORKERS, len(to_parse)),
                    mp_context=multiprocessing.get_context("spawn"),
                )
            except Exception as exc:  # spawn limits/sandbox — degrade, never fail
                logger.warning(
                    "agentwatch: process pool unavailable (%s: %s), parsing serially", type(exc).__name__, exc
                )
                use_pool = False
        if use_pool:
            with pool:
                futures = [
                    (source, path, file_stat, sha, resume, pool.submit(_parse_worker, *_job(path, resume)))
                    for source, path, file_stat, sha, resume in to_parse
                ]
                # Drain in SUBMISSION order, not completion order: warnings are
                # persisted, exported and rendered, so their order must be
                # deterministic run-to-run. Workers still run in parallel; only
                # the apply step is ordered.
                for source, path, file_stat, sha, resume, future in futures:
                    try:
                        result = future.result()
                    except Exception as exc:  # worker died (BrokenProcessPool, pickling)
                        result = ("error", type(exc).__name__, str(exc))
                    _apply(source, path, file_stat, sha, resume, result)
        else:
            for source, path, file_stat, sha, resume in to_parse:
                _apply(source, path, file_stat, sha, resume, _parse_worker(*_job(path, resume)))
    except sqlite3.OperationalError as exc:
        # sessions.db is shared: another writer (a scheduled ceremony, the TUI
        # saving a session) can hold the write lock past the 5s busy timeout,
        # and BEGIN/COMMIT then raises. refresh() promises to never raise —
        # keep what committed, warn, and let the next run reparse the rest
        # (uncommitted files were never cursored).
        logger.warning("agentwatch ingest aborted mid-scan: %s", exc)
        stats.warnings.append("scan interrupted: sessions.db was busy — partial results, rerun to complete")
    finally:
        try:
            batch.close()
        except sqlite3.OperationalError as exc:
            logger.warning("agentwatch batch commit failed: %s", exc)
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
        "agentwatch refresh: %d seen, %d parsed (%d resumed), %d skipped, %d pruned, %d sessions, "
        "%d findings, %d malformed, %d duplicate request(s)",
        stats.files_seen,
        stats.files_parsed,
        stats.files_resumed,
        stats.files_skipped,
        stats.files_pruned,
        stats.sessions_upserted,
        stats.findings_added,
        stats.malformed_lines,
        stats.duplicates,
    )
    return stats


def _parse_worker(path_str: str, scan_security: bool = True, start_offset: int = 0, start_line: int = 0) -> tuple:
    """Parse one transcript into plain builtins — the process-pool work unit.

    Runs in a worker process (or inline below the pool threshold), so the
    arguments and the return value must both survive pickling:
    ``("ok", rollup, findings, malformed_line_count)`` or
    ``("error", exception_class_name, detail)``. Exceptions become the marker
    INSIDE the worker so the parent keeps its class-name-only warning rule —
    a parse exception can quote transcript text, and while ``detail`` may go
    to the local log, it must never reach ``stats.warnings`` (persisted,
    exported, rendered).
    """
    local = IngestStats()
    findings: list[dict] = []

    try:
        rollup = _parse_file(
            Path(path_str),
            stats=local,
            on_finding=findings.append,
            scan_security=scan_security,
            start_offset=start_offset,
            start_line=start_line,
        )
    except Exception as exc:
        return ("error", type(exc).__name__, str(exc))
    return ("ok", rollup, findings, local.malformed_lines)


def _subtract_tail(rollup: _SessionRollup, tail: dict) -> None:
    """On resume, replace the tail request's earlier contribution with the final one.

    The message that was still streaming at the last offset has already been
    counted from its placeholder line; when its final line arrives in this
    chunk, the difference (never less than zero per field) is what is new.
    """
    key = str(tail.get("key") or "")
    if not key or key not in rollup.requests:
        return
    entry = rollup.requests[key]
    merged = _merge_request(tail.get("usage") or {}, entry["usage"])
    prior_usage = tail.get("usage") or {}
    delta = {k: max(0, merged.get(k, 0) - int(prior_usage.get(k, 0))) for k in _USAGE_KEYS}
    delta["recorded_cost_usd"] = max(
        0.0, float(merged.get("recorded_cost_usd", 0.0)) - float(prior_usage.get("recorded_cost_usd", 0.0))
    )
    entry["usage"] = delta
    entry["cumulative"] = merged  # what the cursor must remember, not the delta
    entry["global"] = False  # already claimed by this file's earlier parse


def _store_parsed(
    store: AgentWatchStore,
    source: str,
    path: Path,
    rollup: _SessionRollup,
    findings: list[dict],
    stats: IngestStats,
    *,
    scan_security: bool = True,
    resume: dict | None = None,
) -> None:
    """Write one parsed transcript's rollup + findings.

    A full parse replaces the file's rows, findings included — the old rows
    pointed at lines that may no longer exist. A resumed chunk is added onto
    them and keeps the earlier findings. Only a scanning parse adds findings;
    the cursor's ``security_scanned`` flag tells the next security pass
    whether it can trust what is stored.
    """
    stats.files_parsed += 1
    source_path = str(path)
    if resume:
        stats.files_resumed += 1
        _subtract_tail(rollup, resume.get("tail_request") or {})
    else:
        store.release_request_keys(source_path)
    stats.no_request_id += rollup.no_request_id
    stats.priced_from_log += rollup.priced_from_log
    global_keys = [k for k, e in rollup.requests.items() if e["global"]]
    duplicates = store.claim_request_keys(source_path, global_keys) if global_keys else set()
    stats.duplicates += len(duplicates)
    fold_requests(rollup, skip_keys=duplicates)

    has_content = bool(rollup.model_usage or rollup.turns or rollup.tool_counts)
    if not has_content and not resume:
        return  # not a session transcript (some other tool's JSONL)
    if not rollup.ended_at and not resume:
        rollup.ended_at = rollup.started_at or datetime.now(timezone.utc).isoformat()

    if resume:
        prior = store.get_session(source_path)
        if prior is None:
            resume = None  # nothing to merge onto: store the chunk as the row
        else:
            model_usage = dict(prior["model_usage"])
            for model, usage in rollup.model_usage.items():
                _add_bucket(model_usage.setdefault(model, {}), usage)
            tool_counts = dict(prior["tool_counts"])
            for name, count in rollup.tool_counts.items():
                tool_counts[name] = tool_counts.get(name, 0) + count
            store.upsert_session(
                rollup.session_id if rollup.session_id != path.stem else prior["session_id"] or rollup.session_id,
                source=source,
                source_path=source_path,
                project_path=rollup.project_path or prior["project_path"],
                git_branch=rollup.git_branch or prior["git_branch"],
                cli_version=rollup.cli_version or prior["cli_version"],
                started_at=prior["started_at"] or rollup.started_at,
                ended_at=rollup.ended_at or prior["ended_at"],
                turns=int(prior["turns"]) + rollup.turns,
                model_usage=model_usage,
                tool_counts=tool_counts,
            )
            store.merge_session_days(source_path, rollup.day_usage)
    if not resume:
        store.upsert_session(
            rollup.session_id,
            source=source,
            source_path=source_path,
            project_path=rollup.project_path,
            git_branch=rollup.git_branch,
            cli_version=rollup.cli_version,
            started_at=rollup.started_at,
            ended_at=rollup.ended_at,
            turns=rollup.turns,
            model_usage=rollup.model_usage,
            tool_counts=rollup.tool_counts,
        )
        store.replace_session_days(source_path, rollup.day_usage)
    stats.sessions_upserted += 1
    if not resume:
        store.delete_findings_for_path(source_path)
    if scan_security:
        for finding in findings:
            store.add_finding(source_path=source_path, **finding)
            stats.findings_added += 1
