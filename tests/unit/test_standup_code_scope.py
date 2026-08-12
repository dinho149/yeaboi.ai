"""Repository scope and separate Standup code-update coverage."""

from types import SimpleNamespace

from yeaboi.standup import collector, engine
from yeaboi.standup.code_scope import (
    discover_azdo_projects,
    discover_azdo_repositories,
    discover_github_owners,
    discover_github_repositories,
    expand_github_owners,
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
        lambda *args, **kwargs: ["GitHub · acme", "Azure DevOps · Core"],
    )
    monkeypatch.setattr(
        "yeaboi.standup.code_scope.discover_code_repositories",
        lambda sources: {"github": ["acme"], "azure_devops": ["Core"]},
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
    assert "1 GitHub org(s), 1 Azure project(s)" in message
    with StandupStore(db) as store:
        config = store.load_config("s1")
    assert config["github_owners"] == ["acme"]
    # Cleared, not merged: the picker speaks in owners now, and a leftover repo
    # list would quietly union an older, narrower pick into the new scope.
    assert config["github_repositories"] == []
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


def test_github_owner_discovery_lists_orgs_and_survives_a_broken_token(monkeypatch):
    monkeypatch.setattr("yeaboi.tools.github.github_list_owners", lambda limit=100: ["acme", "dinho"])
    assert discover_github_owners() == ["acme", "dinho"]

    def _boom(limit=100):
        raise RuntimeError("bad credentials")

    monkeypatch.setattr("yeaboi.tools.github.github_list_owners", _boom)
    # An unusable token empties the picker rather than crashing setup.
    assert discover_github_owners() == []


def test_owner_expansion_keeps_active_repos_and_reports_a_failed_owner(monkeypatch):
    seen = {}

    def _inventory(owners, days=120, *, include_trees=True):
        seen["owners"] = list(owners)
        seen["days"] = days
        seen["include_trees"] = include_trees
        return [
            {"name": "acme/api", "active": True},
            {"name": "acme/web", "active": True},
            {"name": "acme/old", "active": False, "skip_reason": "archived repository"},
            # Duplicate slug in a different case — GitHub slugs are case-insensitive.
            {"name": "Acme/API", "active": True},
            {"container": "ghost", "name": "ghost", "active": True, "discovery_error": True, "error": "404"},
        ]

    monkeypatch.setattr("yeaboi.tools.github.github_analysis_inventory", _inventory)
    repositories, warnings = expand_github_owners(["acme", "ghost"], days=1)

    assert repositories == ["acme/api", "acme/web"]
    assert warnings == ["GitHub owner ghost: 404"]
    assert seen["owners"] == ["acme", "ghost"]
    # A one-day standup still looks back far enough to keep a repo whose only
    # activity today was a review (pushed_at does not move for those).
    assert seen["days"] == 14
    assert seen["include_trees"] is False


def test_owner_expansion_degrades_to_a_warning_when_discovery_raises(monkeypatch):
    def _boom(owners, days=120, *, include_trees=True):
        raise RuntimeError("rate limited")

    monkeypatch.setattr("yeaboi.tools.github.github_analysis_inventory", _boom)
    repositories, warnings = expand_github_owners(["acme"], days=1)

    assert repositories == []
    assert warnings and "rate limited" in warnings[0]


def test_expanding_no_owners_makes_no_api_call(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("should not be called")

    monkeypatch.setattr("yeaboi.tools.github.github_analysis_inventory", _boom)
    assert expand_github_owners([], days=1) == ([], [])
    assert expand_github_owners(None, days=1) == ([], [])


def test_collector_fans_an_owner_out_to_every_repo_inside_it(monkeypatch):
    monkeypatch.setattr(
        "yeaboi.standup.code_scope.expand_github_owners",
        lambda owners, *, days: (["acme/api", "acme/web"], []),
    )
    monkeypatch.setattr(
        "yeaboi.tools.github.github_recent_commits",
        lambda repo, **kwargs: [{"author": "Alice", "kind": "commit", "title": repo, "key": repo}],
    )
    monkeypatch.setattr("yeaboi.tools.github.github_recent_prs", lambda repo, **kwargs: [])
    monkeypatch.setattr("yeaboi.tools.github.github_recent_reviews", lambda repo, **kwargs: [])

    bundle = collector.collect_recent_activity(
        sources={collector.SOURCE_GITHUB},
        github_owners=["acme"],
    )

    assert {item["title"] for item in bundle.items} == {"acme/api", "acme/web"}
    assert {item["repository"] for item in bundle.items} == {"acme/api", "acme/web"}


def test_collector_unions_pinned_repos_with_the_owner_fan_out(monkeypatch):
    monkeypatch.setattr(
        "yeaboi.standup.code_scope.expand_github_owners",
        # "Acme/API" repeats the pinned repo in a different case.
        lambda owners, *, days: (["acme/web", "Acme/API"], ["GitHub owner ghost: 404"]),
    )
    monkeypatch.setattr(
        "yeaboi.tools.github.github_recent_commits",
        lambda repo, **kwargs: [{"author": "Alice", "kind": "commit", "title": repo, "key": repo}],
    )
    monkeypatch.setattr("yeaboi.tools.github.github_recent_prs", lambda repo, **kwargs: [])
    monkeypatch.setattr("yeaboi.tools.github.github_recent_reviews", lambda repo, **kwargs: [])

    bundle = collector.collect_recent_activity(
        sources={collector.SOURCE_GITHUB},
        github_owners=["acme", "ghost"],
        github_repositories=["acme/api"],
    )

    assert {item["title"] for item in bundle.items} == {"acme/api", "acme/web"}
    # A failed owner is surfaced, not swallowed into "nothing happened today".
    assert ("github", "GitHub owner ghost: 404") in bundle.errors


def test_owner_scope_round_trips_through_the_store(tmp_path):
    with StandupStore(tmp_path / "sessions.db") as store:
        store.save_config(
            "s1",
            enabled=False,
            time="10:00",
            weekdays="1-5",
            delivery_channels=["terminal"],
            code_sources=["github"],
            github_owners=["acme", "dinho"],
            code_scope_configured=True,
        )
        config = store.load_config("s1")

    assert config["github_owners"] == ["acme", "dinho"]


def test_saved_repositories_never_become_owners_behind_the_users_back(tmp_path):
    """A narrow repo scope stays narrow — widening it is the picker's job."""
    with StandupStore(tmp_path / "sessions.db") as store:
        store.save_config(
            "s1",
            enabled=False,
            time="10:00",
            weekdays="1-5",
            delivery_channels=["terminal"],
            code_sources=["github"],
            github_repositories=["acme/api"],
            code_scope_configured=True,
        )
        config = store.load_config("s1")

    assert config["github_owners"] == []
    assert config["github_repositories"] == ["acme/api"]


def test_picker_leaves_a_saved_repo_scope_narrow_and_intact(monkeypatch, tmp_path):
    """Opening Configure must not widen one repo into its whole org.

    The row reads "GitHub · acme" and says nothing about replacing a one-repo
    scope, so pre-ticking it would turn "add an Azure project" into an org-wide
    standup on a keypress. Nothing is lost by leaving it unticked: the repository
    is preserved because no chosen owner covers it.
    """
    db = tmp_path / "sessions.db"
    with StandupStore(db) as store:
        store.save_config(
            "s1",
            enabled=False,
            time="10:00",
            weekdays="1-5",
            delivery_channels=["terminal"],
            code_sources=["github"],
            github_repositories=["acme/api"],
            code_scope_configured=True,
        )
    monkeypatch.setattr(mode_select, "_ana_dbp", db)
    monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "token")
    monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_azure_devops_org_url", lambda: "")
    monkeypatch.setattr(mode_select, "_run_standup_source_select", lambda *args, **kwargs: ["github"])
    monkeypatch.setattr(
        "yeaboi.standup.code_scope.discover_code_repositories",
        lambda sources: {"github": ["acme", "other"], "azure_devops": []},
    )
    preselected = {}

    def _member_select(live, console, read_key, frame_time, supports_timeout, choices, initial, **kwargs):
        preselected["initial"] = list(initial)
        # The user changes nothing on the GitHub side.
        return list(initial) or ["GitHub · other"]

    monkeypatch.setattr(mode_select, "_run_standup_member_select", _member_select)

    ok, _message = mode_select._standup_code_configure(
        SimpleNamespace(size=(100, 36)),
        SimpleNamespace(update=lambda renderable: None),
        lambda **kwargs: "",
        0.001,
        True,
        "s1",
    )

    assert ok is True
    assert preselected["initial"] == []
    with StandupStore(db) as store:
        config = store.load_config("s1")
    # "other" was picked; "acme" was not, so acme/api stays pinned rather than
    # being wiped by the save.
    assert config["github_owners"] == ["other"]
    assert config["github_repositories"] == ["acme/api"]


def test_picking_an_owner_absorbs_its_own_pinned_repositories(monkeypatch, tmp_path):
    """Choosing "acme" covers acme/api — keeping it would be a redundant entry."""
    db = tmp_path / "sessions.db"
    with StandupStore(db) as store:
        store.save_config(
            "s1",
            enabled=False,
            time="10:00",
            weekdays="1-5",
            delivery_channels=["terminal"],
            code_sources=["github"],
            github_repositories=["acme/api", "zeta/tools"],
            code_scope_configured=True,
        )
    monkeypatch.setattr(mode_select, "_ana_dbp", db)
    monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "token")
    monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_azure_devops_org_url", lambda: "")
    monkeypatch.setattr(mode_select, "_run_standup_source_select", lambda *args, **kwargs: ["github"])
    monkeypatch.setattr(
        "yeaboi.standup.code_scope.discover_code_repositories",
        lambda sources: {"github": ["acme"], "azure_devops": []},
    )
    monkeypatch.setattr(mode_select, "_run_standup_member_select", lambda *args, **kwargs: ["GitHub · acme"])

    ok, message = mode_select._standup_code_configure(
        SimpleNamespace(size=(100, 36)),
        SimpleNamespace(update=lambda renderable: None),
        lambda **kwargs: "",
        0.001,
        True,
        "s1",
    )

    assert ok is True
    with StandupStore(db) as store:
        config = store.load_config("s1")
    assert config["github_owners"] == ["acme"]
    # zeta/tools has no chosen owner behind it, so it survives; acme/api does not
    # need to, because "acme" already covers it.
    assert config["github_repositories"] == ["zeta/tools"]
    assert "1 pinned repo(s)" in message


def test_a_legacy_pinned_repo_survives_the_first_setup_walk(monkeypatch, tmp_path):
    """STANDUP_GITHUB_REPO is an explicit narrow scope, not a starting suggestion.

    Once code_scope_configured flips true the engine stops consulting the env
    default, so the pin has to be persisted here or it is silently dropped.
    """
    db = tmp_path / "sessions.db"
    monkeypatch.setattr(mode_select, "_ana_dbp", db)
    monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "token")
    monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "acme/api")
    monkeypatch.setattr("yeaboi.config.get_azure_devops_org_url", lambda: "")
    monkeypatch.setattr(mode_select, "_run_standup_source_select", lambda *args, **kwargs: ["github"])
    monkeypatch.setattr(
        "yeaboi.standup.code_scope.discover_code_repositories",
        lambda sources: {"github": ["acme", "other"], "azure_devops": []},
    )
    preselected = {}

    def _member_select(live, console, read_key, frame_time, supports_timeout, choices, initial, **kwargs):
        preselected["initial"] = list(initial)
        return ["GitHub · other"]

    monkeypatch.setattr(mode_select, "_run_standup_member_select", _member_select)

    ok, _message = mode_select._standup_code_configure(
        SimpleNamespace(size=(100, 36)),
        SimpleNamespace(update=lambda renderable: None),
        lambda **kwargs: "",
        0.001,
        True,
        "s1",
    )

    assert ok is True
    # A pin means "just this" — it does not pre-tick every visible org.
    assert preselected["initial"] == []
    with StandupStore(db) as store:
        config = store.load_config("s1")
    assert config["github_repositories"] == ["acme/api"]


def test_first_walk_with_no_pin_pre_ticks_every_visible_org(monkeypatch, tmp_path):
    """Matches what an unconfigured run already scans, so setup changes nothing."""
    db = tmp_path / "sessions.db"
    monkeypatch.setattr(mode_select, "_ana_dbp", db)
    monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "token")
    monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_azure_devops_org_url", lambda: "")
    monkeypatch.setattr(mode_select, "_run_standup_source_select", lambda *args, **kwargs: ["github"])
    monkeypatch.setattr(
        "yeaboi.standup.code_scope.discover_code_repositories",
        lambda sources: {"github": ["acme", "other"], "azure_devops": []},
    )
    preselected = {}

    def _member_select(live, console, read_key, frame_time, supports_timeout, choices, initial, **kwargs):
        preselected["initial"] = list(initial)
        return list(initial)

    monkeypatch.setattr(mode_select, "_run_standup_member_select", _member_select)

    mode_select._standup_code_configure(
        SimpleNamespace(size=(100, 36)),
        SimpleNamespace(update=lambda renderable: None),
        lambda **kwargs: "",
        0.001,
        True,
        "s1",
    )

    assert preselected["initial"] == ["GitHub · acme", "GitHub · other"]


def test_owner_expansion_caps_a_large_org_and_says_what_it_dropped(monkeypatch):
    """A 500-repo org would be 1500 sequential API calls on the standup path."""
    from yeaboi.standup import code_scope

    def _inventory(owners, days=120, *, include_trees=True):
        return [
            {"container": "acme", "name": f"acme/r{i:03d}", "active": True, "updated_at": f"2026-08-{i % 28 + 1:02d}"}
            for i in range(40)
        ]

    monkeypatch.setattr("yeaboi.tools.github.github_analysis_inventory", _inventory)
    repositories, warnings = expand_github_owners(["acme"], days=1)

    assert len(repositories) == code_scope._MAX_REPOS_PER_OWNER
    # Most recently pushed survive the cap — the ones today's standup is about.
    assert repositories[0] == "acme/r027"
    assert warnings and "30 skipped" in warnings[0] and "acme (10 of 40)" in warnings[0]


def test_one_busy_owner_does_not_starve_the_others(monkeypatch):
    def _inventory(owners, days=120, *, include_trees=True):
        rows = [
            {"container": "busy", "name": f"busy/r{i:03d}", "active": True, "updated_at": "2026-08-12"}
            for i in range(30)
        ]
        rows.append({"container": "quiet", "name": "quiet/api", "active": True, "updated_at": "2026-08-01"})
        return rows

    monkeypatch.setattr("yeaboi.tools.github.github_analysis_inventory", _inventory)
    repositories, _warnings = expand_github_owners(["busy", "quiet"], days=1)

    assert "quiet/api" in repositories
    assert sum(1 for repo in repositories if repo.startswith("busy/")) == 10


def test_collector_measures_the_window_from_an_aware_since(monkeypatch):
    """Every real standup passes ``since=``; ``days`` is only the legacy path."""
    from datetime import datetime, timedelta, timezone

    seen = {}

    def _expand(owners, *, days):
        seen["days"] = days
        return [], []

    monkeypatch.setattr("yeaboi.standup.code_scope.expand_github_owners", _expand)
    since = datetime.now(timezone(timedelta(hours=2))) - timedelta(days=3)

    collector.collect_recent_activity(
        sources={collector.SOURCE_GITHUB},
        github_owners=["acme"],
        since=since,
    )

    assert seen["days"] == 3


def test_collector_resolves_a_bare_token_to_every_visible_owner(monkeypatch):
    """The auto case the engine deliberately leaves unresolved."""
    monkeypatch.setattr("yeaboi.standup.code_scope.discover_github_owners", lambda: ["acme"])
    monkeypatch.setattr(
        "yeaboi.standup.code_scope.expand_github_owners",
        lambda owners, *, days: ([f"{owners[0]}/api"], []),
    )
    monkeypatch.setattr(
        "yeaboi.tools.github.github_recent_commits",
        lambda repo, **kwargs: [{"author": "Alice", "kind": "commit", "title": repo, "key": repo}],
    )
    monkeypatch.setattr("yeaboi.tools.github.github_recent_prs", lambda repo, **kwargs: [])
    monkeypatch.setattr("yeaboi.tools.github.github_recent_reviews", lambda repo, **kwargs: [])

    bundle = collector.collect_recent_activity(sources={collector.SOURCE_GITHUB})

    assert {item["title"] for item in bundle.items} == {"acme/api"}


def test_a_token_that_can_list_nothing_says_so_instead_of_going_quiet(monkeypatch):
    """Zero owners is a real answer about the token, not a quiet day."""
    monkeypatch.setattr("yeaboi.standup.code_scope.discover_github_owners", lambda: [])

    def _boom(*args, **kwargs):
        raise AssertionError("nothing to expand")

    monkeypatch.setattr("yeaboi.standup.code_scope.expand_github_owners", _boom)

    bundle = collector.collect_recent_activity(sources={collector.SOURCE_GITHUB})

    assert bundle.items == []
    assert any("could not list any organisation" in message for _source, message in bundle.errors)


def test_a_pinned_repo_suppresses_auto_discovery(monkeypatch):
    def _boom():
        raise AssertionError("a pinned repo is an explicit scope — do not widen it")

    monkeypatch.setattr("yeaboi.standup.code_scope.discover_github_owners", _boom)
    monkeypatch.setattr(
        "yeaboi.tools.github.github_recent_commits",
        lambda repo, **kwargs: [{"author": "Alice", "kind": "commit", "title": repo, "key": repo}],
    )
    monkeypatch.setattr("yeaboi.tools.github.github_recent_prs", lambda repo, **kwargs: [])
    monkeypatch.setattr("yeaboi.tools.github.github_recent_reviews", lambda repo, **kwargs: [])

    bundle = collector.collect_recent_activity(
        sources={collector.SOURCE_GITHUB},
        github_repo="acme/api",
    )

    assert {item["title"] for item in bundle.items} == {"acme/api"}
