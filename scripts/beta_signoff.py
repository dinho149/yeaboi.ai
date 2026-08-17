#!/usr/bin/env python3
"""The human half of the release channel: test the batch, sign it, then merge it.

Everything a machine can decide about a release is already decided by the time a
batch PR exists — every constituent passed `make test`, `make lint`, an
independent review and the `pr-feedback` gate individually, and the batch PR's
own CI ran on the assembled tree. What is left is the part no gate covers:
somebody installing the actual wheel and using it. This module makes that a
short ritual instead of an open-ended one.

    make batch-assemble           build the batch branch + its PR (batch_assemble.py)
    make beta-check               what is in the batch, and what to exercise
    make beta-sign-<track>        record one track's sign-off on the batch PR
    make beta-promote             verify every track, mark ready, print the merge

The sign-off is recorded as `<!-- tested: <sha> -->` comments on the batch PR,
where `<sha>` is the PR's head at the moment of the sign-off. `promote` counts a
track only when its signed sha IS the current head: any commit after the
sign-off — a re-assembly, a late constituent — makes the signature stale, which
is the honest reading, because the tree it names is not the tree that would
merge.

**Nothing here merges.** The merge is the sign-off, and it is a human's:
`promote` verifies, prints the `gh pr merge --merge`
command, and stops. `publish.yml` then classifies the human's merge and cuts
the official release from exactly the merged tree.

The checklist belongs to `release_surfaces.py` and the batch to
`batch_assemble.py`; this file owns the terminal output and the marker
round-trip. Stdlib only, like its neighbours.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _gh_transport as transport  # noqa: E402
import batch_assemble  # noqa: E402
import release_lane  # noqa: E402
import release_surfaces  # noqa: E402

PROMOTION_LABEL = batch_assemble.PROMOTION_LABEL

# Written by `sign`, read back by `promote`. A full 40-hex sha, never an
# abbreviation: the marker names the exact tree a human ran, and a prefix that
# stops resolving uniquely is a signature that stops meaning anything.
TESTED_RE = re.compile(r"<!--\s*tested:\s*([0-9a-f]{40})\s*-->")

# The per-track marker, shaped **deliberately not to match** `TESTED_RE`, which
# requires ` -->` directly after the sha:
#
#   `<!-- tested: <sha> track=maintenance -->`  one session, one track
#   `<!-- tested: <sha> -->`                    every required track is signed
#
# So a half-signed batch cannot look complete to any reader that only ever
# learned the bare marker — the same mechanism the issue-era markers used, with
# the tag swapped for the sha.
TRACK_TESTED_RE = re.compile(r"<!--\s*tested:\s*([0-9a-f]{40})\s+track=([a-z][a-z-]*)\s*-->")

BAR = "─" * 72


class SignoffError(RuntimeError):
    """Something the human has to resolve before the ritual can continue."""


def _gh(*args: str) -> str | None:
    """Run `gh` and return stdout, or None if it is missing or refuses.

    None is never fatal here. `beta-check` is a reporting command first, and
    only the marker writes and the draft flip need an answer.

    Through `_gh_transport`'s process seam rather than `subprocess.run`
    directly: this function *writes* — `sign` comments markers on the batch PR —
    so a test that forgets to stub `_gh` must land in `tests/conftest.py`'s
    `_no_real_gh_calls` rather than on the real PR.
    """
    try:
        result = transport._run(["gh", *args], cwd=ROOT, capture_output=True, text=True, check=False)
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


def recent_batches(limit: int = 5) -> list[dict]:
    """The batch PRs, newest first — open, merged and closed alike."""
    data = _json(
        "pr",
        "list",
        "--label",
        PROMOTION_LABEL,
        "--state",
        "all",
        "--limit",
        str(limit),
        "--json",
        "number,state,body,title,url,headRefName,headRefOid,isDraft,labels",
    )
    return data if isinstance(data, list) else []


def open_batch(batches: list[dict]) -> dict | None:
    return next((batch for batch in batches if str(batch.get("state", "")).upper() == "OPEN"), None)


# Who may sign a batch off. `authorAssociation` as GitHub reports it on a
# comment: the three that mean write access to this repository, and nothing else.
SIGNERS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def _comment_bodies(number: int) -> list[str]:
    """Every comment on the batch PR that a maintainer wrote.

    **The filter is the authorization, and there is no other one.** A sign-off
    marker names the tree the human is about to merge and release, and the batch
    is an open PR on a public repository, so anybody at all can comment on it.
    The regex that reads the marker validates its *shape*; it never asks who
    said it. Without this, a stranger's `<!-- tested: <head-sha> -->` satisfies
    `promote` on a batch nobody ran.

    An unrecognised association reads as an outsider. That is the safe
    direction: the cost is a sign-off that has to be repeated by someone the API
    does recognise, and the other way round is a release nobody chose.
    """
    data = _json("pr", "view", str(number), "--json", "comments")
    comments = data.get("comments") if isinstance(data, dict) else None
    if not isinstance(comments, list):
        return []
    return [
        str(entry.get("body", "")) for entry in comments if str(entry.get("authorAssociation", "")).upper() in SIGNERS
    ]


def track_floors(number: int) -> dict[str, set[str]]:
    """Every sha each track has been signed off at, from the batch PR's comments.

    Sets rather than a newest-wins pick: with shas there is no total order to
    pick a winner by, and none is needed — `promote` asks one question, "is the
    *current head* among the signed shas?", and any older signature is stale by
    definition because the tree it names is not the tree that would merge.

    **A bare marker seeds every track**, exactly as the issue-era bare marker
    did: "I tested this build" is a statement about the whole build.
    """
    floors: dict[str, set[str]] = {track: set() for track in release_surfaces.TRACKS}
    for body in _comment_bodies(number):
        for match in TRACK_TESTED_RE.finditer(body):
            if match.group(2) in floors:
                floors[match.group(2)].add(match.group(1))
        # `TRACK_TESTED_RE` cannot match a bare marker and `TESTED_RE` cannot
        # match a tracked one, so scanning both never double-counts.
        for match in TESTED_RE.finditer(body):
            for signed in floors.values():
                signed.add(match.group(1))
    return floors


def changed_paths(batch: dict) -> list[str]:
    """The batch's paths, diffed `origin/main...<head>` after a fetch.

    Falls back to the PR's own file list when git cannot answer — a shallow or
    offline checkout must degrade to a coarser checklist, not to no checklist.
    """
    from batch_assemble import _git

    _git("fetch", "origin", "--quiet")
    diff = _git("diff", "--name-only", f"origin/main...{batch['headRefOid']}")
    if diff.returncode == 0:
        return [line for line in diff.stdout.splitlines() if line.strip()]
    data = _json("pr", "view", str(batch["number"]), "--json", "files")
    files = data.get("files") if isinstance(data, dict) else None
    if isinstance(files, list):
        return [str(entry.get("path", "")) for entry in files if entry.get("path")]
    return []


def providers_of(batch: dict, paths: list[str]) -> tuple[str, ...]:
    """The campaign providers in this batch: paths first, titles corroborating.

    The constituent lines in the batch body carry each PR's title, so an
    `integration(<provider>):` prefix survives into the batch the same way it
    used to survive into the commit subject.
    """
    named = set(release_surfaces.campaign_providers(paths))
    for match in batch_assemble.CONSTITUENT_RE.finditer(str(batch.get("body") or "")):
        prefixed = re.match(r"integration\(([a-z0-9][a-z0-9_-]*)\)", match.group("title"))
        if prefixed:
            named.add(prefixed.group(1))
    return tuple(sorted(named))


def batch_view(batch: dict) -> dict:
    """Everything the three commands ask about one batch, computed once."""
    paths = changed_paths(batch)
    providers = providers_of(batch, paths)
    baseline, per_track = release_surfaces.tracked_checklists(paths, providers)
    head = str(batch.get("headRefOid") or "")
    floors = track_floors(int(batch["number"]))
    required = {track for track, items in per_track.items() if items}
    covered = {track for track in required if head and head in floors.get(track, set())}
    return {
        "batch": batch,
        "head": head,
        "paths": paths,
        "providers": providers,
        "baseline": baseline,
        "per_track": per_track,
        "floors": floors,
        "required": required,
        "covered": covered,
        "outstanding": sorted(required - covered),
        "constituents": batch_assemble.constituents_of(str(batch.get("body") or "")),
    }


def assert_ships_human(batch: dict) -> None:
    """The batch PR must classify `human`, or the merge releases nothing.

    `batch_assemble.py` refuses to create one that would not; this re-check
    exists because a label added *after* creation — a well-meaning `cowork` on
    the batch — would flip the lane with nothing red until the release silently
    fails to happen.
    """
    labels = [entry.get("name") for entry in batch.get("labels") or [] if isinstance(entry, dict)]
    if release_lane.classify({"labels": labels, "head": str(batch.get("headRefName") or "")}) != release_lane.HUMAN:
        raise SignoffError(
            f"batch PR #{batch['number']} would classify as a fleet merge "
            f"(head {batch.get('headRefName')!r}, labels {labels!r}) — merging it would cut NO release. "
            "Remove the `cowork` label before promoting."
        )


def mark_tested(number: int, sha: str, track: str | None = None) -> list[str]:
    """The argv that records a sign-off. Literal, like every other write here.

    ``track`` stays the third, defaulted parameter: a two-argument call writes
    the bare completion marker, which only the last outstanding track earns.
    """
    if track is None:
        body = f"Signed off on `{sha[:8]}` — tested from a real install.\n\n<!-- tested: {sha} -->"
    else:
        body = (
            f"Signed off on the **{track}** half of `{sha[:8]}` — tested from a real install."
            f"\n\n<!-- tested: {sha} track={track} -->"
        )
    return ["gh", "pr", "comment", str(number), "--body", body]


def _already_marked(number: int, sha: str, track: str | None = None) -> bool:
    bodies = _comment_bodies(number)
    if track is None:
        return any(match.group(1) == sha for body in bodies for match in TESTED_RE.finditer(body))
    return any(
        match.group(1) == sha and match.group(2) == track for body in bodies for match in TRACK_TESTED_RE.finditer(body)
    )


def _no_batch() -> int:
    print("[beta] no batch PR is open — run `make batch-assemble` to build one from")
    print("       the waiting fleet PRs. A human PR still ships on its own merge.")
    return 1


def _checklist_lines(view: dict) -> list[str]:
    """One shared baseline, then one section per required track.

    The baseline is above both and not inside each: repeated, it gets done
    twice; in one section only, the other track could be signed by somebody who
    never installed the wheel — and the wheel is what users actually get.
    """
    lines = [release_surfaces.render(view["baseline"], markdown=False), ""]
    for track in release_surfaces.TRACKS:
        items = view["per_track"].get(track) or []
        if not items:
            continue
        named = ", ".join(view["providers"]) if track == "integration" and view["providers"] else None
        head = f"  ── {track.upper()}" + (f": {named}" if named else "")
        if track in view["covered"]:
            head += "   ✓ signed off"
        lines += [head + " " + "─" * max(0, 68 - len(head)), "", release_surfaces.render(items, markdown=False), ""]
    if not view["required"]:
        lines.append("  nothing in this batch needs a hand-test beyond the baseline.")
    return lines


def _report(view: dict) -> str:
    batch = view["batch"]
    lines = [
        BAR,
        f"  batch     {batch['title']}",
        f"  pr        {batch['url']}" + ("   (draft)" if batch.get("isDraft") else ""),
        f"  head      {view['head'][:8]} · {len(view['constituents'])} constituents",
        BAR,
        "",
        "  TEST THIS BATCH",
        "",
        *_checklist_lines(view),
    ]
    stale = {
        track: signed
        for track, signed in view["floors"].items()
        if signed and track in view["required"] and track not in view["covered"]
    }
    for track in sorted(stale):
        lines.append(f"  ⚠ {track} was signed at an older head — the batch moved, so it needs a re-run.")
    if view["outstanding"]:
        lines += [
            "",
            BAR,
            *[f"  next      make beta-sign-{track}" for track in view["outstanding"]],
            "  then      make beta-promote",
            BAR,
        ]
    else:
        lines += ["", BAR, "  next      make beta-promote", BAR]
    return "\n".join(lines)


def check(args: argparse.Namespace) -> int:
    """Report only. Recording is `sign`'s job, because there are two sessions.

    A command that printed both checklists and silently signed both would be a
    command that signs off work nobody ran.
    """
    batch = open_batch(recent_batches())
    if not batch:
        return _no_batch()
    view = batch_view(batch)
    if args.json:
        payload = {
            "number": batch["number"],
            "url": batch["url"],
            "head": view["head"],
            "constituents": view["constituents"],
            "changed_paths": view["paths"],
            "providers": list(view["providers"]),
            "required": sorted(view["required"]),
            "covered": sorted(view["covered"]),
            "outstanding": view["outstanding"],
        }
        print(json.dumps(payload, indent=2))
        return 0
    print(_report(view))
    return 0


def sign(args: argparse.Namespace) -> int:
    """Record one track's sign-off, and the completion marker when it is the last.

    Two comments, deliberately: the per-track marker is shaped not to match the
    bare one, so a half-signed batch cannot look complete to `promote` or to any
    future reader that only ever learned one marker.
    """
    track = args.track
    if track not in release_surfaces.TRACKS:
        print(f"[beta] unknown track {track!r} — one of: {', '.join(release_surfaces.TRACKS)}", file=sys.stderr)
        return 2

    batch = open_batch(recent_batches())
    if not batch:
        return _no_batch()
    view = batch_view(batch)
    if track not in view["required"]:
        # Not an error. It is the honest answer to "sign off the integration
        # half" of a batch with no campaign in it, and signing anyway would
        # record a human's name against a checklist that was never printed.
        print(f"[beta] nothing {track} in this batch — no sign-off needed, and none recorded.")
        return 0

    number, head = int(batch["number"]), view["head"]
    if not head:
        print("[beta] the batch PR reports no head commit — refusing to sign a tree it cannot name.")
        return 2
    if _already_marked(number, head, track):
        print(f"[beta] {track} was already signed off at {head[:8]} (#{number}) — nothing re-recorded.")
    elif _gh(*mark_tested(number, head, track)[1:]) is None:
        print(f"[beta] could not record the {track} sign-off on #{number} — gh is missing or refused.")
        return 2
    else:
        print(f"[beta] recorded the {track} half of {head[:8]} as signed off on #{number}.")

    # The track just recorded counts, without asking GitHub to confirm a comment
    # written one line ago: a comment can lag its own read, and a stale answer
    # here withholds the completion marker on a batch that is fully signed —
    # which reads as "half tested" and is the wrong way for this to be wrong.
    covered = view["covered"] | {track}
    outstanding = sorted(view["required"] - covered)
    if outstanding:
        print(f"[beta] still outstanding: {', '.join(outstanding)} — run make beta-sign-{outstanding[0]}.")
        return 0
    if _already_marked(number, head):
        print(f"[beta] {head[:8]} was already complete — the batch is ready to promote.")
        return 0
    if _gh(*mark_tested(number, head)[1:]) is None:
        print(f"[beta] every track is signed, but the completion marker could not be written to #{number}.")
        return 2
    print("[beta] every required track is signed — run make beta-promote.")
    return 0


def promote(args: argparse.Namespace) -> int:
    """Verify the batch is fully signed, flip it to ready, print the merge. STOP.

    The merge is a human's — the whole model rests on `publish.yml` seeing a
    human-lane push — so this command never runs it and never could: it holds no
    `gh pr merge` anywhere.
    """
    batch = open_batch(recent_batches())
    if not batch:
        return _no_batch()
    assert_ships_human(batch)
    view = batch_view(batch)

    if view["outstanding"] and not args.yes:
        print(f"[beta] not ready — {', '.join(view['outstanding'])} has not been signed off at {view['head'][:8]}.")
        for track in view["outstanding"]:
            print(f"       run: make beta-sign-{track}")
        print("       (--yes overrides, and records that you chose to)")
        return 1

    print(f"  batch     {batch['title']}")
    print(f"  pr        {batch['url']}")
    print(f"  head      {view['head'][:8]} · {len(view['constituents'])} constituents")
    if view["outstanding"]:
        print(f"  ⚠ promoting with {', '.join(view['outstanding'])} unsigned, because --yes was passed")
    if not args.yes:
        try:
            answer = input("  mark the batch ready to merge? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "y":
            print("[beta] not promoted.")
            return 1

    if batch.get("isDraft"):
        # `batch_assemble.py` opens the batch ready for exactly this reason, so a
        # draft here was opened by hand. Flip it — a draft cannot be merged at all
        # — but say what the flip costs: `claude-review.yml` skipped this head
        # while it was a draft, `pr-feedback` stops forgiving a missing verdict the
        # moment it is ready, and neither CI nor the review re-fires on
        # `ready_for_review`. Re-running the CI workflow is what produces the
        # verdict; pushing a commit would move the head sha and void every
        # signature above.
        if _gh("pr", "ready", str(batch["number"])) is None:
            print(f"[beta] could not mark #{batch['number']} ready — do it by hand: gh pr ready {batch['number']}")
        print("[beta] this batch was opened as a DRAFT, so Claude Review never reviewed this head.")
        print("       Re-run this PR's CI before merging, or pr-feedback will hold it with no verdict to find.")
        print("       `make batch-assemble` opens the batch ready precisely to avoid this.")
    print(BAR)
    print("  the batch is signed and ready. The merge is yours — nothing here merges:")
    print(f"      gh pr merge {batch['number']} --merge")
    print("  (--merge, never --squash: the release notes walk this history per item.)")
    print("  Your merge is the sign-off; publish.yml cuts the official release from it.")
    print(BAR)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The batch sign-off")
    sub = parser.add_subparsers(dest="command", required=True)

    checker = sub.add_parser("check", help="what is in the batch, and what to test")
    # Kept, and still a no-op: `check` never records. Accepted rather than
    # removed so a script or a habit that passes it keeps working.
    checker.add_argument("--no-mark", action="store_true", help="deprecated no-op — check never records")
    checker.add_argument("--json", action="store_true", help="the batch as JSON")
    checker.set_defaults(func=check)

    signer = sub.add_parser("sign", help="record one track's sign-off on the batch PR")
    signer.add_argument("track", help=f"one of: {', '.join(release_surfaces.TRACKS)}")
    signer.set_defaults(func=sign)

    promoter = sub.add_parser("promote", help="verify the sign-offs and print the merge command")
    promoter.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    promoter.set_defaults(func=promote)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SignoffError as error:
        print(f"[beta] {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
