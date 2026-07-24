"""Documentation quality — is the team's written knowledge clear, and how does AI show up in it?

# See docs: "Architecture" — engines are UI-free pipelines; this is a sub-analysis
# of team-analysis mode (CLAUDE.md "REQUIRED: Surface Parity" — the TUI/CLI/MCP are
# thin adapters over ``analysis/engine.py:run_team_analysis``, which calls into here).

What this does
--------------
Reads the pages the team has recently written or updated in Notion & Confluence
(pairing the ``*_recent_pages`` metadata helpers with the never-raise
``*_read_page_text`` body readers), then per page computes:

- a **clarity score** (deterministic, readability-based; 0–100, higher = clearer), and
- an **AI-likelihood estimate** (heuristic stylometry over prose features), plus
- an **explicit AI-marker** check (a pasted "Generated with Claude" style disclosure).

It aggregates those into a :class:`DocQualitySignal` and coaches the lead on writing
clearer docs and using AI effectively in them (start / stop / keep / try).

Honesty contract — two different confidence levels, never conflated
-------------------------------------------------------------------
Clarity is a heuristic readability score. The AI-likelihood is a stylometric
**ESTIMATE**, not a detection — prose (unlike a commit) carries no reliable AI
marker, so the number is a conversation-starter, never proof. Explicit AI markers
are a genuine **lower bound**. Every surface must frame it that way;
``DocQualitySignal.is_ai_estimate`` stays ``True`` to force it.

Error contract
--------------
Everything here is best-effort and NEVER raises: a missing SDK/credential or a
failing platform contributes zero and is recorded as a coverage gap.
``run_doc_quality`` wraps the whole thing so the analysis pipeline can call it
unguarded.
"""

from __future__ import annotations

import logging
import re

from yeaboi.analysis.ai_usage import _classify_ai_markers
from yeaboi.team_profile import DocQualitySignal

logger = logging.getLogger(__name__)

# Look-back window and per-platform cap. The recent-page helpers already page a
# bounded set; this keeps the scan a "recent sample" for a quality read, not an
# exhaustive audit, and bounds the number of body-read API calls.
_SCAN_DAYS = 90
_MAX_PAGES_PER_PLATFORM = 20
_READ_CHARS = 8_000  # per-page body budget fed to the analysis

# Clarity score bands (0–100, higher = clearer). Aligned to Flesch reading-ease:
# ~60 is "plain English", below ~40 is "difficult".
_CLEAR_MIN = 60.0
_UNCLEAR_MAX = 40.0
# A page whose stylometric AI-likelihood crosses this is counted "likely AI" — an
# ESTIMATE, deliberately conservative.
_AI_LIKELY_MIN = 55.0

_ESTIMATE_NOTE = (
    "AI-likelihood is a heuristic estimate from writing style, not a detection — prose "
    "carries no reliable AI marker. Treat it as a prompt for a conversation, not proof."
)

# Phrases that skew AI-generated prose (formal connectors + LLM tics). Not proof on
# their own — they feed a weighted estimate, never a verdict.
_AI_TELL_PHRASES: tuple[str, ...] = (
    "moreover",
    "furthermore",
    "in conclusion",
    "in summary",
    "it's worth noting",
    "it is worth noting",
    "it is important to note",
    "delve",
    "leverage",
    "seamless",
    "robust",
    "firstly",
    "additionally",
    "notably",
    "utilize",
    "underscore",
    "a testament to",
    "in the realm of",
    "tapestry",
    "navigating",
    "holistic",
    "paramount",
    "facilitate",
    "streamline",
)


# ---------------------------------------------------------------------------
# Per-page heuristics — pure, deterministic, no I/O (the core unit-test seams)
# ---------------------------------------------------------------------------


def _count_syllables(word: str) -> int:
    """Rough syllable count for the Flesch approximation — vowel groups, silent-e trim."""
    groups = re.findall(r"[aeiouy]+", word.lower())
    n = len(groups)
    if word.lower().endswith("e") and n > 1:
        n -= 1
    return max(1, n)


def _clarity_metrics(text: str) -> dict:
    """Deterministic readability metrics for one page.

    Returns a dict with ``word_count``, ``sentence_count``, ``avg_sentence_words``,
    ``long_sentence_pct``, ``heading_count``, ``has_lists`` and a **clarity** score
    (0–100, higher = clearer) from a Flesch reading-ease approximation plus a small
    structure bonus (headings/lists aid a doc's clarity). Pure — no I/O.
    """
    sentences = [s for s in re.split(r"[.!?]+(?:\s|$)", text) if s.strip()]
    words = re.findall(r"[A-Za-z']+", text)
    n_sentences = len(sentences)
    n_words = len(words)
    if n_words == 0 or n_sentences == 0:
        return {
            "word_count": n_words,
            "sentence_count": n_sentences,
            "avg_sentence_words": 0.0,
            "long_sentence_pct": 0.0,
            "heading_count": 0,
            "has_lists": False,
            "clarity": 0.0,
        }

    avg_sentence_words = n_words / n_sentences
    long_sentences = sum(1 for s in sentences if len(s.split()) > 25)
    long_sentence_pct = round(long_sentences / n_sentences * 100, 1)
    syllables = sum(_count_syllables(w) for w in words)

    heading_count = len(re.findall(r"(?m)^\s{0,3}#{1,6}\s", text))
    has_lists = bool(re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s", text))

    # Flesch Reading Ease — higher = easier to read. Clamp to 0–100.
    flesch = 206.835 - 1.015 * avg_sentence_words - 84.6 * (syllables / n_words)
    clarity = flesch
    # Small structure bonus: a doc with headings/lists reads more clearly than a wall.
    if heading_count:
        clarity += 4
    if has_lists:
        clarity += 3
    clarity = max(0.0, min(100.0, clarity))

    return {
        "word_count": n_words,
        "sentence_count": n_sentences,
        "avg_sentence_words": round(avg_sentence_words, 1),
        "long_sentence_pct": long_sentence_pct,
        "heading_count": heading_count,
        "has_lists": has_lists,
        "clarity": round(clarity, 1),
    }


def _ai_likelihood(text: str) -> float:
    """Heuristic 0–100 ESTIMATE that a page's prose was AI-generated.

    Pure, no I/O. Sums weighted, capped signals: em-dash density, LLM-tell phrase
    rate, unusually low contraction rate, "not only … but also" cadence, and uniform
    paragraph lengths. This is a stylometric *guess*, not a detector — see the module
    docstring. Returns 0.0 for empty text.
    """
    if not text.strip():
        return 0.0
    lower = text.lower()
    words = re.findall(r"[a-z']+", lower)
    n_words = len(words) or 1
    score = 0.0

    # Em-dashes per 1000 words — AI prose leans on them heavily.
    em_dashes = text.count("—")
    score += min(25.0, em_dashes / n_words * 1000 * 3.0)

    # LLM-tell connector/filler phrases per 1000 words.
    tell_hits = sum(lower.count(p) for p in _AI_TELL_PHRASES)
    score += min(40.0, tell_hits / n_words * 1000 * 4.0)

    # Formality: AI drafts rarely use contractions.
    contractions = len(re.findall(r"\b\w+'(?:t|re|ll|ve|d|s|m)\b", lower))
    if n_words >= 80 and contractions / n_words < 0.004:
        score += 15.0

    # Tricolon "not only … but also" cadence.
    if "not only" in lower and "but also" in lower:
        score += 8.0

    # Uniform paragraph lengths (low coefficient of variation) — AI output is even.
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) >= 3:
        lengths = [len(p.split()) for p in paras]
        mean = sum(lengths) / len(lengths)
        if mean > 0:
            var = sum((length - mean) ** 2 for length in lengths) / len(lengths)
            if (var**0.5) / mean < 0.35:
                score += 12.0

    return round(min(100.0, score), 1)


def aggregate_doc_quality(pages: list[dict]) -> DocQualitySignal:
    """Aggregate scanned page dicts into a :class:`DocQualitySignal`.

    Pure over its input (no network). Each page is a normalized dict with
    ``platform``, ``title``, ``text`` (+ optional ``author``/``url``). Returns an
    all-zero signal for an empty list.
    """
    if not pages:
        return DocQualitySignal()

    platforms: list[str] = []
    per_platform: dict[str, int] = {}
    clarities: list[float] = []
    ai_scores: list[float] = []
    clear = mixed = unclear = likely_ai = ai_marked = 0
    # (clarity, likelihood, title) per page — used to pick flagged call-outs.
    scored: list[tuple[float, float, str]] = []

    for page in pages:
        text = str(page.get("text", ""))
        platform = str(page.get("platform", "")).strip()
        title = str(page.get("title", "Untitled")).strip()[:80] or "Untitled"
        if platform and platform not in platforms:
            platforms.append(platform)
        per_platform[platform] = per_platform.get(platform, 0) + 1

        clarity = _clarity_metrics(text)["clarity"]
        clarities.append(clarity)
        if clarity >= _CLEAR_MIN:
            clear += 1
        elif clarity < _UNCLEAR_MAX:
            unclear += 1
        else:
            mixed += 1

        likelihood = _ai_likelihood(text)
        ai_scores.append(likelihood)
        if likelihood >= _AI_LIKELY_MIN:
            likely_ai += 1
        if _classify_ai_markers(text):
            ai_marked += 1

        scored.append((clarity, likelihood, title))

    avg_clarity = round(sum(clarities) / len(clarities), 1) if clarities else 0.0
    avg_ai = round(sum(ai_scores) / len(ai_scores), 1) if ai_scores else 0.0

    # Flagged call-outs: the least-clear pages and the most-AI-likely pages, deduped.
    flagged: list[tuple[str, str]] = []
    seen_titles: set[str] = set()
    for clarity, _lk, title in sorted(scored, key=lambda x: x[0])[:2]:
        if clarity < _CLEAR_MIN and title not in seen_titles:
            flagged.append((title, f"clarity {clarity:.0f}/100 — dense or long-winded"))
            seen_titles.add(title)
    for _cl, likelihood, title in sorted(scored, key=lambda x: -x[1])[:2]:
        if likelihood >= _AI_LIKELY_MIN and title not in seen_titles:
            flagged.append((title, f"reads as AI-generated (estimate {likelihood:.0f}/100)"))
            seen_titles.add(title)

    def _sorted_pairs(d: dict[str, int]) -> tuple[tuple[str, int], ...]:
        return tuple(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))

    return DocQualitySignal(
        pages_scanned=len(pages),
        platforms_scanned=tuple(platforms),
        avg_clarity=avg_clarity,
        clear_pages=clear,
        mixed_pages=mixed,
        unclear_pages=unclear,
        avg_ai_likelihood=avg_ai,
        likely_ai_pages=likely_ai,
        ai_marked_pages=ai_marked,
        per_platform=_sorted_pairs(per_platform),
        flagged_pages=tuple(flagged[:4]),
        is_ai_estimate=True,
    )


# ---------------------------------------------------------------------------
# Data gathering — graceful, best-effort fan-out (mirrors analysis/ai_usage.py)
# ---------------------------------------------------------------------------


def _fetch_confluence_pages() -> list[dict]:
    """Recent Confluence pages, each paired with its body text. Lazily imports the SDK-backed tool."""
    from yeaboi.tools.confluence import confluence_read_page_text, confluence_recent_pages

    recent = confluence_recent_pages(days=_SCAN_DAYS)
    return _read_bodies(recent, lambda pid: confluence_read_page_text(page_id=pid, max_chars=_READ_CHARS))


def _fetch_notion_pages() -> list[dict]:
    """Recent Notion pages, each paired with its body text. Lazily imports the SDK-backed tool."""
    from yeaboi.tools.notion import notion_read_page_text, notion_recent_pages

    recent = notion_recent_pages(days=_SCAN_DAYS)
    return _read_bodies(recent, lambda pid: notion_read_page_text(pid, max_chars=_READ_CHARS))


def _read_bodies(recent: list[dict], reader) -> list[dict]:
    """Pair recent-page metadata with body text via ``reader(page_id) -> {title,text,…}``.

    Dedupes by page id (Confluence emits one item per editor), caps the number of
    body reads, and drops pages with no readable text. ``reader`` never raises.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for meta in recent:
        page_id = str(meta.get("key", "")).strip()
        if not page_id or page_id in seen:
            continue
        seen.add(page_id)
        doc = reader(page_id)
        text = doc.get("text", "") if isinstance(doc, dict) else ""
        if not text.strip():
            continue
        out.append(
            {
                "title": meta.get("title") or (doc.get("title", "") if isinstance(doc, dict) else "") or "Untitled",
                "author": meta.get("author", ""),
                "url": meta.get("url", ""),
                "key": page_id,
                "timestamp": meta.get("timestamp", ""),
                "text": text,
            }
        )
        if len(out) >= _MAX_PAGES_PER_PLATFORM:
            break
    return out


def collect_doc_pages(
    source: str, project_key: str, sub_sources: list[str] | None = None
) -> tuple[list[dict], list[str], list[str]]:
    """Fan out over Confluence + Notion for recently-changed pages with their body text.

    Returns ``(pages, platforms_scanned, coverage_notes)``. Every platform is
    best-effort and lazily imported (optional SDKs); a missing credential/SDK or a
    failing platform contributes zero and is added to ``coverage_notes`` so absent
    coverage is visible rather than silent. Never raises. ``source``/``project_key``
    are accepted for signature parity with the other sub-analyses; doc platforms are
    resolved purely from their own config. ``sub_sources`` restricts which platforms
    to read (subset of ``{"confluence", "notion"}``; None = both).
    """
    from yeaboi.config import get_confluence_base_url, get_confluence_token, get_notion_token

    def _want(tag: str) -> bool:
        return sub_sources is None or tag in sub_sources

    pages: list[dict] = []
    platforms_scanned: list[str] = []
    coverage: list[str] = []

    def _run(name: str, tag: str, fetcher) -> None:
        try:
            raw = fetcher()
        except ImportError as e:
            logger.warning("Doc-quality source %s skipped — SDK not installed: %s", name, e)
            coverage.append(f"{name}: SDK not installed")
            return
        except Exception as e:  # helpers already guard; never let one platform abort
            logger.warning("Doc-quality source %s failed: %s", name, e)
            coverage.append(f"{name}: error ({e})")
            return
        if not raw:
            coverage.append(f"{name}: no pages changed in the last {_SCAN_DAYS} days")
            return
        for page in raw:
            page["platform"] = tag
            pages.append(page)
        platforms_scanned.append(tag)
        logger.info("Doc-quality source %s contributed %d page(s)", name, len(raw))

    # Confluence — reuses the Jira Atlassian creds unless CONFLUENCE_* is set.
    if _want("confluence") and get_confluence_token() and get_confluence_base_url():
        _run("confluence", "confluence", _fetch_confluence_pages)
    elif _want("confluence"):
        coverage.append("confluence: CONFLUENCE_API_TOKEN / base URL not set")

    # Notion — standalone integration token.
    if _want("notion") and get_notion_token():
        _run("notion", "notion", _fetch_notion_pages)
    elif _want("notion"):
        coverage.append("notion: NOTION_TOKEN not set")

    return pages, platforms_scanned, coverage


def _collect_samples(pages: list[dict], limit: int = 12) -> list[dict]:
    """Up to ``limit`` illustrative page call-outs for the report (never page bodies)."""
    out: list[dict] = []
    for page in pages:
        text = str(page.get("text", ""))
        out.append(
            {
                "title": str(page.get("title", "Untitled"))[:80],
                "platform": page.get("platform", ""),
                "clarity": _clarity_metrics(text)["clarity"],
                "ai_likelihood": _ai_likelihood(text),
                "marked": bool(_classify_ai_markers(text)),
                "url": page.get("url", ""),
            }
        )
        if len(out) >= limit:
            break
    return out


def run_doc_quality(
    source: str, project_key: str, sub_sources: list[str] | None = None
) -> tuple[DocQualitySignal, dict]:
    """Orchestrate the doc-quality scan: collect recent pages → score → aggregate.

    Returns ``(signal, examples_blob)``. ``examples_blob`` carries the aggregated
    summary, up to ~12 illustrative page samples (titles/scores only — never bodies),
    and coverage notes. Wholly best-effort — any failure yields an empty signal and a
    coverage note, never an exception (the pipeline calls this unguarded).
    """
    logger.info("run_doc_quality: source=%s project=%s", source, project_key)
    try:
        pages, platforms_scanned, coverage = collect_doc_pages(source, project_key, sub_sources)
        signal = aggregate_doc_quality(pages)

        samples = _collect_samples(pages)
        blob: dict = {
            "summary": {
                "pages_scanned": signal.pages_scanned,
                "platforms_scanned": list(signal.platforms_scanned),
                "avg_clarity": signal.avg_clarity,
                "clear_pages": signal.clear_pages,
                "mixed_pages": signal.mixed_pages,
                "unclear_pages": signal.unclear_pages,
                "avg_ai_likelihood": signal.avg_ai_likelihood,
                "likely_ai_pages": signal.likely_ai_pages,
                "ai_marked_pages": signal.ai_marked_pages,
                "per_platform": [list(p) for p in signal.per_platform],
                "flagged_pages": [list(p) for p in signal.flagged_pages],
                "is_ai_estimate": True,
            },
            "samples": samples,
            "coverage": coverage,
        }
        logger.info(
            "run_doc_quality: pages=%d avg_clarity=%.0f avg_ai=%.0f marked=%d platforms=%s",
            signal.pages_scanned,
            signal.avg_clarity,
            signal.avg_ai_likelihood,
            signal.ai_marked_pages,
            ",".join(platforms_scanned) or "none",
        )
        return signal, blob
    except Exception:  # pragma: no cover - collect/aggregate already guard
        logger.exception("run_doc_quality failed; returning empty signal")
        return DocQualitySignal(), {"summary": {}, "samples": [], "coverage": ["doc-quality scan failed"]}


# ---------------------------------------------------------------------------
# Coaching insights — start / stop / keep / try (mirrors ai_usage insights)
# ---------------------------------------------------------------------------


def _doc_ref(sample: dict) -> str:
    """Short human reference to a sampled page, e.g. "'Onboarding' (confluence, clarity 30/100)"."""
    title = (sample.get("title", "") or "Untitled").strip()
    platform = sample.get("platform", "") or ""
    return f"'{title}' ({platform}, clarity {sample.get('clarity', 0):.0f}/100)"


def _with_doc_link(item: dict, sample: dict | None) -> dict:
    """Attach a best-effort ``link`` (the page url) to an insight item when present."""
    if sample and sample.get("url"):
        item["link"] = sample["url"]
    return item


def _fallback_doc_quality_insights(signal: DocQualitySignal, samples: list[dict] | None = None) -> dict:
    """Deterministic doc-quality coaching when the LLM is unavailable.

    Pure — no LLM, no I/O, never raises. Every category is guaranteed non-empty so
    the screen always has content. Clarity is framed as a score, AI-likelihood as an
    estimate, and explicit markers as a lower bound throughout. When ``samples`` are
    given, relevant items cite a concrete page (with a link).
    """
    from yeaboi.tools.team_learning import _INSIGHT_MAX_ITEMS, _insight_item

    samples = samples or []
    # Representative pages: the least-clear and the most-AI-likely, for concrete call-outs.
    least_clear = min(samples, key=lambda s: s.get("clarity", 100), default=None) if samples else None
    most_ai = max(samples, key=lambda s: s.get("ai_likelihood", 0), default=None) if samples else None

    pages = signal.pages_scanned
    clarity = signal.avg_clarity
    ai_est = signal.avg_ai_likelihood

    start: list[dict] = []
    stop: list[dict] = []
    keep: list[dict] = []
    try_items: list[dict] = []

    # START — raise the clarity floor.
    if clarity < 55 and pages:
        evidence = f"Average clarity {clarity:.0f}/100 across {pages} page(s)"
        if least_clear:
            evidence = f"e.g. {_doc_ref(least_clear)} — the least-clear page scanned"
        start.append(
            _with_doc_link(
                _insight_item(
                    "Tighten the least-clear pages",
                    "Several docs read as dense or long-winded. Shorter sentences, headings and "
                    "bullet lists make them faster to act on.",
                    evidence,
                ),
                least_clear,
            )
        )
    if signal.unclear_pages:
        start.append(
            _insight_item(
                "Add structure to wall-of-text docs",
                "Break the hardest-to-read pages into sections with headings and lists — "
                "structure is the cheapest clarity win.",
                f"{signal.unclear_pages} page(s) scored unclear",
            )
        )
    if not start:
        start.append(
            _insight_item(
                "Set a shared clarity bar",
                "Agree a lightweight doc standard (a lead paragraph, headings, short sentences) "
                "so new pages start clear.",
                f"{pages} page(s) scanned" if pages else "No pages scanned yet",
            )
        )

    # STOP — avoid unedited AI output and dense prose.
    if ai_est >= _AI_LIKELY_MIN:
        evidence = f"Estimated AI-likelihood {ai_est:.0f}/100 — {_ESTIMATE_NOTE}"
        ai_sample = most_ai if most_ai and most_ai.get("ai_likelihood", 0) >= _AI_LIKELY_MIN else None
        if ai_sample:
            evidence = (
                f"e.g. {_doc_ref(ai_sample)} reads as AI-drafted "
                f"(estimate {ai_sample.get('ai_likelihood', 0):.0f}/100) — {_ESTIMATE_NOTE}"
            )
        stop.append(
            _with_doc_link(
                _insight_item(
                    "Stop shipping unedited AI drafts",
                    "Some pages read like raw AI output. Have authors edit for the team's voice and "
                    "concrete detail before publishing.",
                    evidence,
                ),
                ai_sample,
            )
        )
    if not stop:
        stop.append(
            _insight_item(
                "Don't publish walls of text",
                "Long unbroken paragraphs slow everyone down. Split them and lead with the point.",
                _ESTIMATE_NOTE,
            )
        )

    # KEEP — reinforce what's working.
    if clarity >= 60:
        keep.append(
            _insight_item(
                "Your clear, structured writing",
                "The team's docs are largely readable — keep leading with the point and using "
                "headings so the habit sticks.",
                f"Average clarity {clarity:.0f}/100",
            )
        )
    if signal.ai_marked_pages:
        keep.append(
            _insight_item(
                "Disclosing AI-assisted docs",
                "Some pages explicitly note AI assistance — keep that transparency; it builds "
                "trust and makes review easier.",
                f"{signal.ai_marked_pages} page(s) carry an explicit AI marker (a lower bound)",
            )
        )
    if not keep:
        keep.append(
            _insight_item(
                "Keeping docs current",
                "The team is actively updating its written knowledge — keep that cadence and the "
                "docs stay worth trusting.",
                f"{pages} page(s) changed recently" if pages else "Keep updating shared docs",
            )
        )

    # TRY — experiments to raise quality and use AI well.
    if signal.likely_ai_pages:
        try_items.append(
            _insight_item(
                "Try an AI-draft-then-edit workflow",
                "Where AI is already drafting docs, add a short human edit pass for accuracy and "
                "team-specific context — best of both.",
                f"~{signal.likely_ai_pages} page(s) look AI-drafted (estimate)",
            )
        )
    if clarity and clarity < 65:
        try_items.append(
            _insight_item(
                "Ask AI to simplify a dense page",
                "Pick the least-clear doc and prompt an AI tool to rewrite it at a plain-English "
                "reading level, then review — a fast clarity experiment.",
                f"Average clarity {clarity:.0f}/100",
            )
        )
    if not try_items:
        try_items.append(
            _insight_item(
                "A shared doc template",
                "Capture a page template (purpose, TL;DR, sections) so every new doc starts clear and consistent.",
                f"{pages} page(s) scanned" if pages else "Start with a template",
            )
        )

    return {
        "start": start[:_INSIGHT_MAX_ITEMS],
        "stop": stop[:_INSIGHT_MAX_ITEMS],
        "keep": keep[:_INSIGHT_MAX_ITEMS],
        "try": try_items[:_INSIGHT_MAX_ITEMS],
    }


def generate_doc_quality_insights(signal: DocQualitySignal, examples: dict) -> dict:
    """Use the LLM to coach on doc clarity + AI usage: start / stop / keep / try.

    Returns ``{"start": [...], "stop": [...], "keep": [...], "try": [...]}`` where
    each item is ``{"title", "detail", "evidence"}``. Falls back to deterministic
    insights on any failure — must never raise (runs inside the analysis pipeline).
    The prompt frames clarity as a score, AI-likelihood as an estimate, and explicit
    markers as a lower bound.
    """
    import json

    from yeaboi.tools.team_learning import _INSIGHT_KEYS, _INSIGHT_MAX_ITEMS, _insight_item, _llm_invoke

    samples = examples.get("samples", []) if isinstance(examples, dict) else []
    fallback = _fallback_doc_quality_insights(signal, samples)

    # Valid link set — LLM-returned links are accepted only if they cite a real page.
    valid_links = {str(s.get("url", "")) for s in samples if s.get("url")}

    per_platform = ", ".join(f"{p}={n}" for p, n in signal.per_platform) or "none"
    flagged = "; ".join(f"{title} ({reason})" for title, reason in signal.flagged_pages) or "none"
    digest = (
        f"Scanned {signal.pages_scanned} recently-changed page(s) across: "
        f"{', '.join(signal.platforms_scanned) or 'none'} (by platform: {per_platform}).\n"
        f"Clarity (0-100, higher=clearer): average {signal.avg_clarity:.0f} "
        f"— {signal.clear_pages} clear, {signal.mixed_pages} mixed, {signal.unclear_pages} unclear.\n"
        f"AI-likelihood ESTIMATE (stylometric, not a detection): average {signal.avg_ai_likelihood:.0f}/100, "
        f"~{signal.likely_ai_pages} page(s) look AI-drafted.\n"
        f"Explicit AI markers (lower bound): {signal.ai_marked_pages} page(s).\n"
        f"Flagged pages: {flagged}."
    )

    # Concrete pages the LLM can cite (with links) so coaching points at real docs.
    example_lines = []
    for s in samples[:12]:
        url = s.get("url", "")
        line = (
            f"- '{s.get('title', 'Untitled')}' ({s.get('platform', '')}, "
            f"clarity {s.get('clarity', 0):.0f}/100, AI-likelihood {s.get('ai_likelihood', 0):.0f}/100)"
        )
        example_lines.append(line + (f" — {url}" if url else ""))
    examples_block = "\n".join(example_lines) or "(no illustrative pages available)"

    # See docs: "Prompt Construction" — ARC: Ask (coach doc quality + AI usage),
    # Requirements (categories, item shape, estimate/lower-bound honesty), Context (digest).
    prompt = (
        "You are a technical-writing and enablement coach helping a team lead improve the "
        "clarity of their written docs (Notion/Confluence) and use AI effectively in them. A "
        "scan of the team's recently-changed pages produced the digest below.\n\n"
        "CRITICAL framing:\n"
        "- Clarity is a heuristic readability score, not a grade on correctness.\n"
        "- AI-likelihood is a STYLOMETRIC ESTIMATE from writing style, NOT a detection — prose "
        "carries no reliable AI marker. Never assert a page 'was written by AI'; coach on "
        "editing and effective use instead.\n"
        "- The explicit-AI-marker count is a LOWER BOUND (only pages with a pasted disclosure).\n\n"
        "Requirements:\n"
        '- Four categories: "start" (things to start), "stop" (things to stop/avoid), '
        '"keep" (things working well), "try" (experiments worth trying).\n'
        '- 2-4 items per category. Each item: "title" (imperative, max 10 words), '
        '"detail" (1-2 plain-English sentences of practical advice), "evidence" (one short '
        "phrase; where possible cite a specific page from the list below, e.g. \"e.g. 'Onboarding' "
        'reads as dense"), and optionally "link" (the exact URL of that page, copied verbatim '
        "from the list — omit if none applies).\n"
        "- Prefer coaching that references a real page: 'here is the page (link), do X'. Do NOT "
        "invent links; only use URLs from the list.\n"
        "- Ground every item in the digest. At least one item must remind the lead the "
        "AI-likelihood is an estimate, not a detection.\n\n"
        "## Doc-quality digest\n" + digest + "\n\n"
        "## Pages you can cite (use these exact URLs)\n" + examples_block + "\n\n"
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
                            # Accept a link only if it cites a real page URL (no hallucinations).
                            link = it.get("link")
                            if isinstance(link, str) and link.strip() in valid_links:
                                item["link"] = link.strip()
                            items.append(item)
                insights[key] = items[:_INSIGHT_MAX_ITEMS] if items else fallback[key]
            logger.info(
                "LLM doc-quality insights generated (%s)",
                ", ".join(f"{k}={len(v)}" for k, v in insights.items()),
            )
            return insights
        logger.warning("LLM doc-quality insights had unexpected shape; using fallback")
    except Exception as exc:
        logger.warning("LLM doc-quality insights generation failed: %s", exc)

    return fallback
