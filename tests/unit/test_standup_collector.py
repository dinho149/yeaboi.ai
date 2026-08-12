"""Unit tests for the standup recent-activity collector."""

from yeaboi.standup import collector
from yeaboi.standup.collector import (
    SOURCE_GITHUB,
    SOURCE_JIRA,
    SOURCE_LOCAL_GIT,
    SOURCE_NOTION,
    ActivityBundle,
    collect_recent_activity,
)


class TestActivityBundle:
    def test_authors_dedup_preserves_order(self):
        b = ActivityBundle(
            items=[
                {"author": "Alice"},
                {"author": "Bob"},
                {"author": "Alice"},
                {"author": ""},
                {"author": "  "},
            ]
        )
        assert b.authors() == ["Alice", "Bob"]

    def test_total(self):
        b = ActivityBundle(items=[{"author": "x"}, {"author": "y"}])
        assert b.total() == 2


class TestResolveSources:
    def test_explicit_wins(self):
        got = collector._resolve_sources(
            {SOURCE_JIRA},
            jira_project="",
            azdo_project="",
            github_repo="owner/repo",
            local_repo_path="",
            confluence_space="",
        )
        assert got == {SOURCE_JIRA}

    def test_auto_enables_from_params(self):
        got = collector._resolve_sources(
            None,
            jira_project="PROJ",
            azdo_project="",
            github_repo="owner/repo",
            local_repo_path="/tmp/r",
            confluence_space="",
        )
        assert got == {SOURCE_JIRA, SOURCE_GITHUB, SOURCE_LOCAL_GIT}

    def test_auto_enables_notion_from_root(self):
        got = collector._resolve_sources(
            None,
            jira_project="",
            azdo_project="",
            github_repo="",
            local_repo_path="",
            confluence_space="",
            notion_root="root123",
        )
        assert got == {SOURCE_NOTION}


class TestCollect:
    def test_tags_source_and_counts(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.jira.jira_recent_activity",
            lambda project, days=1, since=None, **kw: [{"author": "Alice", "kind": "issue", "title": "t"}],
        )
        monkeypatch.setattr(
            "yeaboi.tools.local_git.local_git_recent_commits",
            lambda path, days=1, since=None, **kw: [{"author": "Bob", "kind": "commit", "title": "c"}],
        )
        bundle = collect_recent_activity(
            days=1,
            sources={SOURCE_JIRA, SOURCE_LOCAL_GIT},
            jira_project="PROJ",
            local_repo_path="/tmp/r",
        )
        assert bundle.total() == 2
        assert {i["source"] for i in bundle.items} == {SOURCE_JIRA, SOURCE_LOCAL_GIT}
        assert dict(bundle.counts) == {SOURCE_JIRA: 1, SOURCE_LOCAL_GIT: 1}

    def test_failing_source_does_not_abort(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", boom)
        monkeypatch.setattr(
            "yeaboi.tools.local_git.local_git_recent_commits",
            lambda path, days=1, since=None, **kw: [{"author": "Bob", "kind": "commit", "title": "c"}],
        )
        bundle = collect_recent_activity(
            sources={SOURCE_JIRA, SOURCE_LOCAL_GIT},
            jira_project="PROJ",
            local_repo_path="/tmp/r",
        )
        # jira failed → no count recorded; local git still collected.
        assert bundle.total() == 1
        assert dict(bundle.counts) == {SOURCE_LOCAL_GIT: 1}

    def test_github_merges_commits_and_prs(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.github.github_recent_commits",
            lambda repo, days=1, since=None: [{"author": "A", "kind": "commit", "title": "c"}],
        )
        monkeypatch.setattr(
            "yeaboi.tools.github.github_recent_prs",
            lambda repo, days=1, since=None: [{"author": "A", "kind": "pr", "title": "p"}],
        )
        monkeypatch.setattr("yeaboi.tools.github.github_recent_reviews", lambda repo, days=1, since=None: [])
        bundle = collect_recent_activity(sources={SOURCE_GITHUB}, github_repo="owner/repo")
        assert dict(bundle.counts) == {SOURCE_GITHUB: 2}
        assert {i["kind"] for i in bundle.items} == {"commit", "pr"}

    def test_notion_source_collected(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.notion.notion_recent_pages",
            lambda root_id, days=1, since=None: [
                {"author": "Alice", "kind": "page", "title": "Doc", "timestamp": "", "key": "1"}
            ],
        )
        bundle = collect_recent_activity(sources={SOURCE_NOTION}, notion_root="root123")
        assert dict(bundle.counts) == {SOURCE_NOTION: 1}
        assert bundle.items[0]["source"] == SOURCE_NOTION

    def test_confluence_partial_enrichment_is_preserved(self, monkeypatch):
        def partial_confluence(_space, days=1, since=None, on_partial=None, **_kwargs):
            on_partial("latest editors captured; earlier-editor enrichment incomplete for 2 page(s)")
            return [{"author": "Alice", "kind": "page", "title": "Runbook"}]

        monkeypatch.setattr("yeaboi.tools.confluence.confluence_recent_pages", partial_confluence)

        bundle = collect_recent_activity(
            sources={collector.SOURCE_CONFLUENCE},
            confluence_space="ENG",
        )

        assert dict(bundle.counts) == {collector.SOURCE_CONFLUENCE: 1}
        assert bundle.partial_sources == [
            (
                collector.SOURCE_CONFLUENCE,
                "latest editors captured; earlier-editor enrichment incomplete for 2 page(s)",
            )
        ]
        assert bundle.errors == []

    def test_no_sources_enabled_is_empty(self):
        bundle = collect_recent_activity(sources=set())
        assert bundle.total() == 0
        assert bundle.counts == []

    def test_source_auth_error_recorded(self, monkeypatch):
        from yeaboi.standup.errors import StandupSourceError

        def auth_fail(project, days=1, since=None, **kw):
            raise StandupSourceError("jira", "authentication failed — check token")

        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", auth_fail)
        monkeypatch.setattr(
            "yeaboi.tools.local_git.local_git_recent_commits",
            lambda path, days=1, since=None, **kw: [{"author": "Bob", "kind": "commit", "title": "c"}],
        )
        bundle = collect_recent_activity(
            sources={SOURCE_JIRA, SOURCE_LOCAL_GIT}, jira_project="PROJ", local_repo_path="/tmp/r"
        )
        # Jira auth error surfaced; local git still collected.
        assert bundle.errors == [("jira", "authentication failed — check token")]
        assert dict(bundle.counts) == {SOURCE_LOCAL_GIT: 1}
        assert bundle.total() == 1


class TestPreviousWorkingDayStart:
    """Window start = local midnight of the last Mon-Fri day before today."""

    def _start(self, y, m, d):
        from datetime import date

        return collector.previous_working_day_start(date(y, m, d))

    def test_saturday_reaches_friday(self):
        # Sat 2026-07-18 → Fri 2026-07-17 00:00 (weekend standups capture Friday).
        start = self._start(2026, 7, 18)
        assert (start.year, start.month, start.day) == (2026, 7, 17)
        assert (start.hour, start.minute) == (0, 0)

    def test_sunday_reaches_friday(self):
        start = self._start(2026, 7, 19)
        assert (start.month, start.day) == (7, 17)

    def test_monday_reaches_friday(self):
        # Mon 2026-07-20 → Fri 2026-07-17 (skips the whole weekend).
        start = self._start(2026, 7, 20)
        assert (start.month, start.day) == (7, 17)

    def test_midweek_is_previous_day_midnight(self):
        # Wed 2026-07-15 → Tue 2026-07-14 00:00 (full previous day + today so far).
        start = self._start(2026, 7, 15)
        assert (start.month, start.day) == (7, 14)
        assert (start.hour, start.minute) == (0, 0)

    def test_result_is_tz_aware(self):
        assert self._start(2026, 7, 15).tzinfo is not None


class TestSincePassthrough:
    def test_since_reaches_helpers(self, monkeypatch):
        from datetime import datetime

        seen: dict = {}

        def fake_jira(project, days=1, since=None, **kw):
            seen["jira"] = since
            return []

        def fake_local(path, days=1, since=None, **kw):
            seen["local_git"] = since
            return []

        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", fake_jira)
        monkeypatch.setattr("yeaboi.tools.local_git.local_git_recent_commits", fake_local)
        window_start = datetime(2026, 7, 17).astimezone()
        collect_recent_activity(
            since=window_start,
            sources={SOURCE_JIRA, SOURCE_LOCAL_GIT},
            jira_project="PROJ",
            local_repo_path="/tmp/r",
        )
        assert seen == {"jira": window_start, "local_git": window_start}


class TestSkippedSources:
    def test_auto_disabled_sources_get_reasons(self):
        bundle = collect_recent_activity(jira_project="PROJ")
        skipped = dict(bundle.skipped)
        assert "jira" not in skipped
        # Credential-shaped, not repo-shaped: a bare token is now scope enough,
        # so "no repo" is no longer the reason GitHub sits out.
        assert skipped["github"] == "GITHUB_TOKEN not set"
        assert skipped["azure_devops"] == "AZURE_DEVOPS_PROJECT not set"
        assert skipped["confluence"] == "CONFLUENCE_SPACE_KEY not set"
        assert skipped["notion"] == "NOTION_ROOT_PAGE_ID not set"
        assert skipped["local_git"] == "no repo path configured"
        # azdo_repos shares azure_devops config — one skip line, not two.
        assert "azdo_repos" not in skipped

    def test_explicit_sources_record_no_skips(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", lambda project, days=1, since=None, **kw: [])
        bundle = collect_recent_activity(sources={SOURCE_JIRA}, jira_project="PROJ")
        assert bundle.skipped == []

    def test_caller_supplied_skips_are_recorded(self, monkeypatch):
        # The engine always passes an explicit source set, so auto-detection never
        # runs for a real standup — the reasons have to be handed in, or the report
        # has no coverage story at all.
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", lambda project, days=1, since=None, **kw: [])
        bundle = collect_recent_activity(
            sources={SOURCE_JIRA},
            jira_project="PROJ",
            skipped=[("github", "not selected in setup")],
        )
        assert bundle.skipped == [("github", "not selected in setup")]

    def test_caller_supplied_skips_never_contradict_what_ran(self, monkeypatch):
        # A stale list must not claim we skipped a source we just collected.
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", lambda project, days=1, since=None, **kw: [])
        bundle = collect_recent_activity(
            sources={SOURCE_JIRA},
            jira_project="PROJ",
            skipped=[("jira", "not selected in setup"), ("github", "GITHUB_TOKEN not set")],
        )
        assert bundle.skipped == [("github", "GITHUB_TOKEN not set")]

    def test_skipped_sources_are_announced_as_progress_steps(self, monkeypatch):
        # The progress list is the only place a user watches a standup being built.
        # A source that never appears there reads as a source that found nothing.
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", lambda project, days=1, since=None, **kw: [])
        steps: list[str] = []
        collect_recent_activity(
            sources={SOURCE_JIRA},
            jira_project="PROJ",
            skipped=[("github", "not selected in setup")],
            on_progress=steps.append,
        )
        assert "GitHub · skipped — not selected in setup" in steps

    def test_missing_sdk_recorded_as_skipped(self, monkeypatch):
        def import_error(*a, **k):
            raise ImportError("No module named 'jira'")

        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", import_error)
        bundle = collect_recent_activity(sources={SOURCE_JIRA}, jira_project="PROJ")
        assert ("jira", collector.SKIP_SDK_MISSING) in bundle.skipped


class TestSourceLabel:
    """One vocabulary, four surfaces — the TUI panel, plaintext, Markdown, the web export."""

    def test_known_sources_read_as_names(self):
        assert collector.source_label("azdo_repos") == "Azure DevOps code"
        assert collector.source_label("azure_devops") == "Azure DevOps tickets"
        assert collector.source_label("github") == "GitHub"

    def test_every_collector_source_has_an_explicit_label(self):
        # A source added without one falls back to Title Case, which is how
        # "azdo_repos" read as "Azdo Repos" in the report and "Azure DevOps code"
        # in the progress steps beside it.
        missing = [s for s in collector.ALL_SOURCES if s not in collector._SOURCE_LABELS]
        assert not missing, f"no label for: {missing}"

    def test_an_unknown_source_title_cases_rather_than_leaking_the_key(self):
        # Mirrored by `sourceLabel` in the web export: a source the server knows
        # about before the bundle is rebuilt must still read as a name.
        assert collector.source_label("shiny_new_thing") == "Shiny New Thing"


class TestAzdoReposSource:
    def test_enabled_by_azdo_project(self):
        got = collector._resolve_sources(
            None,
            jira_project="",
            azdo_project="Proj",
            github_repo="",
            local_repo_path="",
            confluence_space="",
        )
        assert got == {collector.SOURCE_AZDO, collector.SOURCE_AZDO_REPOS}

    def test_merges_commits_and_prs(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.azure_devops.azdevops_recent_commits",
            lambda project, days=1, since=None, **kw: [
                {"author": "A", "kind": "commit", "title": "c", "key": "abc12345"}
            ],
        )
        monkeypatch.setattr(
            "yeaboi.tools.azure_devops.azdevops_recent_prs",
            lambda project, days=1, since=None, **kw: [{"author": "A", "kind": "pr", "title": "p", "key": "!1"}],
        )
        monkeypatch.setattr(
            "yeaboi.tools.azure_devops.azdevops_recent_reviews", lambda project, days=1, since=None, **kw: []
        )
        bundle = collect_recent_activity(sources={collector.SOURCE_AZDO_REPOS}, azdo_project="Proj")
        assert dict(bundle.counts) == {collector.SOURCE_AZDO_REPOS: 2}
        assert {i["kind"] for i in bundle.items} == {"commit", "pr"}


class TestDedupe:
    def test_pr_commit_duplicate_of_branch_commit_collapses(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.github.github_recent_commits",
            lambda repo, days=1, since=None: [{"author": "A", "kind": "commit", "title": "fix bug", "key": "abc12345"}],
        )
        monkeypatch.setattr(
            "yeaboi.tools.github.github_recent_prs",
            lambda repo, days=1, since=None: [
                {"author": "A", "kind": "pr", "title": "Fix bug", "key": "#7"},
                # Same sha arriving again via the PR's commit list, annotated title.
                {"author": "A", "kind": "commit", "title": "fix bug (PR #7)", "key": "abc12345"},
            ],
        )
        monkeypatch.setattr("yeaboi.tools.github.github_recent_reviews", lambda repo, days=1, since=None: [])
        bundle = collect_recent_activity(sources={SOURCE_GITHUB}, github_repo="owner/repo")
        kinds = [i["kind"] for i in bundle.items]
        assert kinds.count("commit") == 1
        assert kinds.count("pr") == 1

    def test_local_git_constant_key_does_not_collapse_distinct_commits(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.local_git.local_git_recent_commits",
            lambda path, days=1, since=None, **kw: [
                {"author": "A", "kind": "commit", "title": "first", "key": "local"},
                {"author": "A", "kind": "commit", "title": "second", "key": "local"},
            ],
        )
        bundle = collect_recent_activity(sources={SOURCE_LOCAL_GIT}, local_repo_path="/tmp/r")
        assert bundle.total() == 2

    def test_distinct_ticket_events_survive(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.jira.jira_recent_activity",
            lambda project, days=1, since=None, **kw: [
                {"author": "A", "kind": "update", "title": "moved PROJ-1 'x' to In Review", "key": "PROJ-1"},
                {"author": "A", "kind": "update", "title": "moved PROJ-1 'x' to Done", "key": "PROJ-1"},
            ],
        )
        bundle = collect_recent_activity(sources={SOURCE_JIRA}, jira_project="PROJ")
        assert bundle.total() == 2


class TestTotalExcludeKinds:
    def test_excludes_named_kinds(self):
        b = ActivityBundle(
            items=[
                {"author": "a", "kind": "commit"},
                {"author": "b", "kind": "wip"},
                {"author": "c", "kind": "wip"},
            ]
        )
        assert b.total() == 3
        assert b.total(exclude_kinds=("wip",)) == 1


class TestReferenceTickets:
    """Open tickets fetched as matching context for the practice rules.

    They are not activity, and the collector must keep that distinction under
    every failure mode — putting them in `items` would give them evidence rows,
    category counts and prompt tokens.
    """

    def _jira(self, monkeypatch, *, open_tickets):
        monkeypatch.setattr(
            "yeaboi.tools.jira.jira_recent_activity",
            lambda project, days=1, since=None, **kw: [{"author": "Alice", "kind": "issue", "title": "t"}],
        )
        monkeypatch.setattr("yeaboi.tools.jira.jira_open_tickets", open_tickets)

    def test_open_tickets_land_off_the_activity_stream(self, monkeypatch):
        self._jira(
            monkeypatch,
            open_tickets=lambda project, **kw: [{"kind": "ticket_context", "key": "PROJ-9", "title": "Old work"}],
        )
        bundle = collect_recent_activity(sources={SOURCE_JIRA}, jira_project="PROJ")

        assert bundle.total() == 1  # the issue only
        assert dict(bundle.counts) == {SOURCE_JIRA: 1}  # context is not counted as activity
        assert [t["key"] for t in bundle.reference_tickets] == ["PROJ-9"]
        assert bundle.reference_tickets[0]["source"] == SOURCE_JIRA

    def test_turning_practices_off_does_not_buy_the_context(self, monkeypatch):
        """The switch has to save the money, not just hide the output.

        ``ticket_context=False`` is what the engine passes when
        ``habit_detection`` is off. It must skip the open-ticket search AND stop
        asking for description/AC/DoD on the activity itself — otherwise a team
        that turned the feature off still pays two extra round trips per tracker
        on every single run.
        """
        asked: dict[str, object] = {}

        def _activity(project, days=1, since=None, **kw):
            asked["include_ticket_text"] = kw.get("include_ticket_text")
            return [{"author": "Alice", "kind": "issue", "title": "t"}]

        def _open(*a, **k):
            raise AssertionError("open tickets fetched with practice detection off")

        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _activity)
        monkeypatch.setattr("yeaboi.tools.jira.jira_open_tickets", _open)
        bundle = collect_recent_activity(sources={SOURCE_JIRA}, jira_project="PROJ", ticket_context=False)

        assert bundle.reference_tickets == []
        assert asked["include_ticket_text"] is False
        assert bundle.total() == 1  # the activity itself is untouched

    def test_a_failing_context_fetch_never_costs_the_activity(self, monkeypatch):
        # This runs inside the source fetcher, AFTER its activity succeeded —
        # letting it raise would make _run_source discard that activity.
        def boom(*a, **k):
            raise RuntimeError("jql rejected")

        self._jira(monkeypatch, open_tickets=boom)
        bundle = collect_recent_activity(sources={SOURCE_JIRA}, jira_project="PROJ")

        assert bundle.total() == 1
        assert bundle.reference_tickets == []
        assert bundle.errors == []  # nothing worth telling a user about

    def test_context_survives_the_roster_filter(self, monkeypatch):
        # _rebuild_bundle drops non-roster ACTIVITY; dropping context alongside
        # it would quietly weaken the rules' ability to excuse a change.
        from yeaboi.standup.engine import _filter_bundle_to_members

        bundle = ActivityBundle(
            items=[{"author": "Outsider", "kind": "commit", "title": "c", "source": SOURCE_JIRA}],
            counts=[(SOURCE_JIRA, 1)],
            reference_tickets=[{"kind": "ticket_context", "key": "PROJ-9"}],
        )
        filtered = _filter_bundle_to_members(bundle, {"Alice": {"alice"}})
        assert filtered.items == []
        assert [t["key"] for t in filtered.reference_tickets] == ["PROJ-9"]
