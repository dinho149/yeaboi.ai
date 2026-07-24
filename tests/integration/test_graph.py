"""Tests for the LangGraph graph factory function."""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph.state import CompiledStateGraph

from yeaboi.agent.graph import create_graph
from yeaboi.agent.state import (
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

# ── Realistic mock data ────────────────────────────────────────────
# Used by TestFeatureGenerationScenario and TestMultiTurnFeatureConversation
# to validate the full pipeline with content resembling real LLM output.

_TODO_APP_DESCRIPTION = (
    "Build a full-stack todo application with user authentication, "
    "CRUD operations for tasks, due dates, priority levels, and a "
    "responsive dashboard. Tech stack: React frontend, FastAPI backend, PostgreSQL."
)

_MOCK_FEATURE_RESPONSE = """\
Based on your project description, here is the initial feature decomposition:

**Feature 1: User Authentication & Authorization**
Priority: High
Scope: Registration, login, logout, password reset, JWT token management, role-based access.

**Feature 2: Task Management (CRUD)**
Priority: High
Scope: Create, read, update, and delete tasks. Assign due dates, \
set priority levels (Low, Medium, High, Critical), mark tasks as complete.

**Feature 3: Dashboard & Reporting**
Priority: Medium
Scope: Responsive dashboard showing task summary, overdue items, priority breakdown, and completion trends.

**Feature 4: Infrastructure & DevOps**
Priority: High
Scope: Project scaffolding (React + FastAPI + PostgreSQL), CI/CD pipeline, \
database migrations, deployment configuration.

Would you like me to proceed with breaking these features into user stories, \
or would you like to adjust any of the features first?"""

_MOCK_CLARIFICATION = (
    "Before I decompose this into features, I have a few clarifying questions:\n\n"
    "1. How many users do you expect to support initially?\n"
    "2. Do you need real-time updates (WebSockets) or is polling acceptable?\n"
    "3. Are there any existing authentication providers you'd like to integrate with (e.g. OAuth, SSO)?"
)

# ── Compilation tests ───────────────────────────────────────────────


class TestCreateGraphCompilation:
    """Tests that create_graph() compiles a valid graph."""

    def test_returns_compiled_state_graph(self):
        """create_graph() must return a CompiledStateGraph instance."""
        graph = create_graph()
        assert isinstance(graph, CompiledStateGraph)

    def test_has_agent_node(self):
        """The compiled graph must contain an 'agent' node."""
        graph = create_graph()
        # CompiledStateGraph exposes node names via .nodes
        assert "agent" in graph.nodes

    def test_has_project_intake_node(self):
        """The compiled graph must contain a 'project_intake' node."""
        graph = create_graph()
        assert "project_intake" in graph.nodes

    def test_has_project_analyzer_node(self):
        """The compiled graph must contain a 'project_analyzer' node."""
        graph = create_graph()
        assert "project_analyzer" in graph.nodes

    def test_has_feature_generator_node(self):
        """The compiled graph must contain a 'feature_generator' node."""
        graph = create_graph()
        assert "feature_generator" in graph.nodes

    def test_has_story_writer_node(self):
        """The compiled graph must contain a 'story_writer' node."""
        graph = create_graph()
        assert "story_writer" in graph.nodes

    def test_has_task_decomposer_node(self):
        """The compiled graph must contain a 'task_decomposer' node."""
        graph = create_graph()
        assert "task_decomposer" in graph.nodes

    def test_has_sprint_planner_node(self):
        """The compiled graph must contain a 'sprint_planner' node."""
        graph = create_graph()
        assert "sprint_planner" in graph.nodes

    def test_has_tools_node(self):
        """The compiled graph must contain a 'tools' node."""
        graph = create_graph()
        assert "tools" in graph.nodes

    def test_has_human_review_node(self):
        """The compiled graph must contain a 'human_review' node for high-risk write operations."""
        graph = create_graph()
        assert "human_review" in graph.nodes

    def test_compiles_with_default_tools(self):
        """Default tools=() should compile without errors."""
        graph = create_graph(tools=())
        assert isinstance(graph, CompiledStateGraph)

    def test_compiles_with_empty_list(self):
        """Explicit tools=[] should compile without errors."""
        graph = create_graph(tools=[])
        assert isinstance(graph, CompiledStateGraph)

    def test_compiles_with_checkpointer_none(self):
        """Explicit checkpointer=None should compile without errors."""
        graph = create_graph(checkpointer=None)
        assert isinstance(graph, CompiledStateGraph)


# ── Invocation tests ────────────────────────────────────────────────


class TestCreateGraphInvocation:
    """Tests that the compiled graph runs correctly (monkeypatched LLM).

    These tests pass a completed questionnaire + project_analysis + features
    so the graph routes to the "agent" node (the LLM path), not the intake,
    analyzer, or feature_generator nodes.
    """

    def _completed_questionnaire(self) -> QuestionnaireState:
        """Return a QuestionnaireState marked as completed."""
        return QuestionnaireState(completed=True)

    def _dummy_analysis(self) -> ProjectAnalysis:
        """Return a minimal ProjectAnalysis for routing past the analyzer."""
        return ProjectAnalysis(
            project_name="Test",
            project_description="Test project",
            project_type="greenfield",
            goals=(),
            end_users=(),
            target_state="",
            tech_stack=(),
            integrations=(),
            constraints=(),
            sprint_length_weeks=2,
            target_sprints=3,
            risks=(),
            out_of_scope=(),
            assumptions=(),
        )

    def test_invoke_returns_messages_with_ai_response(self, monkeypatch):
        """Invoking the graph with a HumanMessage should return an AIMessage."""
        fake_response = AIMessage(content="I'm your Scrum Master.")
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda: mock_llm)

        graph = create_graph()
        result = graph.invoke(
            {
                "messages": [HumanMessage(content="Hello")],
                "questionnaire": self._completed_questionnaire(),
                "project_analysis": self._dummy_analysis(),
                "features": _dummy_features(),
                "stories": _dummy_stories(),
                "tasks": _dummy_tasks(),
                "sprints": _dummy_sprints(),
            }
        )

        # The result should contain messages, with the last being the AI response
        assert len(result["messages"]) >= 2  # at least HumanMessage + AIMessage
        assert isinstance(result["messages"][-1], AIMessage)
        assert result["messages"][-1].content == "I'm your Scrum Master."

    def test_routes_to_end_when_no_tool_calls(self, monkeypatch):
        """When the LLM returns no tool_calls, the graph should stop (single invoke)."""
        fake_response = AIMessage(content="Here's your plan.")
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda: mock_llm)

        graph = create_graph()
        graph.invoke(
            {
                "messages": [HumanMessage(content="Plan my project")],
                "questionnaire": self._completed_questionnaire(),
                "project_analysis": self._dummy_analysis(),
                "features": _dummy_features(),
                "stories": _dummy_stories(),
                "tasks": _dummy_tasks(),
                "sprints": _dummy_sprints(),
            }
        )

        # The LLM should only be called once — no tool loop
        assert mock_llm.invoke.call_count == 1

    def test_preserves_conversation_history(self, monkeypatch):
        """Multi-message input should be preserved in the output state."""
        fake_response = AIMessage(content="Understood, continuing.")
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda: mock_llm)

        msg1 = HumanMessage(content="Build a todo app")
        msg2 = AIMessage(content="Tell me more.")
        msg3 = HumanMessage(content="It should have CRUD operations")

        graph = create_graph()
        result = graph.invoke(
            {
                "messages": [msg1, msg2, msg3],
                "questionnaire": self._completed_questionnaire(),
                "project_analysis": self._dummy_analysis(),
                "features": _dummy_features(),
                "stories": _dummy_stories(),
                "tasks": _dummy_tasks(),
                "sprints": _dummy_sprints(),
            }
        )

        # All 3 input messages + 1 AI response = 4 total
        assert len(result["messages"]) == 4
        assert result["messages"][0].content == "Build a todo app"
        assert result["messages"][1].content == "Tell me more."
        assert result["messages"][2].content == "It should have CRUD operations"
        assert result["messages"][3].content == "Understood, continuing."


# ── Import tests ────────────────────────────────────────────────────


class TestCreateGraphImports:
    """Verify create_graph is importable from the expected locations."""

    def test_importable_from_agent_package(self):
        """create_graph should be re-exported from yeaboi.agent."""
        from yeaboi.agent import create_graph as imported_fn

        assert imported_fn is create_graph

    def test_importable_from_graph_module(self):
        """create_graph should be importable directly from yeaboi.agent.graph."""
        from yeaboi.agent.graph import create_graph as imported_fn

        assert imported_fn is create_graph


# ── Feature generation scenario tests ─────────────────────────────────
# These tests validate the full pipeline with a realistic scenario:
# project description → system prompt injection → LLM → structured feature output.
# See docs: "The ReAct Loop" — this tests the Thought step with realistic content.


def _dummy_analysis() -> ProjectAnalysis:
    """Return a minimal ProjectAnalysis for routing past the analyzer in tests."""
    return ProjectAnalysis(
        project_name="Test",
        project_description="Test project",
        project_type="greenfield",
        goals=(),
        end_users=(),
        target_state="",
        tech_stack=(),
        integrations=(),
        constraints=(),
        sprint_length_weeks=2,
        target_sprints=3,
        risks=(),
        out_of_scope=(),
        assumptions=(),
    )


def _dummy_features() -> list[Feature]:
    """Return a minimal list of features for routing past the feature_generator in tests."""
    return [Feature(id="F1", title="Core Features", description="Core project features", priority=Priority.HIGH)]


def _dummy_stories() -> list[UserStory]:
    """Return a minimal list of stories for routing past the story_writer in tests."""
    return [
        UserStory(
            id="US-F1-001",
            feature_id="F1",
            persona="user",
            goal="do something",
            benefit="value is delivered",
            acceptance_criteria=(AcceptanceCriterion(given="context", when="action", then="outcome"),),
            story_points=StoryPointValue.THREE,
            priority=Priority.HIGH,
        )
    ]


def _dummy_tasks() -> list[Task]:
    """Return a minimal list of tasks for routing past the task_decomposer in tests."""
    return [Task(id="T-US-E1-001-01", story_id="US-F1-001", title="Implement feature", description="Build it")]


def _dummy_sprints() -> list[Sprint]:
    """Return a minimal list of sprints for routing past the sprint_planner in tests."""
    return [Sprint(id="SP-1", name="Sprint 1", goal="Core features", capacity_points=3, story_ids=("US-F1-001",))]


class TestFeatureGenerationScenario:
    """Tests that a project description flows through the graph and produces feature output.

    All tests pass a completed questionnaire + project_analysis + features
    so the graph routes to the agent node.
    """

    def _invoke_with_mock_features(self, monkeypatch):
        """Helper: monkeypatch the LLM to return _MOCK_FEATURE_RESPONSE, invoke the graph."""
        fake_response = AIMessage(content=_MOCK_FEATURE_RESPONSE)
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda: mock_llm)

        graph = create_graph()
        result = graph.invoke(
            {
                "messages": [HumanMessage(content=_TODO_APP_DESCRIPTION)],
                "questionnaire": QuestionnaireState(completed=True),
                "project_analysis": _dummy_analysis(),
                "features": _dummy_features(),
                "stories": _dummy_stories(),
                "tasks": _dummy_tasks(),
                "sprints": _dummy_sprints(),
            }
        )
        return result, mock_llm

    def test_project_description_produces_feature_response(self, monkeypatch):
        """The graph should return the full feature decomposition content from the LLM."""
        result, _ = self._invoke_with_mock_features(monkeypatch)

        last_message = result["messages"][-1]
        assert isinstance(last_message, AIMessage)
        assert last_message.content == _MOCK_FEATURE_RESPONSE

    def test_feature_response_contains_structured_features(self, monkeypatch):
        """The mock response should contain numbered features with priority markers."""
        result, _ = self._invoke_with_mock_features(monkeypatch)

        content = result["messages"][-1].content
        # Verify the structured format survived the full graph traversal
        assert "Feature 1:" in content
        assert "Feature 2:" in content
        assert "Feature 3:" in content
        assert "Feature 4:" in content
        assert "Priority:" in content

    def test_project_description_preserved_in_output_state(self, monkeypatch):
        """The original HumanMessage should be first in state, with exactly 2 messages total."""
        result, _ = self._invoke_with_mock_features(monkeypatch)

        assert len(result["messages"]) == 2
        assert isinstance(result["messages"][0], HumanMessage)
        assert result["messages"][0].content == _TODO_APP_DESCRIPTION
        assert isinstance(result["messages"][1], AIMessage)

    def test_system_prompt_injected_for_feature_generation(self, monkeypatch):
        """call_model should prepend a SystemMessage containing 'Scrum Master' to the LLM call."""
        _, mock_llm = self._invoke_with_mock_features(monkeypatch)

        # call_model builds [SystemMessage, *state["messages"]] and passes it to llm.invoke()
        call_args = mock_llm.invoke.call_args
        messages_sent = call_args[0][0]  # first positional arg is the message list

        # First message should be the system prompt
        assert isinstance(messages_sent[0], SystemMessage)
        assert "Scrum Master" in messages_sent[0].content

        # Second message should be the user's project description
        assert isinstance(messages_sent[1], HumanMessage)
        assert messages_sent[1].content == _TODO_APP_DESCRIPTION

    def test_single_llm_call_no_tool_loop(self, monkeypatch):
        """Without tool_calls in the response, the LLM should be called exactly once."""
        _, mock_llm = self._invoke_with_mock_features(monkeypatch)

        assert mock_llm.invoke.call_count == 1

    def test_empty_description_completes_without_error(self, monkeypatch):
        """An empty project description should still produce a response (LLM asks for more info)."""
        fake_response = AIMessage(content="Could you please describe your project?")
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda: mock_llm)

        graph = create_graph()
        result = graph.invoke(
            {
                "messages": [HumanMessage(content="")],
                "questionnaire": QuestionnaireState(completed=True),
                "project_analysis": _dummy_analysis(),
                "features": _dummy_features(),
                "stories": _dummy_stories(),
                "tasks": _dummy_tasks(),
                "sprints": _dummy_sprints(),
            }
        )

        assert len(result["messages"]) == 2
        assert isinstance(result["messages"][-1], AIMessage)
        assert result["messages"][-1].content == "Could you please describe your project?"


# ── Multi-turn conversation tests ──────────────────────────────────
# Validates that message history accumulates correctly across multiple
# stateless graph invocations (no checkpointer), simulating clarification
# before feature generation.


class TestMultiTurnFeatureConversation:
    """Tests multi-turn conversations where the agent asks clarifications before generating features.

    All tests pass a completed questionnaire + project_analysis + features
    so the graph routes to the agent node.
    """

    def test_clarification_then_features(self, monkeypatch):
        """Two sequential invocations: first returns a question, second returns features.

        This simulates the real flow where the Scrum Master asks clarifying
        questions before decomposing into features. Without a checkpointer, the
        caller must pass accumulated history into the second invocation.
        """
        clarification_response = AIMessage(content=_MOCK_CLARIFICATION)
        feature_response = AIMessage(content=_MOCK_FEATURE_RESPONSE)

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        # First call → clarifying question, second call → feature decomposition
        mock_llm.invoke.side_effect = [clarification_response, feature_response]
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda: mock_llm)

        completed_qs = QuestionnaireState(completed=True)
        analysis = _dummy_analysis()
        features = _dummy_features()
        tasks = _dummy_tasks()
        graph = create_graph()

        sprints = _dummy_sprints()

        # Turn 1: user sends project description, agent asks clarification
        turn1 = graph.invoke(
            {
                "messages": [HumanMessage(content=_TODO_APP_DESCRIPTION)],
                "questionnaire": completed_qs,
                "project_analysis": analysis,
                "features": features,
                "stories": _dummy_stories(),
                "tasks": tasks,
                "sprints": sprints,
            }
        )
        assert turn1["messages"][-1].content == _MOCK_CLARIFICATION
        assert len(turn1["messages"]) == 2

        # Turn 2: user answers, providing accumulated history + new message
        # Without a checkpointer, we must pass the full conversation manually.
        # See docs: "Memory & State" — stateless invocation requires manual history.
        user_followup = HumanMessage(content="About 100 users initially. No WebSockets needed. Use OAuth with Google.")
        accumulated = [*turn1["messages"], user_followup]

        turn2 = graph.invoke(
            {
                "messages": accumulated,
                "questionnaire": completed_qs,
                "project_analysis": analysis,
                "features": features,
                "stories": _dummy_stories(),
                "tasks": tasks,
                "sprints": sprints,
            }
        )

        # Verify message accumulation: 2 from turn1 + 1 followup + 1 AI response = 4
        assert len(turn2["messages"]) == 4
        assert turn2["messages"][0].content == _TODO_APP_DESCRIPTION
        assert turn2["messages"][1].content == _MOCK_CLARIFICATION
        assert turn2["messages"][2].content == "About 100 users initially. No WebSockets needed. Use OAuth with Google."
        assert turn2["messages"][-1].content == _MOCK_FEATURE_RESPONSE

        # Both turns should have called the LLM exactly once each
        assert mock_llm.invoke.call_count == 2


# ── Intake routing integration tests ──────────────────────────────
# Tests that the graph correctly routes between project_intake and agent
# based on questionnaire state. These are integration tests — they invoke
# the full compiled graph (not individual nodes).


class TestIntakeRouting:
    """Tests that the graph routes to intake or agent based on questionnaire state."""

    def test_routes_to_intake_without_questionnaire(self):
        """Without a questionnaire in state, the graph routes to project_intake.

        The default flow is smart intake (the 30-question "standard" Q1-first flow
        was retired), so it initializes a questionnaire and asks an essential gap
        rather than starting at Q1.
        """
        graph = create_graph()
        result = graph.invoke({"messages": []})

        # project_intake should have initialized a QuestionnaireState in smart mode
        assert "questionnaire" in result
        assert isinstance(result["questionnaire"], QuestionnaireState)
        assert result["questionnaire"].intake_mode == "smart"

        # An intake question should have been asked.
        last_msg = result["messages"][-1]
        assert isinstance(last_msg, AIMessage)
        assert last_msg.content.strip()

    def test_routes_to_analyzer_with_complete_questionnaire(self, monkeypatch):
        """With a completed questionnaire but no analysis, graph routes to project_analyzer."""
        fake_response = MagicMock()
        fake_response.content = (
            '{"project_name": "Test", "project_description": "d",'
            ' "project_type": "greenfield", "goals": [], "end_users": [],'
            ' "target_state": "", "tech_stack": [], "integrations": [],'
            ' "constraints": [], "sprint_length_weeks": 2, "target_sprints": 3,'
            ' "risks": [], "out_of_scope": [], "assumptions": []}'
        )
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        graph = create_graph()
        result = graph.invoke(
            {
                "messages": [HumanMessage(content="continue")],
                "questionnaire": QuestionnaireState(
                    completed=True,
                    answers={i: f"Answer {i}" for i in range(1, 27)},
                ),
            }
        )

        # The analyzer should have produced a project_analysis
        assert "project_analysis" in result
        assert isinstance(result["project_analysis"], ProjectAnalysis)

    def test_routes_to_feature_generator_with_analysis_no_features(self, monkeypatch):
        """With completed questionnaire + analysis but no features, graph routes to feature_generator."""
        # The feature_generator node calls get_llm(temperature=0.0), so we use **kw
        fake_response = MagicMock()
        fake_response.content = '[{"id": "E1", "title": "Core", "description": "Core features", "priority": "high"}]'
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        graph = create_graph()
        result = graph.invoke(
            {
                "messages": [HumanMessage(content="continue")],
                "questionnaire": QuestionnaireState(completed=True),
                "project_analysis": _dummy_analysis(),
            }
        )

        # The feature_generator should have produced features
        assert "features" in result
        assert len(result["features"]) >= 1

    def test_routes_to_task_decomposer_with_stories_no_tasks(self, monkeypatch):
        """With questionnaire + analysis + features + stories but no tasks, routes to task_decomposer."""
        # The task_decomposer node calls get_llm(temperature=0.0), so we use **kw
        fake_response = MagicMock()
        fake_response.content = (
            '[{"id": "T-US-E1-001-01", "story_id": "US-F1-001", '
            '"title": "Implement feature", "description": "Build it"}]'
        )
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        graph = create_graph()
        result = graph.invoke(
            {
                "messages": [HumanMessage(content="continue")],
                "questionnaire": QuestionnaireState(completed=True),
                "project_analysis": _dummy_analysis(),
                "features": _dummy_features(),
                "stories": _dummy_stories(),
            }
        )

        # The task_decomposer should have produced tasks
        assert "tasks" in result
        assert len(result["tasks"]) >= 1

    def test_routes_to_sprint_planner_with_tasks_no_sprints(self, monkeypatch):
        """With questionnaire + analysis + features + stories + tasks but no sprints, routes directly to sprint_planner.

        Sprint selection and capacity check are now handled during intake.
        route_entry goes directly from tasks → sprint_planner.
        """
        # The sprint_planner node calls get_llm(temperature=0.0), so we use **kw
        fake_response = MagicMock()
        fake_response.content = (
            '[{"id": "SP-1", "name": "Sprint 1", "goal": "Core features", '
            '"capacity_points": 3, "story_ids": ["US-F1-001"]}]'
        )
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        graph = create_graph()
        base_state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": _dummy_analysis(),
            "features": _dummy_features(),
            "stories": _dummy_stories(),
            "tasks": _dummy_tasks(),
            "starting_sprint_number": -1,
        }

        # Single invoke: route_entry sees tasks but no sprints → sprint_planner
        result = graph.invoke(base_state)

        # Sprint planner may return a capacity override warning — handle it
        if not result.get("sprints"):
            result = graph.invoke({**result, "messages": [HumanMessage(content="yes")]})

        # The sprint_planner should have produced sprints
        assert "sprints" in result
        assert len(result["sprints"]) >= 1

    def test_routes_to_agent_with_features_and_stories_and_tasks(self, monkeypatch):
        """With completed questionnaire + analysis + features + stories + tasks + sprints, graph routes to agent."""
        fake_response = AIMessage(content="Let me generate your Scrum plan.")
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda: mock_llm)

        graph = create_graph()
        result = graph.invoke(
            {
                "messages": [HumanMessage(content="Generate stories")],
                "questionnaire": QuestionnaireState(completed=True),
                "project_analysis": _dummy_analysis(),
                "features": _dummy_features(),
                "stories": _dummy_stories(),
                "tasks": _dummy_tasks(),
                "sprints": _dummy_sprints(),
            }
        )

        # The agent node should have been called (LLM invoked)
        assert mock_llm.invoke.call_count == 1
        assert result["messages"][-1].content == "Let me generate your Scrum plan."

    def test_routes_to_story_writer_with_features_no_stories(self, monkeypatch):
        """With completed questionnaire + analysis + features but no stories, routes to story_writer."""
        # The story_writer node calls get_llm(temperature=0.0), so we use **kw
        fake_response = MagicMock()
        fake_response.content = (
            '[{"id": "US-F1-001", "feature_id": "F1", "persona": "user", "goal": "test", '
            '"benefit": "value", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "high"}]'
        )
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        graph = create_graph()
        result = graph.invoke(
            {
                "messages": [HumanMessage(content="continue")],
                "questionnaire": QuestionnaireState(completed=True),
                "project_analysis": _dummy_analysis(),
                "features": _dummy_features(),
            }
        )

        # The story_writer should have produced stories
        assert "stories" in result
        assert len(result["stories"]) >= 1

    def test_intake_does_not_call_llm(self, monkeypatch):
        """The project_intake node is deterministic — it should NOT call the LLM."""
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda: mock_llm)

        graph = create_graph()
        graph.invoke({"messages": []})

        # LLM should never have been called — intake uses static questions
        mock_llm.invoke.assert_not_called()
