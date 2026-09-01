"""The connector-builder prompt — every draftable shape is taught, nothing derived is."""

from __future__ import annotations

from yeaboi.prompts.connector_builder import create_connector_builder_prompt


class TestConnectorBuilderPrompt:
    def test_the_description_is_embedded_and_truncated(self):
        prompt = create_connector_builder_prompt("connect statuspage " + "x" * 3000)
        assert "connect statuspage" in prompt
        assert len(prompt) < 6000

    def test_all_three_kinds_are_taught(self):
        prompt = create_connector_builder_prompt("anything")
        for kind in ("api", "webhook", "mcp"):
            assert f'"{kind}"' in prompt

    def test_extra_fields_are_taught_shape_only(self):
        prompt = create_connector_builder_prompt("a Datadog-like service with an app key")
        assert "extra_fields" in prompt
        assert "env_suffix" in prompt
        # Values, env names and the icon stay derived/user-side — never drafted.
        assert "icon" in prompt  # the never-propose rule names it
        assert "YEABOI_" not in prompt

    def test_the_webhook_kind_requires_its_mapping(self):
        prompt = create_connector_builder_prompt("anything")
        assert "webhook_verify" in prompt
        assert "REQUIRED" in prompt
