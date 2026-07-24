"""Repository scope and separate Standup code-update coverage."""

from types import SimpleNamespace

from yeaboi.standup import collector, engine
from yeaboi.standup.code_scope import (
    discover_azdo_projects,
    discover_azdo_repositories,
    discover_github_repositories,
    validate_code_sources,
)
from yeaboi.standup.store import StandupStore
from yeaboi.ui import mode_select


def test_validate_code_sources_is_stable_and_rejects_unknown():
    assert validate_code_sources(["azure_devops", "github", "github"]) == ["github", "azure_devops"]
    try:
        validate_code_sources(["gitlab"])
    except ValueError as exc:
        assert "gitlab" in str(exc)
    else:
        raise AssertionError("unknown provider should fail")


def test_github_repository_discovery_includes_accessible_and_legacy(monkeypatch):
    repos = [SimpleNamespace(full_name="acme/api"), SimpleNamespace(full_name="acme/web")]
    client = SimpleNamespace(get_user=lambda: SimpleNamespace(get_repos=lambda **kwargs: repos))
    monkeypatch.setattr("yeaboi.tools.github._get_github_client", lambda: client)
    monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "acme/legacy")

    assert discover_github_repositories() == ["acme/api", "acme/legacy", "acme/web"]


def test_azdo_repository_discovery_spans_projects_and_prioritizes_configured(monkeypatch):
    core = SimpleNamespace(get_projects=lambda: [SimpleNamespace(name="Other"), SimpleNamespace(name="Core")])
    git = SimpleNamespace(get_repositories=lambda project: [SimpleNamespace(name=f"{project}-repo")])
    connection = SimpleNamespace(clients=SimpleNamespace(get_core_client=lambda: core, get_git_client=lambda: git))
    monkeypatch.setattr("yeaboi.config.get_azure_devops_org_url", lambda: "https://dev.azure.com/acme")
    monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "Core")
    monkeypatch.setattr("yeaboi.config.get_azure_devops_token", lambda: "token")
    monkeypatch.setattr("yeaboi.tools.azure_devops._make_connection", lambda *args: connection)

    assert discover_azdo_repositories() == ["Core/Core-repo", "Other/Other-repo"]
    assert discover_azdo_projects() == ["Core", "Other"]


def test_collector_collects_commits_prs_and_reviews_from_each_github_repo(monkeypatch):
    monkeypatch.setattr(
        "yeaboi.tools.github.github_recent_commits",
        lambda repo, **kwargs: [{"author": "Alice", "kind": "commit", "title": f"commit {repo}", "key": repo}],
    )
    monkeypatch.setattr(
        "yeaboi.tools.github.github_recent_prs",
        lambda repo, **kwargs: [{"author": "Alice", "kind": "pr", "title": f"PR {repo}", "key": f"pr-{repo}"}],
    )
    monkeypatch.setattr(
        "yeaboi.tools.github.github_recent_reviews",
        lambda repo, **kwargs: [
            {"author": "Bob", "kind": "review", "title": f"review {repo}", "key": f"review-{repo}"}
        ],
    )

    bundle = collector.collect_recent_activity(
        sources={collector.SOURCE_GITHUB},
        github_repositories=["acme/api", "acme/web"],
    )

    assert len(bundle.items) == 6
    assert {item["repository"] for item in bundle.items} == {"acme/api", "acme/web"}
    assert dict(bundle.counts)["github"] == 6


def test_fallback_member_update_separates_code_from_work_summary():
    grouped = {
        "Alice": [
            {"source": "jira", "kind": "update", "title": "Moved PSOT-1 to review"},
            {
                "source": "github",
                "kind": "pr",
                "title": "Merged authentication",
                "key": "#12",
                "url": "https://github.com/acme/api/pull/12",
            },
        ]
    }

    update = engine._build_fallback_member_updates(grouped, {})[0]

    assert update.summary == "Moved PSOT-1 to review; Merged authentication"
    assert update.code_summary == "Merged authentication"
    assert update.code_activity_count == 1
    assert update.code_links == (("#12", "https://github.com/acme/api/pull/12"),)


def test_code_scope_round_trips_in_store(tmp_path):
    db = tmp_path / "sessions.db"
    with StandupStore(db) as store:
        store.save_config(
            "s1",
            enabled=False,
            time="10:00",
            weekdays="1-5",
            delivery_channels=["terminal"],
            code_sources=["github", "azure_devops"],
            github_repositories=["acme/api"],
            azdo_projects=["Core"],
            azdo_repositories=["Core/service"],
            code_scope_configured=True,
        )
        config = store.load_config("s1")

    assert config["code_sources"] == ["github", "azure_devops"]
    assert config["github_repositories"] == ["acme/api"]
    assert config["azdo_projects"] == ["Core"]
    assert config["azdo_repositories"] == ["Core/service"]
    assert config["code_scope_configured"] is True


def test_code_picker_persists_explicit_repository_scope(monkeypatch, tmp_path):
    db = tmp_path / "sessions.db"
    monkeypatch.setattr(mode_select, "_ana_dbp", db)
    monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "token")
    monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_azure_devops_org_url", lambda: "https://dev.azure.com/acme")
    monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "Core")
    monkeypatch.setattr(mode_select, "_run_standup_source_select", lambda *args, **kwargs: ["github", "azure_devops"])
    monkeypatch.setattr(
        mode_select,
        "_run_standup_member_select",
        lambda *args, **kwargs: ["GitHub · acme/api", "Azure DevOps · Core"],
    )
    monkeypatch.setattr(
        "yeaboi.standup.code_scope.discover_code_repositories",
        lambda sources: {"github": ["acme/api"], "azure_devops": ["Core"]},
    )

    ok, message = mode_select._standup_code_configure(
        SimpleNamespace(size=(100, 36)),
        SimpleNamespace(update=lambda renderable: None),
        lambda **kwargs: "",
        0.001,
        True,
        "s1",
    )

    assert ok is True
    assert "1 GitHub repo(s), 1 Azure project(s)" in message
    with StandupStore(db) as store:
        config = store.load_config("s1")
    assert config["github_repositories"] == ["acme/api"]
    assert config["azdo_projects"] == ["Core"]
    assert config["azdo_repositories"] == []


def test_collector_expands_each_selected_azure_project(monkeypatch):
    calls = []

    def _items(project, **kwargs):
        calls.append(project)
        return [{"author": "Alice", "kind": "commit", "title": project, "key": project}]

    monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_recent_commits", _items)
    monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_recent_prs", lambda project, **kwargs: [])
    monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_recent_reviews", lambda project, **kwargs: [])

    bundle = collector.collect_recent_activity(
        sources={collector.SOURCE_AZDO_REPOS},
        azdo_projects=["Core", "Platform"],
    )

    assert set(calls) == {"Core", "Platform"}
    assert {item["title"] for item in bundle.items} == {"Core", "Platform"}
