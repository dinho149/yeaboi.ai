"""Turn a diagnosed standup gap into a GitHub issue on the yeaboi repo.

This is the only module in the transcript-review feature that talks to GitHub,
and nothing calls it except the explicit "file these" act. The review pipeline
does not import it at all — that is the structural half of "draft, then
confirm", so an unattended scheduled run cannot write to a public repo even by
mistake.

Reuses ``feedback.py``'s primitives (the same repo, title prefix, label scheme
and token-less browser fallback) rather than extending it: that module is the
user-typed feedback path with attachment chips and AI polish, and bolting issue
search, commenting and a dedup ledger onto it would double it.

**Dedup**, cheapest tier first:

1. the local ledger (``standup_gap_issues``) — the normal path;
2. a remote confirm of the stored number. A CLOSED issue is never commented on:
   closure means the maintainer resolved or rejected it, so a recurrence
   deserves a fresh issue that references the old one;
3. a cold-start scan for the invisible ``<!-- yeaboi-gap: … -->`` body marker,
   for a lost or fresh database.

**Redaction is mandatory, not a setting.** ``FEEDBACK_REPO`` is public and
transcript quotes are the richest personal data this app handles, so every body
is name-masked, home-relativised and secret-redacted before it leaves the
machine — and filing is BLOCKED outright if the redacted body still looks like
it carries a credential. Only the verified quote travels, never the transcript.

Like ``submit_feedback``, nothing here raises: every failure becomes a
``GapIssueLink`` state the caller can report.
"""

from __future__ import annotations

import logging
import re
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

from yeaboi.agent.state import GapIssueLink, StandupGap, StandupReport, TranscriptReview
from yeaboi.feedback import (
    FEEDBACK_REPO,
    build_issue_url,
    issue_labels,
    issue_title,
)

logger = logging.getLogger(__name__)

# Marks a body as machine-filed and carries its fingerprint, so a cold-start
# scan can recognise the gap without the local ledger.
GAP_LABEL = "source:standup-review"
_MARKER = "<!-- yeaboi-gap: {fingerprint} -->"
_MARKER_RE = re.compile(r"<!--\s*yeaboi-gap:\s*([0-9a-f]{8,})\s*-->")

# Quotes are the evidence, but a public issue must never carry a paragraph of
# somebody's speech.
_QUOTE_CLIP = 200
# How many browser tabs one filing action may open before it becomes hostile.
_MAX_BROWSER_TABS = 3
# Cold-start scan depth.
_SCAN_LIMIT = 100

# Shapes that must never survive redaction into a public issue. Deliberately
# broader than redaction.py's token list: that one protects logs, this one is
# the last gate before publication.
_LEAK_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"[\w.+-]+@[\w-]+\.[\w.]{2,}", "an email address"),
    (r"(?<!\d)(?:\+\d{1,3}[ -]?)?(?:\(\d{3}\)|\d{3})[ -]\d{3}[ -]\d{4}(?!\d)", "a phone number"),
    (r"(?i)\b(?:api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*\S{6,}", "a credential"),
    # A long opaque run: letters and digits only, containing BOTH, with no
    # separators. Requiring all three is what stops it matching ordinary
    # identifiers — "capability_gap_in_supported_source" is 34 characters and
    # is not a secret.
    (r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{32,}\b", "a long opaque token"),
)


def gap_labels(gap: StandupGap) -> list[str]:
    """Labels for a gap issue. Harmless when GitHub drops them (see feedback.py)."""
    return [*issue_labels(gap.feedback_kind, "standup"), GAP_LABEL, f"gap:{gap.category}"]


# ---------------------------------------------------------------------------
# Redaction — mandatory, applied to every body before it leaves the machine
# ---------------------------------------------------------------------------


def name_mask(review: TranscriptReview, report: StandupReport | None = None) -> dict[str, str]:
    """Map every real member name to a stable ``Engineer A/B/…`` label.

    The mapping is derived from the review and stays local; the issue only ever
    sees the labels. Sorted so the same person is the same letter across a
    review, which keeps a multi-member issue readable.
    """
    names = {c.member for c in review.claims if c.member}
    for gap in (*review.gaps, *review.config_suggestions):
        names.update(gap.members)
    if report is not None:
        names.update(m.name for m in report.member_updates if m.name)
    mapping: dict[str, str] = {}
    for index, name in enumerate(sorted(n for n in names if n.strip())):
        # A-Z, then AA, AB… — a roster bigger than 26 is unusual but must not wrap.
        label = chr(ord("A") + index) if index < 26 else f"A{chr(ord('A') + index - 26)}"
        mapping[name] = f"Engineer {label}"
    return mapping


# Given names that are also technical vocabulary. Masking these would corrupt
# the very text they appear in ("merged into main"), so a member whose name
# collides with one keeps only their full-name masking.
_NAME_TOKEN_STOPLIST = frozenset(
    {
        "main",
        "master",
        "test",
        "prod",
        "dev",
        "api",
        "web",
        "app",
        "core",
        "data",
        "base",
        "will",
        "may",
        "mark",
        "bill",
        "rob",
        "art",
        "june",
        "april",
        "may",
    }
)
_MIN_TOKEN_LEN = 3


def _mask_aliases(mask: dict[str, str]) -> list[tuple[str, str]]:
    """Expand the name→label mask to the forms that actually appear in prose.

    Summaries are LLM-written and refer to people by GIVEN NAME ("Omar also
    reviewed the IRSA trust PR"), so masking only the full name leaks first
    names onto a public repository. Surnames are deliberately NOT expanded:
    they collide with technical vocabulary far more often (a teammate called
    Main, a repo called Popa), and a corrupted issue body is its own problem.
    """
    aliases: dict[str, str] = {}
    for name, label in mask.items():
        if not name.strip():
            continue
        aliases[name] = label
        first = name.strip().split()[0]
        if len(first) >= _MIN_TOKEN_LEN and first.lower() not in _NAME_TOKEN_STOPLIST:
            # Never let a token override a longer full-name entry.
            aliases.setdefault(first, label)
    # Longest first so "Alice Curtis" is replaced before a bare "Alice".
    return sorted(aliases.items(), key=lambda kv: len(kv[0]), reverse=True)


def scrub(text: str, mask: dict[str, str]) -> str:
    """Apply the full publication scrub: names, home paths, then secrets."""
    from yeaboi.redaction import redact

    out = text or ""
    for name, label in _mask_aliases(mask):
        out = re.sub(rf"\b{re.escape(name)}\b", label, out)
    # feedback._relativize_home only rewrites a string that IS a path; here the
    # home directory appears mid-sentence, so replace every occurrence. A public
    # issue must never carry the reporter's username.
    home = str(Path.home())
    out = out.replace(home, "~") if home else out
    return redact(out)


def leak_check(text: str) -> str:
    """Return a human reason if the scrubbed text still looks unsafe, else ""."""
    for pattern, what in _LEAK_PATTERNS:
        match = re.search(pattern, text)
        if match:
            # [REDACTED] itself is long and opaque; don't trip over our own marker.
            if "REDACTED" in match.group(0):
                continue
            logger.warning("gap_issues: filing blocked — body still contains %s", what)
            return what
    return ""


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------


def _evidence_lines(gap: StandupGap, review: TranscriptReview, mask: dict[str, str]) -> list[str]:
    lines: list[str] = []
    source_names = {s.path: s.filename for s in review.sources}
    for claim in gap.claims[:3]:
        if not claim.quote:
            continue
        who = mask.get(claim.member, claim.member) or "Someone"
        where = source_names.get(claim.source_path, "the standup transcript")
        lines.append(f"> {claim.quote[:_QUOTE_CLIP]}")
        lines.append(f"> — {who}, {review.standup_date or 'the'} standup ({where})")
        lines.append("")
    return lines


def _report_lines(gap: StandupGap, report: StandupReport | None) -> list[str]:
    if report is None:
        return ["_No standup run was found for this date._"]
    lines: list[str] = []
    for name in gap.members[:3]:
        member = next((m for m in report.member_updates if m.name == name), None)
        if member is None:
            continue
        for label, value in (
            ("Overall", member.summary),
            ("Tickets", member.ticketing_summary),
            ("Code", member.code_summary),
            ("Docs", member.documentation_summary),
        ):
            if value:
                lines.append(f"- **{label}:** {value}")
    lines.append("")
    lines.append(f"Report date {report.date or 'unknown'} · activity window {report.activity_window or 'unknown'}")
    return lines


def _config_lines(report: StandupReport | None) -> list[str]:
    if report is None:
        return []
    scanned = ", ".join(f"{source} ({count})" for source, count in report.activity_counts) or "none"
    coverage = ", ".join(f"{k}={v}" for k, v in report.category_coverage) or "not recorded"
    lines = [f"Sources scanned: {scanned}", f"Coverage: {coverage}"]
    if report.skipped_sources:
        skipped = ", ".join(f"{s} ({why})" for s, why in report.skipped_sources)
        lines.append(f"Skipped: {skipped}")
    return lines


def build_gap_issue_body(
    gap: StandupGap,
    review: TranscriptReview,
    *,
    report: StandupReport | None = None,
    occurrences: int = 1,
    previous_issue: int = 0,
) -> str:
    """Render the issue body. Always scrubbed — there is no unscrubbed path."""
    from yeaboi import __version__

    mask = name_mask(review, report)
    parts: list[str] = [
        _MARKER.format(fingerprint=gap.fingerprint),
        f"**Type:** {gap.feedback_kind} · **Area:** standup · **Gap:** `{gap.category}`",
        "",
        "### What standup missed",
        gap.detail or gap.title,
        "",
        "### Diagnosed root cause",
        gap.root_cause or "(not determined)",
        "",
        f"Affected: {', '.join(gap.affected_systems) or 'unknown'} · "
        f"Confidence: {gap.confidence} · Priority: {gap.priority}",
    ]

    evidence = _evidence_lines(gap, review, mask)
    if evidence:
        parts += ["", "### Evidence from the standup transcript", *evidence]
    if gap.evidence:
        parts += [f"- {line}" for line in gap.evidence]

    parts += ["", "### What the report said", *_report_lines(gap, report)]

    config_lines = _config_lines(report)
    if config_lines:
        parts += ["", "### Configuration at the time", *config_lines]

    if gap.next_steps:
        parts += ["", "### Suggested next steps", *[f"- {step}" for step in gap.next_steps]]

    recurrence = f"Seen {occurrences} time(s); most recently in the {review.standup_date} standup."
    if previous_issue:
        recurrence += f" Previously filed as #{previous_issue}, which was closed."
    parts += ["", "### Recurrence", recurrence]

    parts += [
        "",
        "---",
        f"_Filed by yeaboi standup transcript review · v{__version__}. "
        "Member names are masked; only verified quotes are included._",
    ]
    return scrub("\n".join(parts), mask)


def build_gap_comment_body(gap: StandupGap, review: TranscriptReview, *, occurrences: int = 1) -> str:
    """Render the recurrence comment. Also always scrubbed."""
    mask = name_mask(review)
    parts = [
        f"Seen again in the **{review.standup_date}** standup (occurrence {occurrences}).",
        "",
    ]
    evidence = _evidence_lines(gap, review, mask)
    if evidence:
        parts += ["Fresh evidence:", "", *evidence]
    parts.append("_Reported by yeaboi standup transcript review._")
    return scrub("\n".join(parts), mask)


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _repo():
    """The PyGithub repo handle, or None when there is no usable token."""
    from yeaboi.config import get_github_token

    if not get_github_token():
        return None
    from yeaboi.tools.github import _get_github_client

    return _get_github_client().get_repo(FEEDBACK_REPO)


def find_existing_issue(fingerprint: str, *, title: str = "") -> tuple[int, str, str] | None:
    """Cold-start scan for an already-filed gap. Returns (number, url, state).

    Used when the local ledger has no row — a fresh machine, or a database that
    was reset. Never raises.
    """
    try:
        repo = _repo()
        if repo is None:
            return None
        try:
            issues = repo.get_issues(state="all", labels=[GAP_LABEL])
        except Exception as exc:
            # GitHub 422s on a label filter for a label that does not exist on
            # the repo yet — which is exactly the state on the very first run.
            logger.info("gap_issues: label-filtered search unavailable (%s); scanning recent issues", exc)
            issues = repo.get_issues(state="all")
        for index, issue in enumerate(issues):
            if index >= _SCAN_LIMIT:
                break
            body = issue.body or ""
            match = _MARKER_RE.search(body)
            if match:
                # A marked issue states which gap it is. If the fingerprint does
                # not match, the title is not evidence that it does — some
                # templates render the same sentence for genuinely different
                # gaps (an unresolved category becomes "an unknown system"), and
                # trusting the title there would post one gap's evidence as a
                # comment on another gap's PUBLIC issue and then never file its
                # own. The title fallback exists for issues filed before the
                # marker did, so it only applies where there is no marker.
                if match.group(1) != fingerprint:
                    continue
            elif not (title and issue.title == issue_title("", title).strip()):
                continue
            logger.info("gap_issues: found existing issue #%s for %s", issue.number, fingerprint)
            return int(issue.number), issue.html_url, str(issue.state)
    except Exception as exc:
        logger.warning("gap_issues: issue search failed: %s", exc)
    return None


def file_gap(
    gap: StandupGap,
    review: TranscriptReview,
    *,
    report: StandupReport | None = None,
    occurrences: int = 1,
    previous_issue: int = 0,
    browser_budget: int = _MAX_BROWSER_TABS,
) -> GapIssueLink:
    """File a new issue for a gap. Never raises."""
    body = build_gap_issue_body(gap, review, report=report, occurrences=occurrences, previous_issue=previous_issue)
    leak = leak_check(body)
    if leak:
        return GapIssueLink(
            fingerprint=gap.fingerprint,
            state="blocked",
            occurrences=occurrences,
            message=(
                f"Filing blocked — the drafted issue still looks like it contains {leak}. "
                "Nothing was sent; review the transcript or edit the draft by hand."
            ),
        )

    try:
        repo = _repo()
    except Exception as exc:
        logger.warning("gap_issues: cannot reach GitHub: %s", exc)
        repo = None

    if repo is not None:
        try:
            issue = repo.create_issue(
                title=issue_title(gap.feedback_kind, gap.title), body=body, labels=gap_labels(gap)
            )
            logger.info("gap_issues: filed #%s for %s", issue.number, gap.fingerprint)
            return GapIssueLink(
                fingerprint=gap.fingerprint,
                issue_number=int(issue.number),
                issue_url=issue.html_url,
                state="filed",
                filed_at=_now(),
                occurrences=occurrences,
                via="api",
                message=f"Filed issue #{issue.number}.",
            )
        except Exception as exc:
            logger.warning("gap_issues: GitHub API filing failed: %s", exc)
            return GapIssueLink(
                fingerprint=gap.fingerprint,
                state="failed",
                occurrences=occurrences,
                via="api",
                message=f"GitHub API filing failed ({exc}) — check GITHUB_TOKEN.",
            )

    # No token: pre-filled browser URL, exactly like submit_feedback.
    url = build_issue_url(gap.feedback_kind, "standup", gap.title, body)
    if browser_budget <= 0:
        return GapIssueLink(
            fingerprint=gap.fingerprint,
            issue_url=url,
            state="browser",
            occurrences=occurrences,
            via="browser",
            message="Too many drafts to open at once — copy this URL to file it.",
        )
    try:
        opened = webbrowser.open(url)
    except Exception as exc:
        logger.warning("gap_issues: browser open failed: %s", exc)
        opened = False
    return GapIssueLink(
        fingerprint=gap.fingerprint,
        issue_url=url,
        state="browser",
        filed_at=_now(),
        occurrences=occurrences,
        via="browser",
        message=(
            "Opened your browser with a pre-filled issue — review and press Submit there."
            if opened
            else "Couldn't open a browser — copy this URL to file the issue:"
        ),
    )


def comment_on_gap(
    issue_number: int, gap: StandupGap, review: TranscriptReview, *, occurrences: int = 1
) -> GapIssueLink:
    """Add fresh evidence to an already-filed issue. Never raises."""
    body = build_gap_comment_body(gap, review, occurrences=occurrences)
    leak = leak_check(body)
    if leak:
        return GapIssueLink(
            fingerprint=gap.fingerprint,
            issue_number=issue_number,
            state="blocked",
            occurrences=occurrences,
            message=f"Comment blocked — it still looks like it contains {leak}.",
        )

    try:
        repo = _repo()
    except Exception as exc:
        logger.warning("gap_issues: cannot reach GitHub: %s", exc)
        repo = None

    if repo is None:
        # Honest degradation: the browser path can file but cannot comment.
        return GapIssueLink(
            fingerprint=gap.fingerprint,
            issue_number=issue_number,
            state="skipped",
            occurrences=occurrences,
            message=(
                "This gap recurred, but adding a comment needs GITHUB_TOKEN. The drafted comment is saved locally."
            ),
        )

    try:
        issue = repo.get_issue(issue_number)
        if str(issue.state).lower() == "closed":
            # A closed issue was resolved or rejected; a recurrence deserves a
            # fresh one rather than a comment nobody will see.
            logger.info("gap_issues: #%s is closed — filing a fresh issue instead", issue_number)
            return file_gap(gap, review, occurrences=occurrences, previous_issue=issue_number)
        issue.create_comment(body)
        logger.info("gap_issues: commented on #%s for %s", issue_number, gap.fingerprint)
        return GapIssueLink(
            fingerprint=gap.fingerprint,
            issue_number=issue_number,
            issue_url=issue.html_url,
            state="commented",
            last_commented_at=_now(),
            occurrences=occurrences,
            via="api",
            message=f"Added fresh evidence to issue #{issue_number}.",
        )
    except Exception as exc:
        logger.warning("gap_issues: commenting on #%s failed: %s", issue_number, exc)
        return GapIssueLink(
            fingerprint=gap.fingerprint,
            issue_number=issue_number,
            state="failed",
            occurrences=occurrences,
            via="api",
            message=f"Could not comment on #{issue_number} ({exc}).",
        )


# ---------------------------------------------------------------------------
# The explicit "file these" act
# ---------------------------------------------------------------------------


def file_review_gaps(
    review: TranscriptReview,
    *,
    report: StandupReport | None = None,
    gap_ids: list[str] | None = None,
    db_path: Path | None = None,
):
    """File (or comment on) every product gap in a review. Never raises.

    This is the ONLY entry point that writes to GitHub, and it exists solely to
    be called by an explicit user act — a TUI action, ``--file-issues``, or
    ``file_issues=true``.
    """
    from yeaboi.agent.state import IssueFilingResult
    from yeaboi.paths import get_db_path
    from yeaboi.standup.store import StandupStore

    wanted = set(gap_ids or [])
    gaps = [g for g in review.gaps if not wanted or g.fingerprint in wanted]
    links: list[GapIssueLink] = []
    warnings: list[str] = []
    browser_budget = _MAX_BROWSER_TABS

    with StandupStore(db_path or get_db_path()) as store:
        for gap in gaps:
            ledger = store.get_gap_issue(gap.fingerprint) or {}
            occurrences = max(1, int(ledger.get("occurrences", 0) or 1))
            number = int(ledger.get("issue_number", 0) or 0)

            if not number:
                found = find_existing_issue(gap.fingerprint, title=gap.title)
                if found:
                    number, url, state = found
                    store.upsert_gap_issue(
                        gap.fingerprint,
                        category=gap.category,
                        title=gap.title,
                        issue_number=number,
                        issue_url=url,
                        state="filed" if state != "closed" else "closed",
                        via="api",
                        bump_occurrence=False,
                    )

            if number:
                link = comment_on_gap(number, gap, review, occurrences=occurrences)
            else:
                link = file_gap(gap, review, report=report, occurrences=occurrences, browser_budget=browser_budget)
                if link.via == "browser":
                    browser_budget -= 1

            links.append(link)
            if link.state in ("filed", "commented", "browser"):
                store.upsert_gap_issue(
                    gap.fingerprint,
                    category=gap.category,
                    title=gap.title,
                    issue_number=link.issue_number or None,
                    issue_url=link.issue_url or None,
                    state=link.state,
                    via=link.via or None,
                    filed_at=link.filed_at or None,
                    last_commented_at=link.last_commented_at or None,
                    review_id=review.review_id,
                    bump_occurrence=False,
                )
            else:
                warnings.append(link.message)

        filed = sum(1 for link in links if link.state in ("filed", "browser"))
        commented = sum(1 for link in links if link.state == "commented")
        skipped = len(links) - filed - commented
        if review.review_id:
            store.set_review_status(
                review.review_id, "filed" if links and not skipped else "partial" if links else "drafted"
            )

    logger.info("gap_issues: filing complete — %d filed, %d commented, %d skipped", filed, commented, skipped)
    return IssueFilingResult(
        review_id=review.review_id,
        links=tuple(links),
        filed=filed,
        commented=commented,
        skipped=skipped,
        warnings=tuple(dict.fromkeys(warnings)),
    )
