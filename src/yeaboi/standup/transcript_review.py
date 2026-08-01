"""Audit a standup report against the meeting that discussed it.

The pipeline, in the order it runs:

1. **Attribute speakers** (deterministic) — map raw transcript labels onto roster
   names using the engine's own alias machinery, so a speaker labelled by their
   GitHub handle still resolves.
2. **Extract claims** (the LLM's only job) — one ``invoke_json`` call per
   transcript date: what did each person say they did, and does it appear in the
   evidence the report already had? The model never names a root cause.
3. **Clamp** (deterministic) — drop anything unverifiable. A quote that is not
   literally in the transcript, a member not on the roster, a match the evidence
   contradicts. Mirrors the per-field clamps in ``engine._summarize_members``.
4. **Classify** (deterministic) — ``gap_taxonomy`` turns each surviving unmatched
   claim into a root cause, or into nothing.

Nothing here reaches GitHub. This module does not import ``gap_issues`` at all,
which is the structural guarantee behind "draft, then confirm": the drafting
path has no code that could file an issue even by accident.

Like every other standup LLM path, an unavailable or failing model is a WARNING,
never an exception — the review degrades to what can be computed from the report
alone and says so.

# See docs: "The ReAct Loop" — parse → fallback → format
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from yeaboi.agent.state import StandupGap, StandupReport, TranscriptClaim, TranscriptReview, TranscriptSource
from yeaboi.standup import gap_taxonomy, transcripts

logger = logging.getLogger(__name__)

# Caps, in the spirit of insights._MAX_SIGNALS_PER_MEMBER: a review that reports
# everything reports nothing.
_MAX_CLAIMS_PER_MEMBER = 6
_MAX_GAPS_PER_REVIEW = 8
# Per-transcript and per-review prompt budgets (characters, ~4 per token).
_TRANSCRIPT_PROMPT_CHARS = 60_000
# Evidence sent per member — recognition needs breadth, not depth. Capped BOTH
# per member and in total: a 12-person roster at 20 items each doubled the
# prompt, and this call already sits on the standup critical path.
_EVIDENCE_PER_MEMBER = 20
_MAX_TOTAL_EVIDENCE = 120
_MIN_EVIDENCE_PER_MEMBER = 4
_TITLE_CLIP = 80

_VALID_STATUS = ("matched", "missing", "contradicted", "unclear")

# Whitespace-insensitive quote grounding: transcripts get re-flowed by the
# parsers (continuation lines are joined), so an exact-substring check on raw
# text would reject quotes that really are present.
_WS_RE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


# ---------------------------------------------------------------------------
# 1. Speaker attribution
# ---------------------------------------------------------------------------


def resolve_speakers(speakers: tuple[str, ...], alias_map: dict[str, set[str]]) -> dict[str, str]:
    """Map raw transcript speaker labels onto roster names.

    Exact alias match first (the engine's own normalisation, so a GitHub handle
    resolves), then a first-token match accepted ONLY when it is unique in the
    roster. Ambiguity yields no mapping: a mis-attributed claim would file an
    issue against the wrong member's evidence, which is worse than not
    attributing it at all.
    """
    from yeaboi.standup.engine import _normalize_author

    resolved: dict[str, str] = {}
    first_tokens: dict[str, list[str]] = defaultdict(list)
    for member in alias_map:
        token = member.strip().split()[0].lower() if member.strip() else ""
        if token:
            first_tokens[token].append(member)

    for speaker in speakers:
        keys = _normalize_author(speaker)
        match = next((m for m, aliases in alias_map.items() if keys & aliases), "")
        if not match:
            token = speaker.strip().split()[0].lower() if speaker.strip() else ""
            candidates = first_tokens.get(token, [])
            if len(candidates) == 1:
                match = candidates[0]
            elif len(candidates) > 1:
                logger.info("transcript review: speaker %r is ambiguous (%d matches)", speaker, len(candidates))
        if match:
            resolved[speaker] = match
    return resolved


# ---------------------------------------------------------------------------
# 2. LLM extraction
# ---------------------------------------------------------------------------


def _parse_review_response(raw: str) -> dict:
    """Extract the claims JSON from an LLM response, tolerating markdown fences."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    try:
        parsed = json.loads(raw.strip())
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("transcript review: could not parse LLM JSON response")
        return {}


def _member_payload(report: StandupReport) -> list[dict]:
    """One entry per member: their summaries plus a compact evidence index.

    URLs are stripped — the model needs to recognise items, not link them, and
    every character spent on a URL is a character not spent on the transcript.
    """
    payload: list[dict] = []
    roster_size = max(1, len(report.member_updates))
    per_member = max(_MIN_EVIDENCE_PER_MEMBER, min(_EVIDENCE_PER_MEMBER, _MAX_TOTAL_EVIDENCE // roster_size))
    for member in report.member_updates:
        evidence = []
        for item in (
            *member.ticketing_evidence,
            *member.code_evidence,
            *member.documentation_evidence,
        )[:per_member]:
            evidence.append(
                {
                    "kind": item.kind,
                    "key": item.key,
                    "title": item.title[:_TITLE_CLIP],
                    "repository": item.repository,
                    "status": item.status,
                }
            )
        payload.append(
            {
                "name": member.name,
                "summary": member.summary,
                "ticketing_summary": member.ticketing_summary,
                "code_summary": member.code_summary,
                "documentation_summary": member.documentation_summary,
                "evidence": evidence,
            }
        )
    return payload


# ---------------------------------------------------------------------------
# 3. Clamps
# ---------------------------------------------------------------------------


def _evidence_keys(report: StandupReport, member_name: str) -> set[str]:
    member = next((m for m in report.member_updates if m.name == member_name), None)
    if member is None:
        return set()
    return {
        item.key.lower()
        for item in (*member.ticketing_evidence, *member.code_evidence, *member.documentation_evidence)
        if item.key
    }


def clamp_claims(
    raw_claims: list,
    *,
    report: StandupReport,
    transcript_text: str,
    source_path: str = "",
    speaker_map: dict[str, str] | None = None,
    attribution: str = "labelled",
) -> tuple[tuple[TranscriptClaim, ...], list[str]]:
    """Keep only claims that survive verification. Returns (claims, notes).

    In order: quote must be literally present; member must be on the roster; a
    supplied key is re-checked against the evidence and overrides the model's
    own verdict; 'unclear' is dropped; per-member cap applied.
    """
    haystack = _normalise(transcript_text)
    roster = {m.name for m in report.member_updates}
    speaker_map = speaker_map or {}
    notes: list[str] = []
    kept: list[TranscriptClaim] = []
    per_member: dict[str, int] = defaultdict(int)
    dropped_quote = dropped_member = dropped_unclear = 0

    for entry in raw_claims:
        if not isinstance(entry, dict):
            continue
        quote = str(entry.get("quote", "")).strip()
        # Quote grounding — the single strongest guard against an invented gap
        # reaching a public issue tracker.
        if not quote or _normalise(quote) not in haystack:
            dropped_quote += 1
            continue

        member = str(entry.get("member", "")).strip()
        member = speaker_map.get(member, member)
        if member and member not in roster:
            dropped_member += 1
            continue
        # An unlabelled transcript cannot support member-scoped conclusions.
        if attribution == "unlabelled" and member and member not in roster:
            member = ""

        status = str(entry.get("status", "")).strip().lower()
        if status not in _VALID_STATUS:
            status = "unclear"

        matched_key = str(entry.get("matched_key", "")).strip()
        if matched_key and member:
            # Re-derive the verdict from the evidence. The model's matched/missing
            # answer only survives where it offered no key to check.
            present = matched_key.lower() in _evidence_keys(report, member)
            if status in ("matched", "missing"):
                status = "matched" if present else "missing"

        if status == "unclear":
            dropped_unclear += 1
            continue

        if member:
            if per_member[member] >= _MAX_CLAIMS_PER_MEMBER:
                continue
            per_member[member] += 1

        kept.append(
            TranscriptClaim(
                member=member,
                claim=str(entry.get("claim", "")).strip(),
                quote=quote[:240],
                status=status,
                matched_key=matched_key,
                system_hint=str(entry.get("system_hint", "")).strip(),
                artifact_hint=str(entry.get("artifact_hint", "")).strip(),
                source_path=source_path,
            )
        )

    if dropped_quote:
        logger.warning("transcript review: dropped %d claim(s) whose quote was not in the transcript", dropped_quote)
        notes.append(f"{dropped_quote} extracted claim(s) could not be verified against the transcript.")
    if dropped_member:
        logger.warning("transcript review: dropped %d claim(s) naming an unknown member", dropped_member)
    if dropped_unclear:
        logger.info("transcript review: dropped %d unclear claim(s)", dropped_unclear)
    return tuple(kept), notes


# ---------------------------------------------------------------------------
# 4. Classification
# ---------------------------------------------------------------------------


def diagnose(
    claims: tuple[TranscriptClaim, ...],
    *,
    report: StandupReport,
    config: dict | None = None,
) -> tuple[tuple[StandupGap, ...], tuple[StandupGap, ...], int, int]:
    """Group claims by root cause.

    Returns ``(product_gaps, config_suggestions, untracked, unclassified)``.
    ``unclassified`` is reported rather than discarded: a claim the ladder could
    not diagnose is a limit of the diagnosis, and saying so is more honest than
    a review that quietly implies everything was accounted for.
    """
    grouped: dict[str, tuple[gap_taxonomy.Diagnosis, list[TranscriptClaim]]] = {}
    untracked = 0
    unclassified = 0

    for claim in claims:
        if claim.status == "matched":
            continue
        if claim.status == "contradicted":
            diagnosis = gap_taxonomy.classify_contradiction(claim, report=report)
        else:
            diagnosis = gap_taxonomy.classify(claim, report=report, config=config)
        if diagnosis is None:
            unclassified += 1
            continue
        if diagnosis.category.scope == gap_taxonomy.SCOPE_NONE:
            untracked += 1
            continue
        key = gap_taxonomy.fingerprint(diagnosis.category.id, diagnosis.systems, diagnosis.kind, diagnosis.scope_token)
        if key in grouped:
            grouped[key][1].append(claim)
        else:
            grouped[key] = (diagnosis, [claim])

    gaps = [gap_taxonomy.build_gap(d, tuple(cs)) for d, cs in grouped.values()]
    # Most severe first, so a truncated list still leads with what matters.
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    gaps.sort(key=lambda g: (order.get(g.priority, 9), g.title))

    product = tuple(g for g in gaps if g.scope == gap_taxonomy.SCOPE_PRODUCT)[:_MAX_GAPS_PER_REVIEW]
    suggestions = tuple(g for g in gaps if g.scope == gap_taxonomy.SCOPE_CONFIG)[:_MAX_GAPS_PER_REVIEW]
    return product, suggestions, untracked, unclassified


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def _accuracy_note(matched: int, missing: int, contradicted: int, untracked: int, unclassified: int = 0) -> str:
    if not (matched or missing or contradicted or untracked or unclassified):
        return "No concrete work claims were found in the transcript."
    parts = [f"{matched} confirmed by the evidence"]
    if missing:
        parts.append(f"{missing} the report did not have")
    if contradicted:
        parts.append(f"{contradicted} the team contradicted")
    if untracked:
        parts.append(f"{untracked} with no digital footprint")
    if unclassified:
        parts.append(f"{unclassified} the review could not attribute to a cause")
    return "Claims checked: " + ", ".join(parts) + "."


def review_transcripts(
    session_id: str,
    *,
    report: StandupReport | None,
    sources: list[tuple[TranscriptSource, tuple]],
    config: dict | None = None,
    standup_date: str = "",
    run_id: int = 0,
    extra_warnings: list[str] | None = None,
    now: str = "",
) -> TranscriptReview:
    """Review one date's transcripts against the report for that date.

    Never raises: an unavailable LLM, a bad response, or an auth failure all
    degrade to a review that says what it could not do.
    """
    warnings = list(extra_warnings or [])
    reviewed_at = now or datetime.now(UTC).isoformat()
    source_tuple = tuple(s for s, _turns in sources)
    for source in source_tuple:
        if source.truncated:
            warnings.append(f"{source.filename} was longer than the read limit — only the start was reviewed.")

    def _empty(llm_mode: str) -> TranscriptReview:
        return TranscriptReview(
            session_id=session_id,
            standup_date=standup_date,
            run_id=run_id,
            reviewed_at=reviewed_at,
            sources=source_tuple,
            accuracy_note=_accuracy_note(0, 0, 0, 0),
            llm_mode=llm_mode,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    if report is None:
        warnings.append(
            f"No standup run found for {standup_date or 'that date'} — the transcript could not be "
            "checked against a report."
        )
        return _empty("deterministic")

    all_turns = tuple(turn for _s, turns in sources for turn in turns)
    if not all_turns:
        warnings.append("The transcript had no readable speech.")
        return _empty("deterministic")

    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("transcript review: LLM not configured (%s)", why)
        warnings.append(f"Transcript review needs AI to read the meeting — {why}.")
        return _empty("deterministic")

    from yeaboi.agent.llm import invoke_json
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error, _local_llm_hint
    from yeaboi.prompts.standup_review import get_transcript_review_prompt

    attribution = "labelled" if any(s.attribution == "labelled" for s in source_tuple) else "unlabelled"
    transcript_text = transcripts.to_prompt_text(all_turns, limit=_TRANSCRIPT_PROMPT_CHARS)

    alias_map = _alias_map_for(report, config)
    speaker_map = resolve_speakers(tuple(dict.fromkeys(sp for s in source_tuple for sp in s.speakers)), alias_map)

    prompt = get_transcript_review_prompt(
        standup_date=standup_date,
        transcript=transcript_text,
        members=_member_payload(report),
        attribution=attribution,
        report_summary=report.team_summary,
    )

    try:
        logger.info(
            "transcript review: invoking LLM (date=%s, %d turn(s), %d member(s))",
            standup_date,
            len(all_turns),
            len(report.member_updates),
        )
        response = invoke_json(prompt)
        parsed = _parse_review_response(response.content)
    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("transcript review: LLM auth/billing error: %s", exc)
            warnings.append("Transcript review unavailable — API key invalid or billing issue.")
        elif _local_llm_hint(exc):
            logger.warning("transcript review: local Ollama failure: %s", exc)
            warnings.append(f"Transcript review unavailable — {_local_llm_hint(exc)}")
        else:
            logger.warning("transcript review: LLM request failed: %s", exc)
            warnings.append("Transcript review unavailable — the AI request failed (see logs).")
        return _empty("deterministic")

    raw_claims = parsed.get("claims") if isinstance(parsed.get("claims"), list) else []
    claims, notes = clamp_claims(
        raw_claims,
        report=report,
        transcript_text=transcript_text,
        source_path=source_tuple[0].path if source_tuple else "",
        speaker_map=speaker_map,
        attribution=attribution,
    )
    warnings.extend(notes)

    gaps, suggestions, untracked, unclassified = diagnose(claims, report=report, config=config)

    matched = sum(1 for c in claims if c.status == "matched")
    missing = sum(1 for c in claims if c.status == "missing")
    contradicted = sum(1 for c in claims if c.status == "contradicted")

    logger.info(
        "transcript review: date=%s claims=%d (matched=%d missing=%d contradicted=%d) gaps=%d suggestions=%d",
        standup_date,
        len(claims),
        matched,
        missing,
        contradicted,
        len(gaps),
        len(suggestions),
    )

    return TranscriptReview(
        session_id=session_id,
        standup_date=standup_date,
        run_id=run_id,
        reviewed_at=reviewed_at,
        sources=source_tuple,
        claims=claims,
        gaps=gaps,
        config_suggestions=suggestions,
        accuracy_note=_accuracy_note(matched, missing, contradicted, untracked, unclassified),
        claims_matched=matched,
        claims_missing=missing,
        claims_contradicted=contradicted,
        untracked_count=untracked,
        llm_mode="llm",
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _alias_map_for(report: StandupReport, config: dict | None) -> dict[str, set[str]]:
    """Rebuild the alias map the report was written with."""
    from yeaboi.standup.engine import _build_alias_map

    config = config or {}
    return _build_alias_map(
        [m.name for m in report.member_updates],
        my_name=report.my_name,
        my_aliases=config.get("my_aliases", ""),
        repo_path=config.get("repo_path", ""),
    )


def sweep_and_review(
    session_id: str,
    *,
    config: dict | None = None,
    before_date: str = "",
    db_path: Path | None = None,
    today: date | None = None,
    transcript_paths: list[str] | None = None,
    standup_date: str = "",
    max_dates: int = 3,
    include_reviewed: bool = False,
) -> list[TranscriptReview]:
    """Find, review and persist transcripts. Returns one review per covered date.

    Grouping by date is what bounds the cost: one LLM call per standup being
    audited, not one per file. Never raises — a failure anywhere degrades to
    fewer reviews plus warnings, because this runs on the standup critical path.
    """
    from yeaboi.paths import get_db_path
    from yeaboi.standup.store import StandupStore

    resolved_db = db_path or get_db_path()
    warnings: list[str] = []

    if transcript_paths:
        found = [(Path(p), True) for p in transcript_paths]
    else:
        found, warnings = transcripts.discover(
            session_id,
            config=config,
            before_date=before_date,
            db_path=resolved_db,
            today=today,
            include_reviewed=include_reviewed,
        )

    if not found and not warnings:
        return []

    # Read everything first, then group by the date each transcript covers.
    by_date: dict[str, list[tuple[TranscriptSource, tuple]]] = defaultdict(list)
    read_failures: list[str] = []
    for path, external in found:
        try:
            source, turns = transcripts.read_transcript(path, external=external, today=today)
        except Exception as exc:
            logger.warning("transcript review: cannot read %s: %s", path, exc)
            read_failures.append(f"Could not read {path.name}: {exc}")
            continue
        key = source.covered_date or standup_date or (today or date.today()).isoformat()
        by_date[key].append((source, turns))

    warnings.extend(read_failures)
    if not by_date:
        if warnings:
            logger.info("transcript review: nothing reviewable (%d warning(s))", len(warnings))
        return []

    dates = sorted(by_date)
    if len(dates) > max_dates:
        deferred = len(dates) - max_dates
        logger.warning("transcript review: deferring %d date(s) beyond the per-sweep cap", deferred)
        warnings.append(f"{deferred} older transcript date(s) will be reviewed on the next run.")
        dates = dates[:max_dates]

    reviews: list[TranscriptReview] = []
    with StandupStore(resolved_db) as store:
        for index, covered in enumerate(dates):
            group = by_date[covered]
            run_id = store.get_run_row_by_date(session_id, covered)
            report = store.get_run_by_id(run_id) if run_id else None
            try:
                review = review_transcripts(
                    session_id,
                    report=report,
                    sources=group,
                    config=config,
                    standup_date=covered,
                    run_id=run_id,
                    # Sweep-level warnings belong to the first review only, so
                    # they are stated once rather than repeated per date.
                    extra_warnings=warnings if index == 0 else None,
                )
            except Exception as exc:  # a broken review must not break the standup
                logger.warning("transcript review: review failed for %s: %s", covered, exc)
                continue

            review_id = store.record_review(review)
            for source, _turns in group:
                try:
                    store.mark_transcript_reviewed(
                        session_id,
                        path=source.path,
                        content_hash=transcripts.content_hash(Path(source.path)),
                        covered_date=covered,
                        review_id=review_id,
                    )
                except OSError as exc:
                    logger.warning("transcript review: cannot record %s as reviewed: %s", source.path, exc)
            for gap in review.gaps:
                store.upsert_gap_issue(gap.fingerprint, category=gap.category, title=gap.title, review_id=review_id)
            from dataclasses import replace

            reviews.append(replace(review, review_id=review_id))

    logger.info("transcript review: completed %d review(s) for session=%s", len(reviews), session_id)
    return reviews


def carry_forward(
    reviews: list[TranscriptReview], previous_report: StandupReport | None
) -> tuple[dict[str, list[str]], list[str]]:
    """Turn reviews into (per-member corrections, report warnings).

    Corrections are fed FORWARD into today's report rather than written back
    into yesterday's: ``standup_history`` is an append-only record of what was
    said at the time, and rewriting it would falsify the record to make today
    look tidy.
    """
    corrections: dict[str, list[str]] = defaultdict(list)
    warnings: list[str] = []
    if not reviews:
        return {}, []

    previous_date = previous_report.date if previous_report else ""
    for review in reviews:
        if previous_date and review.standup_date == previous_date:
            for claim in review.claims:
                if claim.status == "missing" and claim.member and claim.claim:
                    corrections[claim.member].append(claim.claim)
        for gap in review.gaps:
            warnings.append(f"Transcript review ({review.standup_date}): {gap.title} — issue drafted.")
        for suggestion in review.config_suggestions:
            warnings.append(f"Transcript review ({review.standup_date}): {suggestion.title}. {suggestion.remedy}")
        warnings.extend(review.warnings)

    # Cap per member, matching insights._MAX_SIGNALS_PER_MEMBER's restraint.
    capped = {name: items[:3] for name, items in corrections.items()}
    return capped, list(dict.fromkeys(warnings))
