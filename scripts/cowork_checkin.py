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
import _gh_transport as transport  # noqa: E402 - after the sys.path line that makes it importable
from cowork_setup import LEDGER_LABEL, display_zone  # noqa: E402 - same

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


# --- the ledger --------------------------------------------------------------
#
# Everything above measures one run and prints it. This half is what makes the
# measurement survive the run.
#
# The fleet is stateless on purpose — `cowork/README.md`: "GitHub issues **are**
# the queue — there is no other shared state between routine runs." That holds
# for *outcomes*: a PR, a proposal, an approval and a merge are all in GitHub with
# timestamps, so nothing needs recording and only counting. It does not hold for
# runs. This script already knows what a run cost, how long it took, whether it
# worked and where its log is, and until now it printed two lines of that to Slack
# and dropped the rest. Nothing could pull it back either: `agentwatch.collector`
# reads the filesystem, and a routine's sandbox dies with the container.
#
# So a run pushes. One issue per month, one comment per run, a fenced JSON block
# each.
#
# **The ledger is write-only, and that is the whole safety property.** No routine
# reads it, nothing branches on it, and `scripts/cowork_metrics.py` — which does
# read it — is a human's command and not part of any run. The statelessness that
# matters is that no run's behaviour depends on another run's state, and appending
# to a record nobody consults does not touch it. A routine that started reading
# this would have quietly given the fleet a memory, so `tests/unit/test_cowork_checkin.py`
# asserts no file under `cowork/routines/` names it.
#
# It is also invisible to every query that already exists, by carrying none of
# their labels: `digest.md`'s fourteen-day age-out would otherwise close it every
# month, and `codeql-triage.yml` reads `--label cowork --state all --limit 500` as
# its dedupe corpus.

# The marker that makes a comment findable as a ledger row rather than as prose
# somebody left on the issue. Read by `cowork_metrics.py`; a comment without it
# is skipped rather than parsed, so a human answering in the thread cannot become
# a run that never happened.
LEDGER_MARKER = "<!-- fleet-run -->"


def ledger_title(now: datetime | None = None) -> str:
    """The month's issue title. Monthly rather than one-forever because a year of
    twenty-four-a-day check-ins is nine thousand comments on one issue, and
    monthly rather than daily because the reader would then page thirty issues to
    answer one question."""
    return f"fleet ledger {(now or datetime.now(UTC)).strftime('%Y-%m')}"


def ledger_body(facts: dict, usage: dict) -> str:
    """One comment: a human-readable first line, then the row a reader parses.

    Both halves on purpose. The line is for whoever opens the issue wondering what
    this is; the JSON is the contract. Nothing is derived from the line — the
    reader never looks at it — so it can be reworded without breaking anything,
    which is the opposite of the markers in `cowork_relay.py`.
    """
    row = {
        "name": str(facts.get("name") or ""),
        "status": str(facts.get("status") or ""),
        "note": clean_note(str(facts.get("note") or "")),
        "url": str(facts.get("url") or usage.get("run_url") or ""),
        "started_at": usage.get("started_at") or "",
        "ended_at": usage.get("ended_at") or "",
        "duration_seconds": usage.get("duration_seconds") or 0,
        "total_tokens": usage.get("total_tokens") or 0,
        "cost_usd": usage.get("cost_usd") or 0.0,
        "models": usage.get("models") or [],
        # Carried so a reader can tell "this run spent nothing" from "this run
        # could not measure what it spent" — the same distinction `--proposal-slots`
        # draws between zero slots and an unreadable count.
        "available": bool(usage.get("available")),
        "pricing_as_of": usage.get("pricing_as_of") or "",
    }
    glyph = STATUS_GLYPH.get(row["status"], "")
    headline = f"`{row['name']}` {glyph} {duration(int(row['duration_seconds']))} · {row['note']}".rstrip(" ·")
    return f"{headline}\n\n```json\n{json.dumps(row, indent=2, sort_keys=True)}\n```\n\n{LEDGER_MARKER}"


def _ledger_issue(slug: str, title: str) -> tuple[int | None, str]:
    """The month's issue number, creating it if this is the month's first run.

    Whichever run fires first in a month opens that month's issue, and every run
    after it finds one. Deliberately not pre-created by a scheduled routine: that
    would put a write on the calendar to save a write that already happens, and it
    would still need this branch anyway — `cd-deploy` fires at 04:00 and the three
    event routines fire on a webhook, all of them ahead of any 05:45 opener on the
    first of the month.

    Two runs racing on the first of the month leaves two issues for it. That is
    tolerated rather than prevented: `cowork_metrics.py` reads *every* ledger issue
    covering the window rather than the newest, so two partial months are still the
    whole month — and de-duplicating a create needs a lock nothing here has.
    """
    found = transport.api_paged(f"/repos/{slug}/issues?labels={transport.segment(LEDGER_LABEL)}&state=open")
    if not found.ok:
        return None, found.error
    for issue in found.data if isinstance(found.data, list) else []:
        # `/issues` answers with pull requests too, and a PR is never a ledger.
        if isinstance(issue, dict) and issue.get("title") == title and "pull_request" not in issue:
            return int(issue["number"]), ""
    made = transport.api(
        "POST",
        f"/repos/{slug}/issues",
        {
            "title": title,
            "labels": [LEDGER_LABEL],
            "body": (
                "One comment per routine run: what ran, whether it worked, how long it took and what "
                "it spent. Written by `scripts/cowork_checkin.py --record`, read by "
                "`scripts/cowork_metrics.py`.\n\n"
                "**Nothing in the fleet reads this issue.** It is a record, not a queue — no routine "
                "branches on it, which is what keeps the fleet stateless while still leaving a trail. "
                "Do not add `cowork` or `workstream:` labels: they would put it in front of every "
                "query that looks for work, and `digest.md` would close it after fourteen days.\n\n"
                "Closing this issue is safe at any time — next month opens a new one."
            ),
        },
    )
    if not made.ok:
        return None, made.error
    return int(made.data["number"]), ""


def record(facts: dict, usage: dict, *, now: datetime | None = None) -> tuple[bool, str]:
    """Append this run to the month's ledger. Returns ``(wrote, error)``.

    Never raises and never blocks the check-in: a run that cannot reach GitHub has
    still done its work, and the Slack line is the part a human is waiting for.
    The caller reports a failure here on stderr and still exits on the strength of
    the line.
    """
    if not os.environ.get(RUN_SESSION_ENV, "").strip():
        # Outside a routine sandbox this measures the *machine*, not the run.
        # `usage_report` reads all of `~/.claude/projects`, which holds exactly
        # one run in a container built fresh per firing and holds everything you
        # have ever done on a laptop. The first live test of this path wrote a
        # row claiming one check-in took 800 hours and cost $8,699 — a number
        # that would have sat in the ledger looking like a fact, and that
        # `cost_per_merged_pr` would have divided by four.
        #
        # Refusing rather than clamping, because there is no honest local number
        # to write: the reading is not a run's cost that happens to be too big,
        # it is a different quantity. The Slack line is unaffected — it is
        # explicitly a floor and says so with `~`.
        return False, f"not a routine run ({RUN_SESSION_ENV} unset) — the measurement would be this machine, not a run"
    slug = transport.resolve_slug(ROOT)
    if not slug:
        return False, "could not resolve the repository slug"
    number, error = _ledger_issue(slug, ledger_title(now))
    if number is None:
        return False, error
    posted = transport.api("POST", f"/repos/{slug}/issues/{number}/comments", {"body": ledger_body(facts, usage)})
    return (True, "") if posted.ok else (False, posted.error)


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
    parser.add_argument("--record", action="store_true", help="also append this run to the month's fleet ledger")
    parser.add_argument(
        "--dry-run", action="store_true", help="with --record: print what would be posted, write nothing"
    )
    args = parser.parse_args(argv)

    if not args.usage and not args.line and not args.record:
        parser.error("pass --usage, --line or --record")

    usage = usage_report(args.root)
    if args.usage and not args.line and not args.record:
        print(json.dumps(usage, indent=2))
        return 0

    try:
        facts = _read_facts(args.facts)
        if args.line:
            print(check_in_line(facts, usage))
    except (ValueError, OSError) as exc:
        # Non-zero on purpose, and it is the *signal*, not a verdict on the run:
        # `cowork/check-in.md` reads a failure here as "post nothing rather than
        # an improvised line", and there is nothing else for a routine to key off.
        # It is the last step, so nothing downstream reads this exit code.
        print(f"[checkin] {exc}", file=sys.stderr)
        return 1

    if args.record:
        if args.dry_run:
            print(ledger_body(facts, usage))
        else:
            wrote, error = record(facts, usage)
            if not wrote:
                # Reported, never fatal, and deliberately *after* the line is
                # already on stdout. The check-in is what a human is waiting for;
                # the ledger is what a report reads next month. Failing the run
                # over the second would trade a thing somebody needs now for a
                # thing nobody needs yet, and `--line`'s exit code is the only
                # signal `check-in.md` tells a routine to look at.
                print(f"[checkin] ledger: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
