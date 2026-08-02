"""Tests for GitLab tools.

All GitLab API calls are mocked via monkeypatch on _make_gitlab_client so no real
network requests are made. Tests cover happy paths, error cases, and edge cases
for each tool, plus helpers, registration in get_tools(), risk classification,
and token masking. Mirrors test_tools_notion.py / test_tools_github.py.
"""

from unittest.mock import MagicMock

import pytest
from gitlab.exceptions import GitlabAuthenticationError, GitlabCreateError, GitlabGetError

from yeaboi.tools import get_tools
from yeaboi.tools.gitlab import (
    _MISSING_CONFIG_MSG,
    _gitlab_error_msg,
    _parse_project,
    _truncate,
    gitlab_create_issue,
    gitlab_list_issues,
    gitlab_read_readme,
    gitlab_read_repo,
)

# ---------------------------------------------------------------------------
# Helpers — build mock python-gitlab objects
# ---------------------------------------------------------------------------


def _make_project(
    path: str = "group/project",
    description: str = "A test project",
    default_branch: str = "main",
) -> MagicMock:
    """Build a mock python-gitlab Project with the attributes the tools read."""
    project = MagicMock()
    project.path_with_namespace = path
    project.description = description
    project.default_branch = default_branch
    project.star_count = 7
    project.forks_count = 2
    project.open_issues_count = 3
    project.repository_tree.return_value = [
        {"path": "src", "type": "tree"},
        {"path": "src/api", "type": "tree"},
        {"path": "src/api/deep", "type": "tree"},
        {"path": "src/main.py", "type": "blob"},
        {"path": "pyproject.toml", "type": "blob"},
        {"path": ".gitlab-ci.yml", "type": "blob"},
        {"path": "packages/worker/pyproject.toml", "type": "blob"},
    ]
    project.languages.return_value = {"Python": 85.5, "Shell": 14.5}
    return project


def _make_client(project: MagicMock | None = None) -> MagicMock:
    client = MagicMock()
    client.projects.get.return_value = project if project is not None else _make_project()
    return client


def _make_file(content: str) -> MagicMock:
    """Mock a repository file blob — .decode() returns bytes, as the SDK does."""
    blob = MagicMock()
    blob.decode.return_value = content.encode("utf-8")
    return blob


def _make_issue(iid: int = 1, title: str = "Fix the thing", description: str = "", labels=None) -> MagicMock:
    issue = MagicMock()
    issue.iid = iid
    issue.title = title
    issue.description = description
    issue.labels = labels if labels is not None else []
    issue.web_url = f"https://gitlab.com/group/project/-/issues/{iid}"
    return issue


def _http_error(cls, code: int):
    """Build a python-gitlab error carrying an HTTP status code."""
    err = cls("boom")
    err.response_code = code
    return err


@pytest.fixture
def patch_client(monkeypatch):
    """Return a factory that installs a mock client and hands it back."""

    def _install(client: MagicMock | None):
        monkeypatch.setattr("yeaboi.tools.gitlab._make_gitlab_client", lambda: client)
        return client

    return _install


# ---------------------------------------------------------------------------
# _parse_project
# ---------------------------------------------------------------------------


class TestParseProject:
    def test_plain_url(self):
        assert _parse_project("https://gitlab.com/group/project") == "group/project"

    def test_nested_groups_are_preserved(self):
        # The GitHub parser keeps only two segments; GitLab subgroups need all of them.
        assert _parse_project("https://gitlab.com/group/sub/team/project") == "group/sub/team/project"

    def test_git_suffix_stripped(self):
        assert _parse_project("https://gitlab.com/group/project.git") == "group/project"

    def test_trailing_slash_stripped(self):
        assert _parse_project("https://gitlab.com/group/project/") == "group/project"

    def test_ui_path_after_dash_separator_stripped(self):
        assert _parse_project("https://gitlab.com/group/project/-/tree/main") == "group/project"

    def test_self_hosted_host_stripped(self):
        assert _parse_project("https://gitlab.example.com/group/project") == "group/project"

    def test_bare_slug_passes_through(self):
        assert _parse_project("group/project") == "group/project"

    def test_whitespace_stripped(self):
        assert _parse_project("  group/project  ") == "group/project"

    def test_empty_string_returns_empty(self):
        assert _parse_project("") == ""


# ---------------------------------------------------------------------------
# _gitlab_error_msg / _truncate
# ---------------------------------------------------------------------------


class TestErrorMessages:
    def test_401_names_the_env_var(self):
        msg = _gitlab_error_msg(_http_error(GitlabAuthenticationError, 401))
        assert "GITLAB_TOKEN" in msg

    def test_403_names_the_scope(self):
        msg = _gitlab_error_msg(_http_error(GitlabGetError, 403))
        assert "read_api" in msg

    def test_404_mentions_the_project(self):
        assert "not found" in _gitlab_error_msg(_http_error(GitlabGetError, 404))

    def test_429_is_a_rate_limit(self):
        assert "rate limit" in _gitlab_error_msg(_http_error(GitlabGetError, 429))

    def test_unknown_code_falls_back_to_the_raw_error(self):
        assert "boom" in _gitlab_error_msg(_http_error(GitlabGetError, 500))

    def test_error_without_status_code(self):
        assert "plain" in _gitlab_error_msg(ValueError("plain"))


class TestTruncate:
    def test_short_content_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_long_content_gets_marker(self):
        result = _truncate("x" * 9_000)
        assert "[Truncated at 8000 characters]" in result
        assert len(result) < 9_000


# ---------------------------------------------------------------------------
# gitlab_read_repo
# ---------------------------------------------------------------------------


class TestReadRepo:
    def test_happy_path(self, patch_client):
        patch_client(_make_client())
        result = gitlab_read_repo.invoke({"project_url": "https://gitlab.com/group/project"})
        assert "Project: group/project" in result
        assert "Default branch: main" in result
        assert "src/" in result
        assert "Description: A test project" in result

    def test_key_files_are_surfaced(self, patch_client):
        patch_client(_make_client())
        result = gitlab_read_repo.invoke({"project_url": "group/project"})
        assert "pyproject.toml" in result
        assert ".gitlab-ci.yml" in result

    def test_key_files_are_found_at_any_depth(self, patch_client):
        # A monorepo's nested manifests are as informative as the root one.
        patch_client(_make_client())
        result = gitlab_read_repo.invoke({"project_url": "group/project", "max_depth": 1})
        assert "packages/worker/pyproject.toml" in result

    def test_max_depth_limits_the_directory_listing(self, patch_client):
        patch_client(_make_client())
        shallow = gitlab_read_repo.invoke({"project_url": "group/project", "max_depth": 1})
        assert "  src/" in shallow
        assert "src/api/" not in shallow

    def test_deeper_max_depth_shows_nested_dirs(self, patch_client):
        patch_client(_make_client())
        deep = gitlab_read_repo.invoke({"project_url": "group/project", "max_depth": 2})
        assert "src/api/" in deep
        assert "src/api/deep/" not in deep

    def test_only_directories_are_listed(self, patch_client):
        patch_client(_make_client())
        result = gitlab_read_repo.invoke({"project_url": "group/project", "max_depth": 2})
        tree_section = result.split("Key files detected:")[0]
        assert "src/main.py/" not in tree_section

    def test_languages_included(self, patch_client):
        patch_client(_make_client())
        result = gitlab_read_repo.invoke({"project_url": "group/project"})
        assert "Python: 85.5%" in result

    def test_language_failure_still_returns_the_tree(self, patch_client):
        project = _make_project()
        project.languages.side_effect = _http_error(GitlabGetError, 403)
        patch_client(_make_client(project))
        result = gitlab_read_repo.invoke({"project_url": "group/project"})
        assert "Project: group/project" in result
        assert "Languages:" not in result

    def test_missing_credentials(self, patch_client):
        patch_client(None)
        assert gitlab_read_repo.invoke({"project_url": "group/project"}) == _MISSING_CONFIG_MSG

    def test_empty_url_is_an_error_not_a_crash(self, patch_client):
        patch_client(_make_client())
        result = gitlab_read_repo.invoke({"project_url": ""})
        assert result.startswith("Error:")

    def test_api_error(self, patch_client):
        client = _make_client()
        client.projects.get.side_effect = _http_error(GitlabGetError, 404)
        patch_client(client)
        result = gitlab_read_repo.invoke({"project_url": "group/nope"})
        assert result.startswith("Error:")
        assert "not found" in result

    def test_unexpected_error(self, patch_client):
        client = _make_client()
        client.projects.get.side_effect = RuntimeError("kaboom")
        patch_client(client)
        assert "kaboom" in gitlab_read_repo.invoke({"project_url": "group/project"})


# ---------------------------------------------------------------------------
# gitlab_read_readme
# ---------------------------------------------------------------------------


class TestReadReadme:
    def test_happy_path(self, patch_client):
        project = _make_project()
        project.files.get.side_effect = lambda file_path, ref: (
            _make_file("# Hello") if file_path == "README.md" else _raise_missing()
        )
        patch_client(_make_client(project))
        result = gitlab_read_readme.invoke({"project_url": "group/project"})
        assert "=== README (README.md) ===" in result
        assert "# Hello" in result

    def test_falls_back_through_readme_spellings(self, patch_client):
        project = _make_project()
        project.files.get.side_effect = lambda file_path, ref: (
            _make_file("rst readme") if file_path == "README.rst" else _raise_missing()
        )
        patch_client(_make_client(project))
        result = gitlab_read_readme.invoke({"project_url": "group/project"})
        assert "=== README (README.rst) ===" in result

    def test_contributing_included_when_present(self, patch_client):
        project = _make_project()
        project.files.get.side_effect = lambda file_path, ref: _make_file(f"body of {file_path}")
        patch_client(_make_client(project))
        result = gitlab_read_readme.invoke({"project_url": "group/project"})
        assert "=== CONTRIBUTING.md ===" in result

    def test_no_readme_is_reported_not_raised(self, patch_client):
        project = _make_project()
        project.files.get.side_effect = lambda file_path, ref: _raise_missing()
        patch_client(_make_client(project))
        result = gitlab_read_readme.invoke({"project_url": "group/project"})
        assert "No README found" in result

    def test_long_readme_truncated(self, patch_client):
        project = _make_project()
        project.files.get.side_effect = lambda file_path, ref: (
            _make_file("y" * 9_000) if file_path == "README.md" else _raise_missing()
        )
        patch_client(_make_client(project))
        result = gitlab_read_readme.invoke({"project_url": "group/project"})
        assert "[Truncated at 8000 characters]" in result

    def test_missing_credentials(self, patch_client):
        patch_client(None)
        assert gitlab_read_readme.invoke({"project_url": "group/project"}) == _MISSING_CONFIG_MSG

    def test_api_error(self, patch_client):
        client = _make_client()
        client.projects.get.side_effect = _http_error(GitlabAuthenticationError, 401)
        patch_client(client)
        result = gitlab_read_readme.invoke({"project_url": "group/project"})
        assert "GITLAB_TOKEN" in result


def _raise_missing():
    """Raise the SDK's 404 for a file that does not exist."""
    raise _http_error(GitlabGetError, 404)


# ---------------------------------------------------------------------------
# gitlab_list_issues
# ---------------------------------------------------------------------------


class TestListIssues:
    def test_happy_path(self, patch_client):
        project = _make_project()
        project.issues.list.return_value = [
            _make_issue(1, "Fix login", "Users cannot log in", ["bug"]),
            _make_issue(2, "Add export"),
        ]
        patch_client(_make_client(project))
        result = gitlab_list_issues.invoke({"project_url": "group/project"})
        assert "#1: Fix login [bug]" in result
        assert "Users cannot log in" in result
        assert "#2: Add export" in result
        assert "(2 issues shown)" in result

    def test_default_state_is_gitlabs_spelling(self, patch_client):
        project = _make_project()
        project.issues.list.return_value = []
        patch_client(_make_client(project))
        gitlab_list_issues.invoke({"project_url": "group/project"})
        assert project.issues.list.call_args.kwargs["state"] == "opened"

    def test_githubs_open_spelling_is_rejected(self, patch_client):
        patch_client(_make_client())
        result = gitlab_list_issues.invoke({"project_url": "group/project", "state": "open"})
        assert result.startswith("Error:")
        assert "opened" in result

    def test_state_is_case_insensitive(self, patch_client):
        project = _make_project()
        project.issues.list.return_value = []
        patch_client(_make_client(project))
        result = gitlab_list_issues.invoke({"project_url": "group/project", "state": "ALL"})
        assert not result.startswith("Error:")
        assert project.issues.list.call_args.kwargs["state"] == "all"

    def test_long_description_is_previewed(self, patch_client):
        project = _make_project()
        project.issues.list.return_value = [_make_issue(1, "Big", "z" * 400)]
        patch_client(_make_client(project))
        result = gitlab_list_issues.invoke({"project_url": "group/project"})
        assert "..." in result

    def test_max_issues_caps_output(self, patch_client):
        project = _make_project()
        project.issues.list.return_value = [_make_issue(i) for i in range(1, 11)]
        patch_client(_make_client(project))
        result = gitlab_list_issues.invoke({"project_url": "group/project", "max_issues": 3})
        assert "(3 issues shown; increase max_issues to see more)" in result

    def test_no_issues(self, patch_client):
        project = _make_project()
        project.issues.list.return_value = []
        patch_client(_make_client(project))
        assert "No opened issues found." in gitlab_list_issues.invoke({"project_url": "group/project"})

    def test_missing_credentials(self, patch_client):
        patch_client(None)
        assert gitlab_list_issues.invoke({"project_url": "group/project"}) == _MISSING_CONFIG_MSG

    def test_api_error(self, patch_client):
        client = _make_client()
        client.projects.get.side_effect = _http_error(GitlabGetError, 404)
        patch_client(client)
        assert gitlab_list_issues.invoke({"project_url": "group/x"}).startswith("Error:")


# ---------------------------------------------------------------------------
# gitlab_create_issue  (WRITE — gated behind human_review)
# ---------------------------------------------------------------------------


class TestCreateIssue:
    def test_happy_path(self, patch_client):
        project = _make_project()
        project.issues.create.return_value = _make_issue(42, "New story")
        patch_client(_make_client(project))
        result = gitlab_create_issue.invoke({"project_url": "group/project", "title": "New story"})
        assert "Created GitLab issue #42" in result
        assert "https://gitlab.com/group/project/-/issues/42" in result

    def test_labels_are_split_into_a_list(self, patch_client):
        project = _make_project()
        project.issues.create.return_value = _make_issue(1, "T")
        patch_client(_make_client(project))
        gitlab_create_issue.invoke(
            {"project_url": "group/project", "title": "T", "labels": "bug, sprint-1 , "},
        )
        assert project.issues.create.call_args[0][0]["labels"] == ["bug", "sprint-1"]

    def test_no_labels_key_when_blank(self, patch_client):
        project = _make_project()
        project.issues.create.return_value = _make_issue(1, "T")
        patch_client(_make_client(project))
        gitlab_create_issue.invoke({"project_url": "group/project", "title": "T"})
        assert "labels" not in project.issues.create.call_args[0][0]

    def test_blank_title_rejected_before_any_api_call(self, patch_client):
        client = _make_client()
        patch_client(client)
        result = gitlab_create_issue.invoke({"project_url": "group/project", "title": "   "})
        assert result.startswith("Error:")
        client.projects.get.assert_not_called()

    def test_missing_credentials(self, patch_client):
        patch_client(None)
        result = gitlab_create_issue.invoke({"project_url": "group/project", "title": "T"})
        assert result == _MISSING_CONFIG_MSG

    def test_api_error(self, patch_client):
        project = _make_project()
        project.issues.create.side_effect = _http_error(GitlabCreateError, 403)
        patch_client(_make_client(project))
        result = gitlab_create_issue.invoke({"project_url": "group/project", "title": "T"})
        assert "read_api" in result

    def test_unexpected_error(self, patch_client):
        project = _make_project()
        project.issues.create.side_effect = RuntimeError("nope")
        patch_client(_make_client(project))
        assert "nope" in gitlab_create_issue.invoke({"project_url": "group/project", "title": "T"})

    def test_docstring_carries_the_confirmation_guard(self):
        # The human_review gate is driven by risk.py, but the docstring is what
        # tells the LLM not to call this unprompted. @tool moves the docstring to
        # .description — that string is what is actually sent to the model.
        assert "only call this after the user has explicitly confirmed" in gitlab_create_issue.description.lower()


# ---------------------------------------------------------------------------
# Client construction + config
# ---------------------------------------------------------------------------


class TestClientConstruction:
    def test_no_token_returns_none(self, monkeypatch):
        from yeaboi.tools import gitlab as gitlab_tools

        monkeypatch.setattr(gitlab_tools, "get_gitlab_token", lambda: None)
        assert gitlab_tools._make_gitlab_client() is None

    def test_token_builds_a_client_for_the_configured_url(self, monkeypatch):
        from yeaboi.tools import gitlab as gitlab_tools

        monkeypatch.setattr(gitlab_tools, "get_gitlab_token", lambda: "glpat-secret")
        monkeypatch.setattr(gitlab_tools, "get_gitlab_url", lambda: "https://gitlab.example.com")
        captured = {}

        def _fake_gitlab(url, private_token):
            captured["url"] = url
            captured["token"] = private_token
            return MagicMock()

        monkeypatch.setattr(gitlab_tools.gitlab, "Gitlab", _fake_gitlab)
        assert gitlab_tools._make_gitlab_client() is not None
        assert captured == {"url": "https://gitlab.example.com", "token": "glpat-secret"}


class TestConfigGetters:
    def test_url_defaults_to_gitlab_com(self, monkeypatch):
        from yeaboi import config

        monkeypatch.delenv("GITLAB_URL", raising=False)
        assert config.get_gitlab_url() == "https://gitlab.com"

    def test_url_gets_a_scheme_when_missing(self, monkeypatch):
        from yeaboi import config

        monkeypatch.setenv("GITLAB_URL", "gitlab.example.com")
        assert config.get_gitlab_url() == "https://gitlab.example.com"

    def test_url_trailing_slash_stripped(self, monkeypatch):
        from yeaboi import config

        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com/")
        assert config.get_gitlab_url() == "https://gitlab.example.com"

    def test_blank_url_falls_back_to_the_default(self, monkeypatch):
        from yeaboi import config

        monkeypatch.setenv("GITLAB_URL", "   ")
        assert config.get_gitlab_url() == "https://gitlab.com"

    def test_token_getter_reads_the_env_var(self, monkeypatch):
        from yeaboi import config

        monkeypatch.setenv("GITLAB_TOKEN", "glpat-abc")
        assert config.get_gitlab_token() == "glpat-abc"

    def test_token_getter_returns_none_when_blank(self, monkeypatch):
        from yeaboi import config

        monkeypatch.setenv("GITLAB_TOKEN", "")
        assert config.get_gitlab_token() is None


# ---------------------------------------------------------------------------
# Secret masking — a GitLab token must never reach a log file verbatim
# ---------------------------------------------------------------------------


class TestTokenMasking:
    def test_token_shape_is_redacted_without_any_env_var(self):
        from yeaboi.redaction import REDACTED, redact

        token = "glpat-" + "A1b2C3d4E5f6G7h8I9j0"
        out = redact(f"401 Unauthorized for PRIVATE-TOKEN {token}")
        assert token not in out
        assert REDACTED in out

    def test_runner_token_shape_is_redacted(self):
        from yeaboi.redaction import redact

        token = "glrt-" + "A1b2C3d4E5f6G7h8I9j0"
        assert token not in redact(f"job failed with {token}")

    def test_configured_token_value_is_redacted(self, monkeypatch):
        from yeaboi import redaction

        monkeypatch.setenv("GITLAB_TOKEN", "not-a-standard-shape-but-secret")
        out = redaction.redact("token=not-a-standard-shape-but-secret")
        assert "not-a-standard-shape-but-secret" not in out

    def test_gitlab_token_is_a_known_secret_key(self):
        from yeaboi.redaction import SECRET_ENV_KEYS

        assert "GITLAB_TOKEN" in SECRET_ENV_KEYS


# ---------------------------------------------------------------------------
# Registration + risk classification
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_all_four_tools_are_registered(self):
        names = {t.name for t in get_tools()}
        assert {
            "gitlab_read_repo",
            "gitlab_read_readme",
            "gitlab_list_issues",
            "gitlab_create_issue",
        } <= names

    def test_reads_are_read_risk_and_create_is_write(self):
        from yeaboi.tools.risk import TOOL_RISK, ToolRisk

        assert TOOL_RISK["gitlab_read_repo"] is ToolRisk.READ
        assert TOOL_RISK["gitlab_read_readme"] is ToolRisk.READ
        assert TOOL_RISK["gitlab_list_issues"] is ToolRisk.READ
        assert TOOL_RISK["gitlab_create_issue"] is ToolRisk.WRITE
