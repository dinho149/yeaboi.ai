"""Read-waste audit over local agent session transcripts.

Vendored and adapted from Headroom (https://github.com/headroomlabs-ai/headroom),
``headroom/audit/reads.py`` at v0.35.0 — Copyright Headroom contributors,
licensed under the Apache License, Version 2.0 (see THIRD_PARTY_NOTICES.md).
Changes from upstream: the audit walks an explicit list of transcript files
(the advisor engine scopes it to the window's sessions) instead of globbing a
root; the text renderer was dropped (yeaboi renders through
``agentwatch/render.py``); a per-file progress callback was added for the TUI
phase meter.

Measures, from real session data, the addressable bytes for each Read-waste
mechanism — so the advisor's recoverable-spend figures come from traffic, not
theory. Read-only: streams Claude Code session transcripts (JSONL) and never
modifies anything.

What it sizes, per mechanism:

- **identical repeat** — a later Read byte-identical to an earlier Read of the
  same file.
- **subset containment** — a later partial Read contained in an earlier full
  Read of the same file.
- **write-readback** — a Read whose content echoes a prior Write input.
- **stale** — Reads of files later edited in the same session (recoverable
  only with staleness-aware context handling, so the engine reports it as
  context rather than summing it into the recoverable headline).
- **line-number scaffolding** — ``cat -n`` prefix bytes inside Read output,
  counted only for reads not already charged to a whole-read class above
  (those rows contain their scaffolding; counting it again would double-bill).
- **context residency** — how many assistant turns each Read stays in context
  (the multiplier on its prefix-cache read cost).
- **cache-death windows** — inter-message gaps exceeding the provider cache
  TTL (free recompression moments; also a cache-health signal on their own).

The privacy invariant holds here exactly as in the collector: transcript text
is read in the stream and held only in per-file memory for the containment
checks — nothing from a transcript's *content* ever leaves this module. The
report carries counts and byte totals only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Reads below this size are noise, not opportunity (matches upstream's
# ReadLifecycleConfig.min_size_bytes).
MIN_SIZE = 512
_LINE_NUM_RE = re.compile(r"^\s*\d+\t", re.M)

_MUTATING_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


@dataclass
class ReadAuditReport:
    """Aggregated audit results. All byte figures are UTF-8 bytes of
    tool_result content; tokens ≈ bytes / 4."""

    sessions: int = 0
    files_skipped: int = 0
    tool_bytes: dict[str, int] = field(default_factory=dict)
    read_calls: int = 0
    read_bytes: int = 0
    read_calls_small: int = 0
    dedup_identical_calls: int = 0
    dedup_identical_bytes: int = 0
    subset_calls: int = 0
    subset_bytes: int = 0
    write_readback_calls: int = 0
    write_readback_bytes: int = 0
    stale_calls: int = 0
    stale_bytes: int = 0
    linenum_overhead_bytes: int = 0
    residency_median: int = 0
    residency_p90: int = 0
    gaps_over_5m: int = 0
    gaps_over_1h: int = 0
    sessions_with_gap: int = 0


def _block_text(content: object) -> str:
    """Flatten a tool_result content field (string or block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _parse_ts(line: dict) -> float | None:
    ts = line.get("timestamp")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class _Agg:
    """Cross-file accumulator (report + the residency sample list)."""

    def __init__(self) -> None:
        self.report = ReadAuditReport()
        self.tool_bytes: dict[str, int] = defaultdict(int)
        self.residency: list[int] = []


def _audit_session(path: Path, agg: _Agg) -> None:
    """Stream one transcript into the aggregate. Content stays in this frame."""
    r = agg.report
    tool_meta: dict[str, tuple[str, dict]] = {}
    file_reads: dict[str, list[tuple[str, str]]] = defaultdict(list)
    file_writes: dict[str, list[str]] = defaultdict(list)
    read_events: list[tuple[str, int, int, bool]] = []  # (file, size, at, deduped)
    edit_files_at: list[tuple[int, str]] = []
    assistant_idx = 0
    # One Claude Code API response spans several JSONL lines sharing a
    # requestId (see collector.py's dedup) — residency is measured in
    # assistant *turns*, so the index advances once per request, not per line.
    seen_requests: set[str] = set()
    prev_ts: float | None = None
    had_gap = False

    with path.open(errors="replace") as f:
        for line_no, raw in enumerate(f, start=1):
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(line, dict):
                continue
            msg = line.get("message")
            msg = msg if isinstance(msg, dict) else {}
            role = msg.get("role")
            content = msg.get("content")

            ts = _parse_ts(line)
            if ts is not None and prev_ts is not None:
                gap = ts - prev_ts
                if gap > 3600:
                    r.gaps_over_1h += 1
                    r.gaps_over_5m += 1
                    had_gap = True
                elif gap > 300:
                    r.gaps_over_5m += 1
                    had_gap = True
            if ts is not None:
                prev_ts = ts

            if role == "assistant" and isinstance(content, list):
                request_id = str(line.get("requestId") or line.get("uuid") or f"line:{line_no}")
                if request_id not in seen_requests:
                    seen_requests.add(request_id)
                    assistant_idx += 1
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        name = b.get("name", "")
                        inp = b.get("input") or {}
                        inp = inp if isinstance(inp, dict) else {}
                        tool_meta[str(b.get("id", ""))] = (str(name), inp)
                        fp = str(inp.get("file_path") or inp.get("path") or "")
                        if name in _MUTATING_TOOLS and fp:
                            edit_files_at.append((assistant_idx, fp))
                            if name == "Write":
                                file_writes[fp].append(str(inp.get("content", "")))

            if role == "user" and isinstance(content, list):
                for b in content:
                    if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                        continue
                    tid = str(b.get("tool_use_id", ""))
                    name, inp = tool_meta.get(tid, ("", {}))
                    text = _block_text(b.get("content"))
                    size = len(text.encode("utf-8", errors="replace"))
                    agg.tool_bytes[name or "unknown"] += size
                    if name != "Read":
                        continue

                    r.read_calls += 1
                    r.read_bytes += size
                    fp = str(inp.get("file_path") or inp.get("path") or "")
                    is_partial = inp.get("offset") is not None or inp.get("limit") is not None
                    if size < MIN_SIZE:
                        r.read_calls_small += 1

                    h = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
                    deduped = False
                    classified = False
                    if size >= MIN_SIZE and fp:
                        prior = file_reads[fp]
                        if any(ph == h for ph, _ in prior):
                            r.dedup_identical_bytes += size
                            r.dedup_identical_calls += 1
                            deduped = classified = True
                        elif is_partial and text and any(text in pc for _, pc in prior if len(pc) > len(text)):
                            r.subset_bytes += size
                            r.subset_calls += 1
                            classified = True
                        elif any(text.strip() and w.strip() and text.strip() in w for w in file_writes.get(fp, [])):
                            r.write_readback_bytes += size
                            r.write_readback_calls += 1
                            classified = True
                    # Scaffolding bytes only for reads NOT already charged to one
                    # of the whole-read classes above — those rows contain their
                    # scaffolding, and counting it again double-bills the byte
                    # in the recoverable sum.
                    if not classified:
                        r.linenum_overhead_bytes += sum(len(m.group(0)) for m in _LINE_NUM_RE.finditer(text))
                    if fp:
                        file_reads[fp].append((h, text))
                    read_events.append((fp, size, assistant_idx, deduped))

    for fp, size, at, deduped in read_events:
        if size >= MIN_SIZE and fp and not deduped and any(idx > at and ef == fp for idx, ef in edit_files_at):
            r.stale_bytes += size
            r.stale_calls += 1
        agg.residency.append(max(0, assistant_idx - at))

    if had_gap:
        r.sessions_with_gap += 1
    r.sessions += 1


def audit_files(
    paths: Iterable[Path],
    *,
    on_file: Callable[[int, int], None] | None = None,
) -> ReadAuditReport:
    """Audit the given transcript files; never raises.

    ``on_file(current, total)`` is called after each file for progress meters.
    An unreadable file counts in ``files_skipped`` rather than failing the
    audit — the advisor's numbers are a floor either way.
    """
    agg = _Agg()
    resolved = list(paths)
    for i, p in enumerate(resolved, start=1):
        try:
            _audit_session(p, agg)
        except OSError:
            agg.report.files_skipped += 1
        if on_file is not None:
            on_file(i, len(resolved))

    r = agg.report
    r.tool_bytes = dict(sorted(agg.tool_bytes.items(), key=lambda kv: -kv[1]))
    if agg.residency:
        rt = sorted(agg.residency)
        r.residency_median = rt[len(rt) // 2]
        r.residency_p90 = rt[int(len(rt) * 0.9)]
    return r
