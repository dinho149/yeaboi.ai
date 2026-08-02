"""The language-model half of practice relatedness — and only ever a mute button.

``relatedness.py`` decides, deterministically, whether a change reads like a
ticket the team has open. It cannot bridge a wording gap: a ticket saying
"customers cannot check out" and a commit saying "fix the cart total rounding"
share no vocabulary, and no threshold reaches that. This module closes that gap
by handing the still-unplaced changes to a fast model — once per standup, for
the whole team — and letting it drop further ones.

**Suppress-only, structurally.** ``habits.Adjudicator`` returns a collection of
ids to remove. There is no shape in which a verdict could add a change, sharpen
a message, or name a ticket in text anyone reads, so a hallucinating model costs
a missed nudge and nothing else. That is why an LLM is admissible in a feature
whose signals are otherwise deterministic: it authors nothing.

Every failure path returns "drop nothing", which keeps the deterministic
verdicts exactly as they were.

# See docs: "Daily Standup" — practices
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from yeaboi.standup.habits import AdjudicationCase

logger = logging.getLogger(__name__)

# The residue is small by construction — only changes the deterministic pass
# could not place — so a single call covers a whole team. The cap is a backstop
# against a pathological day, and truncating can only leave reports standing.
_MAX_CASES = 40
_REQUEST_TIMEOUT_SECONDS = 60


def _case_payload(case: AdjudicationCase) -> dict:
    return {
        "id": case.case_id,
        "subject": case.subject,
        "branch": case.branch,
        "paths": list(case.paths),
        "candidates": [{"key": key, "title": title, "text": text} for key, title, text in case.candidates],
    }


def adjudicate(cases: Sequence[AdjudicationCase], corrections: Sequence[Mapping] = ()) -> frozenset[str]:
    """Ask the model which of these changes belong to one of their candidate tickets.

    ``corrections`` is the team's own thumbs up/down on earlier reports of this
    kind (``practice_feedback.Ledger.corrections``), passed as few-shot
    calibration. It cannot change the return shape, so it can only move where
    this call draws the line — never what it is able to say.

    Returns the ids to drop. Never raises: this runs inside the standup
    pipeline, and a practice nicety must not be able to fail a report.
    """
    from yeaboi.agent.llm import get_analysis_fast_model, invoke_json
    from yeaboi.prompts.standup_practices import get_practice_adjudication_prompt

    batch = list(cases)[:_MAX_CASES]
    if len(cases) > _MAX_CASES:
        logger.info("standup: adjudicating the first %d of %d change(s)", _MAX_CASES, len(cases))
    if not batch:
        return frozenset()

    valid = {case.case_id for case in batch}
    if corrections:
        logger.info("standup: adjudicating with %d recorded team correction(s)", len(corrections))
    try:
        data = invoke_json(
            get_practice_adjudication_prompt([_case_payload(case) for case in batch], [dict(c) for c in corrections]),
            temperature=0.0,
            max_reasks=0,
            model=get_analysis_fast_model(),
            request_timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.warning("standup: practice adjudication call failed — every report stands", exc_info=True)
        return frozenset()

    raw = (data or {}).get("belongs") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        logger.warning("standup: practice adjudication returned an unusable shape — every report stands")
        return frozenset()
    # Ids we did not send are discarded rather than trusted: the model's only
    # legitimate move is picking from the batch it was given.
    dropped = frozenset(str(item) for item in raw if str(item) in valid)
    logger.info("standup: adjudication excused %d of %d change(s)", len(dropped), len(batch))
    return dropped


def build_adjudicator(config, corrections: Sequence[Mapping] = ()) -> object | None:
    """The adjudicator seam for ``habits.detect_practices``, or None to skip it.

    None whenever the team switched it off or no LLM is configured — in which
    case detection stays exactly as deterministic as it was.

    ``corrections`` is closed over rather than added to ``Adjudicator``'s
    signature: ``habits`` owns the seam's contract, and it has no business
    knowing that one implementation of it learns from feedback.
    """
    from yeaboi.config import is_llm_configured

    if str((config or {}).get("habit_ai_match", "on") or "on").strip().lower() == "off":
        return None
    configured, why = is_llm_configured()
    if not configured:
        logger.info("standup: practice adjudication skipped — %s", why)
        return None
    if not corrections:
        return adjudicate
    return lambda cases: adjudicate(cases, corrections)
