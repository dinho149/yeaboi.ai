#!/usr/bin/env python3
"""Assemble the fleet's gate-green PRs into one batch a human can test and ship.

Fleet PRs — the `cowork` label or an unattended branch prefix, the same
predicate `scripts/release_lane.py` reads — are never merged one by one. They
accumulate open against `main`, each individually CI-green and reviewed, and
this script folds them into a `batch/<date>` branch: one squash commit per PR,
built in a throwaway worktree off fresh `origin/main`, so `main`'s history stays
one commit per item exactly as it did when the fleet merged directly. The batch
opens as a PR labelled `release:promotion` — **ready for review, never a draft**,
because `claude-review.yml` skips drafts and `pr-feedback` would then find no
verdict the moment the batch was readied, with nothing able to produce one. CI
runs on the assembled tree — the first time the constituents are tested
*together* — and `auto-version.yml` gives the whole batch its one version bump.

Shipping is a human merging that PR with `--merge` (never squash: the announce
routine and `release_channel.py` both walk `git log <prev-tag>..<tag>`, and a
squash collapses the batch to one line). The head is `batch/…` and the PR
carries no `cowork` label, so `release_lane.py` classifies the merge `human`
and `publish.yml` cuts the official release from exactly the tree that was
tested. This script REFUSES to open a batch that would classify `fleet` — that
mistake would make the ship a silent no-op, with only a `::notice::unattended
merge` line to show for it.

    make batch-assemble          build the branch, open the batch PR, build the wheel
    ... --dry-run                assemble locally; push nothing, open nothing
    ... --close <batch-pr>       after the batch merges: close its constituents

A PR that conflicts — with `main` or with an earlier constituent — is skipped,
named, and left open for its workstream to rebase; one bad item never blocks
the batch. A PR whose diff moves the version line is skipped too: fleet PRs
carry `semver:none` precisely so `auto-version.yml` does not bump each branch
to the same next version, and a bump that slipped through would conflict with
every other constituent in `pyproject.toml` and `changelog_data.json`.

Stdlib only, like its neighbours.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _gh_transport as transport  # noqa: E402
import release_lane  # noqa: E402
import release_surfaces  # noqa: E402

# The label that marks the live batch PR. It used to mark the weekly promotion
# *issue*; the name survives because the meaning did — "the release channel's
# one open human decision" — and because `cowork_setup.py`'s KEEP_LABELS already
# protects it from teardown.
PROMOTION_LABEL = "release:promotion"

# The batch branch namespace. It MUST never appear in
# `pr_feedback.UNATTENDED_BRANCH_PREFIXES`: the whole ship path relies on the
# batch merge classifying `human`, and a prefix match there would gate
# `publish.yml` off and strand a signed batch with nothing red.
BATCH_PREFIX = "batch/"

# One constituent line in the batch PR body. Written by `_body`, read back by
# `--close` and by `beta_signoff.py`'s provider detection — a contract, so it is
# a module constant rather than two spellings.
CONSTITUENT_RE = re.compile(r"^- (?P<title>.+) \(#(?P<number>\d+)\)$", re.MULTILINE)

# What proves a PR is a batch this script wrote, written by `_body` and required
# by `--close` before it closes anything. `CONSTITUENT_RE` alone is not proof:
# `- <text> (#N)` is an ordinary bullet in an ordinary PR body here, so a
# mistyped number on `--close` would comment on and close a set of unrelated open
# PRs — and the next sweep reads a closure as a rejection, which is the very
# failure the merged-only check beside it exists to prevent.
BATCH_MARKER_RE = re.compile(r"<!--\s*batch:\s*\d{4}-\d{2}-\d{2}\s*-->")

# `Closes #N` / `Closes YEA-NN` lines lifted verbatim from constituent bodies
# into the batch body, so queued issues and Linear tickets close on the batch
# merge exactly as they would have on the constituent's own.
CLOSES_RE = re.compile(r"^(?:closes|fixes|resolves)\s+(?:#\d+|[A-Z][A-Z0-9]*-\d+)\s*$", re.IGNORECASE | re.MULTILINE)


class AssembleError(RuntimeError):
    """Something the human has to resolve before a batch can exist."""


def _gh(*args: str) -> str | None:
    """Run `gh` and return stdout, or None if it is missing or refuses.

    Through `_gh_transport`'s process seam, like every sibling that writes:
    `tests/conftest.py`'s `_no_real_gh_calls` blocks that one name, and a test
    that forgot to stub this must land there rather than on the real repo.
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


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Plain git, with the parent repo's GIT_* env stripped like release_channel does."""
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(  # noqa: S603 - literal argv assembled in this file
        ["git", *args], cwd=cwd or ROOT, env=env, capture_output=True, text=True, check=False
    )


def gate_green(pr: dict) -> bool:
    """Every check on the head commit passed, including `pr-feedback`.

    `statusCheckRollup` mixes two shapes — CheckRun rows carry
    `name`/`conclusion`, commit-status rows carry `context`/`state` — and
    `pr-feedback` is a commit status. An empty rollup is NOT green: it means the
    checks have not reported, and a batch is built only from work the gate has
    finished with.
    """
    rollup = pr.get("statusCheckRollup") or []
    if not rollup:
        return False
    seen_feedback = False
    for row in rollup:
        verdict = str(row.get("conclusion") or row.get("state") or "").upper()
        if verdict not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            return False
        if (row.get("context") or row.get("name")) == "pr-feedback":
            seen_feedback = True
    return seen_feedback


def fleet_prs() -> tuple[list[dict], list[tuple[dict, str]]]:
    """The open fleet-lane PRs against main: (ready to batch, skipped with reason).

    The lane predicate is `release_lane.classify` — imported, never respelled,
    for the same reason `publish.yml` refuses to hardcode a prefix.
    """
    data = _json(
        "pr",
        "list",
        "--base",
        "main",
        "--state",
        "open",
        "--limit",
        "100",
        "--json",
        "number,title,body,labels,headRefName,headRefOid,isDraft,statusCheckRollup",
    )
    if not isinstance(data, list):
        raise AssembleError("could not list open PRs — gh is missing or refused")
    ready: list[dict] = []
    skipped: list[tuple[dict, str]] = []
    for pr in data:
        labels = [entry.get("name") for entry in pr.get("labels") or [] if isinstance(entry, dict)]
        verdict = release_lane.classify({"labels": labels, "head": pr.get("headRefName") or ""})
        if verdict != release_lane.FLEET:
            continue  # a human's PR ships on its own merge, exactly as before
        if pr.get("isDraft"):
            skipped.append((pr, "still a draft"))
        elif not gate_green(pr):
            skipped.append((pr, "checks not green (or pr-feedback missing)"))
        else:
            ready.append(pr)
    ready.sort(key=lambda pr: int(pr["number"]))
    return ready, skipped


def assert_human_lane(head: str, labels: list[str]) -> None:
    """The batch PR must classify `human`, or its merge releases nothing.

    Checked here, at creation, because the failure at merge time is silent:
    `publish.yml` gates off with a `::notice::` and the signed batch just never
    ships.
    """
    if release_lane.classify({"labels": labels, "head": head}) != release_lane.HUMAN:
        raise AssembleError(
            f"the batch PR (head {head!r}, labels {labels!r}) would classify as fleet — "
            "its merge would cut no release. The head must not match an unattended "
            "prefix and the PR must never carry the `cowork` label."
        )


def _bumps_version(worktree: Path) -> bool:
    """Whether the staged squash moves the version line.

    A constituent that bumps `pyproject.toml` was opened without `semver:none`
    and would collide with every other constituent; it is the PR that must
    change, not the batch.
    """
    diff = _git("diff", "--cached", "--", "pyproject.toml", cwd=worktree)
    return "+version = " in diff.stdout


def assemble(prs: list[dict], *, date: str) -> tuple[Path, str, list[dict], list[tuple[dict, str]]]:
    """Build `batch/<date>` in a throwaway worktree: one squash commit per PR.

    Returns (worktree, branch, included, skipped-with-reason). Conflicts skip
    the PR and keep going — one bad item never blocks the batch.
    """
    branch = f"{BATCH_PREFIX}{date}"
    # Outside the repository, and predictable rather than random so the human can
    # find the checkout they are hand-testing. It used to live under `ROOT/.git`,
    # which put a full checkout plus `uv build` output inside the object store's
    # directory, kept it there until a run reused the same date — and could not be
    # created at all from one of this repo's own worktrees, where `.git` is a file.
    worktree = Path(tempfile.gettempdir()) / f"yeaboi-batch-{date}"
    if worktree.exists():
        _git("worktree", "remove", "--force", str(worktree))
        shutil.rmtree(worktree, ignore_errors=True)
    # Unconditional, and that is the fix rather than an accident: the case this
    # repairs is "rmtree succeeded, the registration survived", where the
    # directory is gone by the next run — so a prune guarded by `exists()` never
    # runs in the one situation it was written for, and `worktree add` refuses
    # that path forever.
    _git("worktree", "prune")
    fetched = _git("fetch", "origin", "--quiet")
    if fetched.returncode != 0:
        raise AssembleError(f"git fetch origin failed:\n{fetched.stderr.strip()}")
    _git("branch", "-D", branch)  # a re-run rebuilds from scratch; the old local branch is stale
    added = _git("worktree", "add", "-b", branch, str(worktree), "origin/main")
    if added.returncode != 0:
        raise AssembleError(f"could not create the batch worktree:\n{added.stderr.strip()}")

    included: list[dict] = []
    skipped: list[tuple[dict, str]] = []
    for pr in prs:
        oid = pr["headRefOid"]
        merged = _git("merge", "--squash", oid, cwd=worktree)
        if merged.returncode != 0:
            _git("reset", "--hard", "HEAD", cwd=worktree)
            against = f"#{included[-1]['number']} or main" if included else "main"
            skipped.append((pr, f"conflicts with {against} — rebase it and it joins the next batch"))
            continue
        if _bumps_version(worktree):
            _git("reset", "--hard", "HEAD", cwd=worktree)
            skipped.append((pr, "carries a version bump — label it semver:none and drop the bump commit"))
            continue
        staged = _git("diff", "--cached", "--quiet", cwd=worktree)
        if staged.returncode == 0:
            skipped.append((pr, "empty against the batch — already contained in an earlier constituent"))
            continue
        # --no-verify: the constituents already passed the gate individually and
        # the batch PR gets full CI; a pre-commit hook re-running the suite per
        # squash would make assembly take an hour.
        committed = _git("commit", "--no-verify", "-m", f"{pr['title']} (#{pr['number']})", cwd=worktree)
        if committed.returncode != 0:
            _git("reset", "--hard", "HEAD", cwd=worktree)
            skipped.append(
                (pr, f"commit failed: {committed.stderr.strip().splitlines()[-1] if committed.stderr else '?'}")
            )
            continue
        included.append(pr)
    return worktree, branch, included, skipped


def collect_closes(prs: list[dict]) -> list[str]:
    seen: list[str] = []
    for pr in prs:
        for line in CLOSES_RE.findall(str(pr.get("body") or "")):
            normalised = line.strip()
            if normalised not in seen:
                seen.append(normalised)
    return seen


def _changed_paths(worktree: Path) -> list[str]:
    diff = _git("diff", "--name-only", "origin/main...HEAD", cwd=worktree)
    return [line for line in diff.stdout.splitlines() if line.strip()]


def _body(included: list[dict], skipped: list[tuple[dict, str]], paths: list[str], date: str) -> str:
    lines = [
        f"## Release batch {date}",
        "",
        f"{len(included)} fleet changes, assembled by `scripts/batch_assemble.py` from",
        "gate-green PRs. Each constituent already passed CI, review, and `pr-feedback`",
        "individually; this PR's own CI is the first run on the assembled tree.",
        "",
        "**Merge with a merge commit (`gh pr merge --merge`), never squash** — the",
        "release notes and the next batch both walk this history one commit per item.",
        "",
    ]
    lines += [f"- {pr['title']} (#{pr['number']})" for pr in included]
    if skipped:
        lines += ["", "Not in this batch:", ""]
        lines += [f"- #{pr['number']} — {reason}" for pr, reason in skipped]
    providers = release_surfaces.campaign_providers(paths)
    baseline, per_track = release_surfaces.tracked_checklists(paths, providers)
    lines += ["", "### Hand-test before merging", "", release_surfaces.render(baseline, markdown=True)]
    for track in release_surfaces.TRACKS:
        items = per_track.get(track) or []
        if items:
            named = f": {', '.join(providers)}" if track == "integration" and providers else ""
            lines += ["", f"**{track}{named}**", "", release_surfaces.render(items, markdown=True)]
    closes = collect_closes(included)
    if closes:
        lines += ["", *closes]
    lines += ["", f"<!-- batch: {date} -->"]
    return "\n".join(lines)


def _build_wheel(worktree: Path) -> str | None:
    if shutil.which("uv") is None:
        return None
    built = subprocess.run(  # noqa: S603 - fixed argv
        ["uv", "build"], cwd=worktree, capture_output=True, text=True, check=False
    )
    if built.returncode != 0:
        return None
    wheels = sorted((worktree / "dist").glob("yeaboi-*.whl"))
    return str(wheels[-1]) if wheels else None


def constituents_of(body: str) -> list[int]:
    return [int(match.group("number")) for match in CONSTITUENT_RE.finditer(body)]


def close_constituents(batch_number: int) -> int:
    """After the batch merges: close each constituent with a pointer.

    Their head SHAs were squashed away, so GitHub cannot auto-mark them merged.
    Refuses on an unmerged batch — closing constituents of a batch that never
    shipped reads as sixteen rejections to the next sweep's dedupe pass.
    """
    batch = _json("pr", "view", str(batch_number), "--json", "state,body,url,labels")
    if not isinstance(batch, dict):
        print(f"[batch] could not read PR #{batch_number} — gh is missing or refused.", file=sys.stderr)
        return 2
    if str(batch.get("state", "")).upper() != "MERGED":
        print(f"[batch] PR #{batch_number} is not merged — refusing to close its constituents.", file=sys.stderr)
        return 1
    labels = [entry.get("name") for entry in batch.get("labels") or [] if isinstance(entry, dict)]
    if not BATCH_MARKER_RE.search(str(batch.get("body") or "")) and PROMOTION_LABEL not in labels:
        # A mistyped number is the whole reason for this check: without it, any
        # merged PR whose body happens to carry `- something (#123)` bullets —
        # changelog lists, "supersedes" sections, release notes — nominates those
        # numbers for closure.
        print(
            f"[batch] PR #{batch_number} is not a batch: no `<!-- batch: … -->` marker and no "
            f"`{PROMOTION_LABEL}` label. Refusing to close anything.",
            file=sys.stderr,
        )
        return 1
    numbers = constituents_of(str(batch.get("body") or ""))
    if not numbers:
        print(f"[batch] PR #{batch_number} names no constituents — nothing to close.")
        return 0
    failures = 0
    for number in numbers:
        state = _json("pr", "view", str(number), "--json", "state")
        if isinstance(state, dict) and str(state.get("state", "")).upper() != "OPEN":
            continue
        note = f"shipped in batch #{batch_number} ({batch.get('url', '')}) — closing; the work is on `main`."
        if _gh("pr", "comment", str(number), "--body", note) is None or _gh("pr", "close", str(number)) is None:
            print(f"[batch] could not close #{number} — close it by hand.", file=sys.stderr)
            failures += 1
        else:
            print(f"[batch] closed #{number} — shipped in batch #{batch_number}.")
    return 2 if failures else 0


def run_assemble(*, dry_run: bool) -> int:
    date = dt.date.today().isoformat()
    ready, skipped_pre = fleet_prs()
    for pr, reason in skipped_pre:
        print(f"[batch] skipping #{pr['number']} — {reason}")
    if not ready:
        print("[batch] no gate-green fleet PRs are waiting — nothing to assemble.")
        return 0

    worktree, branch, included, skipped_merge = assemble(ready, date=date)
    skipped = skipped_pre + skipped_merge
    for pr, reason in skipped_merge:
        print(f"[batch] skipping #{pr['number']} — {reason}")
    if not included:
        print("[batch] every candidate was skipped — no batch to open.")
        _git("worktree", "remove", "--force", str(worktree))
        return 1

    paths = _changed_paths(worktree)
    body = _body(included, skipped, paths, date)
    title = f"release batch {date} — {len(included)} changes"
    assert_human_lane(branch, [PROMOTION_LABEL])

    print(f"[batch] assembled {len(included)} of {len(ready)} candidates onto {branch}.")
    if dry_run:
        print(f"[batch] dry run — branch left at {worktree}; nothing pushed, no PR opened.")
        return 0

    pushed = _git("push", "--force-with-lease", "origin", f"{branch}:{branch}", cwd=worktree)
    if pushed.returncode != 0:
        print(f"[batch] push failed:\n{pushed.stderr.strip()}", file=sys.stderr)
        return 2
    body_file = worktree / ".batch-body.md"
    body_file.write_text(body, encoding="utf-8")
    # NOT a draft, and that is load-bearing rather than a preference.
    # `claude-review.yml` skips drafts outright, so a draft batch PR earns no
    # review verdict on its head — and `pr_feedback.py` only forgives that *while*
    # it is a draft. Flipping it ready at promote time therefore re-evaluated the
    # gate with no verdict to find and posted failure on the required context, with
    # nothing able to fix it: `claude-review` fires on CI's `workflow_run`, and
    # `ci.yml` does not list `ready_for_review`, so readying re-runs neither. The
    # only exit left moved the head sha, which invalidates every `tested:` marker.
    # Opening it ready means the review lands on the first CI run, like any PR.
    created = _gh(
        "pr",
        "create",
        "--base",
        "main",
        "--head",
        branch,
        "--title",
        title,
        "--label",
        PROMOTION_LABEL,
        "--body-file",
        str(body_file),
    )
    if created is None:
        # Distinguish "one is already open" (the push above refreshed it — fine)
        # from a real failure. Exiting green on a `gh` outage would report a
        # batch that does not exist.
        existing = _json("pr", "list", "--head", branch, "--state", "open", "--json", "number,url")
        if isinstance(existing, list) and existing:
            print(f"[batch] batch PR already open — the push refreshed it: {existing[0].get('url', '')}")
        else:
            print("[batch] pushed, but the batch PR could not be opened — gh is missing or refused.", file=sys.stderr)
            print(f"        open it by hand: gh pr create --base main --head {branch} \\", file=sys.stderr)
            print(f"          --title {title!r} --label {PROMOTION_LABEL} --body-file {body_file}", file=sys.stderr)
            return 2
    else:
        print(f"[batch] opened batch PR: {created.strip()}")

    wheel = _build_wheel(worktree)
    if wheel:
        print(f"[batch] wheel built: {wheel}")
        print(f"        try it:      uv tool install --force --from {wheel} yeaboi")
    else:
        print("[batch] wheel not built (uv missing or build failed) — test from the branch checkout.")
    print("[batch] next: hand-test, then make beta-sign-maintenance / beta-sign-integration,")
    print("        then make beta-promote. The merge is yours; nothing here merges.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble the fleet's gate-green PRs into a batch")
    parser.add_argument("--dry-run", action="store_true", help="assemble locally; push nothing, open nothing")
    parser.add_argument("--close", type=int, metavar="PR", help="close the constituents of a merged batch PR")
    args = parser.parse_args(argv)
    try:
        if args.close is not None:
            return close_constituents(args.close)
        return run_assemble(dry_run=args.dry_run)
    except AssembleError as error:
        print(f"[batch] {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
