"""Graph topology validation — structural integrity checks for the agent graph.

# See docs: "Agentic Blueprint Reference" — Core Graph Setup, Wiring

These tests catch wiring mistakes (orphan nodes, dead ends, dangling edge targets)
that would break the agent at runtime but are not always caught by LangGraph's
own compile-time validation:

- ``TestNodeReachability``   — every node is reachable from START (no orphans)
- ``TestNodeOutgoingEdges``  — every node has at least one outgoing edge
- ``TestConditionalEdges``   — all conditional edge targets are registered nodes
- ``TestCompileTimeGuardrails`` — what LangGraph does and does not enforce itself

All tests call ``create_graph(tools=[])`` so no LLM / API key is needed.
"""

from collections import deque

import pytest

from yeaboi.agent.graph import create_graph

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

_EXPECTED_NODES = frozenset(
    {
        "project_intake",
        "project_analyzer",
        "feature_skip",
        "feature_generator",
        "story_writer",
        "task_decomposer",
        "sprint_planner",
        "agent",
        "tools",
        "human_review",
    }
)


@pytest.fixture(scope="module")
def graph():
    """Compile the graph once for the whole module — topology never changes."""
    return create_graph(tools=[])


# ---------------------------------------------------------------------------
# Test 1: every node reachable from START
# ---------------------------------------------------------------------------


class TestNodeReachability:
    """Every registered node must be reachable from __start__ via BFS.

    # See docs: "Agentic Blueprint Reference" — Wiring
    #
    # An unreachable node can never be invoked, so any code it contains is
    # dead code. This test catches the common mistake of adding a node but
    # forgetting to wire it into the graph with an edge.
    #
    # Why BFS on get_graph().edges (not builder.edges)?
    # builder.edges only contains direct (non-conditional) edges. get_graph()
    # flattens both direct and conditional edges into a single list of
    # Edge(source, target) objects, making traversal straightforward.
    """

    def test_all_nodes_reachable_from_start(self, graph):
        """BFS from __start__ must visit every registered node."""
        dg = graph.get_graph()

        # Build adjacency list from the drawable graph
        adjacency: dict[str, list[str]] = {}
        for edge in dg.edges:
            adjacency.setdefault(edge.source, []).append(edge.target)

        # BFS
        visited: set[str] = set()
        queue: deque[str] = deque(["__start__"])
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            for neighbour in adjacency.get(node, []):
                queue.append(neighbour)

        # Every node registered with add_node() must be reachable.
        registered = set(graph.builder.nodes.keys())
        unreachable = registered - visited
        assert not unreachable, f"Nodes not reachable from START (orphan nodes): {unreachable}"

    def test_expected_node_set_matches_graph(self, graph):
        """The set of registered nodes equals the expected set — catches accidental renames/removals."""
        registered = set(graph.builder.nodes.keys())
        assert registered == _EXPECTED_NODES, (
            f"Unexpected change to graph nodes.\n"
            f"  Extra  : {registered - _EXPECTED_NODES}\n"
            f"  Missing: {_EXPECTED_NODES - registered}"
        )


# ---------------------------------------------------------------------------
# Test 2: every node has at least one outgoing edge (no dead ends)
# ---------------------------------------------------------------------------


class TestNodeOutgoingEdges:
    """Every registered node must have at least one outgoing edge.

    # See docs: "Agentic Blueprint Reference" — Wiring
    #
    # A node with no outgoing edges is a silent dead end: graph.invoke()
    # would hang or behave unexpectedly after executing it. Nodes that
    # terminate the graph correctly route to END (``__end__``), which
    # appears as an outgoing edge in get_graph().
    """

    def test_every_node_has_outgoing_edge(self, graph):
        """All registered nodes appear as an edge source in the drawable graph."""
        dg = graph.get_graph()
        nodes_with_outgoing = {edge.source for edge in dg.edges}

        registered = set(graph.builder.nodes.keys())
        dead_ends = registered - nodes_with_outgoing
        assert not dead_ends, f"Nodes with no outgoing edges (dead ends): {dead_ends}"

    def test_pipeline_nodes_terminate_at_end(self, graph):
        """Pipeline nodes (intake, analyzer, generators) route to END after completing."""
        # These nodes do one job and hand off to the REPL. They must not loop.
        pipeline_nodes = {
            "project_intake",
            "project_analyzer",
            "feature_generator",
            "story_writer",
            "task_decomposer",
            "sprint_planner",
            "human_review",
        }
        dg = graph.get_graph()
        terminal_nodes = {edge.source for edge in dg.edges if edge.target == "__end__"}
        missing_terminal = pipeline_nodes - terminal_nodes
        assert not missing_terminal, f"Pipeline nodes without an edge to END: {missing_terminal}"


# ---------------------------------------------------------------------------
# Test 3: conditional edge targets are valid registered nodes
# ---------------------------------------------------------------------------


class TestConditionalEdges:
    """All destinations declared in conditional edges must be registered nodes.

    # See docs: "Agentic Blueprint Reference" — conditional edges
    #
    # LangGraph validates edge *sources* at add_conditional_edges() time but
    # destinations in the ``ends`` dict can be silently wrong if a node is
    # renamed after wiring. This test catches that class of mistake.
    #
    # builder.branches structure:
    #   {source_node: {branch_name: Branch(ends={return_value: dest_node})}}
    #
    # The ``ends`` dict maps the return value of the routing function
    # (e.g. "tools") to a destination node name (e.g. "tools"). Both
    # must exist as registered nodes or be the sentinel "__end__".
    """

    def test_all_conditional_targets_are_valid_nodes(self, graph):
        """Every destination in builder.branches.ends is a registered node or __end__."""
        valid_targets = set(graph.builder.nodes.keys()) | {"__end__"}

        invalid: dict[str, str] = {}
        for source_node, branches in graph.builder.branches.items():
            for branch_name, branch in branches.items():
                for dest in branch.ends.values():
                    if dest not in valid_targets:
                        key = f"{source_node}.{branch_name}"
                        invalid[key] = dest

        assert not invalid, f"Conditional edges reference unknown nodes: {invalid}"

    def test_route_entry_covers_all_pipeline_nodes(self, graph):
        """route_entry (START branch) declares all eight routing destinations."""
        expected_destinations = {
            "project_intake",
            "project_analyzer",
            "feature_generator",
            "story_writer",
            "task_decomposer",
            "sprint_planner",
            "agent",
        }
        start_branches = graph.builder.branches.get("__start__", {})
        route_entry_branch = start_branches.get("route_entry")
        assert route_entry_branch is not None, "route_entry branch not found on __start__"

        declared = set(route_entry_branch.ends.values())
        missing = expected_destinations - declared
        assert not missing, f"route_entry branch missing destinations: {missing}"

    def test_should_continue_covers_all_react_destinations(self, graph):
        """should_continue (agent branch) declares tools, human_review, and END."""
        expected_destinations = {"tools", "human_review", "__end__"}
        agent_branches = graph.builder.branches.get("agent", {})
        should_continue_branch = agent_branches.get("should_continue")
        assert should_continue_branch is not None, "should_continue branch not found on agent"

        declared = set(should_continue_branch.ends.values())
        assert declared == expected_destinations, (
            f"should_continue destinations mismatch.\n  Expected: {expected_destinations}\n  Got:      {declared}"
        )


# ---------------------------------------------------------------------------
# Test 4: compile-time guardrails — what LangGraph does and does not enforce
# ---------------------------------------------------------------------------


class TestCompileTimeGuardrails:
    """Document LangGraph's own compile-time validation boundaries.

    # See docs: "Agentic Blueprint Reference" — Core Graph Setup
    #
    # LangGraph enforces referential integrity for explicit edge targets —
    # an edge pointing to a node that was never registered raises ValueError
    # at compile() time. This means typos in add_edge() / add_conditional_edges()
    # are caught immediately rather than silently failing at runtime.
    #
    # LangGraph does NOT raise for orphan nodes (nodes registered with add_node
    # but never connected by an edge). The reachability test above (TestNodeReachability)
    # is what catches orphan nodes in our graph.
    """

    def test_edge_to_unknown_node_raises_at_compile_time(self):
        """Wiring an edge to a non-existent node raises ValueError during compile()."""
        from langgraph.graph import START, StateGraph

        from yeaboi.agent.state import ScrumState

        g = StateGraph(ScrumState)
        g.add_node("valid_node", lambda s: {"messages": []})
        g.add_edge(START, "valid_node")
        g.add_edge("valid_node", "does_not_exist")  # intentional bad edge

        with pytest.raises(ValueError, match="unknown node"):
            g.compile()

    def test_orphan_node_does_not_raise_at_compile_time(self):
        """LangGraph compile() succeeds even if a node has no edges (orphan).

        This confirms that our reachability test (not LangGraph itself) is
        the safety net for orphan nodes — both tests must stay in the suite.
        """
        from langgraph.graph import END, START, StateGraph

        from yeaboi.agent.state import ScrumState

        g = StateGraph(ScrumState)
        g.add_node("connected", lambda s: {"messages": []})
        g.add_node("orphan", lambda s: {"messages": []})  # no edges
        g.add_edge(START, "connected")
        g.add_edge("connected", END)

        # Compile succeeds — LangGraph does not enforce reachability
        compiled = g.compile()
        assert compiled is not None
        # But "orphan" is absent from a reachability BFS from START,
        # which is exactly what TestNodeReachability detects in the real graph.
        dg = compiled.get_graph()
        reachable = {e.target for e in dg.edges}
        assert "orphan" not in reachable
