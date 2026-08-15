#!/usr/bin/env python3
"""Render the Go-migration Slack posts — the bar, the counts, every line.

Two routines post about the migration and neither composes a word:
``cron/go-migration-progress.md`` (Tuesdays) runs ``--weekly`` and
``events/go-migration-wave-merged.md`` runs ``--wave-merged --pr <n>``; both
post the printed ``lines`` verbatim. That is ``--agenda``'s contract — rendered,
not composed — and it exists because a model retyping a number is the failure
the fleet has already been bitten by. Every judgement in the message lives here,
where ``tests/unit/test_migration_progress.py`` can hold it.

Progress is recomputed from durable state on every run — there is no counter
between runs. The sources:

- the program of record's §3 checkbox table (``cowork/migration/program.md``) —
  self-recording, because each wave PR flips its own checkbox;
- open and merged PRs carrying ``workstream:go-migration``, read over REST
  (never ``gh pr list --json`` — that is GraphQL, which the routine-session
  egress proxy refuses; see ``tests/fixtures/cowork_github_access_live.json``);
- the ``yeaboi-core`` version from ``packaging/yeaboi-core/pyproject.toml``;
- the parity-test count from a ``pytest --collect-only`` subprocess, degraded to
  silence — never a guess — when it fails.

**stdlib only**, like ``_gh_transport`` — a routine session runs this in a
checkout with no environment built beyond ``uv run``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# scripts/ is not a package, so the sibling transport is imported by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gh_transport as transport  # noqa: E402 - after the sys.path line that makes it importable

REPO_ROOT = Path(__file__).resolve().parent.parent
PROGRAM_DOC = REPO_ROOT / "cowork" / "migration" / "program.md"
CORE_PYPROJECT = REPO_ROOT / "packaging" / "yeaboi-core" / "pyproject.toml"

LABEL = "workstream:go-migration"

# The sidecar pilot predates the program: waves 1-5 merged as PRs #215/#217/#221
# before the 13-row table existed, so the table cannot record them and the bar
# starts at five. Wave 6 is the seam — PR #224 was open when the program was
# committed, so it is read live rather than baked in: the one hardcoded PR
# number, gone from relevance the day it merges.
PILOT_WAVES_MERGED = 5
WAVE6_PR = 224
PILOT_WAVES = 6

# The campaign's wave-branch prefix — the mirror of PARITY_BRANCH_PREFIX in
# scripts/pr_feedback.py, and the other half of "labelled is not the same as
# being a wave" (see _is_wave).
WAVE_BRANCH_PREFIX = "cowork/migration-w"

# One glyph per wave, so the bar *is* the count. ▰/▱ are the product's own
# meter glyphs (`build_meter`, src/yeaboi/ui/shared/_components.py) and carry
# their own carve-out in TestSlackTemplates' emoji lint.
FILLED = "▰"
EMPTY = "▱"

# A §3 row: | ☐ | 1 | W7 | Retro/poker export builders | S | … |
_ROW = re.compile(r"^\|\s*(?P<box>[☐✔xX])\s*\|\s*(?P<pr>\d+)\s*\|\s*(?P<wave>W\d+)\s*\|\s*(?P<contents>[^|]+)\|")

# A program wave PR's title: `migration(w7): retro/poker export builders`.
_WAVE_TITLE = re.compile(r"^migration\(w(?P<wave>\d+)\)", re.IGNORECASE)

# An open wave PR older than this renders a stalled note. The campaign runs
# every weekday and a wave phase is a session, so ten quiet days is not "large
# wave" — it is "nothing is moving and a human should look".
STALLED_AFTER_DAYS = 10


@dataclass(frozen=True)
class Wave:
    """One §3 row of the program of record."""

    pr: int
    wave: str
    contents: str
    done: bool


def parse_program(text: str) -> list[Wave]:
    """The §3 table rows, in order. The checkbox column is the program's own
    record of what merged — each wave PR flips its box in the same PR.

    Bounded to the §3 section rather than the whole document: the per-wave spec
    sections the campaign appends follow the §6/§7 template and may quote a
    table row — a quoted `| ☐ | 2 | W8 | …` elsewhere must not inflate the bar.
    """
    section = re.search(r"^## 3\..*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if section:
        text = section.group(0)
    waves = []
    for line in text.splitlines():
        match = _ROW.match(line.strip())
        if match:
            waves.append(
                Wave(
                    pr=int(match.group("pr")),
                    wave=match.group("wave"),
                    contents=match.group("contents").strip(),
                    done=match.group("box") != "☐",
                )
            )
    return waves


def meter(filled: int, total: int) -> str:
    """The bar, one glyph per wave, clamped so a miscount cannot render outside
    the track."""
    filled = max(0, min(filled, total))
    return FILLED * filled + EMPTY * (total - filled)


def core_version() -> str | None:
    """The ``yeaboi-core`` wheel version at this checkout."""
    try:
        text = CORE_PYPROJECT.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


def parity_test_count() -> int | None:
    """How many parity tests ``tests/parity`` collects, or None — never a guess.

    Collection, not a run: the count is the coverage story ("the gate grew with
    the wave"), and actually running the suite needs the Go binary this session
    may not have.
    """
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/parity"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d+) tests? collected", result.stdout)
    return int(match.group(1)) if match else None


def _get(path: str) -> object | None:
    """One REST GET through whichever transport this machine has — `gh` when it
    is there (a developer's auth lives in the CLI), the token otherwise (a
    routine session has a token and no CLI). None on any failure; the caller
    renders blindness, never a guess."""
    if transport.gh_available():
        result = transport.gh("api", path.lstrip("/"))
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout or "null")
        except json.JSONDecodeError:
            return None
    result = transport.api("GET", path)
    return result.data if result.ok else None


def _is_wave(data: dict) -> bool:
    """Whether a labelled PR is actually a *wave* — the campaign's branch
    prefix, or the sanctioned Wave 6 rescue PR.

    The same predicate as `parity_gated` in ``scripts/pr_feedback.py``, for the
    same reason: fleet convention labels every PR of a workstream, and a
    renderer bugfix under the label is not a wave. A 🌊 announcement for a docs
    chore — claiming a parity gate its diff deliberately skipped — is exactly
    the false post the README's "non-wave merges say nothing here" forbids.
    """
    head = ((data.get("head") or {}).get("ref")) or ""
    return head.startswith(WAVE_BRANCH_PREFIX) or data.get("number") == WAVE6_PR


def _open_wave_prs() -> list[dict] | None:
    """The open wave PRs, or None when the read failed.

    The pulls endpoint rather than issues, because only it carries the head
    ref `_is_wave` filters on. One page of 100 is deliberate rather than lazy —
    the lane holds one open PR at a time. None is never an empty list — a queue
    reported empty when it could not be asked is a migration that looks idle
    rather than blind.
    """
    slug = transport.resolve_slug(REPO_ROOT)
    if not slug:
        return None
    owner, name = slug.split("/")
    data = _get(f"/repos/{transport.segment(owner)}/{transport.segment(name)}/pulls?state=open&per_page=100")
    if not isinstance(data, list):
        return None
    found = []
    for item in data:
        if not isinstance(item, dict):
            continue
        labels = {label.get("name") for label in item.get("labels", []) if isinstance(label, dict)}
        if LABEL in labels and _is_wave(item):
            found.append(item)
    return found


def _wave6_merged() -> bool | None:
    """Whether the pilot's last PR merged — or None when the read failed.

    None, never False, on a failed read: a guess rendered as a hard number in
    the title is exactly the "degrades to blindness, never guesses" failure the
    module docstring forbids. The caller folds None into the blind marker.
    """
    data = _pr(WAVE6_PR)
    if data is None:
        return None
    return bool(data.get("merged"))


def _pr(number: int) -> dict | None:
    slug = transport.resolve_slug(REPO_ROOT)
    if not slug:
        return None
    owner, name = slug.split("/")
    data = _get(f"/repos/{transport.segment(owner)}/{transport.segment(name)}/pulls/{number}")
    return data if isinstance(data, dict) else None


def _program_url() -> str | None:
    slug = transport.resolve_slug(REPO_ROOT)
    return f"https://github.com/{slug}/blob/main/cowork/migration/program.md" if slug else None


def _day(stamp: str | None) -> str:
    """``Tue 19 Aug`` out of an ISO timestamp, or empty — the digest's date shape."""
    if not stamp:
        return ""
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"{moment:%a} {moment.day} {moment:%b}"


def build_payload(now: datetime | None = None) -> dict:
    """Everything both messages are rendered from, recomputed from scratch."""
    moment = now or datetime.now(UTC)
    waves = parse_program(PROGRAM_DOC.read_text(encoding="utf-8"))
    program_done = sum(1 for wave in waves if wave.done)
    wave6 = _wave6_merged()
    open_prs = _open_wave_prs()

    in_flight = None
    if open_prs is not None:
        in_flight = []
        for item in open_prs:
            opened = _day(item.get("created_at"))
            age_days = None
            try:
                created = datetime.fromisoformat(str(item.get("created_at", "")).replace("Z", "+00:00"))
                age_days = (moment - created).days
            except ValueError:
                pass
            in_flight.append(
                {
                    "title": item.get("title", ""),
                    "number": item.get("number"),
                    "url": item.get("html_url", ""),
                    "opened": opened,
                    "stalled": age_days is not None and age_days > STALLED_AFTER_DAYS,
                }
            )

    next_wave = next((wave for wave in waves if not wave.done), None)
    return {
        "today": f"{moment:%a} {moment.day} {moment:%b}",
        "waves_total": PILOT_WAVES + len(waves),
        # An unknown wave 6 counts as unshipped, and `blind` below is what keeps
        # that from reading as a fact — the bar may undercount while blind, and
        # the ⚠ line says so; it never overcounts.
        "waves_shipped": PILOT_WAVES_MERGED + (1 if wave6 else 0) + program_done,
        "program_total": len(waves),
        "program_done": program_done,
        # Any GitHub read failing makes the whole post blind: the counts came
        # from a mix of repo facts and a queue that could not be asked.
        "blind": in_flight is None or wave6 is None,
        "in_flight": in_flight,  # None = the read failed, [] = genuinely nothing open
        "next_wave": {"wave": next_wave.wave, "contents": next_wave.contents} if next_wave else None,
        "core_version": core_version(),
        "parity_tests": parity_test_count(),
        "program_url": _program_url(),
    }


def _bar_line(payload: dict) -> str:
    shipped, total = payload["waves_shipped"], payload["waves_total"]
    done = "?" if payload["blind"] else payload["program_done"]
    return (
        f"{meter(shipped, total)} {shipped}/{total} waves · {done}/{payload['program_total']} program wave-PRs merged"
    )


def _footer(payload: dict) -> str:
    url = payload.get("program_url")
    if url:
        return f"Next wave and the full plan: [the program of record]({url})"
    return "Next wave and the full plan: `cowork/migration/program.md`"


def weekly_lines(payload: dict) -> list[str]:
    """The Tuesday message. Pure over the payload, like ``agenda_lines``."""
    lines = [
        f"🐹 **Go Migration** — {payload['waves_shipped']} of {payload['waves_total']} waves shipped"
        f" · {payload['today']}",
        _bar_line(payload),
        "",
    ]
    in_flight = payload["in_flight"]
    if payload["blind"]:
        lines += ["⚠️ could not fully read GitHub — the counts above may undercount, never trust them this week", ""]
    if in_flight:
        lines += [f"🚧 **In flight** ({len(in_flight)})", ""]
        for position, item in enumerate(in_flight, start=1):
            clause = f"open since {item['opened']}" if item["opened"] else "open"
            if item["stalled"]:
                clause += " · stalled — see the wave's Linear ticket"
            lines += [f"{position}. [{item['title']} #{item['number']}]({item['url']})", f"   — {clause}", ""]
        lines += ["───────────────────────────", ""]
    shipped_bits = []
    if payload["core_version"]:
        shipped_bits.append(f"[yeaboi-core {payload['core_version']}](https://pypi.org/project/yeaboi-core/) on PyPI")
    if payload["parity_tests"] is not None:
        shipped_bits.append(f"{payload['parity_tests']} parity tests on `main`")
    if shipped_bits:
        lines += ["📦 **Shipped** — " + " · ".join(shipped_bits), ""]
    lines.append(_footer(payload))
    return lines


def wave_merged_lines(payload: dict, merged: dict) -> list[str]:
    """The per-merge message. ``merged`` carries the PR this event is about."""
    wave = merged.get("wave")
    what = f"Wave {wave} merged" if wave else "a wave merged"
    lines = [
        f"🌊 **Go Migration** — {what} · {merged.get('merged_on') or payload['today']}",
        _bar_line(payload),
        "",
        f"[{merged['title']} #{merged['number']}]({merged['url']}) merged with its parity gate green"
        + (f" · yeaboi-core is at {payload['core_version']}" if payload["core_version"] else "")
        + ".",
        "",
    ]
    next_wave = payload["next_wave"]
    url = payload.get("program_url")
    if next_wave and url:
        lines.append(f"Next: {next_wave['wave']}, {next_wave['contents']} — [the program of record]({url})")
    elif url:
        lines.append(f"That was the last wave — [the program of record]({url}) is complete.")
    else:
        lines.append(_footer(payload))
    return lines


def merged_pr_facts(number: int) -> dict | None:
    """The merged PR's title/url/wave, or None when it is not a merged wave PR.

    None for a merged *maintenance* PR under the same label — `_is_wave` is the
    gate, so the 🌊 announcement can never fire for a PR whose parity checks
    were skipped by design.
    """
    data = _pr(number)
    if not data or not data.get("merged"):
        return None
    labels = {label.get("name") for label in data.get("labels", []) if isinstance(label, dict)}
    if LABEL not in labels or not _is_wave(data):
        return None
    title = data.get("title", "")
    head = ((data.get("head") or {}).get("ref")) or ""
    match = _WAVE_TITLE.match(title) or re.match(rf"{re.escape(WAVE_BRANCH_PREFIX)}(?P<wave>\d+)", head)
    wave = int(match.group("wave")) if match else (6 if number == WAVE6_PR else None)
    return {
        "title": title,
        "number": number,
        "url": data.get("html_url", ""),
        "wave": wave,
        "merged_on": _day(data.get("merged_at")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--weekly", action="store_true", help="render the Tuesday progress message")
    mode.add_argument("--wave-merged", action="store_true", help="render the wave-merged announcement")
    parser.add_argument("--pr", type=int, help="the merged PR number (required with --wave-merged)")
    args = parser.parse_args(argv)

    payload = build_payload()
    if args.weekly:
        lines = weekly_lines(payload)
    else:
        if args.pr is None:
            print("--wave-merged needs --pr <n>", file=sys.stderr)
            return 2
        merged = merged_pr_facts(args.pr)
        if merged is None:
            # Not a merged wave PR — the routine's own filter should have caught
            # it; exiting non-zero keeps a wrong event from becoming a post.
            print(f"PR #{args.pr} is not a merged {LABEL} PR — nothing to announce", file=sys.stderr)
            return 1
        lines = wave_merged_lines(payload, merged)
    print(json.dumps({"payload": payload, "lines": lines}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
