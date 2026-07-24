"""AI-adoption footprint — detect how much of a team's tracked work shows an AI-tool trace.

# See docs: "Architecture" — engines are UI-free pipelines; this is a sub-analysis
# of team-analysis mode (CLAUDE.md "REQUIRED: Surface Parity" — the TUI/CLI/MCP are
# thin adapters over ``analysis/engine.py:run_team_analysis``, which calls into here).

What this does
--------------
Fans out over the team's **remote** code sources (GitHub, Azure DevOps),
pulls recent commits + PRs *with their message bodies / descriptions*, and scans
that text for markers left by AI coding tools — ``Co-Authored-By: Claude``,
"Generated with Claude Code", Copilot's co-author line, Cursor / aider / Devin /
Codeium, and a catch-all AI trailer. It then aggregates a per-tool / per-author /
per-activity-type breakdown into an :class:`AiAdoptionSignal` and coaches the lead
on improving adoption (start / stop / keep / try).

Honesty contract — LOWER BOUND, never ground truth
--------------------------------------------------
Only tools that leave a *textual* trace in commit/PR metadata are counted. Inline
IDE assist (Copilot ghost-text, Cursor Tab) leaves no trace, so real usage is
always *at least* the reported footprint. Every surface must frame it that way;
``AiAdoptionSignal.is_lower_bound`` stays ``True`` to force it.

Error contract
--------------
Everything here is best-effort and NEVER raises: a missing SDK/credential or a
failing source contributes zero and is recorded as a coverage gap. ``run_ai_adoption``
wraps the whole thing so the analysis pipeline can call it unguarded.
"""

from __future__ import annotations

import logging
import re

from yeaboi.team_profile import AiAdoptionSignal

logger = logging.getLogger(__name__)

# Look-back window for the commit/PR scan. The recent-activity helpers cap at ~100
# rows/source, so this is a "recent sample" for a footprint %, not an exhaustive audit.
_SCAN_DAYS = 120

# ---------------------------------------------------------------------------
# Marker table — extensible. Each entry: (tool_id, compiled regex over commit/PR text).
# Order matters: ``other_ai`` is the last-resort catch-all and is suppressed when a
# specific tool already matched (see _classify_ai_markers).
# ---------------------------------------------------------------------------
_AI_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "claude",
        re.compile(
            r"co-authored-by:\s*claude|generated with \[?claude code|noreply@anthropic\.com|claude\.com/claude-code",
            re.IGNORECASE,
        ),
    ),
    (
        "copilot",
        re.compile(
            r"github-copilot\[bot\]|co-authored-by:.*copilot|copilot@github\.com|gpt-4-copilot",
            re.IGNORECASE,
        ),
    ),
    (
        "cursor",
        re.compile(r"co-authored-by:.*cursor|\bcursor\s*(ai|assistant|agent)\b|cursor\.com", re.IGNORECASE),
    ),
    (
        "aider",
        re.compile(r"co-authored-by:.*aider|\baider\b\s*(commit|edit|chat)?|aider\.chat", re.IGNORECASE),
    ),
    (
        "devin",
        re.compile(r"co-authored-by:.*devin|\bdevin[\s\-]?ai\b|devin\.ai", re.IGNORECASE),
    ),
    (
        "codeium",
        re.compile(r"co-authored-by:.*(codeium|windsurf)|\bcodeium\b|\bwindsurf\b", re.IGNORECASE),
    ),
    # Catch-all: an explicit co-author/trailer that names *some* AI/bot but matched
    # none of the specific tools above. Kept last; suppressed when a specific hit exists.
    (
        "other_ai",
        re.compile(r"co-authored-by:.*\b(ai|assistant|bot|llm|gpt|agent)\b", re.IGNORECASE),
    ),
)

# A commit whose subject looks documentation-shaped is bucketed as "docs", not "code".
_DOCS_TITLE = re.compile(r"\breadme\b|\bdocs?/|\.md\b|\bdocumentation\b|\bchangelog\b", re.IGNORECASE)

# Human-readable source labels — the raw tags ("github"/"azdo") name the remote
# each scan hit. Single source of truth for renderers. Only remote sources are
# scanned (local-clone scanning was removed — it was environment-dependent and
# meaningless for a hosted team).
_SOURCE_LABELS: dict[str, str] = {
    "github": "GitHub (remote)",
    "azdo": "Azure DevOps (remote)",
}


def _source_label(tag: str) -> str:
    """Friendly label for a source tag ('github' → 'GitHub (remote)'); passthrough otherwise."""
    return _SOURCE_LABELS.get(tag, tag)


def _classify_ai_markers(text: str) -> set[str]:
    """Return the set of AI-tool ids whose markers appear in ``text``.

    Pure, no I/O — the core unit-test seam. ``other_ai`` is dropped when a specific
    tool matched, so a Claude commit that also has a generic ``Co-Authored-By`` line
    is credited to "claude", not double-counted. Returns ``set()`` for empty text.
    """
    if not text:
        return set()
    hits: set[str] = set()
    for tool_id, pattern in _AI_MARKERS:
        if pattern.search(text):
            hits.add(tool_id)
    if len(hits) > 1:
        hits.discard("other_ai")
    return hits


def _activity_bucket(item: dict) -> str:
    """Map a normalized activity item to an adoption activity type: pr / docs / code."""
    if item.get("kind") == "pr":
        return "pr"
    if _DOCS_TITLE.search(str(item.get("title", ""))):
        return "docs"
    return "code"


def aggregate_ai_markers(items: list[dict]) -> AiAdoptionSignal:
    """Aggregate scanned commit/PR items into an :class:`AiAdoptionSignal`.

    Pure over its input (no network). Each item is a normalized activity dict with
    ``kind`` ('commit'/'pr'), ``author``, ``title``, optional ``body``, and
    ``source``. An item is "AI-marked" when :func:`_classify_ai_markers` over its
    ``title + body`` is non-empty. Returns an all-zero signal for an empty list.
    """
    scanned_commits = scanned_prs = ai_commits = ai_prs = 0
    per_tool: dict[str, int] = {}
    per_author: dict[str, int] = {}
    per_activity: dict[str, int] = {}
    per_source: dict[str, int] = {}
    sources: list[str] = []

    for item in items:
        kind = item.get("kind")
        is_pr = kind == "pr"
        if is_pr:
            scanned_prs += 1
        elif kind == "commit":
            scanned_commits += 1
        else:
            continue  # only commits/PRs carry an AI footprint

        src = str(item.get("source", "")).strip()
        if src and src not in sources:
            sources.append(src)

        tools = _classify_ai_markers(f"{item.get('title', '')}\n{item.get('body', '')}")
        if not tools:
            continue

        if is_pr:
            ai_prs += 1
        else:
            ai_commits += 1
        for t in tools:
            per_tool[t] = per_tool.get(t, 0) + 1
        author = (item.get("author") or "").strip() or "unknown"
        per_author[author] = per_author.get(author, 0) + 1
        bucket = _activity_bucket(item)
        per_activity[bucket] = per_activity.get(bucket, 0) + 1
        if src:
            per_source[src] = per_source.get(src, 0) + 1

    scanned = scanned_commits + scanned_prs
    footprint = round((ai_commits + ai_prs) / scanned * 100, 1) if scanned else 0.0

    def _sorted_pairs(d: dict[str, int]) -> tuple[tuple[str, int], ...]:
        return tuple(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))

    return AiAdoptionSignal(
        scanned_commits=scanned_commits,
        scanned_prs=scanned_prs,
        ai_commits=ai_commits,
        ai_prs=ai_prs,
        footprint_pct=footprint,
        per_tool=_sorted_pairs(per_tool),
        per_author=_sorted_pairs(per_author),
        per_activity=_sorted_pairs(per_activity),
        per_source=_sorted_pairs(per_source),
        sources_scanned=tuple(sources),
        is_lower_bound=True,
    )


# ---------------------------------------------------------------------------
# Data gathering — graceful, best-effort fan-out (mirrors standup/collector.py)
# ---------------------------------------------------------------------------


def collect_ai_activity(
    source: str, project_key: str, sub_sources: list[str] | None = None
) -> tuple[list[dict], list[str], list[str], list[str]]:
    """Fan out over GitHub + Azure DevOps (remote only) for recent commits/PRs with bodies.

    Returns ``(items, sources_scanned, coverage_notes, repos_scanned)``. Every source
    is best-effort and lazily imported (optional SDKs); a missing credential/SDK or a
    failing source contributes zero and is added to ``coverage_notes`` so absent
    coverage is visible rather than silent. ``repos_scanned`` holds friendly
    "what was actually scanned" labels (remote slug / project). Only remote sources
    are scanned — local-clone scanning was removed. ``sub_sources`` restricts which
    hosts to scan (subset of ``{"github", "azdo"}``; None = both). Never raises.
    """
    from yeaboi.config import (
        get_azure_devops_project,
        get_azure_devops_token,
        get_github_token,
        get_standup_github_repo,
    )

    def _want(tag: str) -> bool:
        return sub_sources is None or tag in sub_sources

    items: list[dict] = []
    sources_scanned: list[str] = []
    coverage: list[str] = []
    repos_scanned: list[str] = []

    def _run(name: str, tag: str, fetcher) -> None:
        try:
            raw = fetcher()
        except ImportError as e:
            logger.warning("AI-usage source %s skipped — SDK not installed: %s", name, e)
            coverage.append(f"{name}: SDK not installed")
            return
        except Exception as e:  # helpers already guard; never let one source abort
            logger.warning("AI-usage source %s failed: %s", name, e)
            coverage.append(f"{name}: error ({e})")
            return
        if not raw:
            return
        for item in raw:
            item["source"] = tag
            items.append(item)
        sources_scanned.append(tag)
        logger.info("AI-usage source %s contributed %d item(s)", name, len(raw))

    # GitHub — needs STANDUP_GITHUB_REPO (owner/repo) + a token.
    github_repo = get_standup_github_repo()
    if _want("github") and github_repo and get_github_token():

        def _github() -> list[dict]:
            from yeaboi.tools.github import github_recent_commits, github_recent_prs

            return github_recent_commits(github_repo, days=_SCAN_DAYS) + github_recent_prs(github_repo, days=_SCAN_DAYS)

        _run("github", "github", _github)
        if "github" in sources_scanned:
            repos_scanned.append(f"GitHub (remote): {github_repo}")
    else:
        coverage.append("github: STANDUP_GITHUB_REPO / GITHUB_TOKEN not set")

    # Azure DevOps — scan the resolved project's repos when AzDO is configured.
    azdo_project = project_key if source == "azdevops" else (get_azure_devops_project() or "")
    if _want("azdo") and azdo_project and get_azure_devops_token():

        def _azdo() -> list[dict]:
            from yeaboi.tools.azure_devops import azdevops_recent_commits, azdevops_recent_prs

            return azdevops_recent_commits(azdo_project, days=_SCAN_DAYS) + azdevops_recent_prs(
                azdo_project, days=_SCAN_DAYS
            )

        _run("azdo", "azdo", _azdo)
        if "azdo" in sources_scanned:
            repos_scanned.append(f"Azure DevOps (remote): {azdo_project}")
    else:
        coverage.append("azdo: AZURE_DEVOPS_PROJECT / AZURE_DEVOPS_TOKEN not set")

    return items, sources_scanned, coverage, repos_scanned


def _filter_items_by_members(items: list[dict], members: list[str]) -> tuple[list[dict], int]:
    """Keep only commit/PR items authored by one of ``members``.

    Matches a member name (case-insensitive) against the item's ``author`` OR the
    local-part of its ``author_email`` — the tracker's assignee display name and the
    git commit-author name are different identity spaces and often disagree, so we
    check both. Returns ``(filtered_items, distinct_authors_matched)``.
    """
    norm = {m.strip().lower() for m in members if m and m.strip()}
    if not norm:
        return items, 0
    kept: list[dict] = []
    matched_authors: set[str] = set()
    for it in items:
        author = (it.get("author", "") or "").strip().lower()
        email = (it.get("author_email", "") or "").strip().lower()
        local = email.split("@", 1)[0] if email else ""
        if (author and author in norm) or (local and local in norm):
            kept.append(it)
            matched_authors.add(author or local)
    return kept, len(matched_authors)


def run_ai_adoption(
    source: str,
    project_key: str,
    delivery_stories: list[dict],
    all_stories: list[dict],
    members: list[str] | None = None,
    sub_sources: list[str] | None = None,
) -> tuple[AiAdoptionSignal, dict]:
    """Orchestrate the AI-adoption scan: discover sources → collect → aggregate.

    Returns ``(signal, examples_blob)``. ``examples_blob`` carries the aggregated
    summary, up to ~20 illustrative samples for the report, and coverage notes.
    Wholly best-effort — any failure yields an empty signal and a coverage note,
    never an exception (the pipeline calls this unguarded). ``delivery_stories`` /
    ``all_stories`` are accepted for future ticket-derived repo discovery and to
    keep the signature stable; scanning currently uses configured code sources.

    When ``members`` is given, the scan is re-scoped to commits/PRs authored by
    those people (matched by name or email local-part). If the filter matches
    nothing, the whole-team scan is kept and a coverage note is added — reporting a
    false 0% footprint would be worse than an unscoped number.
    """
    logger.info("run_ai_adoption: source=%s project=%s members=%s", source, project_key, members or "all")
    try:
        items, sources_scanned, coverage, repos_scanned = collect_ai_activity(source, project_key, sub_sources)
        if members:
            filtered, matched = _filter_items_by_members(items, members)
            if filtered:
                logger.info(
                    "AI-usage member filter: %d/%d items from %d matched author(s)", len(filtered), len(items), matched
                )
                items = filtered
            else:
                logger.warning("AI-usage member filter matched no commit authors — keeping whole-team scan")
                coverage.append("member filter matched no commit authors — showing whole-team footprint")
        signal = aggregate_ai_markers(items)

        # Repo/source provenance onto the signal for honest, source-aware rendering.
        from dataclasses import replace

        signal = replace(signal, sources_scanned=tuple(sources_scanned), repos_scanned=tuple(repos_scanned))

        samples = _collect_samples(items)
        blob: dict = {
            "summary": {
                "scanned_commits": signal.scanned_commits,
                "scanned_prs": signal.scanned_prs,
                "ai_commits": signal.ai_commits,
                "ai_prs": signal.ai_prs,
                "footprint_pct": signal.footprint_pct,
                "per_tool": [list(p) for p in signal.per_tool],
                "per_author": [list(p) for p in signal.per_author],
                "per_activity": [list(p) for p in signal.per_activity],
                "per_source": [list(p) for p in signal.per_source],
                "repos_scanned": list(repos_scanned),
                "is_lower_bound": True,
            },
            "samples": samples,
            "coverage": coverage,
        }
        logger.info(
            "run_ai_adoption: scanned=%d ai=%d footprint=%.1f%% sources=%s",
            signal.scanned_commits + signal.scanned_prs,
            signal.ai_commits + signal.ai_prs,
            signal.footprint_pct,
            ",".join(sources_scanned) or "none",
        )
        return signal, blob
    except Exception:  # pragma: no cover - collect/aggregate already guard
        logger.exception("run_ai_adoption failed; returning empty signal")
        return AiAdoptionSignal(), {"summary": {}, "samples": [], "coverage": ["ai-usage scan failed"]}


def _collect_samples(items: list[dict], limit: int = 20) -> list[dict]:
    """Up to ``limit`` illustrative AI-marked items for the report (never bodies)."""
    out: list[dict] = []
    for item in items:
        tools = _classify_ai_markers(f"{item.get('title', '')}\n{item.get('body', '')}")
        if not tools:
            continue
        out.append(
            {
                "author": (item.get("author") or "").strip() or "unknown",
                "tool": sorted(tools)[0],
                "activity": _activity_bucket(item),
                "title": str(item.get("title", ""))[:80],
                "source": item.get("source", ""),
                "key": str(item.get("key", "")),
                "url": item.get("url", ""),
            }
        )
        if len(out) >= limit:
            break
    return out


def _pick_sample(samples: list[dict], **filters) -> dict | None:
    """First sample matching all ``filters`` (e.g. activity="code"), or None."""
    for s in samples:
        if all(str(s.get(k, "")) == str(v) for k, v in filters.items()):
            return s
    return samples[0] if samples and not filters else None


def _sample_ref(sample: dict) -> str:
    """Short human reference to a sampled item, e.g. "commit a1b2c3d4 'Fix login' by Dinho"."""
    kind = "PR" if sample.get("activity") == "pr" else "commit"
    key = sample.get("key", "") or ""
    title = (sample.get("title", "") or "").strip()
    author = (sample.get("author", "") or "").strip()
    ref = f"{kind} {key}".strip()
    if title:
        ref += f" '{title}'"
    if author and author != "unknown":
        ref += f" by {author}"
    return ref


def _with_link(item: dict, sample: dict | None) -> dict:
    """Attach a best-effort ``link`` (the sample's url) to an insight item when present."""
    if sample and sample.get("url"):
        item["link"] = sample["url"]
    return item


# ---------------------------------------------------------------------------
# Coaching insights — start / stop / keep / try (mirrors team_learning insights)
# ---------------------------------------------------------------------------

_LOWER_BOUND_NOTE = (
    "This footprint is a lower bound — it only counts AI tools that leave a marker in "
    "commit messages or PR descriptions. Inline IDE assist (Copilot ghost-text, Cursor "
    "Tab) leaves no trace, so real usage is at least this."
)


def _fallback_ai_adoption_insights(signal: AiAdoptionSignal, samples: list[dict] | None = None) -> dict:
    """Deterministic AI-adoption coaching when the LLM is unavailable.

    Pure — no LLM, no I/O, never raises. Every category is guaranteed non-empty so
    the screen always has content. Framed as a lower bound throughout. When
    ``samples`` are given, relevant items cite a concrete example (with a link).
    """
    from yeaboi.tools.team_learning import _INSIGHT_MAX_ITEMS, _insight_item

    samples = samples or []
    footprint = signal.footprint_pct
    scanned = signal.scanned_commits + signal.scanned_prs
    top_tool = signal.per_tool[0][0] if signal.per_tool else ""
    n_authors = len(signal.per_author)
    activity = dict(signal.per_activity)

    start: list[dict] = []
    stop: list[dict] = []
    keep: list[dict] = []
    try_items: list[dict] = []

    # START — grow adoption where it's thin.
    if footprint < 25:
        start.append(
            _insight_item(
                "Adopt an AI pairing tool team-wide",
                "Only a small share of tracked work shows an AI marker. Pick one tool and "
                "roll it out so the whole team benefits, not just early adopters.",
                f"{footprint:.0f}% detectable AI footprint across {scanned} commits/PRs",
            )
        )
    if activity.get("pr", 0) == 0 and signal.ai_commits > 0:
        # Cite an actual AI-marked code commit so the advice points at real work.
        raw_commit = _pick_sample(samples, activity="code") or _pick_sample(samples)
        evidence = "AI shows up in commits but no PRs were scanned"
        if raw_commit:
            evidence = f"e.g. {_sample_ref(raw_commit)} — an AI-assisted commit with no PR"
        start.append(
            _with_link(
                _insight_item(
                    "Use AI to draft PR descriptions",
                    "AI shows up in commits but not PR descriptions. Move that work through a PR "
                    "and have authors generate a first-draft summary — it improves review context.",
                    evidence,
                ),
                raw_commit,
            )
        )
    if not start:
        start.append(
            _insight_item(
                "Standardise AI co-author trailers",
                "Agree on a Co-Authored-By convention so AI-assisted work is visible and "
                "this footprint reflects reality more closely.",
                _LOWER_BOUND_NOTE,
            )
        )

    # STOP — avoid mismeasurement / over-reliance blind spots.
    if any(t == "other_ai" for t, _ in signal.per_tool):
        stop.append(
            _insight_item(
                "Stop relying on unlabelled AI trailers",
                "Some commits carry a generic AI co-author with no tool name. Standardising "
                "the tool makes adoption measurable and reviewable.",
                "Generic 'AI' co-author trailers detected",
            )
        )
    if not stop:
        stop.append(
            _insight_item(
                "Don't treat this number as the whole picture",
                "Inline AI assist is invisible here. Avoid concluding low usage from the "
                "footprint alone — pair it with a quick team check-in.",
                _LOWER_BOUND_NOTE,
            )
        )

    # KEEP — reinforce what's working.
    if top_tool:
        tool_sample = _pick_sample(samples, tool=top_tool)
        evidence = f"{top_tool} is the most-seen tool across scanned work"
        if tool_sample:
            evidence = f"e.g. {_sample_ref(tool_sample)} ({top_tool})"
        keep.append(
            _with_link(
                _insight_item(
                    f"Your investment in {top_tool}",
                    "There is a consistent AI footprint on the team's work — keep sharing "
                    "prompts and workflows so the habit spreads.",
                    evidence,
                ),
                tool_sample,
            )
        )
    if footprint >= 40:
        keep.append(
            _insight_item(
                "A healthy AI-assisted cadence",
                "A large share of tracked work already shows an AI trace — keep the momentum "
                "and capture what's working in a short playbook.",
                f"{footprint:.0f}% detectable footprint",
            )
        )
    if not keep:
        keep.append(
            _insight_item(
                "Making AI-assisted work visible",
                "Even a partial footprint means the team is leaving a trail — keep tagging "
                "AI-assisted commits so adoption stays measurable.",
                f"{scanned} commits/PRs scanned",
            )
        )

    # TRY — experiments to broaden or deepen adoption.
    if n_authors and n_authors <= 3 and scanned > 0:
        try_items.append(
            _insight_item(
                "Run an AI-tooling brown-bag",
                "AI markers cluster on a few people. A 30-minute demo from an adopter often "
                "unblocks the rest of the team.",
                f"AI markers seen from {n_authors} author(s)",
            )
        )
    if activity.get("docs", 0) == 0:
        try_items.append(
            _insight_item(
                "Try AI for documentation, not just code",
                "No AI footprint on docs/README changes. Drafting docs with AI is a low-risk way to widen adoption.",
                "No AI markers on documentation-shaped commits",
            )
        )
    if not try_items:
        try_items.append(
            _insight_item(
                "A shared prompt library",
                "Collect the prompts your adopters use into a team doc — it turns individual "
                "wins into a repeatable practice.",
                _LOWER_BOUND_NOTE,
            )
        )

    return {
        "start": start[:_INSIGHT_MAX_ITEMS],
        "stop": stop[:_INSIGHT_MAX_ITEMS],
        "keep": keep[:_INSIGHT_MAX_ITEMS],
        "try": try_items[:_INSIGHT_MAX_ITEMS],
    }


def generate_ai_adoption_insights(signal: AiAdoptionSignal, examples: dict) -> dict:
    """Use the LLM to coach on AI adoption: start / stop / keep / try.

    Returns ``{"start": [...], "stop": [...], "keep": [...], "try": [...]}`` where
    each item is ``{"title", "detail", "evidence"}``. Falls back to deterministic
    insights on any failure — must never raise (runs inside the analysis pipeline).
    The prompt explicitly frames the footprint as a lower bound.
    """
    import json

    from yeaboi.tools.team_learning import _INSIGHT_KEYS, _INSIGHT_MAX_ITEMS, _insight_item, _llm_invoke

    samples = examples.get("samples", []) if isinstance(examples, dict) else []
    fallback = _fallback_ai_adoption_insights(signal, samples)

    # Valid link set — LLM-returned links are accepted only if they cite a real sample.
    valid_links = {str(s.get("url", "")) for s in samples if s.get("url")}

    per_tool = ", ".join(f"{t}={n}" for t, n in signal.per_tool) or "none detected"
    per_activity = ", ".join(f"{a}={n}" for a, n in signal.per_activity) or "none"
    per_source = ", ".join(f"{_source_label(s)}={n}" for s, n in signal.per_source) or "none"
    digest = (
        f"Scanned {signal.scanned_commits} commits and {signal.scanned_prs} PRs from sources: "
        f"{', '.join(_source_label(s) for s in signal.sources_scanned) or 'none'}.\n"
        f"AI-marked: {signal.ai_commits} commits, {signal.ai_prs} PRs "
        f"(detectable footprint {signal.footprint_pct:.0f}%).\n"
        f"By tool: {per_tool}.\n"
        f"By activity type: {per_activity}.\n"
        f"By source: {per_source}.\n"
        f"AI markers seen from {len(signal.per_author)} distinct author(s)."
    )

    # Concrete items the LLM can cite (with links) so coaching points at real work.
    example_lines = []
    for s in samples[:12]:
        ref = _sample_ref(s)
        url = s.get("url", "")
        example_lines.append(f"- [{_source_label(s.get('source', ''))}] {ref}" + (f" — {url}" if url else ""))
    examples_block = "\n".join(example_lines) or "(no illustrative samples available)"

    # See docs: "Prompt Construction" — ARC: Ask (coach adoption), Requirements
    # (categories, item shape, lower-bound honesty), Context (footprint digest).
    prompt = (
        "You are an engineering enablement coach helping a team lead grow effective, "
        "healthy use of AI coding tools. A scan of the team's commits and pull requests "
        "for AI-tool markers produced the digest below.\n\n"
        "CRITICAL framing: this footprint is a LOWER BOUND. It only counts AI tools that "
        "leave a textual marker in commit messages or PR descriptions; inline IDE assist "
        "(Copilot autocomplete, Cursor Tab) leaves no trace. Never claim the team does not "
        "use AI from a low number — coach on making usage more visible, broader, and more "
        "effective.\n\n"
        "Requirements:\n"
        '- Four categories: "start" (things to start), "stop" (things to stop/avoid), '
        '"keep" (things working well), "try" (experiments worth trying).\n'
        '- 2-4 items per category. Each item: "title" (imperative, max 10 words), '
        '"detail" (1-2 plain-English sentences of practical advice), "evidence" (one short '
        'phrase; where possible cite a specific example from the list below, e.g. "e.g. commit '
        'a1b2c3d4 \'Fix login\'"), and optionally "link" (the exact URL of that example, copied '
        "verbatim from the list — omit if none applies).\n"
        "- Prefer coaching that references a real example: 'here is where you did Y (link), do X "
        "instead'. Do NOT invent links; only use URLs from the list.\n"
        "- Ground every item in the digest. At least one item must remind the lead the "
        "footprint is a lower bound.\n\n"
        "## Footprint digest\n" + digest + "\n\n"
        "## Examples you can cite (use these exact URLs)\n" + examples_block + "\n\n"
        "Return ONLY a JSON object: "
        '{"start": [{"title": "...", "detail": "...", "evidence": "...", "link": "..."}], '
        '"stop": [...], "keep": [...], "try": [...]}'
    )

    try:
        response = _llm_invoke(prompt, temperature=0.0)
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        result = json.loads(text)
        if isinstance(result, dict):
            insights: dict = {}
            for key in _INSIGHT_KEYS:
                raw = result.get(key)
                items = []
                if isinstance(raw, list):
                    for it in raw:
                        if isinstance(it, dict) and isinstance(it.get("title"), str) and it["title"].strip():
                            item = _insight_item(
                                it["title"].strip(),
                                it["detail"].strip() if isinstance(it.get("detail"), str) else "",
                                it["evidence"].strip() if isinstance(it.get("evidence"), str) else "",
                            )
                            # Accept a link only if it cites a real sample URL (no hallucinations).
                            link = it.get("link")
                            if isinstance(link, str) and link.strip() in valid_links:
                                item["link"] = link.strip()
                            items.append(item)
                insights[key] = items[:_INSIGHT_MAX_ITEMS] if items else fallback[key]
            logger.info(
                "LLM AI-adoption insights generated (%s)",
                ", ".join(f"{k}={len(v)}" for k, v in insights.items()),
            )
            return insights
        logger.warning("LLM AI-adoption insights had unexpected shape; using fallback")
    except Exception as exc:
        logger.warning("LLM AI-adoption insights generation failed: %s", exc)

    return fallback
