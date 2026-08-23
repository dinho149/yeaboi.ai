"""Unit tests for the single-repo tree helpers extracted for prior-art enrichment.

github_analysis_inventory / azdevops_analysis_inventory walk a whole owner or
project. Planning needs a tree for a handful of shortlisted repos and must
never pay for an estate scan to get one — hence these entry points.
"""

from __future__ import annotations

import pytest

from yeaboi.tools import azure_devops, github


class _Item:
    def __init__(self, path, type_="blob", is_folder=False):
        self.path = path
        self.type = type_
        self.is_folder = is_folder


class _Tree:
    def __init__(self, items, truncated=False):
        self.tree = items
        self.truncated = truncated


class _Repo:
    def __init__(self, tree=None, languages=None, empty=False, exc=None, description=""):
        self._tree = tree
        self._languages = languages or {}
        self.empty = empty
        self.default_branch = "main"
        self.full_name = "acme/api"
        self.description = description
        self._exc = exc

    def get_git_tree(self, sha=None, recursive=False):
        if self._exc:
            raise self._exc
        return self._tree

    def get_languages(self):
        if self._exc:
            raise self._exc
        return self._languages


class TestRepoTreePaths:
    def test_returns_blob_paths_only(self):
        repo = _Repo(_Tree([_Item("src/a.py"), _Item("src", "tree"), _Item("README.md")]))
        paths, error = github._repo_tree_paths(repo)
        assert paths == ["src/a.py", "README.md"]
        assert error == ""

    def test_truncation_is_reported(self):
        repo = _Repo(_Tree([_Item("a.py")], truncated=True))
        paths, error = github._repo_tree_paths(repo)
        assert paths == ["a.py"]
        assert "truncated" in error

    def test_empty_repo_short_circuits_without_an_api_call(self):
        repo = _Repo(exc=AssertionError("must not call the API for an empty repo"), empty=True)
        assert github._repo_tree_paths(repo) == ([], "")

    def test_failure_degrades_to_an_error_string(self):
        repo = _Repo(exc=RuntimeError("boom"))
        paths, error = github._repo_tree_paths(repo)
        assert paths == [] and "boom" in error


class TestRepoLanguages:
    def test_sorted_by_bytes_descending_and_capped(self):
        repo = _Repo(languages={"Python": 10, "Go": 100, "Rust": 50, "C": 1, "Zig": 2, "Nim": 3})
        assert github._repo_languages(repo) == ["Go", "Rust", "Python", "Nim", "Zig"]

    def test_failure_is_empty_not_an_exception(self):
        assert github._repo_languages(_Repo(exc=RuntimeError("no"))) == []

    def test_no_languages(self):
        assert github._repo_languages(_Repo(languages={})) == []


class TestGithubRepoTree:
    def test_resolves_a_url_then_walks(self, monkeypatch):
        repo = _Repo(_Tree([_Item("main.go")]))

        class _Client:
            def get_repo(self, slug):
                assert slug == "acme/api"
                return repo

        monkeypatch.setattr(github, "_get_github_client", lambda: _Client())
        assert github.github_repo_tree("https://github.com/acme/api") == (["main.go"], "")

    def test_lookup_failure_never_raises(self, monkeypatch):
        def _boom():
            raise RuntimeError("no token")

        monkeypatch.setattr(github, "_get_github_client", _boom)
        paths, error = github.github_repo_tree("acme/api")
        assert paths == [] and "lookup failed" in error


class TestAzdoTreePaths:
    def test_skips_folders_and_strips_the_leading_slash(self):
        class _Client:
            def get_items(self, **kw):
                return [_Item("/src/a.cs"), _Item("/src", is_folder=True), _Item("/README.md")]

        paths, error = azure_devops._azdo_tree_paths(_Client(), "Payments", "repo-id")
        assert paths == ["src/a.cs", "README.md"]
        assert error == ""

    def test_failure_degrades_to_an_error_string(self):
        class _Client:
            def get_items(self, **kw):
                raise RuntimeError("403")

        paths, error = azure_devops._azdo_tree_paths(_Client(), "Payments", "repo-id")
        assert paths == [] and "403" in error

    def test_repo_tree_lookup_failure_never_raises(self, monkeypatch):
        monkeypatch.setattr(azure_devops, "_parse_azdo_url", lambda url: (_ for _ in ()).throw(ValueError("bad url")))
        paths, error = azure_devops.azdevops_repo_tree("nonsense")
        assert paths == [] and "lookup failed" in error


class TestInventoryStillWorks:
    """The extraction must not have changed what the estate scan produces."""

    def test_github_inventory_row_carries_description_and_languages(self, monkeypatch):
        from datetime import datetime, timezone

        repo = _Repo(_Tree([_Item("app.py")]), languages={"Python": 5}, description=" Payments API ")
        repo.pushed_at = datetime.now(timezone.utc)
        repo.html_url = "https://github.com/acme/api"
        repo.archived = False

        class _Owner:
            def get_repos(self):
                return [repo]

        class _Client:
            def get_organization(self, name):
                return _Owner()

        monkeypatch.setattr(github, "_get_github_client", lambda: _Client())
        (row,) = github.github_analysis_inventory(["acme"], days=30)
        assert row["description"] == "Payments API"
        assert row["languages"] == ["Python"]
        assert row["paths"] == ["app.py"]
        assert row["active"] is True

    def test_inactive_repo_skips_the_languages_call(self, monkeypatch):
        from datetime import datetime, timedelta, timezone

        repo = _Repo(_Tree([]), description="stale")
        repo.pushed_at = datetime.now(timezone.utc) - timedelta(days=900)
        repo.html_url = ""
        repo.archived = False
        repo.get_languages = lambda: (_ for _ in ()).throw(AssertionError("must not call for inactive repos"))

        class _Owner:
            def get_repos(self):
                return [repo]

        class _Client:
            def get_organization(self, name):
                return _Owner()

        monkeypatch.setattr(github, "_get_github_client", lambda: _Client())
        (row,) = github.github_analysis_inventory(["acme"], days=30)
        assert row["active"] is False
        assert row["languages"] == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
