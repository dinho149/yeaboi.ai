"""In-memory MCP dispatch — the real FastMCP app behind the tool routes."""

from __future__ import annotations

import pytest

pytest.importorskip("mcp", reason="mcp extra not installed")

from yeaboi.app.dispatch import DispatcherUnavailableError, McpDispatcher  # noqa: E402
from yeaboi.app.events import EventBus  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
    return db


@pytest.fixture(scope="module")
def dispatcher():
    """One live dispatcher for the module — startup imports every tool module."""
    d = McpDispatcher(EventBus())
    d.start()
    yield d
    d.stop()


class TestLifecycle:
    def test_started_dispatcher_serves_the_inventory(self, dispatcher):
        assert dispatcher.available
        names = dispatcher.tool_names()
        assert "sessions_list" in names
        assert len(names) > 40

    def test_unstarted_dispatcher_raises_unavailable(self):
        with pytest.raises(DispatcherUnavailableError):
            McpDispatcher().call_tool("sessions_list")

    def test_failed_start_surfaces_the_error(self, monkeypatch):
        d = McpDispatcher()

        def boom():
            raise RuntimeError("no tools today")

        monkeypatch.setattr("yeaboi.mcp.server.create_app", boom)
        with pytest.raises(DispatcherUnavailableError, match="no tools today"):
            d.start(timeout=10)
        assert not d.available
        d.stop()  # safe on a failed start


class TestCallTool:
    def test_call_returns_the_envelope(self, dispatcher, tmp_db):
        payload = dispatcher.call_tool("sessions_list")
        assert payload["ok"] is True
        assert set(payload) == {"ok", "llm_mode", "warnings", "data"}

    def test_unknown_tool_is_a_value_error(self, dispatcher):
        with pytest.raises(ValueError, match="unknown tool"):
            dispatcher.call_tool("not_a_tool")
