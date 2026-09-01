"""Batch Trello creation with idempotency, progress callbacks, and error accumulation.

# See docs: "Tools" — tool types, write tools, human-in-the-loop pattern
#
# The Trello counterpart of jira_sync.py, called by the TUI pipeline review
# screens, the project export button and MCP plan_sync — NOT by the ReAct agent.
#
# Idempotency: each sync function checks the trello_*_keys dicts in graph_state
# before creating anything, so a re-run after a partial failure is safe.
#
# Semantic mapping (lists are the only orderable, stateful container Trello has):
#   Epic   → one board Label, applied to every card of the plan
#   UserStories → Cards (points recorded in the description)
#   Tasks  → one checklist per story card, one item per task
#   Sprints → a List per sprint, named by the board's own numbering convention;
#             the backlog is a "Backlog" list
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from yeaboi.config import get_trello_api_key, get_trello_token

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]

#: Where a card lands before any sprint list exists.
BACKLOG_LIST = "Backlog"


@dataclass
class TrelloSyncResult:
    """Accumulates results from a batch Trello sync operation."""

    epic_label_id: str | None = None
    stories_created: dict[str, str] = field(default_factory=dict)  # internal_id → card id
    tasks_created: dict[str, str] = field(default_factory=dict)  # internal_id → checklist id
    lists_created: dict[str, str] = field(default_factory=dict)  # internal_id → list id
    lists_updated: dict[str, str] = field(default_factory=dict)  # existing lists that gained cards
    errors: list[str] = field(default_factory=list)
    skipped: int = 0


def is_trello_configured() -> bool:
    """Return True if the Trello key/token pair is present in the environment."""
    return bool(get_trello_api_key() and get_trello_token())


def _ensure_list(name: str) -> dict:
    from yeaboi.tools.trello import create_list, fetch_board_lists

    existing = next((row for row in fetch_board_lists() if row.get("name") == name), None)
    return existing if existing is not None else create_list(name)


def sync_stories_to_trello(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[TrelloSyncResult, dict[str, Any]]:
    """Create the epic Label and the plan's Cards, skipping already-created items.

    Cards land in the Backlog list; the sprint sync moves them. Returns
    (result, updated_graph_state).
    """
    from yeaboi.tools.trello import TrelloError, _resolve_board, _trello_request

    result = TrelloSyncResult()
    state = dict(graph_state)

    if not is_trello_configured():
        result.errors.append("Trello not configured — missing TRELLO_API_KEY / TRELLO_TOKEN.")
        return result, state

    try:
        board = _resolve_board()
    except TrelloError as e:
        result.errors.append(str(e).removeprefix("Error: "))
        return result, state

    stories = state.get("stories", [])
    existing_story_keys: dict[str, str] = dict(state.get("trello_story_keys", {}))
    total = 1 + len(stories)
    current = 0

    # --- Epic label ---
    label_id = state.get("trello_epic_label_id", "")
    if not label_id:
        try:
            analysis = state.get("project_analysis")
            title = getattr(analysis, "project_name", None) or state.get("project_name", "Project")
            params = {"idBoard": board["id"], "name": title[:50], "color": "purple"}
            created = _trello_request("POST", "/labels", params)
            if not isinstance(created, dict) or not created.get("id"):
                result.errors.append("Epic label creation failed: Trello said no.")
                return result, state
            label_id = str(created["id"])
            state["trello_epic_label_id"] = label_id
            result.epic_label_id = label_id
            logger.info("Created Trello epic label: %s", label_id)
        except Exception as e:
            result.errors.append(f"Epic label creation failed: {e}")
            return result, state
    else:
        result.epic_label_id = label_id
        result.skipped += 1

    current += 1
    if on_progress:
        on_progress(current, total, "Epic label ready")

    from yeaboi.agent.state import map_template_headings, resolve_dod_items
    from yeaboi.jira_sync import _format_story_description

    dod_items = resolve_dod_items(state)
    headings = map_template_headings(tuple(state.get("ticket_template_sections") or ()))

    try:
        backlog = _ensure_list(BACKLOG_LIST)
    except Exception as e:
        result.errors.append(f"Backlog list: {e}")
        return result, state

    features = {f.id: f for f in state.get("features", [])}
    new_story_keys: dict[str, str] = {}
    for story in stories:
        if story.id in existing_story_keys:
            result.skipped += 1
            current += 1
            if on_progress:
                on_progress(current, total, f"Card exists: {story.title or story.goal[:40]}")
            continue
        try:
            feature = features.get(story.feature_id)
            description = _format_story_description(story, feature, dod_items=dod_items, headings=headings)
            points = int(story.story_points) if story.story_points else 0
            desc = f"**Points: {points}**\n\n{description}" if points else description
            created = _trello_request(
                "POST",
                "/cards",
                {"idList": backlog["id"], "name": story.title or story.goal, "desc": desc, "idLabels": label_id},
            )
            if not isinstance(created, dict) or not created.get("id"):
                raise TrelloError("Trello said no")
            new_story_keys[story.id] = str(created["id"])
            result.stories_created[story.id] = str(created["id"])
            logger.info("Created Trello card: %s → %s", story.id, created["id"])
            time.sleep(0.1)
        except Exception as e:
            err = f"Story '{story.title or story.id}': {e}"
            logger.error("Trello sync failed — %s", err)
            result.errors.append(err)
        current += 1
        if on_progress:
            on_progress(current, total, f"Card created: {story.title or story.goal[:40]}")

    state["trello_story_keys"] = {**existing_story_keys, **new_story_keys}
    return result, state


def sync_tasks_to_trello(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[TrelloSyncResult, dict[str, Any]]:
    """Create one checklist per story card, one item per task.

    Cascades to create stories first if needed. A story's tasks all share one
    checklist, so every task of a story maps to the same checklist id.
    """
    from yeaboi.tools.trello import create_checklist_with_items

    state = dict(graph_state)
    story_keys = state.get("trello_story_keys", {})
    stories = state.get("stories", [])
    if stories and not story_keys:
        story_result, state = sync_stories_to_trello(state, on_progress)
        story_keys = state.get("trello_story_keys", {})
        if story_result.errors and not story_keys:
            return story_result, state

    result = TrelloSyncResult(epic_label_id=state.get("trello_epic_label_id"))
    if not is_trello_configured():
        result.errors.append("Trello not configured — missing TRELLO_API_KEY / TRELLO_TOKEN.")
        return result, state

    tasks = state.get("tasks", [])
    existing_task_keys: dict[str, str] = dict(state.get("trello_task_keys", {}))
    by_story: dict[str, list] = {}
    for task in tasks:
        if task.id not in existing_task_keys:
            by_story.setdefault(task.story_id, []).append(task)

    total = len(by_story)
    current = 0
    new_task_keys: dict[str, str] = {}
    result.skipped += sum(1 for task in tasks if task.id in existing_task_keys)

    for story_id, story_tasks in by_story.items():
        card_id = story_keys.get(story_id, "")
        if not card_id:
            result.errors.append(f"Tasks for {story_id}: the story has no Trello card.")
            current += 1
            continue
        try:
            checklist_id = create_checklist_with_items(card_id, "Tasks", [t.title for t in story_tasks])
            for task in story_tasks:
                new_task_keys[task.id] = checklist_id
                result.tasks_created[task.id] = checklist_id
            logger.info("Created Trello checklist on %s (%d item(s))", card_id, len(story_tasks))
            time.sleep(0.1)
        except Exception as e:
            err = f"Tasks for {story_id}: {e}"
            logger.error("Trello sync failed — %s", err)
            result.errors.append(err)
        current += 1
        if on_progress:
            on_progress(current, total, f"Checklist created ({len(story_tasks)} task(s))")

    state["trello_task_keys"] = {**existing_task_keys, **new_task_keys}
    return result, state


def sync_sprints_to_trello(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[TrelloSyncResult, dict[str, Any]]:
    """Create a List per sprint and move each sprint's cards into it.

    List names follow the board's own numbering convention (sync_naming), and
    archived lists advance the sequence exactly as closed sprints do on Jira.
    Honors the small-project landing decision: "backlog" leaves every card in
    the Backlog list; "existing" moves them all to the chosen open list.
    """
    from yeaboi.sync_naming import advance_past_closed, derive_board_numbering, resolve_starting_number
    from yeaboi.tools.trello import TrelloError, create_list, fetch_board_lists, move_card_to_list

    state = dict(graph_state)
    story_keys = state.get("trello_story_keys", {})
    stories = state.get("stories", [])
    if stories and not story_keys:
        story_result, state = sync_stories_to_trello(state, on_progress)
        story_keys = state.get("trello_story_keys", {})
        if story_result.errors and not story_keys:
            return story_result, state
        result_stories = dict(story_result.stories_created)
        result_story_errors = list(story_result.errors)
    else:
        result_stories = {}
        result_story_errors = []

    result = TrelloSyncResult(epic_label_id=state.get("trello_epic_label_id"))
    result.stories_created.update(result_stories)
    result.errors.extend(result_story_errors)

    if state.get("sprint_target_mode") == "backlog":
        logger.info("Sprint sync: backlog mode — cards stay in the Backlog list")
        if on_progress:
            on_progress(1, 1, "Cards left in the Backlog — no sprint list created")
        return result, state

    if not is_trello_configured():
        result.errors.append("Trello not configured — missing TRELLO_API_KEY / TRELLO_TOKEN.")
        return result, state

    sprints = state.get("sprints", [])
    existing_list_keys: dict[str, str] = dict(state.get("trello_list_keys", {}))

    try:
        board_lists = fetch_board_lists(include_closed=True)
    except TrelloError as e:
        result.errors.append(str(e).removeprefix("Error: "))
        return result, state

    open_by_name = {str(row.get("name")): row for row in board_lists if not row.get("closed")}

    if state.get("sprint_target_mode") == "existing":
        return _sync_to_existing_list(state, result, story_keys, open_by_name, on_progress)

    # The board's naming convention, archived lists counted as closed so the
    # sequence never reuses a number a finished sprint already wore.
    numbering = derive_board_numbering(
        (str(row.get("name")), "closed" if row.get("closed") else "active") for row in board_lists
    )
    starting_number = resolve_starting_number(state.get("starting_sprint_number", 0), numbering)
    starting_number, closed_warn = advance_past_closed(starting_number, len(sprints), numbering)
    if closed_warn:
        logger.warning("%s", closed_warn)
        if on_progress:
            on_progress(0, len(sprints), closed_warn)

    total = len(sprints)
    current = 0
    new_list_keys: dict[str, str] = {}

    for idx, sprint in enumerate(sprints):
        list_name = sprint.name
        if numbering.prefix and starting_number:
            list_name = f"{numbering.prefix}{starting_number + idx}"

        if sprint.id in existing_list_keys:
            result.skipped += 1
            current += 1
            if on_progress:
                on_progress(current, total, f"List exists: {list_name}")
            continue

        progress_label = f"List failed: {list_name}"
        try:
            matched = open_by_name.get(list_name)
            if matched:
                list_id = str(matched["id"])
                progress_label = f"List reused: {list_name}"
                result.lists_updated[sprint.id] = list_id
            else:
                created = create_list(list_name)
                list_id = str(created["id"])
                progress_label = f"List created: {list_name}"
                result.lists_created[sprint.id] = list_id
                logger.info("Created Trello list: %s → %s", list_name, list_id)

            new_list_keys[sprint.id] = list_id
            for sid in sprint.story_ids:
                if sid in story_keys:
                    move_card_to_list(story_keys[sid], list_id)
            time.sleep(0.1)
        except Exception as e:
            err = f"Sprint '{list_name}': {e}"
            logger.error("Trello sync failed — %s", err)
            result.errors.append(err)

        current += 1
        if on_progress:
            on_progress(current, total, progress_label)

    state["trello_list_keys"] = {**existing_list_keys, **new_list_keys}
    return result, state


def _sync_to_existing_list(
    state: dict[str, Any],
    result: TrelloSyncResult,
    story_keys: dict[str, str],
    open_by_name: dict[str, dict],
    on_progress: ProgressCallback | None,
) -> tuple[TrelloSyncResult, dict[str, Any]]:
    """Move the plan's cards into one chosen open list; create nothing."""
    from yeaboi.tools.trello import move_card_to_list

    sprints = state.get("sprints", [])
    target_name = str(state.get("target_sprint_name") or "")
    target_ext = str(state.get("target_sprint_external_id") or "")
    target = None
    if target_ext:
        target = next((row for row in open_by_name.values() if str(row.get("id")) == target_ext), None)
    if target is None and target_name:
        target = open_by_name.get(target_name)
    if target is None:
        result.errors.append(f"No open Trello list matches '{target_name or target_ext}'.")
        return result, state

    card_ids = sorted({story_keys[sid] for sprint in sprints for sid in sprint.story_ids if sid in story_keys})
    try:
        for card_id in card_ids:
            move_card_to_list(card_id, str(target["id"]))
    except Exception as e:
        result.errors.append(f"Could not move cards to list '{target.get('name')}': {e}")
        return result, state

    merged = dict(state.get("trello_list_keys", {}))
    for sprint in sprints:
        merged[sprint.id] = str(target["id"])
        result.lists_updated[sprint.id] = str(target["id"])
    state["trello_list_keys"] = merged
    logger.info("Moved %d card(s) to existing list %s", len(card_ids), target.get("name"))
    if on_progress:
        on_progress(len(sprints), len(sprints), f"Cards moved to {target.get('name')}")
    return result, state


def sync_all_to_trello(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[TrelloSyncResult, dict[str, Any]]:
    """Full sync: Label + Cards + checklists + Lists, aggregating results."""
    state = dict(graph_state)
    aggregated = TrelloSyncResult()

    story_result, state = sync_stories_to_trello(state, on_progress)
    aggregated.epic_label_id = story_result.epic_label_id
    aggregated.stories_created.update(story_result.stories_created)
    aggregated.errors.extend(story_result.errors)
    aggregated.skipped += story_result.skipped

    if state.get("tasks"):
        task_result, state = sync_tasks_to_trello(state, on_progress)
        aggregated.tasks_created.update(task_result.tasks_created)
        aggregated.errors.extend(task_result.errors)
        aggregated.skipped += task_result.skipped

    if state.get("sprints"):
        sprint_result, state = sync_sprints_to_trello(state, on_progress)
        aggregated.stories_created.update(sprint_result.stories_created)
        aggregated.lists_created.update(sprint_result.lists_created)
        aggregated.lists_updated.update(sprint_result.lists_updated)
        aggregated.errors.extend(sprint_result.errors)
        aggregated.skipped += sprint_result.skipped

    return aggregated, state
