#!/usr/bin/env python3
"""The check-in a cowork routine posts when its run ends.

The fleet ran twenty-four routines a day and said nothing about its own running.
A sweep that found nothing and a sweep that died on an authentication error after
one turn produced the same observable: silence. `cowork/README.md` calls that
silence load-bearing, and it is — for *findings*. It carries nothing at all about
whether the fleet is alive, and nothing anywhere carried what a run cost.

So every routine now closes with one thread reply under the day's 📅 message:
what it was, whether it worked, what it did, what it spent, and where the log is.
Replies, not channel messages — the two-to-four-a-day channel budget is exactly
what makes the channel worth reading, and a heartbeat belongs under the schedule
it is closing out, not beside it.

**This script composes the whole message.** `cron/day-ahead.md` set the pattern:
the routine runs a command and posts the lines it is handed, so nothing is
improvised into a message nobody can diff. A routine supplies four facts it alone
knows — its name, whether it worked, one clause about what it did, and a link —
and everything numeric is measured here.

**The tokens are measured, not estimated by a model.** `agents-standup` once
reported "1 session · ~$0.10" about itself while the laptop it was describing
held seventy-four sessions and about $521, and the fix was to forbid it from
reporting on itself at all. The number here comes from the transcript on disk,
through the same reader the product ships (`agentwatch.collector`), priced by the
same table (`yeaboi.pricing`) — so a check-in and `yeaboi agents cost` cannot
disagree without one of them being broken.

**Why every transcript in the sandbox counts as this run's.** A routine sandbox
is built fresh per firing, so `~/.claude/projects` holds this run and nothing
else — main session and every `Task` subagent alike. That is what lets the total
be right without knowing its own session id, which a routine has no way to learn
(`RemoteTrigger` is absent from the runtime; see `cron/cd-deploy.md`).

**It is a floor, not a total.** The transcript is still being written while it is
read, so the closing turn and the check-in's own tokens are not in it. Hence `~`
and `≈`, once, in the message — never re-derived per run. Measured 2026-08-14
against `yeaboi agents cost` over the same 856 transcripts: $8,644.91 here to
$8,645.27 there, the whole gap being the seconds between the two reads.

Content never leaves: token counts, filenames and model ids only, which is the
privacy invariant `agentwatch/collector.py` already holds.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cowork_setup import display_zone  # noqa: E402 - after the sys.path line that makes it importable

ROOT = Path(__file__).resolve().parent.parent

# Status glyphs. Deliberately not ✅/❌: those are the human approval verbs, and
# `.claude/agents/cowork-scribe.md` permits them in message text only in an
# instructing footer — a check-in carrying one invites a reader to answer a
# heartbeat. These three collide with nothing in `SECTION_EMOJI`, the digest's
# eleven or the README's title-line set, and none is a variation sequence (a
# trailing U+FE0F renders two ways across clients), which `TestCheckIn` pins by
# length rather than by trusting this comment.
STATUS_GLYPH = {"ok": "🟢", "degraded": "🟡", "failed": "🔴"}

# Never allowed in a note, whoever writes one. ✅/❌ are the approval verbs the
# relay's humans use; 🤖 is the relay's own handled-marker and is never written
# in message text at all. A routine improvising one into its summary clause would
# be indistinguishable from a human answering.
RESERVED_GLYPHS = ("✅", "❌", "🤖")

# One clause, one line. Long enough for "1 PR (#261), 2 proposals filed", short
# enough that a thread of twenty stays scannable.
NOTE_LIMIT = 110

# Where Claude Code writes session transcripts, on any machine. Resolved at call
# time, not import: `Path.home()` reads $HOME, and freezing it here would make
# the default unmockable.
TRANSCRIPT_SUBPATH = (".claude", "projects")


def transcript_root() -> Path:
    return Path.home().joinpath(*TRANSCRIPT_SUBPATH)


# The one thing a routine session was assumed not to know. `RemoteTrigger` is
# absent from the runtime, so nothing could look the run up — but the runtime
# exports the id anyway, and it is *the same id* `RemoteTrigger list_runs`
# reports and links. Probed 2026-08-14 from inside two firings of a throwaway
# `probe: run-self` routine and recorded in
# `tests/fixtures/cowork_run_self_live.json`: `run` returned
# `cse_01DBM5LwdWwgpUydtanGHuAt` and the session's own environment held it
# verbatim. So the check-in links the exact run, computed in-session, with no
# API call and no fallback to the routine's page.
RUN_SESSION_ENV = "CLAUDE_CODE_REMOTE_SESSION_ID"

# The id is `cse_…`; the URL spells the same suffix `session_…`.
RUN_URL = "https://claude.ai/code/session_{suffix}"


def run_url(session_id: str = "") -> str:
    """This run's page at claude.ai, or "" when the id is absent or unfamiliar.

    An unrecognised id returns nothing rather than a guessed URL: a check-in with
    no link says so, and a check-in with a link that 404s is worse than both.
    """
    raw = (session_id or os.environ.get(RUN_SESSION_ENV, "")).strip()
    if not raw.startswith("cse_") or len(raw) <= len("cse_"):
        return ""
    return RUN_URL.format(suffix=raw[len("cse_") :])


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


def usage_report(root: Path | None = None) -> dict:
    """Everything this run spent, measured off the transcripts in ``root``.

    Returns ``available: False`` with a reason rather than raising: a check-in
    that cannot price itself must still post that the run happened, which is the
    half of the message that cannot be recovered later.
    """
    root = transcript_root() if root is None else root
    if not root.is_dir():
        return {"available": False, "reason": f"no transcript directory at {root}"}
    try:
        from yeaboi.agentwatch.collector import refresh
        from yeaboi.agentwatch.engine import _session_cost
        from yeaboi.agentwatch.store import AgentWatchStore
        from yeaboi.pricing import PRICING_AS_OF
    except ImportError as exc:  # pragma: no cover - exercised by the bare-checkout path
        return {"available": False, "reason": f"yeaboi is not importable ({exc})"}

    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0}
    cost = 0.0
    known_models = True
    models: set[str] = set()
    starts: list[str] = []
    ends: list[str] = []

    # A throwaway store. The cursor in `collector.refresh` exists to make repeat
    # scans cheap across runs; there is no next run for this database, and a
    # temporary one keeps the check-in from touching the user's agentwatch data.
    with tempfile.TemporaryDirectory(prefix="cowork-checkin-") as tmp:
        with AgentWatchStore(Path(tmp) / "checkin.db") as store:
            # `_session_cost` is private, and imported anyway on purpose: it is the one
            # place the five token kinds are mapped onto `pricing.estimate_cost`, and a
            # public alias for it would have to be mirrored into `go/internal/agentwatch/`
            # for no behaviour change at all. Copying the six lines here is the version
            # that silently drifts.
            refresh(store, roots=(("claude_code", root),))
            sessions = store.list_sessions()
            for row in sessions:
                for model, used in (row.get("model_usage") or {}).items():
                    models.add(model)
                    for key in totals:
                        totals[key] += int(used.get(key, 0) or 0)
                session_cost, all_known = _session_cost(row.get("model_usage") or {})
                cost += session_cost
                known_models = known_models and all_known
                if row.get("started_at"):
                    starts.append(row["started_at"])
                if row.get("ended_at"):
                    ends.append(row["ended_at"])

    return {
        "available": True,
        # Transcript files, not logical sessions: `engine._distinct_session_count`
        # collapses a resumed session's two rollups into one, and this is counting
        # what it summed. In a routine sandbox they are the same number anyway.
        "transcripts": len(sessions),
        "tokens": dict(totals),
        # What the message reports: everything the run put through a model. Cache
        # reads dominate it and belong in it — they are consumption, and leaving
        # them out would make the figure disagree with the cost beside it.
        "total_tokens": sum(totals.values()),
        "cost_usd": round(cost, 4),
        "known_models": known_models,
        "models": sorted(models),
        "pricing_as_of": PRICING_AS_OF,
        "started_at": min(starts) if starts else "",
        "ended_at": max(ends) if ends else "",
        "duration_seconds": _span(starts, ends),
        "run_url": run_url(),
    }


def _moment(stamp: str) -> datetime | None:
    """One ISO timestamp as an aware datetime, or None when it is unreadable.

    A naive stamp is read as UTC. The store holds both spellings — `…Z` from one
    writer and `…+00:00` from another — and subtracting one from the other raises
    `TypeError`, which would take the whole check-in down over a timezone suffix.
    """
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def _span(starts: list[str], ends: list[str]) -> int:
    """Wall-clock seconds from the first transcript's start to the last one's end.

    The endpoints are picked *after* parsing, not by a lexicographic `min`/`max`
    over the raw strings: `…Z` sorts after `…+00:00` for the same instant, so
    comparing the text picks the wrong row whenever both spellings are present.
    """
    first = min((m for m in map(_moment, starts) if m), default=None)
    last = max((m for m in map(_moment, ends) if m), default=None)
    if first is None or last is None:
        return 0
    return max(0, int((last - first).total_seconds()))


# ---------------------------------------------------------------------------
# The line
# ---------------------------------------------------------------------------


def compact(count: int) -> str:
    """``1234567`` → ``1.2M``. A check-in is read at a glance, not audited."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


def duration(seconds: int) -> str:
    """``256`` → ``4m``. Rounded down to the unit a reader can act on."""
    if seconds >= 3600:
        hours, rest = divmod(seconds, 3600)
        return f"{hours}h {rest // 60}m" if rest >= 60 else f"{hours}h"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def clean_note(note: str) -> str:
    """One clause, on one line, carrying no glyph a human or the relay answers."""
    text = re.sub(r"\s+", " ", str(note or "")).strip()
    for glyph in RESERVED_GLYPHS:
        text = text.replace(glyph, "")
    text = re.sub(r"\s+", " ", text).strip(" ·-—")
    if len(text) > NOTE_LIMIT:
        text = text[: NOTE_LIMIT - 1].rstrip() + "…"
    return text


def local_time(started_at: str) -> str:
    """The run's start in ``DISPLAY_TZ``, spelled the way `--agenda` spells it.

    It has to match: the reply is read against the 📅 line it closes out, and two
    renderings of the same instant are two runs to anybody scanning the thread.
    """
    moment = _moment(started_at) if started_at else None
    if moment is None:
        return "--:--"
    zone, _ = display_zone()
    return f"{moment.astimezone(zone) if zone else moment:%H:%M}"


def check_in_line(facts: dict, usage: dict) -> str:
    """The finished two-line Slack reply. Standard Markdown, never Slack mrkdwn.

    Slack's connector takes ``**bold**``; mrkdwn's ``*bold*`` renders as italic
    here, which shipped italic headings on the agenda for weeks before anybody
    measured it against the real channel.
    """
    name = str(facts.get("name") or "").strip()
    if not name:
        raise ValueError("a check-in needs the routine's name")
    status = str(facts.get("status") or "ok")
    if status not in STATUS_GLYPH:
        raise ValueError(f"status must be one of {', '.join(STATUS_GLYPH)}, not {status!r}")

    started = str(facts.get("started_at") or usage.get("started_at") or "")
    seconds = int(facts.get("duration_seconds") or usage.get("duration_seconds") or 0)
    head = f"`{local_time(started)}` **{name}** {STATUS_GLYPH[status]} {duration(seconds)}"
    note = clean_note(facts.get("note", ""))
    if note:
        head += f" · {note}"

    if usage.get("available"):
        spend = f"~{compact(int(usage.get('total_tokens', 0)))} tok ≈ ${float(usage.get('cost_usd', 0.0)):,.2f}"
        # A model with no row in the price table is costed at a fallback rate, and
        # a figure that quietly used one is worse than one that says it did.
        if not usage.get("known_models", True):
            spend += " (some models unpriced)"
    else:
        spend = f"usage unmeasured — {usage.get('reason', 'no transcript')}"

    url = str(facts.get("url") or "").strip() or run_url()
    if url:
        spend += f" · [log]({url})"
    return f"{head}\n{spend}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_facts(path: Path | None) -> dict:
    raw = path.read_text(encoding="utf-8") if path else sys.stdin.read()
    facts = json.loads(raw)
    if not isinstance(facts, dict):
        raise ValueError("facts must be a JSON object")
    return facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--usage", action="store_true", help="print what this run spent, as JSON")
    parser.add_argument("--line", action="store_true", help="print the finished Slack reply")
    parser.add_argument("--facts", type=Path, help="with --line: the facts JSON file (default: stdin)")
    parser.add_argument("--root", type=Path, help="transcript directory to measure (default: ~/.claude/projects)")
    args = parser.parse_args(argv)

    if not args.usage and not args.line:
        parser.error("pass --usage or --line")

    usage = usage_report(args.root)
    if args.usage and not args.line:
        print(json.dumps(usage, indent=2))
        return 0

    try:
        facts = _read_facts(args.facts)
        print(check_in_line(facts, usage))
    except (ValueError, OSError) as exc:
        # Non-zero on purpose, and it is the *signal*, not a verdict on the run:
        # `cowork/check-in.md` reads a failure here as "post nothing rather than
        # an improvised line", and there is nothing else for a routine to key off.
        # It is the last step, so nothing downstream reads this exit code.
        print(f"[checkin] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
