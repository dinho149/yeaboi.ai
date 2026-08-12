"""Unit tests for analysis.repo_inventory — the persisted repo estate.

The key format is the load-bearing part: a repository the user rejected as
prior art is suppressed by key, so an unstable key silently re-offers it.
"""

from __future__ import annotations

import logging

from yeaboi.analysis import repo_inventory


class TestRepoKey:
    def test_github_name_already_qualified(self):
        # GitHub rows carry owner/repo in `name`; the container must not double up.
        assert repo_inventory.repo_key("github", "acme", "acme/platform-auth") == "github:acme/platform-auth"

    def test_azdo_name_is_bare(self):
        assert repo_inventory.repo_key("azdo", "Payments", "checkout-api") == "azdo:payments/checkout-api"

    def test_case_and_whitespace_normalised(self):
        assert repo_inventory.repo_key("GitHub", " Acme ", " Acme/Platform-Auth ") == "github:acme/platform-auth"

    def test_same_repo_from_both_row_shapes_agrees(self):
        qualified = repo_inventory.repo_key("github", "acme", "acme/api")
        joined = repo_inventory.repo_key("github", "acme", "api")
        assert qualified == joined == "github:acme/api"

    def test_missing_container_does_not_produce_a_leading_slash(self):
        assert repo_inventory.repo_key("azdo", "", "checkout") == "azdo:checkout"

    def test_empty_inputs_are_survivable(self):
        assert repo_inventory.repo_key("", "", "") == ":"


class TestNormalise:
    def _row(self, **over):
        row = {
            "provider": "github",
            "container": "acme",
            "name": "acme/api",
            "url": "https://github.com/acme/api",
            "default_branch": "main",
            "updated_at": "2026-08-01T00:00:00+00:00",
            "archived": False,
            "active": True,
            "skip_reason": "",
            "description": "  Payments API  ",
            "languages": ["Python", "TypeScript"],
            "paths": ["src/a.py", "src/b.py"],
        }
        row.update(over)
        return row

    def test_happy_path_shape(self):
        (out,) = repo_inventory.normalise([self._row()])
        assert out["key"] == "github:acme/api"
        assert out["description"] == "Payments API"
        assert out["languages"] == ["Python", "TypeScript"]

    def test_paths_are_never_persisted(self):
        # A recursive tree is thousands of strings per repo; the consumer
        # fetches trees only for the shortlist.
        (out,) = repo_inventory.normalise([self._row()])
        assert "paths" not in out

    def test_discovery_error_sentinels_are_dropped(self):
        rows = [self._row(), {"provider": "github", "container": "acme", "name": "acme", "discovery_error": True}]
        assert [r["key"] for r in repo_inventory.normalise(rows)] == ["github:acme/api"]

    def test_duplicates_collapse_by_key(self):
        rows = [self._row(), self._row(name="ACME/API")]
        assert len(repo_inventory.normalise(rows)) == 1

    def test_rows_without_provider_or_name_are_skipped(self):
        rows = [self._row(provider=""), self._row(name=""), self._row()]
        assert len(repo_inventory.normalise(rows)) == 1

    def test_malformed_rows_do_not_kill_the_batch(self):
        assert len(repo_inventory.normalise(["not a dict", None, self._row()])) == 1

    def test_sorted_most_recently_pushed_first(self):
        rows = [
            self._row(name="acme/old", updated_at="2020-01-01T00:00:00+00:00"),
            self._row(name="acme/new", updated_at="2026-08-01T00:00:00+00:00"),
            self._row(name="acme/mid", updated_at="2024-01-01T00:00:00+00:00"),
        ]
        assert [r["name"] for r in repo_inventory.normalise(rows)] == ["acme/new", "acme/mid", "acme/old"]

    def test_inactive_repos_are_kept(self):
        # A mature, finished service is ideal prior art precisely because
        # nobody has pushed to it lately.
        rows = [self._row(active=False, skip_reason="no recorded push activity")]
        (out,) = repo_inventory.normalise(rows)
        assert out["active"] is False
        assert out["skip_reason"] == "no recorded push activity"

    def test_truncates_at_the_cap_keeping_newest(self, caplog):
        rows = [
            self._row(name=f"acme/r{i:04d}", updated_at=f"2026-01-{(i % 28) + 1:02d}T00:00:00+00:00")
            for i in range(repo_inventory.MAX_INVENTORY_ROWS + 25)
        ]
        with caplog.at_level(logging.INFO, logger="yeaboi.analysis.repo_inventory"):
            out = repo_inventory.normalise(rows)
        assert len(out) == repo_inventory.MAX_INVENTORY_ROWS
        # Newest survive the cut.
        assert out[0]["updated_at"] >= out[-1]["updated_at"]
        # Truncation is reported, never silent.
        assert any("keeping" in rec.getMessage() for rec in caplog.records)

    def test_empty_and_none_inputs(self):
        assert repo_inventory.normalise([]) == []
        assert repo_inventory.normalise(None) == []
