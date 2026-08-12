#!/usr/bin/env python3
"""What GitHub operations can this session actually reach?

`cron/cd-deploy.md` has been a no-op on every firing. The first diagnosis was
"the routine session has no `gh`", and it is true — but a run on 2026-08-11 went
further: it *installed* `gh`, confirmed `GH_TOKEN` was present, and still got

    HTTP 403: This GraphQL query is not enabled for this session — only the
              pinned set of PR-review operations is served.
    HTTP 403: Access to this GitHub Actions path is not permitted through this
              proxy.

So the binary was never the constraint. Outbound GitHub in that session type goes
through an agent proxy with its own allowlist, and *which operations are on it* is
the fact every remaining design decision needs. Nothing in the repo records it,
the way `tests/fixtures/cowork_webhook_live.json` records what the routines API
does — so this script goes and asks, one operation at a time, and prints a table
you can paste.

**It exercises the real transport.** Every call below goes through
``_gh_transport``, the module the fleet actually uses, rather than a lookalike —
a probe that proves a hand-rolled request works would prove nothing about
`cowork_setup.py`.

**Reads by default.** ``--writes <pr>`` adds the write half, and every write in
it is chosen to leave nothing behind: a label the PR already carries, a variable
PATCHed to the value it already holds, a comment posted and then deleted, and a
commit status on a context nothing requires.

**stdlib only**, like its transport: this has to run in a checkout with no
environment built.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gh_transport as transport  # noqa: E402 - after the sys.path line that makes it importable

ROOT = Path(__file__).resolve().parent.parent

# The one GraphQL query the fleet sends. Copied in shape from `pr_feedback.py`'s
# PR_QUERY rather than imported, because the point is to learn whether *this*
# shape is on the allowlist, and an import would drag the whole gate in with it.
PR_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number
      reviewDecision
      reviewThreads(first: 20) { nodes { isResolved } }
    }
  }
}
"""


@dataclass
class Outcome:
    """One operation's answer, plus who needs it — a 403 matters differently for
    `pr_feedback` (the merge gate stops working) than for a label nothing reads."""

    name: str
    consumer: str
    ok: bool
    detail: str = ""


@dataclass
class Probe:
    slug: str
    results: list[Outcome] = field(default_factory=list)

    def record(self, name: str, consumer: str, result: transport.ApiResult) -> transport.ApiResult:
        self.results.append(Outcome(name, consumer, result.ok, "" if result.ok else result.error[:400]))
        return result

    def get(self, name: str, consumer: str, path: str) -> transport.ApiResult:
        return self.record(name, consumer, transport.api("GET", path))

    def paged(self, name: str, consumer: str, path: str, key: str | None = None) -> transport.ApiResult:
        return self.record(name, consumer, transport.api_paged(path, key))


def read_probes(probe: Probe, pr: int) -> None:
    """Every read the fleet makes, in the order a failure would bite."""
    slug = probe.slug
    owner, _, name = slug.partition("/")

    # cowork_setup.py — the two that 403'd, and the one that decides the merge gate.
    probe.paged("labels: list", "cowork_setup", f"/repos/{slug}/labels")
    probe.paged("actions variables: list", "cowork_setup", f"/repos/{slug}/actions/variables", key="variables")
    probe.get("branch ruleset: read", "cowork_setup", f"/repos/{slug}/rules/branches/main")

    # pr_feedback.py — the gate. GraphQL first: it is the documented 403, and
    # `reviewDecision` and thread resolution exist in v4 and nowhere in v3.
    probe.record(
        "graphql: PR + review threads",
        "pr_feedback",
        transport.graphql(PR_QUERY, {"owner": owner, "name": name, "number": pr}),
    )
    probe.paged("issue comments: list", "pr_feedback", f"/repos/{slug}/issues/{pr}/comments")
    probe.paged("pr reviews: list", "pr_feedback", f"/repos/{slug}/pulls/{pr}/reviews")
    probe.paged("issue events: list", "pr_feedback", f"/repos/{slug}/issues/{pr}/events")
    probe.get("actions runs: list", "pr_feedback", f"/repos/{slug}/actions/runs?per_page=1")

    # cowork_relay.py — one issue's labels, which is how a maintainer's tick is read.
    probe.get("issue: read", "cowork_relay", f"/repos/{slug}/issues/{pr}")

    # The routine prose. `gh pr list`, `gh issue list`, `gh pr diff`, `gh release
    # view`, `gh run list` are the verbs the sweeps and the digest are built on;
    # these are their REST spellings.
    probe.get("pulls: list", "routine prose", f"/repos/{slug}/pulls?state=open&per_page=1")
    probe.get("issues: list by label", "routine prose", f"/repos/{slug}/issues?labels=cowork&state=open&per_page=1")
    probe.get("pr: read", "routine prose", f"/repos/{slug}/pulls/{pr}")
    probe.get("releases: latest", "routine prose", f"/repos/{slug}/releases/latest")
    probe.get("commit statuses: read", "routine prose", f"/repos/{slug}/commits/HEAD/status")


def write_probes(probe: Probe, pr: int) -> None:
    """The writes, each one a no-op by construction.

    Nothing here creates state that has to be cleaned up by hand: the label is
    one the PR already carries, the variable is PATCHed to the value it already
    holds, the comment is deleted by the next call, and the status sits on a
    context no ruleset requires. A probe that needs tidying up afterwards is a
    probe nobody runs twice.
    """
    slug = probe.slug

    # A label the PR already has. POST to the collection adds rather than
    # replaces, so re-adding an existing label changes nothing at all.
    existing = transport.api("GET", f"/repos/{slug}/issues/{pr}/labels")
    label = ""
    if existing.ok and isinstance(existing.data, list) and existing.data:
        first = existing.data[0]
        label = first.get("name", "") if isinstance(first, dict) else ""
    if label:
        probe.record(
            f"issue labels: add (re-adding {label!r})",
            "pr_feedback / cowork_relay",
            transport.api("POST", f"/repos/{slug}/issues/{pr}/labels", {"labels": [label]}),
        )
    else:
        probe.results.append(Outcome("issue labels: add", "pr_feedback / cowork_relay", False, "no label to re-add"))

    # A variable PATCHed to its own value. This is the exact `/actions/` path the
    # 2026-08-11 run was refused on, so its answer is the load-bearing one.
    variables = transport.api_paged(f"/repos/{slug}/actions/variables", key="variables")
    if variables.ok and isinstance(variables.data, list) and variables.data:
        current = variables.data[0]
        probe.record(
            f"actions variable: update ({current.get('name')} → same value)",
            "cowork_setup",
            transport.api(
                "PATCH",
                f"/repos/{slug}/actions/variables/{transport.segment(current.get('name', ''))}",
                {"name": current.get("name"), "value": current.get("value")},
            ),
        )
    else:
        probe.results.append(
            Outcome("actions variable: update", "cowork_setup", False, "could not read one to rewrite")
        )

    # A comment, then its deletion. Posted and removed inside one run so the PR
    # is left exactly as it was found — and the delete is itself a probe.
    posted = probe.record(
        "issue comment: create",
        "pr_feedback",
        transport.api(
            "POST",
            f"/repos/{slug}/issues/{pr}/comments",
            {"body": "<!-- probe: github access check, deleted by the same run -->"},
        ),
    )
    if posted.ok and isinstance(posted.data, dict) and posted.data.get("id"):
        probe.record(
            "issue comment: delete",
            "pr_feedback",
            transport.api("DELETE", f"/repos/{slug}/issues/comments/{posted.data['id']}"),
        )
    else:
        probe.results.append(Outcome("issue comment: delete", "pr_feedback", False, "nothing was created to delete"))

    # A commit status on a context no ruleset requires, so it cannot gate a merge
    # whatever it says.
    head = transport.api("GET", f"/repos/{slug}/pulls/{pr}")
    sha = (head.data or {}).get("head", {}).get("sha", "") if head.ok and isinstance(head.data, dict) else ""
    if sha:
        probe.record(
            "commit status: post",
            "pr_feedback",
            transport.api(
                "POST",
                f"/repos/{slug}/statuses/{sha}",
                {"state": "success", "context": "probe/github-access", "description": "transport probe"},
            ),
        )
    else:
        probe.results.append(Outcome("commit status: post", "pr_feedback", False, "could not read the head sha"))


def render(probe: Probe, *, wrote: bool) -> str:
    lines = [
        "## GitHub access probe",
        "",
        f"repository   {probe.slug}",
        f"gh on PATH   {'yes' if transport.gh_available() else 'no'}",
        f"gh ready     {'yes' if transport.gh_ready() else 'no'}",
        f"token        {'present' if transport.github_token() else 'ABSENT'}",
        f"writes       {'probed' if wrote else 'skipped (pass --writes <pr>)'}",
        "",
        "| operation | needed by | result |",
        "|---|---|---|",
    ]
    for item in probe.results:
        verdict = "ok" if item.ok else f"**FAIL** — {item.detail}"
        lines.append(f"| {item.name} | {item.consumer} | {verdict} |")
    failed = [item for item in probe.results if not item.ok]
    lines += ["", f"{len(probe.results) - len(failed)}/{len(probe.results)} permitted."]
    if failed:
        lines.append("Refused: " + ", ".join(item.name for item in failed))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pr", type=int, required=True, help="an open PR to read (and, with --writes, to write to)")
    parser.add_argument("--writes", action="store_true", help="also probe the write half (all of it self-cleaning)")
    args = parser.parse_args(argv)

    slug = transport.resolve_slug(ROOT)
    if not slug:
        print("[probe] no repository — no gh, no GITHUB_REPOSITORY, no github.com origin remote", file=sys.stderr)
        return 2
    if not transport.gh_available() and not transport.github_token():
        print("[probe] neither `gh` nor a token — nothing to probe with", file=sys.stderr)
        return 2

    probe = Probe(slug)
    read_probes(probe, args.pr)
    if args.writes:
        write_probes(probe, args.pr)
    print(render(probe, wrote=args.writes))
    # Always 0: a refusal is the finding, not an error. A non-zero exit here
    # would make a routine's own stop conditions swallow the report.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
