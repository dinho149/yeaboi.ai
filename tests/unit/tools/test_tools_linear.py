"""Tests for Linear tools.

Linear has no Python SDK, so tools/linear.py talks to the GraphQL endpoint with
stdlib urllib. That gives two seams to test, and both are covered here:

* the tools — ``_graphql`` is monkeypatched so no request is built at all;
* ``_graphql`` itself — ``urlopen`` is monkeypatched so no socket is opened.

Nothing in this file touches the network. Mirrors test_tools_notion.py's layout
(helpers → pure helpers → one class per tool → registration), with the extra
transport section Notion does not need because notion-client owns that layer.
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock

import pytest

from yeaboi.redaction import redact
from yeaboi.tools import get_tools
from yeaboi.tools import linear as linear_module
from yeaboi.tools.linear import (
    _MISSING_CONFIG_MSG,
    _MISSING_TEAM_MSG,
    LinearAPIError,
    _completed_points,
    _cycle_label,
    _graphql,
    _linear_error_msg,
    _resolve_team_key,
    linear_create_issue,
    linear_fetch_active_cycle,
    linear_fetch_velocity,
    linear_read_board,
)
from yeaboi.tools.risk import TOOL_RISK, ToolRisk

# ---------------------------------------------------------------------------
# Helpers — fake GraphQL transport and payload builders
# ---------------------------------------------------------------------------


class _FakeGraphQL:
    """Stand-in for ``_graphql``: returns queued responses, records every call.

    A queued entry that is an Exception is raised instead of returned, which is
    how the error-path tests inject a LinearAPIError without a real request.
    """

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, query: str, variables: dict, **kwargs):
        self.calls.append((query, variables))
        result = self.responses.pop(0) if self.responses else {}
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def configured(monkeypatch):
    """Set both Linear env vars so tools get past their config guards."""
    monkeypatch.setenv("LINEAR_API_KEY", "fake-linear-key-for-tests")
    monkeypatch.setenv("LINEAR_TEAM_KEY", "YEA")


@pytest.fixture
def unconfigured(monkeypatch):
    """Remove both Linear env vars."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_TEAM_KEY", raising=False)


def _fake_graphql(monkeypatch, *responses) -> _FakeGraphQL:
    fake = _FakeGraphQL(*responses)
    monkeypatch.setattr(linear_module, "_graphql", fake)
    return fake


def _team_payload(active_cycle: dict | None = None, name: str = "Yeaboi", team_id: str = "team-uuid-1") -> dict:
    return {
        "teams": {
            "nodes": [
                {
                    "id": team_id,
                    "key": "YEA",
                    "name": name,
                    "activeCycle": active_cycle,
                }
            ]
        }
    }


def _backlog_payload(count: int, has_next: bool = False) -> dict:
    return {
        "issues": {
            "nodes": [{"id": f"issue-{i}"} for i in range(count)],
            "pageInfo": {"hasNextPage": has_next},
        }
    }


def _cycle(number: int, issues: list[dict], name: str = "") -> dict:
    return {
        "number": number,
        "name": name,
        "startsAt": "2026-07-01T00:00:00.000Z",
        "endsAt": "2026-07-14T00:00:00.000Z",
        "issues": {"nodes": issues},
    }


def _issue(estimate, assignee_id: str | None = "user-1") -> dict:
    assignee = {"id": assignee_id, "name": "Dev One"} if assignee_id else None
    return {"estimate": estimate, "assignee": assignee}


def _cycles_payload(*cycles: dict) -> dict:
    return {"cycles": {"nodes": list(cycles)}}


# ---------------------------------------------------------------------------
# _resolve_team_key
# ---------------------------------------------------------------------------


class TestResolveTeamKey:
    def test_explicit_key_wins_over_env(self, configured):
        assert _resolve_team_key("OTHER") == "OTHER"

    def test_falls_back_to_env(self, configured):
        assert _resolve_team_key("") == "YEA"

    def test_uppercases_because_the_graphql_filter_is_case_sensitive(self, configured):
        assert _resolve_team_key("yea") == "YEA"

    def test_returns_empty_when_nothing_is_set(self, unconfigured):
        assert _resolve_team_key("") == ""

    def test_whitespace_only_falls_back_to_env(self, configured):
        assert _resolve_team_key("   ") == "YEA"


# ---------------------------------------------------------------------------
# _cycle_label
# ---------------------------------------------------------------------------


class TestCycleLabel:
    def test_uses_the_name_when_present(self):
        assert _cycle_label({"number": 7, "name": "Hardening"}) == "Hardening"

    def test_falls_back_to_the_number_when_unnamed(self):
        assert _cycle_label({"number": 7, "name": None}) == "Cycle 7"

    def test_falls_back_when_the_name_is_blank(self):
        assert _cycle_label({"number": 7, "name": "  "}) == "Cycle 7"


# ---------------------------------------------------------------------------
# _completed_points
# ---------------------------------------------------------------------------


class TestCompletedPoints:
    def test_sums_estimates_and_collects_assignees(self):
        cycle = _cycle(1, [_issue(3, "a"), _issue(5, "b"), _issue(2, "a")])
        points, assignees = _completed_points(cycle)
        assert points == 10.0
        assert assignees == {"a", "b"}

    def test_unestimated_issues_contribute_zero(self):
        cycle = _cycle(1, [_issue(3, "a"), _issue(None, "b")])
        points, assignees = _completed_points(cycle)
        assert points == 3.0
        assert assignees == {"a", "b"}

    def test_unparseable_estimate_is_skipped_not_fatal(self):
        cycle = _cycle(1, [_issue("nonsense", "a"), _issue(4, "a")])
        points, _ = _completed_points(cycle)
        assert points == 4.0

    def test_unassigned_issue_adds_no_assignee(self):
        cycle = _cycle(1, [_issue(3, None)])
        points, assignees = _completed_points(cycle)
        assert points == 3.0
        assert assignees == set()

    def test_empty_cycle_is_zero(self):
        assert _completed_points(_cycle(1, [])) == (0.0, set())


# ---------------------------------------------------------------------------
# _linear_error_msg
# ---------------------------------------------------------------------------


class TestLinearErrorMsg:
    def test_401_names_the_env_var(self):
        assert "LINEAR_API_KEY" in _linear_error_msg(LinearAPIError(401, "bad key"))

    def test_403_mentions_permission(self):
        assert "permission denied" in _linear_error_msg(LinearAPIError(403, "nope"))

    def test_404_mentions_the_team_key(self):
        assert "team key" in _linear_error_msg(LinearAPIError(404, "no team"))

    def test_429_mentions_rate_limit(self):
        assert "rate limit" in _linear_error_msg(LinearAPIError(429, "slow down"))

    def test_unknown_status_falls_through_with_detail(self):
        msg = _linear_error_msg(LinearAPIError(500, "boom"))
        assert "500" in msg and "boom" in msg


# ---------------------------------------------------------------------------
# _graphql — the transport layer (urlopen is faked, no socket is opened)
# ---------------------------------------------------------------------------


def _fake_urlopen(monkeypatch, body: str):
    """Make urlopen return ``body`` and capture the Request it was handed."""
    captured: dict = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return body.encode("utf-8")

    def _urlopen(request, timeout=None):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(linear_module.urllib.request, "urlopen", _urlopen)
    return captured


class TestGraphql:
    def test_returns_the_data_object(self, configured, monkeypatch):
        _fake_urlopen(monkeypatch, json.dumps({"data": {"teams": {"nodes": []}}}))
        assert _graphql("query {}", {}) == {"teams": {"nodes": []}}

    def test_sends_the_key_without_a_bearer_prefix(self, configured, monkeypatch):
        captured = _fake_urlopen(monkeypatch, json.dumps({"data": {}}))
        _graphql("query {}", {"key": "YEA"})
        header = captured["request"].get_header("Authorization")
        assert header == "fake-linear-key-for-tests"
        assert not header.lower().startswith("bearer")

    def test_posts_the_query_and_variables_as_json(self, configured, monkeypatch):
        captured = _fake_urlopen(monkeypatch, json.dumps({"data": {}}))
        _graphql("query Thing {}", {"key": "YEA"})
        sent = json.loads(captured["request"].data.decode("utf-8"))
        assert sent == {"query": "query Thing {}", "variables": {"key": "YEA"}}

    def test_raises_401_when_the_key_is_missing(self, unconfigured, monkeypatch):
        # Guard: the module must never issue an unauthenticated request.
        called = MagicMock()
        monkeypatch.setattr(linear_module.urllib.request, "urlopen", called)
        with pytest.raises(LinearAPIError) as excinfo:
            _graphql("query {}", {})
        assert excinfo.value.status == 401
        called.assert_not_called()

    def test_http_error_carries_the_status_code(self, configured, monkeypatch):
        def _urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                linear_module._API_URL, 500, "Server Error", {}, io.BytesIO(b"upstream exploded")
            )

        monkeypatch.setattr(linear_module.urllib.request, "urlopen", _urlopen)
        with pytest.raises(LinearAPIError) as excinfo:
            _graphql("query {}", {})
        assert excinfo.value.status == 500
        assert "upstream exploded" in excinfo.value.message

    def test_network_error_is_status_zero(self, configured, monkeypatch):
        def _urlopen(request, timeout=None):
            raise urllib.error.URLError("no route to host")

        monkeypatch.setattr(linear_module.urllib.request, "urlopen", _urlopen)
        with pytest.raises(LinearAPIError) as excinfo:
            _graphql("query {}", {})
        assert excinfo.value.status == 0

    def test_non_json_response_is_reported_not_crashed(self, configured, monkeypatch):
        _fake_urlopen(monkeypatch, "<html>maintenance</html>")
        with pytest.raises(LinearAPIError) as excinfo:
            _graphql("query {}", {})
        assert "non-JSON" in excinfo.value.message

    def test_graphql_auth_error_in_a_200_body_becomes_401(self, configured, monkeypatch):
        # The case a plain status check would miss: HTTP 200, auth failure inside.
        _fake_urlopen(
            monkeypatch,
            json.dumps(
                {"errors": [{"message": "Authentication required", "extensions": {"code": "AUTHENTICATION_ERROR"}}]}
            ),
        )
        with pytest.raises(LinearAPIError) as excinfo:
            _graphql("query {}", {})
        assert excinfo.value.status == 401

    def test_graphql_rate_limit_becomes_429(self, configured, monkeypatch):
        _fake_urlopen(
            monkeypatch,
            json.dumps({"errors": [{"message": "too many", "extensions": {"code": "RATELIMITED"}}]}),
        )
        with pytest.raises(LinearAPIError) as excinfo:
            _graphql("query {}", {})
        assert excinfo.value.status == 429

    def test_unknown_graphql_error_code_becomes_400(self, configured, monkeypatch):
        _fake_urlopen(monkeypatch, json.dumps({"errors": [{"message": "bad field"}]}))
        with pytest.raises(LinearAPIError) as excinfo:
            _graphql("query {}", {})
        assert excinfo.value.status == 400
        assert "bad field" in excinfo.value.message


# ---------------------------------------------------------------------------
# linear_read_board
# ---------------------------------------------------------------------------


class TestReadBoard:
    def test_happy_path_reports_cycle_backlog_and_velocity(self, configured, monkeypatch):
        _fake_graphql(
            monkeypatch,
            _team_payload(
                {"number": 12, "name": "", "startsAt": "2026-08-01T00:00:00.000Z", "endsAt": "2026-08-14T00:00:00.000Z"}
            ),
            _backlog_payload(7),
            _cycles_payload(_cycle(10, [_issue(5, "a"), _issue(5, "b")]), _cycle(11, [_issue(10, "a")])),
        )
        result = linear_read_board.invoke({})
        assert "Team: Yeaboi (YEA)" in result
        assert "Active cycle: Cycle 12" in result
        assert "Start: 2026-08-01" in result
        assert "End: 2026-08-14" in result
        assert "Backlog: 7 issues" in result
        assert "Avg velocity (last 2 cycles): 10.0 pts" in result

    def test_backlog_at_the_ceiling_is_marked_as_a_lower_bound(self, configured, monkeypatch):
        _fake_graphql(
            monkeypatch,
            _team_payload({"number": 1, "name": "", "startsAt": "", "endsAt": ""}),
            _backlog_payload(250, has_next=True),
            _cycles_payload(),
        )
        result = linear_read_board.invoke({})
        assert "Backlog: 250+ issues" in result

    def test_no_active_cycle_says_so(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, _team_payload(None), _backlog_payload(3), _cycles_payload())
        result = linear_read_board.invoke({})
        assert "Active cycle: none" in result

    def test_no_closed_cycles_says_so(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, _team_payload(None), _backlog_payload(0), _cycles_payload())
        assert "Avg velocity: no closed cycles found" in linear_read_board.invoke({})

    def test_missing_credentials_returns_the_config_message(self, unconfigured):
        assert linear_read_board.invoke({}) == _MISSING_CONFIG_MSG

    def test_missing_team_key_returns_the_team_message(self, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "fake-linear-key-for-tests")
        monkeypatch.delenv("LINEAR_TEAM_KEY", raising=False)
        assert linear_read_board.invoke({}) == _MISSING_TEAM_MSG

    def test_unknown_team_reports_a_not_found_error(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, {"teams": {"nodes": []}})
        result = linear_read_board.invoke({"team_key": "NOPE"})
        assert result.startswith("Error:")
        assert "team key" in result

    def test_api_error_is_rendered_not_raised(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, LinearAPIError(401, "bad key"))
        assert linear_read_board.invoke({}) == _linear_error_msg(LinearAPIError(401, "bad key"))

    def test_unexpected_error_is_caught(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, RuntimeError("kaboom"))
        assert linear_read_board.invoke({}) == "Error: kaboom"


# ---------------------------------------------------------------------------
# linear_fetch_velocity
# ---------------------------------------------------------------------------


class TestFetchVelocity:
    def test_happy_path_matches_the_jira_json_shape(self, configured, monkeypatch):
        _fake_graphql(
            monkeypatch,
            _cycles_payload(
                _cycle(10, [_issue(5, "a"), _issue(5, "b")]),
                _cycle(11, [_issue(10, "a"), _issue(10, "b")]),
            ),
        )
        data = json.loads(linear_fetch_velocity.invoke({}))
        # 10 and 20 points over two cycles → 15 avg, two unique assignees.
        assert data == {"team_velocity": 15, "jira_team_size": 2, "per_dev_velocity": 7.5}

    def test_zero_velocity_still_reports_team_size(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, _cycles_payload(_cycle(10, [_issue(None, "a"), _issue(None, "b")])))
        data = json.loads(linear_fetch_velocity.invoke({}))
        assert data["team_velocity"] == 0
        assert data["jira_team_size"] == 2
        assert "velocity_error" in data

    def test_team_size_is_never_zero(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, _cycles_payload(_cycle(10, [_issue(6, None)])))
        data = json.loads(linear_fetch_velocity.invoke({}))
        assert data["jira_team_size"] == 1
        assert data["per_dev_velocity"] == 6.0

    def test_no_closed_cycles_is_an_error_string(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, _cycles_payload())
        result = linear_fetch_velocity.invoke({})
        assert result.startswith("Error: No closed cycles")

    def test_missing_credentials_returns_the_config_message(self, unconfigured):
        assert linear_fetch_velocity.invoke({}) == _MISSING_CONFIG_MSG

    def test_missing_team_key_returns_the_team_message(self, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "fake-linear-key-for-tests")
        monkeypatch.delenv("LINEAR_TEAM_KEY", raising=False)
        assert linear_fetch_velocity.invoke({}) == _MISSING_TEAM_MSG

    def test_api_error_is_rendered_not_raised(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, LinearAPIError(429, "slow down"))
        assert "rate limit" in linear_fetch_velocity.invoke({})

    def test_unexpected_error_is_caught(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, RuntimeError("kaboom"))
        assert linear_fetch_velocity.invoke({}) == "Error: kaboom"


# ---------------------------------------------------------------------------
# linear_fetch_active_cycle
# ---------------------------------------------------------------------------


class TestFetchActiveCycle:
    def test_happy_path_matches_the_jira_json_shape(self, configured, monkeypatch):
        _fake_graphql(
            monkeypatch,
            _team_payload({"number": 42, "name": "Hardening", "startsAt": "2026-08-01T00:00:00.000Z"}),
        )
        data = json.loads(linear_fetch_active_cycle.invoke({}))
        assert data == {"sprint_number": 42, "sprint_name": "Hardening", "start_date": "2026-08-01"}

    def test_unnamed_cycle_falls_back_to_its_number(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, _team_payload({"number": 42, "name": None, "startsAt": ""}))
        data = json.loads(linear_fetch_active_cycle.invoke({}))
        assert data["sprint_name"] == "Cycle 42"
        # No startsAt → the optional key is omitted rather than sent empty.
        assert "start_date" not in data

    def test_no_active_cycle_is_an_error_string(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, _team_payload(None))
        result = linear_fetch_active_cycle.invoke({})
        assert result.startswith("Error: No active cycle")

    def test_cycle_without_a_number_is_an_error_string(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, _team_payload({"number": None, "name": "Odd", "startsAt": ""}))
        assert "no cycle number" in linear_fetch_active_cycle.invoke({})

    def test_missing_credentials_returns_the_config_message(self, unconfigured):
        assert linear_fetch_active_cycle.invoke({}) == _MISSING_CONFIG_MSG

    def test_missing_team_key_returns_the_team_message(self, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "fake-linear-key-for-tests")
        monkeypatch.delenv("LINEAR_TEAM_KEY", raising=False)
        assert linear_fetch_active_cycle.invoke({}) == _MISSING_TEAM_MSG

    def test_api_error_is_rendered_not_raised(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, LinearAPIError(403, "nope"))
        assert "permission denied" in linear_fetch_active_cycle.invoke({})

    def test_unexpected_error_is_caught(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, RuntimeError("kaboom"))
        assert linear_fetch_active_cycle.invoke({}) == "Error: kaboom"


# ---------------------------------------------------------------------------
# linear_create_issue
# ---------------------------------------------------------------------------


def _created(identifier: str = "YEA-42", url: str = "https://linear.app/yeaboi/issue/YEA-42") -> dict:
    return {
        "issueCreate": {"success": True, "issue": {"id": "uuid-1", "identifier": identifier, "title": "t", "url": url}}
    }


class TestCreateIssue:
    def test_happy_path_reports_identifier_and_url(self, configured, monkeypatch):
        fake = _fake_graphql(monkeypatch, _team_payload(None), _created())
        result = linear_create_issue.invoke({"title": "Add login rate limiting"})
        assert "Created Linear issue: YEA-42 — Add login rate limiting" in result
        assert "Team: Yeaboi (YEA)" in result
        assert "https://linear.app/yeaboi/issue/YEA-42" in result
        # The mutation must carry the team's UUID, not its key.
        assert fake.calls[1][1]["input"]["teamId"] == "team-uuid-1"

    def test_optional_fields_are_only_sent_when_given(self, configured, monkeypatch):
        fake = _fake_graphql(monkeypatch, _team_payload(None), _created())
        linear_create_issue.invoke({"title": "Bare"})
        payload = fake.calls[1][1]["input"]
        assert set(payload) == {"teamId", "title"}

    def test_estimate_parent_and_description_are_forwarded(self, configured, monkeypatch):
        fake = _fake_graphql(monkeypatch, _team_payload(None), _created())
        result = linear_create_issue.invoke(
            {
                "title": "Add login rate limiting",
                "description": "As a user...",
                "estimate": 5,
                "parent_id": "parent-uuid",
                "internal_id": "story-3",
            }
        )
        payload = fake.calls[1][1]["input"]
        assert payload["estimate"] == 5
        assert payload["parentId"] == "parent-uuid"
        assert payload["description"] == "As a user..."
        assert "Estimate: 5 pts" in result
        assert "Mapping: story-3 → YEA-42" in result

    def test_blank_title_is_rejected_before_any_request(self, configured, monkeypatch):
        fake = _fake_graphql(monkeypatch)
        assert linear_create_issue.invoke({"title": "   "}) == "Error: Provide a title for the issue."
        assert fake.calls == []

    def test_declined_mutation_is_reported(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, _team_payload(None), {"issueCreate": {"success": False, "issue": None}})
        result = linear_create_issue.invoke({"title": "Nope"})
        assert result.startswith("Error: Linear declined")

    def test_missing_credentials_returns_the_config_message(self, unconfigured):
        assert linear_create_issue.invoke({"title": "x"}) == _MISSING_CONFIG_MSG

    def test_missing_team_key_returns_the_team_message(self, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "fake-linear-key-for-tests")
        monkeypatch.delenv("LINEAR_TEAM_KEY", raising=False)
        assert linear_create_issue.invoke({"title": "x"}) == _MISSING_TEAM_MSG

    def test_api_error_is_rendered_not_raised(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, LinearAPIError(403, "nope"))
        assert "permission denied" in linear_create_issue.invoke({"title": "x"})

    def test_unexpected_error_is_caught(self, configured, monkeypatch):
        _fake_graphql(monkeypatch, RuntimeError("kaboom"))
        assert linear_create_issue.invoke({"title": "x"}) == "Error: kaboom"

    def test_docstring_carries_the_confirmation_guard(self):
        # The ReAct loop reads this docstring via bind_tools — the guard has to
        # be in the text, not only in TOOL_RISK.
        assert "only call this after the user has explicitly confirmed" in linear_create_issue.description.lower()


# ---------------------------------------------------------------------------
# Registration and risk classification
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_all_four_tools_are_registered(self):
        names = {t.name for t in get_tools()}
        assert {
            "linear_read_board",
            "linear_fetch_velocity",
            "linear_fetch_active_cycle",
            "linear_create_issue",
        } <= names

    def test_read_tools_are_classified_read(self):
        for name in ("linear_read_board", "linear_fetch_velocity", "linear_fetch_active_cycle"):
            assert TOOL_RISK[name] is ToolRisk.READ

    def test_the_write_tool_is_classified_write(self):
        assert TOOL_RISK["linear_create_issue"] is ToolRisk.WRITE


# ---------------------------------------------------------------------------
# Secret masking
# ---------------------------------------------------------------------------


# These tests need strings that LOOK like real Linear keys, because that shape is
# exactly what redaction's pattern layer matches on. They are assembled from parts
# rather than written out whole so that no complete key-shaped literal is ever
# committed — the repo's own secret scanner stays honest instead of allow-listed.
_API_PREFIX = "lin_api_"
_OAUTH_PREFIX = "lin_oauth_"
_NOT_A_KEY = "EXAMPLENOTAREALKEY000000"


class TestMasking:
    def test_the_configured_key_is_redacted_from_log_text(self, monkeypatch):
        # Value layer: whatever is in LINEAR_API_KEY, whatever shape it has.
        key = _API_PREFIX + _NOT_A_KEY
        monkeypatch.setenv("LINEAR_API_KEY", key)
        redacted = redact(f"Linear call failed: Authorization: {key}")
        assert key not in redacted
        assert "[REDACTED]" in redacted

    def test_a_pasted_key_is_redacted_even_when_it_is_not_ours(self, monkeypatch):
        # Pattern layer: a key echoed back in an API error body never passed
        # through our env, so value matching alone would miss it.
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        key = _API_PREFIX + _NOT_A_KEY
        redacted = redact(f"user pasted {key} by mistake")
        assert key not in redacted
        assert "[REDACTED]" in redacted

    def test_oauth_shaped_linear_tokens_are_also_redacted(self, monkeypatch):
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        token = _OAUTH_PREFIX + _NOT_A_KEY
        assert token not in redact(f"token {token}")
