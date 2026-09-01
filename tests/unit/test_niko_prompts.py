"""Tests for Niko's system prompt (prompts/niko.py).

The prompt is where the read-only promise is stated to the model. The tool
surface is what actually enforces it (there is no write tool to call), but a
Niko that offers to run a standup and then can't is a worse experience than one
that says up front what it can do — so the wording is pinned.
"""

from __future__ import annotations

from yeaboi.prompts.niko import get_niko_system_prompt, get_niko_title_prompt


class TestIdentity:
    def test_it_names_all_three_audiences(self):
        prompt = get_niko_system_prompt()
        assert "Solo" in prompt and "Team" in prompt and "Agents" in prompt

    def test_it_names_the_modes_a_user_would_ask_about(self):
        prompt = get_niko_system_prompt().lower()
        for mode in ("planning", "standup", "retro", "poker", "performance", "reporting", "ship"):
            assert mode in prompt

    def test_it_names_the_agents_family(self):
        prompt = get_niko_system_prompt().lower()
        for member in ("usage", "advisor", "security"):
            assert member in prompt

    def test_it_calls_itself_niko(self):
        assert "You are Niko" in get_niko_system_prompt()


class TestTheReadOnlyPromise:
    def test_it_says_the_tools_are_read_only(self):
        assert "read-only" in get_niko_system_prompt()

    def test_it_forbids_inventing_numbers(self):
        prompt = get_niko_system_prompt().lower()
        assert "never estimate" in prompt or "ground every number" in prompt

    def test_it_tells_the_model_to_check_routes_before_navigating(self):
        assert "list_routes" in get_niko_system_prompt()

    def test_it_names_what_niko_cannot_do(self):
        prompt = get_niko_system_prompt().lower()
        for verb in ("start a run", "delete"):
            assert verb in prompt


class TestContext:
    def test_the_route_and_its_capability_land_in_the_prompt(self):
        prompt = get_niko_system_prompt(route="/team/retro", capability="retro-board", screen_title="Retro")
        assert "/team/retro" in prompt
        assert "retro-board" in prompt
        assert "Retro" in prompt

    def test_an_unknown_screen_is_admitted_rather_than_guessed(self):
        assert "don't know which screen" in get_niko_system_prompt()

    def test_the_user_name_is_omitted_when_blank(self):
        assert "You are talking to" not in get_niko_system_prompt()
        assert "You are talking to Omar" in get_niko_system_prompt(user_name="Omar")

    def test_the_surface_changes_how_navigate_is_described(self):
        assert "desktop window" in get_niko_system_prompt(surface="desktop")
        assert "terminal" in get_niko_system_prompt(surface="terminal").split("## Right now")[1]

    def test_facts_are_listed_verbatim(self):
        prompt = get_niko_system_prompt(facts=("They have 3 saved planning session(s).",))
        assert "- They have 3 saved planning session(s)." in prompt

    def test_no_facts_is_a_shorter_prompt_not_a_broken_one(self):
        assert get_niko_system_prompt(facts=()).endswith(".")


class TestTitlePrompt:
    def test_it_asks_for_a_bare_title(self):
        prompt = get_niko_title_prompt()
        assert "3-5 word" in prompt
        assert "ONLY the title" in prompt
