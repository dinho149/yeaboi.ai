"""Tests for the size-classifier prompt factory."""

from yeaboi.prompts.size_classifier import get_size_classifier_prompt


class TestSizeClassifierPrompt:
    def test_includes_description(self):
        prompt = get_size_classifier_prompt("build a marketplace for vintage synths")
        assert "build a marketplace for vintage synths" in prompt

    def test_defines_all_three_labels(self):
        prompt = get_size_classifier_prompt("x")
        for label in ('"small"', '"large"', '"unclear"'):
            assert label in prompt

    def test_demands_json_only(self):
        prompt = get_size_classifier_prompt("x")
        assert "JSON" in prompt

    def test_unclear_is_the_no_guess_default(self):
        # The deterministic fallback question depends on the model preferring
        # "unclear" over guessing.
        assert "do NOT guess" in get_size_classifier_prompt("x")
