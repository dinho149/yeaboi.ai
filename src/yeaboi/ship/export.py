"""Markdown export of one finished ship run — the record of a supervised run.

Deliberately Markdown-only, unlike the retro/poker/reporting exporters that also
emit HTML: a ship run's browser surface is the live board, and its artifact is a
record rather than a document a team reads together.

The patch itself is **not** included. It is capped per run in the store, it is
noise in a published page, and the branch is the real record — the diff stat
names the shape and a trailer names the command that prints the rest.

Everything here is scrubbed through the publication scrub the PR body already
uses (``standup.gap_issues.scrub``): a run record carries agent-authored text,
command output and absolute paths, and ``get_document`` feeds the shared
Notion/Confluence/copy picker.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from pathlib import Path

from yeaboi.agent.state import ShipRun

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def _slug(name: str) -> str:
    return _UNSAFE.sub("-", (name or "").lower()).strip("-.") or "repo"


def _title(run: ShipRun) -> str:
    return f"Ship — {run.item_id or run.run_id}"


def _stem(run: ShipRun) -> str:
    return _slug(run.run_id or run.item_id or "ship-run")


def _elapsed(seconds: float) -> str:
    """A phase duration as `1m 04s` / `12s`; empty below a second."""
    if seconds < 1:
        return ""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def build_ship_markdown(run: ShipRun, *, gate_events: Sequence[tuple[str, str, str]] = ()) -> str:
    """The run as a Markdown record. Scrubbed — safe to publish.

    ``gate_events`` is ``ShipStore.gate_events(run_id)`` (event, detail, created_at),
    oldest first; it is the approval trail and the only place a rejection comment
    is kept.
    """
    from yeaboi.standup.gap_issues import scrub

    status = run.status or "unknown"
    lines: list[str] = [
        f"# {_title(run)}",
        "",
        f"**Status** {status}"
        + (f" · **Branch** `{run.branch}`" if run.branch else "")
        + (f" · **Cost** ${run.cost_usd:.2f}" if run.cost_usd else ""),
        "",
    ]
    if run.pr_url:
        lines += [f"**Pull request** {run.pr_url}", ""]

    facts = [
        ("Run", run.run_id),
        ("Repository", run.repo),
        ("Base commit", run.base_sha[:12] if run.base_sha else ""),
        ("Started", run.created_at),
        ("Updated", run.updated_at),
    ]
    lines += ["| | |", "|---|---|"]
    for label, value in facts:
        if value:
            lines.append(f"| {label} | `{value}` |")
    lines.append("")

    if run.phases:
        lines += ["## Phases", ""]
        marks = {"completed": "✓", "failed": "✗", "skipped": "○"}
        for phase in run.phases:
            mark = marks.get(phase.status, "·")
            took = _elapsed(phase.duration_s)
            detail = f" — {phase.detail}" if phase.detail else ""
            lines.append(f"- {mark} **{phase.name}**{f' ({took})' if took else ''}{detail}")
        lines.append("")

    lines += ["## Changes", ""]
    if run.diff_stat:
        lines += ["```", run.diff_stat, "```", ""]
    else:
        lines += ["_No diff was recorded for this run._", ""]
    if run.branch and run.base_sha:
        lines += [f"Read the patch with `git diff {run.base_sha[:12]}..{run.branch}`.", ""]

    lines += ["## Validation", ""]
    if not run.validation.configured:
        lines += ["_None configured — nothing was proven about this change._", ""]
    else:
        verdict = "passed" if run.validation.passed else f"FAILED (exit {run.validation.exit_code})"
        lines += [f"`{run.validation.command}` — {verdict}", ""]
        if run.validation.output_tail:
            lines += ["```", run.validation.output_tail.strip(), "```", ""]

    if run.transcript_findings:
        lines += ["## Security findings", "", "| Kind | Severity | What |", "|---|---|---|"]
        for kind, severity, label in run.transcript_findings:
            lines.append(f"| {kind} | {severity} | {label} |")
        lines.append("")

    lines += ["## Approval trail", ""]
    if gate_events:
        for event, detail, created_at in gate_events:
            lines.append(f"- **{event}** {created_at}" + (f" — {detail}" if detail else ""))
    elif run.gate_resolution:
        lines.append(f"- **{run.gate_resolution}**" + (f" — {run.gate_comment}" if run.gate_comment else ""))
    else:
        lines.append("- _The gate was never answered._")
    if run.rejection_count:
        lines.append(f"- Rejections before this state: {run.rejection_count}")
    lines.append("")

    if run.warnings:
        lines += ["## Warnings", "", *[f"- {w}" for w in run.warnings], ""]

    lines += ["---", "🦆 Recorded by yeaboi's supervised agent pipeline."]
    return scrub("\n".join(lines), {})


def export_ship(run: ShipRun, *, gate_events: Sequence[tuple[str, str, str]] = ()) -> dict[str, Path]:
    """Write the run record as Markdown under the ship export dir.

    Returns ``{"markdown": Path}``. Named by run id, which is already unique, so
    re-exporting a run overwrites its own file and never another's.
    """
    from yeaboi.paths import get_ship_export_dir

    out_dir = get_ship_export_dir(Path(run.repo).name if run.repo else "ship")
    md_path = out_dir / f"{_stem(run)}.md"
    md_path.write_text(build_ship_markdown(run, gate_events=gate_events), encoding="utf-8")
    logger.info("Ship run exported: %s", md_path)
    return {"markdown": md_path}
