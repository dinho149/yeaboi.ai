"""Tests for the surface-neutral export menu (exporting.py)."""

from __future__ import annotations

import pytest

from yeaboi.exporting import LOCAL_DESTINATIONS, destination_blocker, destination_options


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "NOTION_TOKEN",
        "NOTION_ROOT_PAGE_ID",
        "NOTION_EXPORT_PARENT_PAGE_ID",
        "CONFLUENCE_SPACE_KEY",
        "CONFLUENCE_EXPORT_PARENT_PAGE_ID",
        "CONFLUENCE_BASE_URL",
        "CONFLUENCE_EMAIL",
        "CONFLUENCE_API_TOKEN",
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


class TestDestinationOptions:
    def test_files_and_copy_need_no_configuration(self):
        assert [o["key"] for o in destination_options(mode="retro")] == ["files", "copy"]

    def test_every_option_carries_a_description(self):
        assert all(o["description"] for o in destination_options(mode="retro"))

    def test_copy_is_the_one_the_client_completes(self):
        options = {o["key"]: o["local"] for o in destination_options(mode="retro")}
        assert options == {"files": False, "copy": True}
        assert LOCAL_DESTINATIONS == {"copy"}

    def test_notion_appears_configured_but_blocked(self, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "t")
        notion = next(o for o in destination_options(mode="retro") if o["key"] == "notion")
        # A token is enough to offer it and not enough to publish with it: the
        # blocker is what the surface shows instead of failing after the click.
        assert notion["blocked"]

    def test_notion_unblocks_once_a_page_exists(self, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "t")
        monkeypatch.setenv("NOTION_EXPORT_PARENT_PAGE_ID", "pg1")
        notion = next(o for o in destination_options(mode="retro") if o["key"] == "notion")
        assert notion["blocked"] == ""

    def test_extras_are_described_and_never_blocked(self):
        options = destination_options(mode="poker", extras=["jira"])
        jira = next(o for o in options if o["key"] == "jira")
        assert jira["description"] == "Send to jira"
        assert jira["blocked"] == ""

    def test_mode_names_the_files_subdirectory(self):
        files = next(o for o in destination_options(mode="standup") if o["key"] == "files")
        assert files["description"].endswith("/standup")


class TestBlocker:
    def test_files_and_copy_are_never_blocked(self):
        assert destination_blocker("files") == ""
        assert destination_blocker("copy") == ""

    def test_confluence_needs_a_space_key(self):
        assert destination_blocker("confluence")

    def test_an_unknown_key_is_not_blocked(self):
        assert destination_blocker("powerpoint") == ""


class TestMaskedNote:
    def test_none_renders_exactly_as_unmasked(self):
        from yeaboi.anonymize.apply import masked_note

        assert masked_note(None) == ""

    def test_the_count_and_the_warning_both_appear(self):
        from yeaboi.agent.state import AnonymizedOutput
        from yeaboi.anonymize.apply import masked_note

        note = masked_note(AnonymizedOutput(replacements=[("Acme", "Company A"), ("Ada", "Engineer 1")]))
        assert "2 masked" in note and "review before sharing" in note
