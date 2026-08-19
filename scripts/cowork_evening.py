#!/usr/bin/env python3
"""Render the evening Slack posts — one per area that moved today.

``cron/shipped-standup.md`` runs this and posts each ``posts[].lines`` block
verbatim, one channel message each. That is ``--agenda``'s contract and
``migration_progress.py``'s — rendered, not composed — and it exists because a
model retyping a number is a failure the fleet has already been bitten by.

**What changed, and why.** The evening post used to be one message grouped by
*type*: a fix in ``analysis/`` arrived as a ``[bug]`` line between a go-migration
wave and a platform chore, and the twelve areas with no other voice in the
channel had no voice at all. So the grouping is now the ``workstream:`` label,
and each area gets a message headed by its glyph from ``cowork/README.md``'s
table. Everything else about the shape is unchanged — the same sections, the
same ``[type]`` tag, the same dividers, the same install footer.

**Rendered here, not by the routine, for one specific reason beyond the usual.**
A fan-out means N messages instead of one, and a model deciding *how many* and
*which* is a model that can post the same area twice or drop one silently. The
count is arithmetic over labels, so it lives where a test can hold it.

Three rules the caller depends on:

- **A post fires on a change, never on a state.** Something merged today, a PR
  opened today, or a PR newly crossed into stuck. A PR quietly building for three
  days re-announcing itself nightly is how a channel gets muted — but it still
  appears under 🔨 **Building** inside a post that fired for another reason.
- **Nothing about proposals.** Those are decisions and belong to ``cron/digest.md``;
  ``sweep-procedure.md`` states the split and it is what keeps 🗳️ readable.
- **Never a fact that was not read.** A review verdict, a merge time, a regression
  run: absent from the API means absent from the clause, never guessed.

**stdlib only**, like ``_gh_transport`` and ``migration_progress`` — a routine
session runs this in a checkout with no environment built beyond ``uv run``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# scripts/ is not a package, so the siblings are imported by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _gh_transport as transport  # noqa: E402
import cowork_setup as setup  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# `ci-sentinel.yml` opens unattended fix PRs for a red `main` and labels them
# only `ci-sentinel`. A `cowork`-only filter would drop exactly the merges nobody
# watched, which are the ones worth reporting.
LANE_LABELS = ("cowork", "ci-sentinel")

# Open longer than this, or red, and it is stuck rather than building. The number
# is `shipped-standup.md`'s and is unchanged by the fan-out.
# 14, not 7: under the batch release model a gate-green fleet PR routinely
# waits up to a week for `make batch-assemble` — that is the design, not a
# stall (`cowork/sweep-procedure.md` step 2). Fourteen days means it missed a
# batch, usually a conflict skip, and that IS worth calling stuck.
STUCK_DAYS = 14

# The area a PR carrying no `workstream:` label is reported under. It is reported,
# never dropped: an untagged PR is a convention miss somebody should see, and the
# fleet's own upkeep is the closest thing to an owner.
UNCLAIMED_AREA = "fleet"

# The routine that runs this. It is on the 18:00 agenda and checks in *after*
# posting, so it can never appear in `--checked-in` and `no_shows()` excludes it
# by name. There is no general rule to derive this from: every other routine on
# the agenda has finished by the time it is asked about.
SELF_ROUTINE = "shipped-standup"

# A `type:bug` PR's admission ticket is a regression test that fails before the
# fix and passes after (`sweep-procedure.md`). The body carries both runs. This
# looks for the claim rather than parsing pytest output — the clause it produces
# says a regression test is *there*, which is what the reader is checking.
_REGRESSION = re.compile(r"regression test|fails? before|failed before", re.I)


def _get(path: str) -> object | None:
    """One REST GET through whichever transport this machine has.

    `gh` when it is there (a developer's auth lives in the CLI), the token
    otherwise (a routine session has a token and no CLI). Never `gh pr list
    --json`: that is GraphQL, and the routine-session egress proxy refuses it.
    None on any failure; the caller renders blindness, never a guess.
    """
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


def _repo_path(suffix: str) -> str | None:
    slug = transport.resolve_slug(REPO_ROOT)
    if not slug or "/" not in slug:
        return None
    owner, name = slug.split("/", 1)
    return f"/repos/{transport.segment(owner)}/{transport.segment(name)}{suffix}"


def _labels(item: dict) -> set[str]:
    return {label["name"] for label in item.get("labels", []) if isinstance(label, dict) and "name" in label}


def in_lane(item: dict) -> bool:
    return bool(_labels(item) & set(LANE_LABELS))


def workstream_of(item: dict) -> str:
    """The area a PR belongs to, or "" — the same join `cowork_metrics.py` uses."""
    for name in sorted(_labels(item)):
        if name.startswith("workstream:"):
            return name.split(":", 1)[1]
    return ""


def type_of(item: dict) -> str:
    for name in sorted(_labels(item)):
        if name.startswith("type:"):
            return name.split(":", 1)[1]
    return ""


def _moment(stamp: str | None) -> datetime | None:
    """An ISO stamp as an aware datetime, or None.

    A naive stamp is read as UTC rather than left naive. GitHub always sends the
    ``Z``, but ``--since`` is typed by a human, and a naive one used to parse
    cleanly here and then raise ``TypeError`` on the first comparison — killing
    the run for a missing letter.
    """
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _clock(stamp: str | None, zone: object | None) -> str:
    """``14:02`` in the display zone, or "" — never a guessed time."""
    moment = _moment(stamp)
    if moment is None:
        return ""
    local = moment.astimezone(zone) if zone is not None else moment
    return f"{local:%H:%M}"


def _day_label(now: datetime, zone: object | None) -> str:
    """``Tue 11 Aug`` — the shape every other fleet message dates itself with."""
    local = now.astimezone(zone) if zone is not None else now
    return f"{local:%a %-d %b}"


# How far back the closed-PR walk will page before it gives up. Five pages is 500
# PRs; the window is one day and this repo merges single digits a day, so hitting
# this means something is wrong rather than large.
MAX_PAGES = 5


def fetch_open() -> list[dict] | None:
    """Every open PR, or None when the read failed or overflowed.

    None is never an empty list. A page reported empty when it could not be asked
    renders as a quiet day, which is the one thing this post must not invent — and
    a full page is provably not the whole answer, so it is blindness too.
    """
    path = _repo_path("/pulls?state=open&per_page=100")
    if path is None:
        return None
    data = _get(path)
    if not isinstance(data, list) or len(data) >= 100:
        return None
    return [item for item in data if isinstance(item, dict)]


def fetch_closed_since(since: datetime) -> list[dict] | None:
    """Every closed PR touched since `since`, or None when the read failed.

    ``/pulls`` rather than ``/search/issues``: search takes ``merged:>=`` server
    side and would be one call, and it is **not on the probed allowlist** a cloud
    routine session's egress proxy enforces (``tests/fixtures/
    cowork_github_access_live.json``). ``GET /repos/{slug}/pulls`` is, so the
    filtering happens here.

    Sorted by ``updated`` descending, so the walk stops at the window edge rather
    than paging the repo's whole history. That is safe in the one direction that
    matters: a PR's ``updated_at`` is never earlier than its ``merged_at``, so a
    merge inside the window cannot be sitting behind one that is outside it.
    """
    collected: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        path = _repo_path(f"/pulls?state=closed&sort=updated&direction=desc&per_page=100&page={page}")
        if path is None:
            return None
        data = _get(path)
        if not isinstance(data, list):
            return None
        items = [item for item in data if isinstance(item, dict)]
        collected.extend(items)
        # `data`, not `items`: the page-is-short test asks GitHub how many rows it
        # sent, and one malformed entry on a full page would otherwise end the
        # walk as though it were the last one.
        if len(data) < 100:
            return collected
        oldest = _moment(items[-1].get("updated_at"))
        if oldest is not None and oldest < since:
            return collected
    # Ran out of pages with the window still open. Reported as blindness rather
    # than returned short: a truncated list becomes "nothing else merged", which
    # is the guess this module exists to refuse.
    return None


def review_verdict(number: int) -> tuple[str, datetime | None]:
    """``review clean`` when the API says so, "" when it does not — and when.

    Never "review pending" or any other invented state: an absent fact is an
    omitted clause, which is honest, and an invented one costs the whole post its
    credibility.

    The timestamp is the second half because a verdict is a *change*, and the
    firing rule (see ``collect``) needs to know whether that change happened
    inside the window. It is the moment of the surviving ``CHANGES_REQUESTED``,
    and None for every other verdict — nothing fires on an approval.
    """
    path = _repo_path(f"/pulls/{number}/reviews")
    if path is None:
        return "", None
    data = _get(path)
    if not isinstance(data, list):
        return "", None
    # Last verdict per reviewer — an APPROVED after a CHANGES_REQUESTED is a
    # resolved round, and counting both would report a clean PR as contested.
    latest: dict[str, tuple[str, datetime | None]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        state = item.get("state") or ""
        if state not in ("APPROVED", "CHANGES_REQUESTED"):
            continue
        who = ((item.get("user") or {}).get("login")) or ""
        latest[who] = (state, _moment(item.get("submitted_at")))
    blocked = [when for state, when in latest.values() if state == "CHANGES_REQUESTED"]
    if blocked:
        # The most recent block is the one the PR is sitting behind.
        stamps = [when for when in blocked if when is not None]
        return "changes requested", max(stamps) if stamps else None
    approved = any(state == "APPROVED" for state, _ in latest.values())
    return ("review clean" if approved else ""), None


def ci_state(sha: str) -> str:
    """``ci red`` for a definite failure, ``ci green`` for a definite success, "".

    ``GET /repos/{slug}/commits/{ref}/status`` is on the probed allowlist
    (``tests/fixtures/cowork_github_access_live.json``), which is why this reads
    the combined *status* rather than ``/check-runs``.

    **A zero-status or pending commit reports nothing**, and that asymmetry is the
    whole safety of the call. The combined API answers ``pending`` both for a run
    in flight and for a commit that has no statuses at all, and this repo's
    Actions report as check runs — so treating ``pending`` as a fact would mark
    every open PR stuck on a signal that means "I was not told".
    """
    if not sha:
        return ""
    path = _repo_path(f"/commits/{sha}/status")
    if path is None:
        return ""
    data = _get(path)
    if not isinstance(data, dict) or not data.get("statuses"):
        return ""
    state = data.get("state") or ""
    if state in ("failure", "error"):
        return "ci red"
    return "ci green" if state == "success" else ""


def _head_sha(pr: dict) -> str:
    head = pr.get("head")
    return (head.get("sha") or "") if isinstance(head, dict) else ""


def merged_clause(pr: dict, zone: object | None) -> str:
    """The trace behind one merge: what proved it, the verdict, the time.

    Every clause is read or omitted. `shipped-standup.md` has said so since it was
    written — "a missing clause is honest and an invented one is the whole
    message's credibility" — and the fan-out changes nothing about that.
    """
    bits: list[str] = []
    if type_of(pr) == "bug" and _REGRESSION.search(pr.get("body") or ""):
        bits.append("regression test added, failed before and passes after")
    checks = ci_state(_head_sha(pr))
    if checks:
        bits.append(checks)
    verdict, _blocked_at = review_verdict(pr.get("number") or 0)
    if verdict:
        bits.append(verdict)
    clock = _clock(pr.get("merged_at"), zone)
    if clock:
        bits.append(f"merged {clock}")
    return " · ".join(bits)


def open_clause(pr: dict, now: datetime, zone: object | None) -> tuple[str, bool, datetime | None]:
    """One open PR's clause, whether it is stuck, and when it became so.

    Three ways to be stuck, and they are `shipped-standup.md`'s original three:
    open past ``STUCK_DAYS``, red checks, or a standing ``changes requested``.
    The red one came back after a round-trip through the fan-out — the renderer
    read only ``/pulls/{n}/reviews`` at first, so a PR red since the hour it
    opened reported as *building*, while the README went on promising the trace.

    The third value is the moment it crossed, or None when the crossing has no
    timestamp (age and red checks both cross silently). ``collect`` fires on it.
    """
    opened = _moment(pr.get("created_at"))
    age = (now - opened).days if opened else 0
    verdict, blocked_at = review_verdict(pr.get("number") or 0)
    checks = ci_state(_head_sha(pr))
    stuck = age >= STUCK_DAYS or checks == "ci red" or verdict == "changes requested"
    if opened and opened.astimezone(zone or UTC).date() == now.astimezone(zone or UTC).date():
        when = "opened today"
    elif age <= 0:
        when = "open"
    else:
        when = f"opened {age} day{'s' if age != 1 else ''} ago"
    bits = [when]
    if checks:
        bits.append(checks)
    if verdict:
        bits.append(verdict)
    elif age >= STUCK_DAYS:
        bits.append(f"no review verdict in {age} days")
    return " · ".join(bits), stuck, blocked_at


def collect(since: datetime, now: datetime, zone: object | None) -> dict:
    """Every area's day, keyed by workstream. Blindness is reported, not smoothed."""
    warnings: list[str] = []
    closed = fetch_closed_since(since)
    opened = fetch_open()
    if closed is None:
        warnings.append("could not read merged PRs — this post undercounts, do not trust it as a full day")
        closed = []
    if opened is None:
        warnings.append("could not read open PRs — Building and Stuck are missing from every area below")
        opened = []

    areas: dict[str, dict] = {}

    def area(name: str) -> dict:
        return areas.setdefault(name or UNCLAIMED_AREA, {"merged": [], "building": [], "stuck": [], "fires": False})

    for pr in closed:
        merged_at = _moment(pr.get("merged_at"))
        if merged_at is None or merged_at < since or not in_lane(pr):
            continue
        bucket = area(workstream_of(pr))
        bucket["merged"].append(
            {
                "number": pr.get("number"),
                "title": pr.get("title") or "",
                "url": pr.get("html_url") or "",
                "type": type_of(pr),
                "untagged": not workstream_of(pr),
                "clause": merged_clause(pr, zone),
            }
        )
        bucket["fires"] = True

    for pr in opened:
        if not in_lane(pr):
            continue
        bucket = area(workstream_of(pr))
        clause, stuck, blocked_at = open_clause(pr, now, zone)
        entry = {
            "number": pr.get("number"),
            "title": pr.get("title") or "",
            "url": pr.get("html_url") or "",
            "untagged": not workstream_of(pr),
            "clause": clause,
        }
        bucket["stuck" if stuck else "building"].append(entry)
        # A change fires a post; a state does not. Opened today is a change. So is
        # crossing into stuck — measured by the same STUCK_DAYS boundary, so it
        # fires on the day it crosses and not on the six after it.
        #
        # A `changes requested` verdict is the third crossing and it has its own
        # clock: a PR blocked on day 2 is stuck on day 2, and dating that
        # crossing by age would leave it unannounced until day 7 — or for ever,
        # if it merged first. So the review's own timestamp is what places it.
        opened_at = _moment(pr.get("created_at"))
        if opened_at and opened_at >= since:
            bucket["fires"] = True
        elif stuck and opened_at and (now - opened_at).days == STUCK_DAYS:
            bucket["fires"] = True
        elif stuck and blocked_at and blocked_at >= since:
            bucket["fires"] = True

    return {"areas": areas, "warnings": warnings}


# `9108e9aa fix what the review found (#272)` — the squash subject GitHub writes.
_MERGED_PR = re.compile(r"\(#(\d+)\)")


def _installable() -> tuple[str, str, frozenset[int] | None]:
    """The tag-backed pre-release, a warning, and what that rc does **not** hold.

    Never ``latest_prerelease``: that is what the *next* release-worthy merge
    would be numbered, raised by every docs merge including ones that publish
    nothing, so quoting it in an install command hands out a 404.

    **An unreadable channel is a warning, not an empty string.** ``pending()``
    shells out to git and can fail for reasons that have nothing to do with the
    batch, and "" renders as *no new pre-release* — a claim about the world. This
    module's contract is that blindness is reported and never smoothed, and it
    applies to its own imports.

    The third value is the PR numbers in ``untested_commits`` — everything on
    ``main`` that landed *after* the published tag. **A merge being in the day is
    not the same as it being in the rc**, and the common case is the one that
    diverges: ``publish-beta.yml`` skips the publish when a merge did not move
    the version, and `chore` and `docs` are two of the four types a sweep can
    produce. An area whose only merge today was a chore was being handed
    ``→ `3.9.0rc14``` and an install line for a build that predates it — a fact
    inferred rather than read, which is the one thing this module refuses.

    ``None`` means the batch could not be read, and the caller claims nothing
    rather than guessing in either direction.
    """
    try:
        import release_channel
    except Exception as exc:
        return (
            "",
            f"could not read the release channel ({exc.__class__.__name__}) — the version clause is missing, not empty",
            None,
        )
    try:
        batch = release_channel.pending()
    except Exception as exc:
        return (
            "",
            f"could not read the release channel ({exc.__class__.__name__}) — the version clause is missing, not empty",
            None,
        )
    if not isinstance(batch, dict):
        return (
            "",
            "the release channel answered in an unexpected shape — the version clause is missing, not empty",
            None,
        )
    untested = batch.get("untested_commits")
    if not isinstance(untested, list):
        return batch.get("installable") or "", "", None
    numbers = {int(match.group(1)) for line in untested if isinstance(line, str) for match in _MERGED_PR.finditer(line)}
    return batch.get("installable") or "", "", frozenset(numbers)


def _title(
    glyph: str, display: str, day: str, merged: list, installable: str, channel_known: bool = True, in_rc: bool = True
) -> str:
    """``🔬 **Analysis** — Sun 16 Aug · 2 merged → `3.9.0rc14```.

    The dating fact is what tells two posts under the same glyph apart — 🧭 at
    06:15 about other agents' output and 🧭 in the evening about this area's
    merges are both about agents, and the clause is the difference.

    **Four states, not two**, and only one of them names a version:

    - the channel could not be read → no clause at all, because "" would render
      as *no new pre-release*, a claim about the world made out of a failure;
    - nothing was published for this batch → *no new pre-release*, which is read
      rather than inferred;
    - something was published but it does not contain **this area's** merges → no
      clause, because a chore or docs merge publishes no rc and the newest tag
      predates it;
    - the published rc holds one of this area's merges → the version.
    """
    head = f"{glyph} **{display}** — {day}"
    if not merged:
        return f"{head} · nothing merged"
    head += f" · {len(merged)} merged"
    if not channel_known:
        return head
    # `installable` is empty when nothing has been published for this batch. Say
    # so rather than printing a stale version, which is `shipped-standup.md`'s
    # rule and the reason the field is read at all.
    if not installable:
        return head + " → no new pre-release"
    return head + (f" → `{installable}`" if in_rc else "")


def _in_rc(data: dict, not_in_rc: frozenset[int] | None) -> bool:
    """Whether this area has a merge the published pre-release actually holds.

    The gate on the version clause and the install footer. ``None`` — the batch
    could not be read — claims nothing, which is the same direction every other
    unreadable source takes here.

    An area with no merges is False by construction, which is the case the footer
    was gated on before: a post that fired because a PR *opened* has contributed
    nothing to any build. A chore-only merge is the case that was missing, and it
    is the more common of the two.
    """
    if not_in_rc is None:
        return False
    numbers = {item.get("number") for item in data["merged"]}
    return bool(numbers - set(not_in_rc)) and bool(numbers)


def area_lines(
    display: str,
    glyph: str,
    day: str,
    data: dict,
    installable: str,
    channel_known: bool = True,
    in_rc: bool = True,
) -> list[str]:
    """One area's whole message. Pure over its inputs, like ``agenda_lines``.

    Sections are separated by a divider rather than a blank line, and the divider
    goes *between* them rather than after each: Slack renders `1.` items as a list
    block and eats the blank line that ends it, so a heading written after a list
    arrives glued to the final item. A divider is not blank, so it survives — and
    a trailing one with nothing after it is a rule under the last line, which is
    the shape a reader reads as "something is missing".
    """
    merged = data["merged"]
    blocks: list[list[str]] = []

    if merged:
        block: list[str] = []
        for position, item in enumerate(merged, start=1):
            tag = f"**[{item['type']}]** " if item["type"] else ""
            block.append(f"{position}. {tag}[{item['title']}]({item['url']})")
            clause = item["clause"]
            if item["untagged"]:
                clause = " · ".join(filter(None, [clause, "no `workstream:` label — untagged, not unowned"]))
            if clause:
                block.append(f"   — {clause}")
        blocks.append(block)

    # Empty sections are omitted heading and all. `🚧 **Stuck** (0)` is a line
    # whose only content is that it has none.
    for heading, key in (("🔨 **Building**", "building"), ("🚧 **Stuck**", "stuck")):
        items = data[key]
        if not items:
            continue
        block = [f"{heading} ({len(items)})", ""]
        for position, item in enumerate(items, start=1):
            block.append(f"{position}. [{item['title']}]({item['url']})")
            if item["clause"]:
                block.append(f"   — {item['clause']}")
        blocks.append(block)

    # Gated on `in_rc`, which is False for both ways an area can fail to be in a
    # build: a post that fired because a PR *opened*, and a chore or docs merge
    # the release channel never picked up. An install line under either
    # advertises a version this area is not in.
    if installable and merged and in_rc:
        blocks.append([f"`pip install --pre yeaboi=={installable}`"])

    lines = [_title(glyph, display, day, merged, installable, channel_known, in_rc), ""]
    for position, block in enumerate(blocks):
        if position:
            lines += ["───────────────────────────", ""]
        lines += block
    while lines and not lines[-1]:
        lines.pop()
    return lines


def health_lines(missing: list[dict], day: str) -> list[str]:
    """The one section a per-area post cannot carry: a no-show has no area.

    It is a channel message rather than a reply under 📅 because a thread reply
    does not notify, and this is the fault the fleet cannot report on itself — on
    2026-08-06 a sweep died after one turn and nothing said so for a week.
    """
    count = len(missing)
    lines = [
        f"🩺 **Fleet health** — {day} · {count} routine{'s' if count != 1 else ''} never ran",
        "",
    ]
    for position, item in enumerate(missing, start=1):
        lines.append(f"{position}. `{item.get('due', '')}` **{item.get('name', '')}** — due, never checked in")
    return lines


def build(since: datetime, now: datetime, checked_in: list[str] | None = None) -> dict:
    """The whole evening: the posts, the health message, and what was not read."""
    zone, zone_note = setup.display_zone()
    day = _day_label(now, zone)
    gathered = collect(since, now, zone)
    warnings = list(gathered["warnings"])
    if zone_note:
        warnings.append(zone_note)

    glyphs = setup.parse_workstream_glyphs()
    names = setup.parse_workstream_names()
    installable, channel_note, not_in_rc = _installable()
    if channel_note:
        warnings.append(channel_note)
    posts = []
    # Sorted by name so two runs over the same day post in the same order — a
    # dict's insertion order here is GitHub's page order, which is not stable.
    for name in sorted(gathered["areas"]):
        data = gathered["areas"][name]
        if not data["fires"]:
            continue
        glyph = glyphs.get(name)
        if not glyph:
            warnings.append(f"{name} has no area glyph in README.md — posting it would have no title emoji")
            continue
        display = names.get(name) or name.replace("-", " ").title()
        posts.append(
            {
                "workstream": name,
                "glyph": glyph,
                "lines": area_lines(display, glyph, day, data, installable, not channel_note, _in_rc(data, not_in_rc)),
            }
        )

    health = None
    if checked_in is not None:
        missing = no_shows(now, zone, checked_in)
        if missing:
            health = {"lines": health_lines(missing, day)}

    return {
        "payload": {"day": day, "since": since.isoformat(), "areas": gathered["areas"], "warnings": warnings},
        "posts": posts,
        "health": health,
    }


def no_shows(now: datetime, zone: object | None, checked_in: list[str]) -> list[dict]:
    """Routines due today, after 📅 went up, that left no check-in.

    The check-in names come from the routine (it reads the 📅 thread; a script
    cannot reach Slack). The schedule comes from ``--agenda``, so the two halves
    of the diff are the same two the reader sees.

    Anything due *before* 📅 is excluded rather than reported: `cd-deploy` at
    04:00 and every GitHub event have no thread to reply to and check in to their
    run log instead. Reporting those would put a false 🔴 here every morning.

    **And the caller excludes itself.** ``shipped-standup`` is on the 18:00
    agenda and its own check-in is the last thing it does — *after* this post
    goes up — so its name can never be in ``checked_in`` and it named itself as a
    no-show on every single firing. A 🩺 that is wrong every evening is a 🩺
    nobody reads, which is the one failure this section cannot survive: it is the
    fault the fleet cannot otherwise report on itself.
    """
    plan = setup.agenda(now.date())
    seen = {name.strip().lower() for name in checked_in if name.strip()}
    missing = []
    for entry in plan.get("today", []):
        name = entry.get("name") or ""
        if name.lower() in seen or name.lower() == SELF_ROUTINE:
            continue
        # `--agenda` carries a list per routine; a routine firing more than once
        # today is a no-show only if it never replied at all, so the first firing
        # is the one the line is dated by.
        utc = next(iter(entry.get("times_utc") or []), "")
        due = next(iter(entry.get("times_local") or []), "")
        # 📅 goes up at 05:45 UTC. Earlier is silent by design, later has not
        # happened yet — `slack-relay`'s window counts only up to now.
        if not _between(utc, "05:45", f"{now:%H:%M}"):
            continue
        missing.append({"name": name, "due": due or utc})
    return missing


def _between(utc: str, floor: str, ceiling: str) -> bool:
    """Whether a routine was due after 📅 and before now. Unparseable → excluded.

    Excluded rather than included: a 🔴 naming a routine that ran is how this
    section stops being read, and it is the only section here that names a fault.

    **All three are UTC**, and that is load-bearing rather than incidental. The
    schedule's UTC time was once compared against a *local* ceiling, which broke
    two ways at once: any ``DISPLAY_TZ`` past about +6 wraps ``now`` past midnight
    so the ceiling sorts below everything and 🩺 silently never fires, and
    ``cowork_setup._local()`` appends ``" (+1d)"`` to a time that lands on
    another date, which string-sorts below every bare ``HH:MM``. The local string
    is for the reader; the comparison is UTC on both sides.
    """
    if not utc:
        return False
    return floor < utc <= ceiling


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the evening per-area Slack posts.")
    parser.add_argument("--since", help="ISO timestamp of the last post; defaults to 24 hours ago")
    parser.add_argument(
        "--checked-in",
        help="comma-separated routine names that checked in under today's 📅 (enables the 🩺 message)",
    )
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    since = _moment(args.since) or (now - timedelta(hours=24))
    checked_in = args.checked_in.split(",") if args.checked_in is not None else None
    print(json.dumps(build(since, now, checked_in), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
