"""Tests for the PRD prose prompt factory."""

from yeaboi.prompts.prd import PRD_PROSE_KEYS, get_prd_prose_prompt


class TestGetPrdProsePrompt:
    def test_schema_carries_all_six_prose_keys(self):
        prompt = get_prd_prose_prompt("Project: X")
        for key in PRD_PROSE_KEYS:
            assert f'"{key}"' in prompt

    def test_digest_included(self):
        prompt = get_prd_prose_prompt("Project: WidgetCo\nGoals: ship widgets")
        assert "Project: WidgetCo" in prompt
        assert "ship widgets" in prompt

    def test_arc_actor_line(self):
        prompt = get_prd_prose_prompt("digest")
        assert prompt.startswith("You are a senior product manager")

    def test_architecture_note_conditional(self):
        assert "architecture decision" in get_prd_prose_prompt("d", has_architecture=True)
        assert "architecture decision" not in get_prd_prose_prompt("d", has_architecture=False)

    def test_grounding_rule_present(self):
        assert "do NOT invent scope" in get_prd_prose_prompt("d")
