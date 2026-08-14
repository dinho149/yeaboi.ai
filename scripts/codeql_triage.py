"""Classify this repo's open code-scanning alerts for `codeql-triage.yml`.

This is the deterministic, zero-token half of the weekly triage job. It reads the
raw alerts and `.github/codeql/triage-policy.yml` and decides, for every alert,
which of three lanes it belongs in — and it decides *in Python*, because the one
thing the model must never do here is fail silently.

It used to live as a heredoc inside the workflow, where nothing could test it.
It moved out when it grew the two rules below, both of which are exactly the kind
of comparison `cowork/README.md` insists Python owns rather than a prompt:

**A rejection is scoped to a location, not to a rule.** The propose lane used to
dedupe on `gh issue list --search "<rule id>" --state all`, so closing one issue
retired that rule *everywhere*, forever. `actions/untrusted-checkout/medium` was
answered for `auto-version.yml` and `publish.yml` in #248; under the old key the
same rule firing on a genuinely unsafe checkout in a workflow that does not exist
yet would have been swallowed with nothing printed. So the record of what was
accepted is the `accepted:` path list on a `propose` entry — reviewed, in a file,
in the diff — and an alert on a decided rule at an *unlisted* path is a finding,
not a duplicate.

**An accept without a dismissal is reported every week.** `codeql-triage.yml` may
not dismiss an alert (`cowork/house-rules.md`: closing one by declaring it
uninteresting is a human's call), so a recorded accept can sit open in the
Security tab indefinitely — which is how four alerts sat there from 2026-08-12
looking unexamined when they had been decided the same day. Because the survey
only ever fetches `state=open`, every alert that lands in the `accepted` lane is
by definition one nobody dismissed, and each one gets a `::warning::`.

Both rules share a design bias: silence means "nothing found", so anything this
script declines to act on it says out loud instead.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from dataclasses import dataclass, field

import yaml

# Worst first, so a capped batch keeps the alerts that matter rather than
# whichever rule sorts earliest in the alphabet.
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "error": 2, "warning": 3, "note": 4}

# What the model should do with a rule's remaining alerts. Named rather than
# inferred, so the prompt branches on a value instead of re-deriving the reason.
ACTION_OPEN = "open"  # no issue has ever covered this rule
ACTION_COMMENT = "comment"  # an open issue covers it — add this week's numbers
ACTION_NEW_LOCATION = "new-location"  # a rejected rule, at a path nobody accepted


@dataclass(frozen=True)
class Group:
    """One rule's proposable alerts, plus what to do about them."""

    rule: str
    action: str
    alerts: list[dict]
    existing_issue: int | None = None

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "action": self.action,
            "existing_issue": self.existing_issue,
            "alerts": self.alerts,
        }


@dataclass
class Classification:
    auto: list[dict] = field(default_factory=list)
    accepted: list[dict] = field(default_factory=list)
    propose: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)


def slim(alert: dict) -> dict:
    """Keep only what the prompt needs. The rest is noise in a token budget."""
    inst = alert.get("most_recent_instance") or {}
    loc = inst.get("location") or {}
    return {
        "number": alert["number"],
        "rule": alert["rule"]["id"],
        "severity": alert["rule"].get("security_severity_level") or alert["rule"].get("severity"),
        "path": loc.get("path"),
        "line": loc.get("start_line"),
        "message": (inst.get("message") or {}).get("text", ""),
        "url": alert.get("html_url"),
    }


def _sort_key(alert: dict) -> tuple:
    return (SEVERITY_ORDER.get(alert["severity"], 9), alert["rule"], alert["path"] or "", alert["line"] or 0)


def accepted_paths(policy: dict) -> dict[str, set[str]]:
    """rule id -> the paths a human recorded as accepted risk for that rule.

    Absent or empty means "decided nowhere yet": a `propose` entry with no
    `accepted:` list still proposes, which is what keeps adding the list a
    deliberate act rather than a side effect of writing down a reason.
    """
    return {entry["id"]: set(entry.get("accepted") or ()) for entry in policy.get("propose") or ()}


def classify(alerts: list[dict], policy: dict) -> Classification:
    """Split open alerts into auto-fixable, already-accepted, and proposable."""
    auto_rules = {entry["id"] for entry in policy["auto"]}
    accepted = accepted_paths(policy)
    out = Classification()

    for alert in (slim(a) for a in alerts):
        if alert["rule"] in auto_rules:
            out.auto.append(alert)
        elif alert["path"] in accepted.get(alert["rule"], set()):
            out.accepted.append(alert)
        else:
            out.propose.append(alert)

    out.auto.sort(key=_sort_key)
    out.accepted.sort(key=_sort_key)
    out.propose.sort(key=_sort_key)

    max_batch = int(policy["max_batch"])
    if len(out.auto) > max_batch:
        out.auto, out.dropped = out.auto[:max_batch], out.auto[max_batch:]
    return out


def _issue_for_rule(rule: str, issues: list[dict]) -> dict | None:
    """The issue covering a rule, preferring an open one.

    Matched on the title, which `codeql-triage.yml` writes as
    `[security][security] codeql: <rule id>`. Title rather than body because a
    body may merely *mention* a rule — a sweep quoting one, another issue linking
    to it — and a loose match here suppresses a real finding.
    """
    hits = [i for i in issues if rule in (i.get("title") or "")]
    if not hits:
        return None
    open_hits = [i for i in hits if (i.get("state") or "").upper() == "OPEN"]
    pool = open_hits or hits
    return max(pool, key=lambda i: i["number"])


def group_proposals(propose: list[dict], issues: list[dict]) -> list[Group]:
    """One group per rule, carrying the action the prompt should take.

    Dedup is per rule, as it always was — a repeated rule would otherwise flood
    the queue. What changed is that a *closed* issue no longer ends the matter:
    every alert reaching here already survived the `accepted` filter, so a
    rejected rule turning up again means a location nobody has ruled on.
    """
    by_rule: dict[str, list[dict]] = {}
    for alert in propose:
        by_rule.setdefault(alert["rule"], []).append(alert)

    groups = []
    for rule, alerts in by_rule.items():
        issue = _issue_for_rule(rule, issues)
        if issue is None:
            action, number = ACTION_OPEN, None
        elif (issue.get("state") or "").upper() == "OPEN":
            action, number = ACTION_COMMENT, issue["number"]
        else:
            action, number = ACTION_NEW_LOCATION, issue["number"]
        groups.append(Group(rule=rule, action=action, alerts=alerts, existing_issue=number))

    groups.sort(key=lambda g: _sort_key(g.alerts[0]))
    return groups


def report(found: Classification, groups: list[Group]) -> list[str]:
    """Every line the job prints, including the warnings. Returned rather than
    printed so a test can assert on them."""
    lines = [
        f"open={len(found.auto) + len(found.accepted) + len(found.propose) + len(found.dropped)} "
        f"auto={len(found.auto)} accepted={len(found.accepted)} "
        f"propose={len(found.propose)} dropped={len(found.dropped)}"
    ]
    for a in found.auto:
        lines.append(f"  AUTO     #{a['number']:>4} {a['rule']:<45} {a['path']}:{a['line']}")
    for a in found.accepted:
        lines.append(f"  ACCEPTED #{a['number']:>4} {a['rule']:<45} {a['path']}:{a['line']}")
    for g in groups:
        for a in g.alerts:
            lines.append(f"  PROPOSE  #{a['number']:>4} {a['rule']:<45} {a['path']}:{a['line']} [{g.action}]")

    # An accept is recorded in the policy file; dismissing the alert is a
    # separate, human-only act. Anything in this lane came back from a
    # `state=open` query, so it is an accept whose dismissal never happened.
    for a in found.accepted:
        lines.append(
            f"::warning::Alert #{a['number']} ({a['rule']} at {a['path']}) is recorded as accepted "
            "in .github/codeql/triage-policy.yml but is still open. Dismiss it in the Security tab "
            "so the tab means what it says; this job may not dismiss it."
        )

    # The case the old rule-scoped dedup swallowed in silence.
    for g in groups:
        if g.action != ACTION_NEW_LOCATION:
            continue
        paths = ", ".join(sorted({a["path"] or "?" for a in g.alerts}))
        lines.append(
            f"::warning::{g.rule} was answered in #{g.existing_issue}, but is now firing at {paths}, "
            "which no `accepted:` entry covers. Filing it as new rather than treating it as decided."
        )

    # Never let a cap read as full coverage.
    if found.dropped:
        nums = ", ".join(f"#{a['number']}" for a in found.dropped)
        lines.append(f"::warning::Batch capped. Deferred to next week: {nums}")

    if not found.auto and not groups:
        lines.append("::notice::No open code-scanning alerts to act on. Nothing to triage.")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alerts", required=True, help="`gh api … --slurp` output: a list of pages")
    parser.add_argument("--issues", required=True, help="`gh issue list --json number,title,state` output")
    parser.add_argument("--policy", default=".github/codeql/triage-policy.yml")
    parser.add_argument("--auto-out", default="codeql-auto.json")
    parser.add_argument("--propose-out", default="codeql-propose.json")
    args = parser.parse_args(argv)

    policy = yaml.safe_load(pathlib.Path(args.policy).read_text())
    pages = json.loads(pathlib.Path(args.alerts).read_text() or "[]")
    alerts = [a for page in pages for a in page]
    issues = json.loads(pathlib.Path(args.issues).read_text() or "[]")

    found = classify(alerts, policy)
    groups = group_proposals(found.propose, issues)

    fixes = {entry["id"]: entry["fix"] for entry in policy["auto"]}
    pathlib.Path(args.auto_out).write_text(json.dumps({"fixes": fixes, "alerts": found.auto}, indent=2))
    pathlib.Path(args.propose_out).write_text(json.dumps([g.as_dict() for g in groups], indent=2))

    for line in report(found, groups):
        print(line)

    if output := os.environ.get("GITHUB_OUTPUT"):
        with pathlib.Path(output).open("a") as fh:
            fh.write(f"found={'true' if (found.auto or groups) else 'false'}\n")
            fh.write(f"auto_count={len(found.auto)}\n")
            fh.write(f"propose_count={len(found.propose)}\n")
            fh.write(f"accepted_count={len(found.accepted)}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
