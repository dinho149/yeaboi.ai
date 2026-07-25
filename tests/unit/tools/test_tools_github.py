"""Tests for GitHub read-only tools.

All GitHub API calls are mocked via unittest.mock.patch so no real network
requests are made. Tests cover happy paths, error cases, and edge cases for
each tool and the _parse_repo helper.
"""

from unittest.mock import MagicMock, patch

import github as _gh_import_check  # noqa: F401 — ensures PyGithub is installed

from yeaboi.tools import detect_platform, get_tools
from yeaboi.tools.github import (
    _parse_repo,
    github_changed_files,
    github_list_issues,
    github_read_file,
    github_read_readme,
    github_read_repo,
)

# ---------------------------------------------------------------------------
# _parse_repo
# ---------------------------------------------------------------------------


class TestParseRepo:
    def test_https_url(self):
        assert _parse_repo("https://github.com/owner/repo") == "owner/repo"

    def test_http_url(self):
        assert _parse_repo("http://github.com/owner/repo") == "owner/repo"

    def test_slug_passthrough(self):
        assert _parse_repo("owner/repo") == "owner/repo"

    def test_trailing_slash(self):
        assert _parse_repo("https://github.com/owner/repo/") == "owner/repo"

    def test_git_suffix(self):
        assert _parse_repo("https://github.com/owner/repo.git") == "owner/repo"

    def test_deep_url_truncated_to_owner_repo(self):
        # Extra path segments (e.g. /tree/main) should be stripped
        assert _parse_repo("https://github.com/owner/repo/tree/main") == "owner/repo"

    def test_whitespace_stripped(self):
        assert _parse_repo("  https://github.com/owner/repo  ") == "owner/repo"


class TestChangedFiles:
    @patch("yeaboi.tools.github._get_github_client")
    def test_commit_and_pr_attribution_are_separate(self, mock_client):
        repo = mock_client.return_value.get_repo.return_value
        commit_file = MagicMock(filename="src/a.py", status="modified", additions=4, deletions=1, patch="+x")
        pr_file = MagicMock(filename="src/b.py", status="added", additions=8, deletions=0, patch="+y")
        repo.get_commit.return_value.files = [commit_file]
        repo.get_pull.return_value.get_files.return_value = [pr_file]

        files = github_changed_files(
            "owner/repo",
            [
                {"kind": "commit", "commit_id": "abc", "author": "Alice"},
                {"kind": "pr", "pr_id": 7, "author": "Alice"},
            ],
        )

        assert [(f["path"], f["attribution"], f["confidence"]) for f in files] == [
            ("src/a.py", "authored_commit", "high"),
            ("src/b.py", "authored_pr", "medium"),
        ]


# ---------------------------------------------------------------------------
# Helpers — build mock Github objects
# ---------------------------------------------------------------------------


def _make_tree_item(path: str, item_type: str = "blob") -> MagicMock:
    item = MagicMock()
    item.path = path
    item.type = item_type
    return item


def _make_content_file(path: str, content: str, size: int | None = None) -> MagicMock:
    cf = MagicMock()
    cf.path = path
    cf.size = size or len(content)
    cf.decoded_content = content.encode("utf-8")
    cf.pull_request = None
    return cf


def _make_issue(number: int, title: str, labels: list[str] = (), body: str = "", is_pr: bool = False) -> MagicMock:
    issue = MagicMock()
    issue.number = number
    issue.title = title
    issue.body = body
    issue.pull_request = MagicMock() if is_pr else None
    # MagicMock(name=x) sets the mock's display name, not the .name attribute.
    # Create each label mock and set .name explicitly as a plain string.
    label_mocks = []
    for label_name in labels:
        lm = MagicMock()
        lm.name = label_name
        label_mocks.append(lm)
    issue.labels = label_mocks
    return issue


# ---------------------------------------------------------------------------
# github_read_repo
# ---------------------------------------------------------------------------


class TestGithubReadRepo:
    def _make_repo(self, tree_items: list, languages: dict | None = None) -> MagicMock:
        repo = MagicMock()
        repo.default_branch = "main"
        repo.stargazers_count = 42
        repo.forks_count = 7
        repo.open_issues_count = 3
        repo.description = "A test repo"

        tree = MagicMock()
        tree.tree = tree_items
        repo.get_git_tree.return_value = tree
        repo.get_languages.return_value = languages or {"Python": 8000, "Shell": 2000}
        return repo

    @patch("yeaboi.tools.github.github.Github")
    def test_normal_tree_returned(self, mock_github):
        items = [
            _make_tree_item("src", "tree"),
            _make_tree_item("src/main.py"),
            _make_tree_item("pyproject.toml"),
            _make_tree_item("README.md"),
        ]
        repo = self._make_repo(items)
        mock_github.return_value.get_repo.return_value = repo

        result = github_read_repo.invoke({"repo_url": "owner/repo"})

        assert "owner/repo" in result
        assert "pyproject.toml" in result
        assert "README.md" in result
        assert "Python" in result

    @patch("yeaboi.tools.github.github.Github")
    def test_empty_repo(self, mock_github):
        repo = self._make_repo([])
        mock_github.return_value.get_repo.return_value = repo

        result = github_read_repo.invoke({"repo_url": "owner/repo"})

        assert "owner/repo" in result
        # No key files found — section absent
        assert "Key files" not in result

    @patch("yeaboi.tools.github.github.Github")
    def test_rate_limit_error(self, mock_github):
        import github as gh_module

        mock_github.return_value.get_repo.side_effect = gh_module.RateLimitExceededException(
            403, {"message": "rate limit"}, {}
        )

        result = github_read_repo.invoke({"repo_url": "owner/repo"})

        assert "rate limit" in result.lower()

    @patch("yeaboi.tools.github.github.Github")
    def test_github_exception(self, mock_github):
        import github as gh_module

        mock_github.return_value.get_repo.side_effect = gh_module.GithubException(404, {"message": "Not Found"}, {})

        result = github_read_repo.invoke({"repo_url": "owner/repo"})

        assert "Error" in result
        assert "Not Found" in result

    @patch("yeaboi.tools.github.github.Github")
    def test_generic_exception(self, mock_github):
        mock_github.return_value.get_repo.side_effect = RuntimeError("network error")

        result = github_read_repo.invoke({"repo_url": "owner/repo"})

        assert "Error" in result


# ---------------------------------------------------------------------------
# github_read_file
# ---------------------------------------------------------------------------


class TestGithubReadFile:
    @patch("yeaboi.tools.github.github.Github")
    def test_file_found_and_decoded(self, mock_github):
        content = "name = 'my-project'\nversion = '1.0'\n"
        cf = _make_content_file("pyproject.toml", content)
        mock_github.return_value.get_repo.return_value.get_contents.return_value = cf

        result = github_read_file.invoke({"repo_url": "owner/repo", "file_path": "pyproject.toml"})

        assert "pyproject.toml" in result
        assert "name = 'my-project'" in result
        assert "[Truncated" not in result

    @patch("yeaboi.tools.github.github.Github")
    def test_file_not_found(self, mock_github):
        import github as gh_module

        mock_github.return_value.get_repo.return_value.get_contents.side_effect = gh_module.GithubException(
            404, {"message": "Not Found"}, {}
        )

        result = github_read_file.invoke({"repo_url": "owner/repo", "file_path": "missing.py"})

        assert "Error" in result
        assert "Not Found" in result

    @patch("yeaboi.tools.github.github.Github")
    def test_truncation_at_8000_chars(self, mock_github):
        long_content = "x" * 10_000
        cf = _make_content_file("big.py", long_content)
        mock_github.return_value.get_repo.return_value.get_contents.return_value = cf

        result = github_read_file.invoke({"repo_url": "owner/repo", "file_path": "big.py"})

        assert "[Truncated at 8000 characters]" in result
        # Content before truncation marker should be 8000 x's
        assert "x" * 8000 in result
        assert "x" * 8001 not in result

    @patch("yeaboi.tools.github.github.Github")
    def test_directory_path(self, mock_github):
        # get_contents on a dir returns a list
        cf1 = MagicMock()
        cf1.path = "src/main.py"
        cf2 = MagicMock()
        cf2.path = "src/utils.py"
        mock_github.return_value.get_repo.return_value.get_contents.return_value = [cf1, cf2]

        result = github_read_file.invoke({"repo_url": "owner/repo", "file_path": "src"})

        assert "directory" in result.lower()
        assert "src/main.py" in result

    @patch("yeaboi.tools.github.github.Github")
    def test_rate_limit_error(self, mock_github):
        import github as gh_module

        mock_github.return_value.get_repo.return_value.get_contents.side_effect = gh_module.RateLimitExceededException(
            403, {"message": "rate limit"}, {}
        )

        result = github_read_file.invoke({"repo_url": "owner/repo", "file_path": "any.py"})

        assert "rate limit" in result.lower()


# ---------------------------------------------------------------------------
# github_list_issues
# ---------------------------------------------------------------------------


class TestGithubListIssues:
    @patch("yeaboi.tools.github.github.Github")
    def test_issues_returned(self, mock_github):
        issues = [
            _make_issue(1, "Fix login bug", labels=["bug"], body="Users can't log in when using SSO."),
            _make_issue(2, "Add dark mode", labels=["enhancement"], body="Support dark colour scheme."),
            _make_issue(3, "PR: refactor auth", is_pr=True),
        ]
        mock_github.return_value.get_repo.return_value.get_issues.return_value = issues

        result = github_list_issues.invoke({"repo_url": "owner/repo"})

        assert "#1" in result
        assert "Fix login bug" in result
        assert "[bug]" in result
        assert "#3" in result
        assert "[PR]" in result

    @patch("yeaboi.tools.github.github.Github")
    def test_empty_issues(self, mock_github):
        mock_github.return_value.get_repo.return_value.get_issues.return_value = []

        result = github_list_issues.invoke({"repo_url": "owner/repo"})

        assert "No open issues found" in result

    @patch("yeaboi.tools.github.github.Github")
    def test_max_issues_respected(self, mock_github):
        issues = [_make_issue(i, f"Issue {i}") for i in range(1, 25)]
        mock_github.return_value.get_repo.return_value.get_issues.return_value = issues

        result = github_list_issues.invoke({"repo_url": "owner/repo", "max_issues": 5})

        assert "#5" in result
        assert "#6" not in result

    @patch("yeaboi.tools.github.github.Github")
    def test_rate_limit_error(self, mock_github):
        import github as gh_module

        mock_github.return_value.get_repo.return_value.get_issues.side_effect = gh_module.RateLimitExceededException(
            403, {"message": "rate limit"}, {}
        )

        result = github_list_issues.invoke({"repo_url": "owner/repo"})

        assert "rate limit" in result.lower()

    @patch("yeaboi.tools.github.github.Github")
    def test_body_preview_truncated(self, mock_github):
        long_body = "A" * 300
        issues = [_make_issue(1, "Big issue", body=long_body)]
        mock_github.return_value.get_repo.return_value.get_issues.return_value = issues

        result = github_list_issues.invoke({"repo_url": "owner/repo"})

        # Body preview is 200 chars + "..." — not the full 300
        assert "..." in result
        assert "A" * 200 in result
        assert "A" * 201 not in result


# ---------------------------------------------------------------------------
# github_read_readme
# ---------------------------------------------------------------------------


class TestGithubReadReadme:
    @patch("yeaboi.tools.github.github.Github")
    def test_readme_only(self, mock_github):
        import github as gh_module

        readme_cf = _make_content_file("README.md", "# My Project\n\nThis is a test project.")
        repo = MagicMock()
        repo.get_readme.return_value = readme_cf
        repo.get_contents.side_effect = gh_module.GithubException(404, {"message": "Not Found"}, {})
        mock_github.return_value.get_repo.return_value = repo

        result = github_read_readme.invoke({"repo_url": "owner/repo"})

        assert "README" in result
        assert "My Project" in result
        assert "CONTRIBUTING" not in result

    @patch("yeaboi.tools.github.github.Github")
    def test_readme_and_contributing(self, mock_github):
        readme_cf = _make_content_file("README.md", "# Project\n\nDocs here.")
        contrib_cf = _make_content_file("CONTRIBUTING.md", "## How to contribute\n\nOpen a PR.")
        repo = MagicMock()
        repo.get_readme.return_value = readme_cf
        repo.get_contents.return_value = contrib_cf
        mock_github.return_value.get_repo.return_value = repo

        result = github_read_readme.invoke({"repo_url": "owner/repo"})

        assert "README" in result
        assert "Project" in result
        assert "CONTRIBUTING.md" in result
        assert "How to contribute" in result

    @patch("yeaboi.tools.github.github.Github")
    def test_missing_readme(self, mock_github):
        import github as gh_module

        repo = MagicMock()
        repo.get_readme.side_effect = gh_module.GithubException(404, {"message": "Not Found"}, {})
        repo.get_contents.side_effect = gh_module.GithubException(404, {"message": "Not Found"}, {})
        mock_github.return_value.get_repo.return_value = repo

        result = github_read_readme.invoke({"repo_url": "owner/repo"})

        assert "No README found" in result

    @patch("yeaboi.tools.github.github.Github")
    def test_readme_truncated(self, mock_github):
        import github as gh_module

        long_readme = "# Title\n\n" + "Content line.\n" * 700  # > 8000 chars
        readme_cf = _make_content_file("README.md", long_readme)
        repo = MagicMock()
        repo.get_readme.return_value = readme_cf
        repo.get_contents.side_effect = gh_module.GithubException(404, {"message": "Not Found"}, {})
        mock_github.return_value.get_repo.return_value = repo

        result = github_read_readme.invoke({"repo_url": "owner/repo"})

        assert "[Truncated at 8000 characters]" in result

    @patch("yeaboi.tools.github.github.Github")
    def test_rate_limit_error(self, mock_github):
        import github as gh_module

        mock_github.return_value.get_repo.side_effect = gh_module.RateLimitExceededException(
            403, {"message": "rate limit"}, {}
        )

        result = github_read_readme.invoke({"repo_url": "owner/repo"})

        assert "rate limit" in result.lower()


# ---------------------------------------------------------------------------
# get_tools()
# ---------------------------------------------------------------------------


class TestGetTools:
    def test_returns_thirty_tools(self):
        tools = get_tools()
        assert len(tools) == 37

    def test_all_are_base_tools(self):
        from langchain_core.tools import BaseTool

        tools = get_tools()
        for t in tools:
            assert isinstance(t, BaseTool), f"{t} is not a BaseTool"

    def test_github_tools_present(self):
        tools = get_tools()
        names = {t.name for t in tools}
        github_names = {"github_read_repo", "github_read_file", "github_list_issues", "github_read_readme"}
        assert github_names.issubset(names)


# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------


class TestDetectPlatform:
    def test_github_url(self):
        assert detect_platform("https://github.com/owner/repo") == "GitHub"

    def test_azdo_dev_azure_com(self):
        assert detect_platform("https://dev.azure.com/org/proj/_git/repo") == "Azure DevOps"

    def test_azdo_visualstudio_com(self):
        assert detect_platform("https://myorg.visualstudio.com/proj/_git/repo") == "Azure DevOps"

    def test_gitlab_url(self):
        assert detect_platform("https://gitlab.com/owner/repo") == "GitLab"

    def test_bitbucket_url(self):
        assert detect_platform("https://bitbucket.org/owner/repo") == "Bitbucket"

    def test_unknown_url_returns_none(self):
        assert detect_platform("https://example.com/owner/repo") is None

    def test_empty_string_returns_none(self):
        assert detect_platform("") is None

    def test_whitespace_stripped(self):
        assert detect_platform("  https://github.com/owner/repo  ") == "GitHub"


# ---------------------------------------------------------------------------
# github_list_issues — rate limit message + max_issues truncation note
# ---------------------------------------------------------------------------


class TestGithubListIssuesRateLimitAndPagination:
    @patch("yeaboi.tools.github.github.Github")
    def test_rate_limit_message_includes_req_hr(self, mock_github):
        import github as gh_module

        mock_github.return_value.get_repo.return_value.get_issues.side_effect = gh_module.RateLimitExceededException(
            403, {"message": "rate limit"}, {}
        )

        result = github_list_issues.invoke({"repo_url": "owner/repo"})

        assert "5 000 req/hr" in result

    @patch("yeaboi.tools.github.github.Github")
    def test_max_issues_truncation_note(self, mock_github):
        # Return exactly max_issues issues so the cap is hit
        issues = [_make_issue(i, f"Issue {i}") for i in range(1, 6)]
        mock_github.return_value.get_repo.return_value.get_issues.return_value = issues

        result = github_list_issues.invoke({"repo_url": "owner/repo", "max_issues": 5})

        assert "increase max_issues to see more" in result

    @patch("yeaboi.tools.github.github.Github")
    def test_no_truncation_note_when_under_cap(self, mock_github):
        # Return fewer issues than max_issues — no note expected
        issues = [_make_issue(i, f"Issue {i}") for i in range(1, 4)]
        mock_github.return_value.get_repo.return_value.get_issues.return_value = issues

        result = github_list_issues.invoke({"repo_url": "owner/repo", "max_issues": 10})

        assert "increase max_issues" not in result


# ---------------------------------------------------------------------------
# Activity-scan caps (analysis cold-run bounds)
# ---------------------------------------------------------------------------


class TestActivityScanCaps:
    @patch("yeaboi.tools.github._get_github_client")
    def test_commit_iteration_capped(self, mock_client):
        from datetime import UTC, datetime

        from yeaboi.tools.github import _MAX_REPO_COMMITS, github_recent_commits

        def _commit(index):
            c = MagicMock()
            c.sha = f"sha{index:05d}"
            c.html_url = ""
            c.commit.message = f"change {index}"
            c.commit.author.name = "Alice"
            c.commit.author.email = "a@example.com"
            c.commit.author.date = datetime(2026, 1, 1, tzinfo=UTC)
            return c

        repo = mock_client.return_value.get_repo.return_value
        repo.get_commits.return_value = [_commit(index) for index in range(_MAX_REPO_COMMITS + 40)]

        items = github_recent_commits("owner/repo", days=120, include_changed_files=False)

        assert len(items) == _MAX_REPO_COMMITS

    @patch("yeaboi.tools.github._get_github_client")
    def test_pr_listing_sliced_even_without_metadata_cache(self, mock_client):
        from yeaboi.tools.github import _MAX_REPO_PRS, github_recent_prs

        def _pr(number):
            pr = MagicMock()
            pr.number = number
            pr.title = f"PR {number}"
            pr.body = ""
            pr.merged = False
            pr.state = "open"
            pr.updated_at = None
            pr.html_url = ""
            pr.user.login = "alice"
            pr.get_reviews.return_value = []
            pr.get_issue_comments.return_value = []
            pr.get_commits.return_value = []
            return pr

        repo = mock_client.return_value.get_repo.return_value
        repo.get_pulls.return_value = [_pr(number) for number in range(_MAX_REPO_PRS + 30)]

        items = github_recent_prs("owner/repo", days=120, include_changed_files=False)

        assert sum(1 for item in items if item.get("kind") == "pr") == _MAX_REPO_PRS

    @patch("yeaboi.tools.github._get_github_client")
    def test_pr_items_carry_source_branch(self, mock_client):
        from yeaboi.tools.github import github_recent_prs

        pr = MagicMock()
        pr.number = 7
        pr.title = "Fix login"
        pr.body = ""
        pr.merged = False
        pr.state = "open"
        pr.updated_at = None
        pr.html_url = ""
        pr.user.login = "alice"
        pr.head.ref = "codex/fix-login"
        pr.get_reviews.return_value = []
        pr.get_issue_comments.return_value = []
        pr.get_commits.return_value = []
        mock_client.return_value.get_repo.return_value.get_pulls.return_value = [pr]

        items = github_recent_prs("owner/repo", days=120, include_changed_files=False)

        pr_items = [item for item in items if item.get("kind") == "pr"]
        assert pr_items and pr_items[0]["branch"] == "codex/fix-login"


class TestGithubAnalysisInventory:
    @staticmethod
    def _repo(full_name, *, pushed_at, archived=False):
        repo = MagicMock()
        repo.full_name = full_name
        repo.pushed_at = pushed_at
        repo.updated_at = pushed_at
        repo.archived = archived
        repo.html_url = f"https://github.com/{full_name}"
        repo.default_branch = "main"
        repo.empty = False
        return repo

    @patch("yeaboi.tools.github._get_github_client")
    def test_relevance_flags(self, mock_client):
        from datetime import UTC, datetime, timedelta

        from yeaboi.tools.github import github_analysis_inventory

        now = datetime.now(UTC)
        owner = mock_client.return_value.get_organization.return_value
        owner.get_repos.return_value = [
            self._repo("acme/live", pushed_at=now - timedelta(days=3)),
            self._repo("acme/stale", pushed_at=now - timedelta(days=400)),
            self._repo("acme/dead", pushed_at=now - timedelta(days=3), archived=True),
            self._repo("acme/unknown", pushed_at=None),
        ]

        rows = {r["name"]: r for r in github_analysis_inventory(["acme"], days=120, include_trees=False)}

        assert rows["acme/live"]["active"] is True
        assert rows["acme/stale"]["active"] is False and rows["acme/stale"]["skip_reason"] == ""
        assert rows["acme/dead"]["active"] is False
        assert rows["acme/dead"]["skip_reason"] == "archived repository"
        assert rows["acme/unknown"]["active"] is False
        assert rows["acme/unknown"]["skip_reason"] == "no recorded push activity"
