"""Coverage and actionable-output contracts for exhaustive Analysis mode."""

from __future__ import annotations

from yeaboi.analysis.code_health import analyse_changed_files, analyse_repository_health, prioritize_actions
from yeaboi.analysis.coverage import CoverageTracker
from yeaboi.analysis.doc_quality import aggregate_doc_quality, run_doc_quality
from yeaboi.team_profile import TeamProfileStore


def test_coverage_is_partial_on_any_failed_asset():
    tracker = CoverageTracker("code", 120)
    tracker.add("github", "acme", "one", "succeeded")
    tracker.add("github", "acme", "two", "failed", "rate limited")
    tracker.add("github", "acme", "old", "unchanged", eligible=False)
    report = tracker.as_dict()
    assert report["status"] == "partial"
    assert report["discovered"] == 3
    assert report["eligible"] == 2
    assert report["failed"] == 1
    assert report["unchanged"] == 1


def test_cached_assets_count_as_completed_without_network_attempts():
    tracker = CoverageTracker("docs", 120)
    tracker.add("confluence", "PSO", "page-1", "cached")
    report = tracker.as_dict()
    assert report["status"] == "complete"
    assert report["attempted"] == 0
    assert report["cached"] == report["completed"] == 1


def test_coverage_is_failed_when_every_eligible_asset_fails():
    tracker = CoverageTracker("code", 120)
    tracker.add("azdo", "Project/repo", "commit-a", "failed", "SDK wrapper mismatch")
    tracker.add("azdo", "Project/repo", "commit-b", "failed", "SDK wrapper mismatch")

    report = tracker.as_dict()

    assert report["status"] == "failed"
    assert report["has_data"] is False
    assert report["completion_pct"] == 0
    assert report["grouped_errors"] == [
        {
            "provider": "azdo",
            "status": "failed",
            "detail": "SDK wrapper mismatch",
            "count": 2,
            "containers": ["Project/repo"],
            "examples": ["commit-a", "commit-b"],
        }
    ]


def test_coverage_distinguishes_successful_empty_scope_from_failure():
    report = CoverageTracker("docs", 120).as_dict()

    assert report["status"] == "no_data"
    assert report["has_data"] is False
    assert report["completion_pct"] == 100


def test_repository_health_processes_every_active_repo_and_prioritizes_breadth():
    inventory = [
        {"provider": "github", "container": "acme", "name": "acme/a", "active": True, "paths": [], "url": "u1"},
        {"provider": "github", "container": "acme", "name": "acme/b", "active": True, "paths": [], "url": "u2"},
        {"provider": "github", "container": "acme", "name": "acme/c", "active": False, "paths": [], "url": "u3"},
    ]
    reports, findings = analyse_repository_health(inventory, set())
    assert len(reports) == 3
    assert sum(report["status"] == "succeeded" for report in reports) == 2
    actions = prioritize_actions(findings)
    readme = next(action for action in actions if action["title"] == "Add a repository README")
    assert readme["affected_scope"] == ["acme/a", "acme/b"]
    assert readme["breadth"] == 2
    assert readme["owner_role"] and readme["effort"] and readme["completion_check"]


def test_changed_file_analysis_is_attribution_scoped_and_reports_exclusions():
    reports, findings, coverage = analyse_changed_files(
        [
            {
                "provider": "github",
                "container": "acme",
                "repository": "acme/api",
                "path": "src/api.py",
                "status": "modified",
                "additions": 300,
                "deletions": 250,
                "attribution": "authored_commit",
                "confidence": "high",
                "change_id": "abc",
            },
            {
                "provider": "github",
                "container": "acme",
                "repository": "acme/api",
                "path": "assets/logo.png",
                "status": "modified",
                "attribution": "authored_pr",
                "confidence": "medium",
                "change_id": "pr:1",
            },
        ]
    )
    assert [r["path"] for r in reports] == ["src/api.py", "assets/logo.png"]
    assert reports[1]["analysis_status"] == "excluded"
    assert coverage["eligible"] == 1
    assert {f["category"] for f in findings} == {"change-size", "testing"}
    assert all("src/api.py" in f["affected_scope"][0] for f in findings)


def test_doc_signal_uses_usefulness_not_ai_authorship():
    signal = aggregate_doc_quality(
        [
            {
                "platform": "notion",
                "title": "Runbook",
                "text": "# Purpose\n\nOwner: SRE\n\n- Run the check.\n- Verify the result.",
            }
        ]
    )
    assert signal.pages_scanned == 1
    assert signal.avg_usefulness >= 80
    assert signal.owned_pages == 1
    assert signal.actionable_pages == 1
    assert signal.avg_ai_likelihood == 0
    assert signal.is_ai_estimate is False


def test_doc_run_keeps_every_page_in_assets(monkeypatch):
    pages = [
        {"platform": "notion", "key": str(i), "title": f"Page {i}", "text": "Short useful step. Run it."}
        for i in range(25)
    ]
    monkeypatch.setattr(
        "yeaboi.analysis.doc_quality.collect_doc_pages",
        lambda *args, **kwargs: (
            pages,
            ["notion"],
            [],
            {
                "component": "docs",
                "status": "complete",
                "window_days": 120,
                "discovered": 25,
                "eligible": 25,
                "attempted": 25,
                "succeeded": 25,
                "failed": 0,
                "unchanged": 0,
                "inaccessible": 0,
                "truncated": 0,
                "per_container": {},
                "assets": [],
            },
        ),
    )
    signal, blob = run_doc_quality("", "")
    assert signal.pages_scanned == 25
    assert len(blob["assets"]) == 25
    assert len(blob["samples"]) == 25


def test_normalized_analysis_run_persistence(tmp_path):
    db = tmp_path / "sessions.db"
    result = {
        "analysis_window_days": 120,
        "analysis_features": ["ai_footprint", "code_health"],
        "analysis_scope": {"github": ["acme"]},
        "coverage": {
            "status": "complete",
            "components": {
                "code": {
                    "assets": [
                        {
                            "provider": "github",
                            "container": "acme",
                            "asset": "acme/repo",
                            "status": "succeeded",
                            "detail": "",
                        }
                    ]
                }
            },
        },
        "code": {
            "examples": {
                "findings": [
                    {"id": "f1", "priority": "high", "title": "Add CI"},
                ]
            }
        },
        "docs": None,
    }
    with TeamProfileStore(db) as store:
        run_id = store.save_analysis_run(result)
        assert store._conn.execute("SELECT status FROM analysis_runs WHERE run_id = ?", (run_id,)).fetchone() == (
            "complete",
        )
        assert store._conn.execute("SELECT COUNT(*) FROM analysis_assets").fetchone()[0] == 1
        assert store._conn.execute("SELECT COUNT(*) FROM analysis_findings").fetchone()[0] == 1
        assert store._conn.execute(
            "SELECT features_json FROM analysis_runs WHERE run_id = ?", (run_id,)
        ).fetchone() == ('["ai_footprint", "code_health"]',)
