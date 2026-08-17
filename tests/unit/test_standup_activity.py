"""Unit tests for the recent-activity helpers used by the Daily Standup collector.

Each source degrades gracefully to [] when unconfigured or on error, and
normalizes into the shared {author, kind, title, timestamp, key} shape.
"""

import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from yeaboi.tools.azure_devops import azdevops_recent_activity
from yeaboi.tools.confluence import confluence_recent_pages
from yeaboi.tools.github import github_recent_commits, github_recent_prs
from yeaboi.tools.jira import jira_recent_activity
from yeaboi.tools.local_git import git_subprocess_env, local_git_recent_commits


@pytest.fixture(autouse=True)
def _clean_git_env(monkeypatch):
    """Scrub git env vars that hook runners (pre-commit) export to children.

    GIT_DIR / GIT_INDEX_FILE pointing at the parent repo would redirect this
    module's temp-repo `git` subprocesses (commits fail; a non-repo dir
    resolves to the parent repo), so the tests behave differently under
    `make test` vs a commit hook. Delete them for a hermetic environment.
    """
    for var in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_PREFIX"):
        monkeypatch.delenv(var, raising=False)


class TestJiraRecentActivity:
    def test_missing_config_returns_empty(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: None)
        assert jira_recent_activity("PROJ", days=1) == []

    def test_normalizes_issues(self, monkeypatch):
        issue = MagicMock()
        issue.key = "PROJ-12"
        issue.fields = SimpleNamespace(
            summary="Fix login",
            assignee=SimpleNamespace(displayName="Alice"),
            status=SimpleNamespace(name="In Progress"),
            updated="2026-07-10T09:00:00.000+0000",
        )
        client = MagicMock()
        client.search_issues.return_value = [issue]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_project_key", lambda: "PROJ")
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_base_url", lambda: "https://x.atlassian.net")

        items = jira_recent_activity("PROJ", days=2)
        assert items == [
            {
                "author": "Alice",
                "author_email": "",
                "kind": "issue",
                "title": "Fix login",
                "status": "In Progress",
                "timestamp": "2026-07-10T09:00:00",
                "key": "PROJ-12",
                "url": "https://x.atlassian.net/browse/PROJ-12",
                # Hierarchy defaults when the instance returns no issuetype/parent.
                "issue_type": "",
                "subtask": False,
                "parent_key": "",
            }
        ]

    def test_api_error_returns_empty(self, monkeypatch):
        from jira import JIRAError

        client = MagicMock()
        client.search_issues.side_effect = JIRAError(status_code=500, text="boom")
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_project_key", lambda: "PROJ")
        assert jira_recent_activity("PROJ") == []

    def test_auth_error_raises_source_error(self, monkeypatch):
        from jira import JIRAError

        from yeaboi.standup.errors import StandupSourceError

        client = MagicMock()
        client.search_issues.side_effect = JIRAError(status_code=401, text="unauthorized")
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_project_key", lambda: "PROJ")
        with pytest.raises(StandupSourceError) as exc:
            jira_recent_activity("PROJ")
        assert exc.value.source == "jira"


class TestGithubRecentActivity:
    def test_commits_normalized(self, monkeypatch):
        commit_obj = SimpleNamespace(
            sha="abcdef1234",
            files=[SimpleNamespace(filename="docs/guide.md"), SimpleNamespace(filename="src/app.py")],
            commit=SimpleNamespace(
                author=SimpleNamespace(name="Bob", date=datetime(2026, 7, 10, 8, 0, tzinfo=UTC)),
                message="Add feature\n\nbody",
            ),
        )
        repo = MagicMock()
        repo.get_commits.return_value = [commit_obj]
        client = MagicMock()
        client.get_repo.return_value = repo
        monkeypatch.setattr("yeaboi.tools.github._get_github_client", lambda: client)

        items = github_recent_commits("owner/repo", days=1)
        assert items[0]["author"] == "Bob"
        assert items[0]["kind"] == "commit"
        assert items[0]["title"] == "Add feature"
        assert items[0]["key"] == "abcdef12"
        assert items[0]["changed_files"] == ["docs/guide.md", "src/app.py"]

    def test_commits_error_returns_empty(self, monkeypatch):
        client = MagicMock()
        client.get_repo.side_effect = RuntimeError("nope")
        monkeypatch.setattr("yeaboi.tools.github._get_github_client", lambda: client)
        assert github_recent_commits("owner/repo") == []

    def test_prs_normalized_and_merged_status(self, monkeypatch):
        pr = SimpleNamespace(
            number=42,
            title="Refactor",
            merged=True,
            state="closed",
            user=SimpleNamespace(login="carol"),
            # Relative to now so the PR always falls inside the days=1 window —
            # a fixed date made this test fail once the clock passed it.
            updated_at=datetime.now(UTC) - timedelta(hours=1),
            get_files=lambda: [SimpleNamespace(filename="README.md")],
        )
        repo = MagicMock()
        repo.get_pulls.return_value = [pr]
        client = MagicMock()
        client.get_repo.return_value = repo
        monkeypatch.setattr("yeaboi.tools.github._get_github_client", lambda: client)

        items = github_recent_prs("owner/repo", days=1)
        assert items[0]["status"] == "merged"
        assert items[0]["key"] == "#42"
        assert items[0]["author"] == "carol"
        assert items[0]["changed_files"] == ["README.md"]


class TestAzdoRecentActivity:
    def test_no_project_returns_empty(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.azure_devops.get_azure_devops_project", lambda: None)
        assert azdevops_recent_activity("", days=1) == []

    def test_normalizes_work_items(self, monkeypatch):
        wit = MagicMock()
        wit.query_by_wiql.return_value = SimpleNamespace(work_items=[SimpleNamespace(id=7)])
        item = SimpleNamespace(
            fields={
                "System.Id": 7,
                "System.Title": "Build API",
                "System.State": "Active",
                "System.AssignedTo": {"displayName": "Dana"},
                "System.ChangedDate": "2026-07-10T06:00:00Z",
            }
        )
        wit.get_work_items.return_value = [item]
        monkeypatch.setattr("yeaboi.tools.azure_devops._make_azdo_clients", lambda: (wit, MagicMock()))

        items = azdevops_recent_activity("Proj", days=1)
        assert items[0]["author"] == "Dana"
        assert items[0]["kind"] == "work_item"
        assert items[0]["key"] == "#7"
        assert items[0]["status"] == "Active"


class TestConfluenceRecentPages:
    def test_missing_config_returns_empty(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: None)
        assert confluence_recent_pages("SPACE", days=1) == []

    def test_normalizes_pages(self, monkeypatch):
        conf = MagicMock()
        conf.cql.return_value = {
            "results": [
                {
                    "content": {
                        "id": "123",
                        "title": "Runbook",
                        "history": {"lastUpdated": {"by": {"displayName": "Eve"}, "when": "2026-07-10T05:00:00.000Z"}},
                    }
                }
            ]
        }
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: conf)
        monkeypatch.setattr("yeaboi.tools.confluence.get_confluence_space_key", lambda: "SPACE")

        items = confluence_recent_pages("SPACE", days=1)
        assert items[0]["author"] == "Eve"
        assert items[0]["kind"] == "page"
        assert items[0]["title"] == "Runbook"
        assert items[0]["key"] == "123"


class TestLocalGitRecentCommits:
    def test_reads_real_repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        def git(*args):
            # git_subprocess_env() strips GIT_DIR/GIT_INDEX_FILE — without it,
            # running this suite inside a git hook (pre-commit) would make these
            # mutating commands target the project repo instead of the tmp repo.
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=git_subprocess_env())

        git("init", "-q")
        git("config", "user.email", "dev@example.com")
        git("config", "user.name", "Dev Person")
        (repo / "a.txt").write_text("hi")
        git("add", ".")
        git("commit", "-q", "-m", "first commit")

        items = local_git_recent_commits(str(repo), days=1)
        assert len(items) == 1
        assert items[0]["author"] == "Dev Person"
        assert items[0]["title"] == "first commit"
        assert items[0]["kind"] == "commit"

    def test_non_directory_returns_empty(self):
        assert local_git_recent_commits("/no/such/path", days=1) == []

    def test_empty_path_returns_empty(self):
        assert local_git_recent_commits("", days=1) == []

    def test_non_repo_directory_returns_empty(self, tmp_path):
        assert local_git_recent_commits(str(tmp_path), days=1) == []

    def test_git_subprocess_env_strips_repo_targeting_vars(self, monkeypatch):
        monkeypatch.setenv("GIT_DIR", "/somewhere/.git")
        monkeypatch.setenv("GIT_INDEX_FILE", "/somewhere/.git/index")
        monkeypatch.setenv("GIT_WORK_TREE", "/somewhere")
        monkeypatch.setenv("PATH", "/usr/bin")  # non-GIT_ vars survive
        env = git_subprocess_env()
        assert not any(k.startswith("GIT_") for k in env)
        assert env["PATH"] == "/usr/bin"

    def test_hook_style_git_dir_env_does_not_leak_other_repo(self, tmp_path, monkeypatch):
        """Regression: with GIT_DIR exported (as inside a git hook), a non-repo
        path must still return [] — not the GIT_DIR repo's commits."""
        other = tmp_path / "other"
        other.mkdir()

        def git(*args):
            subprocess.run(["git", "-C", str(other), *args], check=True, capture_output=True, env=git_subprocess_env())

        git("init", "-q")
        git("config", "user.email", "dev@example.com")
        git("config", "user.name", "Dev Person")
        (other / "a.txt").write_text("hi")
        git("add", ".")
        git("commit", "-q", "-m", "other repo commit")

        monkeypatch.setenv("GIT_DIR", str(other / ".git"))
        empty = tmp_path / "empty"
        empty.mkdir()
        assert local_git_recent_commits(str(empty), days=1) == []


class TestSinceWindow:
    """Each helper honours an absolute `since` window start (previous working day 00:00)."""

    _SINCE = datetime(2026, 7, 17).astimezone()  # a Friday midnight, local tz

    def test_jira_uses_date_literal(self, monkeypatch):
        client = MagicMock()
        client.search_issues.return_value = []
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_project_key", lambda: "PROJ")
        jira_recent_activity("PROJ", since=self._SINCE)
        # First search is the updated-window query; later calls are the WIP scan.
        jql = client.search_issues.call_args_list[0][0][0]
        assert 'updated >= "2026-07-17"' in jql
        assert "-1d" not in jql

    def test_confluence_uses_date_literal(self, monkeypatch):
        conf = MagicMock()
        conf.cql.return_value = {"results": []}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: conf)
        monkeypatch.setattr("yeaboi.tools.confluence.get_confluence_space_key", lambda: "SPACE")
        confluence_recent_pages("SPACE", since=self._SINCE)
        cql = conf.cql.call_args[0][0]
        assert 'lastModified >= "2026-07-17"' in cql
        assert 'now("' not in cql

    def test_azdo_uses_whole_day_delta(self, monkeypatch):
        wit = MagicMock()
        wit.query_by_wiql.return_value = SimpleNamespace(work_items=[])
        monkeypatch.setattr("yeaboi.tools.azure_devops._make_azdo_clients", lambda: (wit, MagicMock()))
        from datetime import date as _date

        since = datetime.combine(_date.today() - timedelta(days=3), datetime.min.time()).astimezone()
        azdevops_recent_activity("Proj", since=since)
        # First WIQL is the changed-window query; the second is the WIP scan.
        wiql = wit.query_by_wiql.call_args_list[0][0][0].query
        assert "[System.ChangedDate] >= @Today - 3" in wiql

    def test_github_commits_pass_since_datetime(self, monkeypatch):
        repo = MagicMock()
        repo.get_commits.return_value = []
        client = MagicMock()
        client.get_repo.return_value = repo
        monkeypatch.setattr("yeaboi.tools.github._get_github_client", lambda: client)
        github_recent_commits("owner/repo", since=self._SINCE)
        assert repo.get_commits.call_args.kwargs["since"] == self._SINCE.astimezone(UTC)

    def test_github_prs_cut_at_since(self, monkeypatch):
        old_pr = SimpleNamespace(
            number=1,
            title="Old",
            merged=False,
            state="open",
            user=SimpleNamespace(login="x"),
            updated_at=self._SINCE.astimezone(UTC) - timedelta(days=2),
        )
        repo = MagicMock()
        repo.get_pulls.return_value = [old_pr]
        client = MagicMock()
        client.get_repo.return_value = repo
        monkeypatch.setattr("yeaboi.tools.github._get_github_client", lambda: client)
        assert github_recent_prs("owner/repo", since=self._SINCE) == []

    def test_local_git_builds_iso_since(self, monkeypatch, tmp_path):
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("yeaboi.tools.local_git.subprocess.run", fake_run)
        # tmp_path (whitelisted by the conftest sandbox fixture), not /tmp —
        # a path outside the sandbox never reaches subprocess.run.
        local_git_recent_commits(str(tmp_path), since=self._SINCE)
        assert f"--since={self._SINCE.isoformat()}" in captured["cmd"]

    def test_notion_cuts_at_since(self, monkeypatch):
        old_page = {
            "id": "p1",
            "last_edited_time": (self._SINCE.astimezone(UTC) - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
            "last_edited_by": {"id": ""},
            "properties": {},
        }
        client = MagicMock()
        client.search.return_value = {"results": [old_page]}
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: client)
        from yeaboi.tools.notion import notion_recent_pages

        assert notion_recent_pages("root", since=self._SINCE) == []


def _jira_issue(key="PROJ-1", summary="Fix login", assignee_name="Alice", assignee_email="alice@corp.com"):
    """A fake python-jira issue with empty changelog/comments by default."""
    issue = MagicMock()
    issue.key = key
    issue.fields = SimpleNamespace(
        summary=summary,
        assignee=SimpleNamespace(displayName=assignee_name, emailAddress=assignee_email) if assignee_name else None,
        status=SimpleNamespace(name="In Progress"),
        updated="2026-07-17T09:00:00.000+0000",
        comment=SimpleNamespace(comments=[]),
    )
    issue.changelog = SimpleNamespace(histories=[])
    return issue


class TestJiraChangelogItems:
    _NOW = datetime.now(UTC).isoformat()

    def _client(self, monkeypatch, issues, wip=None):
        client = MagicMock()
        client.search_issues.side_effect = [issues, wip or []]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_project_key", lambda: "PROJ")
        return client

    def test_status_move_credited_to_actor(self, monkeypatch):
        issue = _jira_issue()
        issue.changelog = SimpleNamespace(
            histories=[
                SimpleNamespace(
                    author=SimpleNamespace(displayName="Bob", emailAddress="bob@corp.com"),
                    created=self._NOW,
                    items=[SimpleNamespace(field="status", toString="In Review")],
                )
            ]
        )
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        updates = [i for i in items if i["kind"] == "update"]
        assert len(updates) == 1
        assert updates[0]["author"] == "Bob"
        assert updates[0]["author_email"] == "bob@corp.com"
        assert updates[0]["title"] == "moved PROJ-1 'Fix login' to In Review"
        # The clean ticket summary rides alongside the action-phrase title so
        # evidence rows can show what the ticket IS, not what happened to it.
        assert updates[0]["summary"] == "Fix login"
        assert updates[0]["status"] == "In Review"

    def test_generic_edit_by_assignee_suppressed(self, monkeypatch):
        issue = _jira_issue()
        issue.changelog = SimpleNamespace(
            histories=[
                SimpleNamespace(
                    author=SimpleNamespace(displayName="Alice", emailAddress=""),
                    created=self._NOW,
                    items=[SimpleNamespace(field="description", toString="new text")],
                ),
                SimpleNamespace(
                    author=SimpleNamespace(displayName="Carol", emailAddress=""),
                    created=self._NOW,
                    items=[SimpleNamespace(field="description", toString="more text")],
                ),
            ]
        )
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        updates = [i for i in items if i["kind"] == "update"]
        # Alice is the assignee (already credited via the issue item); only Carol's edit shows.
        assert [u["author"] for u in updates] == ["Carol"]
        assert updates[0]["title"] == "updated PROJ-1 'Fix login'"
        assert updates[0]["summary"] == "Fix login"

    def test_rank_only_edit_is_not_activity(self, monkeypatch):
        # Dragging a card up the board writes a Rank changelog entry (on
        # neighbours too) — board mechanics, not work someone did on a ticket.
        issue = _jira_issue()
        issue.changelog = SimpleNamespace(
            histories=[
                SimpleNamespace(
                    author=SimpleNamespace(displayName="Bob", emailAddress=""),
                    created=self._NOW,
                    items=[SimpleNamespace(field="Rank", toString="Ranked higher")],
                )
            ]
        )
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        assert [i for i in items if i["kind"] == "update"] == []

    def test_sprint_field_rewrite_is_not_activity(self, monkeypatch):
        # Completing a sprint rewrites the Sprint field on every open ticket,
        # with the sprint-closer as the actor of all of them.
        issue = _jira_issue()
        issue.changelog = SimpleNamespace(
            histories=[
                SimpleNamespace(
                    author=SimpleNamespace(displayName="Bob", emailAddress=""),
                    created=self._NOW,
                    items=[SimpleNamespace(field="Sprint", toString="Sprint 43")],
                )
            ]
        )
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        assert [i for i in items if i["kind"] == "update"] == []

    def test_meaningful_edit_alongside_rank_still_counts(self, monkeypatch):
        # Grooming often reorders too — a description edit in the same history
        # entry keeps it as real activity.
        issue = _jira_issue()
        issue.changelog = SimpleNamespace(
            histories=[
                SimpleNamespace(
                    author=SimpleNamespace(displayName="Bob", emailAddress=""),
                    created=self._NOW,
                    items=[
                        SimpleNamespace(field="Rank", toString="Ranked higher"),
                        SimpleNamespace(field="description", toString="tightened AC"),
                    ],
                )
            ]
        )
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        updates = [i for i in items if i["kind"] == "update"]
        assert [u["title"] for u in updates] == ["updated PROJ-1 'Fix login'"]

    def test_status_move_with_rank_alongside_still_counts(self, monkeypatch):
        issue = _jira_issue()
        issue.changelog = SimpleNamespace(
            histories=[
                SimpleNamespace(
                    author=SimpleNamespace(displayName="Bob", emailAddress=""),
                    created=self._NOW,
                    items=[
                        SimpleNamespace(field="Rank", toString="Ranked higher"),
                        SimpleNamespace(field="status", toString="Done"),
                    ],
                )
            ]
        )
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        updates = [i for i in items if i["kind"] == "update"]
        assert [u["status"] for u in updates] == ["Done"]

    def test_out_of_window_history_ignored(self, monkeypatch):
        issue = _jira_issue()
        issue.changelog = SimpleNamespace(
            histories=[
                SimpleNamespace(
                    author=SimpleNamespace(displayName="Bob", emailAddress=""),
                    created="2020-01-01T00:00:00.000+0000",
                    items=[SimpleNamespace(field="status", toString="Done")],
                )
            ]
        )
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        assert [i for i in items if i["kind"] == "update"] == []

    def test_comment_items_emitted_without_bodies(self, monkeypatch):
        issue = _jira_issue()
        issue.fields.comment = SimpleNamespace(
            comments=[
                SimpleNamespace(
                    author=SimpleNamespace(displayName="Dana", emailAddress="dana@corp.com"),
                    created=self._NOW,
                    body="secret detail",
                )
            ]
        )
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        comments = [i for i in items if i["kind"] == "comment"]
        assert len(comments) == 1
        assert comments[0]["author"] == "Dana"
        assert comments[0]["title"] == "commented on PROJ-1 'Fix login'"
        assert comments[0]["summary"] == "Fix login"
        assert "secret detail" not in str(comments)

    def test_gdpr_hidden_email_defaults_empty(self, monkeypatch):
        issue = _jira_issue()
        issue.fields.assignee = SimpleNamespace(displayName="Alice")  # no emailAddress attr
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        assert items[0]["author_email"] == ""


class TestJiraBotFiltering:
    """App/automation accounts must never be credited as activity authors —
    otherwise they surface as standup team members (e.g. "Automation for Jira")."""

    _NOW = datetime.now(UTC).isoformat()

    def _client(self, monkeypatch, issues):
        client = MagicMock()
        client.search_issues.side_effect = [issues, []]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_project_key", lambda: "PROJ")
        return client

    def test_app_account_changelog_skipped(self, monkeypatch):
        issue = _jira_issue()
        issue.changelog = SimpleNamespace(
            histories=[
                SimpleNamespace(
                    author=SimpleNamespace(displayName="Deploy Bot", accountType="app"),
                    created=self._NOW,
                    items=[SimpleNamespace(field="status", toString="Done")],
                )
            ]
        )
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        assert [i for i in items if i["kind"] == "update"] == []

    def test_automation_for_jira_name_filtered_without_account_type(self, monkeypatch):
        # Server/DC has no accountType — the well-known display name is enough.
        issue = _jira_issue()
        issue.fields.comment = SimpleNamespace(
            comments=[
                SimpleNamespace(
                    author=SimpleNamespace(displayName="Automation for Jira"),
                    created=self._NOW,
                    body="rule fired",
                )
            ]
        )
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        assert [i for i in items if i["kind"] == "comment"] == []

    def test_bot_assignee_treated_as_unassigned(self, monkeypatch):
        issue = _jira_issue()
        issue.fields.assignee = SimpleNamespace(displayName="Automation for Jira", accountType="app")
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        assert items[0]["kind"] == "issue"
        assert items[0]["author"] == ""

    def test_human_actor_unaffected(self, monkeypatch):
        # atlassian accounts carry accountType == "atlassian" — must pass through.
        issue = _jira_issue()
        issue.fields.assignee = SimpleNamespace(displayName="Alice", emailAddress="a@corp.com", accountType="atlassian")
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        assert items[0]["author"] == "Alice"


class TestJiraWip:
    def test_wip_items_credited_to_assignee(self, monkeypatch):
        wip_issue = _jira_issue(key="PROJ-9", summary="Ship exports", assignee_name="Eve", assignee_email="")
        client = MagicMock()
        client.search_issues.side_effect = [[], [wip_issue]]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_project_key", lambda: "PROJ")
        items = jira_recent_activity("PROJ", days=1)
        assert len(items) == 1
        assert items[0]["kind"] == "wip"
        assert items[0]["author"] == "Eve"
        assert items[0]["key"] == "PROJ-9"
        wip_jql = client.search_issues.call_args_list[1][0][0]
        assert "openSprints()" in wip_jql
        assert 'statusCategory = "In Progress"' in wip_jql

    def test_wip_skips_keys_already_in_window(self, monkeypatch):
        fresh = _jira_issue(key="PROJ-1")
        wip_dupe = _jira_issue(key="PROJ-1")
        client = MagicMock()
        client.search_issues.side_effect = [[fresh], [wip_dupe]]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_project_key", lambda: "PROJ")
        items = jira_recent_activity("PROJ", days=1)
        assert [i["kind"] for i in items] == ["issue"]

    def test_open_sprints_failure_falls_back(self, monkeypatch):
        from jira import JIRAError

        wip_issue = _jira_issue(key="PROJ-9", assignee_name="Eve")
        client = MagicMock()
        # main search → [], sprint WIP query → 400 (no boards), fallback → [issue]
        client.search_issues.side_effect = [[], JIRAError(status_code=400, text="no sprint field"), [wip_issue]]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_project_key", lambda: "PROJ")
        items = jira_recent_activity("PROJ", days=1)
        assert [i["kind"] for i in items] == ["wip"]
        fallback_jql = client.search_issues.call_args_list[2][0][0]
        assert "openSprints()" not in fallback_jql
        assert "updated >= -14d" in fallback_jql

    def test_include_wip_false_skips_queries(self, monkeypatch):
        client = MagicMock()
        client.search_issues.side_effect = [[]]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_project_key", lambda: "PROJ")
        assert jira_recent_activity("PROJ", days=1, include_wip=False) == []
        assert client.search_issues.call_count == 1


class TestActivityHierarchy:
    """Story/subtask facts ride every tracker item, from fields already fetched."""

    _NOW = datetime.now(UTC).isoformat()

    def _client(self, monkeypatch, issues, wip=None):
        client = MagicMock()
        client.search_issues.side_effect = [issues, wip or []]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_project_key", lambda: "PROJ")
        return client

    def test_jira_subtask_facts_ride_issue_and_update_items(self, monkeypatch):
        issue = _jira_issue()
        issue.fields.issuetype = SimpleNamespace(name="Sub-task", subtask=True)
        issue.fields.parent = SimpleNamespace(key="PROJ-9")
        issue.changelog = SimpleNamespace(
            histories=[
                SimpleNamespace(
                    author=SimpleNamespace(displayName="Bob", emailAddress=""),
                    created=self._NOW,
                    items=[SimpleNamespace(field="status", toString="Done")],
                )
            ]
        )
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        by_kind = {i["kind"]: i for i in items}
        for kind in ("issue", "update"):
            assert by_kind[kind]["issue_type"] == "Sub-task"
            assert by_kind[kind]["subtask"] is True
            # URL-dedupe can leave the update as the ticket's surviving evidence
            # row, so the parent must ride it too.
            assert by_kind[kind]["parent_key"] == "PROJ-9"

    def test_jira_team_managed_story_is_not_a_subtask_of_its_epic(self, monkeypatch):
        issue = _jira_issue()
        issue.fields.issuetype = SimpleNamespace(name="Story", subtask=False)
        # On a team-managed project fields.parent on a Story points at its EPIC.
        issue.fields.parent = SimpleNamespace(key="PROJ-100")
        self._client(monkeypatch, [issue])
        items = jira_recent_activity("PROJ", days=1)
        assert items[0]["issue_type"] == "Story"
        assert items[0]["subtask"] is False
        assert items[0]["parent_key"] == "PROJ-100"

    def test_jira_requests_hierarchy_fields_on_both_searches(self, monkeypatch):
        client = self._client(monkeypatch, [_jira_issue()], wip=[])
        jira_recent_activity("PROJ", days=1)
        for call in client.search_issues.call_args_list:
            fields = call.kwargs["fields"]
            assert "issuetype" in fields
            assert "parent" in fields

    def _azdo_item(self, work_item_type, parent=None):
        fields = {
            "System.Id": 7,
            "System.Title": "Build API",
            "System.State": "Active",
            "System.AssignedTo": {"displayName": "Dana"},
            "System.ChangedDate": "2026-07-10T06:00:00Z",
            "System.WorkItemType": work_item_type,
        }
        if parent is not None:
            fields["System.Parent"] = parent
        return SimpleNamespace(fields=fields)

    def _azdo_activity(self, monkeypatch, item):
        wit = MagicMock()
        wit.query_by_wiql.return_value = SimpleNamespace(work_items=[SimpleNamespace(id=7)])
        wit.get_work_items.return_value = [item]
        monkeypatch.setattr("yeaboi.tools.azure_devops._make_azdo_clients", lambda: (wit, MagicMock()))
        return wit, azdevops_recent_activity("Proj", days=1)

    def test_azdo_task_is_the_subtask_analogue(self, monkeypatch):
        wit, items = self._azdo_activity(monkeypatch, self._azdo_item("Task", parent=12))
        row = items[0]
        assert row["issue_type"] == "Task"
        assert row["subtask"] is True
        # Spelled the way sibling evidence rows spell work-item keys.
        assert row["parent_key"] == "#12"
        requested = wit.get_work_items.call_args.kwargs.get("fields") or wit.get_work_items.call_args.args[1]
        assert "System.WorkItemType" in requested
        assert "System.Parent" in requested

    def test_azdo_story_level_items_never_nest(self, monkeypatch):
        _, items = self._azdo_activity(monkeypatch, self._azdo_item("User Story"))
        assert items[0]["issue_type"] == "User Story"
        assert items[0]["subtask"] is False
        assert items[0]["parent_key"] == ""


class TestAzdoChangedBy:
    def _wit(self, monkeypatch, fields, wip_result=None):
        wit = MagicMock()
        wit.query_by_wiql.side_effect = [
            SimpleNamespace(work_items=[SimpleNamespace(id=7)]),
            wip_result or SimpleNamespace(work_items=[]),
        ]
        wit.get_work_items.return_value = [SimpleNamespace(fields=fields)]
        monkeypatch.setattr("yeaboi.tools.azure_devops._make_azdo_clients", lambda: (wit, MagicMock()))
        return wit

    def test_changed_by_wins_over_assignee(self, monkeypatch):
        self._wit(
            monkeypatch,
            {
                "System.Id": 7,
                "System.Title": "Build API",
                "System.State": "Active",
                "System.AssignedTo": {"displayName": "Dana", "uniqueName": "dana@corp.com"},
                "System.ChangedBy": {"displayName": "Erik", "uniqueName": "erik@corp.com"},
                "System.ChangedDate": "2026-07-17T06:00:00Z",
            },
        )
        items = azdevops_recent_activity("Proj", days=1)
        assert items[0]["author"] == "Erik"
        assert items[0]["author_email"] == "erik@corp.com"

    def test_string_identity_parsed(self, monkeypatch):
        self._wit(
            monkeypatch,
            {
                "System.Id": 7,
                "System.Title": "Build API",
                "System.State": "Active",
                "System.AssignedTo": "Dana Smith <dana@corp.com>",
                "System.ChangedBy": None,
                "System.ChangedDate": "2026-07-17T06:00:00Z",
            },
        )
        items = azdevops_recent_activity("Proj", days=1)
        assert items[0]["author"] == "Dana Smith"
        assert items[0]["author_email"] == "dana@corp.com"

    def test_wip_work_items_emitted(self, monkeypatch):
        wit = MagicMock()
        wit.query_by_wiql.side_effect = [
            SimpleNamespace(work_items=[]),
            SimpleNamespace(work_items=[SimpleNamespace(id=9)]),
        ]
        wit.get_work_items.return_value = [
            SimpleNamespace(
                fields={
                    "System.Id": 9,
                    "System.Title": "Ship exports",
                    "System.State": "In Progress",
                    "System.AssignedTo": {"displayName": "Fay", "uniqueName": "fay@corp.com"},
                    "System.ChangedDate": "2026-07-01T06:00:00Z",
                }
            )
        ]
        monkeypatch.setattr("yeaboi.tools.azure_devops._make_azdo_clients", lambda: (wit, MagicMock()))
        items = azdevops_recent_activity("Proj", days=1)
        assert [i["kind"] for i in items] == ["wip"]
        assert items[0]["author"] == "Fay"
        wip_wiql = wit.query_by_wiql.call_args_list[1][0][0].query
        assert "[System.State] IN ('Active', 'In Progress', 'Doing', 'Committed')" in wip_wiql
        assert "[System.AssignedTo] <> ''" in wip_wiql


class TestAzdoRepoActivity:
    def _git_client(self, monkeypatch, repos):
        git = MagicMock()
        git.get_repositories.return_value = repos
        monkeypatch.setattr("yeaboi.tools.azure_devops._make_git_client", lambda: git)
        monkeypatch.setattr("yeaboi.tools.azure_devops.get_azure_devops_project", lambda: "Proj")
        return git

    def test_commits_normalized(self, monkeypatch):
        from yeaboi.tools.azure_devops import azdevops_recent_commits

        repo = SimpleNamespace(id="r1", name="api", web_url="https://dev.azure.com/org/Proj/_git/api")
        git = self._git_client(monkeypatch, [repo])
        git.get_commits.return_value = [
            SimpleNamespace(
                commit_id="abcdef1234567890",
                comment="add endpoint\n\nbody",
                author=SimpleNamespace(name="Gina", email="gina@corp.com", date="2026-07-17T08:00:00Z"),
            )
        ]
        git.get_changes.return_value = SimpleNamespace(
            changes=[SimpleNamespace(item=SimpleNamespace(path="/docs/api.md"))]
        )
        items = azdevops_recent_commits("Proj", days=1)
        assert items == [
            {
                "author": "Gina",
                "author_email": "gina@corp.com",
                "kind": "commit",
                "title": "add endpoint (api)",
                "body": "body",
                "timestamp": "2026-07-17T08:00:00",
                "key": "abcdef12",
                "commit_id": "abcdef1234567890",
                "url": "https://dev.azure.com/org/Proj/_git/api/commit/abcdef1234567890",
                "repository": "Proj/api",
                "changed_files": ["/docs/api.md"],
            }
        ]
        criteria = git.get_commits.call_args.kwargs["search_criteria"]
        assert criteria.from_date  # window start passed to the API

    def test_one_bad_repo_does_not_hide_others(self, monkeypatch):
        from yeaboi.tools.azure_devops import azdevops_recent_commits

        good = SimpleNamespace(id="r2", name="web")
        bad = SimpleNamespace(id="r1", name="broken")
        git = self._git_client(monkeypatch, [bad, good])

        def commits_for(repository_id, search_criteria, project):
            if repository_id == "r1":
                raise RuntimeError("disabled repo")
            return [
                SimpleNamespace(
                    commit_id="1234567890",
                    comment="fix",
                    author=SimpleNamespace(name="Hal", email="", date="2026-07-17T08:00:00Z"),
                )
            ]

        git.get_commits.side_effect = commits_for
        items = azdevops_recent_commits("Proj", days=1)
        assert len(items) == 1
        assert items[0]["author"] == "Hal"

    def test_prs_filtered_client_side_by_window(self, monkeypatch):
        from yeaboi.tools.azure_devops import azdevops_recent_prs

        repo = SimpleNamespace(id="r1", name="api")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr(
            "yeaboi.tools.azure_devops.get_azure_devops_org_url",
            lambda: "https://dev.azure.com/acme",
        )
        recent = datetime.now(UTC) - timedelta(hours=2)
        old = datetime.now(UTC) - timedelta(days=30)
        git.get_pull_requests_by_project.return_value = [
            SimpleNamespace(
                pull_request_id=1,
                title="New PR",
                status="active",
                created_by=SimpleNamespace(display_name="Ivy", unique_name="ivy@corp.com"),
                creation_date=recent,
                closed_date=None,
                repository=repo,
                source_ref_name="refs/heads/codex/new-pr",
            ),
            SimpleNamespace(
                pull_request_id=2,
                title="Merged old PR",
                status="completed",
                created_by=SimpleNamespace(display_name="Jon", unique_name=""),
                creation_date=old,
                closed_date=recent,
                repository=repo,
            ),
            SimpleNamespace(
                pull_request_id=3,
                title="Ancient PR",
                status="completed",
                created_by=SimpleNamespace(display_name="Kim", unique_name=""),
                creation_date=old,
                closed_date=old,
                repository=repo,
            ),
        ]
        items = azdevops_recent_prs("Proj", days=1)
        assert [i["key"] for i in items] == ["!1", "!2"]
        assert items[0]["author"] == "Ivy"
        assert items[0]["branch"] == "codex/new-pr"  # refs/heads/ stripped
        assert items[1]["branch"] == ""  # absent on the SDK object → empty
        assert items[1]["status"] == "merged"  # completed → merged label
        assert items[0]["url"] == "https://dev.azure.com/acme/Proj/_git/api/pullrequest/1"
        assert items[1]["url"] == "https://dev.azure.com/acme/Proj/_git/api/pullrequest/2"
        git.get_pull_requests_by_project.assert_called_once()
        git.get_pull_requests.assert_not_called()
        git.get_repository.assert_not_called()

    def test_reviews_only_fetch_threads_for_recent_or_active_prs(self, monkeypatch):
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        repo = SimpleNamespace(id="r1", name="api", web_url="")
        git = self._git_client(monkeypatch, [repo])
        recent = datetime.now(UTC) - timedelta(hours=2)
        old = datetime.now(UTC) - timedelta(days=30)
        git.get_pull_requests_by_project.return_value = [
            SimpleNamespace(
                pull_request_id=1,
                title="Active",
                status="active",
                creation_date=old,
                closed_date=None,
                repository=repo,
            ),
            SimpleNamespace(
                pull_request_id=2,
                title="Recently merged",
                status="completed",
                creation_date=old,
                closed_date=recent,
                repository=repo,
            ),
            SimpleNamespace(
                pull_request_id=3,
                title="Ancient",
                status="completed",
                creation_date=old,
                closed_date=old,
                repository=repo,
            ),
        ]
        git.get_threads.return_value = []

        assert azdevops_recent_reviews("Proj", days=1) == []

        fetched_prs = {call.args[1] for call in git.get_threads.call_args_list}
        assert fetched_prs == {1, 2}

    def test_review_builds_link_from_partial_project_repo_reference(self, monkeypatch):
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        repo = SimpleNamespace(id="r1", name="API Service", web_url="")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr(
            "yeaboi.tools.azure_devops.get_azure_devops_org_url",
            lambda: "https://acme.visualstudio.com",
        )
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        recent = datetime.now(UTC) - timedelta(hours=2)
        git.get_pull_requests_by_project.return_value = [
            SimpleNamespace(
                pull_request_id=42,
                title="Review me",
                status="active",
                creation_date=recent,
                closed_date=None,
                repository=repo,
            )
        ]
        git.get_threads.return_value = [
            SimpleNamespace(
                id=9,
                comments=(
                    SimpleNamespace(
                        id=7,
                        published_date=recent,
                        author=SimpleNamespace(display_name="Rae", unique_name="rae@example.com"),
                        content="Looks good",
                    ),
                ),
            )
        ]

        items = azdevops_recent_reviews("Project Space", days=1)

        assert len(items) == 1
        assert items[0]["url"] == (
            "https://acme.visualstudio.com/Project%20Space/_git/API%20Service/pullrequest/42?discussionId=9"
        )
        git.get_repository.assert_not_called()

    def test_review_skips_system_comments(self, monkeypatch):
        # AzDO "system" thread comments are vote/status noise (or service-hook
        # posts) — never a member's review work.
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        repo = SimpleNamespace(id="r1", name="api", web_url="")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        recent = datetime.now(UTC) - timedelta(hours=2)
        git.get_pull_requests_by_project.return_value = [
            SimpleNamespace(
                pull_request_id=1,
                title="Review me",
                status="active",
                creation_date=recent,
                closed_date=None,
                repository=repo,
            )
        ]

        def _comment(cid, ctype, content):
            return SimpleNamespace(
                id=cid,
                published_date=recent,
                author=SimpleNamespace(display_name="Rae", unique_name="rae@example.com"),
                content=content,
                comment_type=ctype,
            )

        git.get_threads.return_value = [
            SimpleNamespace(
                comments=(
                    _comment(1, "system", "Rae voted 10"),
                    _comment(2, "text", "Looks good to me"),
                    # comment_type absent on old SDK objects → kept (back-compat).
                    SimpleNamespace(
                        id=3,
                        published_date=recent,
                        author=SimpleNamespace(display_name="Rae", unique_name="rae@example.com"),
                        content="One more nit",
                    ),
                    # AzDO's pushed-N-commits notices are noise; an unknown
                    # type from an odd payload is somebody's words — kept.
                    _comment(4, "codeChange", "pushed 2 commits"),
                    _comment(5, "unknown", "odd payload, still a human comment"),
                )
            )
        ]

        items = azdevops_recent_reviews("Proj", days=1)

        # The "Rae voted 10" system comment sits on a thread with no
        # properties, so it must not fabricate a vote row either.
        assert [i["key"] for i in items] == ["review-comment-2", "review-comment-3", "review-comment-5"]

    def _review_pr(self, repo, pr_id=1, *, status="active", created=None, closed=None, reviewers=()):
        return SimpleNamespace(
            pull_request_id=pr_id,
            title="Review me",
            status=status,
            creation_date=created or (datetime.now(UTC) - timedelta(hours=3)),
            closed_date=closed,
            repository=repo,
            reviewers=list(reviewers),
        )

    def _vote_thread(self, *, thread_id=11, vote="10", identity_index="1", voter, published):
        # The wire shape of a VoteUpdate system thread: typed property wrappers,
        # the voter behind an index into thread.identities, and a system comment
        # whose published_date is the actual vote event time.
        return SimpleNamespace(
            id=thread_id,
            properties={
                "CodeReviewThreadType": {"$type": "System.String", "$value": "VoteUpdate"},
                "CodeReviewVoteResult": {"$type": "System.String", "$value": vote},
                "CodeReviewVotedByIdentity": {"$type": "System.String", "$value": identity_index},
            },
            identities={identity_index: voter},
            comments=(
                SimpleNamespace(
                    id=100 + thread_id,
                    published_date=published,
                    author=voter,
                    content=f"{getattr(voter, 'display_name', '')} voted {vote}",
                    comment_type="system",
                ),
            ),
        )

    def test_vote_update_thread_becomes_dated_review_row(self, monkeypatch):
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        repo = SimpleNamespace(id="r1", name="api", web_url="https://dev.azure.com/org/Proj/_git/api")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        recent = datetime.now(UTC) - timedelta(hours=2)
        vic = SimpleNamespace(display_name="Vic", unique_name="vic@corp.com", id="guid-vic")
        git.get_pull_requests_by_project.return_value = [self._review_pr(repo, 42)]
        git.get_threads.return_value = [self._vote_thread(voter=vic, published=recent)]

        items = azdevops_recent_reviews("Proj", days=1)

        assert len(items) == 1
        assert items[0]["kind"] == "review"
        assert items[0]["status"] == "approved"
        assert items[0]["author"] == "Vic"
        assert items[0]["key"] == "review:42:guid-vic"
        assert items[0]["timestamp"] == str(recent)[:19]
        assert items[0]["url"] == "https://dev.azure.com/org/Proj/_git/api/pullrequest/42?discussionId=11"

    def test_vote_update_before_window_is_not_emitted(self, monkeypatch):
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        repo = SimpleNamespace(id="r1", name="api", web_url="https://dev.azure.com/org/Proj/_git/api")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        old = datetime.now(UTC) - timedelta(days=30)
        vic = SimpleNamespace(display_name="Vic", unique_name="vic@corp.com", id="guid-vic")
        git.get_pull_requests_by_project.return_value = [self._review_pr(repo, 42)]
        git.get_threads.return_value = [self._vote_thread(voter=vic, published=old)]

        assert azdevops_recent_reviews("Proj", days=1) == []

    def test_pr_closed_in_window_snapshots_reviewer_votes(self, monkeypatch):
        # Votes are frozen at completion and the closed date passes through the
        # window exactly once — credited on merge day, never re-credited.
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        repo = SimpleNamespace(id="r1", name="api", web_url="https://dev.azure.com/org/Proj/_git/api")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        recent = datetime.now(UTC) - timedelta(hours=2)
        old = datetime.now(UTC) - timedelta(days=30)
        reviewers = [
            SimpleNamespace(display_name="Vic", unique_name="vic@corp.com", id="guid-vic", vote=10),
            SimpleNamespace(display_name="Ann", unique_name="ann@corp.com", id="guid-ann", vote=-10),
            SimpleNamespace(display_name="Zed", unique_name="zed@corp.com", id="guid-zed", vote=0),
        ]
        git.get_pull_requests_by_project.return_value = [
            self._review_pr(repo, 7, status="completed", created=old, closed=recent, reviewers=reviewers)
        ]
        git.get_threads.return_value = []

        items = azdevops_recent_reviews("Proj", days=1)

        # vote=0 is "no vote cast" and never becomes a row.
        assert sorted(i["key"] for i in items) == ["review:7:guid-ann", "review:7:guid-vic"]
        by_key = {i["key"]: i for i in items}
        assert by_key["review:7:guid-vic"]["status"] == "approved"
        assert by_key["review:7:guid-ann"]["status"] == "rejected"
        assert by_key["review:7:guid-vic"]["timestamp"] == str(recent)[:19]
        # Snapshot rows keep the bare PR URL — there is no thread to anchor to.
        assert by_key["review:7:guid-vic"]["url"] == "https://dev.azure.com/org/Proj/_git/api/pullrequest/7"

    def test_snapshot_votes_survive_past_the_thread_lookup_cap(self, monkeypatch):
        # The completion snapshot needs no thread lookup, so it must cover
        # every eligible PR — including those the cap excluded.
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        monkeypatch.setattr("yeaboi.tools.azure_devops._MAX_REVIEW_THREAD_LOOKUPS", 1)
        repo = SimpleNamespace(id="r1", name="api", web_url="https://dev.azure.com/org/Proj/_git/api")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        newest = datetime.now(UTC) - timedelta(hours=1)
        older = datetime.now(UTC) - timedelta(hours=5)
        vic = SimpleNamespace(display_name="Vic", unique_name="vic@corp.com", id="guid-vic", vote=10)
        git.get_pull_requests_by_project.return_value = [
            self._review_pr(repo, 1, status="completed", created=older, closed=newest),
            self._review_pr(repo, 2, status="completed", created=older, closed=older, reviewers=[vic]),
        ]
        git.get_threads.return_value = []

        items = azdevops_recent_reviews("Proj", days=1)

        assert git.get_threads.call_count == 1  # the cap held
        assert [i["key"] for i in items] == ["review:2:guid-vic"]  # the capped PR still credited

    def test_stale_vote_on_old_active_pr_is_not_emitted(self, monkeypatch):
        # An undated vote on a long-open PR would repeat in every standup.
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        repo = SimpleNamespace(id="r1", name="api", web_url="https://dev.azure.com/org/Proj/_git/api")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        old = datetime.now(UTC) - timedelta(days=30)
        vic = SimpleNamespace(display_name="Vic", unique_name="vic@corp.com", id="guid-vic", vote=10)
        git.get_pull_requests_by_project.return_value = [
            self._review_pr(repo, 3, status="active", created=old, reviewers=[vic])
        ]
        git.get_threads.return_value = []

        assert azdevops_recent_reviews("Proj", days=1) == []

    def test_thread_vote_beats_completion_snapshot(self, monkeypatch):
        # A VoteUpdate thread carries the real event time; the snapshot's
        # closed-date approximation must not duplicate it.
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        repo = SimpleNamespace(id="r1", name="api", web_url="https://dev.azure.com/org/Proj/_git/api")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        voted_at = datetime.now(UTC) - timedelta(hours=4)
        closed_at = datetime.now(UTC) - timedelta(hours=1)
        vic = SimpleNamespace(display_name="Vic", unique_name="vic@corp.com", id="guid-vic", vote=10)
        git.get_pull_requests_by_project.return_value = [
            self._review_pr(repo, 8, status="completed", closed=closed_at, reviewers=[vic])
        ]
        git.get_threads.return_value = [self._vote_thread(voter=vic, published=voted_at)]

        items = azdevops_recent_reviews("Proj", days=1)

        assert [i["key"] for i in items] == ["review:8:guid-vic"]
        assert items[0]["timestamp"] == str(voted_at)[:19]

    def test_comment_threads_get_distinct_urls(self, monkeypatch):
        # Engine evidence dedupes URL-first; comments sharing the PR's bare URL
        # used to collapse into one row.
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        repo = SimpleNamespace(id="r1", name="api", web_url="https://dev.azure.com/org/Proj/_git/api")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        recent = datetime.now(UTC) - timedelta(hours=2)
        rae = SimpleNamespace(display_name="Rae", unique_name="rae@example.com")

        def _thread(tid, cid, content):
            return SimpleNamespace(
                id=tid,
                comments=(SimpleNamespace(id=cid, published_date=recent, author=rae, content=content),),
            )

        git.get_pull_requests_by_project.return_value = [self._review_pr(repo, 5)]
        git.get_threads.return_value = [_thread(21, 1, "First thread"), _thread(22, 2, "Second thread")]

        items = azdevops_recent_reviews("Proj", days=1)

        assert [i["url"] for i in items] == [
            "https://dev.azure.com/org/Proj/_git/api/pullrequest/5?discussionId=21",
            "https://dev.azure.com/org/Proj/_git/api/pullrequest/5?discussionId=22",
        ]

    def test_freshest_prs_win_the_thread_lookup_cap(self, monkeypatch):
        # When the cap bites, the PRs most likely to carry this window's review
        # activity keep their lookups — not whatever the API listed first.
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        monkeypatch.setattr("yeaboi.tools.azure_devops._MAX_REVIEW_THREAD_LOOKUPS", 1)
        repo = SimpleNamespace(id="r1", name="api", web_url="")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        newest = datetime.now(UTC) - timedelta(hours=1)
        older = datetime.now(UTC) - timedelta(hours=6)
        git.get_pull_requests_by_project.return_value = [
            self._review_pr(repo, 1, created=older),
            self._review_pr(repo, 2, created=newest),
        ]
        git.get_threads.return_value = []

        azdevops_recent_reviews("Proj", days=1)

        assert [call.args[1] for call in git.get_threads.call_args_list] == [2]

    def test_an_active_pr_outranks_a_newer_completed_one_for_the_cap(self, monkeypatch):
        # Only an active PR can gain a *new* thread, so a three-week-old PR still
        # under review is worth a lookup that a PR merged this morning is not.
        # Sorting on date alone put the population this fetcher exists to
        # capture at the bottom of the list.
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        monkeypatch.setattr("yeaboi.tools.azure_devops._MAX_REVIEW_THREAD_LOOKUPS", 1)
        repo = SimpleNamespace(id="r1", name="api", web_url="")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        git.get_pull_requests_by_project.return_value = [
            self._review_pr(
                repo,
                1,
                status="completed",
                created=datetime.now(UTC) - timedelta(hours=2),
                closed=datetime.now(UTC) - timedelta(hours=1),
            ),
            self._review_pr(repo, 2, status="active", created=datetime.now(UTC) - timedelta(days=21)),
        ]
        git.get_threads.return_value = []

        azdevops_recent_reviews("Proj", days=1)

        assert [call.args[1] for call in git.get_threads.call_args_list] == [2]

    def test_a_vote_thread_does_not_also_emit_its_own_system_comment(self, monkeypatch):
        # The comment filter drops "system", but that attribute is absent on some
        # serializations — and the widened filter keeps unknown types. Without
        # skipping the thread outright, AzDO's own "Vic voted 10" notice lands as
        # a second "reviewed PR" row beside the approval it describes.
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        repo = SimpleNamespace(id="r1", name="api", web_url="https://dev.azure.com/org/Proj/_git/api")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        recent = datetime.now(UTC) - timedelta(hours=2)
        vic = SimpleNamespace(display_name="Vic", unique_name="vic@corp.com", id="guid-vic")
        thread = self._vote_thread(voter=vic, published=recent)
        del thread.comments[0].comment_type  # the shape where the type never arrives
        git.get_pull_requests_by_project.return_value = [self._review_pr(repo, 42)]
        git.get_threads.return_value = [thread]

        items = azdevops_recent_reviews("Proj", days=1)

        assert [item["status"] for item in items] == ["approved"]

    def test_a_vote_reset_thread_emits_nothing_at_all(self, monkeypatch):
        # AzDO records a vote being *reset* as a VoteUpdate thread with vote 0.
        # There is no review event to report, and its system comment is the same
        # bookkeeping — so guarding on the parsed vote rather than the thread
        # type let this one fall through and become a "reviewed PR" row.
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        repo = SimpleNamespace(id="r1", name="api", web_url="https://dev.azure.com/org/Proj/_git/api")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        recent = datetime.now(UTC) - timedelta(hours=2)
        vic = SimpleNamespace(display_name="Vic", unique_name="vic@corp.com", id="guid-vic")
        thread = self._vote_thread(vote="0", voter=vic, published=recent)
        del thread.comments[0].comment_type
        git.get_pull_requests_by_project.return_value = [self._review_pr(repo, 42)]
        git.get_threads.return_value = [thread]

        assert azdevops_recent_reviews("Proj", days=1) == []

    def test_a_snapshot_vote_inherits_the_changed_files_the_thread_pass_paid_for(self, monkeypatch):
        # `categories.is_documentation_activity` reads changed_files, so the same
        # approval must not land under Documentation via one path and Code via
        # the other.
        from yeaboi.tools.azure_devops import azdevops_recent_reviews

        repo = SimpleNamespace(id="r1", name="api", web_url="https://dev.azure.com/org/Proj/_git/api")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: ["docs/runbook.md"])
        recent = datetime.now(UTC) - timedelta(hours=2)
        vic = SimpleNamespace(display_name="Vic", unique_name="vic@corp.com", id="guid-vic")
        ada = SimpleNamespace(display_name="Ada", unique_name="ada@corp.com", id="guid-ada", vote=10)
        git.get_pull_requests_by_project.return_value = [
            self._review_pr(repo, 42, status="completed", closed=recent, reviewers=(ada,))
        ]
        git.get_threads.return_value = [self._vote_thread(voter=vic, published=recent)]

        items = azdevops_recent_reviews("Proj", days=1)

        by_author = {item["author"]: item for item in items}
        assert by_author["Vic"]["changed_files"] == ["docs/runbook.md"]
        assert by_author["Ada"]["changed_files"] == ["docs/runbook.md"]

    def test_the_repo_scoped_listing_asks_for_the_same_bound_as_the_project_one(self, monkeypatch):
        # The branch a standup takes whenever the user picked specific repos. It
        # was left at top=25 while the project-wide path was raised to 200 —
        # the identical silent truncation, in the more common configuration.
        from yeaboi.tools.azure_devops import _MAX_REPO_PRS, azdevops_recent_reviews

        repo = SimpleNamespace(id="r1", name="api", web_url="")
        git = self._git_client(monkeypatch, [repo])
        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", lambda *a, **k: [])
        git.get_pull_requests.return_value = []

        azdevops_recent_reviews("Proj", days=1, repositories=["Proj/api"])

        assert git.get_pull_requests.call_args.kwargs["top"] == _MAX_REPO_PRS

    def test_auth_error_raises_source_error(self, monkeypatch):
        from azure.devops.exceptions import AzureDevOpsServiceError

        from yeaboi.standup.errors import StandupSourceError
        from yeaboi.tools.azure_devops import azdevops_recent_commits

        class _FakeAzdoError(AzureDevOpsServiceError):
            """Bypasses the wrapped-SDK-object __init__ (same pattern as test_tools_azure_devops)."""

            def __init__(self, message: str):
                Exception.__init__(self, message)
                self.message = message

            def __str__(self) -> str:
                return self.message

        def boom():
            raise _FakeAzdoError("401 unauthorized")

        monkeypatch.setattr("yeaboi.tools.azure_devops._make_git_client", boom)
        monkeypatch.setattr("yeaboi.tools.azure_devops.get_azure_devops_project", lambda: "Proj")
        with pytest.raises(StandupSourceError):
            azdevops_recent_commits("Proj", days=1)

    def test_missing_org_url_returns_empty(self, monkeypatch):
        from yeaboi.tools.azure_devops import azdevops_recent_prs

        def no_org():
            raise ValueError("AZURE_DEVOPS_ORG_URL is not set.")

        monkeypatch.setattr("yeaboi.tools.azure_devops._make_git_client", no_org)
        monkeypatch.setattr("yeaboi.tools.azure_devops.get_azure_devops_project", lambda: "Proj")
        assert azdevops_recent_prs("Proj", days=1) == []


class TestAzdoVoteWireShapes:
    """The degradation branches of the vote helpers.

    Every one of these exists because the property/identity wire shape varies by
    SDK version and serialization, and every one of them fails the *same silent
    way* — no vote row, no error, no log — which is precisely the bug the vote
    capture was written to fix. A shape that stops parsing must be caught here
    or it is not caught at all.
    """

    def _thread(self, properties, *, comments=(), identities=None):
        return SimpleNamespace(
            id=7,
            properties=properties,
            identities=identities if identities is not None else {},
            comments=comments,
        )

    def _comment(self, author=None, published=None):
        return SimpleNamespace(
            id=70,
            published_date=published or (datetime.now(UTC) - timedelta(hours=1)),
            author=author,
            content="voted",
            comment_type="system",
        )

    def test_prop_value_reads_all_three_shapes(self):
        from yeaboi.tools.azure_devops import _azdo_prop_value

        assert _azdo_prop_value({"$type": "System.String", "$value": "VoteUpdate"}) == "VoteUpdate"
        assert _azdo_prop_value({"value": "VoteUpdate"}) == "VoteUpdate"
        assert _azdo_prop_value(SimpleNamespace(value="VoteUpdate")) == "VoteUpdate"
        assert _azdo_prop_value("VoteUpdate") == "VoteUpdate"
        assert _azdo_prop_value(None) is None

    def test_identity_fields_read_object_and_dict_alike(self):
        from yeaboi.tools.azure_devops import _azdo_identity_fields

        obj = SimpleNamespace(display_name="Vic", unique_name="vic@corp.com", id="guid-vic")
        assert _azdo_identity_fields(obj) == ("Vic", "vic@corp.com", "guid-vic")
        assert _azdo_identity_fields({"displayName": "Vic", "uniqueName": "vic@corp.com", "id": "guid-vic"}) == (
            "Vic",
            "vic@corp.com",
            "guid-vic",
        )
        # snake_case is the other serialization the SDK emits.
        assert _azdo_identity_fields({"display_name": "Vic", "unique_name": "vic@corp.com"}) == (
            "Vic",
            "vic@corp.com",
            "",
        )
        assert _azdo_identity_fields(None) == ("", "", "")

    def test_the_system_comment_author_stands_in_for_a_missing_identity_index(self):
        from yeaboi.tools.azure_devops import _azdo_thread_vote

        vic = SimpleNamespace(display_name="Vic", unique_name="vic@corp.com", id="guid-vic")
        # No CodeReviewVotedByIdentity, and no identities map to look one up in.
        thread = self._thread(
            {"CodeReviewThreadType": "VoteUpdate", "CodeReviewVoteResult": "10"},
            comments=(self._comment(author=vic),),
        )
        display, email, voter_id, vote, published = _azdo_thread_vote(thread)
        assert (display, email, voter_id, vote) == ("Vic", "vic@corp.com", "guid-vic", 10)
        assert published is not None

    def test_a_vote_that_cannot_be_read_is_not_reported(self):
        from yeaboi.tools.azure_devops import _azdo_thread_vote

        vic = SimpleNamespace(display_name="Vic", unique_name="vic@corp.com", id="guid-vic")
        comments = (self._comment(author=vic),)
        # Properties that are not a mapping at all.
        assert _azdo_thread_vote(self._thread(None, comments=comments)) is None
        assert _azdo_thread_vote(self._thread([], comments=comments)) is None
        # A thread that is not a vote.
        assert _azdo_thread_vote(self._thread({"CodeReviewThreadType": "Comment"}, comments=comments)) is None
        # An unparseable or absent vote value.
        assert (
            _azdo_thread_vote(
                self._thread({"CodeReviewThreadType": "VoteUpdate", "CodeReviewVoteResult": "yes"}, comments=comments)
            )
            is None
        )
        assert _azdo_thread_vote(self._thread({"CodeReviewThreadType": "VoteUpdate"}, comments=comments)) is None
        # A reset-to-no-vote, which is not an event worth crediting.
        assert (
            _azdo_thread_vote(
                self._thread({"CodeReviewThreadType": "VoteUpdate", "CodeReviewVoteResult": "0"}, comments=comments)
            )
            is None
        )
        # No comment means no timestamp, and an undated vote would repeat daily.
        assert (
            _azdo_thread_vote(
                self._thread({"CodeReviewThreadType": "VoteUpdate", "CodeReviewVoteResult": "10"}, comments=())
            )
            is None
        )

    def test_the_thread_type_key_is_matched_case_insensitively(self):
        from yeaboi.tools.azure_devops import _azdo_thread_vote

        vic = SimpleNamespace(display_name="Vic", unique_name="vic@corp.com", id="guid-vic")
        thread = self._thread(
            {"codeReviewThreadType": {"$value": "voteUpdate"}, "codeReviewVoteResult": {"$value": "-10"}},
            comments=(self._comment(author=vic),),
        )
        result = _azdo_thread_vote(thread)
        assert result is not None
        assert result[3] == -10


class TestConfluenceMultiEditor:
    _NOW_ISO = datetime.now(UTC).isoformat()

    def _page(self, editors_last="Eve", created_by="", created_when=""):
        history = {"lastUpdated": {"by": {"displayName": editors_last}, "when": self._NOW_ISO}}
        if created_by:
            history["createdBy"] = {"displayName": created_by}
            history["createdDate"] = created_when or self._NOW_ISO
        return {"content": {"id": "123", "title": "Runbook", "history": history}}

    def test_version_history_credits_earlier_editors(self, monkeypatch):
        conf = MagicMock()
        conf.cql.return_value = {"results": [self._page()]}
        conf.get.return_value = {
            "results": [
                {"by": {"displayName": "Eve"}, "when": self._NOW_ISO, "number": 3},
                {"by": {"displayName": "Omar", "email": "omar@corp.com"}, "when": self._NOW_ISO, "number": 2},
                {"by": {"displayName": "Old Editor"}, "when": "2020-01-01T00:00:00.000Z", "number": 1},
            ]
        }
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: conf)
        monkeypatch.setattr("yeaboi.tools.confluence.get_confluence_space_key", lambda: "SPACE")
        items = confluence_recent_pages("SPACE", days=1)
        authors = [i["author"] for i in items]
        # Eve once (lastUpdated), Omar from version history, Old Editor out of window.
        assert authors == ["Eve", "Omar"]
        assert items[1]["title"] == "edited 'Runbook'"
        # The clean page title rides alongside the action phrase so evidence
        # rows can link the page by name instead of showing a numeric id.
        assert items[1]["summary"] == "Runbook"
        assert items[1]["author_email"] == "omar@corp.com"

    def test_analysis_discovery_can_skip_version_history(self, monkeypatch):
        conf = MagicMock()
        conf.cql.return_value = {"results": [self._page()]}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: conf)
        monkeypatch.setattr("yeaboi.tools.confluence.get_confluence_space_key", lambda: "SPACE")

        items = confluence_recent_pages("SPACE", days=1, include_version_history=False)

        assert [item["author"] for item in items] == ["Eve"]
        conf.get.assert_not_called()

    def test_analysis_discovery_counts_first_and_reports_batches(self, monkeypatch):
        conf = MagicMock()
        conf.cql.side_effect = [
            {"results": [self._page()], "total": 1},
            {"results": [self._page()], "total": 1},
        ]
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda *args: conf)
        updates = []

        items = confluence_recent_pages(
            "SPACE",
            days=1,
            include_version_history=False,
            count_first=True,
            progress_callback=lambda discovered, total, batch: updates.append((discovered, total, batch)),
        )

        assert len(items) == 1
        assert updates == [(0, 1, 0), (1, 1, 1)]

    def test_analysis_discovery_follows_provider_next_link(self, monkeypatch):
        conf = MagicMock()

        def page(page_id):
            value = self._page()
            value["content"]["id"] = str(page_id)
            return value

        first = [page(index) for index in range(100)]
        second = [page(100), page(101)]
        conf.cql.return_value = {
            "results": first,
            "total": 102,
            "_links": {"base": "https://example.atlassian.net/wiki", "next": "/wiki/rest/api/search?cursor=next"},
        }
        conf.get.return_value = {"results": second, "total": 102, "_links": {}}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda *args: conf)

        result = confluence_recent_pages(
            "SPACE",
            days=1,
            include_version_history=False,
            return_metadata=True,
        )

        assert result.complete is True
        assert len(result.items) == 102
        conf.get.assert_called_once_with(
            "https://example.atlassian.net/wiki/rest/api/search?cursor=next",
            absolute=True,
        )

    def test_analysis_discovery_preserves_wiki_context_for_rest_next_link(self, monkeypatch):
        conf = MagicMock()

        def page(page_id):
            value = self._page()
            value["content"]["id"] = str(page_id)
            return value

        first = [page(index) for index in range(100)]
        conf.cql.return_value = {
            "results": first,
            "total": 101,
            "_links": {
                "base": "https://example.atlassian.net/wiki",
                "next": "/rest/api/search?cursor=next",
            },
        }
        conf.get.return_value = {"results": [page(100)], "total": 101, "_links": {}}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda *args: conf)

        result = confluence_recent_pages(
            "SPACE",
            days=1,
            include_version_history=False,
            return_metadata=True,
        )

        assert result.complete is True
        assert len(result.items) == 101
        conf.get.assert_called_once_with(
            "https://example.atlassian.net/wiki/rest/api/search?cursor=next",
            absolute=True,
        )

    def test_analysis_discovery_falls_back_when_next_link_repeats_first_page(self, monkeypatch):
        conf = MagicMock()

        def page(page_id):
            value = self._page()
            value["content"]["id"] = str(page_id)
            return value

        first = [page(index) for index in range(100)]
        second = [page(index) for index in range(100, 200)]
        conf.cql.side_effect = [
            {
                "results": first,
                "total": 200,
                "_links": {
                    "base": "https://example.atlassian.net/wiki",
                    "next": "/rest/api/search?cursor=stale",
                },
            },
            {"results": second, "total": 200, "_links": {}},
        ]
        conf.get.return_value = {"results": first, "total": 200, "_links": {}}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda *args: conf)

        result = confluence_recent_pages(
            "SPACE",
            days=1,
            include_version_history=False,
            return_metadata=True,
        )

        assert result.complete is True
        assert len(result.items) == 200
        assert conf.cql.call_args_list[1].kwargs["start"] == 100

    def test_analysis_discovery_retains_unique_pages_when_pagination_stalls(self, monkeypatch):
        conf = MagicMock()

        def page(page_id):
            value = self._page()
            value["content"]["id"] = str(page_id)
            return value

        first = [page(index) for index in range(100)]
        conf.cql.side_effect = [
            {"results": first, "total": 200},
            {"results": first, "total": 200},
        ]
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda *args: conf)

        result = confluence_recent_pages(
            "SPACE",
            days=1,
            include_version_history=False,
            return_metadata=True,
        )

        assert result.complete is False
        assert len(result.items) == 100
        assert "repeated page IDs" in result.error

    def test_created_in_window_emits_page_created(self, monkeypatch):
        conf = MagicMock()
        conf.cql.return_value = {"results": [self._page(created_by="Nia")]}
        conf.get.return_value = {"results": []}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: conf)
        monkeypatch.setattr("yeaboi.tools.confluence.get_confluence_space_key", lambda: "SPACE")
        items = confluence_recent_pages("SPACE", days=1)
        created = [i for i in items if i["kind"] == "page-created"]
        assert len(created) == 1
        assert created[0]["author"] == "Nia"
        assert created[0]["title"] == "created 'Runbook'"
        assert created[0]["summary"] == "Runbook"

    def test_app_account_editors_skipped(self, monkeypatch):
        # Cloud automation/app users edit pages too — they must not be credited.
        conf = MagicMock()
        page = self._page()
        page["content"]["history"]["lastUpdated"]["by"]["accountType"] = "app"
        conf.cql.return_value = {"results": [page]}
        conf.get.return_value = {
            "results": [
                {"by": {"displayName": "App Sync", "accountType": "app"}, "when": self._NOW_ISO, "number": 2},
                {"by": {"displayName": "Omar", "accountType": "atlassian"}, "when": self._NOW_ISO, "number": 1},
            ]
        }
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: conf)
        monkeypatch.setattr("yeaboi.tools.confluence.get_confluence_space_key", lambda: "SPACE")
        items = confluence_recent_pages("SPACE", days=1)
        # The page item stays (author blank), only the human version editor is credited.
        assert [i["author"] for i in items] == ["", "Omar"]

    def test_version_lookup_failure_skips_page_quietly(self, monkeypatch):
        conf = MagicMock()
        conf.cql.return_value = {"results": [self._page()]}
        conf.get.side_effect = RuntimeError("boom")
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: conf)
        monkeypatch.setattr("yeaboi.tools.confluence.get_confluence_space_key", lambda: "SPACE")
        items = confluence_recent_pages("SPACE", days=1)
        assert [i["author"] for i in items] == ["Eve"]  # base item still present

    def test_first_revision_skips_version_lookup(self, monkeypatch):
        conf = MagicMock()
        page = self._page()
        page["content"]["history"]["lastUpdated"]["number"] = 1
        conf.cql.return_value = {"results": [page]}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: conf)
        monkeypatch.setattr("yeaboi.tools.confluence.get_confluence_space_key", lambda: "SPACE")

        items = confluence_recent_pages("SPACE", days=1)

        assert [item["author"] for item in items] == ["Eve"]
        conf.get.assert_not_called()

    def test_only_five_newest_cache_misses_are_fetched_and_partial_is_reported(self, monkeypatch):
        conf = MagicMock()
        pages = []
        for index in range(8):
            page = self._page(editors_last=f"Editor {index}")
            page["content"]["id"] = str(index)
            page["content"]["history"]["lastUpdated"]["number"] = 2
            pages.append(page)
        conf.cql.return_value = {"results": pages}
        conf.get.return_value = {"results": []}
        notices = []
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: conf)
        monkeypatch.setattr("yeaboi.tools.confluence.get_confluence_space_key", lambda: "SPACE")

        items = confluence_recent_pages("SPACE", days=1, on_partial=notices.append)

        assert len(items) == 8
        assert conf.get.call_count == 5
        assert notices == ["latest editors captured; earlier-editor enrichment incomplete for 3 page(s)"]

    def test_version_history_cache_is_reused_across_activity_windows(self, monkeypatch, tmp_path):
        from yeaboi.standup.cache import StandupMetadataCache

        conf = MagicMock()
        page = self._page()
        page["content"]["history"]["lastUpdated"]["number"] = 2
        conf.cql.return_value = {"results": [page]}
        conf.get.return_value = {"results": [{"by": {"displayName": "Omar"}, "when": self._NOW_ISO, "number": 1}]}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: conf)
        monkeypatch.setattr("yeaboi.tools.confluence.get_confluence_space_key", lambda: "SPACE")
        cache = StandupMetadataCache(tmp_path / "standup.db")
        try:
            first = confluence_recent_pages(
                "SPACE",
                since=datetime.now(UTC) - timedelta(days=1),
                metadata_cache=cache,
            )
            second = confluence_recent_pages(
                "SPACE",
                since=datetime.now(UTC) - timedelta(hours=1),
                metadata_cache=cache,
            )
        finally:
            cache.close()

        assert [item["author"] for item in first] == ["Eve", "Omar"]
        assert [item["author"] for item in second] == ["Eve", "Omar"]
        assert conf.get.call_count == 1

    def test_slow_history_does_not_hold_up_base_activity(self, monkeypatch):
        conf = MagicMock()
        page = self._page()
        page["content"]["history"]["lastUpdated"]["number"] = 2
        conf.cql.return_value = {"results": [page]}
        release = threading.Event()

        def slow_history(*_args, **_kwargs):
            release.wait(timeout=1)
            return {"results": []}

        conf.get.side_effect = slow_history
        notices = []
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: conf)
        monkeypatch.setattr("yeaboi.tools.confluence.get_confluence_space_key", lambda: "SPACE")

        started = time.monotonic()
        items = confluence_recent_pages(
            "SPACE",
            days=1,
            enrichment_budget_seconds=0.03,
            on_partial=notices.append,
        )
        elapsed = time.monotonic() - started
        release.set()

        assert elapsed < 0.2
        assert [item["author"] for item in items] == ["Eve"]
        assert notices == ["latest editors captured; earlier-editor enrichment incomplete for 1 page(s)"]

    def test_cql_timeout_is_a_source_failure_not_an_empty_success(self, monkeypatch):
        from requests.exceptions import Timeout

        from yeaboi.standup.errors import StandupSourceError

        conf = MagicMock()
        conf.cql.side_effect = Timeout("slow")
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: conf)
        monkeypatch.setattr("yeaboi.tools.confluence.get_confluence_space_key", lambda: "SPACE")

        with pytest.raises(StandupSourceError, match="timed out after 8 seconds"):
            confluence_recent_pages("SPACE", days=1)


class TestGithubPrBranchCommits:
    def test_open_pr_commits_emitted(self, monkeypatch):
        now = datetime.now(UTC)
        pr_commit = SimpleNamespace(
            sha="feedbeef1234",
            commit=SimpleNamespace(
                author=SimpleNamespace(name="Bob", email="bob@corp.com", date=now - timedelta(hours=3)),
                message="wip: new screen\n\nbody",
            ),
        )
        pr = MagicMock()
        pr.number = 7
        pr.title = "New screen"
        pr.merged = False
        pr.state = "open"
        pr.user = SimpleNamespace(login="bob")
        pr.updated_at = now - timedelta(hours=1)
        pr.get_commits.return_value = [pr_commit]
        repo = MagicMock()
        repo.get_pulls.return_value = [pr]
        client = MagicMock()
        client.get_repo.return_value = repo
        monkeypatch.setattr("yeaboi.tools.github._get_github_client", lambda: client)

        items = github_recent_prs("owner/repo", days=1)
        kinds = [i["kind"] for i in items]
        assert kinds == ["pr", "commit"]
        assert items[1]["author"] == "Bob"
        assert items[1]["author_email"] == "bob@corp.com"
        assert items[1]["title"] == "wip: new screen (PR #7)"
        assert items[1]["key"] == "feedbeef"

    def test_closed_unmerged_pr_commits_skipped(self, monkeypatch):
        now = datetime.now(UTC)
        pr = MagicMock()
        pr.number = 8
        pr.title = "Abandoned"
        pr.merged = False
        pr.state = "closed"
        pr.user = SimpleNamespace(login="x")
        pr.updated_at = now - timedelta(hours=1)
        repo = MagicMock()
        repo.get_pulls.return_value = [pr]
        client = MagicMock()
        client.get_repo.return_value = repo
        monkeypatch.setattr("yeaboi.tools.github._get_github_client", lambda: client)

        items = github_recent_prs("owner/repo", days=1)
        assert [i["kind"] for i in items] == ["pr"]
        pr.get_commits.assert_not_called()


class TestLocalGitAuthorEmail:
    def test_email_captured(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        def git(*args):
            # See TestLocalGitRecentCommits.test_reads_real_repo — scrubbed env
            # so a git-hook parent can never redirect these to the project repo.
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=git_subprocess_env())

        git("init", "-q")
        git("config", "user.email", "dev@example.com")
        git("config", "user.name", "Dev Person")
        (repo / "a.txt").write_text("hi")
        git("add", ".")
        git("commit", "-q", "-m", "first commit")

        items = local_git_recent_commits(str(repo), days=1)
        assert items[0]["author_email"] == "dev@example.com"


class TestTicketTextFetch:
    """The ticket's own words, fetched for the standup's practice matcher.

    ``include_ticket_text`` is opt-in because reporting and performance reuse
    these same collectors over 28-90 day windows and would pay for text they
    drop immediately.
    """

    def _issue(self, key="PROJ-12", **fields):
        issue = MagicMock()
        issue.key = key
        issue.fields = SimpleNamespace(
            summary="Fix login",
            assignee=SimpleNamespace(displayName="Alice"),
            status=SimpleNamespace(name="In Progress"),
            updated="2026-07-10T09:00:00.000+0000",
            **fields,
        )
        return issue

    def _jira(self, monkeypatch, client):
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_project_key", lambda: "PROJ")
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_base_url", lambda: "https://x.atlassian.net")
        monkeypatch.setattr("yeaboi.tools.jira._acceptance_field_cache", None)
        monkeypatch.setattr("yeaboi.tools.jira._dod_field_cache", None)

    def test_off_by_default(self, monkeypatch):
        client = MagicMock()
        client.search_issues.return_value = [self._issue(description="Rename the plugins")]
        self._jira(monkeypatch, client)

        items = jira_recent_activity("PROJ", days=2, include_wip=False)
        assert "body" not in items[0]
        assert "description" not in client.search_issues.call_args.kwargs["fields"]
        client.fields.assert_not_called()  # no discovery request either

    def test_description_acceptance_and_definition_of_done_land_on_body(self, monkeypatch):
        client = MagicMock()
        client.fields.return_value = [
            {"id": "customfield_1", "name": "Acceptance Criteria"},
            {"id": "customfield_2", "name": "Definition of Done"},
        ]
        client.search_issues.return_value = [
            self._issue(
                description="h2. Goal\nRename the plugins",
                customfield_1="AC1: the new names are used",
                customfield_2="- Documentation",
            )
        ]
        self._jira(monkeypatch, client)

        items = jira_recent_activity("PROJ", days=2, include_wip=False, include_ticket_text=True)
        body = items[0]["body"]
        assert "Rename the plugins" in body
        assert "AC1: the new names are used" in body
        assert "- Documentation" in body  # the section the docs carve-out reads
        fields = client.search_issues.call_args.kwargs["fields"]
        assert "description" in fields and "customfield_1" in fields and "customfield_2" in fields

    def test_the_wip_query_asks_for_the_same_fields(self, monkeypatch):
        # Jira's WIP search is a SEPARATE request: without this it returns
        # title-only tickets that relatedness cannot match against.
        client = MagicMock()
        client.fields.return_value = [{"id": "customfield_1", "name": "Acceptance Criteria"}]
        client.search_issues.return_value = []
        self._jira(monkeypatch, client)

        jira_recent_activity("PROJ", days=2, include_ticket_text=True)
        wip_fields = client.search_issues.call_args_list[1].kwargs["fields"]
        assert "description" in wip_fields and "customfield_1" in wip_fields

    def test_a_rejected_field_costs_the_text_not_the_activity(self, monkeypatch):
        from jira import JIRAError

        client = MagicMock()
        client.fields.return_value = [{"id": "customfield_stale", "name": "Acceptance Criteria"}]
        client.search_issues.side_effect = [JIRAError(status_code=400, text="bad field"), [self._issue()]]
        self._jira(monkeypatch, client)

        items = jira_recent_activity("PROJ", days=2, include_wip=False, include_ticket_text=True)
        assert [i["key"] for i in items] == ["PROJ-12"]
        assert items[0]["body"] == ""
        assert (
            client.search_issues.call_args.kwargs["fields"]
            == "summary,assignee,status,updated,comment,issuetype,parent"
        )

    def test_field_discovery_failure_degrades_to_empty_text(self, monkeypatch):
        client = MagicMock()
        client.fields.side_effect = RuntimeError("api down")
        client.search_issues.return_value = [self._issue(description="Rename the plugins")]
        self._jira(monkeypatch, client)

        items = jira_recent_activity("PROJ", days=2, include_wip=False, include_ticket_text=True)
        assert "Rename the plugins" in items[0]["body"]  # description still rides along

    def test_comment_items_never_carry_ticket_text(self, monkeypatch):
        # "comment" is one of automation.py's detectable kinds; a description
        # there would go through scanner-marker matching and burst fingerprinting.
        recent = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        comment = SimpleNamespace(created=recent, author=SimpleNamespace(displayName="Bob"))
        issue = self._issue(description="Rename the plugins", comment=SimpleNamespace(comments=[comment]))
        client = MagicMock()
        client.fields.return_value = []
        client.search_issues.return_value = [issue]
        self._jira(monkeypatch, client)

        items = jira_recent_activity("PROJ", days=2, include_wip=False, include_ticket_text=True)
        assert [i.get("body", "") for i in items if i["kind"] == "comment"] == [""]

    def test_azdo_description_and_acceptance_land_on_body(self, monkeypatch):
        from yeaboi.tools import azure_devops as azdo

        work_item = SimpleNamespace(
            fields={
                "System.Id": 7,
                "System.Title": "Rename the plugins",
                "System.State": "Active",
                "System.AssignedTo": {"displayName": "Alice"},
                "System.ChangedBy": {"displayName": "Alice"},
                "System.ChangedDate": "2026-07-10T09:00:00Z",
                "System.Description": "<p>Rename the pipeline approval plugin</p>",
                "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>New names used</li></ul>",
            }
        )
        wit = MagicMock()
        wit.query_by_wiql.return_value = SimpleNamespace(work_items=[SimpleNamespace(id=7)])
        wit.get_work_items.return_value = [work_item]
        monkeypatch.setattr(azdo, "_make_azdo_clients", lambda: (wit, MagicMock()))
        monkeypatch.setattr(azdo, "get_azure_devops_org_url", lambda: "https://dev.azure.com/acme")

        items = azdevops_recent_activity("Proj", days=1, include_ticket_text=True)
        body = next(i["body"] for i in items if i["kind"] == "work_item")
        assert "Rename the pipeline approval plugin" in body
        assert "New names used" in body
        assert "System.Description" in wit.get_work_items.call_args.kwargs["fields"]

    def test_azdo_unknown_field_falls_back_to_the_base_list(self, monkeypatch):
        # A custom inherited process can drop AcceptanceCriteria, and Azure
        # answers with a 400 — which would empty the whole ticketing source.
        from yeaboi.tools import azure_devops as azdo

        work_item = SimpleNamespace(
            fields={
                "System.Id": 7,
                "System.Title": "Rename the plugins",
                "System.State": "Active",
                "System.AssignedTo": {"displayName": "Alice"},
                "System.ChangedBy": {"displayName": "Alice"},
                "System.ChangedDate": "2026-07-10T09:00:00Z",
            }
        )
        wit = MagicMock()
        wit.query_by_wiql.return_value = SimpleNamespace(work_items=[SimpleNamespace(id=7)])
        wit.get_work_items.side_effect = [Exception("TF51535 unknown field"), [work_item], [work_item]]
        monkeypatch.setattr(azdo, "_make_azdo_clients", lambda: (wit, MagicMock()))
        monkeypatch.setattr(azdo, "get_azure_devops_org_url", lambda: "https://dev.azure.com/acme")

        items = azdevops_recent_activity("Proj", days=1, include_ticket_text=True)
        assert [i["key"] for i in items if i["kind"] == "work_item"] == ["#7"]
