"""Pre-intake chat exchange — greeting and project-size resolution.

# See docs: "Prompt Construction" — the flipped prompt (the classifier
# returns JSON; this module owns the decision)
# See docs: "Scrum Standards" — Small vs Large plans

The live chat opens BEFORE the first graph invocation: the agent greets, the
user describes the project, and the size (Small → "small_project", Large →
"smart") is settled conversationally instead of via the old intake cards.
Everything here is deliberately outside the graph:

- ``project_intake`` reads ``state["messages"][0]`` as the project
  description, so the greeting/size exchange must never enter ``messages`` —
  the chat driver keeps it in the ``_chat_preamble`` state field and makes
  the first graph invoke with the description as the one HumanMessage,
  byte-identical to what the old card flow produced. Planning results are
  therefore unaffected by the new front door.
- Size resolution is deterministic-first: an explicit reply ("1", "small",
  "large"...) is parsed locally; only a real description triggers one JSON
  classification call, and "unclear"/failure falls back to asking the
  numbered question — which always works, LLM or no LLM.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

GREETING_TEXT = (
    "Hey — I'm your Scrum planning agent. Tell me about your project: "
    "what are you building, and why?\n\n"
    "I'll figure out whether this needs a Small plan (a ticket or two) or a "
    "Large one (epics and sprints) as we go — or force it any time with "
    "/small or /large. Prefer a questionnaire? /form opens one — and /finish "
    "fills in defaults and builds the whole plan in one go. Type /help to see "
    "every command."
)

SIZE_QUESTION_TEXT = (
    "Quick check before we dig in — how big is this?\n\n"
    "[1] Small — a ticket or two, one quick sprint\n"
    "[2] Large — a fuller project with epics and multiple sprints"
)

# Bare size replies, matched against the whole (lowercased, stripped) input.
# Kept deliberately exact-match — "we're a small team building a huge app"
# must NOT parse as a size answer; longer texts go to the classifier instead.
_SMALL_REPLIES = frozenset({"1", "small", "tiny", "a ticket", "ticket", "one ticket", "quick", "minor", "small one"})
_LARGE_REPLIES = frozenset({"2", "large", "big", "epic", "huge", "full", "major", "large one", "big one"})

# A reply longer than this many words is treated as a project description
# (worth a classification call) rather than a failed size answer.
_DESCRIPTION_MIN_WORDS = 8


def parse_size_reply(text: str) -> str | None:
    """Parse an explicit size answer. Returns an intake mode or None.

    Deterministic and total: "1"/"small"/... → "small_project",
    "2"/"large"/... → "smart", anything else → None.
    """
    lowered = text.strip().lower().rstrip(".!")
    if lowered in _SMALL_REPLIES:
        return "small_project"
    if lowered in _LARGE_REPLIES:
        return "smart"
    return None


def classify_size_from_description(description: str) -> str | None:
    """Classify a project description as an intake mode via one JSON LLM call.

    Returns "small_project" | "smart" | None (unclear, bad JSON, or provider
    failure — the caller falls back to the deterministic size question).
    Auth/billing and actionable local-Ollama errors re-raise, matching the
    _should_reraise_llm_error convention: silently degrading THOSE would hide
    a broken setup behind an extra question forever.
    """
    import json

    from yeaboi.agent.llm import invoke_json, strip_json_fences
    from yeaboi.agent.nodes import _should_reraise_llm_error
    from yeaboi.prompts.size_classifier import get_size_classifier_prompt

    try:
        response = invoke_json(get_size_classifier_prompt(description))
        payload = json.loads(strip_json_fences(response.content))
        size = str(payload.get("size", "")).strip().lower()
    except Exception as exc:
        if _should_reraise_llm_error(exc):
            raise
        logger.warning("Size classification failed, falling back to the size question: %s", exc)
        return None

    mode = {"small": "small_project", "large": "smart"}.get(size)
    logger.info("Size classified: %s -> %s", size or "<empty>", mode or "ask")
    return mode


def resolve_intake_mode(user_text: str) -> tuple[str | None, str]:
    """Resolve the user's first substantive chat message into (mode, description).

    - An explicit bare size answer → (mode, "") — no description yet.
    - A real description (>= _DESCRIPTION_MIN_WORDS words) → classify it;
      (mode-or-None, the description). None means "announce nothing, ask".
    - Anything else (a short description, a greeting back) → (None, text);
      the caller keeps the text as the description and asks the size question.
    """
    text = user_text.strip()
    direct = parse_size_reply(text)
    if direct is not None:
        return direct, ""
    if len(text.split()) >= _DESCRIPTION_MIN_WORDS:
        return classify_size_from_description(text), text
    return None, text
