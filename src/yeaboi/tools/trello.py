"""Trello integration tools — the board-and-lists tracker.

# See docs: "Tools" — tool types, risk levels
#
# Trello authenticates with a key/token PAIR riding the query string, so no
# function in this module may ever log a request URL — paths are logged, the
# query never. Semantic mapping, chosen because lists are the only orderable,
# stateful container Trello has:
#   Epic   → board Label (applied to every card of the plan)
#   Story  → Card (points recorded in the description; Trello has no points)
#   Task   → checklist item on the parent card
#   Sprint → a List per sprint; the backlog is a "Backlog" list
"""

from __future__ import annotations

import logging
import re

from langchain_core.tools import tool

from yeaboi.config import get_trello_api_key, get_trello_board_id, get_trello_token

logger = logging.getLogger(__name__)

API_BASE = "https://api.trello.com/1"

_MISSING_CONFIG_MSG = (
    "Error: Trello is not configured. Add TRELLO_API_KEY and TRELLO_TOKEN "
    "via Settings ▸ Integrations or `yeaboi connections add trello`."
)

_TIMEOUT = 15


class TrelloError(RuntimeError):
    """A Trello call that failed, with a user-facing message (never a URL)."""


def _trello_request(method: str, path: str, params: dict | None = None) -> object:
    """One authenticated REST call, returning the decoded JSON body.

    The credentials join the query as params — the URL is never formatted with
    them and never logged.
    """
    api_key, token = get_trello_api_key(), get_trello_token()
    if not (api_key and token):
        raise TrelloError(_MISSING_CONFIG_MSG)
    import httpx

    merged = {"key": api_key, "token": token, **(params or {})}
    resp = httpx.request(method, f"{API_BASE}{path}", params=merged, timeout=_TIMEOUT)
    logger.debug("trello: %s %s -> %s", method, path, resp.status_code)
    if resp.status_code in (401, 403):
        raise TrelloError("Error: Trello rejected the credentials — check TRELLO_API_KEY and TRELLO_TOKEN.")
    if resp.status_code == 404:
        raise TrelloError(f"Error: Trello has no {path.split('/')[1] or 'resource'} there — check the id.")
    if resp.status_code == 429:
        raise TrelloError("Error: Trello rate limit hit — try again in a minute.")
    if resp.status_code >= 400:
        raise TrelloError(f"Error: Unexpected Trello response ({resp.status_code}).")
    try:
        return resp.json()
    except Exception:
        raise TrelloError("Error: Trello returned a non-JSON response.") from None


def _resolve_board(board_hint: str = "") -> dict:
    """The board to plan against: the argument, else TRELLO_BOARD_ID, else the sole open board."""
    boards = _trello_request("GET", "/members/me/boards", {"filter": "open", "fields": "name"})
    boards = [b for b in boards if isinstance(b, dict)] if isinstance(boards, list) else []
    if not boards:
        raise TrelloError("Error: The Trello token can see no open boards.")
    wanted = board_hint.strip() or (get_trello_board_id() or "").strip()
    if wanted:
        board = next((b for b in boards if b.get("id") == wanted or b.get("name") == wanted), None)
        if board is None:
            known = ", ".join(str(b.get("name", "")) for b in boards)
            raise TrelloError(f"Error: No Trello board '{wanted}'. Boards: {known}")
        return board
    if len(boards) == 1:
        return boards[0]
    known = ", ".join(str(b.get("name", "")) for b in boards)
    raise TrelloError(f"Error: Several Trello boards ({known}) — set TRELLO_BOARD_ID to choose one.")


# ---------------------------------------------------------------------------
# Non-@tool helpers used by trello_sync.py (not exposed to the agent)
# ---------------------------------------------------------------------------


def fetch_board_lists(include_closed: bool = False) -> list[dict]:
    """The board's lists as ``{id, name, closed}``, board order preserved."""
    board = _resolve_board()
    filter_value = "all" if include_closed else "open"
    lists = _trello_request("GET", f"/boards/{board['id']}/lists", {"filter": filter_value, "fields": "name,closed"})
    return [row for row in lists if isinstance(row, dict)] if isinstance(lists, list) else []


def create_list(name: str) -> dict:
    """Create one list at the bottom of the board. Returns {id, name}."""
    board = _resolve_board()
    created = _trello_request("POST", "/lists", {"idBoard": board["id"], "name": name, "pos": "bottom"})
    if not isinstance(created, dict) or not created.get("id"):
        raise TrelloError("Error: Trello did not create the list.")
    return created


def create_checklist_with_items(card_id: str, name: str, items: list[str]) -> str:
    """Create one checklist on a card and fill it. Returns the checklist id."""
    checklist = _trello_request("POST", "/checklists", {"idCard": card_id, "name": name})
    if not isinstance(checklist, dict) or not checklist.get("id"):
        raise TrelloError("Error: Trello did not create the checklist.")
    for item in items:
        _trello_request("POST", f"/checklists/{checklist['id']}/checkItems", {"name": item[:500]})
    return str(checklist["id"])


def move_card_to_list(card_id: str, list_id: str) -> None:
    _trello_request("PUT", f"/cards/{card_id}", {"idList": list_id})


def _numbered_open_lists() -> list[tuple[int, dict]]:
    """Open lists whose names end in a number — the board's sprint sequence."""
    numbered = []
    for row in fetch_board_lists():
        match = re.search(r"(\d+)\s*$", str(row.get("name", "")))
        if match:
            numbered.append((int(match.group(1)), row))
    numbered.sort(key=lambda pair: pair[0])
    return numbered


# ---------------------------------------------------------------------------
# @tool functions (exposed to the agent)
# ---------------------------------------------------------------------------


@tool
def trello_read_board(board: str = "") -> str:
    """Read the current state of a Trello board: its lists, card counts, and labels.

    Falls back to TRELLO_BOARD_ID (or the account's sole open board) when board
    is not provided. Returns a formatted summary of the board's lists with
    their card counts and the labels in use.
    """
    # See docs: "The ReAct Loop" — this is the Action step; the result is the Observation
    logger.debug("trello_read_board called with board=%r", board)
    if not (get_trello_api_key() and get_trello_token()):
        return _MISSING_CONFIG_MSG
    try:
        resolved = _resolve_board(board)
        detail = _trello_request(
            "GET",
            f"/boards/{resolved['id']}",
            {"lists": "open", "cards": "open", "card_fields": "idList", "fields": "name"},
        )
        lists = detail.get("lists", []) if isinstance(detail, dict) else []
        cards = detail.get("cards", []) if isinstance(detail, dict) else []
        counts: dict[str, int] = {}
        for card in cards:
            if isinstance(card, dict):
                counts[str(card.get("idList"))] = counts.get(str(card.get("idList")), 0) + 1
        lines = [f"Board: {detail.get('name', resolved.get('name', ''))}", ""]
        for row in lists:
            if isinstance(row, dict):
                lines.append(f"  {row.get('name')}: {counts.get(str(row.get('id')), 0)} card(s)")
        labels = _trello_request("GET", f"/boards/{resolved['id']}/labels", {"fields": "name"})
        names = [str(x.get("name")) for x in labels if isinstance(x, dict) and x.get("name")]
        if names:
            lines.append(f"Labels: {', '.join(names[:15])}")
        return "\n".join(lines)
    except TrelloError as e:
        return str(e)
    except Exception as e:
        logger.error("Unexpected error in trello_read_board: %s", e)
        return f"Error: {e}"


@tool
def trello_fetch_active_sprint(board: str = "") -> str:
    """Fetch the board's current sprint, read as its highest trailing-numbered open list.

    Trello has no sprint primitive — a list per sprint is the convention this
    integration writes, and this reads it back. Returns a JSON string with
    keys: sprint_number, sprint_name, start_date (always null — lists carry no
    dates). Returns an error string starting with "Error:" when no numbered
    list exists.
    """
    import json

    logger.debug("trello_fetch_active_sprint called")
    if not (get_trello_api_key() and get_trello_token()):
        return _MISSING_CONFIG_MSG
    try:
        numbered = _numbered_open_lists()
        if not numbered:
            return "Error: No open list ends in a number — no sprint convention to read."
        number, row = numbered[-1]
        return json.dumps({"sprint_number": number, "sprint_name": str(row.get("name")), "start_date": None})
    except TrelloError as e:
        return str(e)
    except Exception as e:
        logger.error("Unexpected error in trello_fetch_active_sprint: %s", e)
        return f"Error: {e}"


@tool
def trello_create_epic(title: str, internal_id: str = "") -> str:
    """Create the plan's container on the Trello board: a Label every card will carry.

    Trello has no epic type — a board label groups the plan's cards the way an
    epic groups issues. Only call this after the user has explicitly confirmed
    they want to create items in Trello. Pass internal_id (e.g. 'epic-1') to
    get a 'Mapping:' line. Returns the new label's id on success.
    """
    logger.debug("trello_create_epic called: title=%r", title)
    if not (get_trello_api_key() and get_trello_token()):
        return _MISSING_CONFIG_MSG
    try:
        board = _resolve_board()
        created = _trello_request("POST", "/labels", {"idBoard": board["id"], "name": title[:50], "color": "purple"})
        if not isinstance(created, dict) or not created.get("id"):
            return "Error: Trello did not create the label."
        lines = [f"Created Label: {created['id']} — {title[:50]}"]
        if internal_id:
            lines.append(f"Mapping: {internal_id} → {created['id']}")
        return "\n".join(lines)
    except TrelloError as e:
        return str(e)
    except Exception as e:
        logger.error("Unexpected error in trello_create_epic: %s", e)
        return f"Error: {e}"


@tool
def trello_create_story(
    title: str,
    list_name: str = "Backlog",
    story_points: int = 0,
    description: str = "",
    internal_id: str = "",
    label_ids: list[str] | None = None,
) -> str:
    """Create a card on the Trello board, in the named list (created if missing).

    Only call this after the user has explicitly confirmed they want to create
    items in Trello. Trello has no points field, so story_points are recorded
    at the top of the description. Pass internal_id (e.g. 'story-3') to get a
    'Mapping:' line. Returns the new card's id and URL on success.
    """
    logger.debug("trello_create_story called: title=%r, list=%r", title, list_name)
    if not (get_trello_api_key() and get_trello_token()):
        return _MISSING_CONFIG_MSG
    try:
        target = next((row for row in fetch_board_lists() if row.get("name") == list_name), None)
        if target is None:
            target = create_list(list_name)
        desc = f"**Points: {story_points}**\n\n{description}" if story_points else description
        params: dict = {"idList": target["id"], "name": title, "desc": desc}
        if label_ids:
            params["idLabels"] = ",".join(label_ids)
        created = _trello_request("POST", "/cards", params)
        if not isinstance(created, dict) or not created.get("id"):
            return "Error: Trello did not create the card."
        lines = [f"Created Card: {created['id']}", f"URL: {created.get('shortUrl') or created.get('url', '')}"]
        if internal_id:
            lines.append(f"Mapping: {internal_id} → {created['id']}")
        return "\n".join(lines)
    except TrelloError as e:
        return str(e)
    except Exception as e:
        logger.error("Unexpected error in trello_create_story: %s", e)
        return f"Error: {e}"


@tool
def trello_create_sprint(sprint_name: str) -> str:
    """Create a sprint on the Trello board: a new list at the bottom.

    Only call this after the user has explicitly confirmed they want to create
    items in Trello. Cards are moved into the list to plan the sprint. Returns
    the new list's id and name on success.
    """
    logger.debug("trello_create_sprint called: name=%r", sprint_name)
    if not (get_trello_api_key() and get_trello_token()):
        return _MISSING_CONFIG_MSG
    try:
        created = create_list(sprint_name)
        return f"Created list '{created.get('name', sprint_name)}' (ID: {created['id']})"
    except TrelloError as e:
        return str(e)
    except Exception as e:
        logger.error("Unexpected error in trello_create_sprint: %s", e)
        return f"Error: {e}"
