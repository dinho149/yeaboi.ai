"""Poker engine — the one LLM call that gives an AI perspective on a vote spread.

Like the retro engine, this is a standalone helper (NOT a LangGraph node): it
calls the LLM directly via ``invoke_json`` and follows the same
**parse → fallback → format** convention the graph nodes use (agent/nodes.py).
After the admin reveals the votes on a ticket, they can ask for an AI take —
the model sees the ticket, the revealed spread, and the team's recorded history
from the other yeaboi modes (poker/context.py: calibration data, delivered
tickets, standup blockers, retro themes, planned-story sizing), comments on the
disagreement, and suggests a deck estimate — citing the data points it used.

An LLM auth/billing error is NOT re-raised — it is folded into the returned
dict and the deterministic fallback (median + spread note) is used, so a live
poker session never crashes over a missing key (same policy as retro/engine.py).

# See docs: "The ReAct Loop" — using the LLM outside the main graph
# See docs: "Prompt Construction" — the poker perspective prompt
"""

from __future__ import annotations

import json
import logging

from yeaboi.paths import get_poker_log_dir
from yeaboi.poker.board import POKER_DECK, median_of, snap_to_deck

logger = logging.getLogger(__name__)


def _numeric_votes(votes: dict[str, str]) -> list[float]:
    """The votes that carry a size opinion ("?" and "☕" are excluded)."""
    out: list[float] = []
    for value in votes.values():
        if value in ("?", "☕"):
            continue
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            continue
    return out


def _fallback_suggestion(votes: dict[str, str]) -> float | None:
    """Deterministic suggestion: deck-snapped median of the numeric votes."""
    med = median_of(_numeric_votes(votes))
    return snap_to_deck(med) if med is not None else None


def _build_fallback_note(votes: dict[str, str], context=None) -> str:
    """Deterministic perspective when the LLM is unavailable.

    Pure function over the vote spread — median, range, and a nudge about what
    the disagreement (or the "?" votes) means. Never empty when there are votes.
    When cross-mode history is available (poker/context.py), up to two
    deterministic evidence lines are appended: what stories of the suggested
    size actually cost this team, and any current blocker on the assignee.
    """
    if not votes:
        return "No votes to reason about yet."
    numeric = _numeric_votes(votes)
    unsure = sum(1 for v in votes.values() if v == "?")
    parts: list[str] = []
    if numeric:
        low, high = min(numeric), max(numeric)
        suggestion = _fallback_suggestion(votes)

        def _fmt(x: float) -> str:
            return str(int(x)) if x == int(x) else str(x)

        if low == high:
            parts.append(f"The team agrees on {_fmt(low)} points.")
        else:
            parts.append(
                f"Votes span {_fmt(low)}–{_fmt(high)}; the median lands on {_fmt(suggestion)}. "
                "The high voters may see complexity the low voters don't — "
                f"talk through what a {_fmt(high)} would involve before settling."
            )
    if unsure:
        parts.append(f"{unsure} voter(s) played '?' — the ticket may need clarifying before it can be sized.")
    if not parts:
        parts.append("Only non-numeric votes were played — clarify the ticket and re-vote.")
    if context is not None and not context.is_empty:
        suggestion = _fallback_suggestion(votes)
        if suggestion is not None:
            for value, line in context.calibration_by_value:
                if value == suggestion:
                    parts.append(f"Team history: {line}")
                    break
        if context.assignee_lines:
            parts.append(context.assignee_lines[0])
    return " ".join(parts)


_CONFIDENCE_LEVELS = ("high", "medium", "low")
_MAX_EVIDENCE = 3
_MAX_EVIDENCE_LEN = 140


def _parse_perspective(raw: str) -> tuple[str, float | None, str, tuple[str, ...]]:
    """Extract (comment, suggested_points, confidence, evidence) from an LLM response.

    Tolerates markdown fences. An out-of-deck suggestion is snapped to the
    nearest numeric card; an unknown confidence collapses to ""; evidence is
    sanitized (strings only, trimmed, capped). Anything unusable becomes
    ("", None, "", ()) so the caller falls back. Older 2-field responses
    (no confidence/evidence) still parse — the extras just come back empty.
    """
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    try:
        parsed = json.loads(raw.strip())
    except (json.JSONDecodeError, TypeError):
        logger.warning("poker: could not parse LLM JSON response")
        return "", None, "", ()
    if not isinstance(parsed, dict):
        return "", None, "", ()
    comment = str(parsed.get("comment") or "").strip()
    suggested = parsed.get("suggested_points")
    if suggested is not None:
        try:
            suggested = snap_to_deck(float(suggested))
        except (TypeError, ValueError):
            suggested = None
    confidence = str(parsed.get("confidence") or "").strip().lower()
    if confidence not in _CONFIDENCE_LEVELS:
        confidence = ""
    raw_evidence = parsed.get("evidence")
    evidence = tuple(
        str(e).strip()[:_MAX_EVIDENCE_LEN]
        for e in (raw_evidence if isinstance(raw_evidence, list) else [])
        if isinstance(e, str) and str(e).strip()
    )[:_MAX_EVIDENCE]
    return comment, suggested, confidence, evidence


def get_poker_perspective(
    ticket: dict,
    votes: dict[str, str],
    *,
    project_name: str = "",
    context=None,
    debate_transcript: str = "",
    scope=None,
) -> dict:
    """One LLM call commenting on a revealed vote spread. Never raises.

    Args:
        ticket: a normalized poker ticket row ({summary, description_text, story_points, …}).
        votes: revealed votes as {voter name: deck value}.
        project_name: the session's project name — scopes the retro/standup history.
        context: a pre-gathered ``PokerEstimationContext`` (tests / callers that
            already have one); None means gather it here from the other modes'
            stores. Gathering is local SQLite reads — cheap next to the LLM call.
        debate_transcript: the transcribed low-vs-high duel debate ("" when no
            duel ran). Passed through to the prompt, where the model is asked
            to judge which argument was stronger.
        scope: an optional ``ProjectScope`` applying the run's context toggles
            to the gather; ignored when ``context`` is injected.

    Returns ``{"note": str, "suggested_points": float | None, "confidence": str,
    "evidence": [str, ...], "llm_mode": "llm" | "fallback", "warnings": [str, ...]}``
    — the mode/warnings shape every yeaboi engine reports so adapters can surface
    fallback runs. ``confidence``/``evidence`` say how strongly the team's recorded
    history backs the suggestion and which data points were used ("" / [] when the
    LLM was unavailable).
    """
    summary = (ticket or {}).get("summary", "")
    # Transcript content is participant speech — log its size only.
    logger.info(
        "poker: generating AI perspective — ticket=%s voters=%d debate_chars=%d",
        (ticket or {}).get("key", ""),
        len(votes),
        len(debate_transcript),
    )

    # Cross-mode history (analysis/standup/reporting/retro/planning) — gathered
    # before the LLM-configured check so the deterministic fallback benefits too.
    if context is None:
        from yeaboi.poker.context import gather_poker_context

        context = gather_poker_context(ticket or {}, project_name=project_name, scope=scope)

    def _fallback(warning: str) -> dict:
        note = _build_fallback_note(votes, context)
        if debate_transcript:
            # Deterministic and content-free — the transcript itself is already
            # visible on the board; only the AI's judgment of it is missing.
            note += (
                f" A {len(debate_transcript)}-char duel transcript was recorded — "
                "read it on the board; AI judgment of the debate is unavailable."
            )
        return {
            "note": note,
            "suggested_points": _fallback_suggestion(votes),
            "confidence": "",
            "evidence": [],
            "llm_mode": "fallback",
            "warnings": [warning] if warning else [],
        }

    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("poker: LLM not configured (%s) — using deterministic fallback", why)
        return _fallback(f"AI unavailable ({why}) — showing the vote median instead.")

    # invoke_json tracks usage + turns on JSON mode + re-asks once on bad JSON.
    # See docs: "Local Mode (Ollama)" — reliability layer.
    from yeaboi.agent.llm import invoke_json
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error, _is_llm_rate_limited, _local_llm_hint
    from yeaboi.prompts.poker import get_poker_perspective_prompt

    prompt = get_poker_perspective_prompt(
        summary=summary,
        description=(ticket or {}).get("description_text", "") or (ticket or {}).get("description", ""),
        current_points=(ticket or {}).get("story_points"),
        votes=votes,
        deck=POKER_DECK,
        context_md=context.summary_md,
        debate_transcript=debate_transcript,
        acceptance=(ticket or {}).get("acceptance_text", "") or "",
    )
    try:
        response = invoke_json(prompt, temperature=0.3)
        note, suggested, confidence, evidence = _parse_perspective(response.content)
    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("poker: LLM auth/billing error — surfacing as warning: %s", exc)
            return _fallback("AI unavailable (API key/billing) — showing the vote median instead.")
        if _is_llm_rate_limited(exc):
            logger.warning("poker: LLM rate limited — surfacing as warning: %s", exc)
            return _fallback("AI is rate limited right now — try again shortly, showing the vote median instead.")
        local_hint = _local_llm_hint(exc)
        if local_hint:
            logger.warning("poker: local Ollama failure: %s", exc)
            return _fallback(local_hint)
        logger.warning("poker: LLM request failed, using fallback: %s", exc)
        # Named, not "see logs": the log lives in a directory nobody would guess.
        log_path = get_poker_log_dir() / "poker.log"
        return _fallback(f"AI request failed ({log_path}) — showing the vote median instead.")

    if not note:
        return _fallback("AI returned nothing usable — showing the vote median instead.")

    logger.info("poker: AI perspective generated — suggested=%s confidence=%s", suggested, confidence or "n/a")
    return {
        "note": note,
        "suggested_points": suggested,
        "confidence": confidence,
        "evidence": list(evidence),
        "llm_mode": "llm",
        "warnings": [],
    }
