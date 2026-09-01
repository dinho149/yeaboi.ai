"""Tests for the Trello REST tools.

The one Trello-specific guarantee under test everywhere here: the credentials
ride ``params``, never a formatted URL — so nothing this module logs or returns
can carry them.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from yeaboi.tools import trello


class Router:
    """Answers each REST path by substring match, recording every call."""

    def __init__(self, routes: list[tuple[str, object]], status: int = 200):
        self.routes, self.status = routes, status
        self.calls: list[dict] = []

    def __call__(self, method, url, params=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params or {}})
        for fragment, payload in self.routes:
            if fragment in url:
                return SimpleNamespace(status_code=self.status, json=lambda p=payload: p)
        return SimpleNamespace(status_code=self.status, json=lambda: {})


BOARDS = [{"id": "board-1", "name": "Platform"}]
LISTS = [
    {"id": "l-backlog", "name": "Backlog", "closed": False},
    {"id": "l-s1", "name": "Sprint 1", "closed": False},
    {"id": "l-s2", "name": "Sprint 2", "closed": False},
]


def install(monkeypatch, routes, status: int = 200) -> Router:
    router = Router(routes, status)
    monkeypatch.setattr("httpx.request", router)
    return router


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("TRELLO_API_KEY", "k123")
    monkeypatch.setenv("TRELLO_TOKEN", "t456")
    monkeypatch.delenv("TRELLO_BOARD_ID", raising=False)


class TestRequest:
    def test_credentials_ride_params_never_the_url(self, monkeypatch):
        router = install(monkeypatch, [("/members/me/boards", BOARDS)])
        trello._resolve_board()
        call = router.calls[0]
        assert call["params"]["key"] == "k123"
        assert call["params"]["token"] == "t456"
        assert "k123" not in call["url"] and "t456" not in call["url"]

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_credentials_name_both_env_vars(self, monkeypatch, status):
        install(monkeypatch, [("/members/me/boards", BOARDS)], status=status)
        with pytest.raises(trello.TrelloError, match="TRELLO_API_KEY"):
            trello._resolve_board()

    def test_no_credentials_short_circuit_before_any_request(self, monkeypatch):
        monkeypatch.delenv("TRELLO_TOKEN", raising=False)
        monkeypatch.setattr("httpx.request", lambda *a, **k: pytest.fail("a request left with no token"))
        with pytest.raises(trello.TrelloError, match="not configured"):
            trello._resolve_board()


class TestResolveBoard:
    def test_a_sole_open_board_needs_no_id(self, monkeypatch):
        install(monkeypatch, [("/members/me/boards", BOARDS)])
        assert trello._resolve_board()["id"] == "board-1"

    def test_the_board_id_or_name_chooses(self, monkeypatch):
        two = BOARDS + [{"id": "board-2", "name": "Marketing"}]
        install(monkeypatch, [("/members/me/boards", two)])
        monkeypatch.setenv("TRELLO_BOARD_ID", "Marketing")
        assert trello._resolve_board()["id"] == "board-2"

    def test_several_boards_and_no_id_is_an_actionable_error(self, monkeypatch):
        two = BOARDS + [{"id": "board-2", "name": "Marketing"}]
        install(monkeypatch, [("/members/me/boards", two)])
        with pytest.raises(trello.TrelloError, match="TRELLO_BOARD_ID"):
            trello._resolve_board()


class TestActiveSprint:
    def test_the_highest_numbered_open_list_is_the_sprint(self, monkeypatch):
        install(monkeypatch, [("/members/me/boards", BOARDS), ("/lists", LISTS)])
        data = json.loads(trello.trello_fetch_active_sprint.invoke({}))
        assert data == {"sprint_number": 2, "sprint_name": "Sprint 2", "start_date": None}

    def test_no_numbered_list_is_an_error_string(self, monkeypatch):
        install(monkeypatch, [("/members/me/boards", BOARDS), ("/lists", [LISTS[0]])])
        assert trello.trello_fetch_active_sprint.invoke({}).startswith("Error")


class TestCreateStory:
    def test_points_are_recorded_in_the_description(self, monkeypatch):
        router = install(
            monkeypatch,
            [
                ("/members/me/boards", BOARDS),
                ("/lists", LISTS),
                ("/cards", {"id": "card-9", "shortUrl": "https://trello.com/c/x"}),
            ],
        )
        result = trello.trello_create_story.invoke(
            {"title": "Login", "story_points": 5, "description": "d", "internal_id": "story-1"}
        )
        assert "card-9" in result and "Mapping: story-1 → card-9" in result
        card_call = next(c for c in router.calls if c["url"].endswith("/cards"))
        assert card_call["params"]["desc"].startswith("**Points: 5**")
        assert card_call["params"]["idList"] == "l-backlog"

    def test_a_missing_list_is_created_first(self, monkeypatch):
        router = install(
            monkeypatch,
            [
                ("/members/me/boards", BOARDS),
                ("/lists", []),  # answers both the fetch and the create
                ("/cards", {"id": "card-1"}),
            ],
        )
        # The create-list POST answers with a dict, not the empty fetch list.
        router.routes[1] = ("/lists", {"id": "l-new", "name": "Backlog"})
        trello.trello_create_story.invoke({"title": "T"})
        posts = [c for c in router.calls if c["method"] == "POST" and c["url"].endswith("/lists")]
        assert len(posts) == 1


class TestCreateEpic:
    def test_the_epic_is_a_board_label(self, monkeypatch):
        router = install(monkeypatch, [("/members/me/boards", BOARDS), ("/labels", {"id": "lbl-1"})])
        result = trello.trello_create_epic.invoke({"title": "Auth Platform", "internal_id": "epic-1"})
        assert "Mapping: epic-1 → lbl-1" in result
        label_call = next(c for c in router.calls if c["url"].endswith("/labels"))
        assert label_call["params"]["name"] == "Auth Platform"
