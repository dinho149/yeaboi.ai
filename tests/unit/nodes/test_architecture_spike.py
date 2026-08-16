"""Tests for the architecture options + validation-spike feature.

Covers _parse_architecture guardrails, the opt-in/out gate
(_maybe_prompt_spike_choice + _parse_spike_reply), both deterministic
injectors, and the sprint-1 pin.

See docs: "Scrum Standards" — DoD Spike.
"""

from tests._node_helpers import make_dummy_analysis, make_sample_features, make_valid_story
from yeaboi.agent.nodes import (
    _inject_architecture_spike_story,
    _inject_architecture_spike_task,
    _maybe_prompt_spike_choice,
    _parse_architecture,
    _parse_spike_reply,
    _pin_spike_to_first_sprint,
    _spike_eligible,
    spike_recommended,
)
from yeaboi.agent.state import (
    ArchitectureDecision,
    ArchitectureOption,
    Priority,
    Sprint,
    Task,
    TaskLabel,
)


def _open_decision(confidence="medium"):
    return ArchitectureDecision(
        options=(
            ArchitectureOption(name="Modular monolith", summary="One deployable", pros=("simple",), cons=("scaling",)),
            ArchitectureOption(name="Microservices", summary="Many services", pros=("scale",), cons=("ops cost",)),
        ),
        chosen="Modular monolith",
        confidence=confidence,
        rationale="Small team, single product.",
    )


class TestParseArchitecture:
    def test_happy_path(self):
        arch = _parse_architecture(
            {
                "options": [
                    {"name": "Monolith", "summary": "s", "pros": ["a"], "cons": ["b"]},
                    {"name": "Serverless", "summary": "s2", "pros": [], "cons": []},
                ],
                "chosen": "Serverless",
                "confidence": "LOW",
                "rationale": "why",
                "pinned_by_constraint": False,
            }
        )
        assert arch is not None
        assert [o.name for o in arch.options] == ["Monolith", "Serverless"]
        assert arch.chosen == "Serverless"
        assert arch.confidence == "low"

    def test_non_dict_and_optionless_return_none(self):
        assert _parse_architecture(None) is None
        assert _parse_architecture("monolith") is None
        assert _parse_architecture({"options": []}) is None
        assert _parse_architecture({"options": [{"summary": "nameless"}]}) is None

    def test_clamps_to_three_options(self):
        raw = {"options": [{"name": f"O{i}"} for i in range(6)], "chosen": "O0"}
        arch = _parse_architecture(raw)
        assert len(arch.options) == 3

    def test_bad_chosen_coerced_to_first_option(self):
        arch = _parse_architecture({"options": [{"name": "A"}, {"name": "B"}], "chosen": "Nonexistent"})
        assert arch.chosen == "A"

    def test_unknown_confidence_normalizes_to_medium(self):
        arch = _parse_architecture({"options": [{"name": "A"}, {"name": "B"}], "confidence": "certain"})
        assert arch.confidence == "medium"

    def test_single_option_counts_as_pinned(self):
        arch = _parse_architecture({"options": [{"name": "Existing stack"}], "pinned_by_constraint": False})
        assert arch.pinned_by_constraint is True


class TestSpikeGate:
    def test_eligible_needs_two_options_not_pinned(self):
        assert _spike_eligible(make_dummy_analysis(architecture=_open_decision()))
        pinned = ArchitectureDecision(
            options=_open_decision().options, chosen="Modular monolith", confidence="high", pinned_by_constraint=True
        )
        assert not _spike_eligible(make_dummy_analysis(architecture=pinned))
        assert not _spike_eligible(make_dummy_analysis())  # no architecture at all
        assert not _spike_eligible(None)

    def test_confidence_rule(self):
        assert spike_recommended("medium") is True
        assert spike_recommended("low") is True
        assert spike_recommended("high") is False

    def test_prompt_parked_when_undecided(self, monkeypatch):
        monkeypatch.delenv("YEABOI_ARCHITECTURE_SPIKE", raising=False)
        analysis = make_dummy_analysis(architecture=_open_decision())
        result = _maybe_prompt_spike_choice({}, analysis)
        assert result is not None
        assert result["_spike_prompt"]["chosen"] == "Modular monolith"
        assert result["_spike_prompt"]["recommended"] == "include"
        assert "validation spike" in result["messages"][0].content

    def test_high_confidence_recommends_skip(self, monkeypatch):
        monkeypatch.delenv("YEABOI_ARCHITECTURE_SPIKE", raising=False)
        analysis = make_dummy_analysis(architecture=_open_decision(confidence="high"))
        result = _maybe_prompt_spike_choice({}, analysis)
        assert result["_spike_prompt"]["recommended"] == "skip"

    def test_no_prompt_when_decided_pinned_or_overridden(self, monkeypatch):
        monkeypatch.delenv("YEABOI_ARCHITECTURE_SPIKE", raising=False)
        analysis = make_dummy_analysis(architecture=_open_decision())
        assert _maybe_prompt_spike_choice({"spike_choice": "skip"}, analysis) is None
        assert _maybe_prompt_spike_choice({}, make_dummy_analysis()) is None
        monkeypatch.setenv("YEABOI_ARCHITECTURE_SPIKE", "skip")
        assert _maybe_prompt_spike_choice({}, analysis) is None

    def test_parse_spike_reply(self):
        from langchain_core.messages import HumanMessage

        prompt = {"recommended": "include"}
        assert _parse_spike_reply({"messages": [HumanMessage("skip it")], "_spike_prompt": prompt}) == "skip"
        assert _parse_spike_reply({"messages": [HumanMessage("add the spike")], "_spike_prompt": prompt}) == "include"
        assert _parse_spike_reply({"messages": [HumanMessage("2")], "_spike_prompt": prompt}) == "skip"
        # Ambiguous (incl. the REPL export-only "continue") → recommended default.
        assert _parse_spike_reply({"messages": [HumanMessage("continue")], "_spike_prompt": prompt}) == "include"
        assert (
            _parse_spike_reply({"messages": [HumanMessage("continue")], "_spike_prompt": {"recommended": "skip"}})
            == "skip"
        )


class TestSpikeStoryInjection:
    def test_injects_first_with_spike_shape(self):
        analysis = make_dummy_analysis(architecture=_open_decision())
        features = make_sample_features()
        stories, note = _inject_architecture_spike_story([make_valid_story()], features, analysis)
        assert note is not None
        spike = stories[0]
        assert spike.title.startswith("[Spike] ")
        assert spike.id == f"US-{features[0].id}-SPIKE"
        assert spike.priority is Priority.CRITICAL
        assert spike.story_points == 2
        assert len(spike.acceptance_criteria) == 3
        # Testing / Code Merged / Released do not apply to a spike.
        assert spike.dod_applicable == (True, True, False, False, False, True, True)

    def test_idempotent_across_edit_reruns(self):
        analysis = make_dummy_analysis(architecture=_open_decision())
        features = make_sample_features()
        stories, _ = _inject_architecture_spike_story([make_valid_story()], features, analysis)
        again, note = _inject_architecture_spike_story(stories, features, analysis)
        assert note is None
        assert sum(1 for s in again if s.title.startswith("[Spike] ")) == 1

    def test_bullets_style_acs(self):
        analysis = make_dummy_analysis(architecture=_open_decision())
        stories, _ = _inject_architecture_spike_story([], make_sample_features(), analysis, "bullets")
        assert all(ac.text for ac in stories[0].acceptance_criteria)

    def test_no_features_no_injection(self):
        analysis = make_dummy_analysis(architecture=_open_decision())
        stories, note = _inject_architecture_spike_story([make_valid_story()], [], analysis)
        assert note is None


class TestSpikeTaskInjection:
    def test_spliced_first_for_first_story(self):
        analysis = make_dummy_analysis(architecture=_open_decision())
        story = make_valid_story()
        tasks = [
            Task(id="T-1", story_id=story.id, title="Build it", description="d"),
            Task(id="T-2", story_id=story.id, title="Test it", description="d"),
        ]
        result, note = _inject_architecture_spike_task(tasks, [story], analysis)
        assert note is not None
        assert result[0].title.startswith("[Spike] ")
        assert result[0].label is TaskLabel.SPIKE
        assert result[0].test_plan == ""
        assert result[0].ai_prompt  # the ARC research prompt rides along
        assert len(result) == 3

    def test_idempotent(self):
        analysis = make_dummy_analysis(architecture=_open_decision())
        story = make_valid_story()
        tasks, _ = _inject_architecture_spike_task([], [story], analysis)
        again, note = _inject_architecture_spike_task(tasks, [story], analysis)
        assert note is None
        assert len(again) == 1

    def test_no_stories_no_injection(self):
        analysis = make_dummy_analysis(architecture=_open_decision())
        tasks, note = _inject_architecture_spike_task([], [], analysis)
        assert note is None


class TestPinSpikeToFirstSprint:
    def _stories(self):
        spike = make_valid_story()
        import dataclasses

        spike = dataclasses.replace(spike, id="US-F1-SPIKE", title="[Spike] Validate architecture: X")
        other = make_valid_story()
        return [spike, other]

    def test_moves_misplaced_spike(self):
        stories = self._stories()
        sprints = [
            Sprint(id="SP-1", name="Sprint 1", goal="g", capacity_points=5, story_ids=(stories[1].id,)),
            Sprint(id="SP-2", name="Sprint 2", goal="g", capacity_points=5, story_ids=("US-F1-SPIKE",)),
        ]
        pinned = _pin_spike_to_first_sprint(sprints, stories)
        assert "US-F1-SPIKE" in pinned[0].story_ids
        assert "US-F1-SPIKE" not in pinned[1].story_ids

    def test_noop_when_already_first_or_absent(self):
        stories = self._stories()
        sprints = [Sprint(id="SP-1", name="Sprint 1", goal="g", capacity_points=5, story_ids=("US-F1-SPIKE",))]
        assert _pin_spike_to_first_sprint(sprints, stories) == sprints
        assert _pin_spike_to_first_sprint(sprints, [stories[1]]) == sprints
        assert _pin_spike_to_first_sprint([], stories) == []
