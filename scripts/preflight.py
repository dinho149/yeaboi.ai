#!/usr/bin/env python3
"""Run the optional CI jobs this branch's diff needs, before the PR exists.

`make test` proves the Python suite and nothing else. CI checks eight further
things — the ruff format check, gitleaks, actionlint, the front-end bundles, the
docs site, the Go sidecar, the parity suite unskipped, the golden evaluators and
the wheel's contents — and every one of them used to be discovered *after* the
PR was open, minutes later, on a branch already pushed.

Which of them a diff needs is not a judgement call: `scripts/test_scope.py`
already computes it for CI's `scope` job, and already carries the rule that makes
narrowing safe — anything it cannot classify runs everything. Nothing called it
with `--base` until this script.

Two conventions it inherits from the rest of the fleet:

* **Never silently narrow.** Every skipped job is printed with the reason it was
  skipped, the way `make beta-check` lists the angles a batch did not reach. A
  run that says nothing about what it left out reads as "covered everything".
* **An unreadable scope is not an empty one.** If the selector crashes, or hands
  back something unparseable, run every job rather than none — a `|| true` here
  would turn a broken selector into a silent green.

Standard library only and `python3`, not `uv run`: this sits in the ship gate and
putting a dependency resolve in front of it is how a fast gate becomes one people
skip.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOPE = ROOT / "scripts" / "test_scope.py"

# One entry per Job in test_scope.py's JOBS registry. The mapping is asserted
# to be total by tests/unit/test_ship_gate.py — a job added there and not here
# would otherwise just never run locally, and a selector's failure mode is
# silence.
JOB_TARGETS: dict[str, tuple[str, ...]] = {
    "go": ("go-lint", "go-test", "go-build"),
    "parity": ("parity",),
    "web": ("web-check",),
    "site": ("site-check",),
    "package": ("package-check",),
    "eval": ("eval",),
}

# The binary each job needs before it can run at all. A job whose toolchain is
# absent is reported and skipped, not failed — see the module docstring.
JOB_TOOLCHAIN: dict[str, str] = {
    "go": "go",
    "parity": "go",
    "web": "npm",
}

# actionlint is not one of test_scope.py's jobs — CI runs it unconditionally and
# it has no Makefile target — so it is keyed off the diff directly.
WORKFLOW_PREFIX = ".github/workflows/"


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def changed_paths(base: str) -> list[str]:
    """Paths in the merge-base diff against `base`, plus anything uncommitted.

    Both halves matter at ship time. The committed half is what the PR will
    carry; the uncommitted half is what is about to be committed, and a gate that
    only looked at one of them would pass on work it never saw.
    """
    merge_base = _git("merge-base", base, "HEAD").strip()
    paths: list[str] = []
    if merge_base:
        paths += [p for p in _git("diff", "--name-only", "--no-renames", f"{merge_base}...HEAD").splitlines() if p]
    for line in _git("status", "--porcelain").splitlines():
        entry = line[3:] if len(line) > 3 else ""
        for side in entry.split(" -> "):
            if side.strip():
                paths.append(side.strip().strip('"'))
    return paths


def decide(changed: list[str]) -> tuple[dict[str, bool], str]:
    """Ask test_scope.py which optional jobs these paths need.

    Takes the path list rather than a ref, and feeds it over `--changed-files -`.
    `--base` would have been the obvious call and is wrong here: in
    `test_scope.main` the source arguments are a mutually-exclusive group, so
    `--base` is a *committed-only* merge-base diff. `cowork-builder` runs this
    gate before it commits, and a partially-committed branch would then skip
    `web`/`go`/`parity`/`package` for anything living only in the working tree —
    a gate passing on work it never saw.

    Returns (jobs, note). A selector that cannot be read returns every job true
    and says why, rather than an empty selection that would look like a pass.
    """
    result = subprocess.run(
        [sys.executable, str(SCOPE), "--changed-files", "-", "--jobs"],
        cwd=ROOT,
        input="\n".join(changed),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return dict.fromkeys(JOB_TARGETS, True), f"scope selector exited {result.returncode} — running every job"
    try:
        jobs = json.loads(result.stdout)
    except json.JSONDecodeError:
        return dict.fromkeys(JOB_TARGETS, True), "scope selector output was not JSON — running every job"
    if not isinstance(jobs, dict) or not jobs:
        return dict.fromkeys(JOB_TARGETS, True), "scope selector returned no jobs — running every job"
    # A job the selector knows about and this script does not is the drift the
    # totality test guards; run it loudly rather than dropping it.
    unknown = sorted(set(jobs) - set(JOB_TARGETS))
    note = f"scope reported jobs this script has no target for: {', '.join(unknown)}" if unknown else ""
    return {name: bool(value) for name, value in jobs.items()}, note


def run_make(targets: tuple[str, ...]) -> int:
    print(f"→ make {' '.join(targets)}", flush=True)
    return subprocess.run(["make", *targets], cwd=ROOT).returncode


def run_actionlint(changed: list[str]) -> tuple[str, str]:
    """Returns (status, detail). Best-effort: CI runs it regardless."""
    if not any(p.startswith(WORKFLOW_PREFIX) for p in changed):
        return "skipped", f"no {WORKFLOW_PREFIX} paths in the diff"
    if shutil.which("actionlint") is None:
        return "unavailable", "actionlint is not on PATH — CI's `Workflow lint` job will check it"
    print("→ actionlint", flush=True)
    code = subprocess.run(["actionlint"], cwd=ROOT).returncode
    return ("passed" if code == 0 else "failed"), ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="ref to diff against (default: origin/main)")
    parser.add_argument("--list", action="store_true", help="report the plan and exit without running anything")
    args = parser.parse_args(argv)

    changed = changed_paths(args.base)
    jobs, note = decide(changed)
    if note:
        print(f"[preflight] {note}")

    needed, skipped = [], []
    for name in JOB_TARGETS:
        if not jobs.get(name):
            skipped.append((name, "the diff touches none of its paths"))
        elif (tool := JOB_TOOLCHAIN.get(name)) and shutil.which(tool) is None:
            skipped.append((name, f"{tool} is not on PATH — CI's `{name}` job will check it"))
        else:
            needed.append(name)

    print(f"[preflight] base {args.base} · {len(changed)} changed path(s)")
    if needed:
        print(f"[preflight] running: {', '.join(needed)}")
    for name, why in skipped:
        print(f"[preflight] skipped {name} — {why}")

    if args.list:
        return 0

    failures: list[str] = []
    for name in needed:
        if run_make(JOB_TARGETS[name]) != 0:
            failures.append(name)
            # Stop at the first failure: the point of preflight is to fail before
            # the PR, and the remaining jobs cost minutes each.
            break

    if not failures:
        status, detail = run_actionlint(changed)
        if status == "failed":
            failures.append("actionlint")
        elif detail:
            print(f"[preflight] actionlint {status} — {detail}")

    if failures:
        print(
            f"✗ preflight failed: {', '.join(failures)} — CI would have found this after the PR opened", file=sys.stderr
        )
        return 1
    print(f"✓ preflight passed ({len(needed)} job(s) run, {len(skipped)} skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
