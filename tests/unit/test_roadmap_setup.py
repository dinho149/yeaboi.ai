"""Where a roadmap lives, and whether this machine can read it."""

from __future__ import annotations

from dataclasses import dataclass

from yeaboi.roadmap import setup


@dataclass
class _Project:
    name: str
    description: str
    size: str


@dataclass
class _Analysis:
    projects: tuple


class TestSourceOptions:
    def test_three_sources_in_order(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: "https://x.atlassian.net")
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: "secret")
        assert [o["key"] for o in setup.source_options()] == ["confluence", "notion", "local"]

    def test_an_unconfigured_source_stays_selectable_and_says_what_is_missing(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: "")
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: "")
        options = {o["key"]: o for o in setup.source_options()}
        assert options["confluence"]["configured"] is False
        assert "CONFLUENCE_" in options["confluence"]["hint"]
        assert "NOTION_TOKEN" in options["notion"]["hint"]

    def test_a_local_file_needs_no_credentials(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: "")
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: "")
        options = {o["key"]: o for o in setup.source_options()}
        assert options["local"]["configured"] is True

    def test_every_source_carries_the_prompt_it_asks(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: "")
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: "")
        assert all(o["prompt"] for o in setup.source_options())


class TestResolveSource:
    def test_a_confluence_url_becomes_a_page_id(self):
        source, problem = setup.resolve_source(
            "confluence", "https://x.atlassian.net/wiki/spaces/K/pages/12345/Q3-Roadmap"
        )
        assert problem == ""
        assert (source.source_type, source.locator) == ("confluence", "12345")

    def test_a_notion_url_becomes_a_page_id(self):
        source, _ = setup.resolve_source("notion", "https://notion.so/ws/Q3-" + "0" * 32)
        assert source.locator == "0" * 32

    def test_a_local_file_is_labelled_by_its_name(self, tmp_path):
        path = tmp_path / "roadmap.md"
        path.write_text("# Q3\n", encoding="utf-8")
        source, problem = setup.resolve_source("local", str(path))
        assert problem == ""
        assert (source.source_type, source.label) == ("local", "roadmap.md")

    def test_a_missing_file_is_a_status_line_not_an_exception(self, tmp_path):
        source, problem = setup.resolve_source("local", str(tmp_path / "nope.md"))
        assert source is None
        assert "File not found" in problem

    def test_a_directory_is_not_a_roadmap(self, tmp_path):
        source, problem = setup.resolve_source("local", str(tmp_path))
        assert source is None
        assert problem

    def test_an_empty_locator_asks_again(self):
        source, problem = setup.resolve_source("confluence", "   ")
        assert source is None
        assert problem == "Enter where the roadmap lives."

    def test_an_unknown_kind_is_refused_by_name(self):
        source, problem = setup.resolve_source("sharepoint", "x")
        assert source is None
        assert "sharepoint" in problem


class TestProjectChoice:
    analysis = _Analysis(
        projects=(
            _Project("Billing", "Rebuild billing", "large"),
            _Project("Search", "", "small"),
        )
    )

    def test_returns_the_intake_mode_and_the_description(self):
        mode, description = setup.project_choice(self.analysis, 0)
        assert description == "Rebuild billing"
        assert mode in ("smart", "small_project")

    def test_a_project_with_no_description_plans_from_its_name(self):
        _mode, description = setup.project_choice(self.analysis, 1)
        assert description == "Search"

    def test_the_cursor_is_clamped_to_the_list(self):
        assert setup.project_choice(self.analysis, 99)[1] == "Search"
        assert setup.project_choice(self.analysis, -5)[1] == "Rebuild billing"

    def test_nothing_to_plan_is_none(self):
        assert setup.project_choice(_Analysis(projects=()), 0) is None
        assert setup.project_choice(None, 0) is None
