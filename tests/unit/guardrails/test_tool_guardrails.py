"""Tests for tool guardrails — tool activity display in the REPL.

Tests the _display_tool_activity helper that shows which tools the agent
called during a graph invocation. This is the "log and display medium-risk
tool outputs" guardrail from Phase 11.
"""

from io import StringIO

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from rich.console import Console

from yeaboi.repl import _display_tool_activity


def _make_console() -> tuple[Console, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120)
    return console, buf


class TestDisplayToolActivity:
    def test_no_tool_messages_prints_nothing(self):
        console, buf = _make_console()
        old = [HumanMessage(content="hi")]
        new = [HumanMessage(content="hi"), AIMessage(content="hello")]
        _display_tool_activity(console, old, new)
        assert buf.getvalue() == ""

    def test_single_tool_message_displayed(self):
        console, buf = _make_console()
        old = [HumanMessage(content="hi")]
        new = [
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[{"name": "github_read_repo", "args": {}, "id": "1"}]),
            ToolMessage(content="Repository: my-repo\nFiles: 42", tool_call_id="1", name="github_read_repo"),
            AIMessage(content="I found your repo."),
        ]
        _display_tool_activity(console, old, new)
        output = buf.getvalue()
        assert "github_read_repo" in output

    def test_multiple_tool_messages_all_displayed(self):
        console, buf = _make_console()
        old = [HumanMessage(content="hi")]
        new = [
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[{"name": "github_read_repo", "args": {}, "id": "1"}]),
            ToolMessage(content="repo info", tool_call_id="1", name="github_read_repo"),
            AIMessage(content="", tool_calls=[{"name": "github_read_file", "args": {}, "id": "2"}]),
            ToolMessage(content="file contents", tool_call_id="2", name="github_read_file"),
            AIMessage(content="Done."),
        ]
        _display_tool_activity(console, old, new)
        output = buf.getvalue()
        assert "github_read_repo" in output
        assert "github_read_file" in output

    def test_error_tool_message_shows_failed(self):
        console, buf = _make_console()
        old = [HumanMessage(content="hi")]
        new = [
            HumanMessage(content="hi"),
            ToolMessage(content="Error: repository not found", tool_call_id="1", name="github_read_repo"),
            AIMessage(content="Sorry, I couldn't find that repo."),
        ]
        _display_tool_activity(console, old, new)
        output = buf.getvalue()
        assert "github_read_repo" in output
        assert "failed" in output

    def test_long_content_truncated(self):
        console, buf = _make_console()
        old = []
        long_content = "x" * 200
        new = [
            ToolMessage(content=long_content, tool_call_id="1", name="read_codebase"),
            AIMessage(content="Done."),
        ]
        _display_tool_activity(console, old, new)
        output = buf.getvalue()
        assert "read_codebase" in output
        # The full 200-char content should not appear — it's truncated to ~80
        assert "x" * 200 not in output

    def test_empty_messages_no_crash(self):
        console, buf = _make_console()
        _display_tool_activity(console, [], [])
        assert buf.getvalue() == ""

    def test_only_old_messages_no_output(self):
        """When new_messages is same length as old, nothing to display."""
        console, buf = _make_console()
        msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
        _display_tool_activity(console, msgs, msgs)
        assert buf.getvalue() == ""
