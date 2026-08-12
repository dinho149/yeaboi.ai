"""Agent graph module.

Lazy re-exports (PEP 562 module ``__getattr__``): importing any submodule
first initialises this package, and this file used to eagerly import
``agent.graph``/``agent.llm``/``agent.nodes`` — so even ``import
yeaboi.agent.state`` (all the engines, the MCP server) paid for the whole
langgraph + anthropic stack. Now a name is resolved from its owning submodule
on first attribute access and cached in the module namespace, so
``from yeaboi.agent import ScrumState`` still works unchanged while
state-only importers stop pulling the LLM stack. Guarded by
tests/unit/test_cli_startup.py's leak check via cli.py's import chain.
"""

# Name → owning submodule. Every public name must appear here AND in __all__;
# __getattr__ resolves through this map on first access.
_EXPORTS = {
    "create_graph": "yeaboi.agent.graph",
    "get_llm": "yeaboi.agent.llm",
    "call_model": "yeaboi.agent.nodes",
    "feature_generator": "yeaboi.agent.nodes",
    "feature_skip": "yeaboi.agent.nodes",
    "human_review": "yeaboi.agent.nodes",
    "make_call_model": "yeaboi.agent.nodes",
    "project_analyzer": "yeaboi.agent.nodes",
    "project_intake": "yeaboi.agent.nodes",
    "resolve_sprint_selection": "yeaboi.agent.nodes",
    "route_entry": "yeaboi.agent.nodes",
    "should_continue": "yeaboi.agent.nodes",
    "sprint_planner": "yeaboi.agent.nodes",
    "story_writer": "yeaboi.agent.nodes",
    "task_decomposer": "yeaboi.agent.nodes",
    "ActivityEvidence": "yeaboi.agent.state",
    "AnonymizedOutput": "yeaboi.agent.state",
    "DeliveredItem": "yeaboi.agent.state",
    "DeliveryReport": "yeaboi.agent.state",
    "Discipline": "yeaboi.agent.state",
    "EngineerActivity": "yeaboi.agent.state",
    "EngineerRef": "yeaboi.agent.state",
    "EngineerStory": "yeaboi.agent.state",
    "GapIssueLink": "yeaboi.agent.state",
    "IssueFilingResult": "yeaboi.agent.state",
    "MemberUpdate": "yeaboi.agent.state",
    "OneOnOnePrep": "yeaboi.agent.state",
    "OneOnOneRecord": "yeaboi.agent.state",
    "PracticeSignal": "yeaboi.agent.state",
    "ProjectAnalysis": "yeaboi.agent.state",
    "PromptQualityRating": "yeaboi.agent.state",
    "RetroCard": "yeaboi.agent.state",
    "RetroReport": "yeaboi.agent.state",
    "RoadmapAnalysis": "yeaboi.agent.state",
    "RoadmapProject": "yeaboi.agent.state",
    "ScrumState": "yeaboi.agent.state",
    "SixMonthReview": "yeaboi.agent.state",
    "StandupGap": "yeaboi.agent.state",
    "StandupReport": "yeaboi.agent.state",
    "SupportingSignal": "yeaboi.agent.state",
    "TranscriptClaim": "yeaboi.agent.state",
    "TranscriptReview": "yeaboi.agent.state",
    "TranscriptSource": "yeaboi.agent.state",
    "AnswerSource": "yeaboi.prompts.intake",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    """Resolve a public name from its submodule on first access (PEP 562)."""
    try:
        module_name = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    import importlib

    value = getattr(importlib.import_module(module_name), name)
    # Cache in the module namespace so later accesses bypass __getattr__.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
