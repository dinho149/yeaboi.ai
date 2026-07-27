"""Tests for tools/local_git.py sandbox behaviour.

(The commit-parsing behaviour is covered by the standup engine suite; this
file holds the direct local_git unit tests, starting with the sandbox gate.)
"""

from yeaboi.tools.local_git import local_git_recent_commits


class TestSandboxGating:
    def test_denied_repo_path_returns_empty(self):
        """A repo path outside the whitelist degrades to [] (never raises)."""
        assert local_git_recent_commits("/denied-sandbox-dir/repo", days=1) == []

    def test_whitelisted_non_repo_still_empty(self, tmp_path):
        """Inside the whitelist but not a git repo — the pre-existing [] contract."""
        assert local_git_recent_commits(str(tmp_path), days=1) == []
