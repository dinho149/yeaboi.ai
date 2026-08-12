#!/usr/bin/env python3
"""The release channel's arithmetic: which pre-release this commit is, and what is in it.

Merging to `main` no longer ships to users. `publish-beta.yml` publishes a PyPI
*pre-release* (``X.Y.ZrcN``) on every release-worthy merge, and those accumulate
until a human promotes the batch; `publish.yml` then publishes the plain
``X.Y.Z``. Both need the same three facts — the last final tag, the version
`pyproject.toml` is heading for, and what sits between them — and none of the
three is a judgement call. Deriving them here rather than in a prompt keeps the
routine in ``cowork/routines/cron/release-promote-ask.md`` out of the arithmetic,
the same argument ``cowork_relay.py`` makes about diffing by eye.

Two invariants everything below rests on:

**``v*`` tags are finals only.** Pre-releases are deliberately untagged, which is
what makes "the last final tag" a reliable anchor — for the rc number, for the
promotion manifest, and for ``release-published-announce.md``'s
``git log <previous-tag>..<tag>``.

**A published pre-release is tagged, an unpublished one is not.** ``beta/X.Y.ZrcN``
is pushed by `publish-beta.yml` *after* the PyPI upload succeeds, so the tag is
proof the version exists and its commit is the tree that produced it. Without it
"the latest pre-release" could only ever be ``next_prerelease(HEAD)`` — what the
next merge *would* be numbered, which every non-bumping commit inflates past
anything actually on PyPI. That number belongs in a workflow deciding what to
upload; it does not belong in a ``pip install --pre`` line handed to a human.
``installable`` is the one that does.

**An rc string is never committed.** ``scripts/bump_version.py`` rejects anything
that is not ``X.Y.Z`` and ``auto-version.yml`` runs it on every release-worthy
PR, so an rc on ``main`` would break the next PR and the one after with no
obvious cause. ``--write`` exists for one caller: the publish job, stamping a
checkout that is thrown away with the runner.

Stdlib only, deliberately — CI runs this before ``uv sync``.

    python3 scripts/release_channel.py --next-rc        # 3.6.0rc12
    python3 scripts/release_channel.py --manifest --markdown
    python3 scripts/release_channel.py --manifest --json
    python3 scripts/release_channel.py --manifest --since beta/3.6.0rc4
    python3 scripts/release_channel.py --published        # what is really on PyPI
    python3 scripts/release_channel.py --write --version 3.6.0rc12
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_surfaces  # noqa: E402
from bump_version import read_current, write_version  # noqa: E402
from release_surfaces import checklist  # noqa: E402
from release_surfaces import render as render_checklist  # noqa: E402

# `vX.Y.Z` and nothing else. A `v3.6.0rc4` tag would poison every count below,
# which is why the beta workflow creates no tags at all.
FINAL_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# The pre-release namespace, deliberately NOT `v*`: `last_final_tag` globs `v*` and
# `release-published-announce.md` walks the same namespace, so a `v3.6.0rc4` would
# poison both. `beta/` is inert to every existing reader.
BETA_TAG_RE = re.compile(r"^beta/(\d+)\.(\d+)\.(\d+)rc(\d+)$")
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# What `--write` will put in pyproject.toml: the plain version, or that version
# with an rc suffix. Anything else is a bug in the caller, not input to trust.
STAMP_RE = re.compile(r"^\d+\.\d+\.\d+(?:rc\d+)?$")

CHANGELOG = ROOT / "src" / "yeaboi" / "changelog_data.json"


class ReleaseChannelError(RuntimeError):
    """Something that must stop a publish rather than be worked around."""


def _git(*args: str) -> str:
    """Run git against this repository, and only this one.

    The inherited ``GIT_*`` environment is stripped rather than trusted. Git
    exports ``GIT_DIR`` and ``GIT_INDEX_FILE`` into every child process, so
    ``cwd=ROOT`` alone does not pin which repository a subprocess talks about:
    called from inside a hook or a rebase, this would count commits and read tags
    from whatever repository invoked it. Every number below — the rc, the batch —
    would then describe the wrong tree while looking entirely ordinary.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False, env=env)
    if result.returncode != 0:
        raise ReleaseChannelError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _triple(version: str) -> tuple[int, int, int]:
    match = SEMVER_RE.match(version)
    if match is None:
        raise ReleaseChannelError(f"expected an X.Y.Z version, got {version!r}")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def last_final_tag() -> tuple[str, tuple[int, int, int]] | None:
    """The newest ``vX.Y.Z`` tag, or None before the first release.

    Sorted numerically on the parsed triple rather than lexically: `sort -V` is
    not portable here and string order puts `v3.9.0` above `v3.10.0`.
    """
    found: list[tuple[tuple[int, int, int], str]] = []
    for line in _git("tag", "--list", "v*").splitlines():
        match = FINAL_TAG_RE.match(line.strip())
        if match is not None:
            found.append(((int(match.group(1)), int(match.group(2)), int(match.group(3))), line.strip()))
    if not found:
        return None
    version, name = max(found)
    return name, version


def prerelease_key(version: str) -> tuple[int, int, int, int]:
    """``3.9.0rc7`` -> ``(3, 9, 0, 7)`` — the only correct way to order pre-releases.

    String order puts `rc10` below `rc9`, and "the newest pre-release" is what
    every caller of this module actually wants.
    """
    match = BETA_TAG_RE.match(f"beta/{version.removeprefix('beta/')}")
    if match is None:
        raise ReleaseChannelError(f"expected an X.Y.ZrcN pre-release, got {version!r}")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4)))


def published_prereleases() -> list[dict]:
    """Every ``beta/*`` tag, newest first — the pre-releases that really exist.

    `publish-beta.yml` pushes the tag only after `gh-action-pypi-publish` returns,
    so a tag here means a file on PyPI. That is the whole point: everything else in
    this module derives a version from `pyproject.toml` and a commit count, which
    answers "what would the next upload be called" and not "what can somebody
    install right now". Those two diverge the moment a docs merge lands, and the
    second question is the one a human asking to be told what to test is asking.

    Sorted on ``(triple, rc)`` rather than on the string, for the same reason
    `last_final_tag` parses: `rc10` sorts below `rc9` lexically, and the newest
    entry is the one every caller reads.
    """
    found: list[tuple[tuple[int, int, int, int], dict]] = []
    for line in _git("tag", "--list", "beta/*").splitlines():
        match = BETA_TAG_RE.match(line.strip())
        if match is None:
            continue
        tag = line.strip()
        found.append(
            (
                prerelease_key(tag),
                {
                    "version": tag.removeprefix("beta/"),
                    "tag": tag,
                    "sha": _git("rev-list", "-n", "1", tag),
                    "date": _git("log", "-1", "--format=%cs", tag),
                },
            )
        )
    return [entry for _, entry in sorted(found, key=lambda item: item[0], reverse=True)]


def resolve_beta(name: str) -> dict | None:
    """A ``beta/X.Y.ZrcN`` tag or a bare ``X.Y.ZrcN``, resolved against what exists.

    Callers get this string out of an issue marker, which is data from a public
    repository — so it is looked up in the list rather than interpolated into a
    git command. An unknown name is None, never a guess.
    """
    wanted = name.strip()
    if wanted.startswith("beta/"):
        wanted = wanted.removeprefix("beta/")
    return next((entry for entry in published_prereleases() if entry["version"] == wanted), None)


def next_prerelease(ref: str = "HEAD") -> str | None:
    """``X.Y.ZrcN`` where N is the commit count since the last final tag.

    None when ``pyproject.toml`` still holds the version that was last released —
    the ordinary steady state between a promotion and the next bump, where there
    is simply nothing to pre-release. That is a fact, not a fault:
    ``publish-beta.yml`` already gates on the version line having moved, and the
    promotion ask uses it to decide whether to stay silent.

    The number is a pure function of the commit, which is what makes it safe in
    the two situations that actually happen. Two merges racing: each commit has a
    strictly larger reachable set, so they cannot collide and the later one is
    higher. A workflow re-run: same commit, same N, same string, and
    ``skip-existing`` turns the duplicate upload into a green no-op rather than a
    second release of the same code under a new number.
    """
    base = read_current()
    base_triple = _triple(base)  # raises if an rc string ever reached `main`
    tag = last_final_tag()
    if tag is None:
        # Before the first `v*` tag there is nothing to count from, and every
        # commit is "since the beginning" — rc1 until a real release anchors it.
        return f"{base}rc1"
    name, version = tag
    if base_triple == version:
        return None
    if base_triple < version:
        # The dual-PR race, landed: two PRs branched off the same base picked
        # different levels and the lower one merged second. An rc under this base
        # would sort below a version already on PyPI, and a version that goes
        # backwards is worse than a workflow that stops and says so.
        raise ReleaseChannelError(
            f"pyproject version {base} is below the last final {name} — "
            f"fix the bump before publishing (two PRs likely bumped from the same base)"
        )
    count = int(_git("rev-list", "--count", f"{name}..{ref}"))
    return f"{base}rc{max(count, 1)}"


def _changelog_entries() -> list[dict]:
    try:
        data = json.loads(CHANGELOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else []


def check_promotable(ref: str = "HEAD") -> str:
    """The version safe to promote, or raise saying why it is not.

    ``next_prerelease`` refuses a version that sorts below the last final tag,
    but only the *beta* path calls it — so without this the promotion path had a
    weaker check than the pre-release path it promotes. `publish.yml` only
    refused an exactly-matching tag, which the dual-PR race walks straight past:
    ``main`` at 1.9.0 with ``v2.0.0`` already released has no ``v1.9.0`` tag, so
    the final uploaded to PyPI would sort *below* one already published, and PyPI
    has no delete.
    """
    version = read_current()
    triple = _triple(version)
    tag = last_final_tag()
    if tag is not None:
        name, released = tag
        if triple == released:
            raise ReleaseChannelError(f"{name} is already released — main has not been bumped since")
        if triple < released:
            raise ReleaseChannelError(
                f"pyproject version {version} is below the last final {name} — "
                f"promoting it would publish a version that sorts backwards"
            )
    return version


# The corroborating half of the track split. `cron/integrations-campaign.md` titles
# every campaign PR `integration(<provider>): <angle>`, and a squash merge keeps the
# PR title as the commit subject — which is the one part of a merge commit that
# survives every squash setting, every rebase and GitHub's own trailer block.
#
# Deliberately NOT a commit trailer, which was the first design and does not work
# here: git's trailer parser reads only the last paragraph, and `auto-version.yml`
# pushes a bump commit onto every release-worthy PR, so GitHub's `Co-authored-by:`
# block below a `---------` separator hides any trailer of ours on exactly the PRs
# that produce an rc.
#
# And it is only corroborating. The primary signal is the changed paths, so a
# forgotten prefix costs a redundant checklist row rather than a wrong release —
# which is the right way for this to fail.
INTEGRATION_SUBJECT_RE = re.compile(r"^[0-9a-f]+\s+integration\(([a-z0-9][a-z0-9_-]*)\)")


def providers_from_subjects(commits: list[str]) -> tuple[str, ...]:
    """Providers named by `integration(<provider>):` in a list of `--oneline` commits."""
    return tuple(sorted({match.group(1) for line in commits if (match := INTEGRATION_SUBJECT_RE.match(line))}))


def _base_triple(prerelease: str) -> tuple[int, int, int]:
    """The ``X.Y.Z`` a pre-release was cut from — ``3.9.0rc7`` -> ``(3, 9, 0)``."""
    return _triple(prerelease.split("rc", 1)[0])


def _tracks(commits: list[str], changed: list[str]) -> dict:
    """The two hand-test sessions, as data. Additive: every other key is untouched.

    The split is of the CHECKLIST, not of the commits — see `release_surfaces.TRACKS`.
    Both signals feed it, and they are combined so that a miss costs an extra test
    session and never a missed one:

    * paths (primary)   — `tools/<provider>.py` in the diff;
    * subjects (backup) — `integration(<provider>):`, which is the only way a reach
      angle shows up at all, since it touches no provider module.

    ``required`` is what `beta_signoff.promote` refuses on, and it is False for a
    track with nothing in it. A week with no campaign must not block promotion on a
    test session whose checklist is empty, because an empty checklist reads as
    "signed off" when it means "never asked".

    ``carried_forward`` is conservative on purpose: a track carries only when
    *both* signals agree nothing of its shape landed since its own floor. Either
    one says otherwise and the human re-signs.
    """
    providers = tuple(sorted(set(release_surfaces.campaign_providers(changed)) | set(providers_from_subjects(commits))))
    _, per_track = release_surfaces.tracked_checklists(changed, providers)
    integration_commits = [line for line in commits if INTEGRATION_SUBJECT_RE.match(line)]
    # Everything not attributed to the campaign is maintenance. That is also where
    # an attribution *failure* lands, which is why the split may only ever add a
    # checklist row and never remove one.
    maintenance_commits = [line for line in commits if not INTEGRATION_SUBJECT_RE.match(line)]

    return {
        "maintenance": {
            "commits": maintenance_commits,
            "items": len(per_track["maintenance"]),
            "required": bool(per_track["maintenance"]),
        },
        "integration": {
            "commits": integration_commits,
            "items": len([item for item in per_track["integration"] if item.reached]),
            "required": bool(per_track["integration"]),
            "providers": list(providers),
        },
    }


def _is_ancestor(sha: str, ref: str) -> bool:
    """Whether ``sha`` is at or below ``ref`` — i.e. already released."""
    try:
        _git("merge-base", "--is-ancestor", sha, ref)
    except ReleaseChannelError:
        return False
    return True


def pending(ref: str = "HEAD", since: str | None = None) -> dict:
    """Everything between the last final release and ``ref`` — the promotion batch.

    Changelog entries are the primary source: ``auto-version.yml`` already writes
    one per bump, in user-facing prose, area-tagged. ``git log`` is the backstop
    for anything that shipped without an entry.

    ``since`` narrows all of that to one pre-release tag, which is the skipped-week
    case: a batch nobody signed off on grows every merge, and re-reading it whole
    every Monday is how a reviewer starts skimming. The full span is still
    reachable — the caller renders the already-seen part collapsed rather than
    dropping it — but the question a delta answers is "what is new since I last
    looked", and that is the only question with a bounded amount of work behind it.
    """
    tag = last_final_tag()
    name, version = tag if tag is not None else (None, (0, 0, 0))

    # Only the pre-releases above the last final belong to this batch. Older
    # `beta/*` tags are history: they describe versions already promoted.
    published = [entry for entry in published_prereleases() if _base_triple(entry["version"]) > version]
    installable = published[0] if published else None

    # The floor of the span: an explicit `since` when the caller has one, the last
    # final otherwise. `resolve_beta` returns None for a tag that does not exist,
    # and falling back to the full batch is the right answer there — a marker
    # naming a deleted or misspelled tag must widen the review, never narrow it.
    seen = resolve_beta(since) if since else None
    # A `tested:` marker naming an ALREADY-PROMOTED pre-release would otherwise set
    # the floor below the last final and widen the span past the release boundary.
    # `resolve_beta` searches the unfiltered tag list, and `newest_tested` scans
    # closed asks — including the one that was promoted, which carries exactly such
    # a marker. Mostly masked while promotion pinned to the newest rc; per-track
    # floors unmask it, because a quiet track keeps a stale marker as its newest.
    if seen is not None and name and _is_ancestor(seen["sha"], name):
        seen = None
    floor = seen["sha"] if seen else name
    floor_version = _base_triple(seen["version"]) if seen else version
    span = f"{floor}..{ref}" if floor else ref

    entries = []
    for entry in _changelog_entries():
        match = SEMVER_RE.match(str(entry.get("version", "")))
        if match is not None and _triple(str(entry["version"])) > floor_version:
            entries.append(entry)

    commits = [line for line in _git("log", span, "--oneline", "--no-merges").splitlines() if line]
    target = read_current()
    prerelease = next_prerelease(ref)

    # What a promotion would actually ship. Promotion is pinned to a published
    # pre-release, so the diff that matters ends at that tag and not at `ref` —
    # measuring to HEAD would put paths in the checklist that are not in the
    # release, which is worse than missing one because it spends the reviewer's
    # attention on something they cannot affect.
    head = installable["sha"] if installable else ref
    changed = [line for line in _git("diff", "--name-only", f"{floor}..{head}" if floor else head).splitlines() if line]

    # On `main`, in no pre-release at all. Ordinarily empty or docs-only; when it
    # is not, it is the set of commits a pinned promotion deliberately leaves
    # behind, and saying so is the whole reason promotion is pinned.
    untested = (
        [line for line in _git("log", f"{installable['sha']}..{ref}", "--oneline", "--no-merges").splitlines() if line]
        if installable
        else list(commits)
    )

    # The subject signal has to measure the same span as the path signal, or the
    # two disagree about what a promotion contains. `commits` runs to HEAD;
    # `changed` stops at the pinned tag. An `integration(gitlab):` PR merged after
    # the newest rc would otherwise mark the integration track `required` — and
    # `beta_signoff.promote` then refuses a batch for an angle that is not in it,
    # with no way to sign the track off short of cutting another rc.
    # `untested` is the newest slice of `commits` by construction, so dropping it
    # leaves exactly the commits the pinned tree holds, with no second git call.
    shipped = commits[len(untested) :] if installable else commits

    return {
        "target": target,
        "tracks": _tracks(shipped, changed),
        "last_final": name,
        "last_final_date": _git("log", "-1", "--format=%cs", name) if name else None,
        "commits_since": int(_git("rev-list", "--count", span)),
        "entries": entries,  # newest first, straight out of changelog_data.json
        "commits": commits,
        "changed_paths": changed,
        "latest_prerelease": prerelease,
        # What exists on PyPI, as opposed to what the next merge would be called.
        # Every `pip install --pre` line anywhere must come from here.
        "published": published,
        "installable": installable["version"] if installable else None,
        "installable_tag": installable["tag"] if installable else None,
        "installable_sha": installable["sha"] if installable else None,
        "untested_commits": untested,
        "since": seen["tag"] if seen else None,
        "delta": seen is not None,
        # The signed-off pre-release IS the newest published one. Everything after
        # it is on `main` and in nothing installable, so there is no new *release*
        # to review — the checklist would be empty and the install line would name
        # the build they already ran. Distinct from "nothing pending": a promotion
        # is still available and still worth making, it just needs no re-testing.
        "nothing_new": bool(seen and installable and seen["tag"] == installable["tag"]),
        # Whether there is anything to promote — driven by the version, not by the
        # commit count. Docs and CI merges move `main` without moving the version
        # line, and promoting then would only re-tag a version already released.
        # This is the "exit silently" signal, and it must never be confused with a
        # git failure, which raises instead.
        "promotable": prerelease is not None,
    }


def _checklist_markdown(batch: dict) -> list[str]:
    """The sign-off checklist, as one shared baseline plus one section per track.

    Read through ``.get`` throughout: a batch dict built before the split (a
    cached JSON, an older caller) still renders, as the single flat checklist it
    always was, rather than raising on a missing key.

    The baseline is above both sections rather than inside each. Repeated it gets
    done twice; in one section only, the other track could be signed off by
    somebody who never installed the wheel.
    """
    paths = batch.get("changed_paths") or []
    tracks = batch.get("tracks")
    if not isinstance(tracks, dict):
        return [render_checklist(checklist(paths), markdown=True), ""]

    providers = tuple((tracks.get("integration") or {}).get("providers") or ())
    baseline, per_track = release_surfaces.tracked_checklists(paths, providers)
    # No heading on the baseline: it sits directly under the section's own
    # `### Before you ✅` and the install line, which is what it belongs to.
    lines = [render_checklist(baseline, markdown=True), ""]
    for track in release_surfaces.TRACKS:
        items = per_track.get(track) or []
        if not items:
            continue
        named = ", ".join(providers) if track == "integration" and providers else None
        heading = f"**{track.capitalize()}**" + (f" — {named}" if named else "")
        lines += [heading, "", render_checklist(items, markdown=True), ""]
    return lines


def markdown(batch: dict, *, asking: bool = True) -> str:
    """The batch, rendered. One renderer, two audiences.

    ``asking=True`` is the promotion issue body and the text the Slack ask quotes:
    it opens with the question, carries the sign-off checklist, and closes with the
    ✅/❌ verbs and the two markers `publish.yml` reads — `<!-- promote: X.Y.Z -->`
    for what was asked and `<!-- beta: beta/X.Y.ZrcN -->` for which commit to cut
    it from.

    ``asking=False`` is the **released** notes, and the difference is not
    cosmetic. `publish.yml` publishes this as the GitHub Release body, which is
    the most public artefact the channel produces and the text
    `release-published-announce.md` reads back. Leading a shipped release with
    "Promote 3.7.0?" and closing it with an invitation to react to a decision made
    an hour ago on a now-closed issue reads as a draft nobody finished — and it
    would scatter copies of the promote marker, which the promotion path trusts,
    across public pages. A checklist of things to test before shipping is wrong
    there for a plainer reason: it already shipped. The changelog half is
    identical either way.
    """
    target, last = batch["target"], batch["last_final"]
    if batch.get("since"):
        since = f"since `{batch['since']}`, the pre-release you last signed off on"
        age = ""
    else:
        since = f"since `{last}`" if last else "since the beginning of the project"
        age = f" ({batch['last_final_date']})" if batch["last_final_date"] else ""
    head = (
        f"**Promote `{target}`?**  {batch['commits_since']} commits {since}{age}."
        if asking
        else f"{batch['commits_since']} commits {since}{age}."
    )
    lines = [head, ""]
    for entry in batch["entries"]:
        lines.append(f"**{entry.get('version', '?')}** — {entry.get('summary', '')}".rstrip(" —"))
        for highlight in entry.get("highlights") or []:
            areas = ", ".join(highlight.get("areas") or [])
            suffix = f"  `[{areas}]`" if areas else ""
            lines.append(f"  · {highlight.get('text', '')}{suffix}")
        lines.append("")
    if not batch["entries"]:
        lines += ["_No changelog entries — nothing user-facing was recorded for this batch._", ""]
    lines += [
        f"<details><summary>{len(batch['commits'])} commits</summary>",
        "",
        *[f"- {line}" for line in batch["commits"]],
        "",
        "</details>",
        "",
    ]
    if not asking:
        return "\n".join(lines)

    lines += ["### Before you ✅", ""]
    if batch.get("nothing_new"):
        lines += [
            f"Nothing new has been published since you signed off on `{batch['since']}` — "
            f"the build below is the one you already tested, and promoting it needs no further checks.",
            "",
            f"`pip install --pre yeaboi=={batch['installable']}`",
            "",
        ]
    elif batch.get("installable"):
        lines += [f"Install what you are approving: `pip install --pre yeaboi=={batch['installable']}`", ""]
    else:
        # No tag means no upload. Saying "nothing published" is the honest form;
        # printing `next_prerelease` here is what used to hand out an install
        # command that 404s.
        lines += ["_Nothing has been published for this batch yet — there is no pre-release to install._", ""]
    if not batch.get("nothing_new"):
        lines += _checklist_markdown(batch)

    untested = batch.get("untested_commits") or []
    if untested and batch.get("installable"):
        lines += [
            f"<details><summary>⚠ {len(untested)} commits are on `main` but in no pre-release — "
            f"they are <b>not</b> in this release</summary>",
            "",
            *[f"- {line}" for line in untested],
            "",
            "</details>",
            "",
        ]
    lines += [
        f"✅ to release `{target}` to PyPI + GitHub · ❌ to wait another week",
        "",
    ]
    if batch.get("installable_tag"):
        lines.append(f"<!-- beta: {batch['installable_tag']} -->")
    lines.append(f"<!-- promote: {target} -->")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-release numbering and the promotion batch")
    parser.add_argument("--next-rc", action="store_true", help="print the pre-release version for HEAD")
    parser.add_argument("--check-promotable", action="store_true", help="print the version safe to promote, or fail")
    parser.add_argument("--manifest", action="store_true", help="what is pending promotion")
    parser.add_argument("--markdown", action="store_true", help="render --manifest as the promotion ask body")
    parser.add_argument(
        "--release-notes", action="store_true", help="render --manifest as published release notes (no ask)"
    )
    parser.add_argument("--json", action="store_true", help="render --manifest as JSON")
    parser.add_argument("--published", action="store_true", help="list the pre-releases really on PyPI, newest first")
    parser.add_argument("--since", help="narrow --manifest to what landed after this beta/X.Y.ZrcN tag")
    parser.add_argument("--write", action="store_true", help="stamp --version into pyproject.toml")
    parser.add_argument("--version", help="the version --write stamps")
    parser.add_argument("--ref", default="HEAD", help="the commit to measure from (default HEAD)")
    args = parser.parse_args(argv)

    try:
        if args.write:
            if not args.version:
                parser.error("--write needs --version")
            if not STAMP_RE.match(args.version):
                raise ReleaseChannelError(f"refusing to stamp {args.version!r} — expected X.Y.Z or X.Y.ZrcN")
            write_version(args.version)
            print(args.version)
            return 0
        if args.check_promotable:
            print(check_promotable(args.ref))
            return 0
        if args.next_rc:
            version = next_prerelease(args.ref)
            if version is None:
                print(
                    "[release-channel] pyproject.toml still holds the last released version — nothing to pre-release",
                    file=sys.stderr,
                )
                return 1
            print(version)
            return 0
        if args.published:
            print(json.dumps(published_prereleases(), indent=2))
            return 0
        if args.manifest:
            batch = pending(args.ref, since=args.since)
            if args.markdown or args.release_notes:
                print(markdown(batch, asking=not args.release_notes))
            else:
                print(json.dumps(batch, indent=2))
            return 0
    except ReleaseChannelError as error:
        print(f"[release-channel] {error}", file=sys.stderr)
        return 2

    parser.error("pass one of --next-rc, --check-promotable, --manifest, --published, --write")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
