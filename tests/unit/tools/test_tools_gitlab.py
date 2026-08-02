"""Tests for GitLab tools.

Every GitLab API call is mocked via monkeypatch on ``_make_gitlab_client`` so no
real network requests are made and no token is needed — the default suite stays
green with zero credentials. Tests cover the happy path and an error path for
each tool, the missing-credential path, the helpers, token masking, and
registration in ``get_tools()``. Mirrors test_tools_notion.py.
"""

from unittest.mock import MagicMock

import gitlab.exceptions
import pytest

from yeaboi.tools import get_tools
from yeaboi.tools.gitlab import (
    _MISSING_CONFIG_MSG,
    _format_tree,
    _gitlab_error_msg,
    _make_gitlab_client,
    _parse_project,
    gitlab_create_issue,
    gitlab_list_issues,
    gitlab_read_readme,
    gitlab_read_repo,
)

# ---------------------------------------------------------------------------
# Helpers — build mock GitLab errors and SDK objects
# ---------------------------------------------------------------------------


def _api_error(status: int, message: str = "boom") -> gitlab.exceptions.GitlabError:
    """Build a real python-gitlab error carrying an HTTP status code."""
    return gitlab.exceptions.GitlabGetError(message, response_code=status)


def _mock_project(**overrides):
    """A MagicMock standing in for a python-gitlab Project object."""
    project = MagicMock()
    project.name = overrides.get("name", "My Project")
    project.path_with_namespace = overrides.get("path_with_namespace", "group/my-project")
    project.description = overrides.get("description", "A project")
    project.web_url = overrides.get("web_url", "https://gitlab.com/group/my-project")
    project.default_branch = overrides.get("default_branch", "main")
    project.visibility = overrides.get("visibility", "private")
    project.last_activity_at = overrides.get("last_activity_at", "2026-01-01T00:00:00Z")
    project.star_count = overrides.get("star_count", 3)
    project.forks_count = overrides.get("forks_count", 1)
    project.topics = overrides.get("topics", ["python"])
    project.repository_tree.return_value = overrides.get(
        "tree", [{"name": "src", "type": "tree"}, {"name": "pyproject.toml", "type": "blob"}]
    )
    return project


def _client_returning(project) -> MagicMock:
    client = MagicMock()
    client.projects.get.return_value = project
    return client


def _patch_client(monkeypatch, client) -> None:
    """Point every tool in the module at *client* (or None for 'no token')."""
    monkeypatch.setattr("yeaboi.tools.gitlab._make_gitlab_client", lambda *a, **k: client)


def _mock_issue(iid=7, title="Fix login", labels=None, assignee=None, author=None):
    issue = MagicMock()
    issue.iid = iid
    issue.title = title
    issue.labels = labels if labels is not None else ["bug"]
    issue.assignee = assignee
    issue.author = author if author is not None else {"name": "Ada"}
    issue.web_url = f"https://gitlab.com/group/my-project/-/issues/{iid}"
    return issue


# ---------------------------------------------------------------------------
# _parse_project
# ---------------------------------------------------------------------------


class TestParseProject:
    def test_full_url(self):
        assert _parse_project("https://gitlab.com/group/my-project") == "group/my-project"

    def test_nested_subgroups_are_preserved(self):
        """GitLab projects nest arbitrarily deep — unlike GitHub's fixed owner/repo."""
        url = "https://gitlab.com/org/team/sub/my-project"
        assert _parse_project(url) == "org/team/sub/my-project"

    def test_self_hosted_host(self):
        assert _parse_project("https://gitlab.example.com/group/proj") == "group/proj"

    def test_strips_dot_git(self):
        assert _parse_project("https://gitlab.com/group/proj.git") == "group/proj"

    def test_strips_web_ui_suffix(self):
        assert _parse_project("https://gitlab.com/group/proj/-/tree/main") == "group/proj"

    def test_strips_issues_suffix(self):
        assert _parse_project("https://gitlab.com/group/proj/-/issues") == "group/proj"

    def test_bare_path_passes_through(self):
        assert _parse_project("group/proj") == "group/proj"

    def test_trailing_slash_and_whitespace(self):
        assert _parse_project("  https://gitlab.com/group/proj/  ") == "group/proj"

    def test_empty_input(self):
        assert _parse_project("   ") == ""


# ---------------------------------------------------------------------------
# _gitlab_error_msg
# ---------------------------------------------------------------------------


class TestGitlabErrorMsg:
    def test_401_names_the_env_var(self):
        assert "GITLAB_TOKEN" in _gitlab_error_msg(_api_error(401))

    def test_403_names_the_scope(self):
        msg = _gitlab_error_msg(_api_error(403))
        assert "read_api" in msg

    def test_404_mentions_the_url(self):
        assert "not found" in _gitlab_error_msg(_api_error(404))

    def test_429_is_rate_limit(self):
        assert "rate limit" in _gitlab_error_msg(_api_error(429))

    def test_unknown_code_falls_through(self):
        assert "500" in _gitlab_error_msg(_api_error(500))

    def test_error_without_status_code(self):
        msg = _gitlab_error_msg(gitlab.exceptions.GitlabError("odd failure"))
        assert "odd failure" in msg


# ---------------------------------------------------------------------------
# _format_tree
# ---------------------------------------------------------------------------


class TestFormatTree:
    def test_directories_get_a_slash(self):
        assert _format_tree([{"name": "src", "type": "tree"}]) == ["  src/"]

    def test_key_files_are_starred(self):
        assert _format_tree([{"name": "pyproject.toml", "type": "blob"}]) == ["  pyproject.toml ★"]

    def test_gitlab_ci_counts_as_a_key_file(self):
        assert "★" in _format_tree([{"name": ".gitlab-ci.yml", "type": "blob"}])[0]

    def test_plain_file_is_not_starred(self):
        assert _format_tree([{"name": "notes.txt", "type": "blob"}]) == ["  notes.txt"]

    def test_long_tree_is_truncated_with_a_count(self):
        entries = [{"name": f"f{i}.py", "type": "blob"} for i in range(50)]
        lines = _format_tree(entries)
        assert len(lines) == 41  # 40 entries + the "… (10 more entries)" line
        assert "10 more entries" in lines[-1]

    def test_skips_malformed_entries(self):
        assert _format_tree(["nope", {"type": "blob"}]) == []


# ---------------------------------------------------------------------------
# _make_gitlab_client
# ---------------------------------------------------------------------------


class TestMakeGitlabClient:
    def test_returns_none_without_a_token(self, monkeypatch):
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        assert _make_gitlab_client() is None

    def test_builds_a_client_with_the_default_host(self, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "glpat-abc123")
        monkeypatch.delenv("GITLAB_URL", raising=False)
        client = _make_gitlab_client()
        assert client is not None
        assert client.url == "https://gitlab.com"

    def test_honours_a_self_hosted_url(self, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "glpat-abc123")
        monkeypatch.setenv("GITLAB_URL", "gitlab.internal.example.com")
        # A bare host must be normalised to https:// — python-gitlab joins the
        # API path onto this and would otherwise raise MissingSchema.
        assert _make_gitlab_client().url == "https://gitlab.internal.example.com"


# ---------------------------------------------------------------------------
# Missing credentials — every tool returns the shared message, none raise
# ---------------------------------------------------------------------------


class TestMissingCredentials:
    @pytest.mark.parametrize(
        ("fn", "kwargs"),
        [
            (gitlab_read_repo, {"repo_url": "group/proj"}),
            (gitlab_read_readme, {"repo_url": "group/proj"}),
            (gitlab_list_issues, {"repo_url": "group/proj"}),
            (gitlab_create_issue, {"repo_url": "group/proj", "title": "T"}),
        ],
    )
    def test_returns_missing_config_msg(self, monkeypatch, fn, kwargs):
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        assert fn.invoke(kwargs) == _MISSING_CONFIG_MSG

    def test_message_names_the_env_var(self):
        assert "GITLAB_TOKEN" in _MISSING_CONFIG_MSG


# ---------------------------------------------------------------------------
# gitlab_read_repo
# ---------------------------------------------------------------------------


class TestReadRepo:
    def test_happy_path_summarises_the_project(self, monkeypatch):
        _patch_client(monkeypatch, _client_returning(_mock_project()))
        result = gitlab_read_repo.invoke({"repo_url": "https://gitlab.com/group/my-project"})
        assert "group/my-project" in result
        assert "Default branch: main" in result
        assert "Visibility: private" in result
        assert "src/" in result
        assert "pyproject.toml ★" in result

    def test_includes_topics_when_present(self, monkeypatch):
        _patch_client(monkeypatch, _client_returning(_mock_project(topics=["scrum", "cli"])))
        assert "Topics: scrum, cli" in gitlab_read_repo.invoke({"repo_url": "group/proj"})

    def test_omits_topics_when_empty(self, monkeypatch):
        _patch_client(monkeypatch, _client_returning(_mock_project(topics=[])))
        assert "Topics:" not in gitlab_read_repo.invoke({"repo_url": "group/proj"})

    def test_empty_tree_renders_a_placeholder(self, monkeypatch):
        _patch_client(monkeypatch, _client_returning(_mock_project(tree=[])))
        assert "(empty)" in gitlab_read_repo.invoke({"repo_url": "group/proj"})

    def test_blank_url_is_rejected_before_any_api_call(self, monkeypatch):
        client = _client_returning(_mock_project())
        _patch_client(monkeypatch, client)
        assert gitlab_read_repo.invoke({"repo_url": "  "}).startswith("Error: Provide")
        client.projects.get.assert_not_called()

    def test_api_error_becomes_a_friendly_message(self, monkeypatch):
        client = MagicMock()
        client.projects.get.side_effect = _api_error(404)
        _patch_client(monkeypatch, client)
        assert "not found" in gitlab_read_repo.invoke({"repo_url": "group/missing"})

    def test_unexpected_error_is_caught(self, monkeypatch):
        client = MagicMock()
        client.projects.get.side_effect = ValueError("kaboom")
        _patch_client(monkeypatch, client)
        assert gitlab_read_repo.invoke({"repo_url": "group/proj"}) == "Error: kaboom"


# ---------------------------------------------------------------------------
# gitlab_read_readme
# ---------------------------------------------------------------------------


class TestReadReadme:
    def test_happy_path_returns_decoded_text(self, monkeypatch):
        project = _mock_project()
        project.files.get.return_value = MagicMock(decode=lambda: b"# Hello\n\nDocs here.")
        _patch_client(monkeypatch, _client_returning(project))
        result = gitlab_read_readme.invoke({"repo_url": "group/proj"})
        assert "# Hello" in result
        assert "README (README.md)" in result

    def test_falls_through_to_the_next_candidate_on_404(self, monkeypatch):
        project = _mock_project()
        project.files.get.side_effect = [
            _api_error(404),  # README.md missing
            MagicMock(decode=lambda: "rst body"),  # README.rst found
        ]
        _patch_client(monkeypatch, _client_returning(project))
        result = gitlab_read_readme.invoke({"repo_url": "group/proj"})
        assert "README.rst" in result
        assert "rst body" in result

    def test_no_readme_reports_cleanly(self, monkeypatch):
        project = _mock_project()
        project.files.get.side_effect = _api_error(404)
        _patch_client(monkeypatch, _client_returning(project))
        assert "No README found" in gitlab_read_readme.invoke({"repo_url": "group/proj"})

    def test_long_readme_is_truncated(self, monkeypatch):
        project = _mock_project()
        project.files.get.return_value = MagicMock(decode=lambda: "x" * 9_000)
        _patch_client(monkeypatch, _client_returning(project))
        result = gitlab_read_readme.invoke({"repo_url": "group/proj"})
        assert "[Truncated at 8000 characters]" in result

    def test_non_404_error_is_not_swallowed_by_the_candidate_loop(self, monkeypatch):
        """A 403 must surface as a permission message, not as 'No README found'."""
        project = _mock_project()
        project.files.get.side_effect = _api_error(403)
        _patch_client(monkeypatch, _client_returning(project))
        result = gitlab_read_readme.invoke({"repo_url": "group/proj"})
        assert "permission denied" in result.lower()

    def test_blank_url_is_rejected(self, monkeypatch):
        _patch_client(monkeypatch, _client_returning(_mock_project()))
        assert gitlab_read_readme.invoke({"repo_url": ""}).startswith("Error: Provide")


# ---------------------------------------------------------------------------
# gitlab_list_issues
# ---------------------------------------------------------------------------


class TestListIssues:
    def test_happy_path_lists_issues(self, monkeypatch):
        project = _mock_project()
        project.issues.list.return_value = [_mock_issue(), _mock_issue(iid=8, title="Add tests")]
        _patch_client(monkeypatch, _client_returning(project))
        result = gitlab_list_issues.invoke({"repo_url": "group/proj"})
        assert "#7: Fix login" in result
        assert "#8: Add tests" in result
        assert "Labels: bug" in result
        assert "(2 issues shown)" in result

    def test_assignee_wins_over_author(self, monkeypatch):
        project = _mock_project()
        project.issues.list.return_value = [_mock_issue(assignee={"name": "Grace"})]
        _patch_client(monkeypatch, _client_returning(project))
        result = gitlab_list_issues.invoke({"repo_url": "group/proj"})
        assert "Assignee: Grace" in result
        assert "Author:" not in result

    def test_falls_back_to_author_when_unassigned(self, monkeypatch):
        project = _mock_project()
        project.issues.list.return_value = [_mock_issue(assignee=None)]
        _patch_client(monkeypatch, _client_returning(project))
        assert "Author: Ada" in gitlab_list_issues.invoke({"repo_url": "group/proj"})

    def test_empty_backlog_reports_cleanly(self, monkeypatch):
        project = _mock_project()
        project.issues.list.return_value = []
        _patch_client(monkeypatch, _client_returning(project))
        assert "No opened issues found" in gitlab_list_issues.invoke({"repo_url": "group/proj"})

    def test_limit_is_enforced_client_side(self, monkeypatch):
        project = _mock_project()
        project.issues.list.return_value = [_mock_issue(iid=i) for i in range(10)]
        _patch_client(monkeypatch, _client_returning(project))
        result = gitlab_list_issues.invoke({"repo_url": "group/proj", "limit": 3})
        assert "(3 issues shown)" in result

    def test_oversized_limit_is_clamped_before_the_api_call(self, monkeypatch):
        """GitLab 400s on per_page > 100, so an over-eager LLM limit must be clamped."""
        project = _mock_project()
        project.issues.list.return_value = []
        _patch_client(monkeypatch, _client_returning(project))
        gitlab_list_issues.invoke({"repo_url": "group/proj", "limit": 5000})
        assert project.issues.list.call_args.kwargs["per_page"] == 100

    def test_invalid_state_is_rejected(self, monkeypatch):
        project = _mock_project()
        _patch_client(monkeypatch, _client_returning(project))
        result = gitlab_list_issues.invoke({"repo_url": "group/proj", "state": "wibble"})
        assert "state must be" in result
        project.issues.list.assert_not_called()

    def test_api_error_becomes_a_friendly_message(self, monkeypatch):
        project = _mock_project()
        project.issues.list.side_effect = _api_error(401)
        _patch_client(monkeypatch, _client_returning(project))
        assert "authentication failed" in gitlab_list_issues.invoke({"repo_url": "group/proj"})


# ---------------------------------------------------------------------------
# gitlab_create_issue  (WRITE — gated behind human_review)
# ---------------------------------------------------------------------------


class TestCreateIssue:
    def test_happy_path_returns_the_new_issue(self, monkeypatch):
        project = _mock_project()
        project.issues.create.return_value = _mock_issue(iid=42, title="New story")
        _patch_client(monkeypatch, _client_returning(project))
        result = gitlab_create_issue.invoke({"repo_url": "group/proj", "title": "New story"})
        assert "#42" in result
        assert "issues/42" in result

    def test_labels_are_split_on_commas(self, monkeypatch):
        project = _mock_project()
        project.issues.create.return_value = _mock_issue()
        _patch_client(monkeypatch, _client_returning(project))
        gitlab_create_issue.invoke(
            {"repo_url": "group/proj", "title": "T", "description": "D", "labels": "backend, sprint-1 , "}
        )
        payload = project.issues.create.call_args[0][0]
        assert payload["labels"] == ["backend", "sprint-1"]
        assert payload["description"] == "D"

    def test_no_labels_key_when_none_given(self, monkeypatch):
        project = _mock_project()
        project.issues.create.return_value = _mock_issue()
        _patch_client(monkeypatch, _client_returning(project))
        gitlab_create_issue.invoke({"repo_url": "group/proj", "title": "T"})
        assert "labels" not in project.issues.create.call_args[0][0]

    def test_blank_title_is_rejected_before_the_api_call(self, monkeypatch):
        project = _mock_project()
        _patch_client(monkeypatch, _client_returning(project))
        assert "Provide a title" in gitlab_create_issue.invoke({"repo_url": "group/proj", "title": "   "})
        project.issues.create.assert_not_called()

    def test_insufficient_scope_error_is_explained(self, monkeypatch):
        project = _mock_project()
        project.issues.create.side_effect = _api_error(403)
        _patch_client(monkeypatch, _client_returning(project))
        result = gitlab_create_issue.invoke({"repo_url": "group/proj", "title": "T"})
        assert "'api'" in result

    def test_unexpected_error_is_caught(self, monkeypatch):
        project = _mock_project()
        project.issues.create.side_effect = RuntimeError("nope")
        _patch_client(monkeypatch, _client_returning(project))
        assert gitlab_create_issue.invoke({"repo_url": "group/proj", "title": "T"}) == "Error: nope"

    def test_docstring_carries_the_confirmation_guard(self):
        """The human_review gate is enforced by risk.py, but the LLM also reads this."""
        assert "after the user has explicitly confirmed" in gitlab_create_issue.description


# ---------------------------------------------------------------------------
# Token masking — a GitLab PAT must never reach a log or an export
# ---------------------------------------------------------------------------


class TestTokenMasking:
    def test_env_token_value_is_redacted(self, monkeypatch):
        from yeaboi.redaction import redact

        monkeypatch.setenv("GITLAB_TOKEN", "supersecrettokenvalue123")
        assert "supersecrettokenvalue123" not in redact("token=supersecrettokenvalue123 in the log")

    def test_glpat_shape_is_redacted_without_the_env_var(self, monkeypatch):
        """Catches a token pasted into a transcript by a user we never configured.

        The sample is assembled at runtime rather than written as a literal:
        it is fake, but it is convincing enough that GitHub push protection and
        gitleaks both flag the source line otherwise. Adjacent-literal
        concatenation keeps the PAT shape out of the file while the value the
        test actually exercises is unchanged.
        """
        from yeaboi.redaction import redact

        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        fake_pat = "glpat" + "-AbCdEf1234567890xyzQ"
        text = f"here it is: {fake_pat}"
        assert fake_pat not in redact(text)
        assert "[REDACTED]" in redact(text)

    def test_gitlab_token_is_in_the_secret_key_registry(self):
        from yeaboi.redaction import SECRET_ENV_KEYS

        assert "GITLAB_TOKEN" in SECRET_ENV_KEYS


# ---------------------------------------------------------------------------
# Registration + risk classification
# ---------------------------------------------------------------------------


class TestGitlabToolsRegistered:
    def test_all_four_tools_are_in_get_tools(self):
        names = {t.name for t in get_tools()}
        assert {
            "gitlab_read_repo",
            "gitlab_read_readme",
            "gitlab_list_issues",
            "gitlab_create_issue",
        } <= names

    def test_only_the_create_tool_is_high_risk(self):
        from yeaboi.tools.risk import high_risk_tool_names

        gate = high_risk_tool_names()
        assert "gitlab_create_issue" in gate
        assert "gitlab_read_repo" not in gate
        assert "gitlab_read_readme" not in gate
        assert "gitlab_list_issues" not in gate
