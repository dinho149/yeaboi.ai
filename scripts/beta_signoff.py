#!/usr/bin/env python3
"""The human half of the release channel: what to test, and then ship it.

Everything a machine can decide about a release is already decided by the time an
rc reaches PyPI — `make test`, `make lint`, `make parity` and `make web-check` all
ran, an independent reviewer read the diff, and `pr-feedback` held the merge until
every finding was answered. What is left is the part no gate covers: somebody
installing the actual wheel and using it. This module makes that a two-command
ritual instead of an open-ended one.

    make beta-check      what is installable, what changed, what to exercise
    make beta-promote    turn it into the official X.Y.Z

`beta-check` also *records* the sign-off, as a `<!-- tested: beta/X.Y.ZrcN -->`
comment on the promotion ask. Two things read it back. `publish.yml` cuts the
final from that exact commit, so what shipped is what was tested rather than
whatever `main` happened to be at the moment of the ✅. And next week's ask uses
it as the floor of the batch, so a skipped week asks for the delta rather than
re-presenting work already signed off — which is the difference between a review
that stays bounded and one that grows until it gets skimmed.

The arithmetic all belongs to `release_channel.py` and the checklist to
`release_surfaces.py`; this file owns the terminal output and the two GitHub
writes. `--add-label release:promote` is imported from `cowork_relay` rather than
spelled again, because the Slack ✅ and this command must be the same action —
two spellings of it is how they drift apart, and the one that drifts is the one
nobody is watching.

Stdlib only, like its neighbours.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cowork_relay  # noqa: E402
import release_channel as channel  # noqa: E402
import release_surfaces  # noqa: E402

PROMOTION_LABEL = cowork_relay.PROMOTION_LABEL

# Written by this module, read by `publish.yml` and by next Monday's ask. Its own
# marker rather than a reuse of `<!-- beta: … -->`: the ask says which pre-release
# it is *about*, this says which one a human actually ran, and those differ the
# moment somebody signs off mid-week against a newer rc.
TESTED_RE = re.compile(r"<!--\s*tested:\s*(beta/\d+\.\d+\.\d+rc\d+)\s*-->")
BETA_MARKER_RE = re.compile(r"<!--\s*beta:\s*(beta/\d+\.\d+\.\d+rc\d+)\s*-->")

# The per-track marker, and it is shaped **deliberately not to match** either
# `TESTED_RE` above or `publish.yml`'s grep, which both require ` -->` directly
# after the digits. That is the mechanism rather than an accident:
#
#   `<!-- tested: beta/X.Y.ZrcN track=maintenance -->`  one session, this file only
#   `<!-- tested: beta/X.Y.ZrcN -->`                    every required track, and the
#                                                       only marker publish.yml reads
#
# So `publish.yml` needs no edit at all, and an existing bare marker keeps meaning
# exactly what it meant when somebody wrote it — "I ran this build and signed the
# whole thing off" — which under the split is precisely the completion marker.
TRACK_TESTED_RE = re.compile(r"<!--\s*tested:\s*(beta/\d+\.\d+\.\d+rc\d+)\s+track=([a-z][a-z-]*)\s*-->")

BAR = "─" * 72


class SignoffError(RuntimeError):
    """Something the human has to resolve before the ritual can continue."""


def _gh(*args: str) -> str | None:
    """Run `gh` and return stdout, or None if it is missing or refuses.

    None is never fatal here. `beta-check` is a reporting command first: with no
    `gh` on the machine it still prints the batch and the checklist off git alone,
    and only the marker and the label need an answer. Failing the whole command
    because the queue is unreachable would hide the information the human came for.
    """
    try:
        result = subprocess.run(["gh", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    return result.stdout if result.returncode == 0 else None


def _json(*args: str) -> object | None:
    payload = _gh(*args)
    if not payload:
        return None
    try:
        return json.loads(payload)
    except ValueError:
        return None


def recent_asks(limit: int = 5) -> list[dict]:
    """The promotion asks, newest first — open and closed.

    Closed ones matter: the routine supersedes an unanswered ask by closing it, so
    the sign-off marker from the week somebody tested but did not promote is on an
    issue that is no longer open.
    """
    data = _json(
        "issue",
        "list",
        "--label",
        PROMOTION_LABEL,
        "--state",
        "all",
        "--limit",
        str(limit),
        "--json",
        "number,state,body,title,url",
    )
    return data if isinstance(data, list) else []


def open_ask(asks: list[dict]) -> dict | None:
    return next((ask for ask in asks if str(ask.get("state", "")).upper() == "OPEN"), None)


def _comment_bodies(issue: int) -> list[str]:
    data = _json("issue", "view", str(issue), "--json", "comments")
    comments = data.get("comments") if isinstance(data, dict) else None
    return [str(entry.get("body", "")) for entry in comments] if isinstance(comments, list) else []


def newest_tested(asks: list[dict]) -> str | None:
    """The most recent ``beta/…`` tag anyone has signed off on, or None.

    Ordered by the tag, not by the comment date. Two people signing off on the
    same batch from different machines is not a conflict — the newer *pre-release*
    is the one that supersedes, and comment timestamps would let a late sign-off on
    an older rc silently narrow the next batch past work nobody looked at.
    """
    found: list[dict] = []
    for ask in asks:
        for body in _comment_bodies(int(ask["number"])):
            for match in TESTED_RE.finditer(body):
                entry = channel.resolve_beta(match.group(1))
                if entry is not None:
                    found.append(entry)
    if not found:
        return None
    return max(found, key=lambda entry: channel.prerelease_key(entry["version"]))["tag"]


def track_floors(asks: list[dict]) -> dict[str, str]:
    """The newest pre-release each track has been signed off on, by track.

    Ordered by ``prerelease_key`` and not by comment date, for the same reason
    ``newest_tested`` is: the newer *pre-release* supersedes, and a late sign-off
    on an older rc must not narrow the next batch past work nobody looked at.

    **A bare marker seeds every track.** It was written when there was one session,
    and "I tested this build" is a statement about the whole build. Reading it as
    maintenance-only would strand promotion behind an integration sign-off nobody
    was ever asked for; reading it as neither would lose the floor entirely.
    """
    floors: dict[str, list[dict]] = {track: [] for track in release_surfaces.TRACKS}
    for ask in asks:
        for body in _comment_bodies(int(ask["number"])):
            for match in TRACK_TESTED_RE.finditer(body):
                entry = channel.resolve_beta(match.group(1))
                if entry is not None and match.group(2) in floors:
                    floors[match.group(2)].append(entry)
            # A bare marker is one every track carries. `TRACK_TESTED_RE` cannot
            # match it and `TESTED_RE` cannot match a tracked one, so scanning
            # both never double-counts.
            for match in TESTED_RE.finditer(body):
                entry = channel.resolve_beta(match.group(1))
                if entry is not None:
                    for found in floors.values():
                        found.append(entry)
    return {
        track: max(found, key=lambda entry: channel.prerelease_key(entry["version"]))["tag"]
        for track, found in floors.items()
        if found
    }


def required_tracks(batch: dict) -> set[str]:
    """The tracks this batch actually needs a human to sit down with.

    A track with nothing in it is **not** required. A week with no campaign must
    not block promotion on a session whose checklist is empty — an empty checklist
    reads as "signed off" when it means "never asked", and that is the one way this
    split could ship something untested while looking complete.
    """
    tracks = batch.get("tracks")
    if not isinstance(tracks, dict):
        # A pre-split batch dict: one session, and the old bare marker covers it.
        return set()
    return {name for name in release_surfaces.TRACKS if (tracks.get(name) or {}).get("required")}


def ask_beta(ask: dict | None) -> str | None:
    """The pre-release an ask is about, from its `<!-- beta: … -->` marker."""
    if not ask:
        return None
    match = BETA_MARKER_RE.search(str(ask.get("body") or ""))
    return match.group(1) if match else None


def _checklist_lines(batch: dict, floors: dict[str, str]) -> list[str]:
    """One shared baseline, then one section per required track.

    The baseline is above both and not inside each. Repeated, it gets done twice;
    in one section only, the other track can be signed without anyone installing
    the wheel — and the wheel is the artefact users actually get.
    """
    providers = tuple(((batch.get("tracks") or {}).get("integration") or {}).get("providers") or ())
    baseline, per_track = release_surfaces.tracked_checklists(batch["changed_paths"], providers)
    lines = [release_surfaces.render(baseline, markdown=False), ""]
    required = required_tracks(batch)
    for track in release_surfaces.TRACKS:
        items = per_track.get(track) or []
        if not items:
            continue
        named = ", ".join(providers) if track == "integration" and providers else None
        head = f"  ── {track.upper()}" + (f": {named}" if named else "")
        signed = floors.get(track)
        if signed and channel.prerelease_key(signed.split("/", 1)[-1]) >= _tag_key(batch["installable_tag"]):
            head += "   ✓ signed off"
        lines += [head + " " + "─" * max(0, 68 - len(head)), "", release_surfaces.render(items, markdown=False), ""]
    if not required:
        lines.append("  nothing in this batch needs a hand-test beyond the baseline.")
    return lines


def _tag_key(tag: str | None) -> tuple[int, ...]:
    """A ``beta/X.Y.ZrcN`` tag as a comparable key; the zero tuple when absent."""
    return channel.prerelease_key(tag.split("/", 1)[-1]) if tag else (0, 0, 0, 0)


def _report(batch: dict, ask: dict | None, tested: str | None, floors: dict[str, str] | None = None) -> str:
    # `or {}` rather than trusting the default: `_checklist_lines` reads `floors`
    # unconditionally, so the signature as written advertises a call that raises.
    floors = floors or {}
    lines = [BAR]
    if not batch["promotable"]:
        lines += [
            f"  nothing pending — pyproject is at {batch['target']}, which is already released",
            f"  as {batch['last_final']}. The next release-worthy merge starts a new batch.",
            BAR,
        ]
        return "\n".join(lines)

    if batch["installable"]:
        lines.append(f"  install   pip install --pre yeaboi=={batch['installable']}")
    else:
        lines.append("  install   nothing published yet — no pre-release exists for this batch")
    span = batch["since"] or batch["last_final"] or "the beginning of the project"
    lines += [
        f"  batch     {batch['target']} · {batch['commits_since']} commits since {span}",
        f"  ask       {ask['url'] if ask else 'none open — the Monday routine opens it'}",
    ]
    if tested and batch["since"]:
        lines.append(f"  delta     you last signed off on {tested}; below is only what is new since")
    lines.append(BAR)

    if batch["entries"]:
        lines.append("")
        for entry in batch["entries"]:
            lines.append(f"  {entry.get('version', '?')} — {entry.get('summary', '')}".rstrip(" —"))
            for highlight in entry.get("highlights") or []:
                areas = ", ".join(highlight.get("areas") or [])
                lines.append(f"      · {highlight.get('text', '')}{f'  [{areas}]' if areas else ''}")

    if batch.get("nothing_new"):
        lines += [
            "",
            f"  Nothing new published since you signed off on {batch['since']} —",
            "  the build above is the one you already tested. Nothing to re-check.",
        ]
    else:
        lines += ["", "  TEST THIS WEEK", ""]
        lines += _checklist_lines(batch, floors)

    untested = batch["untested_commits"]
    if untested and batch["installable"]:
        lines += [
            "",
            f"  NOT IN THIS RELEASE — {len(untested)} commits are on main but in no pre-release.",
            "  Promotion is pinned to the tag above, so they ride the next one.",
            *[f"    - {line}" for line in untested],
        ]
    outstanding = sorted(required_tracks(batch) - _covered(batch, floors or {}))
    if outstanding:
        lines += [
            "",
            BAR,
            *[f"  next      make beta-sign-{track}" for track in outstanding],
            "  then      make beta-promote      (or ✅ the Slack ask)",
            BAR,
        ]
    else:
        lines += ["", BAR, "  next      make beta-promote      (or ✅ the Slack ask)", BAR]
    return "\n".join(lines)


def _covered(batch: dict, floors: dict[str, str]) -> set[str]:
    """Which tracks are signed off at or above this batch's installable tag."""
    target = _tag_key(batch.get("installable_tag"))
    return {track for track, tag in floors.items() if _tag_key(tag) >= target}


def mark_tested(issue: int, tag: str, track: str | None = None) -> list[str]:
    """The argv that records a sign-off. Literal, like every other write here.

    ``track`` stays the third, defaulted parameter so every existing two-argument
    call keeps writing the bare completion marker `publish.yml` greps for.
    """
    if track is None:
        body = f"Signed off on `{tag}` — tested from a real install.\n\n<!-- tested: {tag} -->"
    else:
        body = (
            f"Signed off on the **{track}** half of `{tag}` — tested from a real install."
            f"\n\n<!-- tested: {tag} track={track} -->"
        )
    return ["gh", "issue", "comment", str(issue), "--body", body]


def _already_marked(issue: int, tag: str, track: str | None = None) -> bool:
    bodies = _comment_bodies(issue)
    if track is None:
        return any(match.group(1) == tag for body in bodies for match in TESTED_RE.finditer(body))
    return any(
        match.group(1) == tag and match.group(2) == track for body in bodies for match in TRACK_TESTED_RE.finditer(body)
    )


def check(args: argparse.Namespace) -> int:
    """Report only. Recording moved to `sign`, because there are two sessions now.

    ``beta-check`` used to end by writing the sign-off, which was right when
    printing the checklist and running it were one act. With two tracks it is not:
    a command that printed both sections and silently signed both would be a
    command that signs off work nobody ran.
    """
    asks = recent_asks()
    floors = track_floors(asks)
    tested = newest_tested(asks)
    batch = channel.pending(since=tested)
    if args.json:
        payload = {
            **batch,
            "tested": tested,
            "floors": floors,
            "required": sorted(required_tracks(batch)),
            "outstanding": sorted(required_tracks(batch) - _covered(batch, floors)),
            "ask": (open_ask(asks) or {}).get("number"),
        }
        print(json.dumps(payload, indent=2))
        return 0
    print(_report(batch, open_ask(asks), tested, floors))
    return 0


def sign(args: argparse.Namespace) -> int:
    """Record one track's sign-off, and the completion marker when it is the last.

    Two comments, deliberately, and only the second one `publish.yml` can see. The
    per-track marker is shaped not to match its grep, so a half-signed batch cannot
    be promoted by a workflow that only ever learned to read one marker.
    """
    track = args.track
    if track not in release_surfaces.TRACKS:
        print(f"[beta] unknown track {track!r} — one of: {', '.join(release_surfaces.TRACKS)}", file=sys.stderr)
        return 2

    asks = recent_asks()
    batch = channel.pending(since=newest_tested(asks))
    if not batch["promotable"]:
        print(f"[beta] nothing to sign off — {batch['target']} is already released as {batch['last_final']}.")
        return 1

    required = required_tracks(batch)
    if track not in required:
        # Not an error. It is the honest answer to "sign off the integration half"
        # in a week with no campaign in it, and signing it anyway would record a
        # human's name against a checklist that was never printed.
        print(f"[beta] nothing {track} in this batch — no sign-off needed, and none recorded.")
        return 0

    ask = open_ask(asks)
    tag = batch["installable_tag"]
    if not tag:
        print("[beta] there is no published pre-release to sign off on yet.")
        return 1
    if not ask:
        print("[beta] no promotion ask is open. Monday's routine opens one, or promote by hand with:")
        print(f"       gh workflow run publish.yml -f version={batch['target']}")
        return 1

    issue = int(ask["number"])
    if _already_marked(issue, tag, track):
        print(f"[beta] {track} was already signed off on {tag} (#{issue}) — nothing re-recorded.")
    elif _gh(*mark_tested(issue, tag, track)[1:]) is None:
        print(f"[beta] could not record the {track} sign-off on #{issue} — gh is missing or refused.")
        return 2
    else:
        print(f"[beta] recorded the {track} half of {tag} as signed off on #{issue}.")

    # The track just recorded counts, without asking GitHub to confirm a comment
    # written one line ago. Re-reading would be slower and less correct: a comment
    # can lag its own read, and a stale answer here withholds the completion marker
    # on a batch that is in fact fully signed — which reads as "half tested" and is
    # the wrong way for this to be wrong.
    floors = {**track_floors(asks), track: tag}
    outstanding = sorted(required - _covered(batch, floors))
    if outstanding:
        print(f"[beta] still outstanding: {', '.join(outstanding)} — run make beta-sign-{outstanding[0]}.")
        return 0
    if _already_marked(issue, tag):
        print(f"[beta] {tag} was already complete — publish.yml already has its marker.")
        return 0
    if _gh(*mark_tested(issue, tag)[1:]) is None:
        print(f"[beta] every track is signed, but the completion marker could not be written to #{issue}.")
        return 2
    print(f"[beta] every required track is signed — {tag} is ready to promote.")
    return 0


def promote(args: argparse.Namespace) -> int:
    asks = recent_asks()
    ask = open_ask(asks)
    batch = channel.pending(since=newest_tested(asks))
    if not batch["promotable"]:
        print(f"[beta] nothing to promote — {batch['target']} is already released as {batch['last_final']}.")
        return 1
    if not ask:
        print("[beta] no promotion ask is open. Either wait for Monday's routine, or run:")
        print(f"       gh workflow run publish.yml -f version={batch['target']}")
        print("       (that promotes main's HEAD, not a pinned pre-release)")
        return 1

    # `track_floors` reads every ask's comments, so it is only worth asking when
    # the answer can change the outcome. A batch with no required track — a quiet
    # week, or a dict from before the split — has nothing to be outstanding.
    required = required_tracks(batch)
    outstanding = sorted(required - _covered(batch, track_floors(asks))) if required else []
    if outstanding and not args.yes:
        print(f"[beta] not promoted — {', '.join(outstanding)} has not been signed off on this batch.")
        for track in outstanding:
            print(f"       run: make beta-sign-{track}")
        print("       (--yes overrides, and records that you chose to)")
        return 1

    tag = ask_beta(ask) or batch["installable_tag"]
    print(f"  promoting {batch['target']} from {tag or 'main HEAD (no pre-release tag)'}")
    if outstanding:
        print(f"  ⚠ promoting with {', '.join(outstanding)} unsigned, because --yes was passed")
    print(f"  ask       {ask['url']}")
    untested = batch["untested_commits"]
    if untested:
        print(f"  ⚠ {len(untested)} commits on main are NOT in this release:")
        for line in untested:
            print(f"      - {line}")
    if not args.yes:
        try:
            answer = input("  cut the official release? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "y":
            print("[beta] not promoted.")
            return 1

    # The same argv the Slack ✅ produces. Imported rather than respelled: the two
    # paths approving the same release must be one action, and `_command` is where
    # the repo already decided that `--add-label` (adds) is the only acceptable
    # spelling of it — `gh api -X PUT .../labels` replaces, and once wiped an
    # issue's whole label set.
    argv = cowork_relay._command("promote", int(ask["number"]))
    if _gh(*argv[1:]) is None:
        print("[beta] could not apply release:promote — gh is missing or refused. Run:")
        print("       " + " ".join(argv))
        return 2
    print(f"[beta] labelled #{ask['number']} release:promote — publish.yml takes it from here.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The weekly beta sign-off")
    sub = parser.add_subparsers(dest="command", required=True)

    checker = sub.add_parser("check", help="what is installable, what changed, what to test")
    # Kept, and now a no-op: `check` never records. Accepted rather than removed so
    # a script or a habit that passes it keeps working instead of erroring.
    checker.add_argument("--no-mark", action="store_true", help="deprecated no-op — check never records")
    checker.add_argument("--json", action="store_true", help="the batch as JSON")
    checker.set_defaults(func=check)

    signer = sub.add_parser("sign", help="record one track's sign-off on the open ask")
    signer.add_argument("track", help=f"one of: {', '.join(release_surfaces.TRACKS)}")
    signer.set_defaults(func=sign)

    promoter = sub.add_parser("promote", help="turn the tested pre-release into the official X.Y.Z")
    promoter.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    promoter.set_defaults(func=promote)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (channel.ReleaseChannelError, SignoffError) as error:
        print(f"[beta] {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
