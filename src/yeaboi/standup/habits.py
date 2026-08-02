"""Deterministic engineering-practice signals: the habits behind the day's work.

The standup already says *what* each member did. This says something about
*how* — the process habits that quietly cost a team, most importantly work that
lands with no ticket behind it, which is sprint scope nobody agreed to and
nobody can see on the board.

Seven rules, all pure (no I/O, no LLM), all deliberately coach-shaped: each
signal is an observation plus a nudge, carrying the links to the very items it
observed, so the reader can check it in one click.

**Precision, harder than anywhere else in the codebase.** ``insights.py`` states
the rule — a false "you look blocked" erodes trust faster than a missed blocker
— and this module names a person *and* implies they did something wrong, so the
same doctrine applies with less slack:

- **Every predicate is one-sided.** A rule fires on positive evidence only, and
  concludes nothing from absence. The collectors cap their per-repo detail
  lookups (``github._MAX_CHANGED_FILE_LOOKUPS``, the Azure equivalent), swallow
  errors to empty, and never fetch changed files for local git at all — so an
  empty ``changed_paths`` means UNKNOWN, never "zero files".
- **No tracker, no tracker rules.** If ticketing coverage is ``failed`` or
  ``not_configured``, the untracked/board family is silent for everyone. You
  cannot fault someone for not linking a ticket you could not have seen.
- **Ticket-shaped is not tracked.** ``UTF-8`` matches a Jira key regex and
  ``#91`` is a GitHub PR number; the gates live in ``references.py``.
- **Naming no key is not the same as having no ticket.** Plenty of real work
  belongs to a ticket it never mentions — documentation a definition of done
  asked for, most of all. ``relatedness.py`` reads the ticket's own description,
  acceptance criteria and definition of done to answer that, and it may only
  ever SUPPRESS a signal, never create or strengthen one. The same one-sidedness
  lets an optional language-model pass ride along safely: it can stay quiet, and
  that is the only thing it can do.
- **Capped at three per member**, tail rolled into a count. One untracked PR is
  a nudge; twelve bullets is a pillory — and these also travel out over Slack
  and email, where a ten-person team would otherwise add thirty lines a day.

# See docs: "Daily Standup" — practices
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import TYPE_CHECKING

from yeaboi.agent.state import PracticeSignal
from yeaboi.standup import categories, references, relatedness

if TYPE_CHECKING:
    from yeaboi.agent.state import StandupReport

logger = logging.getLogger(__name__)

RULE_UNTRACKED_WORK = "untracked-work"
RULE_UNTRACKED_DOCS = "untracked-docs"
RULE_BOARD_NOT_UPDATED = "board-not-updated"
RULE_WIP_SPRAWL = "wip-sprawl"
RULE_LARGE_CHANGE = "large-change"
RULE_NO_PULL_REQUEST = "no-pull-request"
RULE_COMMIT_MESSAGES = "commit-messages"

ALL_RULES = (
    RULE_UNTRACKED_WORK,
    RULE_UNTRACKED_DOCS,
    RULE_BOARD_NOT_UPDATED,
    RULE_WIP_SPRAWL,
    RULE_LARGE_CHANGE,
    RULE_NO_PULL_REQUEST,
    RULE_COMMIT_MESSAGES,
)

# Short labels for the chip in every surface; the detail sentence carries the nudge.
RULE_TITLES = {
    RULE_UNTRACKED_WORK: "Untracked work",
    RULE_UNTRACKED_DOCS: "Untracked docs",
    RULE_BOARD_NOT_UPDATED: "Board out of date",
    RULE_WIP_SPRAWL: "Spread thin",
    RULE_LARGE_CHANGE: "Oversized change",
    RULE_NO_PULL_REQUEST: "Bypassed review",
    RULE_COMMIT_MESSAGES: "Thin commit messages",
}

VALID_HABIT_HANDLING = ("on", "off")

# ``(rule, change_handle) -> was this already judged wrong by the team?``. The
# same suppress-only shape as ``Adjudicator``: a truthy answer removes a report
# and there is no answer that adds one, so the feedback loop cannot make this
# module louder than its deterministic rules — only quieter.
# ``practice_feedback.Ledger.is_excused`` is the implementation.
Excuser = Callable[[str, str], bool]

_MAX_SIGNALS_PER_MEMBER = 3
_TITLE_CLIP = 60
_EVIDENCE_PER_SIGNAL = 4  # links attached to one signal

# Rule 3 (board-not-updated): the columns a merged change should have moved a
# ticket out of. EXACT matches only — no prefix families, unlike
# insights._BLOCKED_PREFIXES — because "Open Questions" and "Ready for QA" both
# start like entries here and neither means "not started".
_TODO_STATUSES = frozenset(
    {
        "to do",
        "todo",
        "to-do",
        "backlog",
        "new",
        "open",
        "ready",
        "ready for development",
        "selected for development",
        "not started",
    }
)

# Rule 4 (wip-sprawl): what counts as held-in-flight. "In Review" is deliberately
# absent — waiting on a reviewer is not the context-switching cost this measures.
_IN_PROGRESS_STATUSES = frozenset(
    {
        "in progress",
        "in-progress",
        "inprogress",
        "doing",
        "active",
        "committed",
        "in development",
        "started",
    }
)

# Kinds whose `status` describes where a ticket sits AND which are credited to
# the person holding it. Jira changelog "update" items are excluded from both
# board rules on purpose: they carry the *destination* column and are credited
# to whoever made the move, not to the assignee.
_HELD_TICKET_KINDS = frozenset({"issue", "wip", "work_item"})

# Rule 6 (large-change): paths that are generated, vendored, or lockfiles — bulk
# a human did not write and a reviewer does not read. Shaped like
# categories._DOC_DIRECTORIES: a narrow, named list rather than a heuristic.
_GENERATED_FILENAMES = frozenset(
    {"uv.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock", "gemfile.lock"}
)
_GENERATED_DIRECTORIES = frozenset({"dist", "build", "vendor", "node_modules", "__snapshots__", "generated"})
_GENERATED_SUFFIXES = (".min.js", ".min.css", ".snap", ".lock", ".map")

# Rule 7 (commit-messages): subjects that name no outcome. Exact matches after
# normalisation, so a real message that merely *starts* "fix" is untouched and a
# non-English subject simply never matches — silent, which is the right failure.
_LOW_INFORMATION_SUBJECTS = frozenset(
    {
        "fix",
        "fixes",
        "fixed",
        "fixup",
        "wip",
        "update",
        "updates",
        "updated",
        "change",
        "changes",
        "minor",
        "cleanup",
        "clean up",
        "tweak",
        "tweaks",
        "refactor",
        "test",
        "tests",
        "temp",
        "tmp",
        "stuff",
        "misc",
        "more",
        "asdf",
        "x",
        ".",
        "..",
        "...",
    }
)
_MIN_SUBJECT_CHARS = 12  # a normalised subject shorter than this says nothing either

# Thresholds. Named so the tests read as the policy they pin.
_LARGE_CHANGE_FILES = 40
_WIP_SPRAWL_TICKETS = 4
_LOOSE_COMMITS = 3
_LOW_INFORMATION_COMMITS = 3


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def enabled(config: Mapping | None) -> bool:
    """Whether practice detection runs at all (``habit_detection`` config)."""
    return str((config or {}).get("habit_detection", "on") or "on").strip().lower() != "off"


def selected_rules(config: Mapping | None) -> frozenset[str]:
    """Rules a team has opted into; empty ``habit_rules`` means all of them."""
    raw = str((config or {}).get("habit_rules", "") or "").strip()
    if not raw:
        return frozenset(ALL_RULES)
    chosen = {part.strip().lower() for part in raw.split(",") if part.strip()}
    return frozenset(chosen & set(ALL_RULES)) or frozenset(ALL_RULES)


def validate_habit_rules(raw: str) -> str:
    """Normalize a habit_rules csv, raising on an unknown rule id.

    Mirrors ``roster.validate_tracker_sources`` / ``code_scope.validate_code_sources``:
    an unrecognised value is a caller error, not something to silently drop.
    """
    parts = [part.strip().lower() for part in str(raw or "").split(",") if part.strip()]
    unknown = [part for part in parts if part not in ALL_RULES]
    if unknown:
        raise ValueError(f"unknown habit rule(s): {', '.join(unknown)}. Valid: {', '.join(ALL_RULES)}")
    # Preserve ALL_RULES order so the stored string is canonical regardless of
    # the order the caller listed them in.
    return ",".join(rule for rule in ALL_RULES if rule in set(parts))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _clip(text: str, limit: int = _TITLE_CLIP) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _norm(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _count(n: int, noun: str) -> str:
    """ "3 commits" / "1 commit" — these sentences are read by the person in them."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _label(item: Mapping) -> str:
    """Human handle for an item: key plus clipped title when both exist."""
    key = str(item.get("key") or "").strip()
    title = _clip(str(item.get("summary") or item.get("title") or ""))
    if key and title:
        return f"{key} '{title}'"
    return key or title or "an item"


def change_handle(item: Mapping) -> str:
    """A stable id for one change, so a verdict about it survives to tomorrow.

    Public because feedback is the only reason it exists: a thumbs-down remembers
    handles, and a PR can sit open for a week, so the handle a run computes today
    must be byte-identical to the one the next run computes for the same change.

    That rules out anything derived from the day's framing — position, member,
    signal — and leaves the change's own identity, in descending order of how
    much we trust it to be stable:

    1. its URL, which every hosted source gives us and none of them recycle;
    2. its repository plus PR number or commit sha / page key;
    3. failing both, a hash of the normalised subject, which is the only handle
       a local-git commit with no remote can have.

    The tail case is the weakest — two identical subjects in one repo collide —
    and it is deliberately still allowed: the cost is excusing a second change
    that reads exactly like one the team already excused, which is the same
    judgement they just made.
    """
    url = _norm(item.get("url"))
    if url:
        return f"url:{url}"
    kind = _norm(item.get("kind")) or "change"
    repo = _norm(item.get("repository"))
    ident = str(item.get("pr_id") or "").strip() or str(item.get("key") or "").strip()
    if ident:
        return f"{kind}:{repo}:{ident.lower()}"
    subject = references.normalize_commit_subject(str(item.get("summary") or item.get("title") or ""))
    digest = hashlib.sha1(subject.encode("utf-8", "replace")).hexdigest()[:16]  # noqa: S324 — an id, not a secret
    return f"{kind}:{repo}:s{digest}"


def _is_excused(rule: str, item: Mapping, feedback: Excuser | None) -> bool:
    """Did the team already say this rule was wrong about this change?

    Keyed by ``(rule, handle)``: excusing a PR for ``untracked-work`` says
    nothing about whether it is also an oversized change.
    """
    return feedback is not None and feedback(rule, change_handle(item))


def _excuse(rule: str, items: Sequence[Mapping], feedback: Excuser | None) -> list[Mapping]:
    """The changes still worth reporting for this rule.

    Every rule applies this to its *offending* list, before its threshold check
    and before its sentence is built — so "and 2 other changes" counts what
    survived, three thin commits minus one excused fall back under the
    three-commit bar, and a rule with nothing left never produces a signal at
    all rather than producing an empty one.
    """
    if feedback is None:
        return list(items)
    kept = [item for item in items if not _is_excused(rule, item, feedback)]
    if len(kept) != len(items):
        logger.info("standup: %d %s report(s) excused by earlier feedback", len(items) - len(kept), rule)
    return kept


def _evidence(items: Sequence[Mapping]) -> tuple[tuple[str, str], ...]:
    """(label, url) pairs for the items a signal is based on, deduped by url."""
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for item in items:
        url = str(item.get("url") or "").strip()
        key = str(item.get("key") or "").strip() or _clip(str(item.get("title") or ""), 40)
        if not key or (url and url in seen):
            continue
        if url:
            seen.add(url)
        pairs.append((key, url))
        if len(pairs) >= _EVIDENCE_PER_SIGNAL:
            break
    return tuple(pairs)


def _changed_paths(item: Mapping) -> tuple[str, ...]:
    """Changed paths carried past the grouping — EMPTY MEANS UNKNOWN.

    Deliberately reads ``changed_paths``, not ``changed_files``: the grouped
    dicts also feed ``categories.split_activity``, which would reclassify
    docs-only repository events if the canonical key appeared there.
    """
    return tuple(str(path) for path in (item.get("changed_paths") or ()) if path)


def _links_known(item: Mapping) -> bool:
    """Whether we could actually see this item's tracker links.

    Only Azure Repos PRs can hide a link behind the API (linked through the PR
    UI, absent from the list response), so only they set the flag; every other
    source links a ticket in text we already hold, and defaults to known.
    """
    return bool(item.get("work_items_known", True))


def _linked_work_items(item: Mapping) -> tuple[str, ...]:
    return tuple(str(wid) for wid in (item.get("work_item_ids") or ()) if str(wid).strip())


def _is_revert(subject: str) -> bool:
    return (subject or "").strip().lower().startswith("revert ")


def _belongs_to_a_pull_request(subject: str) -> bool:
    """Skip-gate for the rules about a commit's *home*: does it name a PR at all?

    Any PR reference counts, including the parenthesised "(#91)" of a squash
    merge — the commit has a home either way.
    """
    return references.claims_pull_request(subject) or _is_revert(subject)


def _is_plumbing(subject: str) -> bool:
    """Skip-gate for the rules about a commit's *message*: merges and reverts.

    Deliberately narrower than the one above. "fix login (#91)" is an authored
    subject wearing a squash-merge tail, and judging message quality has to see
    it — otherwise the rule is dead for every team that squash-merges.
    """
    return references.is_merge_subject(subject) or _is_revert(subject)


# ---------------------------------------------------------------------------
# The adjudication seam
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AdjudicationCase:
    """One still-unattributed change, with the tickets it most resembles.

    Deliberately flat text and ids: an adjudicator gets what a reviewer would
    need to answer "does this belong to one of these?" and nothing that would
    let it say anything else.
    """

    case_id: str = ""
    subject: str = ""
    branch: str = ""
    paths: tuple[str, ...] = ()
    candidates: tuple[tuple[str, str, str], ...] = ()  # (key, title, clipped text)


# Returns the ids to DROP. The return type is the whole safety argument: an
# adjudicator can only ever remove a report, so a wrong answer costs a missed
# nudge and can never produce a false one.
Adjudicator = Callable[[tuple[AdjudicationCase, ...]], Collection[str]]

_ADJUDICATION_TEXT_CLIP = 600
_ADJUDICATION_CANDIDATES = 3


def _adjudicate(
    loose_work: dict[str, list[Mapping]],
    loose_docs: dict[str, list[Mapping]],
    corpus: relatedness.TicketCorpus,
    own_keys_by_member: Mapping[str, Collection[str]],
    adjudicator: Adjudicator,
) -> tuple[dict[str, list[Mapping]], dict[str, list[Mapping]]]:
    """Offer every surviving change to the adjudicator once, as a single batch.

    One call for the whole team, not one per member: the residue is small by
    construction (only changes the deterministic pass could not place), and a
    batch keeps the cost flat as the team grows.
    """
    if not corpus:
        return loose_work, loose_docs
    cases: list[AdjudicationCase] = []
    index: dict[str, tuple[str, str, int]] = {}
    for bucket_name, bucket in (("work", loose_work), ("docs", loose_docs)):
        for name in sorted(bucket):
            own_keys = own_keys_by_member.get(name, frozenset())
            for position, item in enumerate(bucket[name]):
                case_id = f"{bucket_name}-{len(cases)}"
                profile = relatedness.build_change_profile(item)
                candidates = tuple(
                    (key, corpus.tickets[key].title, corpus.tickets[key].text[:_ADJUDICATION_TEXT_CLIP])
                    for key in relatedness.near_misses(
                        profile, corpus, own_keys=own_keys, limit=_ADJUDICATION_CANDIDATES
                    )
                )
                if not candidates:
                    continue  # nothing to weigh it against; do not spend a slot
                cases.append(
                    AdjudicationCase(
                        case_id=case_id,
                        subject=str(item.get("summary") or item.get("title") or ""),
                        branch=str(item.get("branch") or ""),
                        paths=_reviewable_paths(item)[:10],
                        candidates=candidates,
                    )
                )
                index[case_id] = (bucket_name, name, position)
    if not cases:
        return loose_work, loose_docs

    try:
        dropped = {str(case_id) for case_id in adjudicator(tuple(cases))}
    except Exception:  # an adjudicator failing must never cost the whole report
        logger.warning("standup: practice adjudication failed — keeping every deterministic verdict", exc_info=True)
        return loose_work, loose_docs
    # Ids we did not send are discarded rather than trusted.
    dropped &= set(index)
    if not dropped:
        return loose_work, loose_docs
    logger.info("standup: adjudication dropped %d untracked-change report(s)", len(dropped))

    remove: dict[tuple[str, str], set[int]] = {}
    for case_id in dropped:
        bucket_name, name, position = index[case_id]
        remove.setdefault((bucket_name, name), set()).add(position)
    out: list[dict[str, list[Mapping]]] = []
    for bucket_name, bucket in (("work", loose_work), ("docs", loose_docs)):
        out.append(
            {
                name: [item for i, item in enumerate(items) if i not in remove.get((bucket_name, name), ())]
                for name, items in bucket.items()
            }
        )
    return out[0], out[1]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_practices(
    grouped: Mapping[str, Sequence[Mapping]],
    *,
    config: Mapping | None = None,
    category_coverage: Sequence[tuple[str, str]] | Mapping[str, str] = (),
    previous_report: StandupReport | None = None,
    reference_grouped: Mapping[str, Sequence[Mapping]] | None = None,
    reference_items: Sequence[Mapping] = (),
    adjudicator: Adjudicator | None = None,
    feedback: Excuser | None = None,
) -> dict[str, tuple[PracticeSignal, ...]]:
    """Per-member practice signals (members with none are absent).

    ``grouped`` is ``engine._group_activity_by_author``'s output, which now also
    carries ``body`` and ``changed_paths`` so the rules can read a PR's
    description and its file list.

    ``reference_items`` are open tickets nobody necessarily touched today,
    fetched purely as matching context, and ``reference_grouped`` is the same
    set keyed by the member who holds each one. They never become activity and
    never appear in a report — they only ever let a change be recognised as
    belonging somewhere, which is why passing more of them can only make this
    function quieter.

    ``adjudicator`` is an optional seam (``engine`` supplies the language-model
    one) that sees the changes still reported as untracked and may drop further
    ones. Suppress-only by type: it returns ids to remove, so there is no
    channel through which it could invent or sharpen a report.

    ``feedback`` is the team's own verdicts (``practice_feedback.Ledger``), and
    the same shape for the same reason: it answers "was this rule wrong about
    this change?" and a yes removes a report. Applied before the adjudicator, so
    a change the team already excused never costs a language-model slot on a
    question they have answered.
    """
    if not enabled(config):
        return {}
    rules = selected_rules(config)

    coverage = dict(category_coverage)  # accepts the report's tuple-of-pairs or a dict
    # The kill switch: no usable tracker, no tracker-shaped accusations.
    ticketing = coverage.get(categories.CATEGORY_TICKETING, categories.COVERED)
    tracker_usable = ticketing not in (categories.FAILED, categories.NOT_CONFIGURED)

    all_items = [item for items in grouped.values() for item in items]
    # The gates read the open tickets too, not just today's board movement.
    # Whether "PROJ-12" in a commit subject is a real key is a fact about the
    # tracker, not about whether anyone happened to touch a ticket today.
    gate_items = [*all_items, *reference_items]
    prefixes = references.tracker_prefixes(gate_items)
    work_item_ids = references.tracker_work_item_ids(gate_items)
    ticket_status = _ticket_status_index(all_items)
    previous = _previous_signal_rules(previous_report)

    reference_grouped = reference_grouped or {}
    # Only the tracker-shaped rules consult it, so an unusable tracker skips the
    # tokenising entirely rather than indexing text nothing will read.
    corpus = relatedness.build_corpus(all_items, reference_items) if tracker_usable else relatedness.TicketCorpus()
    loose_work: dict[str, list[Mapping]] = {}
    loose_docs: dict[str, list[Mapping]] = {}
    if tracker_usable:
        # A member's own tickets are the ones they touched today plus the ones
        # they hold open — the pool where ordinary word overlap is trustworthy
        # enough to act on. Everyone else's tickets stay in the corpus but only
        # the strong predicates may reach them.
        own_keys_by_member = {
            name: relatedness.ticket_keys(items) | relatedness.ticket_keys(reference_grouped.get(name, ()))
            for name, items in grouped.items()
        }
        for name, items in grouped.items():
            own_keys = own_keys_by_member[name]
            if RULE_UNTRACKED_WORK in rules:
                loose_work[name] = _excuse(
                    RULE_UNTRACKED_WORK,
                    _loose_untracked_work(
                        items, prefixes=prefixes, work_item_ids=work_item_ids, corpus=corpus, own_keys=own_keys
                    ),
                    feedback,
                )
            if RULE_UNTRACKED_DOCS in rules:
                loose_docs[name] = _excuse(
                    RULE_UNTRACKED_DOCS,
                    _loose_untracked_docs(
                        items, prefixes=prefixes, work_item_ids=work_item_ids, corpus=corpus, own_keys=own_keys
                    ),
                    feedback,
                )
        if adjudicator is not None:
            loose_work, loose_docs = _adjudicate(loose_work, loose_docs, corpus, own_keys_by_member, adjudicator)

    signals: dict[str, tuple[PracticeSignal, ...]] = {}
    for name, items in grouped.items():
        found: list[PracticeSignal] = []
        if tracker_usable:
            if RULE_UNTRACKED_WORK in rules:
                found.extend(_untracked_work_signal(loose_work.get(name, ()), checked=bool(corpus)))
            if RULE_UNTRACKED_DOCS in rules:
                found.extend(_untracked_docs_signal(loose_docs.get(name, ()), checked=bool(corpus)))
            if RULE_BOARD_NOT_UPDATED in rules:
                found.extend(
                    _board_not_updated(
                        items,
                        prefixes=prefixes,
                        work_item_ids=work_item_ids,
                        statuses=ticket_status,
                        feedback=feedback,
                    )
                )
            if RULE_WIP_SPRAWL in rules:
                found.extend(_wip_sprawl(items, feedback=feedback))
        if RULE_LARGE_CHANGE in rules:
            found.extend(_large_change(items, feedback=feedback))
        if RULE_NO_PULL_REQUEST in rules:
            found.extend(_no_pull_request(items, feedback=feedback))
        if RULE_COMMIT_MESSAGES in rules:
            found.extend(_commit_messages(items, feedback=feedback))
        if found:
            signals[name] = tuple(_mark_repeats(found[:_MAX_SIGNALS_PER_MEMBER], previous.get(name, frozenset())))

    if signals:
        logger.info(
            "standup: %d practice signal(s) across %d member(s)%s",
            sum(len(v) for v in signals.values()),
            len(signals),
            "" if tracker_usable else " (tracker rules suppressed — ticketing coverage unusable)",
        )
    return signals


def rollup(signals: Mapping[str, Sequence]) -> tuple[tuple[str, int], ...]:
    """(rule, member count) for the overview, in ALL_RULES order.

    Counts *members*, not signals: "3 members have untracked work" is the team
    fact worth acting on, while a raw signal count would read as a scoreboard.
    """
    counts: dict[str, set[str]] = {}
    for name, member_signals in signals.items():
        for signal in member_signals:
            counts.setdefault(getattr(signal, "rule", ""), set()).add(name)
    return tuple((rule, len(counts[rule])) for rule in ALL_RULES if rule in counts)


def _signal(rule: str, detail: str, evidence: Sequence[Mapping]) -> PracticeSignal:
    # ``handles`` covers every item, ``evidence`` only the first few links: a
    # thumbs-down has to remember the changes it silences, including the ones
    # the sentence rolled into "and 2 others".
    return PracticeSignal(
        rule=rule,
        title=RULE_TITLES.get(rule, rule.replace("-", " ").capitalize()),
        detail=detail,
        evidence=_evidence(evidence),
        handles=tuple(dict.fromkeys(change_handle(item) for item in evidence)),
    )


def _previous_signal_rules(previous_report: StandupReport | None) -> dict[str, frozenset[str]]:
    """Per-member ``rule`` ids that already fired in the previous standup."""
    if previous_report is None:
        return {}
    out: dict[str, frozenset[str]] = {}
    for member in previous_report.member_updates:
        rules = {s.rule for s in getattr(member, "practices", ()) or () if getattr(s, "rule", "")}
        if rules:
            out[member.name] = frozenset(rules)
    return out


def _mark_repeats(found: Sequence[PracticeSignal], previous_rules: Collection[str]) -> list[PracticeSignal]:
    """Flag signals whose rule also fired yesterday — a pattern, not a one-off."""

    return [dataclasses.replace(s, repeat=True) if s.rule in previous_rules else s for s in found]


def _ticket_status_index(items: Sequence[Mapping]) -> dict[str, str]:
    """Team-wide ticket key → current status, newest observation winning.

    Built from held-ticket kinds only. A Jira ``update`` item carries the column
    a ticket moved *to*, which is the same information, but it is credited to
    whoever moved it — including it would let a teammate's board move mask the
    assignee's stale ticket.
    """
    index: dict[str, str] = {}
    stamps: dict[str, str] = {}
    for item in items:
        if item.get("kind") not in _HELD_TICKET_KINDS:
            continue
        key = str(item.get("key") or "").strip()
        status = str(item.get("status") or "").strip()
        if not key or not status:
            continue
        # ISO-8601 strings sort chronologically; carried WIP has an empty stamp
        # and so never displaces a dated observation.
        stamp = str(item.get("timestamp") or "")
        if key not in index or stamp >= stamps.get(key, ""):
            index[key] = status
            stamps[key] = stamp
    return index


def _referenced_keys(item: Mapping, *, prefixes: Collection[str], work_item_ids: Collection[str]) -> tuple[str, ...]:
    """Ticket handles this change names, across every place a team might put one."""
    haystacks = (
        str(item.get("title") or ""),
        str(item.get("branch") or ""),
        str(item.get("body") or ""),
    )
    keys: list[str] = []
    for text in haystacks:
        keys.extend(references.gated_ticket_keys(text, prefixes=prefixes))
        keys.extend(references.AZDO_REF_RE.findall(text))
        keys.extend(match for match in references.BARE_ID_RE.findall(text) if match in work_item_ids)
    keys.extend(_linked_work_items(item))
    return tuple(dict.fromkeys(keys))


def _has_reference(item: Mapping, *, prefixes: Collection[str], work_item_ids: Collection[str]) -> bool:
    if _linked_work_items(item):
        return True
    return references.has_tracker_reference(
        str(item.get("title") or ""),
        str(item.get("branch") or ""),
        str(item.get("body") or ""),
        prefixes=prefixes,
        work_item_ids=work_item_ids,
    )


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


def _is_docs_only(item: Mapping) -> bool:
    """Whether every reviewable path in this change is documentation.

    Empty means UNKNOWN, so it is NOT docs-only: the documentation carve-out
    lowers the matching bar, and lowering it on a guess is the one direction
    this module cannot afford.
    """
    paths = _reviewable_paths(item)
    return bool(paths) and all(categories.is_documentation_path(path) for path in paths)


def _loose_untracked_work(
    items: Sequence[Mapping],
    *,
    prefixes: Collection[str],
    work_item_ids: Collection[str],
    corpus: relatedness.TicketCorpus,
    own_keys: Collection[str],
) -> list[Mapping]:
    """Code that landed with no ticket behind it — sprint scope nobody can see.

    Two units: a PR (the natural unit of a change), and a commit that claims no
    PR at all. Merge and revert subjects are skipped even when their PR is
    outside the window — textual evidence of a PR is enough here, unlike
    ``engine._nest_pr_commits``, which needs a real parent to fold under.

    A change that names no key but reads like an existing ticket's description,
    acceptance criteria or definition of done is dropped silently. Silently, and
    without recording which ticket: the reader never sees the guess, so guessing
    the wrong sibling ticket in an epic costs nothing.
    """
    loose: list[Mapping] = []
    for item in items:
        kind = item.get("kind")
        if kind == "pr":
            # An Azure PR whose links we couldn't read is UNKNOWN, not unlinked.
            if not _links_known(item):
                continue
        elif kind == "commit":
            if _belongs_to_a_pull_request(str(item.get("title") or "")):
                continue
            # Local-git commits carry no repository, so they can never be
            # matched to a PR — treating them as loose would fire on every
            # commit of anyone who configured a local repo path.
            if not str(item.get("repository") or "").strip():
                continue
        else:
            continue
        if _has_reference(item, prefixes=prefixes, work_item_ids=work_item_ids):
            continue
        profile = relatedness.build_change_profile(item, docs_only=_is_docs_only(item))
        if relatedness.relates_to_ticket(profile, corpus, own_keys=own_keys):
            continue
        loose.append(item)
    return loose


def _untracked_work_signal(loose: Sequence[Mapping], *, checked: bool) -> list[PracticeSignal]:
    if not loose:
        return []
    # Lead with a PR when there is one — it is the reviewable unit, and naming a
    # loose commit first would read as nit-picking a change that has a home.
    prs = [i for i in loose if i.get("kind") == "pr"]
    head = _label(prs[0] if prs else loose[0])
    if len(loose) > 1:
        subject = f"{head} and {_count(len(loose) - 1, 'other change')} carry"
    else:
        subject = f"{head} carries"
    # Never claim a check that did not run: with no tickets in the window there
    # was nothing to match against, and the longer sentence would be a lie.
    if checked:
        detail = (
            f"{subject} no ticket reference — no key in the branch, title, or description, and no "
            "wording or file path that matches a ticket the team has open. Link a ticket (or raise "
            "one) so the work counts toward sprint scope."
        )
    else:
        detail = (
            f"{subject} no ticket reference in the branch, title, or description. "
            "Link a ticket (or raise one) so the work counts toward sprint scope."
        )
    return [_signal(RULE_UNTRACKED_WORK, detail, loose)]


def _loose_untracked_docs(
    items: Sequence[Mapping],
    *,
    prefixes: Collection[str],
    work_item_ids: Collection[str],
    corpus: relatedness.TicketCorpus,
    own_keys: Collection[str],
) -> list[Mapping]:
    """Documentation written outside the board — the same scope leak, quieter."""
    loose: list[Mapping] = []
    seen_keys: set[str] = set()
    created_keys = {str(i.get("key") or "") for i in items if i.get("kind") == "page-created"}
    for item in items:
        kind = item.get("kind")
        if kind not in ("page", "page-created"):
            continue
        key = str(item.get("key") or "").strip()
        # Confluence emits one item per editor and a separate created/edited
        # pair for a new page; one page is one observation either way.
        if kind == "page" and key in created_keys:
            continue
        if key and key in seen_keys:
            continue
        seen_keys.add(key)
        haystack = (str(item.get("summary") or ""), str(item.get("title") or ""), str(item.get("body") or ""))
        if references.has_tracker_reference(*haystack, prefixes=prefixes, work_item_ids=work_item_ids):
            continue
        # A page IS documentation, so it always gets the definition-of-done bar.
        profile = relatedness.build_change_profile(item, docs_only=True)
        if relatedness.relates_to_ticket(profile, corpus, own_keys=own_keys):
            continue
        loose.append(item)
    return loose


def _untracked_docs_signal(loose: Sequence[Mapping], *, checked: bool) -> list[PracticeSignal]:
    if not loose:
        return []
    head = _label(loose[0])
    if len(loose) > 1:
        subject = f"{head} and {_count(len(loose) - 1, 'other page')} have"
    else:
        subject = f"{head} has"
    if checked:
        detail = (
            f"{subject} no ticket reference, and nothing in them matches a ticket the team has open "
            "— including tickets whose definition of done covers documentation. Documentation effort "
            "is real effort — tie it to a ticket so it shows up in the sprint."
        )
    else:
        detail = (
            f"{subject} no ticket reference. Documentation effort is real effort — "
            "tie it to a ticket so it shows up in the sprint."
        )
    return [_signal(RULE_UNTRACKED_DOCS, detail, loose)]


def _board_not_updated(
    items: Sequence[Mapping],
    *,
    prefixes: Collection[str],
    work_item_ids: Collection[str],
    statuses: Mapping[str, str],
    feedback: Excuser | None = None,
) -> list[PracticeSignal]:
    """Something shipped against a ticket still parked in a not-started column.

    Merged changes only: an open PR against a To Do ticket is an ordinary
    morning, and firing on it would make the rule noise.
    """
    stale: dict[str, Mapping] = {}
    for item in items:
        if item.get("kind") != "pr" or _norm(item.get("status")) != "merged":
            continue
        for key in _referenced_keys(item, prefixes=prefixes, work_item_ids=work_item_ids):
            for candidate in (key, f"#{key}"):
                if _norm(statuses.get(candidate, "")) in _TODO_STATUSES and candidate not in stale:
                    stale[candidate] = item

    stale = {k: i for k, i in stale.items() if not _is_excused(RULE_BOARD_NOT_UPDATED, i, feedback)}
    if not stale:
        return []
    keys = sorted(stale)
    head = ", ".join(keys[:3])
    return [
        _signal(
            RULE_BOARD_NOT_UPDATED,
            f"{head} shipped in a merged pull request but the board still has it not started. "
            "Move the ticket so the sprint reflects what actually landed.",
            list(stale.values()),
        )
    ]


def _wip_sprawl(items: Sequence[Mapping], *, feedback: Excuser | None = None) -> list[PracticeSignal]:
    """Too many tickets held open at once — the cost is switching, not effort.

    Counts held tickets by *status*, not by ``kind == "wip"``: Jira's WIP query
    skips any issue already returned by the updated-in-window search, so a
    member actively touching five in-progress tickets today has no ``wip`` items
    at all. Counting those alone would fire the rule only on people who touched
    nothing — exactly backwards.
    """
    held: dict[str, Mapping] = {}
    for item in items:
        if item.get("kind") not in _HELD_TICKET_KINDS:
            continue
        if _norm(item.get("status")) not in _IN_PROGRESS_STATUSES:
            continue
        key = str(item.get("key") or "").strip()
        if key:
            held.setdefault(key, item)

    held = {k: i for k, i in held.items() if not _is_excused(RULE_WIP_SPRAWL, i, feedback)}
    if len(held) < _WIP_SPRAWL_TICKETS:
        return []
    keys = sorted(held)
    return [
        _signal(
            RULE_WIP_SPRAWL,
            f"{_count(len(held), 'ticket')} in progress at once ({', '.join(keys[:4])}"
            f"{'…' if len(keys) > 4 else ''}). Finishing one beats starting another.",
            list(held.values()),
        )
    ]


def _reviewable_paths(item: Mapping) -> tuple[str, ...]:
    """Changed paths a human would actually review: no lockfiles, no generated bulk."""
    out: list[str] = []
    for path in _changed_paths(item):
        normalized = path.replace("\\", "/").strip("/").lower()
        if not normalized:
            continue
        parts = normalized.split("/")
        if parts[-1] in _GENERATED_FILENAMES or normalized.endswith(_GENERATED_SUFFIXES):
            continue
        if any(part in _GENERATED_DIRECTORIES for part in parts[:-1]):
            continue
        out.append(path)
    return tuple(out)


def _large_change(items: Sequence[Mapping], *, feedback: Excuser | None = None) -> list[PracticeSignal]:
    """A pull request too big to review honestly.

    ``kind == "pr"`` only. Review items carry the *reviewed* PR's file list
    (github.py attaches it to every review and review comment), so any wider
    predicate would bill the reviewer for the author's sprawling change.
    Never fires on an empty path list: the collectors cap detail lookups, so
    empty means unknown.
    """
    big: list[Mapping] = []
    for item in items:
        if item.get("kind") != "pr":
            continue
        paths = _reviewable_paths(item)
        if len(paths) < _LARGE_CHANGE_FILES:
            continue
        # A big docs-only change is a different animal from a big code change.
        if all(categories.is_documentation_path(path) for path in paths):
            continue
        big.append(item)

    big = _excuse(RULE_LARGE_CHANGE, big, feedback)
    if not big:
        return []
    counts = ", ".join(f"{_label(item)} ({len(_reviewable_paths(item))} files)" for item in big[:2])
    return [
        _signal(
            RULE_LARGE_CHANGE,
            f"{counts} — changes this size are hard to review well. "
            "Splitting them gets sharper review and lands sooner.",
            big,
        )
    ]


def _no_pull_request(items: Sequence[Mapping], *, feedback: Excuser | None = None) -> list[PracticeSignal]:
    """Commits that bypassed a review flow the member clearly uses elsewhere.

    Gated per repository on the member having opened a PR in that same repo in
    this window: that is the proof the repo reviews via PRs at all. A repo that
    genuinely commits straight to the trunk is a team decision, not a habit.
    """
    pr_repos = {str(i.get("repository") or "") for i in items if i.get("kind") == "pr"}
    pr_repos.discard("")
    if not pr_repos:
        return []

    loose: dict[str, list[Mapping]] = {}
    seen: set[str] = set()
    for item in items:
        if item.get("kind") != "commit":
            continue
        repo = str(item.get("repository") or "")
        if repo not in pr_repos:
            continue
        if _belongs_to_a_pull_request(str(item.get("title") or "")):
            continue
        key = str(item.get("key") or "")
        if key and key in seen:
            continue
        seen.add(key)
        loose.setdefault(repo, []).append(item)

    # Excused before the per-repo threshold, so excusing one of three loose
    # commits takes the repo back under the bar rather than shortening a
    # sentence that should no longer be there.
    offenders = {
        repo: kept
        for repo, commits in loose.items()
        if len(kept := _excuse(RULE_NO_PULL_REQUEST, commits, feedback)) >= _LOOSE_COMMITS
    }
    if not offenders:
        return []
    repo, commits = next(iter(sorted(offenders.items())))
    return [
        _signal(
            RULE_NO_PULL_REQUEST,
            f"{_count(len(commits), 'commit')} landed in {repo} without a pull request, in a repo "
            "where you opened one today. Even a small PR gets the change a second pair of eyes.",
            commits,
        )
    ]


def _is_low_information(subject: str) -> tuple[bool, str]:
    """Whether a commit subject names no outcome, plus the normalised text."""
    text = references.normalize_commit_subject(subject)
    # Conventional-commit prefix: "fix: null-deref in auth" is a fine message,
    # "fix:" is not — judge what comes after the colon.
    if ":" in text:
        head, _, tail = text.partition(":")
        if head and " " not in head.strip() and tail.strip():
            text = tail.strip()
    # A subject that is only a ticket key is tracked work but still says nothing
    # about what changed.
    for key in references.find_ticket_keys(text):
        text = text.replace(key, " ")
    text = " ".join(text.split())
    normalized = text.lower().strip(" .-_")
    if not normalized:
        return True, text
    return (normalized in _LOW_INFORMATION_SUBJECTS or len(normalized) < _MIN_SUBJECT_CHARS), text


def _commit_messages(items: Sequence[Mapping], *, feedback: Excuser | None = None) -> list[PracticeSignal]:
    """Several commits whose subjects tell a future reader nothing."""
    # Item and its normalised subject travel together: the sentence quotes the
    # subjects of the commits it reports, and excusing one has to take its quote
    # with it or the two lists silently fall out of step.
    thin: list[tuple[Mapping, str]] = []
    seen: set[str] = set()
    for item in items:
        if item.get("kind") != "commit":
            continue
        subject = str(item.get("title") or "")
        if not subject.strip() or _is_plumbing(subject):
            continue
        key = str(item.get("key") or "")
        if key and key in seen:
            continue
        seen.add(key)
        low, normalized = _is_low_information(subject)
        if low:
            thin.append((item, normalized))

    thin = [pair for pair in thin if not _is_excused(RULE_COMMIT_MESSAGES, pair[0], feedback)]
    if len(thin) < _LOW_INFORMATION_COMMITS:
        return []
    samples = [normalized for _, normalized in thin if normalized]
    quoted = ", ".join(f"'{_clip(s, 24)}'" for s in samples[:3])
    return [
        _signal(
            RULE_COMMIT_MESSAGES,
            f"{_count(len(thin), 'commit')} have subjects that name no outcome ({quoted}). "
            "A subject that says what changed saves the next reader — often you — a bisect.",
            [item for item, _ in thin],
        )
    ]
