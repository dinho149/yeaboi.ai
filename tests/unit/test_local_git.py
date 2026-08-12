"""Tests for tools/local_git.py sandbox behaviour.

(The commit-parsing behaviour is covered by the standup engine suite; this
file holds the direct local_git unit tests, starting with the sandbox gate.)
"""

import pytest

from yeaboi.tools.local_git import _known_git_host, _origin_commit_url_base, local_git_recent_commits


class TestSandboxGating:
    def test_denied_repo_path_returns_empty(self):
        """A repo path outside the whitelist degrades to [] (never raises)."""
        assert local_git_recent_commits("/denied-sandbox-dir/repo", days=1) == []

    def test_whitelisted_non_repo_still_empty(self, tmp_path):
        """Inside the whitelist but not a git repo — the pre-existing [] contract."""
        assert local_git_recent_commits(str(tmp_path), days=1) == []


class TestKnownGitHost:
    """Whole-host matching. A substring test here is a link-hijack primitive."""

    @pytest.mark.parametrize(
        "host",
        [
            "github.com",
            "dev.azure.com",
            "visualstudio.com",
            "myorg.visualstudio.com",  # the legacy AzDO shape — the subdomain arm exists for this
            "GitHub.com",  # hosts are case-insensitive
            "github.com:443",
        ],
    )
    def test_accepts_known_hosts(self, host):
        assert _known_git_host(host) is True

    @pytest.mark.parametrize(
        "host",
        [
            "evil-github.com",  # suffix-of-a-word, not a subdomain
            "github.com.attacker.tld",  # known host as a *prefix* label
            "notgithub.com",
            "evil.tld",
            "",
        ],
    )
    def test_rejects_lookalike_hosts(self, host):
        assert _known_git_host(host) is False


class TestOriginCommitUrlBase:
    """End-to-end over `remote.origin.url`, which anyone with the clone can set."""

    @staticmethod
    def _with_remote(monkeypatch, url: str):
        """Stand in for `git config --get remote.origin.url` returning `url`."""

        class _Proc:
            returncode = 0
            stdout = url

        monkeypatch.setattr("yeaboi.tools.local_git.subprocess.run", lambda *a, **k: _Proc())

    @pytest.mark.parametrize(
        ("remote", "expected"),
        [
            ("git@github.com:owner/repo.git", "https://github.com/owner/repo"),
            ("https://github.com/owner/repo.git", "https://github.com/owner/repo"),
            ("https://dev.azure.com/org/proj/_git/repo", "https://dev.azure.com/org/proj/_git/repo"),
            ("https://myorg.visualstudio.com/proj/_git/repo", "https://myorg.visualstudio.com/proj/_git/repo"),
            # userinfo is stripped, and the *real* host is what gets checked
            ("https://user:pw@github.com/owner/repo", "https://github.com/owner/repo"),
        ],
    )
    def test_known_remotes_normalise(self, monkeypatch, remote, expected):
        self._with_remote(monkeypatch, remote)
        assert _origin_commit_url_base("/repo") == expected

    @pytest.mark.parametrize(
        "remote",
        [
            "git@evil-github.com:owner/repo.git",
            "git@github.com.attacker.tld:owner/repo.git",
            # The known host is in the PATH, not the authority. A check over the
            # joined host-and-path string accepted this and returned it verbatim.
            "https://evil.tld/x/github.com/y",
            # Userinfo smuggling: the known host is the *username*.
            "https://github.com@evil.tld/owner/repo",
            "https://gitlab.com/owner/repo",
        ],
    )
    def test_untrusted_remotes_yield_no_link(self, monkeypatch, remote):
        self._with_remote(monkeypatch, remote)
        assert _origin_commit_url_base("/repo") == ""
