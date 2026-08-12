from yeaboi.standup import categories, collector, engine
from yeaboi.standup.documentation_scope import validate_documentation_sources


def test_documentation_path_conventions():
    assert categories.is_documentation_path("README.md")
    assert categories.is_documentation_path("docs/setup.txt")
    assert categories.is_documentation_path("architecture/ADR-004.md")
    assert not categories.is_documentation_path("src/readme.py")
    assert not categories.is_documentation_path("pyproject.toml")


def test_split_activity_partitions_and_duplicates_mixed_repository_work():
    ticket = {"source": collector.SOURCE_JIRA, "kind": "update", "title": "Moved PSOT-1"}
    docs_only = {
        "source": collector.SOURCE_GITHUB,
        "kind": "commit",
        "title": "Update guide",
        "changed_files": ["docs/guide.md"],
    }
    mixed = {
        "source": collector.SOURCE_AZDO_REPOS,
        "kind": "pr",
        "title": "Add API and guide",
        "changed_files": ["src/api.py", "README.md"],
    }
    confluence = {"source": collector.SOURCE_CONFLUENCE, "kind": "page", "title": "Runbook"}

    split = categories.split_activity([ticket, docs_only, mixed, confluence])

    assert split["ticketing"] == [ticket]
    assert split["code"] == [mixed]
    assert split["documentation"] == [docs_only, mixed, confluence]


def test_unknown_repository_paths_stay_code_only():
    event = {"source": collector.SOURCE_GITHUB, "kind": "commit", "title": "Work"}
    split = categories.split_activity([event])
    assert split["code"] == [event]
    assert split["documentation"] == []


def test_coverage_distinguishes_configured_partial_and_missing():
    bundle = collector.ActivityBundle(
        counts=[(collector.SOURCE_JIRA, 0), (collector.SOURCE_GITHUB, 1)],
        errors=[(collector.SOURCE_CONFLUENCE, "authentication failed")],
    )
    enabled = {
        collector.SOURCE_JIRA,
        collector.SOURCE_GITHUB,
        collector.SOURCE_CONFLUENCE,
    }
    states = dict(categories.coverage_states(enabled, bundle))
    assert states == {
        "ticketing": "covered",
        "code": "covered",
        "documentation": "partial",
    }


def test_partial_enrichment_marks_documentation_partial_without_source_failure():
    bundle = collector.ActivityBundle(
        counts=[(collector.SOURCE_CONFLUENCE, 1)],
        partial_sources=[(collector.SOURCE_CONFLUENCE, "earlier editors incomplete")],
    )

    states = dict(categories.coverage_states({collector.SOURCE_CONFLUENCE}, bundle))

    assert states["documentation"] == "partial"


def test_explicit_empty_messages():
    assert categories.empty_summary("ticketing", "not_configured") == "Ticketing sources not configured."
    assert categories.empty_summary("documentation", "failed").startswith("Documentation activity unavailable")


def test_is_empty_state_recognises_droppable_sentences_and_nothing_else():
    for category in categories.CATEGORIES:
        for coverage in ("covered", "partial", "not_configured"):
            assert categories.is_empty_state(categories.empty_summary(category, coverage))
    # FAILED is not droppable: "we could not look" is per-member news, and a
    # member folded into a "No activity detected" strip on a 401 day would be
    # a positive claim nobody verified.
    for category in categories.CATEGORIES:
        assert not categories.is_empty_state(categories.empty_summary(category, "failed"))
    # Whitespace tolerated; bespoke prose and near-misses are not droppable.
    assert categories.is_empty_state("  No code activity detected in the selected repositories. ")
    assert not categories.is_empty_state("No code activity detected today.")
    assert not categories.is_empty_state("Nothing merged, two reviews pending.")
    assert not categories.is_empty_state("")


def test_documentation_source_validation_and_saved_resolution():
    assert validate_documentation_sources(["notion", "confluence", "notion"]) == [
        "notion",
        "confluence",
    ]
    source_params = {"confluence_space": "ENG", "notion_root": "root"}
    assert engine._resolve_documentation_sources(None, None, source_params) == [
        "confluence",
        "notion",
    ]
    assert (
        engine._resolve_documentation_sources(
            {"documentation_scope_configured": True, "documentation_sources": []},
            None,
            source_params,
        )
        == []
    )


def test_notion_token_enables_workspace_wide_standup_without_root(monkeypatch):
    monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_confluence_space_key", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_notion_root_page_id", lambda: None)
    monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: "token")
    monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "")

    params = engine._resolve_source_params(None)
    sources = engine._collector_sources(params, [], [], ["notion"])

    assert params["notion_root"] == "workspace"
    assert collector.SOURCE_NOTION in sources


def test_fallback_builds_all_structured_sections():
    grouped = {
        "Alice": [
            {"source": "jira", "kind": "update", "title": "Moved PSOT-1 to review"},
            {
                "source": "github",
                "kind": "pr",
                "title": "Add API and guide",
                "changed_files": ["src/api.py", "docs/api.md"],
            },
            {"source": "confluence", "kind": "page", "title": "Updated support runbook"},
        ]
    }
    update = engine._build_fallback_member_updates(grouped, {})[0]
    assert "Moved PSOT-1" in update.summary
    assert update.ticketing_summary == "Moved PSOT-1 to review"
    assert update.code_summary == "Add API and guide"
    assert update.documentation_summary == "Add API and guide; Updated support runbook"
