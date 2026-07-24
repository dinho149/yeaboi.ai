"""Integration tests for the ReAct (Reason + Act) loop in the agent graph.

# See docs: "The ReAct Loop" — Thought → Action → Observation pattern
# See docs: "Guardrails" — human-in-the-loop pattern (human_review node)

These tests cover the critical LLM → tool → LLM feedback loop that has no
dedicated test elsewhere:

- ``TestReActSingleTool``  — one tool call, result fed back, final answer
- ``TestReActMultipleTools`` — parallel (same LLM response) and sequential
  (multiple LLM turns) multi-tool patterns
- ``TestReActToolError``   — tool raises RuntimeError, ToolNode catches it,
  error ToolMessage fed to LLM, no crash, no infinite loop
- ``TestHumanReview``      — high-risk tools (jira_create_epic) intercepted
  before execution; tool runs only after the user types "yes"

All LLM calls are mocked — no real API calls.
"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from yeaboi.agent.graph import create_graph
from yeaboi.agent.state import (
    TOTAL_QUESTIONS,
    AcceptanceCriterion,
    Feature,
    Priority,
    ProjectAnalysis,
    QuestionnaireState,
    Sprint,
    StoryPointValue,
    Task,
    UserStory,
)

# ---------------------------------------------------------------------------
# Test tools
# ---------------------------------------------------------------------------


@tool
def echo_tool(text: str) -> str:
    """Echo the input text back."""
    return f"echoed: {text}"


@tool
def counter_tool(n: int) -> str:
    """Count to n and return the result as a string."""
    return f"counted to {n}"


@tool
def failing_tool(reason: str) -> str:
    """Always raises a RuntimeError — used to test error-handling in ToolNode."""
    raise RuntimeError(f"tool failed: {reason}")


@tool
def jira_create_epic(summary: str, description: str = "") -> str:
    """Create an epic in Jira. Mirrors the name in _HIGH_RISK_TOOLS so the
    human_review guardrail intercepts it during tests."""
    return f"Created epic: {summary}"


# ---------------------------------------------------------------------------
# State and mock helpers
# ---------------------------------------------------------------------------


def _make_completed_questionnaire() -> QuestionnaireState:
    """Return a QuestionnaireState with all 26 questions answered and completed=True."""
    qs = QuestionnaireState(completed=True, current_question=TOTAL_QUESTIONS + 1)
    for i in range(1, TOTAL_QUESTIONS + 1):
        qs.answers[i] = f"Answer {i}"
    return qs


def _make_dummy_analysis() -> ProjectAnalysis:
    """Return a minimal but valid ProjectAnalysis."""
    return ProjectAnalysis(
        project_name="Test Project",
        project_description="A minimal test project",
        project_type="greenfield",
        goals=("Build the feature",),
        end_users=("developers",),
        target_state="Deployed",
        tech_stack=("Python",),
        integrations=(),
        constraints=(),
        sprint_length_weeks=2,
        target_sprints=3,
        risks=(),
        out_of_scope=(),
        assumptions=(),
    )


def _pipeline_complete_state() -> dict:
    """Return a ScrumState that passes all route_entry checks and routes to 'agent'.

    # See docs: "Architecture" — route_entry checks questionnaire → analysis →
    # epics → stories → tasks → sprints in order. Only when ALL are populated
    # does it route to the 'agent' (ReAct) node.
    #
    # Why all six fields?
    # route_entry is a six-way conditional: questionnaire → analyzer → epics →
    # stories → tasks → sprints → agent. Each gate stops routing to 'agent'
    # if its field is absent. Providing minimal but non-empty values for all
    # six fields lets ReAct tests bypass the pipeline and go straight to
    # the tool-calling loop.
    """
    return {
        "messages": [],
        "questionnaire": _make_completed_questionnaire(),
        "project_analysis": _make_dummy_analysis(),
        "features": [Feature(id="F1", title="Auth", description="Auth feature", priority=Priority.HIGH)],
        "stories": [
            UserStory(
                id="US-1",
                feature_id="F1",
                persona="developer",
                goal="do something",
                benefit="it helps",
                acceptance_criteria=(AcceptanceCriterion(given="a condition", when="an action", then="a result"),),
                story_points=StoryPointValue.THREE,
                priority=Priority.MEDIUM,
            )
        ],
        "tasks": [Task(id="T1", story_id="US-1", title="Do it", description="Implement it")],
        "sprints": [Sprint(id="SP1", name="Sprint 1", goal="Launch", capacity_points=20, story_ids=("US-1",))],
    }


def _make_llm_mock(*responses):
    """Return ``(mock_llm, bound)`` where ``bound.invoke()`` yields *responses* in order.

    # See docs: "Agentic Blueprint Reference" — bind_tools wires tools into the LLM
    #
    # make_call_model() calls ``get_llm().bind_tools(tools)`` lazily on its first
    # invocation, then stores the result in a closure variable (_bound_llm).
    # All subsequent LLM calls go through that stored bound object.
    #
    # Two mock objects are needed to mirror this two-step call:
    #   1. ``get_llm()`` → ``mock_llm``  (patched via monkeypatch)
    #   2. ``mock_llm.bind_tools(tools)`` → ``bound``  (stored by the closure)
    #
    # Responses accumulate on ``bound.invoke.side_effect`` — one entry consumed
    # per LLM call across the entire graph.invoke() run.
    """
    bound = MagicMock()
    bound.invoke.side_effect = list(responses)
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = bound
    return mock_llm, bound


def _tc(name: str, args: dict, call_id: str = "call_1") -> dict:
    """Build a ``tool_calls`` entry compatible with ``AIMessage.tool_calls``.

    # See docs: "The ReAct Loop" — tool_calls is the structured payload the
    # LLM returns when it wants to invoke a tool. ToolNode reads ``name`` and
    # ``args`` to dispatch to the correct function and pass the right arguments.
    """
    return {"id": call_id, "name": name, "args": args, "type": "tool_call"}


# ---------------------------------------------------------------------------
# Single-tool ReAct cycle
# ---------------------------------------------------------------------------


class TestReActSingleTool:
    """LLM requests one tool → ToolNode executes it → result fed back → final answer.

    # See docs: "The ReAct Loop" — Thought → Action → Observation
    #
    # Simplest ReAct cycle:
    #   agent call 1: LLM returns AIMessage(tool_calls=[echo_tool])
    #   tools node:   ToolNode runs echo_tool → ToolMessage("echoed: hello")
    #   agent call 2: LLM sees ToolMessage → returns final AIMessage
    """

    def test_tool_result_fed_back_to_llm(self, monkeypatch):
        """LLM returns tool_calls → ToolNode executes → result in state → LLM final answer."""
        first = AIMessage(content="", tool_calls=[_tc("echo_tool", {"text": "hello"})])
        final = AIMessage(content="Done: echoed: hello")
        mock_llm, bound = _make_llm_mock(first, final)
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = _pipeline_complete_state()
        state["messages"] = [HumanMessage(content="run echo")]
        result = create_graph(tools=[echo_tool]).invoke(state)

        # LLM called twice: once to produce tool_calls, once after seeing the result.
        assert bound.invoke.call_count == 2

        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert "echoed: hello" in tool_msgs[0].content

        # Final message is a plain AIMessage — no further tool calls.
        assert isinstance(result["messages"][-1], AIMessage)
        assert not getattr(result["messages"][-1], "tool_calls", None)

    def test_tool_call_receives_correct_args(self, monkeypatch):
        """ToolNode passes the args dict from tool_calls to the tool function verbatim."""
        first = AIMessage(content="", tool_calls=[_tc("echo_tool", {"text": "specific-value"})])
        final = AIMessage(content="done")
        mock_llm, bound = _make_llm_mock(first, final)
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = _pipeline_complete_state()
        state["messages"] = [HumanMessage(content="run")]
        result = create_graph(tools=[echo_tool]).invoke(state)

        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert "specific-value" in tool_msgs[0].content


# ---------------------------------------------------------------------------
# Multi-tool ReAct cycles
# ---------------------------------------------------------------------------


class TestReActMultipleTools:
    """LLM calls multiple tools — parallel (one response) and sequential (multi-turn).

    # See docs: "The ReAct Loop" — real agents often call multiple tools per run.
    #
    # Two patterns:
    # Parallel — one AIMessage carries two tool_calls; ToolNode executes both
    #            in the same node invocation and returns two ToolMessages.
    # Sequential — LLM calls tool_a, sees result, then calls tool_b on the
    #              next agent turn (three LLM calls total).
    """

    def test_parallel_tool_calls_both_execute(self, monkeypatch):
        """Two tool_calls in one AIMessage → both ToolMessages in state after ToolNode."""
        first = AIMessage(
            content="",
            tool_calls=[
                _tc("echo_tool", {"text": "first"}, "call_1"),
                _tc("counter_tool", {"n": 3}, "call_2"),
            ],
        )
        final = AIMessage(content="Got both results")
        mock_llm, bound = _make_llm_mock(first, final)
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = _pipeline_complete_state()
        state["messages"] = [HumanMessage(content="run both tools")]
        result = create_graph(tools=[echo_tool, counter_tool]).invoke(state)

        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 2

        combined = " ".join(m.content for m in tool_msgs)
        assert "echoed: first" in combined
        assert "counted to 3" in combined

    def test_sequential_tool_calls_accumulate_in_state(self, monkeypatch):
        """LLM calls tool_a, sees result, calls tool_b — two separate ReAct turns."""
        # Turn 1: agent returns tool_calls for echo_tool
        turn1 = AIMessage(content="", tool_calls=[_tc("echo_tool", {"text": "step1"})])
        # Turn 2: agent sees echo_tool result, requests counter_tool
        turn2 = AIMessage(content="", tool_calls=[_tc("counter_tool", {"n": 5})])
        # Turn 3: agent is done — final answer
        final = AIMessage(content="Done with both steps")
        mock_llm, bound = _make_llm_mock(turn1, turn2, final)
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = _pipeline_complete_state()
        state["messages"] = [HumanMessage(content="run tools sequentially")]
        result = create_graph(tools=[echo_tool, counter_tool]).invoke(state)

        # Three LLM calls: after turn1 tool result, after turn2 tool result, final.
        assert bound.invoke.call_count == 3

        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 2

        assert isinstance(result["messages"][-1], AIMessage)


# ---------------------------------------------------------------------------
# Tool error handling
# ---------------------------------------------------------------------------


class TestReActToolError:
    """Tool raises an error → ToolNode returns error ToolMessage → LLM responds gracefully.

    # See docs: "The ReAct Loop" — tool errors must not crash the graph.
    #
    # ToolNode's default ``handle_tool_errors`` catches exceptions and converts
    # them to ToolMessages so the LLM can acknowledge the failure. Without this,
    # any tool exception would bubble up and crash graph.invoke().
    #
    # Why "no infinite loop"?
    # After the error ToolMessage, the LLM sees a non-tool response path.
    # Because the mock returns a plain AIMessage (no tool_calls), should_continue
    # routes to END and the graph terminates normally.
    """

    def test_tool_error_does_not_crash_graph(self, monkeypatch):
        """RuntimeError in tool → error ToolMessage → LLM graceful reply → graph ends."""
        first = AIMessage(content="", tool_calls=[_tc("failing_tool", {"reason": "test"})])
        graceful = AIMessage(content="I encountered an error and will try differently.")
        mock_llm, bound = _make_llm_mock(first, graceful)
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = _pipeline_complete_state()
        state["messages"] = [HumanMessage(content="run failing tool")]
        result = create_graph(tools=[failing_tool]).invoke(state)

        messages = result["messages"]

        # ToolNode caught the error — one ToolMessage with error content.
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        err = tool_msgs[0].content.lower()
        assert "error" in err or "failed" in err

        # No infinite loop — the graph terminated after the graceful LLM reply.
        assert bound.invoke.call_count == 2

        # Final message is a plain AIMessage — graph completed cleanly.
        assert isinstance(messages[-1], AIMessage)
        assert not getattr(messages[-1], "tool_calls", None)

    def test_error_toolmessage_is_visible_to_llm_on_next_turn(self, monkeypatch):
        """The error ToolMessage is included in the context the LLM receives on retry."""
        first = AIMessage(content="", tool_calls=[_tc("failing_tool", {"reason": "bad input"})])
        graceful = AIMessage(content="Apologies — the tool failed.")
        mock_llm, bound = _make_llm_mock(first, graceful)
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = _pipeline_complete_state()
        state["messages"] = [HumanMessage(content="trigger error")]
        create_graph(tools=[failing_tool]).invoke(state)

        # The second LLM call receives the full message history including the
        # error ToolMessage.  call_args_list[1][0][0] is the messages arg.
        second_call_messages = bound.invoke.call_args_list[1][0][0]
        tool_msgs_seen = [m for m in second_call_messages if isinstance(m, ToolMessage)]
        assert len(tool_msgs_seen) == 1
        err = tool_msgs_seen[0].content.lower()
        assert "error" in err or "failed" in err


# ---------------------------------------------------------------------------
# Human-in-the-loop: high-risk tool confirmation
# ---------------------------------------------------------------------------


class TestHumanReview:
    """High-risk write tools pause for user confirmation before executing.

    # See docs: "Guardrails" — human-in-the-loop pattern
    #
    # should_continue routes to human_review when the LLM requests a tool
    # whose name is in _HIGH_RISK_TOOLS (jira_create_epic, confluence_create_page,
    # etc.). human_review replaces the tool_calls AIMessage with a plain
    # confirmation request (same message ID → add_messages *replaces* it, so
    # there are never two consecutive AIMessages). The graph then routes to END.
    #
    # On the next graph.invoke(), the user's "yes" is the last HumanMessage.
    # call_model re-generates the tool call. should_continue then detects the
    # confirmation pattern (prev_ai has no tool_calls, prev_human is affirmative)
    # and routes to "tools" directly, executing the write operation.
    """

    def test_high_risk_tool_pauses_before_executing(self, monkeypatch):
        """High-risk tool call → human_review intercepts → tool NOT executed."""
        first = AIMessage(
            content="",
            tool_calls=[_tc("jira_create_epic", {"summary": "Auth Epic", "description": "OAuth2"})],
        )
        mock_llm, bound = _make_llm_mock(first)
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = _pipeline_complete_state()
        state["messages"] = [HumanMessage(content="create a Jira epic")]
        result = create_graph(tools=[jira_create_epic]).invoke(state)

        messages = result["messages"]

        # Tool was NOT executed — no ToolMessage in state after invoke 1.
        assert not any(isinstance(m, ToolMessage) for m in messages)

        # Last message is the human_review replacement: plain AIMessage, no tool_calls.
        final = messages[-1]
        assert isinstance(final, AIMessage)
        assert not getattr(final, "tool_calls", None)

        # Confirmation text mentions the tool name or asks the user to confirm.
        assert "jira_create_epic" in final.content or "confirm" in final.content.lower()

    def test_yes_confirmation_executes_tool_on_next_invoke(self, monkeypatch):
        """After human_review pause, 'yes' → LLM re-intends tool → tool executes."""
        # Three LLM calls across two graph.invoke() calls (shared _bound_llm closure):
        #   invoke 1, call 1: LLM requests jira_create_epic → human_review intercepts
        #   invoke 2, call 2: LLM re-intends jira_create_epic (after seeing "yes")
        #   invoke 2, call 3: LLM final answer after tool result
        intent = AIMessage(content="", tool_calls=[_tc("jira_create_epic", {"summary": "Auth Epic"})])
        reintent = AIMessage(content="", tool_calls=[_tc("jira_create_epic", {"summary": "Auth Epic"})])
        final = AIMessage(content="Epic created successfully!")
        mock_llm, bound = _make_llm_mock(intent, reintent, final)
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        graph = create_graph(tools=[jira_create_epic])

        # Invoke 1 — human_review intercepts and pauses at confirmation request.
        base = _pipeline_complete_state()
        base["messages"] = [HumanMessage(content="create a Jira epic")]
        result1 = graph.invoke(base)

        # Invoke 2 — user confirms with "yes"; tool should now execute.
        state2 = dict(result1)
        state2["messages"] = [*result1["messages"], HumanMessage(content="yes")]
        result2 = graph.invoke(state2)

        messages2 = result2["messages"]

        # Tool executed this time — exactly one ToolMessage with the right args.
        tool_msgs = [m for m in messages2 if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert "Auth Epic" in tool_msgs[0].content

        # Pipeline completed cleanly — final message is a plain AIMessage.
        assert isinstance(messages2[-1], AIMessage)
        assert not getattr(messages2[-1], "tool_calls", None)

    def test_safe_tool_bypasses_human_review(self, monkeypatch):
        """Non-high-risk tool executes immediately — no confirmation pause."""
        first = AIMessage(content="", tool_calls=[_tc("echo_tool", {"text": "safe"})])
        final = AIMessage(content="done")
        mock_llm, bound = _make_llm_mock(first, final)
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        state = _pipeline_complete_state()
        state["messages"] = [HumanMessage(content="run safe tool")]
        result = create_graph(tools=[echo_tool]).invoke(state)

        # Tool executed immediately — ToolMessage present after a single invoke.
        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert "safe" in tool_msgs[0].content
