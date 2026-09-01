"""Tests for the Linear GraphQL tools.

Same stance as the Jira tool tests: no cassettes — the assertion that matters
for a hand-written GraphQL call is "we sent this document with these variables
and this header", which a mocked ``httpx.post`` states exactly.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from yeaboi.tools import linear


def _resp(payload: dict, status: int = 200):
    return SimpleNamespace(status_code=status, content=b"{}", json=lambda: payload)


class Router:
    """Answers each GraphQL document by a substring match, recording calls."""

    def __init__(self, routes: list[tuple[str, dict]], status: int = 200):
        self.routes, self.status = routes, status
        self.calls: list[dict] = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "body": json or {}})
        for fragment, payload in self.routes:
            if fragment in (json or {}).get("query", ""):
                return _resp(payload, self.status)
        return _resp({"data": {}}, self.status)


TEAMS = {"data": {"teams": {"nodes": [{"id": "team-uuid", "key": "ENG", "name": "Engineering"}]}}}


def install(monkeypatch, routes, status: int = 200) -> Router:
    router = Router(routes, status)
    monkeypatch.setattr("httpx.post", router)
    return router


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "lin_key")
    monkeypatch.delenv("LINEAR_TEAM_KEY", raising=False)


class TestRequest:
    def test_the_key_rides_the_authorization_header_bare(self, monkeypatch):
        router = install(monkeypatch, [("teams", TEAMS)])
        linear._resolve_team()
        assert router.calls[0]["url"] == "https://api.linear.app/graphql"
        assert router.calls[0]["headers"]["Authorization"] == "lin_key"

    def test_a_200_with_a_graphql_errors_array_still_raises(self, monkeypatch):
        install(monkeypatch, [("teams", {"errors": [{"message": "no"}], "data": None})])
        with pytest.raises(linear.LinearError):
            linear._resolve_team()

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_rejected_key_names_the_env_var(self, monkeypatch, status):
        install(monkeypatch, [("teams", {})], status=status)
        with pytest.raises(linear.LinearError, match="LINEAR_API_KEY"):
            linear._resolve_team()

    def test_no_key_short_circuits_before_any_request(self, monkeypatch):
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        monkeypatch.setattr("httpx.post", lambda *a, **k: pytest.fail("a request left with no key"))
        with pytest.raises(linear.LinearError, match="not configured"):
            linear._resolve_team()


class TestResolveTeam:
    def test_a_sole_team_needs_no_key(self, monkeypatch):
        install(monkeypatch, [("teams", TEAMS)])
        assert linear._resolve_team()["id"] == "team-uuid"

    def test_the_team_key_chooses_case_blind(self, monkeypatch):
        two = {
            "data": {
                "teams": {
                    "nodes": [
                        {"id": "a", "key": "ENG", "name": "Engineering"},
                        {"id": "b", "key": "OPS", "name": "Operations"},
                    ]
                }
            }
        }
        install(monkeypatch, [("teams", two)])
        monkeypatch.setenv("LINEAR_TEAM_KEY", "ops")
        assert linear._resolve_team()["id"] == "b"

    def test_several_teams_and_no_key_is_an_actionable_error(self, monkeypatch):
        two = {"data": {"teams": {"nodes": [{"id": "a", "key": "ENG"}, {"id": "b", "key": "OPS"}]}}}
        install(monkeypatch, [("teams", two)])
        with pytest.raises(linear.LinearError, match="LINEAR_TEAM_KEY"):
            linear._resolve_team()

    def test_an_unknown_key_lists_what_exists(self, monkeypatch):
        install(monkeypatch, [("teams", TEAMS)])
        monkeypatch.setenv("LINEAR_TEAM_KEY", "NOPE")
        with pytest.raises(linear.LinearError, match="ENG"):
            linear._resolve_team()


class TestVelocity:
    BODY = {
        "data": {
            "team": {
                "members": {"nodes": [{"id": "u1"}, {"id": "u2"}]},
                "cycles": {
                    "nodes": [
                        {"number": 1, "completedAt": "2026-07-01", "completedScopeHistory": [0, 10]},
                        {"number": 2, "completedAt": "2026-07-15", "completedScopeHistory": [0, 14]},
                        {"number": 3, "completedAt": None, "completedScopeHistory": [0, 3]},
                    ]
                },
            }
        }
    }

    def test_averages_completed_cycles_and_divides_by_the_team(self, monkeypatch):
        install(monkeypatch, [("teams", TEAMS), ("completedScopeHistory", self.BODY)])
        data = json.loads(linear.linear_fetch_velocity.invoke({}))
        assert data == {"team_velocity": 12.0, "jira_team_size": 2, "per_dev_velocity": 6.0}

    def test_no_completed_cycles_is_an_error_string(self, monkeypatch):
        empty = {"data": {"team": {"members": {"nodes": []}, "cycles": {"nodes": []}}}}
        install(monkeypatch, [("teams", TEAMS), ("completedScopeHistory", empty)])
        assert linear.linear_fetch_velocity.invoke({}).startswith("Error")


class TestActiveSprint:
    def test_maps_the_cycle_onto_the_sprint_wire(self, monkeypatch):
        body = {"data": {"team": {"activeCycle": {"name": "", "number": 7, "startsAt": "2026-08-24T00:00:00Z"}}}}
        install(monkeypatch, [("teams", TEAMS), ("activeCycle", body)])
        data = json.loads(linear.linear_fetch_active_sprint.invoke({}))
        assert data == {"sprint_number": 7, "sprint_name": "Cycle 7", "start_date": "2026-08-24"}

    def test_no_active_cycle_says_how_to_fix_it(self, monkeypatch):
        install(monkeypatch, [("teams", TEAMS), ("activeCycle", {"data": {"team": {"activeCycle": None}}})])
        result = linear.linear_fetch_active_sprint.invoke({})
        assert result.startswith("Error")
        assert "cycle" in result.lower()


class TestCreateStory:
    CREATED = {"data": {"issueCreate": {"success": True, "issue": {"id": "uuid-1", "identifier": "ENG-9", "url": "u"}}}}

    def test_points_ride_estimate_and_priority_maps_to_linears_integers(self, monkeypatch):
        no_labels = {"data": {"issueLabels": {"nodes": []}}}
        router = install(monkeypatch, [("teams", TEAMS), ("issueLabels", no_labels), ("issueCreate", self.CREATED)])
        result = linear.linear_create_story.invoke(
            {"title": "Login", "project_id": "proj-1", "story_points": 3, "priority": "critical"}
        )
        assert "ENG-9" in result
        sent = next(c for c in router.calls if "issueCreate" in c["body"]["query"])["body"]["variables"]["input"]
        assert sent["estimate"] == 3
        assert sent["priority"] == 1
        assert sent["projectId"] == "proj-1"

    def test_the_mapping_line_carries_the_internal_id(self, monkeypatch):
        no_labels = {"data": {"issueLabels": {"nodes": []}}}
        install(monkeypatch, [("teams", TEAMS), ("issueLabels", no_labels), ("issueCreate", self.CREATED)])
        result = linear.linear_create_story.invoke({"title": "T", "project_id": "p", "internal_id": "story-3"})
        assert "Mapping: story-3 → ENG-9" in result


class TestCycles:
    def test_states_derive_from_the_cycles_own_timestamps(self, monkeypatch):
        body = {
            "data": {
                "team": {
                    "cycles": {
                        "nodes": [
                            {
                                "id": "c1",
                                "name": "",
                                "number": 1,
                                "startsAt": "2020-01-01",
                                "endsAt": "2020-01-14",
                                "completedAt": "2020-01-14",
                            },
                            {
                                "id": "c2",
                                "name": "Now",
                                "number": 2,
                                "startsAt": "2020-02-01",
                                "endsAt": "2999-01-01",
                                "completedAt": None,
                            },
                            {
                                "id": "c3",
                                "name": "Later",
                                "number": 3,
                                "startsAt": "2998-01-01",
                                "endsAt": "2999-06-01",
                                "completedAt": None,
                            },
                        ]
                    }
                }
            }
        }
        install(monkeypatch, [("teams", TEAMS), ("cycles", body)])
        cycles = linear.fetch_team_cycles(states=("active", "future"))
        assert [c["id"] for c in cycles] == ["c2", "c3"]  # closed dropped, active first
        assert cycles[0]["state"] == "active"

    def test_create_sprint_says_when_cycles_are_disabled(self, monkeypatch):
        install(monkeypatch, [("teams", TEAMS), ("cycleCreate", {"data": {"cycleCreate": {"success": False}}})])
        result = linear.linear_create_sprint.invoke(
            {"sprint_name": "Sprint 1", "start_date": "2026-09-01", "end_date": "2026-09-14"}
        )
        assert "cycles enabled" in result
