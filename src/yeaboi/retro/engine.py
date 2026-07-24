"""Retro engine — the one LLM call that turns retro cards into action items.

Like the standup engine, this is a standalone helper (NOT a LangGraph node): it
calls ``get_llm()`` directly and follows the same **parse → fallback** convention
the graph nodes use (agent/nodes.py). The team fills the "What didn't go well"
grid; this reads those cards (plus "What went well" for context) and appends
AI-suggested action items to the board's "Action items" grid.

An LLM auth/billing error is NOT re-raised — it is turned into a user-facing
status message and the deterministic fallback is used, so the retro never
crashes over a missing key (same policy as standup/engine.py).

# See docs: "The ReAct Loop" — using the LLM outside the main graph
# See docs: "Prompt Construction" — the retro action-items prompt
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from yeaboi.agent.state import RetroCard
from yeaboi.retro.board import CARRIED_OPEN_STATUSES, RetroBoard

logger = logging.getLogger(__name__)


def carried_action_items_for_session(
    session_id: str, *, project_name: str = "", db_path: Path | None = None
) -> tuple[RetroCard, ...]:
    """Return the previous retro's action items for review, reset to ``pending``.

    The headless carry-forward entrypoint (the TUI + browser are adapters over it):
    finds "the retro before this one" and returns its ``action_items`` cards with
    ``status="pending"`` and ``origin="carryover"`` so the new board can seed its
    "Last sprint's actions" review column.

    "Previous retro" is resolved **across sessions**, not just this ``session_id``:
    retros run under auto-created quick sessions, so each one typically lands on a
    different session and a same-session lookup would almost always come up empty.
    We reuse ``RetroStore.get_recent_reports(limit, project_name)`` (the same
    cross-session, project-first primitive ``ceremony_history`` uses) and take the most
    recent recorded report. This intentionally carries forward across a reopen of the
    *same* session (close a retro, open it again → last run's actions appear) — at
    board-open the current run isn't recorded yet, so the newest report is always a
    genuinely prior retro. ``project_name`` biases toward the same project's retros;
    ``session_id`` is used only for logging.

    Graceful — returns an empty tuple when there's no prior retro or on any read error
    (never raises).

    # See CLAUDE.md — Retro action-item carry-forward loop (mirrors Performance 1:1s)
    """
    from dataclasses import replace

    try:
        from yeaboi.paths import get_db_path
        from yeaboi.retro.store import RetroStore

        path = db_path or get_db_path()
        with RetroStore(path) as store:
            # Project-first, newest-first across ALL sessions (see docstring).
            reports = store.get_recent_reports(limit=5, project_name=project_name)
    except Exception as exc:  # pragma: no cover - defensive; carry-forward is best-effort
        logger.warning("retro: could not load carried action items (session=%s): %s", session_id, exc)
        return ()

    # "The retro before this one" = the most recent recorded report (project-first via
    # get_recent_reports). At board-open the current run is NOT recorded yet, so the
    # newest report is always a genuinely prior retro — INCLUDING a reopen of the same
    # session, which is the common case: close a retro, open it again, and last run's
    # actions should carry forward. We deliberately do NOT skip same-session reports;
    # that guard used to eat the only prior report whenever retros reused the latest
    # quick session (each retro auto-creates a session that stays "latest"), so nothing
    # ever carried. ``session_id`` is kept for logging/telemetry only.
    prior_report = reports[0] if reports else None
    if prior_report is None:
        return ()
    # Source = last retro's action_items grid PLUS any items it explicitly kept open in
    # its own review column (its carried_action_items with a still-open status). The
    # latter matters when the team marked something "Carried Over" but never clicked
    # Generate to re-add it to the grid — without this it would silently vanish. Dedup
    # by normalised text, grid items first.
    kept_open = [c for c in prior_report.carried_action_items if c.status in CARRIED_OPEN_STATUSES]
    seen: set[str] = set()
    combined = []
    for c in (*prior_report.by_grid().get("action_items", []), *kept_open):
        text = c.text.strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        combined.append(c)
    carried = tuple(replace(c, origin="carryover", status="pending") for c in combined)
    logger.info(
        "retro: %d carried-over action item(s) available from session %s (current=%s)",
        len(carried),
        prior_report.session_id,
        session_id,
    )
    return carried


def _parse_action_items(raw: str) -> list[str]:
    """Extract the action-item list from an LLM response, tolerating markdown fences."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("retro: could not parse LLM JSON response")
        return []
    items = parsed.get("action_items", []) if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        return []
    return [str(x).strip() for x in items if str(x).strip()]


def _build_fallback_action_items(didnt_go_well: list[str]) -> list[str]:
    """Deterministic action items when the LLM is unavailable.

    Turns each problem card into a plain "Address: <problem>" follow-up so the
    grid is never left empty just because AI is offline.
    """
    return [f"Address: {p}" for p in didnt_go_well[:6] if p.strip()]


def generate_action_items(board: RetroBoard) -> str:
    """Generate action items from the board's feedback and append them (origin="ai").

    Returns a short human-facing status message for the TUI (never raises).
    """
    grids = board.cards_by_grid()

    def _annotate(card) -> str:
        # Tag a card with its total reactions so the AI can weight team sentiment.
        total = sum(board.reaction_counts(card.id).values())
        return f"{card.text}  [{total} reactions]" if total else card.text

    # Raw text drives the deterministic fallback; reaction-annotated text drives the LLM.
    didnt_raw = [c.text for c in grids.get("didnt_go_well", [])]
    didnt = [_annotate(c) for c in grids.get("didnt_go_well", [])]
    went = [_annotate(c) for c in grids.get("went_well", [])]

    # Carry the loop forward: last sprint's actions the team marked "Carried Over" are
    # re-added to this sprint's grid (origin="carryover"); items still open (pending /
    # in-progress / carried-over) are handed to the LLM as context so it doesn't
    # duplicate them. Done / Not Relevant items are dropped.
    carried = board.carried_snapshot()
    carried_over_texts = [c.text for c in carried if c.status == "carried_over"]
    still_open = [c.text for c in carried if c.status in CARRIED_OPEN_STATUSES]

    if not didnt and not went:
        # A no-op click on an empty board must not mutate the grid.
        logger.info("retro: no feedback cards yet — nothing to generate")
        return "Add some cards first — no feedback to work from yet."

    if carried_over_texts:
        readded = board.add_carryover_cards(carried_over_texts)
        logger.info("retro: re-added %d carried-over action item(s) to the grid", readded)

    logger.info("retro: generating action items from %d problem / %d positive card(s)", len(didnt), len(went))

    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("retro: LLM not configured (%s) — using deterministic fallback", why)
        added = board.add_ai_cards(_build_fallback_action_items(didnt_raw))
        return f"AI unavailable ({why}) — added {added} basic action item(s)."

    # invoke_json tracks usage + turns on JSON mode + re-asks once on bad JSON.
    # See docs: "Local Mode (Ollama)" — reliability layer.
    from yeaboi.agent.llm import invoke_json
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error, _local_llm_hint
    from yeaboi.prompts.retro import get_retro_action_items_prompt

    prompt = get_retro_action_items_prompt(went_well=went, didnt_go_well=didnt, still_open=still_open)
    try:
        response = invoke_json(prompt, temperature=0.2)
        items = _parse_action_items(response.content)
    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("retro: LLM auth/billing error — surfacing as warning: %s", exc)
            added = board.add_ai_cards(_build_fallback_action_items(didnt_raw))
            return f"AI unavailable (API key/billing) — added {added} basic action item(s)."
        local_hint = _local_llm_hint(exc)
        if local_hint:
            logger.warning("retro: local Ollama failure: %s", exc)
            added = board.add_ai_cards(_build_fallback_action_items(didnt_raw))
            return f"{local_hint} Added {added} basic action item(s)."
        logger.warning("retro: LLM request failed, using fallback: %s", exc)
        added = board.add_ai_cards(_build_fallback_action_items(didnt_raw))
        return f"AI request failed — added {added} basic action item(s) (see logs)."

    if not items:
        added = board.add_ai_cards(_build_fallback_action_items(didnt_raw))
        return f"AI returned nothing usable — added {added} basic action item(s)."

    added = board.add_ai_cards(items)
    logger.info("retro: added %d AI action item(s)", added)
    return f"Generated {added} action item(s) from the team's feedback."
