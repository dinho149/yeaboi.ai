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

**An rc string is never committed.** ``scripts/bump_version.py`` rejects anything
that is not ``X.Y.Z`` and ``auto-version.yml`` runs it on every release-worthy
PR, so an rc on ``main`` would break the next PR and the one after with no
obvious cause. ``--write`` exists for one caller: the publish job, stamping a
checkout that is thrown away with the runner.

Stdlib only, deliberately — CI runs this before ``uv sync``.

    python3 scripts/release_channel.py --next-rc        # 3.6.0rc12
    python3 scripts/release_channel.py --manifest --markdown
    python3 scripts/release_channel.py --manifest --json
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

from bump_version import read_current, write_version  # noqa: E402

# `vX.Y.Z` and nothing else. A `v3.6.0rc4` tag would poison every count below,
# which is why the beta workflow creates no tags at all.
FINAL_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
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


def pending(ref: str = "HEAD") -> dict:
    """Everything between the last final release and ``ref`` — the promotion batch.

    Changelog entries are the primary source: ``auto-version.yml`` already writes
    one per bump, in user-facing prose, area-tagged. ``git log`` is the backstop
    for anything that shipped without an entry.
    """
    tag = last_final_tag()
    name, version = tag if tag is not None else (None, (0, 0, 0))
    span = f"{name}..{ref}" if name else ref

    entries = []
    for entry in _changelog_entries():
        match = SEMVER_RE.match(str(entry.get("version", "")))
        if match is not None and _triple(str(entry["version"])) > version:
            entries.append(entry)

    commits = [line for line in _git("log", span, "--oneline", "--no-merges").splitlines() if line]
    target = read_current()
    prerelease = next_prerelease(ref)
    return {
        "target": target,
        "last_final": name,
        "last_final_date": _git("log", "-1", "--format=%cs", name) if name else None,
        "commits_since": int(_git("rev-list", "--count", span)),
        "entries": entries,  # newest first, straight out of changelog_data.json
        "commits": commits,
        "latest_prerelease": prerelease,
        # Whether there is anything to promote — driven by the version, not by the
        # commit count. Docs and CI merges move `main` without moving the version
        # line, and promoting then would only re-tag a version already released.
        # This is the "exit silently" signal, and it must never be confused with a
        # git failure, which raises instead.
        "promotable": prerelease is not None,
    }


def markdown(batch: dict) -> str:
    """The promotion issue body, and the text the Slack ask quotes.

    Ends with the `<!-- promote: X.Y.Z -->` marker `publish.yml` reads to tell
    whether `main` moved between the ask and the approval.
    """
    target, last = batch["target"], batch["last_final"]
    since = f"since `{last}`" if last else "since the beginning of the project"
    age = f" ({batch['last_final_date']})" if batch["last_final_date"] else ""
    lines = [
        f"**Promote `{target}`?**  {batch['commits_since']} commits {since}{age}.",
        "",
    ]
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
        (
            f"Latest pre-release: `pip install --pre yeaboi=={batch['latest_prerelease']}`"
            if batch["latest_prerelease"]
            else "_No pre-release published for this batch._"
        ),
        f"✅ to release `{target}` to PyPI + GitHub · ❌ to wait another week",
        "",
        f"<!-- promote: {target} -->",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-release numbering and the promotion batch")
    parser.add_argument("--next-rc", action="store_true", help="print the pre-release version for HEAD")
    parser.add_argument("--manifest", action="store_true", help="what is pending promotion")
    parser.add_argument("--markdown", action="store_true", help="render --manifest as the issue body")
    parser.add_argument("--json", action="store_true", help="render --manifest as JSON")
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
        if args.manifest:
            batch = pending(args.ref)
            print(json.dumps(batch, indent=2) if args.json or not args.markdown else markdown(batch))
            return 0
    except ReleaseChannelError as error:
        print(f"[release-channel] {error}", file=sys.stderr)
        return 2

    parser.error("pass one of --next-rc, --manifest, --write")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
