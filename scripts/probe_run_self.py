#!/usr/bin/env python3
"""What does a cloud routine session know about its own run?

Two facts that `cowork/check-in.md` rests on are not recorded anywhere, and both
are cheap to settle from inside one firing:

1. **Is the session's own transcript on disk?** Claude Code writes a JSONL per
   session under ``~/.claude/projects/``. If the routine sandbox has one, the
   check-in can sum its ``usage`` blocks and report what the run cost — and
   because the sandbox is built fresh per run, *every* transcript in that tree
   belongs to this run, main session and subagents alike, so no session-id
   matching is needed. If it is absent, there is no token figure to report and
   the check-in ships without one.
2. **Does the session know its own run id?** ``RemoteTrigger list_runs`` returns
   a per-run ``https://claude.ai/code/session_<id>`` URL, but that tool is
   absent from the routine runtime (see `cron/cd-deploy.md`, "Being granted it is
   not the same as having it"). If the id arrives some other way — an env var, or
   an injected tag the way local scheduled tasks get ``<scheduled-task name=…>``
   — the check-in can link the exact run instead of the routine.

Neither answer blocks the feature; each picks which branch ships. Run it from a
throwaway routine whose name does **not** begin with ``cowork: ``, so it stays
invisible to `trigger_plan`'s orphan detection forever, then disable it — the
routines API has no delete.

**No secret values are printed.** Environment variables are reported as names
with a presence flag and a length, never contents, and transcript *content* is
never read out — only the numeric ``usage`` blocks, which is the same privacy
invariant `agentwatch/collector.py` holds.

**stdlib only**, like `probe_github_access.py`: it has to run in a checkout with
no environment built.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cowork_checkin import RUN_SESSION_ENV, run_url  # noqa: E402 - after the sys.path line that makes it importable

# Names worth reporting the presence of. A routine session is a Claude Code
# process, so if it is told its own run id at all it is almost certainly through
# one of these prefixes.
ENV_PREFIXES = ("CLAUDE", "ANTHROPIC", "CCR_", "SESSION", "AGENT", "TRIGGER", "ROUTINE")

# Usage keys, spelled exactly as `agentwatch/collector.py:_add_usage` reads them.
# Duplicated rather than imported on purpose: the probe must run before anything
# is installed, and it is proving the *shape on disk*, which an import of the
# reader would assume rather than test.
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")

# How many transcript rows the table prints before summarising the rest.
FILE_ROWS = 20


def env_report() -> list[dict]:
    """Every interesting env var by name, with presence and length — never a value."""
    out = []
    for key in sorted(os.environ):
        if any(key.startswith(prefix) for prefix in ENV_PREFIXES):
            value = os.environ[key]
            out.append({"name": key, "set": bool(value), "length": len(value)})
    return out


def _usage_of(record: dict) -> dict | None:
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    return usage if isinstance(usage, dict) else None


def transcript_report(root: Path) -> dict:
    """What is in ``~/.claude/projects``, in numbers only."""
    report: dict = {"root": str(root), "exists": root.is_dir(), "files": [], "totals": {}}
    if not report["exists"]:
        return report

    totals = dict.fromkeys(USAGE_KEYS, 0)
    totals["cache_creation_5m"] = 0
    totals["cache_creation_1h"] = 0
    seen_requests: set[str] = set()
    models: set[str] = set()

    for path in sorted(root.rglob("*.jsonl")):
        entry = {
            "path": str(path.relative_to(root)),
            "bytes": path.stat().st_size,
            "lines": 0,
            "session_id": "",
            "cwd": "",
            "sidechain_lines": 0,
            "assistant_lines": 0,
        }
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                entry["lines"] += 1
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(record, dict):
                    continue
                entry["session_id"] = entry["session_id"] or str(record.get("sessionId") or "")
                entry["cwd"] = entry["cwd"] or str(record.get("cwd") or "")
                # A subagent's turns ride the same file marked `isSidechain`. Whether
                # they do here decides if the check-in's number covers the Task fan-out.
                if record.get("isSidechain"):
                    entry["sidechain_lines"] += 1
                if record.get("type") != "assistant":
                    continue
                entry["assistant_lines"] += 1
                usage = _usage_of(record)
                if usage is None:
                    continue
                # One API message spans several lines repeating identical usage;
                # `collector.py` dedupes on requestId and so must this, or the
                # probe reports a total the real reader will never agree with.
                key = str(record.get("requestId") or record.get("uuid") or "")
                if key and key in seen_requests:
                    continue
                if key:
                    seen_requests.add(key)
                model = (record.get("message") or {}).get("model")
                if model:
                    models.add(str(model))
                for field in USAGE_KEYS:
                    totals[field] += int(usage.get(field) or 0)
                creation = usage.get("cache_creation")
                if isinstance(creation, dict):
                    totals["cache_creation_5m"] += int(creation.get("ephemeral_5m_input_tokens") or 0)
                    totals["cache_creation_1h"] += int(creation.get("ephemeral_1h_input_tokens") or 0)
        report["files"].append(entry)

    report["totals"] = totals
    report["models"] = sorted(models)
    report["deduped_requests"] = len(seen_requests)
    return report


def probe(tag: str) -> dict:
    home = Path(os.path.expanduser("~"))
    return {
        "session": {
            "home": str(home),
            "cwd": os.getcwd(),
            "uid": os.getuid(),
            "python": sys.executable,
            # What the model saw at the top of its own conversation. The script
            # cannot look at that itself, so the routine reports it with --tag.
            "self_identifying_tag": tag or "not reported",
            # Printed in full, unlike every other variable: this is the id of the
            # account's own run, it is what the check-in's log link is built from,
            # and a masked one would make the probe unable to answer the question
            # it exists for. It is not a credential.
            "run_session_id": os.environ.get(RUN_SESSION_ENV, ""),
            "run_url": run_url(),
        },
        "env": env_report(),
        "transcripts": transcript_report(home / ".claude" / "projects"),
    }


def render(data: dict) -> str:
    session = data["session"]
    transcripts = data["transcripts"]
    lines = [
        "## Routine self-knowledge probe",
        "",
        f"home                 {session['home']}",
        f"cwd                  {session['cwd']}",
        f"self-identifying tag {session['self_identifying_tag']}",
        f"run session id      {session['run_session_id'] or '—'}",
        f"run url             {session['run_url'] or '—'}",
        "",
        "### Environment (names only, never values)",
        "",
    ]
    if data["env"]:
        lines += ["| name | set | length |", "|---|---|---|"]
        lines += [f"| `{item['name']}` | {'yes' if item['set'] else 'no'} | {item['length']} |" for item in data["env"]]
    else:
        lines.append("_Nothing matching " + ", ".join(ENV_PREFIXES) + "._")

    lines += ["", "### Own transcript", "", f"root    {transcripts['root']}"]
    if not transcripts["exists"]:
        lines += ["", "**ABSENT** — there is no token figure to report; the check-in ships without one."]
        return "\n".join(lines)

    lines += [
        f"files   {len(transcripts['files'])}",
        f"models  {', '.join(transcripts['models']) or '—'}",
        "",
        "| file | lines | assistant | sidechain | session id |",
        "|---|---|---|---|---|",
    ]
    # A routine sandbox holds one or two files. A cap only bites when the probe is
    # run locally against a real machine, where the table is not the point.
    for item in transcripts["files"][:FILE_ROWS]:
        lines.append(
            f"| `{item['path']}` | {item['lines']} | {item['assistant_lines']} "
            f"| {item['sidechain_lines']} | `{item['session_id'] or '—'}` |"
        )
    if len(transcripts["files"]) > FILE_ROWS:
        lines.append(f"| _… {len(transcripts['files']) - FILE_ROWS} more_ | | | | |")
    totals = transcripts["totals"]
    lines += [
        "",
        f"input {totals['input_tokens']:,} · output {totals['output_tokens']:,} · "
        f"cache read {totals['cache_read_input_tokens']:,} · "
        f"cache write 5m {totals['cache_creation_5m']:,} / 1h {totals['cache_creation_1h']:,}",
        f"({transcripts['deduped_requests']:,} distinct API requests)",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--tag",
        default="",
        help="what the session was told about itself at the top of its conversation, verbatim (or 'none')",
    )
    parser.add_argument("--json", action="store_true", help="print the fixture JSON instead of the table")
    parser.add_argument("--out", type=Path, help="also write the fixture JSON here")
    args = parser.parse_args(argv)

    data = probe(args.tag)
    print(json.dumps(data, indent=2) if args.json else render(data))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    # Always 0: an absent transcript is the finding, not an error. A non-zero exit
    # would make the routine's stop conditions swallow the report.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
