"""Prior art: the team's own repositories, offered as reference for a new build.

A greenfield plan is written from a blank page even when the team already runs
the auth service, the design system, the deployment pattern and the payments
integration the new project needs. Team-analysis walks that estate on every run
and now persists what it finds (``analysis.repo_inventory``); this module reads
it back, ranks it against the requirements the intake just gathered, and hands
the intake node a handful of candidates to ask about.

**Layering, deliberately the same split as ``repo_signals.py``.** Everything
that decides *which* repositories matter is pure and offline — ``rank`` reads
nothing but its arguments. The two impure steps sit at the edges and each fails
soft: ``enrich`` reaches the network for the shortlist only and keeps the stored
row when a call fails, and ``pitch`` calls one LLM and falls back to a
deterministic sentence built from the same facts. So the step degrades from
"good pitch" to "plain pitch" to "no candidates", and never to an error.

**Enrichment is GitHub-only, and that shapes the results.** Azure DevOps
exposes neither a repository description nor a language breakdown, and its
inventory rows carry no push date, so an AzDO candidate can only score on
name-token overlap and never earns the recency term. It is still offered — a
repository the team owns is worth asking about either way — but in practice the
shortlist skews GitHub. Not an oversight; the API has nothing more to give.

**The model may never nominate a repository.** ``pitch`` is handed the
candidates ranking already chose and may only write bullets for them or mark
them dropped. That keeps the accusation surface deterministic — the same
suppress-only invariant as ``standup/adjudicate.py`` — so a hostile or confused
model can at worst leave the user with a shorter list, never with an invented
one. See ``prior_art_feedback`` for the other half of the loop.

# See docs: "Project Intake Questionnaire" — prior art
"""

from __future__ import annotations

import dataclasses
import logging
import re

from yeaboi.agent.prior_art_feedback import Ledger
from yeaboi.analysis import repo_inventory

logger = logging.getLogger(__name__)

# How many repositories the user is asked about. Five is a shortlist someone
# will actually read; twenty is a form they will skip.
SHORTLIST_LIMIT = 5

# Only a greenfield build gets asked. "Hybrid" is a plausible future addition —
# it is one entry in this set — but was deliberately left out: a team extending
# an existing codebase already knows what they are extending.
PRIOR_ART_Q2_ANSWERS = frozenset({"greenfield"})

# Why a shortlist came back empty. The card shows the reason rather than going
# quiet, because "we found nothing" and "we never looked" are different facts
# and only one of them is the user's to fix.
EMPTY_NO_PROFILE = "no_profile"
EMPTY_NO_INVENTORY = "no_inventory"
EMPTY_NO_MATCH = "no_match"

EMPTY_REASON_TEXT = {
    EMPTY_NO_PROFILE: (
        "No team-analysis profile yet — run Team Analysis so planning can learn from your repositories."
    ),
    EMPTY_NO_INVENTORY: ("This analysis profile has no repository details — re-run Team Analysis to capture them."),
    EMPTY_NO_MATCH: "Nothing in your repositories looks close to this project.",
}

# Scoring weights. Stack agreement dominates because it is the strongest
# evidence that code could actually be lifted; wording overlap is suggestive
# but noisy, and recency is only a tie-breaker.
_W_STACK = 3.0
_W_KEYWORD = 1.0
_W_RECENCY = 0.5
_MIN_SCORE = 1.0  # below this a repo is noise, not a candidate

# Words too common in project prose to carry signal.
_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have how i if in into is it its of on or
    that the their them then there these they this to was were what when where which who
    will with would you your our we us new project build building system service app
    application platform team need needs want using use used support supports""".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.-]*")

# Positive structure signals for the pitch. code_health.analyse_repository_health
# answers the opposite question — what a repository is *missing* — so its
# findings cannot be reused here; the manifest table can be, and is.
_STRUCTURE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tests", ("test/", "tests/", "__tests__/", "spec/", "_test.go", "_test.py", ".test.ts", ".spec.ts")),
    ("CI", (".github/workflows/", ".azure-pipelines", "azure-pipelines.yml", ".gitlab-ci.yml", "jenkinsfile")),
    ("Docker", ("dockerfile", "docker-compose.")),
    ("Terraform", (".tf",)),
    ("Kubernetes", ("k8s/", "kubernetes/", "chart.yaml")),
    ("docs", ("docs/", "readme.md", "adr/", "architecture")),
    ("migrations", ("migrations/", "alembic/", "flyway")),
)


@dataclasses.dataclass(frozen=True)
class Requirements:
    """The slice of the questionnaire that ranking reads.

    A plain value object rather than a QuestionnaireState so ranking stays
    testable without constructing the intake machinery.
    """

    description: str = ""
    outcomes: str = ""
    stack: str = ""
    integrations: str = ""

    @property
    def prose(self) -> str:
        return " ".join(part for part in (self.description, self.outcomes) if part)

    @property
    def stated_stack(self) -> str:
        return " ".join(part for part in (self.stack, self.integrations) if part)


@dataclasses.dataclass(frozen=True)
class RepoCandidate:
    """One repository offered as prior art, with the evidence behind it."""

    key: str
    name: str
    platform: str = ""
    url: str = ""
    description: str = ""
    languages: tuple[str, ...] = ()
    frameworks: tuple[str, ...] = ()
    integrations: tuple[str, ...] = ()
    structure: tuple[str, ...] = ()
    last_activity: str = ""
    pitch: tuple[str, ...] = ()
    score: float = 0.0

    @property
    def stack(self) -> tuple[str, ...]:
        """Everything known about what this repository is built from."""
        seen: list[str] = []
        for item in (*self.languages, *self.frameworks, *self.integrations):
            if item and item not in seen:
                seen.append(item)
        return tuple(seen)


@dataclasses.dataclass(frozen=True)
class Shortlist:
    """The result of a scan: candidates, or the reason there are none."""

    candidates: tuple[RepoCandidate, ...] = ()
    empty_reason: str = ""

    @property
    def message(self) -> str:
        return EMPTY_REASON_TEXT.get(self.empty_reason, "")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def applies(answers: dict[int, str] | None) -> bool:
    """Whether the prior-art step runs for these answers (greenfield only)."""
    answer = str((answers or {}).get(2, "") or "").strip().lower()
    return any(marker in answer for marker in PRIOR_ART_Q2_ANSWERS)


def requirements_from_answers(answers: dict[int, str] | None) -> Requirements:
    """Pull the ranking inputs out of the questionnaire answer map."""
    answers = answers or {}

    def _get(num: int) -> str:
        return str(answers.get(num, "") or "").strip()

    return Requirements(
        description=_get(1),
        outcomes=" ".join(part for part in (_get(3), _get(4)) if part),
        stack=_get(11),
        integrations=_get(12),
    )


def _tokens(text: str) -> set[str]:
    """Meaningful lowercase words in a blob of prose."""
    return {tok for tok in _TOKEN_RE.findall((text or "").lower()) if len(tok) > 2 and tok not in _STOPWORDS}


# Languages whose names are shorter than the prose threshold. Without these,
# a team that answered "Go" at Q11 could never match a repo whose language is
# Go — and stack overlap is the heaviest term in the score, so the dominant
# signal would be silently unavailable to whole ecosystems.
_SHORT_STACK_TOKENS = frozenset({"go", "c", "r", "c#", "f#", "js", "ts", "ml", "qt", "vb", "sh"})


def _stack_tokens(text: str) -> set[str]:
    """Like ``_tokens``, but keeps short language names.

    Deliberately not used for prose: "go" is a common verb, and letting it
    through the description path would score every repository written in Go
    against a project that merely wants to "go to market".
    """
    found = {tok for tok in _TOKEN_RE.findall((text or "").lower()) if tok not in _STOPWORDS}
    return {tok for tok in found if len(tok) > 2 or tok in _SHORT_STACK_TOKENS}


def structure_signals(paths: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Positive capability signals visible in a repository's file tree.

    The inverse of ``code_health.analyse_repository_health``, which reports
    what a repository is *missing*. A pitch needs what it *has*.
    """
    from yeaboi.analysis.code_health import _MANIFESTS

    lower = [str(path).lower() for path in (paths or [])]
    if not lower:
        return ()
    joined = "\n".join(lower)
    found: list[str] = []
    for label, markers in _STRUCTURE_MARKERS:
        if any(marker in joined for marker in markers):
            found.append(label)
    basenames = {path.rsplit("/", 1)[-1] for path in lower}
    if basenames & _MANIFESTS:
        found.append("dependency manifest")
    return tuple(found)


def _recency_score(updated_at: str) -> float:
    """0.0–1.0 by how recently the repository was pushed to.

    Deliberately coarse and never a filter: a mature service nobody has touched
    in a year is often the *best* thing to copy from.
    """
    year = (updated_at or "")[:4]
    if not year.isdigit():
        return 0.0
    from datetime import UTC, datetime

    age = datetime.now(UTC).year - int(year)
    if age <= 0:
        return 1.0
    if age == 1:
        return 0.6
    if age == 2:
        return 0.3
    return 0.0


def score(row: dict, requirements: Requirements) -> tuple[float, list[str]]:
    """Score one inventory row against the requirements — ``(score, why)``.

    Pure. ``why`` is the deterministic evidence, which doubles as the fallback
    pitch when no LLM is available.
    """
    why: list[str] = []
    total = 0.0

    stated = _stack_tokens(requirements.stated_stack)
    languages = [str(lang) for lang in (row.get("languages") or [])]
    shared_stack = sorted({lang for lang in languages if lang.lower() in stated})
    if shared_stack:
        total += _W_STACK * len(shared_stack)
        why.append(f"Shares your stack: {', '.join(shared_stack)}")

    prose = _tokens(requirements.prose)
    haystack = _tokens(f"{row.get('name', '')} {row.get('description', '')}")
    shared_words = sorted(prose & haystack)
    if shared_words:
        total += _W_KEYWORD * len(shared_words)
        why.append(f"Mentions {', '.join(shared_words[:4])}")

    recency = _recency_score(str(row.get("updated_at", "")))
    total += _W_RECENCY * recency

    return total, why


def rank(
    rows: list[dict] | tuple[dict, ...] | None,
    requirements: Requirements,
    ledger: Ledger | None = None,
    *,
    limit: int = SHORTLIST_LIMIT,
) -> list[RepoCandidate]:
    """Deterministically pick the repositories worth asking about. Pure.

    Rejected repositories are dropped by key before scoring — a verdict the
    user already gave is never re-litigated by a score.
    """
    ledger = ledger or Ledger()
    scored: list[tuple[float, RepoCandidate]] = []
    for row in rows or []:
        key = str(row.get("key") or "")
        if not key or ledger.is_rejected(key):
            continue
        value, why = score(row, requirements)
        if value < _MIN_SCORE:
            continue
        scored.append(
            (
                value,
                RepoCandidate(
                    key=key,
                    name=str(row.get("name", "") or ""),
                    platform=str(row.get("provider", "") or ""),
                    url=str(row.get("url", "") or ""),
                    description=str(row.get("description", "") or ""),
                    languages=tuple(str(lang) for lang in (row.get("languages") or [])),
                    last_activity=str(row.get("updated_at", "") or ""),
                    pitch=tuple(why),
                    score=value,
                ),
            )
        )
    # Key breaks ties so two equally-scored repos always order the same way —
    # a shortlist that shuffles between runs is a shortlist nobody trusts.
    scored.sort(key=lambda pair: (-pair[0], pair[1].key))
    return [candidate for _, candidate in scored[:limit]]


# ---------------------------------------------------------------------------
# Impure edges — each one fails soft
# ---------------------------------------------------------------------------


def load_candidates(profile_id: str = "", db_path=None) -> tuple[list[dict], str]:
    """Read the stored repository estate — ``(rows, empty_reason)``.

    Offline: this is a read of the analysis profile the user already ran, not a
    fresh walk of GitHub and Azure DevOps. Planning therefore needs no
    credentials of its own, and a planning session on a plane still works.

    The two empty outcomes are distinguished on purpose — "you have no profile"
    is a different instruction to the user than "your profile is too old".
    """
    try:
        from yeaboi.agent.nodes import _load_team_examples

        examples = _load_team_examples(profile_id, db_path=db_path)
    except Exception:  # pragma: no cover — a loader that never raises, wrapped anyway
        logger.warning("prior_art: team examples unavailable", exc_info=True)
        examples = None
    if not examples:
        logger.info("prior_art: no analysis profile for %r", profile_id or "(auto)")
        return [], EMPTY_NO_PROFILE
    rows = examples.get(repo_inventory.INVENTORY_KEY) or []
    if not rows:
        # Every profile captured before the inventory landed reaches here.
        logger.info("prior_art: profile has no repository inventory")
        return [], EMPTY_NO_INVENTORY
    logger.info("prior_art: loaded %d repositories from the analysis profile", len(rows))
    return list(rows), ""


def _is_error(text: str) -> bool:
    """The read tools signal failure in their return string, never by raising."""
    return not text or text.startswith(("Error:", "GitHub rate limit"))


def enrich(candidates: list[RepoCandidate]) -> list[RepoCandidate]:
    """Fill in frameworks, integrations and structure for the shortlist only.

    Costs one repo read plus one tree read per candidate — a handful of calls,
    never an estate scan. Every failure keeps the stored row untouched, so a
    missing token degrades the pitch instead of removing the candidate.
    """
    from yeaboi.agent.repo_signals import analyze_context

    out: list[RepoCandidate] = []
    for candidate in candidates:
        if not candidate.url or candidate.platform != "github":
            # Azure exposes neither a description nor a language breakdown, so
            # there is nothing to enrich with; those candidates pitch from the
            # inventory row alone. Known asymmetry, not an oversight.
            out.append(candidate)
            continue
        frameworks: tuple[str, ...] = ()
        integrations: tuple[str, ...] = ()
        structure: tuple[str, ...] = ()
        try:
            from yeaboi.tools.github import github_read_repo, github_repo_tree

            raw = github_read_repo.invoke({"repo_url": candidate.url})
            if not _is_error(raw):
                signals = analyze_context(raw, source="github")
                frameworks = tuple(signals.detected_stack)
                integrations = tuple(signals.integrations)
            paths, tree_error = github_repo_tree(candidate.url)
            if not tree_error or paths:
                structure = structure_signals(paths)
        except Exception:
            logger.warning("prior_art: enrichment failed for %s", candidate.key, exc_info=True)
        out.append(
            dataclasses.replace(
                candidate,
                frameworks=frameworks or candidate.frameworks,
                integrations=integrations or candidate.integrations,
                structure=structure or candidate.structure,
            )
        )
    return out


def _fallback_pitch(candidate: RepoCandidate) -> tuple[str, ...]:
    """Deterministic bullets from the evidence, when no model is available.

    Plainer than the model's prose and never wrong about a fact, because every
    clause is something the scan actually saw.
    """
    bullets: list[str] = list(candidate.pitch)
    if candidate.description:
        bullets.insert(0, candidate.description)
    stack = ", ".join(candidate.stack[:5])
    if stack:
        bullets.append(f"Built with {stack}")
    if candidate.structure:
        bullets.append(f"Has {', '.join(candidate.structure)}")
    # Dedupe while preserving order — the description often repeats a keyword hit.
    seen: list[str] = []
    for bullet in bullets:
        if bullet and bullet not in seen:
            seen.append(bullet)
    return tuple(seen[:4])


def pitch(
    candidates: list[RepoCandidate],
    requirements: Requirements,
    ledger: Ledger | None = None,
) -> list[RepoCandidate]:
    """Write each candidate's "it does X, Y, Z" and drop the ones that don't help.

    The model is handed candidates ranking already chose and may only describe
    or drop them — it is never asked for a repository, so it cannot invent one.
    On any failure every candidate keeps a deterministic fallback pitch, so the
    step degrades in prose quality and not in correctness.
    """
    if not candidates:
        return []
    ledger = ledger or Ledger()
    verdicts: dict[str, dict] = {}
    try:
        from yeaboi.agent.nodes import _invoke_json
        from yeaboi.prompts.prior_art import get_prior_art_pitch_prompt

        prompt = get_prior_art_pitch_prompt(
            candidates=[dataclasses.asdict(c) for c in candidates],
            description=requirements.description,
            outcomes=requirements.outcomes,
            stack=requirements.stated_stack,
            corrections=ledger.corrections(),
        )
        response = _invoke_json(prompt)
        verdicts = _parse_pitch_response(response.content, {c.key for c in candidates})
    except Exception as exc:
        from yeaboi.agent.nodes import _should_reraise_llm_error

        if _should_reraise_llm_error(exc):
            raise
        logger.warning("prior_art: pitch generation failed, using deterministic bullets", exc_info=True)

    out: list[RepoCandidate] = []
    for candidate in candidates:
        verdict = verdicts.get(candidate.key, {})
        if verdict.get("drop"):
            logger.info("prior_art: dropped %s as not relevant", candidate.key)
            continue
        bullets = tuple(verdict.get("pitch") or ()) or _fallback_pitch(candidate)
        out.append(dataclasses.replace(candidate, pitch=bullets))
    return out


def _parse_pitch_response(raw: str, known_keys: set[str]) -> dict[str, dict]:
    """Parse the pitch model's reply. Pure; unknown keys are discarded.

    Discarding is what makes the loop suppress-only: a key the model invented
    matches no candidate, so an invented repository cannot reach the user.
    """
    import json

    from yeaboi.agent.llm import strip_json_fences

    try:
        parsed = json.loads(strip_json_fences(raw or ""))
    except Exception:
        logger.warning("prior_art: pitch reply was not JSON", exc_info=True)
        return {}
    entries = parsed.get("repos") if isinstance(parsed, dict) else parsed
    if not isinstance(entries, list):
        return {}
    out: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key", "") or "").strip().lower()
        if key not in known_keys:
            continue
        bullets = [str(b).strip() for b in (entry.get("pitch") or []) if str(b).strip()]
        out[key] = {"drop": bool(entry.get("drop")), "pitch": bullets[:4]}
    return out


def shortlist(
    answers: dict[int, str] | None,
    *,
    profile_id: str = "",
    db_path=None,
    limit: int = SHORTLIST_LIMIT,
) -> Shortlist:
    """Load, rank, enrich and pitch — the whole scan, in one call.

    Never raises for a data reason. The only exception that escapes is an
    actionable LLM auth/billing error, which ``pitch`` re-raises deliberately
    so the user is told their key is wrong rather than silently getting a
    thinner feature.
    """
    from yeaboi.agent import prior_art_feedback

    requirements = requirements_from_answers(answers)
    rows, reason = load_candidates(profile_id, db_path)
    if reason:
        return Shortlist(empty_reason=reason)
    ledger = prior_art_feedback.load(db_path=db_path)
    ranked = rank(rows, requirements, ledger, limit=limit)
    if not ranked:
        return Shortlist(empty_reason=EMPTY_NO_MATCH)
    candidates = pitch(enrich(ranked), requirements, ledger)
    if not candidates:
        # Ranking found matches but the model dropped all of them.
        return Shortlist(empty_reason=EMPTY_NO_MATCH)
    logger.info("prior_art: shortlisted %d repositories", len(candidates))
    return Shortlist(candidates=tuple(candidates))
