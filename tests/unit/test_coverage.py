"""Tests for the shared coverage accounting (analysis/coverage.py).

Covers the error-grouping normalization: repeated exceptions that differ only
by URL/sha/id must collapse into one grouped entry (and thus one rendered
note), while genuinely different failures stay separate.
"""

from __future__ import annotations

from yeaboi.analysis.coverage import CoverageTracker, _normalize_detail, coverage_notes


def _dns_error(repo: str, sha: str) -> str:
    return (
        "Error occurred in request., ConnectionError: HTTPSConnectionPool(host='dev.azure.com', port=443): "
        f"Max retries exceeded with url: /org/Proj/_apis/git/repositories/{repo}/commits/{sha}/changes?top=2000&skip=0 "
        "(Caused by NameResolutionError(\"Failed to resolve 'dev.azure.com'\"))"
    )


class TestNormalizeDetail:
    def test_strips_urls_api_paths_shas_and_long_numbers(self):
        detail = _dns_error("RepoA", "493e8533e651c449c1c4a0ebf56407852f7b147f")
        normalized = _normalize_detail(detail)
        assert "<api-path>" in normalized
        assert "493e8533" not in normalized
        assert "RepoA" not in normalized
        assert "dev.azure.com" in normalized  # the host is the signal — keep it

    def test_truncates_at_cap(self):
        normalized = _normalize_detail("x" * 500)
        assert len(normalized) == 200 and normalized.endswith("…")

    def test_empty_stays_empty(self):
        assert _normalize_detail("") == ""


class TestGroupedErrors:
    def test_same_error_shape_groups_across_assets(self):
        tracker = CoverageTracker("code", 120)
        tracker.add("azdo", "Proj", "RepoA", "failed", _dns_error("RepoA", "a" * 40))
        tracker.add("azdo", "Proj", "RepoB", "failed", _dns_error("RepoB", "b" * 40))
        tracker.add("azdo", "Other", "RepoC", "failed", _dns_error("RepoC", "c" * 40))
        grouped = tracker.as_dict()["grouped_errors"]
        assert len(grouped) == 1
        assert grouped[0]["count"] == 3
        assert grouped[0]["containers"] == ["Other", "Proj"]
        assert grouped[0]["examples"] == ["RepoA", "RepoB", "RepoC"]

    def test_different_error_shapes_stay_separate(self):
        tracker = CoverageTracker("code", 120)
        tracker.add("azdo", "Proj", "RepoA", "failed", _dns_error("RepoA", "a" * 40))
        tracker.add("azdo", "Proj", "RepoB", "failed", "TF401019: The Git repository does not exist")
        grouped = tracker.as_dict()["grouped_errors"]
        assert len(grouped) == 2

    def test_empty_detail_falls_back_to_status(self):
        tracker = CoverageTracker("code", 120)
        tracker.add("azdo", "Proj", "RepoA", "inaccessible", "")
        grouped = tracker.as_dict()["grouped_errors"]
        assert grouped[0]["detail"] == "inaccessible"

    def test_notes_render_one_line_per_group_with_counts(self):
        tracker = CoverageTracker("code", 120)
        for index in range(24):
            tracker.add("azdo", f"Proj{index % 3}", f"Repo{index}", "failed", _dns_error(f"Repo{index}", "d" * 40))
        notes = coverage_notes(tracker.as_dict())
        assert len(notes) == 1
        assert "24 item(s)" in notes[0]
        assert "across 3 container(s)" in notes[0]
        assert "dev.azure.com" in notes[0]
