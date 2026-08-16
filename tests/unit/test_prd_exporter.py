"""Tests for prd_exporter — the Product Requirements Document builder.

Deterministic sections assemble straight from the plan artifacts; the one
LLM call (prose sections) is mocked or gated off, and a fallback run must
still emit the FULL document with an honest warning.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from tests._node_helpers import make_dummy_analysis, make_sample_features, make_valid_story
from yeaboi.agent.state import (
    AcceptanceCriterion,
    ArchitectureDecision,
    ArchitectureOption,
    Sprint,
)
from yeaboi.prd_exporter import PrdResult, build_prd_markdown, export_prd_markdown


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """Default every test to the no-LLM path; LLM tests override explicitly."""
    monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no API key"))


def _graph_state(**overrides):
    story = make_valid_story()
    features = make_sample_features()
    state = {
        "messages": [],
        "project_analysis": make_dummy_analysis(),
        "features": features,
        "stories": [story],
        "sprints": [Sprint(id="SP-1", name="Sprint 1", goal="Foundation", capacity_points=10, story_ids=(story.id,))],
        "sprint_length_weeks": 2,
        "velocity_per_sprint": 10,
        "team_size": 3,
    }
    state.update(overrides)
    return state


class TestDeterministicSections:
    def test_full_document_in_fallback_mode(self):
        result = build_prd_markdown(_graph_state())
        assert result.llm_mode == "fallback"
        assert result.warnings and "deterministic placeholders" in result.warnings[0]
        md = result.markdown
        # Every deterministic section is present even without an LLM.
        for heading in (
            "# PRD — Test Project",
            "## Executive Summary",
            "## Mission",
            "## MVP Scope",
            "## User Stories",
            "## Technology Stack",
            "## Security & Configuration",
            "## Success Criteria",
            "## Implementation Phases",
            "## Risks & Mitigations",
            "## Appendix",
        ):
            assert heading in md, heading

    def test_mvp_scope_checkboxes(self):
        md = build_prd_markdown(_graph_state()).markdown
        assert "✅ **User Authentication**" in md
        assert "❌ Mobile app" in md  # out_of_scope from make_dummy_analysis
        assert "⚠️ Default velocity assumed" in md

    def test_stories_verbatim_with_flat_acs(self):
        story = make_valid_story()
        import dataclasses

        story = dataclasses.replace(
            story, acceptance_criteria=(AcceptanceCriterion(text="Search returns within 200ms."),)
        )
        md = build_prd_markdown(_graph_state(stories=[story])).markdown
        assert story.text in md
        assert "AC: Search returns within 200ms." in md
        assert "Given" not in md.split("## User Stories")[1].split("##")[0]

    def test_architecture_section_present_and_absent(self):
        arch = ArchitectureDecision(
            options=(
                ArchitectureOption(name="Monolith", summary="one deployable", pros=("simple",)),
                ArchitectureOption(name="Serverless", summary="functions"),
            ),
            chosen="Monolith",
            confidence="medium",
        )
        with_arch = build_prd_markdown(_graph_state(project_analysis=make_dummy_analysis(architecture=arch)))
        assert "## Core Architecture & Patterns" in with_arch.markdown
        assert "✓ Monolith" in with_arch.markdown
        without = build_prd_markdown(_graph_state())
        assert "## Core Architecture & Patterns" not in without.markdown

    def test_phases_mirror_sprints(self):
        md = build_prd_markdown(_graph_state()).markdown
        assert "### Phase 1: Sprint 1" in md
        assert "**Goal:** Foundation" in md
        assert "Definition of Done" in md

    def test_api_section_conditional(self):
        # make_dummy_analysis has "FastAPI" in the stack → the section appears.
        assert "## API Specification" in build_prd_markdown(_graph_state()).markdown
        no_api = make_dummy_analysis(tech_stack=("Excel",))
        story = make_valid_story()
        state = _graph_state(project_analysis=no_api, stories=[story])
        if not any(w in f"{story.title} {story.goal}".lower() for w in ("api", "endpoint")):
            assert "## API Specification" not in build_prd_markdown(state).markdown

    def test_never_raises_on_empty_state(self):
        result = build_prd_markdown({"messages": []})
        assert result.markdown.startswith("# PRD — ")
        assert result.llm_mode == "fallback"


class TestLlmPath:
    def _mock_llm(self, monkeypatch, payload: dict):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        response = MagicMock()
        response.content = json.dumps(payload)
        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", lambda prompt, temperature=0.3: response)

    def test_happy_path(self, monkeypatch):
        self._mock_llm(
            monkeypatch,
            {
                "executive_summary": "A great product.",
                "mission": "Ship it.",
                "target_users": [{"persona": "developer", "description": "wants speed"}],
                "success_criteria": ["95% uptime"],
                "future_considerations": ["Mobile app"],
                "risks_mitigations": [{"risk": "Tight timeline", "mitigation": "Cut scope early"}],
            },
        )
        result = build_prd_markdown(_graph_state())
        assert result.llm_mode == "llm"
        assert result.warnings == ()
        assert "A great product." in result.markdown
        assert "developer — wants speed" in result.markdown
        assert "✅ 95% uptime" in result.markdown
        assert "Tight timeline — Cut scope early" in result.markdown

    def test_partial_reply_falls_back_per_section(self, monkeypatch):
        self._mock_llm(monkeypatch, {"executive_summary": "Only this."})
        result = build_prd_markdown(_graph_state())
        assert result.llm_mode == "llm"
        assert any("mission" in w for w in result.warnings)
        assert "Only this." in result.markdown
        # The missing sections still render from the deterministic fallback.
        assert "## Risks & Mitigations" in result.markdown

    def test_all_falsy_dict_entries_skipped_not_crashed(self, monkeypatch):
        # A degenerate reply entry ({"persona": "", "description": ""}) must
        # render as nothing, not IndexError — build_prd_markdown never raises.
        self._mock_llm(
            monkeypatch,
            {"executive_summary": "Still here.", "target_users": [{"persona": "", "description": ""}]},
        )
        result = build_prd_markdown(_graph_state())
        assert "Still here." in result.markdown
        assert "## Target Users" not in result.markdown  # empty section skipped, not crashed

    def test_llm_failure_falls_back_whole(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))

        def _boom(prompt, temperature=0.3):
            raise RuntimeError("connection refused")

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", _boom)
        result = build_prd_markdown(_graph_state())
        assert result.llm_mode == "fallback"
        assert result.warnings


class TestExportPrdMarkdown:
    def test_writes_file(self, tmp_path):
        path = export_prd_markdown(_graph_state(), path=tmp_path / "prd.md")
        assert path.exists()
        assert path.read_text().startswith("# PRD — Test Project")

    def test_prebuilt_result_reused(self, tmp_path, monkeypatch):
        # A pre-built result must be written as-is — no second build/LLM call.
        def _boom(state):
            raise AssertionError("build_prd_markdown must not be called again")

        monkeypatch.setattr("yeaboi.prd_exporter.build_prd_markdown", _boom)
        path = export_prd_markdown({}, path=tmp_path / "prd.md", result=PrdResult(markdown="# PRD — X\n"))
        assert path.read_text() == "# PRD — X\n"
