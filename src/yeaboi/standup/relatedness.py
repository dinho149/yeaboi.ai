"""Does this change plausibly belong to a ticket it never names?

``references.py`` answers a syntactic question — does this text *say* a ticket
key, under a gate the tracker produced — and its ``True`` is strong enough that
``export.py`` turns it into a hyperlink. This module answers a softer one, and
its ``True`` is only ever safe to stay quiet with. They are deliberately
separate modules: one truth value that linkifies and one that merely suppresses
must not live behind the same import, or someone eventually linkifies a guess.

**The governing invariant: relatedness may only ever SUPPRESS a practice
signal, never create or strengthen one.** Every predicate here is one-sided in
that direction, and ``habits.py`` calls them only to *drop* a change from a
report. A wrong match costs a missed nudge. A wrong accusation costs trust in a
message that names a person. That asymmetry decides every threshold below, and
it is why the matched ticket is never surfaced: if the reader never sees which
ticket we guessed, guessing the wrong sibling ticket costs exactly nothing.

Two tiers, because a match is only as trustworthy as its candidate pool:

- **Tier A — tickets this member touched today, or holds open.** Every predicate
  applies, including the word-overlap ones.
- **Tier B — every other ticket in the window.** Only the strong predicates:
  the ticket naming the change outright, or a rare compound identifier. Tier B
  exists because a lead who pushes a fix on someone else's ticket, without ever
  touching the ticket, would otherwise be reported for untracked work.

**Why bare token overlap would be fatal here.** ``poker/context.py`` matches
titles with ``len(a & b) >= 2``, which is calibrated for two ~8-token documents.
Against a 300-word description, two shared content words is near-certain for
*any* pair. Worse, the text this module reads is the most repetitive text in a
tracker: a definition-of-done block is boilerplate copied onto every ticket in
the project ("documentation", "testing", "merged", "sign-off"). A naive matcher
would match everything to everything through that boilerplate alone, and the
untracked-work rule would go silent forever with nobody noticing why. Three
defences, in order of how much work they do:

1. **Rarity.** A token counts only if it appears in at most a quarter of the
   window's tickets. Boilerplate has a document frequency of *all of them* by
   construction, so it self-cancels with no hand-maintained stoplist.
2. **Coverage is denominated on the CHANGE's tokens, never the ticket's.** A
   commit subject has three to eight content words, and asking that most of
   *those* land is a real constraint a long description does not automatically
   satisfy. Denominating on the ticket would make long tickets unmatchable;
   counting raw matches makes them universally matchable.
3. **A ceiling on ticket size.** Above ``_HUGE_TICKET_TOKENS`` the word
   predicates are inadmissible outright. A 400-token ticket is a document, and
   shared words say nothing about a document.

# See docs: "Daily Standup" — practices
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from yeaboi.standup import references

# --- tokenizer -------------------------------------------------------------

# Content words: four characters or more, so "fix"/"the"/"api" never carry a
# match on their own.
_WORD_RE = re.compile(r"[a-z][a-z0-9]{3,}")
# A compound a human deliberately typed as ONE token: pipeline-approval,
# access_request, standup/habits, foo.bar. This is the high-signal class.
_COMPOUND_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-_./][A-Za-z0-9]+)+")
# camelCase / PascalCase, canonicalised to the same hyphenated form. The
# leading character may be either case — `PipelineApproval` and
# `pipelineApproval` are the same identifier to everyone except a regex.
_CAMEL_RE = re.compile(r"[A-Za-z][a-z0-9]*(?:[A-Z][a-z0-9]+)+")
_SPLIT_RE = re.compile(r"[-_./]+")
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# Word occurrences with their offsets, for the ticket-side bigram pass.
_RUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")

_MIN_IDENT_PART_CHARS = 3
_MIN_IDENT_CHARS = 9

# Structural English plus the words every engineering tracker repeats. The
# rarity gate handles project-specific boilerplate on its own; this list only
# has to cover the small-corpus case (two or three tickets in a window) where
# document frequency carries almost no information — weekends, holidays.
_STOPWORDS = frozenset(
    {
        "this",
        "that",
        "with",
        "from",
        "into",
        "when",
        "then",
        "than",
        "them",
        "they",
        "their",
        "there",
        "should",
        "would",
        "could",
        "have",
        "will",
        "been",
        "being",
        "does",
        "done",
        "make",
        "made",
        "also",
        "only",
        "some",
        "such",
        "each",
        "both",
        "must",
        "need",
        "needs",
        "want",
        "wants",
        "the",
        "and",
        "for",
        "are",
        "was",
        "not",
        "use",
        "its",
        "via",
        "per",
        "new",
        "any",
        "all",
        "our",
        "page",
        "ticket",
        "story",
        "task",
        "issue",
        "item",
        "items",
        "work",
        "sprint",
        "board",
        "code",
        "file",
        "files",
        "change",
        "changes",
        "update",
        "updates",
        "user",
        "users",
        "test",
        "tests",
        "testing",
        "documentation",
        "docs",
        "review",
        "reviewed",
        "merged",
        "merge",
        "acceptance",
        "criteria",
        "definition",
        "released",
        "release",
        "sign",
        "stakeholder",
        "knowledge",
        "sharing",
        "given",
        "when",
        "then",
        "should",
        "able",
    }
)

# --- thresholds ------------------------------------------------------------

# A token is "rare" when at most this share of the window's tickets mention it.
# Self-tuning: with four tickets the bar is one, with forty it is ten.
_RARE_DF_RATIO = 0.25
# Above this many body tokens, word predicates are inadmissible entirely.
_HUGE_TICKET_TOKENS = 300

_TITLE_COVERAGE = 0.50
_SUBJECT_COVERAGE = 0.60
_BRANCH_COVERAGE = 0.60
_DOCS_COVERAGE = 0.34

_MIN_CHANGE_TOKENS = 3
_MIN_RARE_WORDS = 3
_MIN_RARE_WORDS_TITLE = 2
_MIN_RARE_WORDS_BRANCH = 2
_MIN_RARE_WORDS_DOCS = 1

_MIN_PATH_TOKEN_CHARS = 5
_PATH_RARE_HITS = 2
# A forty-file pull request offers forty basenames, so accidental suppression
# gets steadily likelier on exactly the change you most want reported. Past this
# many files the bar goes up rather than staying flat.
_PATH_SHOTGUN_MAX = 25
_PATH_RARE_HITS_SHOTGUN = 3
_MIN_SHA_CHARS = 7
# Bounds the per-change work. Hitting the cap can only LOSE a match, i.e. only
# produce a report — the safe direction.
_MAX_CANDIDATES_PER_CHANGE = 8
_MAX_TICKET_BODY_CHARS = 4000

# Branch-name segments that name a workflow, not the work: strip before reading
# the slug. The segment following a "users"-family namespace is an author name
# and goes too.
_BRANCH_NAMESPACES = frozenset(
    {"feature", "feat", "fix", "bugfix", "hotfix", "chore", "release", "refactor", "docs", "doc", "test", "dev"}
)
_BRANCH_ACTOR_NAMESPACES = frozenset({"users", "user", "personal"})

# Basenames so common they identify nothing.
_GENERIC_BASENAMES = frozenset(
    {
        "index",
        "main",
        "utils",
        "util",
        "types",
        "init",
        "test",
        "tests",
        "conftest",
        "setup",
        "readme",
        "config",
        "const",
        "constants",
        "helpers",
        "common",
        "base",
    }
)

# A checklist-shaped documentation line in a definition of done. Matches
# "- [ ] Documentation", "* Docs updated", "Definition of done: user guide" —
# and deliberately not the word "documented" inside a prose sentence, because
# this gate is what keeps the relaxed documentation bar honest.
_DOD_DOC_RE = re.compile(
    r"(?im)^\s*(?:[-*+]\s*)?(?:\[[ xX]?\]\s*)?"
    r"(?:definition of done\s*:?\s*)?"
    r"(documentation|docs\b|user guide|runbook|release notes|update (?:the )?docs)",
)

# References a ticket might use to name a change.
_URL_RE = re.compile(r"https?://\S+")
_NUM_RE = re.compile(r"[#!](\d+)")
_SHA_RE = re.compile(r"\b([0-9a-f]{7,40})\b", re.IGNORECASE)

# Kinds that carry a ticket's own text. ``ticket_context`` is the open-ticket
# matching context the collector fetches separately; it is never activity.
_TICKET_KINDS = frozenset({"issue", "wip", "work_item", "update", "comment", "ticket_context"})


def _canonical_ident(raw: str) -> str:
    """``PipelineApproval`` / ``pipeline_approval`` / ``pipeline.approval`` → ``pipeline-approval``.

    Returns "" for anything that is not identifier-shaped enough to carry a
    match on its own: fewer than two meaningful parts, or too short overall.
    """
    pieces: list[str] = []
    for chunk in _SPLIT_RE.split(raw):
        pieces.extend(_CAMEL_SPLIT_RE.split(chunk))
    parts = [p.lower() for p in pieces if len(p) >= _MIN_IDENT_PART_CHARS and not p.isdigit()]
    if len(parts) < 2:
        return ""
    ident = "-".join(parts)
    return ident if len(ident) >= _MIN_IDENT_CHARS else ""


def _words(text: str) -> frozenset[str]:
    return frozenset(w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOPWORDS)


def _idents(text: str) -> frozenset[str]:
    """Compound identifiers a human typed as one token."""
    out: set[str] = set()
    for match in _COMPOUND_RE.findall(text or ""):
        ident = _canonical_ident(match)
        if ident:
            out.add(ident)
    for match in _CAMEL_RE.findall(text or ""):
        ident = _canonical_ident(match)
        if ident:
            out.add(ident)
    return frozenset(out)


def _bigrams(text: str) -> frozenset[str]:
    """Adjacent word pairs — TICKET SIDE ONLY.

    This asymmetry is the mechanism behind the whole module. The change side
    must be identifier-precise: a human deliberately wrote ``pipeline-approval``
    as one token, or a path made it one. The ticket side is prose written by
    whoever raised it, who will type "the pipeline approval plugin" in words.
    Reading bigrams from the ticket lets those meet, while a *symmetric* bigram
    pass would let two unrelated prose documents collide on ordinary phrasing.
    """
    out: set[str] = set()
    previous: re.Match[str] | None = None
    for match in _RUN_RE.finditer(text or ""):
        if previous is not None and (text[previous.end() : match.start()] == " "):
            first, second = previous.group().lower(), match.group().lower()
            if (
                len(first) >= _MIN_IDENT_PART_CHARS
                and len(second) >= _MIN_IDENT_PART_CHARS
                and first not in _STOPWORDS
                and second not in _STOPWORDS
            ):
                ident = f"{first}-{second}"
                if len(ident) >= _MIN_IDENT_CHARS:
                    out.add(ident)
        previous = match
    return frozenset(out)


def _branch_tokens(branch: str) -> tuple[frozenset[str], frozenset[str]]:
    """(words, idents) from a branch name, with workflow namespaces stripped."""
    slug = (branch or "").strip().strip("/")
    if not slug:
        return frozenset(), frozenset()
    segments = slug.split("/")
    while segments:
        head = segments[0].lower()
        if head in _BRANCH_NAMESPACES and len(segments) > 1:
            segments = segments[1:]
        elif head in _BRANCH_ACTOR_NAMESPACES and len(segments) > 2:
            segments = segments[2:]  # the namespace AND the author name after it
        else:
            break
    remainder = "/".join(segments)
    # A ticket key in the branch is handled by references.py, not by wording.
    for key in references.find_ticket_keys(remainder.upper()):
        remainder = re.sub(re.escape(key), " ", remainder, flags=re.IGNORECASE)
    idents = _idents(remainder)
    ident = _canonical_ident(remainder.replace("/", "-"))
    if ident:
        idents = idents | {ident}
    return _words(remainder), idents


def _path_tokens(paths: Sequence[str]) -> frozenset[str]:
    """Distinctive tokens from changed paths: basename stems, directories, modules."""
    out: set[str] = set()
    for path in paths:
        normalized = str(path).replace("\\", "/").strip("/")
        if not normalized:
            continue
        parts = normalized.split("/")
        stem = parts[-1].rsplit(".", 1)[0].lower()
        if len(stem) >= _MIN_PATH_TOKEN_CHARS and stem not in _GENERIC_BASENAMES and stem not in _STOPWORDS:
            out.add(stem)
        for directory in parts[-3:-1]:
            token = directory.lower()
            if len(token) >= _MIN_PATH_TOKEN_CHARS and token not in _GENERIC_BASENAMES and token not in _STOPWORDS:
                out.add(token)
    return frozenset(out)


def _path_idents(paths: Sequence[str]) -> frozenset[str]:
    """Compound identifiers implied by a path: ``standup/habits.py`` → ``standup-habits``."""
    out: set[str] = set()
    for path in paths:
        parts = str(path).replace("\\", "/").strip("/").split("/")
        if len(parts) >= 2:
            stem = parts[-1].rsplit(".", 1)[0]
            ident = _canonical_ident(f"{parts[-2]}-{stem}")
            if ident:
                out.add(ident)
        ident = _canonical_ident(parts[-1])
        if ident:
            out.add(ident)
    return frozenset(out)


def _normalize_url(url: str) -> str:
    cleaned = str(url or "").strip().lower()
    for marker in ("?", "#"):
        cleaned = cleaned.split(marker, 1)[0]
    return cleaned.rstrip("/")


# --- profiles --------------------------------------------------------------


@dataclass(frozen=True)
class TicketProfile:
    """One ticket, reduced to what a matcher can ask about."""

    key: str = ""
    # The readable text, kept alongside the token sets purely so an adjudicator
    # can be shown what it is ruling on. No predicate here reads them.
    title: str = ""
    text: str = ""
    title_words: frozenset[str] = frozenset()
    words: frozenset[str] = frozenset()
    idents: frozenset[str] = frozenset()
    # Every token in the ticket's text, unfiltered — used ONLY to confirm a
    # repository name beside an ambiguous "#91". `words` is no good for that:
    # it drops anything under four characters, so a repo called "web" or "api"
    # could never satisfy the guard.
    mentions: frozenset[str] = frozenset()
    urls: frozenset[str] = frozenset()
    numbers: frozenset[str] = frozenset()
    shas: frozenset[str] = frozenset()
    size: int = 0
    docs_in_dod: bool = False


@dataclass(frozen=True)
class TicketCorpus:
    """Every ticket in the window, plus the index that makes lookups cheap."""

    tickets: Mapping[str, TicketProfile] = field(default_factory=dict)
    postings: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # Urls, numbers and shas a ticket names, indexed separately and consulted
    # WITHOUT the rarity gate. A back-reference shares no vocabulary with the
    # change it points at, so routing it through the word index would make the
    # strongest predicate unreachable — and a url cited by several tickets is
    # still a pointer, not noise.
    ref_postings: Mapping[str, frozenset[str]] = field(default_factory=dict)
    rare_max: int = 1

    def __bool__(self) -> bool:
        return bool(self.tickets)

    def is_rare(self, token: str) -> bool:
        return len(self.postings.get(token, ())) <= self.rare_max


@dataclass(frozen=True)
class ChangeProfile:
    """One commit or pull request, reduced the same way."""

    subject_words: frozenset[str] = frozenset()
    branch_words: frozenset[str] = frozenset()
    path_words: frozenset[str] = frozenset()
    idents: frozenset[str] = frozenset()
    url: str = ""
    pr_id: str = ""
    repo_token: str = ""
    shas: frozenset[str] = frozenset()
    path_count: int = 0
    docs_only: bool = False


def build_corpus(*item_groups: Iterable[Mapping]) -> TicketCorpus:
    """Index every ticket across the given item sequences, merged by key.

    Text is merged rather than picked from one item because ``kind`` does not
    predict which item carries the body: Jira changelog and comment items name
    the same ticket and deliberately carry no description, and the WIP query is
    a different search from the updated-in-window one.
    """
    titles: dict[str, list[str]] = {}
    bodies: dict[str, str] = {}
    for items in item_groups:
        for item in items:
            if item.get("kind") not in _TICKET_KINDS:
                continue
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            for candidate in (item.get("summary"), item.get("title")):
                text = str(candidate or "").strip()
                if text and text not in titles.setdefault(key, []):
                    titles[key].append(text)
            body = str(item.get("body") or "").strip()
            # Longest wins: order-independent, unlike newest-wins, so the corpus
            # does not depend on how the collector happened to interleave sources.
            if len(body) > len(bodies.get(key, "")):
                bodies[key] = body[:_MAX_TICKET_BODY_CHARS]

    tickets: dict[str, TicketProfile] = {}
    for key in sorted(set(titles) | set(bodies)):
        title = " ".join(titles.get(key, ()))
        body = bodies.get(key, "")
        combined = f"{title}\n{body}"
        title_words = _words(title)
        body_words = _words(body)
        if not title_words and not body_words:
            continue  # nothing to match on; carrying it would only cost time
        tickets[key] = TicketProfile(
            key=key,
            title=title,
            text=body,
            title_words=title_words,
            words=title_words | body_words,
            idents=_idents(combined) | _bigrams(combined),
            mentions=frozenset(_RUN_RE.findall(combined.lower())),
            urls=frozenset(_normalize_url(u) for u in _URL_RE.findall(body)),
            numbers=frozenset(_NUM_RE.findall(body)),
            shas=frozenset(s.lower() for s in _SHA_RE.findall(body) if len(s) >= _MIN_SHA_CHARS),
            size=len(body_words),
            docs_in_dod=bool(_DOD_DOC_RE.search(body)),
        )

    postings: dict[str, set[str]] = {}
    ref_postings: dict[str, set[str]] = {}
    for key, profile in tickets.items():
        for token in profile.words | profile.idents:
            postings.setdefault(token, set()).add(key)
        for ref in _profile_refs(profile):
            ref_postings.setdefault(ref, set()).add(key)
    return TicketCorpus(
        tickets=tickets,
        postings={token: frozenset(keys) for token, keys in postings.items()},
        ref_postings={ref: frozenset(keys) for ref, keys in ref_postings.items()},
        rare_max=max(1, int(_RARE_DF_RATIO * len(tickets))),
    )


def _profile_refs(profile: TicketProfile) -> frozenset[str]:
    """Namespaced reference tokens, so a url can never collide with a word."""
    return frozenset(
        [f"url:{u}" for u in profile.urls] + [f"sha:{s}" for s in profile.shas] + [f"num:{n}" for n in profile.numbers]
    )


def _change_refs(change: ChangeProfile) -> frozenset[str]:
    refs = {f"sha:{s}" for s in change.shas}
    if change.url:
        refs.add(f"url:{change.url}")
    if change.pr_id:
        refs.add(f"num:{change.pr_id}")
    return frozenset(refs)


def ticket_keys(items: Iterable[Mapping]) -> frozenset[str]:
    """Ticket keys these items name — the member's Tier-A pool.

    Built from every tracker kind, not from ``wip``: Jira's WIP query skips any
    issue the updated-in-window search already returned, so an actively working
    member has zero ``wip`` items. Selecting on that kind would hand an empty
    candidate pool to exactly the people doing the most work.
    """
    return frozenset(str(item.get("key") or "").strip() for item in items if item.get("kind") in _TICKET_KINDS) - {""}


def build_change_profile(item: Mapping, *, docs_only: bool = False) -> ChangeProfile:
    """Reduce one commit or pull request to its matchable tokens."""
    # `summary` as well as `title`: a Confluence page carries its real name
    # there, and the documentation rule judges pages. Commits and pull requests
    # never set it, so this is free for them.
    subject = " ".join(part for part in (item.get("summary"), item.get("title")) if part)
    body = str(item.get("body") or "")
    branch = str(item.get("branch") or "")
    paths = tuple(str(p) for p in (item.get("changed_paths") or ()) if p)
    branch_words, branch_idents = _branch_tokens(branch)
    repo = str(item.get("repository") or "").strip()
    key = str(item.get("key") or "").strip().lstrip("#!")
    return ChangeProfile(
        subject_words=_words(subject),
        branch_words=branch_words,
        path_words=_path_tokens(paths),
        idents=_idents(subject) | _idents(body) | branch_idents | _path_idents(paths),
        url=_normalize_url(item.get("url") or ""),
        pr_id=str(item.get("pr_id") or "").strip(),
        repo_token=repo.rsplit("/", 1)[-1].lower(),
        shas=frozenset({key.lower()}) if len(key) >= _MIN_SHA_CHARS and _SHA_RE.fullmatch(key) else frozenset(),
        path_count=len(paths),
        docs_only=docs_only,
    )


# --- predicates ------------------------------------------------------------


def _rare_hits(tokens: Collection[str], profile_tokens: Collection[str], corpus: TicketCorpus) -> int:
    return sum(1 for token in tokens if token in profile_tokens and corpus.is_rare(token))


def _covered(tokens: Collection[str], profile_tokens: Collection[str]) -> float:
    """Share of the CHANGE's tokens the ticket accounts for — never the reverse."""
    if not tokens:
        return 0.0
    return sum(1 for token in tokens if token in profile_tokens) / len(tokens)


def _backreference(change: ChangeProfile, profile: TicketProfile) -> bool:
    """The ticket names the change outright. Exact, and admissible in both tiers."""
    if change.url and change.url in profile.urls:
        return True
    if change.shas & profile.shas:
        return True
    # A bare "#91" is ambiguous by construction — on GitHub it is a PR number,
    # on Azure Boards a work-item id — so it only counts when the ticket also
    # mentions the repository the change lives in.
    return bool(
        change.pr_id and change.pr_id in profile.numbers and change.repo_token and change.repo_token in profile.mentions
    )


def _shared_identifier(change: ChangeProfile, profile: TicketProfile, corpus: TicketCorpus) -> bool:
    """A rare compound identifier on both sides. The strongest lexical evidence."""
    return any(ident in profile.idents and corpus.is_rare(ident) for ident in change.idents)


def _word_match(change: ChangeProfile, profile: TicketProfile, corpus: TicketCorpus) -> bool:
    """Ordinary-word overlap. Tier A only, and never against a huge ticket."""
    if profile.size > _HUGE_TICKET_TOKENS:
        return False
    coverage_floor, rare_floor = _SUBJECT_COVERAGE, _MIN_RARE_WORDS
    title_rare_floor = _MIN_RARE_WORDS_TITLE
    branch_rare_floor = _MIN_RARE_WORDS_BRANCH
    title_floor, branch_floor = _TITLE_COVERAGE, _BRANCH_COVERAGE
    if change.docs_only and profile.docs_in_dod:
        # Documentation is a definition-of-done item on every story this
        # product's planner generates, so docs accompanying a ticket is the
        # expected shape rather than new scope — and being wrong here is the
        # worst variant of the message, telling someone the runbook that WAS
        # the ticket's definition of done "doesn't count toward the sprint".
        # The relaxation is gated on the ticket actually saying so: no docs in
        # its definition of done, no discount.
        coverage_floor = title_floor = branch_floor = _DOCS_COVERAGE
        rare_floor = title_rare_floor = branch_rare_floor = _MIN_RARE_WORDS_DOCS

    if (
        len(change.branch_words) >= 2
        and _covered(change.branch_words, profile.words) >= branch_floor
        and _rare_hits(change.branch_words, profile.words, corpus) >= branch_rare_floor
    ):
        return True
    if (
        change.subject_words
        and _covered(change.subject_words, profile.title_words) >= title_floor
        and _rare_hits(change.subject_words, profile.title_words, corpus) >= title_rare_floor
    ):
        return True
    if (
        len(change.subject_words) >= _MIN_CHANGE_TOKENS
        and _covered(change.subject_words, profile.words) >= coverage_floor
        and _rare_hits(change.subject_words, profile.words, corpus) >= rare_floor
    ):
        return True
    # Paths. Empty means UNKNOWN — the collectors cap detail lookups — so an
    # absent list contributes nothing in either direction. Two distinct rare
    # matches, not one: a path is contextual rather than declarative, and
    # `src/auth/session.py` against any ticket mentioning sessions is not
    # evidence of anything. A path that really does name the work reaches the
    # identifier predicate instead, which is admissible in both tiers.
    needed = _PATH_RARE_HITS_SHOTGUN if change.path_count > _PATH_SHOTGUN_MAX else _PATH_RARE_HITS
    return bool(change.path_words) and _rare_hits(change.path_words, profile.words, corpus) >= needed


def _candidates(change: ChangeProfile, corpus: TicketCorpus, own_keys: Collection[str]) -> list[str]:
    """Tickets worth scoring: the member's own, plus anything sharing a rare token.

    Postings for common tokens are skipped rather than walked, which *is* the
    rarity gate — implemented for free, and the reason this stays linear in the
    number of tickets a change actually resembles rather than in the corpus.
    """
    scored: dict[str, int] = {}
    tokens = change.idents | change.subject_words | change.branch_words | change.path_words
    for token in tokens:
        keys = corpus.postings.get(token, ())
        if len(keys) > corpus.rare_max:
            continue
        for key in keys:
            scored[key] = scored.get(key, 0) + 1
    # Ungated, and scored highest: a ticket that names this change outright is
    # the best candidate there can be, even with no words in common.
    for ref in _change_refs(change):
        for key in corpus.ref_postings.get(ref, ()):
            scored[key] = scored.get(key, 0) + 100
    for key in own_keys:
        if key in corpus.tickets:
            scored.setdefault(key, 0)
    # Own tickets first, then by shared-rare-token count, then by key — fully
    # deterministic, so a shuffled input cannot change the answer.
    ordered = sorted(scored, key=lambda k: (0 if k in own_keys else 1, -scored[k], k))
    return ordered[:_MAX_CANDIDATES_PER_CHANGE]


def relates_to_ticket(change: ChangeProfile, corpus: TicketCorpus, *, own_keys: Collection[str] = ()) -> bool:
    """Whether this change plausibly belongs to some ticket. Suppression only.

    The matched key is deliberately not returned. Nothing downstream may name
    it, so matching the wrong sibling ticket in an epic costs nothing at all.
    """
    if not corpus:
        return False
    for key in _candidates(change, corpus, own_keys):
        profile = corpus.tickets[key]
        if _backreference(change, profile) or _shared_identifier(change, profile, corpus):
            return True
        if key in own_keys and _word_match(change, profile, corpus):
            return True
    return False


def near_misses(
    change: ChangeProfile, corpus: TicketCorpus, *, own_keys: Collection[str] = (), limit: int = 3
) -> tuple[str, ...]:
    """The tickets a change most resembles without matching — the adjudicator's shortlist.

    Only ever used to give a language model something concrete to rule on. It
    carries no verdict of its own.
    """
    if not corpus:
        return ()
    return tuple(_candidates(change, corpus, own_keys)[:limit])
