"""Tests for Niko's tool surface (niko/tools.py).

Two properties carry the weight. The surface is **read-only** — a write tool
appearing here is a guardrail that stopped existing, so the test asserts the
inventory by name rather than by count. And a tool **never raises**: a failure
has to reach the model as an observation, because a raised exception ends the
turn with a traceback instead of an answer.
"""

from __future__ import annotations

import json

import pytest

from yeaboi.niko import tools as niko_tools

#: Verbs that would mean Niko can change something. Substring match on purpose:
#: `create_card`, `plan_publish` and `standup_config_set` all get caught.
_MUTATING = ("create", "update", "delete", "set_", "_set", "write", "publish", "sync", "apply", "run", "scan", "add")


class TestTheSurfaceIsReadOnly:
    def test_no_tool_name_reads_as_a_mutation(self):
        offenders = [name for name in niko_tools.TOOLS_BY_NAME if any(verb in name for verb in _MUTATING)]
        assert offenders == [], f"Niko's tools must be read-only; these look like writes: {offenders}"

    def test_navigate_is_the_only_tool_with_an_effect(self):
        assert niko_tools.NAVIGATE_TOOL == "navigate"
        assert niko_tools.NAVIGATE_TOOL in niko_tools.TOOLS_BY_NAME

    def test_every_tool_is_registered_exactly_once(self):
        names = [t.name for t in niko_tools.NIKO_TOOLS]
        assert len(names) == len(set(names))
        assert set(names) == set(niko_tools.TOOLS_BY_NAME)

    def test_every_tool_documents_itself_for_bind_tools(self):
        # bind_tools sends the docstring as the tool description; a blank one
        # leaves the model guessing what the tool is for.
        undocumented = [t.name for t in niko_tools.NIKO_TOOLS if not (t.description or "").strip()]
        assert undocumented == []


class TestGuard:
    def test_a_raising_read_becomes_an_observation(self):
        def boom():
            raise RuntimeError("no saved sessions found")

        assert niko_tools._guard("x", boom) == {"error": "no saved sessions found"}

    def test_an_exception_with_no_message_still_names_itself(self):
        def boom():
            raise KeyError()

        assert niko_tools._guard("x", boom)["error"]

    def test_results_come_back_json_able(self):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Row:
            n: int = 1

        result = niko_tools._guard("x", lambda: {"rows": (Row(),)})
        assert json.dumps(result)


class TestCall:
    def test_unknown_tool_is_an_observation_not_a_raise(self):
        assert niko_tools.call("nope", {}) == {"error": "Unknown tool: nope"}

    def test_an_invented_argument_reaches_the_model_as_text(self):
        # The model can send an arg the tool does not take; that must come back
        # as an observation, not as a TypeError that ends the turn.
        assert "wrong arguments" in niko_tools.call("llm_usage", {"unexpected": 1})["error"]

    def test_a_missing_required_argument_reaches_the_model_as_text(self):
        assert "wrong arguments" in niko_tools.call("navigate", {})["error"]

    def test_none_arguments_are_treated_as_empty(self):
        assert niko_tools.call("navigate", None)["error"]


class TestNavigate:
    def test_accepts_a_route_the_manifest_serves(self):
        assert niko_tools.call("navigate", {"route": "/agents/usage"}) == {"route": "/agents/usage"}

    def test_refuses_an_invented_route(self):
        result = niko_tools.call("navigate", {"route": "/team/teleport"})
        assert result["route"] == ""
        assert "list_routes" in result["error"]

    def test_refuses_everything_when_the_manifest_is_missing(self, monkeypatch):
        monkeypatch.setattr(niko_tools, "known_routes", lambda: [])
        assert niko_tools.call("navigate", {"route": "/home"})["route"] == ""


class TestKnownRoutes:
    def test_reads_the_committed_manifest(self):
        paths = {row["path"] for row in niko_tools.known_routes()}
        assert {"/home", "/agents/usage", "/team/retro"} <= paths

    def test_falls_back_to_the_repo_copy_when_unpackaged(self):
        # The wheel ships a copy under yeaboi/data/; a source checkout has none
        # and must still resolve. If this ever fails, `navigate` is dead in dev.
        assert not niko_tools._PACKAGED_MANIFEST.exists() or niko_tools._REPO_MANIFEST.exists()
        assert niko_tools.known_routes()

    def test_a_missing_manifest_is_empty_not_an_exception(self, monkeypatch, tmp_path):
        monkeypatch.setattr(niko_tools, "_PACKAGED_MANIFEST", tmp_path / "a.json")
        monkeypatch.setattr(niko_tools, "_REPO_MANIFEST", tmp_path / "b.json")
        assert niko_tools.known_routes() == []

    def test_an_unreadable_manifest_is_empty_not_an_exception(self, monkeypatch, tmp_path):
        broken = tmp_path / "a.json"
        broken.write_text("{{not json")
        monkeypatch.setattr(niko_tools, "_PACKAGED_MANIFEST", broken)
        assert niko_tools.known_routes() == []


class TestReadsReachTheRealHelpers:
    """Each tool must call the same helper the MCP tool calls — not a copy."""

    @pytest.mark.parametrize(
        ("tool_name", "module", "helper"),
        [
            ("list_sessions", "yeaboi.mcp.tools_sessions", "_list_sessions"),
            ("get_session", "yeaboi.mcp.tools_sessions", "_get_session"),
            ("llm_usage", "yeaboi.mcp.tools_sessions", "_usage_get"),
            ("standup_history", "yeaboi.mcp.tools_standup", "_standup_history"),
            ("reporting_history", "yeaboi.mcp.tools_reporting", "_reporting_history"),
            ("retro_history", "yeaboi.mcp.tools_retro", "_retro_history"),
            ("poker_history", "yeaboi.mcp.tools_poker", "_poker_history"),
            ("team_roster", "yeaboi.mcp.tools_team", "_team_roster"),
            ("team_profile", "yeaboi.mcp.tools_team", "_team_profile_get"),
            ("performance_roster", "yeaboi.mcp.tools_performance", "_roster"),
            ("ship_status", "yeaboi.mcp.tools_ship", "_status"),
            ("ship_history", "yeaboi.mcp.tools_ship", "_history"),
            ("agents_usage_history", "yeaboi.mcp.tools_agentwatch", "_usage_history"),
            ("agents_advisor_history", "yeaboi.mcp.tools_agentwatch", "_advisor_history"),
            ("agents_security_history", "yeaboi.mcp.tools_agentwatch", "_security_history"),
            ("ceremonies_list", "yeaboi.mcp.tools_ceremonies", "_list"),
            ("ceremonies_history", "yeaboi.mcp.tools_ceremonies", "_history"),
            ("provenance_audit", "yeaboi.mcp.tools_provenance", "_audit"),
            ("provenance_trace", "yeaboi.mcp.tools_provenance", "_trace"),
        ],
    )
    def test_tool_delegates_to_the_shared_helper(self, monkeypatch, tool_name, module, helper):
        import importlib

        target = importlib.import_module(module)
        seen = {}

        def _spy(*a, **k):
            seen["called"] = (a, k)
            return {"ok": 1}

        monkeypatch.setattr(target, helper, _spy)
        arguments = {"entity_id": "e1"} if tool_name == "provenance_trace" else {}
        # `list_sessions` wraps its helper's list in a key; the rest pass the
        # payload through. Either way the helper is what ran.
        result = niko_tools.call(tool_name, arguments)
        assert "called" in seen, f"{tool_name} does not reach {module}.{helper}"
        assert "error" not in result

    def test_a_helper_that_raises_is_reported_not_propagated(self, monkeypatch):
        from yeaboi.mcp import tools_ship

        def boom():
            raise ValueError("No saved sessions found")

        monkeypatch.setattr(tools_ship, "_status", boom)
        assert niko_tools.call("ship_status", {}) == {"error": "No saved sessions found"}


class TestCapabilities:
    def test_list_capabilities_serves_the_real_cards(self):
        result = niko_tools.call("list_capabilities", {})
        assert {"categories", "modes", "agents", "intake"} <= set(result)
        assert {"solo", "team", "agents"} <= {row["key"] for row in result["categories"]}

    def test_list_routes_names_the_capability_each_screen_belongs_to(self):
        rows = {row["path"]: row for row in niko_tools.call("list_routes", {})["routes"]}
        assert rows["/agents/usage"]["capability"] == "agent-usage"
