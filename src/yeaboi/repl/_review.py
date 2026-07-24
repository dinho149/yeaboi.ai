"""Review checkpoint helpers — artifact clearing, choice resolution, serialization."""

import logging

from yeaboi.agent.state import ReviewDecision

logger = logging.getLogger(__name__)

# See docs: "Guardrails" — human-in-the-loop pattern
#
# After each generation node (feature_generator, story_writer, task_decomposer,
# sprint_planner), the REPL intercepts the next user input for the
# [Accept / Edit / Reject] review flow. These constants map node names to
# their artifact keys in graph_state and define the pipeline order for
# downstream cascade clearing.

_REVIEW_ARTIFACT_KEYS: dict[str, str] = {
    "project_analyzer": "project_analysis",
    "feature_generator": "features",
    "story_writer": "stories",
    "task_decomposer": "tasks",
    "sprint_planner": "sprints",
}

# Pipeline order — used to determine which artifacts are downstream of a given node.
_PIPELINE_ORDER: tuple[str, ...] = (
    "project_analyzer",
    "feature_generator",
    "story_writer",
    "task_decomposer",
    "sprint_planner",
)

# Rich-formatted hint shown during review checkpoints.
# Uses the same [command]\\[N][/command] pattern as _render_choice_options() (intake menus)
# so the UX feels consistent across all numbered selection prompts.
REVIEW_HINT = (
    "  [command]\\[1][/command] Accept   "
    "[command]\\[2][/command] Edit   "
    "[command]\\[3][/command] Export\n"
    "[hint](pick a number, or type accept / edit / export)[/hint]"
)

# Ordered menu options — index+1 maps to the keyword passed to the handler.
# Follows the same pattern as INTAKE_MODE_ORDER and OFFLINE_SUBMENU_ORDER.
# Reject is removed — Edit covers the same need with better UX (keeps previous
# output as context so the LLM knows what to change, not just what was wrong).
_REVIEW_MENU_ORDER: tuple[str, ...] = ("accept", "edit", "export")


def _clear_downstream_artifacts(graph_state: dict, node_name: str) -> None:
    """Clear artifacts from all nodes downstream of (and including) the given node.

    # See docs: "Guardrails" — human-in-the-loop pattern
    #
    # When the user rejects features, stories/tasks/sprints derived from those
    # features are stale and must be cleared. This function clears the artifact
    # key for the given node and all downstream nodes in the pipeline.
    #
    # Mutates graph_state in place — the REPL operates between graph.invoke()
    # calls, so operator.add reducers don't apply here.

    Args:
        graph_state: The mutable graph state dict.
        node_name: The node whose artifacts (and downstream) should be cleared.
    """
    try:
        start_idx = _PIPELINE_ORDER.index(node_name)
    except ValueError:
        logger.warning("_clear_downstream_artifacts: unknown node %s", node_name)
        return

    cleared = []
    for downstream_node in _PIPELINE_ORDER[start_idx:]:
        artifact_key = _REVIEW_ARTIFACT_KEYS[downstream_node]
        if artifact_key in graph_state:
            del graph_state[artifact_key]
            cleared.append(artifact_key)
    logger.info("Cleared downstream artifacts from %s: %s", node_name, cleared)


def _resolve_review_choice(user_input: str) -> str:
    """Resolve numeric input to the corresponding review keyword.

    Maps "1" → "accept", "2" → "edit", "3" → "reject".
    Non-numeric input passes through unchanged so existing keywords
    (accept, lgtm, reject: feedback, edit: changes, etc.) still flow
    directly to _parse_review_intent.

    Follows the same pattern as _resolve_intake_mode() and _resolve_offline_choice().

    Args:
        user_input: The raw user input (stripped).

    Returns:
        The resolved keyword, or the original input if not a valid menu number.
    """
    try:
        idx = int(user_input)
    except ValueError:
        return user_input

    if 1 <= idx <= len(_REVIEW_MENU_ORDER):
        resolved = _REVIEW_MENU_ORDER[idx - 1]
        logger.debug("_resolve_review_choice: %s -> %s", user_input, resolved)
        return resolved
    return user_input


def _is_unrecognized_review_input(resolved: str, decision: ReviewDecision, feedback: str) -> bool:
    """Detect when _parse_review_intent fell through to the default REJECT path.

    The fallback in _parse_review_intent returns (REJECT, original_text) for any
    unrecognized input. This catches typos (e.g. "accpet") without flagging
    intentional rejects — those either have empty feedback (bare "reject") or
    feedback after a colon (e.g. "reject: more detail").

    The key distinction: fallback REJECT returns the full original text as
    feedback, whereas intentional "reject" returns empty feedback, and
    "reject: reason" returns only the part after the colon.

    Args:
        resolved: The input after _resolve_review_choice (numeric already mapped).
        decision: The ReviewDecision returned by _parse_review_intent.
        feedback: The feedback string returned by _parse_review_intent.

    Returns:
        True if the input was unrecognized (typo), False otherwise.
    """
    if decision != ReviewDecision.REJECT:
        return False
    # Intentional reject keywords return empty feedback or stripped suffix.
    # Fallback returns the full original text as feedback — so feedback == resolved.
    return feedback == resolved


def _serialize_artifacts_for_review(graph_state: dict, node_name: str) -> str:
    """Serialize current artifacts as text for edit mode reference.

    Produces a simple JSON-like text representation that gets packed into
    the feedback string so the generation node can include it as context.

    Args:
        graph_state: The current graph state dict.
        node_name: The node whose artifacts to serialize.

    Returns:
        A text representation of the artifacts, or "" if none found.
    """
    import json
    from dataclasses import asdict

    artifact_key = _REVIEW_ARTIFACT_KEYS.get(node_name, "")
    artifacts = graph_state.get(artifact_key, [])
    if not artifacts:
        return ""

    try:
        serialized = [asdict(item) for item in artifacts]
        return json.dumps(serialized, indent=2, default=str)
    except Exception:
        return ""
