"""Exact-value tests for the deterministic code-health analysis (analysis/code_health.py).

These deliberately pin whole dicts, exact floats, and exact ordering rather than
loose membership checks: the suite doubles as the assertion source for the
Python↔Go byte-parity corpus, so every asserted literal is a parity fixture.
"""

from __future__ import annotations

import copy

from yeaboi.analysis.code_health import (
    _eligibility,
    _file_scope,
    _is_test_path,
    analyse_changed_files,
    analyse_repository_health,
    changed_file_summary,
    prioritize_actions,
    repository_health_summary,
)


def _change(**overrides) -> dict:
    base = {
        "provider": "github",
        "container": "acme",
        "repository": "acme/api",
        "path": "src/api.py",
        "status": "modified",
        "additions": 10,
        "deletions": 2,
        "attribution": "authored_commit",
        "confidence": "high",
        "change_id": "c1",
        "url": "https://example.test/c1",
    }
    base.update(overrides)
    return base


# One path per repository-health check, so a repo carrying all of them is clean.
_HEALTHY_PATHS = [
    "README.md",
    "docs/adr/0001-record-decisions.md",
    "tests/test_api.py",
    ".github/workflows/ci.yml",
    "pyproject.toml",
    ".github/CODEOWNERS",
    ".github/dependabot.yml",
]


class TestIsTestPath:
    def test_test_directory_parts_match(self):
        for path in (
            "tests/test_api.py",
            "src/tests/util.py",
            "__tests__/App.tsx",
            "spec/models.rb",
            "specs/models.rb",
            "test/helper.js",
        ):
            assert _is_test_path(path), path

    def test_test_filenames_match(self):
        for path in ("test_helpers.py", "src/test_edge.py", "app.test.ts", "app.spec.js"):
            assert _is_test_path(path), path

    def test_matching_is_case_insensitive(self):
        for path in ("Tests/Foo.cs", "TEST_helpers.py", "App.Test.TS", "App.Spec.JS"):
            assert _is_test_path(path), path

    def test_non_test_paths_do_not_match(self):
        for path in (
            "src/api.py",
            "src/testing/util.py",  # "testing" is not a whole test part
            "contest/entry.py",
            "attest.py",
            "conftest.py",
            "protest/spec.txt",  # no ".spec." in the name, "protest" is not "spec"
        ):
            assert not _is_test_path(path), path

    def test_suffix_style_test_names_do_not_match(self):
        # practices.py imports this helper: the Go-style "_test" suffix is
        # deliberately outside its vocabulary, only the "test_" prefix counts.
        assert not _is_test_path("src/api_test.py")
        assert not _is_test_path("pkg/store_test.go")

    def test_bare_test_component_matches(self):
        assert _is_test_path("test")

    def test_empty_path_is_not_a_test(self):
        assert not _is_test_path("")

    def test_unicode_paths(self):
        assert _is_test_path("tests/tëst.py")
        assert not _is_test_path("src/prüfung/tëst.py")


class TestEligibility:
    def test_source_file_is_eligible(self):
        assert _eligibility({"path": "src/api.py", "status": "modified"}) == (True, "")

    def test_empty_change_is_eligible(self):
        assert _eligibility({}) == (True, "")

    def test_failed_status_uses_error_reason(self):
        assert _eligibility({"path": "src/api.py", "status": "failed", "error": "boom"}) == (False, "boom")

    def test_failed_status_without_error_falls_back(self):
        assert _eligibility({"path": "src/api.py", "status": "failed"}) == (False, "change lookup failed")
        assert _eligibility({"path": "src/api.py", "status": "FAILED", "error": ""}) == (False, "change lookup failed")

    def test_deleted_variants_are_excluded(self):
        assert _eligibility({"path": "src/api.py", "status": "delete"}) == (False, "deleted file")
        assert _eligibility({"path": "src/api.py", "status": "deleted"}) == (False, "deleted file")
        assert _eligibility({"path": "src/api.py", "status": "Deleted"}) == (False, "deleted file")

    def test_binary_suffixes_are_excluded_case_insensitively(self):
        for path in ("assets/logo.png", "assets/Logo.PNG", "fonts/x.woff2", "cache/mod.pyc", "docs/deck.pptx"):
            assert _eligibility({"path": path, "status": "modified"}) == (False, "binary file"), path

    def test_generated_and_vendored_parts_are_excluded(self):
        for path in (
            "node_modules/pkg/index.js",
            "dist/bundle.js",
            "Dist/bundle.js",
            "vendor/lib.go",
            ".next/chunk.js",
            "src/generated/client.py",
            "build",  # a bare path component still counts as a part
        ):
            assert _eligibility({"path": path, "status": "modified"}) == (False, "generated or vendored file"), path

    def test_exclusion_precedence_is_failed_deleted_binary_generated(self):
        assert _eligibility({"path": "assets/logo.png", "status": "failed", "error": "boom"}) == (False, "boom")
        assert _eligibility({"path": "assets/logo.png", "status": "deleted"}) == (False, "deleted file")
        assert _eligibility({"path": "node_modules/logo.png", "status": "modified"}) == (False, "binary file")


class TestFileScope:
    def test_joins_repository_and_path(self):
        assert _file_scope({"repository": "acme/api", "path": "src/a.py"}) == "acme/api:src/a.py"

    def test_missing_fields_produce_bare_separator(self):
        assert _file_scope({}) == ":"


class TestAnalyseChangedFiles:
    def test_change_with_tests_yields_no_findings_and_complete_coverage(self):
        prod = _change(path="src/api.py", change_id="ok")
        test = _change(path="tests/test_api.py", change_id="ok")
        reports, findings, coverage = analyse_changed_files([prod, test])
        assert reports == [
            {**prod, "analysis_status": "succeeded", "reason": ""},
            {**test, "analysis_status": "succeeded", "reason": ""},
        ]
        assert findings == []
        assert coverage == {
            "component": "code",
            "status": "complete",
            "has_data": True,
            "completion_pct": 100.0,
            "window_days": 120,
            "discovered": 2,
            "eligible": 2,
            "attempted": 2,
            "succeeded": 2,
            "cached": 0,
            "failed": 0,
            "unchanged": 0,
            "inaccessible": 0,
            "truncated": 0,
            "completed": 2,
            "per_container": {
                "github:acme/acme/api": {"discovered": 2, "succeeded": 2, "cached": 0, "failed": 0, "unchanged": 0}
            },
            "grouped_errors": [],
            "assets": [
                {
                    "provider": "github",
                    "container": "acme/acme/api",
                    "asset": "src/api.py",
                    "status": "succeeded",
                    "detail": "",
                    "eligible": True,
                },
                {
                    "provider": "github",
                    "container": "acme/acme/api",
                    "asset": "tests/test_api.py",
                    "status": "succeeded",
                    "detail": "",
                    "eligible": True,
                },
            ],
        }

    def test_exclusions_and_failures_are_accounted_exactly(self):
        deleted = _change(path="src/old.py", status="deleted", change_id="d1")
        changes = [
            _change(path="src/broken.py", status="failed", error="boom", change_id="f1"),
            deleted,
            _change(path="assets/Logo.PNG", change_id="b1"),
            _change(path="node_modules/pkg/index.js", change_id="g1"),
            _change(path="src/api.py", change_id="ok"),
            _change(path="tests/test_api.py", change_id="ok"),
        ]
        reports, findings, coverage = analyse_changed_files(changes)
        assert [(r["analysis_status"], r["reason"]) for r in reports] == [
            ("failed", "boom"),
            ("excluded", "deleted file"),
            ("excluded", "binary file"),
            ("excluded", "generated or vendored file"),
            ("succeeded", ""),
            ("succeeded", ""),
        ]
        assert reports[1] == {**deleted, "analysis_status": "excluded", "reason": "deleted file"}
        assert findings == []
        assets = coverage.pop("assets")
        assert len(assets) == 6
        # Excluded files are tracked as ineligible "unchanged"; only the failed
        # lookup stays eligible and therefore drags completion below 100.
        assert coverage == {
            "component": "code",
            "status": "partial",
            "has_data": True,
            "completion_pct": 66.7,
            "window_days": 120,
            "discovered": 6,
            "eligible": 3,
            "attempted": 3,
            "succeeded": 2,
            "cached": 0,
            "failed": 1,
            "unchanged": 3,
            "inaccessible": 0,
            "truncated": 0,
            "completed": 2,
            "per_container": {
                "github:acme/acme/api": {"discovered": 6, "succeeded": 2, "cached": 0, "failed": 1, "unchanged": 3}
            },
            "grouped_errors": [
                {
                    "provider": "github",
                    "status": "failed",
                    "detail": "boom",
                    "count": 1,
                    "containers": ["acme/acme/api"],
                    "examples": ["src/broken.py"],
                }
            ],
        }

    def test_hotspot_and_large_change_findings_are_exact(self):
        changes = [
            _change(path="src/hot.py", change_id="c1", url="https://example.test/c1", additions=100, deletions=100),
            _change(path="src/hot.py", change_id="c2", url="https://example.test/c2", additions=100, deletions=100),
            _change(
                path="src/hot.py",
                change_id="c3",
                url="https://example.test/c3",
                additions=100,
                deletions=100,
                confidence="medium",
            ),
        ]
        _reports, findings, _coverage = analyse_changed_files(changes)
        assert [f["id"] for f in findings] == [
            "acme/api:src/hot.py:hotspot",
            "acme/api:src/hot.py:large-change",
            "acme/api:c1:tests",
            "acme/api:c2:tests",
            "acme/api:c3:tests",
        ]
        # `first_by_scope` is built with a last-write-wins dict comprehension, so
        # despite its name the hotspot exemplar is the LAST change for the scope:
        # link/confidence come from c3. A Go port must mirror this exactly.
        assert findings[0] == {
            "id": "acme/api:src/hot.py:hotspot",
            "category": "hotspot",
            "title": "Stabilise a frequently changed file",
            "detail": (
                "Review why this file changes repeatedly and split responsibilities if it has become a bottleneck."
            ),
            "priority": "medium",
            "impact": "Reduces regression risk in a concentrated change hotspot.",
            "confidence": "medium",
            "evidence": "acme/api:src/hot.py changed in 3 selected-user changes during the window.",
            "link": "https://example.test/c3",
            "affected_scope": ["acme/api:src/hot.py"],
            "next_steps": [
                "Review recent changes together.",
                "Extract unstable responsibilities or add focused tests.",
            ],
            "owner_role": "Selected contributor",
            "effort": "medium",
            "completion_check": (
                "The file has a clear responsibility and regression coverage for its frequently changed paths."
            ),
        }
        assert findings[1] == {
            "id": "acme/api:src/hot.py:large-change",
            "category": "change-size",
            "title": "Break down a large code change",
            "detail": "Split large changes into independently reviewable units with targeted validation.",
            "priority": "high",
            "impact": "Makes review and rollback safer.",
            "confidence": "medium",
            "evidence": "acme/api:src/hot.py accumulated 600 added/deleted lines.",
            "link": "https://example.test/c3",
            "affected_scope": ["acme/api:src/hot.py"],
            "next_steps": ["Separate behavioural and mechanical changes.", "Add focused tests for each unit."],
            "owner_role": "Selected contributor",
            "effort": "small",
            "completion_check": "Future changes are split into reviewable units with explicit validation.",
        }
        # Per-change testing exemplars keep their own confidence (first of group).
        assert findings[2]["confidence"] == "high"
        assert findings[4]["confidence"] == "medium"

    def test_hotspot_priority_boundary_at_five_touches(self):
        def hotspot_for(count: int) -> dict:
            changes = [_change(path="src/hot.py", change_id=f"c{i}", additions=0, deletions=0) for i in range(count)]
            _reports, findings, _coverage = analyse_changed_files(changes)
            return next(f for f in findings if f["category"] == "hotspot")

        assert hotspot_for(4)["priority"] == "medium"
        assert hotspot_for(5)["priority"] == "high"

    def test_churn_boundary_at_five_hundred(self):
        below = analyse_changed_files([_change(additions=250, deletions=249)])[1]
        assert [f["category"] for f in below] == ["testing"]
        at = analyse_changed_files([_change(additions=250, deletions=250)])[1]
        assert [f["category"] for f in at] == ["change-size", "testing"]
        assert at[0]["evidence"] == "acme/api:src/api.py accumulated 500 added/deleted lines."

    def test_none_additions_count_as_zero_churn(self):
        _reports, findings, _coverage = analyse_changed_files([_change(additions=None, deletions=None)])
        assert [f["category"] for f in findings] == ["testing"]

    def test_missing_tests_finding_is_exact_and_ignores_doc_files(self):
        changes = [
            _change(path="README.md", change_id="c9", url="https://example.test/c9"),
            _change(path="src/pay.py", change_id="c9", url="https://example.test/c9"),
            _change(path="notes.txt", change_id="c9", url="https://example.test/c9"),
        ]
        _reports, findings, _coverage = analyse_changed_files(changes)
        assert findings == [
            {
                "id": "acme/api:c9:tests",
                "category": "testing",
                "title": "Add tests alongside production changes",
                "detail": "Add or update focused tests in the same commit or PR as the behavioural change.",
                "priority": "high",
                "impact": "Makes selected-user changes safer to review and release.",
                "confidence": "high",
                "evidence": "1 production file(s) changed without a test-file change.",
                "link": "https://example.test/c9",
                "affected_scope": ["acme/api:src/pay.py"],
                "next_steps": [
                    "Identify the changed behaviour.",
                    "Add a regression test that fails without the change.",
                ],
                "owner_role": "Selected contributor",
                "effort": "small",
                "completion_check": "The change has an automated test covering its intended behaviour.",
            }
        ]

    def test_docs_only_and_test_only_changes_yield_no_findings(self):
        docs_only = [_change(path="README.md", change_id="c1"), _change(path="notes.txt", change_id="c1")]
        assert analyse_changed_files(docs_only)[1] == []
        tests_only = [_change(path="tests/test_edge.py", change_id="c2")]
        assert analyse_changed_files(tests_only)[1] == []

    def test_excluded_repeats_do_not_form_hotspots(self):
        changes = [_change(path="assets/logo.png", change_id=f"c{i}") for i in range(5)]
        reports, findings, _coverage = analyse_changed_files(changes)
        assert findings == []
        assert {r["analysis_status"] for r in reports} == {"excluded"}

    def test_truncated_change_stays_eligible_but_gaps_coverage(self):
        change = _change(truncated=True, error="partial diff")
        reports, findings, coverage = analyse_changed_files([change])
        assert reports == [{**change, "analysis_status": "truncated", "reason": "partial diff"}]
        assert [f["category"] for f in findings] == ["testing"]
        assert coverage["truncated"] == 1
        assert coverage["completed"] == 0
        assert coverage["status"] == "failed"
        assert coverage["completion_pct"] == 0.0

    def test_empty_changes_and_window_days_passthrough(self):
        reports, findings, coverage = analyse_changed_files([], window_days=7)
        assert reports == []
        assert findings == []
        assert coverage == {
            "component": "code",
            "status": "no_data",
            "has_data": False,
            "completion_pct": 100.0,
            "window_days": 7,
            "discovered": 0,
            "eligible": 0,
            "attempted": 0,
            "succeeded": 0,
            "cached": 0,
            "failed": 0,
            "unchanged": 0,
            "inaccessible": 0,
            "truncated": 0,
            "completed": 0,
            "per_container": {},
            "grouped_errors": [],
            "assets": [],
        }

    def test_unicode_paths_flow_through_scopes_and_ids(self):
        _reports, findings, _coverage = analyse_changed_files([_change(path="src/héllo/fiłe.py", change_id="u1")])
        assert [f["id"] for f in findings] == ["acme/api:u1:tests"]
        assert findings[0]["affected_scope"] == ["acme/api:src/héllo/fiłe.py"]


def _stub_finding(category: str, title: str, priority: str, scopes: list[str], evidence: str = "E.") -> dict:
    return {"category": category, "title": title, "priority": priority, "evidence": evidence, "affected_scope": scopes}


class TestPrioritizeActions:
    def test_empty_findings_yield_no_actions(self):
        assert prioritize_actions([]) == []

    def test_exact_ordering_priority_then_breadth_then_title(self):
        findings = [
            _stub_finding("testing", "Beta wide", "high", ["r1"]),
            _stub_finding("testing", "Beta wide", "high", ["r2"]),
            _stub_finding("docs", "Alpha narrow", "high", ["r1"]),
            _stub_finding("ops", "Zulu", "critical", ["r9"]),
            _stub_finding("misc", "Aardvark", "urgent", ["r1"]),  # unknown priority sorts last
            _stub_finding("hygiene", "Mike", "low", ["r1"]),
            _stub_finding("deps", "Lima", "medium", ["r1"]),
        ]
        actions = prioritize_actions(findings)
        assert [a["title"] for a in actions] == ["Zulu", "Beta wide", "Alpha narrow", "Lima", "Mike", "Aardvark"]
        assert actions[1] == {
            "category": "testing",
            "title": "Beta wide",
            "priority": "high",
            "evidence": "E. Affects 2 repositories.",
            "affected_scope": ["r1", "r2"],
            "breadth": 2,
        }

    def test_scopes_are_sorted_deduped_and_falsy_dropped(self):
        findings = [
            _stub_finding("c", "T", "low", ["r1", "", "r1"]),
            _stub_finding("c", "T", "low", ["r0"]),
        ]
        actions = prioritize_actions(findings)
        assert len(actions) == 1
        assert actions[0]["affected_scope"] == ["r0", "r1"]
        assert actions[0]["breadth"] == 2
        assert actions[0]["evidence"] == "E. Affects 2 repositories."

    def test_single_scope_action_keeps_evidence_verbatim(self):
        actions = prioritize_actions([_stub_finding("c", "T", "medium", ["r1"])])
        assert actions == [
            {
                "category": "c",
                "title": "T",
                "priority": "medium",
                "evidence": "E.",
                "affected_scope": ["r1"],
                "breadth": 1,
            }
        ]

    def test_same_title_different_category_stays_separate(self):
        findings = [
            _stub_finding("a", "T", "high", ["r1"]),
            _stub_finding("b", "T", "high", ["r1"]),
        ]
        actions = prioritize_actions(findings)
        assert [(a["category"], a["breadth"]) for a in actions] == [("a", 1), ("b", 1)]

    def test_title_tiebreak_is_alphabetical(self):
        findings = [
            _stub_finding("x", "Beta", "high", ["r1"]),
            _stub_finding("y", "Alpha", "high", ["r1"]),
        ]
        assert [a["title"] for a in prioritize_actions(findings)] == ["Alpha", "Beta"]

    def test_input_findings_are_not_mutated(self):
        findings = [
            _stub_finding("testing", "T", "high", ["r1"]),
            _stub_finding("testing", "T", "high", ["r2"]),
        ]
        snapshot = copy.deepcopy(findings)
        prioritize_actions(findings)
        assert findings == snapshot


class TestChangedFileSummary:
    def test_empty_inputs_produce_exact_zero_summary(self):
        assert changed_file_summary([], []) == {
            "files_analysed": 0,
            "files_excluded": 0,
            "files_failed": 0,
            "repositories_touched": 0,
            "authored_commit_files": 0,
            "authored_pr_files": 0,
            "findings": 0,
            "by_category": {},
        }

    def test_mixed_reports_summarise_exactly(self):
        reports = [
            {"analysis_status": "succeeded", "repository": "api", "attribution": "authored_commit"},
            {"analysis_status": "truncated", "repository": "web", "attribution": "authored_pr"},
            {"analysis_status": "succeeded", "repository": "api", "attribution": "authored_pr"},
            {"analysis_status": "excluded", "repository": "web", "attribution": "authored_commit"},
            {"analysis_status": "failed", "repository": "api", "attribution": "authored_commit"},
            {"analysis_status": "succeeded", "repository": "", "attribution": "authored_commit"},
        ]
        findings = [{"category": "testing"}, {"category": "hotspot"}, {"category": "testing"}]
        summary = changed_file_summary(reports, findings)
        # Attribution counts only cover eligible files: the failed authored
        # commit is out, and the empty repository never counts as touched.
        assert summary == {
            "files_analysed": 4,
            "files_excluded": 1,
            "files_failed": 1,
            "repositories_touched": 2,
            "authored_commit_files": 2,
            "authored_pr_files": 2,
            "findings": 3,
            "by_category": {"hotspot": 1, "testing": 2},
        }
        assert list(summary["by_category"]) == ["hotspot", "testing"]


class TestAnalyseRepositoryHealth:
    def _repo(self, **overrides) -> dict:
        base = {
            "provider": "github",
            "container": "acme",
            "name": "acme/api",
            "active": True,
            "url": "https://example.test/acme/api",
            "default_branch": "main",
            "paths": list(_HEALTHY_PATHS),
        }
        base.update(overrides)
        return base

    def test_fully_equipped_repo_yields_no_findings(self):
        reports, findings = analyse_repository_health([self._repo()], set())
        assert findings == []
        assert reports == [
            {
                "provider": "github",
                "container": "acme",
                "repository": "acme/api",
                "url": "https://example.test/acme/api",
                "default_branch": "main",
                "files_scanned": 7,
                "status": "succeeded",
                "findings": [],
            }
        ]

    def test_bare_repo_yields_all_baseline_findings_in_order(self):
        _reports, findings = analyse_repository_health([self._repo(paths=[])], set())
        assert [f["category"] for f in findings] == [
            "documentation",
            "architecture",
            "testing",
            "delivery",
            "maintainability",
            "ownership",
            "security",
        ]
        assert findings[0] == {
            "id": "github:acme/api:documentation:add-a-repository-readme",
            "category": "documentation",
            "title": "Add a repository README",
            "detail": "Document the repository purpose, setup, test, and release workflow.",
            "priority": "high",
            "impact": "Improves delivery safety and maintainability",
            "confidence": "high",
            "evidence": "No README was found on the default branch.",
            "link": "https://example.test/acme/api",
            "affected_scope": ["acme/api"],
            "next_steps": ["Document the repository purpose, setup, test, and release workflow."],
            "owner_role": "Repository maintainer",
            "effort": "small",
            "completion_check": "Confirm add a repository readme for acme/api.",
        }

    def test_operations_finding_requires_infra_without_runbook(self):
        with_infra = self._repo(paths=[*_HEALTHY_PATHS, "Dockerfile"])
        _reports, findings = analyse_repository_health([with_infra], set())
        assert [f["category"] for f in findings] == ["operations"]
        assert findings[0]["priority"] == "high"
        assert findings[0]["confidence"] == "medium"
        with_runbook = self._repo(paths=[*_HEALTHY_PATHS, "Dockerfile", "docs/runbook.md"])
        assert analyse_repository_health([with_runbook], set())[1] == []

    def test_azdo_activity_comes_from_active_names_not_the_flag(self):
        named = self._repo(provider="azdo", name="Api", active=False)
        flagged = self._repo(provider="azdo", name="Other", active=True)
        reports, _findings = analyse_repository_health([named, flagged], {("azdo", "api")})
        assert reports[0]["status"] == "succeeded"
        assert reports[0]["repository"] == "Api"
        assert reports[1]["status"] == "unchanged"

    def test_inactive_github_repo_is_reported_unchanged_verbatim(self):
        repo = self._repo(active=False)
        reports, findings = analyse_repository_health([repo], set())
        assert findings == []
        assert reports == [{**repo, "status": "unchanged", "findings": []}]

    def test_discovery_error_repo_is_skipped_entirely(self):
        reports, findings = analyse_repository_health([self._repo(discovery_error="403")], set())
        assert reports == []
        assert findings == []

    def test_listing_error_marks_report_truncated(self):
        reports, _findings = analyse_repository_health([self._repo(error="timeout")], set())
        assert reports[0]["status"] == "truncated"


class TestRepositoryHealthSummary:
    def test_empty_inputs_produce_exact_zero_summary(self):
        assert repository_health_summary([], []) == {
            "repositories_analysed": 0,
            "repositories_unchanged": 0,
            "files_inventoried": 0,
            "findings": 0,
            "by_category": {},
        }

    def test_counts_and_sorted_categories_are_exact(self):
        reports = [
            {"status": "succeeded", "files_scanned": 3},
            {"status": "truncated", "files_scanned": 2},
            {"status": "unchanged"},
        ]
        findings = [{"category": "testing"}, {"category": "delivery"}, {"category": "testing"}]
        summary = repository_health_summary(reports, findings)
        assert summary == {
            "repositories_analysed": 2,
            "repositories_unchanged": 1,
            "files_inventoried": 5,
            "findings": 3,
            "by_category": {"delivery": 1, "testing": 2},
        }
        assert list(summary["by_category"]) == ["delivery", "testing"]
