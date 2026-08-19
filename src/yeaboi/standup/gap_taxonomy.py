"""Why standup missed something — the deterministic root-cause ladder.

The LLM's only job in a transcript review is to report WHAT WAS SAID and whether
it appears in the report's evidence (see ``transcript_review``). It never names a
root cause. This module does, from facts already in hand: which sources the run
actually scanned, which of them failed, what the configured scope was, and what
the collectors are capable of fetching at all.

That split is the whole design. A model asked "why did standup miss this?" will
always produce a fluent answer, and a fluent wrong answer becomes a GitHub issue
on a public repo. A rule ladder either matches or returns nothing.

The taxonomy's most important field is ``scope``:

- ``config``  — the user can fix this by configuring standup. Stays local as a
  suggestion with an exact remedy; never becomes an issue.
- ``product`` — yeaboi is at fault and no amount of configuring helps. This is
  what drafts a GitHub issue.
- ``none``    — expected, not a defect (work with no digital footprint).

Follows ``standup/insights.py``'s stance: precision over recall. A missed gap
costs nothing; a false one costs trust and pollutes a public issue tracker.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

from yeaboi.agent.state import MEMBER_EVIDENCE_CAP, StandupReport, TranscriptClaim

logger = logging.getLogger(__name__)

SCOPE_CONFIG = "config"
SCOPE_PRODUCT = "product"
SCOPE_NONE = "none"

# Fingerprint scheme version. Bumping it deliberately re-files everything, so it
# is part of the hash rather than something to forget.
_FINGERPRINT_VERSION = "v1"


@dataclass(frozen=True)
class GapCategory:
    """One diagnosable reason standup missed or misstated work."""

    id: str
    label: str
    scope: str
    feedback_kind: str  # a feedback.FEEDBACK_TYPES value
    priority: str
    question: str  # which of the five questions this feature answers


# The five questions, as the user framed them.
Q_INACCURATE = "were the updates inaccurate"
Q_INTEGRATION = "did it miss an integration"
Q_FIX = "is there a fix it should make"
Q_FEATURE = "is there a feature it should add"
Q_EXPECTED = "expected — not a defect"

CATEGORIES: tuple[GapCategory, ...] = (
    GapCategory(
        "source_not_configured",
        "A source that would have seen this work is not configured",
        SCOPE_CONFIG,
        "Improvement",
        "medium",
        Q_INTEGRATION,
    ),
    GapCategory(
        "scope_gap_repository",
        "The repository or project is outside the configured code scope",
        SCOPE_CONFIG,
        "Improvement",
        "medium",
        Q_INTEGRATION,
    ),
    GapCategory(
        "source_configured_but_failed",
        "A configured source failed and its work went unreported",
        SCOPE_PRODUCT,
        "Bug",
        "high",
        Q_FIX,
    ),
    GapCategory(
        "integration_missing",
        "The work happened in a system yeaboi cannot read at all",
        SCOPE_PRODUCT,
        "Feature",
        "medium",
        Q_FEATURE,
    ),
    GapCategory(
        "capability_gap_in_supported_source",
        "A supported source is connected, but this kind of activity is never fetched",
        SCOPE_PRODUCT,
        "Feature",
        "high",
        Q_FEATURE,
    ),
    GapCategory(
        "automation_filter_false_positive",
        "Real work was excluded as service-hook automation",
        SCOPE_PRODUCT,
        "Bug",
        "high",
        Q_INACCURATE,
    ),
    GapCategory(
        "evidence_cap_truncation",
        "The evidence list hit its cap, so real activity was cut from the report",
        SCOPE_PRODUCT,
        "Bug",
        "medium",
        Q_FIX,
    ),
    GapCategory(
        "summary_dropped_it",
        "The activity was collected but the written summary left it out",
        SCOPE_PRODUCT,
        "Bug",
        "high",
        Q_INACCURATE,
    ),
    GapCategory(
        "report_inaccurate",
        "The report asserted something the team contradicted",
        SCOPE_PRODUCT,
        "Bug",
        "high",
        Q_INACCURATE,
    ),
    GapCategory(
        "untracked_work",
        "Work with no digital footprint (pairing, a call, interviews)",
        SCOPE_NONE,
        "Other",
        "low",
        Q_EXPECTED,
    ),
)

_BY_ID: dict[str, GapCategory] = {c.id: c for c in CATEGORIES}


def category(category_id: str) -> GapCategory | None:
    """Look up a category by id."""
    return _BY_ID.get(category_id)


# ---------------------------------------------------------------------------
# Systems and artifact kinds — a closed vocabulary, never LLM free text
# ---------------------------------------------------------------------------

# Collector source keys (standup/collector.ALL_SOURCES) plus the two non-source
# answers the model may give.
SYSTEM_ALIASES: dict[str, str] = {
    "jira": "jira",
    "azure_devops": "azure_devops",
    "azure_boards": "azure_devops",
    "azdo": "azure_devops",
    "azure_repos": "azdo_repos",
    "azdo_repos": "azdo_repos",
    "github": "github",
    "local_git": "local_git",
    "git": "local_git",
    "confluence": "confluence",
    "notion": "notion",
    "none": "none",
    "unknown": "unknown",
}

# Systems yeaboi has no integration for at all. Naming them explicitly is what
# lets "integration_missing" be a fact rather than a guess — an unlisted system
# falls through to "unknown" and produces nothing.
KNOWN_UNSUPPORTED: dict[str, str] = {
    "slack": "Slack",
    "teams": "Microsoft Teams",
    "linear": "Linear",
    "gitlab": "GitLab",
    "bitbucket": "Bitbucket",
    "figma": "Figma",
    "miro": "Miro",
    "sentry": "Sentry",
    "pagerduty": "PagerDuty",
    "datadog": "Datadog",
    "google_docs": "Google Docs",
    "gdocs": "Google Docs",
    "asana": "Asana",
    "trello": "Trello",
    "clickup": "ClickUp",
    "monday": "Monday",
    "zoom": "Zoom",
    "email": "email",
    "ci": "CI",
    "jenkins": "Jenkins",
}

# Free text → a slug. The slug, not the free text, is what gets fingerprinted;
# hashing the model's wording would silently break dedup and leave a public repo
# full of near-duplicate issues.
# ORDER MATTERS — first match wins, and the ACTIVITY outranks the OBJECT it was
# performed on. "commented on the work item" is a comment, not a ticket: on
# Azure Boards, work items are fetched and their discussion is not, so
# resolving that phrase to "ticket" would hide a real capability gap.
_ARTIFACT_KEYWORDS: tuple[tuple[str, str], ...] = (
    # Activities first. "comment" outranks "review": a REVIEW COMMENT is a
    # comment, and the word "review" turns up constantly inside object names
    # ("the Access Audit Review page"), so letting it win misfiled real cases.
    ("comment", "comment"),
    ("discussion", "comment"),
    ("thread", "comment"),
    ("replied", "comment"),
    ("review", "review"),
    ("approv", "review"),
    ("worklog", "worklog"),
    ("work log", "worklog"),
    ("time log", "worklog"),
    ("hours", "worklog"),
    # Then the objects.
    ("pull request", "pull_request"),
    ("merge request", "pull_request"),
    (" pr ", "pull_request"),
    ("commit", "commit"),
    ("branch", "commit"),
    ("page", "page"),
    ("doc", "page"),
    ("runbook", "page"),
    ("wiki", "page"),
    ("spec", "page"),
    ("ticket", "ticket"),
    ("issue", "ticket"),
    ("work item", "ticket"),
    ("story", "ticket"),
    ("task", "ticket"),
    ("bug", "ticket"),
    ("message", "message"),
    ("channel", "message"),
    ("alert", "alert"),
    ("incident", "alert"),
    ("board", "board"),
    ("sprint", "board"),
    ("pipeline", "build"),
    ("build", "build"),
    ("deploy", "build"),
    ("release", "build"),
)

FETCHED = "fetched"
NOT_FETCHED = "not_fetched"

# What each connected source can actually surface today, derived from the
# collectors (tools/*.py emit these "kind" values). Anything marked not_fetched
# is a real capability gap: the integration exists and is healthy, but this kind
# of activity never reaches a standup. Kept in lockstep with
# collector.ALL_SOURCES by test_standup_gap_taxonomy.
CAPABILITY_MANIFEST: dict[tuple[str, str], str] = {
    # Jira — issues, transitions, comments and assigned WIP.
    ("jira", "ticket"): FETCHED,
    ("jira", "comment"): FETCHED,
    ("jira", "worklog"): NOT_FETCHED,
    ("jira", "board"): NOT_FETCHED,
    # Azure Boards — work items and assigned WIP; discussion is not read.
    ("azure_devops", "ticket"): FETCHED,
    ("azure_devops", "comment"): NOT_FETCHED,
    ("azure_devops", "worklog"): NOT_FETCHED,
    ("azure_devops", "board"): NOT_FETCHED,
    # Azure Repos — commits, PRs, review votes and PR thread comments.
    ("azdo_repos", "commit"): FETCHED,
    ("azdo_repos", "pull_request"): FETCHED,
    ("azdo_repos", "review"): FETCHED,
    ("azdo_repos", "comment"): FETCHED,
    ("azdo_repos", "build"): NOT_FETCHED,
    # GitHub — commits, PRs, reviews and PR comments; issues are not scanned.
    ("github", "commit"): FETCHED,
    ("github", "pull_request"): FETCHED,
    ("github", "review"): FETCHED,
    ("github", "comment"): FETCHED,
    ("github", "ticket"): NOT_FETCHED,
    ("github", "build"): NOT_FETCHED,
    # Local git — commits only.
    ("local_git", "commit"): FETCHED,
    ("local_git", "pull_request"): NOT_FETCHED,
    # Confluence / Notion — page edits and creations; comments are not read.
    ("confluence", "page"): FETCHED,
    ("confluence", "comment"): NOT_FETCHED,
    ("notion", "page"): FETCHED,
    ("notion", "comment"): NOT_FETCHED,
}

_SYSTEM_LABELS = {
    "jira": "Jira",
    "azure_devops": "Azure Boards",
    "azdo_repos": "Azure Repos",
    "github": "GitHub",
    "local_git": "local git",
    "confluence": "Confluence",
    "notion": "Notion",
}

_ARTIFACT_LABELS = {
    "pull_request": "pull requests",
    "review": "code reviews",
    "comment": "comments",
    "worklog": "work logs",
    "commit": "commits",
    "ticket": "tickets",
    "page": "pages",
    "message": "messages",
    "alert": "alerts",
    "board": "board changes",
    "build": "builds and deployments",
    "unknown": "activity",
}

# owner/repo or project/repo, as it appears in speech.
_REPO_RE = re.compile(r"\b([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)\b")

# Which collector source each category of a MemberUpdate is fed by — used to map
# a claim's system onto the right evidence tuple.
_SYSTEM_CATEGORY = {
    "jira": "ticketing",
    "azure_devops": "ticketing",
    "azdo_repos": "code",
    "github": "code",
    "local_git": "code",
    "confluence": "documentation",
    "notion": "documentation",
}


# Which standup category an artifact belongs to, inferred from the OBJECT named
# rather than the activity. "commented on the design doc" is a comment (the
# kind) about documentation (the category) — the model is told never to guess a
# system, so this is how an honest "unknown" still gets diagnosed instead of
# vanishing. Order matters; first hit wins.
_CATEGORY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("confluence", "documentation"),
    ("notion", "documentation"),
    ("runbook", "documentation"),
    ("wiki", "documentation"),
    ("design doc", "documentation"),
    ("doc", "documentation"),
    ("page", "documentation"),
    ("spec", "documentation"),
    ("readme", "documentation"),
    ("pull request", "code"),
    ("merge request", "code"),
    (" pr ", "code"),
    ("commit", "code"),
    ("branch", "code"),
    ("repo", "code"),
    ("code review", "code"),
    ("jira", "ticketing"),
    ("ticket", "ticketing"),
    ("issue", "ticketing"),
    ("work item", "ticketing"),
    ("story", "ticketing"),
    ("backlog", "ticketing"),
)

# Which sources serve each category — the inverse of _SYSTEM_CATEGORY.
_CATEGORY_SYSTEMS: dict[str, tuple[str, ...]] = {
    "ticketing": ("jira", "azure_devops"),
    "code": ("github", "azdo_repos", "local_git"),
    "documentation": ("confluence", "notion"),
}

_CATEGORY_LABELS = {
    "ticketing": "ticket tracking",
    "code": "code hosting",
    "documentation": "documentation",
}


def infer_category(hint: str) -> str:
    """Infer the standup category (ticketing/code/documentation) from a hint."""
    text = f" {(hint or '').strip().lower()} "
    for needle, category in _CATEGORY_KEYWORDS:
        if needle in text:
            return category
    return ""


def normalize_system(hint: str) -> str:
    """Map a model's system hint onto a collector source key."""
    key = (hint or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key in SYSTEM_ALIASES:
        return SYSTEM_ALIASES[key]
    if key in KNOWN_UNSUPPORTED:
        return key
    return "unknown"


def artifact_kind(hint: str) -> str:
    """Reduce a free-text artifact hint to a closed-vocabulary slug."""
    text = f" {(hint or '').strip().lower()} "
    for needle, slug in _ARTIFACT_KEYWORDS:
        if needle in text:
            return slug
    return "unknown"


def artifact_is_unfetched(system_hint: str, artifact_hint: str) -> bool:
    """True when the manifest says this kind of activity is never collected.

    Used to stop an evidence key from CONFIRMING a claim about a different
    artifact: a Jira ticket appearing in the evidence does not confirm that the
    worklog against it was captured, because worklogs are not fetched at all.
    """
    system = normalize_system(system_hint)
    if system in ("none", "unknown"):
        return False
    return CAPABILITY_MANIFEST.get((system, artifact_kind(artifact_hint))) == NOT_FETCHED


def fingerprint(category_id: str, systems: tuple[str, ...], kind: str, scope_token: str = "") -> str:
    """A stable dedup key for one gap.

    Deliberately excludes member names, dates, session ids, the quote and the
    model's paraphrase, so "Confluence comments aren't fetched" is ONE gap
    whoever raises it and whenever. Only closed-vocabulary slugs go in — see
    ``artifact_kind``.
    """
    payload = f"{_FINGERPRINT_VERSION}|{category_id}|{'+'.join(sorted(systems))}|{kind}|{scope_token}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Facts read off the report
# ---------------------------------------------------------------------------


def scanned_sources(report: StandupReport) -> set[str]:
    """Sources the run actually examined (they reported a count)."""
    return {str(source) for source, _count in report.activity_counts}


def skipped_sources(report: StandupReport) -> dict[str, str]:
    """Sources deliberately NOT scanned, mapped to the reason given."""
    return {str(source): str(reason) for source, reason in report.skipped_sources}


_FAILURE_WORDS = ("authentication", "auth", "401", "403", "429", "500", "503", "timed out", "timeout", "failed")


def failed_sources(report: StandupReport) -> set[str]:
    """Sources that were configured but errored, per the report's own warnings.

    Reads ``warnings`` rather than guessing: the engine already turns a source
    401/403/5xx into a user-visible warning line naming the source.
    """
    failed: set[str] = set()
    for warning in report.warnings:
        lowered = warning.lower()
        if not any(word in lowered for word in _FAILURE_WORDS):
            continue
        for source, label in _SYSTEM_LABELS.items():
            if label.lower() in lowered or source in lowered:
                failed.add(source)
    # A skip reason that reads like a failure counts too.
    for source, reason in skipped_sources(report).items():
        if any(word in reason.lower() for word in _FAILURE_WORDS):
            failed.add(source)
    return failed


# Wording the automation partitioner uses when it drops activity from a member's
# credit (standup/automation.py notice_lines). Matching the report's own notice
# is what makes this rule a fact rather than an inference.
_AUTOMATION_NOTICE_MARKERS = ("look automated", "service-hook automation", "looks automated")


def automation_excluded_members(report: StandupReport) -> set[str]:
    """Members whose activity the report says it excluded as automation.

    Read off the notices the run already surfaced, so the rule can only fire
    where standup itself admits it dropped something.
    """
    members = {m.name for m in report.member_updates}
    excluded: set[str] = set()
    for warning in report.warnings:
        if not any(marker in warning.lower() for marker in _AUTOMATION_NOTICE_MARKERS):
            continue
        for name in members:
            if name and name in warning:
                excluded.add(name)
    return excluded


def _member(report: StandupReport, name: str):
    return next((m for m in report.member_updates if m.name == name), None)


def _evidence_for(report: StandupReport, name: str, system: str) -> tuple:
    member = _member(report, name)
    if member is None:
        return ()
    return {
        "ticketing": member.ticketing_evidence,
        "code": member.code_evidence,
        "documentation": member.documentation_evidence,
    }.get(_SYSTEM_CATEGORY.get(system, ""), ())


def _summaries_for(report: StandupReport, name: str) -> str:
    member = _member(report, name)
    if member is None:
        return ""
    return " ".join(
        (member.summary, member.ticketing_summary, member.code_summary, member.documentation_summary)
    ).lower()


def configured_scope(config: dict | None) -> set[str]:
    """Every repository/project name the run was told to look at."""
    config = config or {}
    scope: set[str] = set()
    for key in ("github_repositories", "azdo_projects", "azdo_repositories"):
        scope.update(str(v).strip().lower() for v in config.get(key, []) or [] if str(v).strip())
    return scope


def _named_repository(hint: str, claim_text: str) -> str:
    """Pull an ``owner/repo``-shaped token out of what was said, if present."""
    for text in (hint, claim_text):
        match = _REPO_RE.search(text or "")
        if match:
            return match.group(1)
    return ""


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Diagnosis:
    """A matched rule: the category plus the facts that justified it."""

    category: GapCategory
    systems: tuple[str, ...]
    kind: str
    scope_token: str = ""
    detail: str = ""
    root_cause: str = ""
    remedy: str = ""
    evidence: tuple[str, ...] = ()


def classify(
    claim: TranscriptClaim,
    *,
    report: StandupReport,
    config: dict | None = None,
    # The engine's real cap, not a copy of it. Rule 8 reads "the list is exactly
    # at its cap" as proof that items were cut, so a default below the true cap
    # turns every busy member into a truncation report.
    evidence_cap: int = MEMBER_EVIDENCE_CAP,
) -> Diagnosis | None:
    """Diagnose one unmatched claim. Returns None when no rule fires.

    First match wins, cheapest and most certain first. Returning None is the
    correct outcome for anything ambiguous — an unclassified claim is counted,
    never guessed at.
    """
    system = normalize_system(claim.system_hint)
    kind = artifact_kind(claim.artifact_hint or claim.claim)

    # Work with no digital footprint is the expected case, not a defect.
    if system == "none":
        return Diagnosis(_BY_ID["untracked_work"], (), kind, detail=claim.claim)

    scanned_now = scanned_sources(report)

    if system == "unknown":
        # The model is told never to guess a system, so "unknown" is the honest
        # answer for "commented on the design doc" — Confluence or Notion? Rather
        # than drop the claim, infer from the CATEGORY the artifact belongs to
        # and this run's own configuration. Only an unambiguous answer is used.
        category = infer_category(claim.artifact_hint or claim.claim)
        if not category:
            logger.debug("gap_taxonomy: unclassified claim (system hint %r)", claim.system_hint)
            return None
        candidates = [s for s in _CATEGORY_SYSTEMS[category] if s in scanned_now]
        if len(candidates) == 1:
            system = candidates[0]
            logger.info("gap_taxonomy: resolved unknown system to %s via %s category", system, category)
        elif not candidates:
            # Nothing serving that category was scanned at all — a config gap
            # naming the category, which is more useful than naming no system.
            category_label = _CATEGORY_LABELS[category]
            options = ", ".join(_SYSTEM_LABELS[s] for s in _CATEGORY_SYSTEMS[category])
            return Diagnosis(
                _BY_ID["source_not_configured"],
                (),
                kind,
                scope_token=category,
                detail=f"No {category_label} source was scanned for this standup.",
                root_cause=(
                    f"Work discussed as {category_label} could not be seen because no "
                    f"{category_label} source is connected."
                ),
                remedy=f"Connect one of {options} for standup (Standup → Configure).",
                evidence=(f"Sources scanned: {', '.join(sorted(scanned_now)) or 'none'}.",),
            )
        else:
            logger.debug("gap_taxonomy: %s category is ambiguous (%s)", category, candidates)
            return None

    label = _SYSTEM_LABELS.get(system, KNOWN_UNSUPPORTED.get(system, system))
    kind_label = _ARTIFACT_LABELS.get(kind, "activity")

    # 1. A system yeaboi cannot read at all → a feature request.
    if system in KNOWN_UNSUPPORTED:
        return Diagnosis(
            _BY_ID["integration_missing"],
            (system,),
            kind,
            detail=f"Work happened in {label}, which standup has no integration for.",
            root_cause=f"yeaboi has no {label} collector, so {kind_label} there can never appear in a standup.",
            evidence=(f"{label} is not among the sources standup can read.",),
        )

    scanned = scanned_now
    skipped = skipped_sources(report)
    failed = failed_sources(report)

    # 2. A configured source that errored — its work was lost to a failure, not
    #    to a missing capability. Checked BEFORE the capability manifest so a
    #    401 is never misreported as "we don't fetch that".
    if system in failed:
        reason = skipped.get(system, "see the run's notices")
        return Diagnosis(
            _BY_ID["source_configured_but_failed"],
            (system,),
            kind,
            detail=f"{label} is configured but failed during this run, so its {kind_label} were not reported.",
            root_cause=f"The {label} fetch failed ({reason}); standup reported the gap but the work was still missed.",
            evidence=(f"The report's notices record a {label} failure.",),
        )

    # 3. Not configured / not scanned → the user can fix this.
    if system not in scanned:
        reason = skipped.get(system, "not configured for this session")
        return Diagnosis(
            _BY_ID["source_not_configured"],
            (system,),
            kind,
            detail=f"{label} was not scanned for this standup ({reason}).",
            root_cause=f"{label} is not part of this session's standup sources, so its {kind_label} were invisible.",
            remedy=f"Enable {label} for standup (Standup → Configure), or set the credentials it needs.",
            evidence=(f"{label} was not among the sources examined ({reason}).",),
        )

    # 4. The source ran AND the item was fetched, but the automation partitioner
    #    dropped it from this member's credit. Checked BEFORE the scope and
    #    capability rules: the activity really was collected, so reporting it as
    #    "we don't fetch that" would send the maintainer down the wrong path.
    if claim.member and claim.member in automation_excluded_members(report):
        markers = [m.strip().lower() for m in str((config or {}).get("automation_markers", "")).split(",") if m.strip()]
        said = f"{claim.artifact_hint} {claim.claim}".lower()
        if kind in ("review", "comment") or any(marker in said for marker in markers):
            return Diagnosis(
                _BY_ID["automation_filter_false_positive"],
                (system,),
                kind,
                detail=(
                    f"{label} {kind_label} by this member were excluded as service-hook automation, "
                    "and the team says they were real work."
                ),
                root_cause=(
                    "The automation partitioner (standup/automation.py) classified genuine activity as "
                    "a service hook, so it was collected and then dropped from the member's credit."
                ),
                evidence=("The report's notices record excluded automation for this member.",),
            )

    # 5. The source ran, but the named repository/project is outside the scope.
    named = _named_repository(claim.artifact_hint, claim.claim)
    if named and system in ("github", "azdo_repos", "local_git"):
        scope = configured_scope(config)
        if scope and named.lower() not in scope:
            return Diagnosis(
                _BY_ID["scope_gap_repository"],
                (system,),
                kind,
                scope_token=named.lower(),
                detail=f"`{named}` is not in the configured {label} scope, so its {kind_label} were not collected.",
                root_cause=f"Standup only scans the repositories you list; `{named}` is not one of them.",
                remedy=f"Add `{named}` via Standup → Configure → Code, or standup_config_set(github_repositories=[…]).",
                evidence=(f"Configured scope: {', '.join(sorted(scope)) or 'none'}.",),
            )

    # 6. The source ran and is healthy, but this KIND of activity is never
    #    fetched — a real capability gap, and the most actionable finding here.
    if CAPABILITY_MANIFEST.get((system, kind)) == NOT_FETCHED:
        return Diagnosis(
            _BY_ID["capability_gap_in_supported_source"],
            (system,),
            kind,
            detail=f"{label} is connected, but standup never fetches {kind_label} from it.",
            root_cause=(
                f"The {label} collector reads other activity but not {kind_label}, "
                f"so this work cannot appear in a standup no matter how it is configured."
            ),
            evidence=(f"{label} was scanned successfully; {kind_label} are not among what it collects.",),
        )

    # 7. The source ran, the kind IS fetched, and the item is in the evidence —
    #    so the collector did its job and the written summary dropped it.
    if claim.member:
        evidence = _evidence_for(report, claim.member, system)
        keys = {e.key.lower() for e in evidence if e.key}
        if claim.matched_key and claim.matched_key.lower() in keys:
            if claim.matched_key.lower() not in _summaries_for(report, claim.member):
                return Diagnosis(
                    _BY_ID["summary_dropped_it"],
                    (system,),
                    kind,
                    detail=f"{claim.matched_key} was collected from {label} but never made it into the summary.",
                    root_cause=(
                        "The activity reached the report as evidence, so this is a summarisation defect, "
                        "not a collection one."
                    ),
                    evidence=(f"{claim.matched_key} is present in the member's {label} evidence.",),
                )
        # 8. The category's evidence list is exactly at its cap while more
        #    activity was counted — items were provably cut.
        member = _member(report, claim.member)
        if member is not None and len(evidence) >= evidence_cap:
            counted = {
                "ticketing": member.ticketing_activity_count,
                "code": member.code_activity_count,
                "documentation": member.documentation_activity_count,
            }.get(_SYSTEM_CATEGORY.get(system, ""), 0)
            if counted > len(evidence):
                return Diagnosis(
                    _BY_ID["evidence_cap_truncation"],
                    (system,),
                    kind,
                    detail=(f"{claim.member} had {counted} {label} items but the report kept only {len(evidence)}."),
                    root_cause=(f"The per-category evidence cap ({evidence_cap}) cut real activity out of the report."),
                    evidence=(f"{counted} items counted, {len(evidence)} retained.",),
                )

    logger.debug("gap_taxonomy: no rule matched for system=%s kind=%s", system, kind)
    return None


def classify_contradiction(claim: TranscriptClaim, *, report: StandupReport) -> Diagnosis | None:
    """Diagnose a contradicted claim — the report asserted something untrue.

    Requires the contradicted assertion to name a key that IS in the evidence.
    "No, I didn't finish that" is usually self-correction, not a yeaboi defect;
    demanding a concrete key keeps this to cases where standup made a specific
    claim of its own.
    """
    if not claim.member or not claim.matched_key:
        return None
    system = normalize_system(claim.system_hint)
    if system in ("none", "unknown"):
        return None
    keys = {e.key.lower() for e in _evidence_for(report, claim.member, system) if e.key}
    if claim.matched_key.lower() not in keys:
        return None
    label = _SYSTEM_LABELS.get(system, system)
    return Diagnosis(
        _BY_ID["report_inaccurate"],
        (system,),
        artifact_kind(claim.artifact_hint or claim.claim),
        detail=f"The report credited {claim.matched_key}, and the team said that is not what happened.",
        root_cause=(
            f"Standup inferred completed work from a {label} item whose real state differed — "
            "the inference was wrong, not merely incomplete."
        ),
        evidence=(f"{claim.matched_key} appears in the member's {label} evidence.",),
    )


# ---------------------------------------------------------------------------
# Turning a diagnosis into the drafted gap
# ---------------------------------------------------------------------------

_NEXT_STEPS: dict[str, tuple[str, ...]] = {
    "integration_missing": (
        "Decide whether {system} is worth a collector for standup.",
        "If so, add a fetcher in standup/collector.py returning normalised activity dicts.",
        "Classify its events into a standup category in standup/categories.py.",
    ),
    "capability_gap_in_supported_source": (
        "Extend the {system} collector to fetch {kind}.",
        "Classify them into the right standup category (standup/categories.py).",
        "Surface them as ActivityEvidence so exports and the TUI can show them.",
    ),
    "source_configured_but_failed": (
        "Reproduce the {system} failure and confirm the error surfaces as a notice.",
        "Add a retry or a clearer remedy message for this failure mode.",
    ),
    "summary_dropped_it": (
        "Check the standup summary prompt's rules for dropping collected evidence.",
        "Consider requiring every evidence key above a threshold to be mentioned or explicitly set aside.",
    ),
    "evidence_cap_truncation": (
        "Reconsider the per-category evidence cap in standup/engine.py (_member_evidence).",
        "At minimum, say '8 of 23 shown' rather than silently truncating.",
    ),
    "report_inaccurate": (
        "Review how standup infers completion from a {system} item's state.",
        "Prefer under-claiming: an unstated outcome beats a wrong one.",
    ),
}


def build_gap(diagnosis: Diagnosis, claims: tuple[TranscriptClaim, ...]):
    """Turn a diagnosis plus its supporting claims into a StandupGap.

    Titles and next steps are TEMPLATED, not model-written: the issue body has to
    be stable across recurrences for dedup to read naturally, and a model
    rephrasing the same gap each week would defeat that.
    """
    from yeaboi.agent.state import StandupGap

    cat = diagnosis.category
    system_label = (
        ", ".join(_SYSTEM_LABELS.get(s, KNOWN_UNSUPPORTED.get(s, s)) for s in diagnosis.systems) or "an unknown system"
    )
    kind_label = _ARTIFACT_LABELS.get(diagnosis.kind, "activity")

    titles = {
        "integration_missing": f"Standup cannot see work in {system_label}",
        "capability_gap_in_supported_source": f"Standup misses {system_label} {kind_label}",
        "source_configured_but_failed": f"Standup silently loses {system_label} activity when the fetch fails",
        "summary_dropped_it": f"Standup summary drops collected {system_label} evidence",
        "evidence_cap_truncation": f"Standup truncates {system_label} evidence without saying so",
        "report_inaccurate": f"Standup over-claims completion from {system_label} state",
        "source_not_configured": f"{system_label} is not configured for standup",
        "scope_gap_repository": f"`{diagnosis.scope_token}` is outside your standup code scope",
        "untracked_work": "Work discussed with no digital footprint",
    }
    steps = tuple(step.format(system=system_label, kind=kind_label) for step in _NEXT_STEPS.get(cat.id, ()))

    return StandupGap(
        fingerprint=fingerprint(cat.id, diagnosis.systems, diagnosis.kind, diagnosis.scope_token),
        category=cat.id,
        scope=cat.scope,
        title=titles.get(cat.id, cat.label),
        detail=diagnosis.detail or cat.label,
        root_cause=diagnosis.root_cause,
        priority=cat.priority,
        # A contradiction is the one category where the signal is genuinely
        # weaker — people self-correct in standups all the time.
        confidence="medium" if cat.id == "report_inaccurate" else "high",
        feedback_kind=cat.feedback_kind,
        members=tuple(dict.fromkeys(c.member for c in claims if c.member)),
        claims=claims,
        evidence=diagnosis.evidence,
        next_steps=steps,
        affected_systems=diagnosis.systems,
        remedy=diagnosis.remedy,
    )
