package analysis

// code_health_test.go — port of tests/unit/test_code_health.py. The Python
// suite deliberately pins whole dicts, exact floats, and exact ordering; the
// pins are transcribed here verbatim (as pysem.JSONDumps byte strings, which
// match Python json.dumps defaults), so every asserted literal doubles as a
// parity fixture.

import (
	"encoding/json"
	"fmt"
	"testing"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// obj builds an ordered object from key/value pairs, mirroring a Python dict
// literal.
func obj(kv ...any) *pysem.Obj {
	o := pysem.EmptyObj()
	for i := 0; i+1 < len(kv); i += 2 {
		o.Set(kv[i].(string), kv[i+1])
	}
	return o
}

// chg mirrors the _change fixture: the base dict plus overrides (existing
// keys keep their position, new keys append — Python dict.update).
func chg(overrides ...any) *pysem.Obj {
	c := obj(
		"provider", "github",
		"container", "acme",
		"repository", "acme/api",
		"path", "src/api.py",
		"status", "modified",
		"additions", json.Number("10"),
		"deletions", json.Number("2"),
		"attribution", "authored_commit",
		"confidence", "high",
		"change_id", "c1",
		"url", "https://example.test/c1",
	)
	for i := 0; i+1 < len(overrides); i += 2 {
		c.Set(overrides[i].(string), overrides[i+1])
	}
	return c
}

// healthyPaths mirrors _HEALTHY_PATHS — one path per repository-health check.
func healthyPaths() []any {
	return []any{
		"README.md",
		"docs/adr/0001-record-decisions.md",
		"tests/test_api.py",
		".github/workflows/ci.yml",
		"pyproject.toml",
		".github/CODEOWNERS",
		".github/dependabot.yml",
	}
}

// repoObj mirrors TestAnalyseRepositoryHealth._repo.
func repoObj(overrides ...any) *pysem.Obj {
	r := obj(
		"provider", "github",
		"container", "acme",
		"name", "acme/api",
		"active", true,
		"url", "https://example.test/acme/api",
		"default_branch", "main",
		"paths", healthyPaths(),
	)
	for i := 0; i+1 < len(overrides); i += 2 {
		r.Set(overrides[i].(string), overrides[i+1])
	}
	return r
}

func dumps(v any) string { return pysem.JSONDumps(v) }

func assertDump(t *testing.T, got any, want string) {
	t.Helper()
	if d := dumps(got); d != want {
		t.Errorf("dump mismatch\n got: %s\nwant: %s", d, want)
	}
}

func TestIsTestPathDirectoryPartsMatch(t *testing.T) {
	for _, path := range []string{
		"tests/test_api.py", "src/tests/util.py", "__tests__/App.tsx",
		"spec/models.rb", "specs/models.rb", "test/helper.js",
	} {
		if !isTestPath(path) {
			t.Errorf("isTestPath(%q) = false, want true", path)
		}
	}
}

func TestIsTestPathFilenamesMatch(t *testing.T) {
	for _, path := range []string{"test_helpers.py", "src/test_edge.py", "app.test.ts", "app.spec.js"} {
		if !isTestPath(path) {
			t.Errorf("isTestPath(%q) = false, want true", path)
		}
	}
}

func TestIsTestPathMatchingIsCaseInsensitive(t *testing.T) {
	for _, path := range []string{"Tests/Foo.cs", "TEST_helpers.py", "App.Test.TS", "App.Spec.JS"} {
		if !isTestPath(path) {
			t.Errorf("isTestPath(%q) = false, want true", path)
		}
	}
}

func TestIsTestPathNonTestPathsDoNotMatch(t *testing.T) {
	for _, path := range []string{
		"src/api.py",
		"src/testing/util.py", // "testing" is not a whole test part
		"contest/entry.py",
		"attest.py",
		"conftest.py",
		"protest/spec.txt", // no ".spec." in the name, "protest" is not "spec"
	} {
		if isTestPath(path) {
			t.Errorf("isTestPath(%q) = true, want false", path)
		}
	}
}

func TestIsTestPathSuffixStyleTestNamesDoNotMatch(t *testing.T) {
	// The Go-style "_test" suffix is deliberately outside the vocabulary,
	// only the "test_" prefix counts.
	for _, path := range []string{"src/api_test.py", "pkg/store_test.go"} {
		if isTestPath(path) {
			t.Errorf("isTestPath(%q) = true, want false", path)
		}
	}
}

func TestIsTestPathBareTestComponentMatches(t *testing.T) {
	if !isTestPath("test") {
		t.Error("isTestPath(\"test\") = false, want true")
	}
}

func TestIsTestPathEmptyPathIsNotATest(t *testing.T) {
	if isTestPath("") {
		t.Error("isTestPath(\"\") = true, want false")
	}
}

func TestIsTestPathUnicodePaths(t *testing.T) {
	if !isTestPath("tests/t\u00ebst.py") {
		t.Error("isTestPath(tests/t\u00ebst.py) = false, want true")
	}
	if isTestPath("src/pr\u00fcfung/t\u00ebst.py") {
		t.Error("isTestPath(src/pr\u00fcfung/t\u00ebst.py) = true, want false")
	}
}

func assertEligibility(t *testing.T, change *pysem.Obj, wantOK bool, wantReason string) {
	t.Helper()
	ok, reason := eligibility(change)
	if ok != wantOK || reason != wantReason {
		t.Errorf("eligibility(%s) = (%v, %q), want (%v, %q)", dumps(change), ok, reason, wantOK, wantReason)
	}
}

func TestEligibilitySourceFileIsEligible(t *testing.T) {
	assertEligibility(t, obj("path", "src/api.py", "status", "modified"), true, "")
}

func TestEligibilityEmptyChangeIsEligible(t *testing.T) {
	assertEligibility(t, obj(), true, "")
}

func TestEligibilityFailedStatusUsesErrorReason(t *testing.T) {
	assertEligibility(t, obj("path", "src/api.py", "status", "failed", "error", "boom"), false, "boom")
}

func TestEligibilityFailedStatusWithoutErrorFallsBack(t *testing.T) {
	assertEligibility(t, obj("path", "src/api.py", "status", "failed"), false, "change lookup failed")
	assertEligibility(t, obj("path", "src/api.py", "status", "FAILED", "error", ""), false, "change lookup failed")
}

func TestEligibilityDeletedVariantsAreExcluded(t *testing.T) {
	for _, status := range []string{"delete", "deleted", "Deleted"} {
		assertEligibility(t, obj("path", "src/api.py", "status", status), false, "deleted file")
	}
}

func TestEligibilityBinarySuffixesAreExcludedCaseInsensitively(t *testing.T) {
	for _, path := range []string{"assets/logo.png", "assets/Logo.PNG", "fonts/x.woff2", "cache/mod.pyc", "docs/deck.pptx"} {
		assertEligibility(t, obj("path", path, "status", "modified"), false, "binary file")
	}
}

func TestEligibilityGeneratedAndVendoredPartsAreExcluded(t *testing.T) {
	for _, path := range []string{
		"node_modules/pkg/index.js",
		"dist/bundle.js",
		"Dist/bundle.js",
		"vendor/lib.go",
		".next/chunk.js",
		"src/generated/client.py",
		"build", // a bare path component still counts as a part
	} {
		assertEligibility(t, obj("path", path, "status", "modified"), false, "generated or vendored file")
	}
}

func TestEligibilityExclusionPrecedenceIsFailedDeletedBinaryGenerated(t *testing.T) {
	assertEligibility(t, obj("path", "assets/logo.png", "status", "failed", "error", "boom"), false, "boom")
	assertEligibility(t, obj("path", "assets/logo.png", "status", "deleted"), false, "deleted file")
	assertEligibility(t, obj("path", "node_modules/logo.png", "status", "modified"), false, "binary file")
}

func TestFileScopeJoinsRepositoryAndPath(t *testing.T) {
	if got := fileScope(obj("repository", "acme/api", "path", "src/a.py")); got != "acme/api:src/a.py" {
		t.Errorf("fileScope = %q", got)
	}
}

func TestFileScopeMissingFieldsProduceBareSeparator(t *testing.T) {
	if got := fileScope(obj()); got != ":" {
		t.Errorf("fileScope = %q", got)
	}
}

func TestAnalyseChangedFilesWithTestsYieldsNoFindingsAndCompleteCoverage(t *testing.T) {
	prod := chg("path", "src/api.py", "change_id", "ok")
	test := chg("path", "tests/test_api.py", "change_id", "ok")
	reports, findings, coverage := AnalyseChangedFiles([]any{prod, test}, 120)
	assertDump(t, reports, `[{"provider": "github", "container": "acme", "repository": "acme/api", "path": "src/api.py", "status": "modified", "additions": 10, "deletions": 2, "attribution": "authored_commit", "confidence": "high", "change_id": "ok", "url": "https://example.test/c1", "analysis_status": "succeeded", "reason": ""}, {"provider": "github", "container": "acme", "repository": "acme/api", "path": "tests/test_api.py", "status": "modified", "additions": 10, "deletions": 2, "attribution": "authored_commit", "confidence": "high", "change_id": "ok", "url": "https://example.test/c1", "analysis_status": "succeeded", "reason": ""}]`)
	if len(findings) != 0 {
		t.Errorf("findings = %s, want []", dumps(findings))
	}
	assertDump(t, coverage, `{"component": "code", "status": "complete", "has_data": true, "completion_pct": 100.0, "window_days": 120, "discovered": 2, "eligible": 2, "attempted": 2, "succeeded": 2, "cached": 0, "failed": 0, "unchanged": 0, "inaccessible": 0, "truncated": 0, "completed": 2, "per_container": {"github:acme/acme/api": {"discovered": 2, "succeeded": 2, "cached": 0, "failed": 0, "unchanged": 0}}, "grouped_errors": [], "assets": [{"provider": "github", "container": "acme/acme/api", "asset": "src/api.py", "status": "succeeded", "detail": "", "eligible": true}, {"provider": "github", "container": "acme/acme/api", "asset": "tests/test_api.py", "status": "succeeded", "detail": "", "eligible": true}]}`)
}

func TestAnalyseChangedFilesExclusionsAndFailuresAreAccountedExactly(t *testing.T) {
	deleted := chg("path", "src/old.py", "status", "deleted", "change_id", "d1")
	changes := []any{
		chg("path", "src/broken.py", "status", "failed", "error", "boom", "change_id", "f1"),
		deleted,
		chg("path", "assets/Logo.PNG", "change_id", "b1"),
		chg("path", "node_modules/pkg/index.js", "change_id", "g1"),
		chg("path", "src/api.py", "change_id", "ok"),
		chg("path", "tests/test_api.py", "change_id", "ok"),
	}
	reports, findings, coverage := AnalyseChangedFiles(changes, 120)
	wantPairs := [][2]string{
		{"failed", "boom"},
		{"excluded", "deleted file"},
		{"excluded", "binary file"},
		{"excluded", "generated or vendored file"},
		{"succeeded", ""},
		{"succeeded", ""},
	}
	if len(reports) != len(wantPairs) {
		t.Fatalf("len(reports) = %d, want %d", len(reports), len(wantPairs))
	}
	for i, want := range wantPairs {
		r := pysem.AsObj(reports[i])
		if r.Get("analysis_status") != want[0] || r.Get("reason") != want[1] {
			t.Errorf("reports[%d] = (%v, %v), want %v", i, r.Get("analysis_status"), r.Get("reason"), want)
		}
	}
	assertDump(t, reports[1], `{"provider": "github", "container": "acme", "repository": "acme/api", "path": "src/old.py", "status": "deleted", "additions": 10, "deletions": 2, "attribution": "authored_commit", "confidence": "high", "change_id": "d1", "url": "https://example.test/c1", "analysis_status": "excluded", "reason": "deleted file"}`)
	if len(findings) != 0 {
		t.Errorf("findings = %s, want []", dumps(findings))
	}
	assets, _ := coverage.Get("assets").([]any)
	if len(assets) != 6 {
		t.Errorf("len(assets) = %d, want 6", len(assets))
	}
	// Excluded files are tracked as ineligible "unchanged"; only the failed
	// lookup stays eligible and therefore drags completion below 100.
	coverage.Delete("assets")
	assertDump(t, coverage, `{"component": "code", "status": "partial", "has_data": true, "completion_pct": 66.7, "window_days": 120, "discovered": 6, "eligible": 3, "attempted": 3, "succeeded": 2, "cached": 0, "failed": 1, "unchanged": 3, "inaccessible": 0, "truncated": 0, "completed": 2, "per_container": {"github:acme/acme/api": {"discovered": 6, "succeeded": 2, "cached": 0, "failed": 1, "unchanged": 3}}, "grouped_errors": [{"provider": "github", "status": "failed", "detail": "boom", "count": 1, "containers": ["acme/acme/api"], "examples": ["src/broken.py"]}]}`)
}

func TestAnalyseChangedFilesHotspotAndLargeChangeFindingsAreExact(t *testing.T) {
	changes := []any{
		chg("path", "src/hot.py", "change_id", "c1", "url", "https://example.test/c1",
			"additions", json.Number("100"), "deletions", json.Number("100")),
		chg("path", "src/hot.py", "change_id", "c2", "url", "https://example.test/c2",
			"additions", json.Number("100"), "deletions", json.Number("100")),
		chg("path", "src/hot.py", "change_id", "c3", "url", "https://example.test/c3",
			"additions", json.Number("100"), "deletions", json.Number("100"), "confidence", "medium"),
	}
	_, findings, _ := AnalyseChangedFiles(changes, 120)
	wantIDs := []string{
		"acme/api:src/hot.py:hotspot",
		"acme/api:src/hot.py:large-change",
		"acme/api:c1:tests",
		"acme/api:c2:tests",
		"acme/api:c3:tests",
	}
	if len(findings) != len(wantIDs) {
		t.Fatalf("len(findings) = %d, want %d", len(findings), len(wantIDs))
	}
	for i, id := range wantIDs {
		if got := pysem.AsObj(findings[i]).Get("id"); got != id {
			t.Errorf("findings[%d] id = %v, want %q", i, got, id)
		}
	}
	// first_by_scope is built with a last-write-wins dict comprehension, so
	// despite its name the hotspot exemplar is the LAST change for the scope:
	// link/confidence come from c3.
	assertDump(t, findings[0], `{"id": "acme/api:src/hot.py:hotspot", "category": "hotspot", "title": "Stabilise a frequently changed file", "detail": "Review why this file changes repeatedly and split responsibilities if it has become a bottleneck.", "priority": "medium", "impact": "Reduces regression risk in a concentrated change hotspot.", "confidence": "medium", "evidence": "acme/api:src/hot.py changed in 3 selected-user changes during the window.", "link": "https://example.test/c3", "affected_scope": ["acme/api:src/hot.py"], "next_steps": ["Review recent changes together.", "Extract unstable responsibilities or add focused tests."], "owner_role": "Selected contributor", "effort": "medium", "completion_check": "The file has a clear responsibility and regression coverage for its frequently changed paths."}`)
	assertDump(t, findings[1], `{"id": "acme/api:src/hot.py:large-change", "category": "change-size", "title": "Break down a large code change", "detail": "Split large changes into independently reviewable units with targeted validation.", "priority": "high", "impact": "Makes review and rollback safer.", "confidence": "medium", "evidence": "acme/api:src/hot.py accumulated 600 added/deleted lines.", "link": "https://example.test/c3", "affected_scope": ["acme/api:src/hot.py"], "next_steps": ["Separate behavioural and mechanical changes.", "Add focused tests for each unit."], "owner_role": "Selected contributor", "effort": "small", "completion_check": "Future changes are split into reviewable units with explicit validation."}`)
	// Per-change testing exemplars keep their own confidence (first of group).
	if got := pysem.AsObj(findings[2]).Get("confidence"); got != "high" {
		t.Errorf("findings[2] confidence = %v, want high", got)
	}
	if got := pysem.AsObj(findings[4]).Get("confidence"); got != "medium" {
		t.Errorf("findings[4] confidence = %v, want medium", got)
	}
}

func hotspotFor(t *testing.T, count int) *pysem.Obj {
	t.Helper()
	changes := []any{}
	for i := 0; i < count; i++ {
		changes = append(changes, chg("path", "src/hot.py", "change_id", fmt.Sprintf("c%d", i),
			"additions", json.Number("0"), "deletions", json.Number("0")))
	}
	_, findings, _ := AnalyseChangedFiles(changes, 120)
	for _, f := range findings {
		if o := pysem.AsObj(f); o.Get("category") == "hotspot" {
			return o
		}
	}
	t.Fatalf("no hotspot finding for %d touches", count)
	return nil
}

func TestAnalyseChangedFilesHotspotPriorityBoundaryAtFiveTouches(t *testing.T) {
	if got := hotspotFor(t, 4).Get("priority"); got != "medium" {
		t.Errorf("4 touches priority = %v, want medium", got)
	}
	if got := hotspotFor(t, 5).Get("priority"); got != "high" {
		t.Errorf("5 touches priority = %v, want high", got)
	}
}

func categoriesOf(findings []any) []string {
	out := []string{}
	for _, f := range findings {
		out = append(out, pysem.AsObj(f).Get("category").(string))
	}
	return out
}

func TestAnalyseChangedFilesChurnBoundaryAtFiveHundred(t *testing.T) {
	_, below, _ := AnalyseChangedFiles([]any{chg("additions", json.Number("250"), "deletions", json.Number("249"))}, 120)
	if got := categoriesOf(below); len(got) != 1 || got[0] != "testing" {
		t.Errorf("below categories = %v, want [testing]", got)
	}
	_, at, _ := AnalyseChangedFiles([]any{chg("additions", json.Number("250"), "deletions", json.Number("250"))}, 120)
	if got := categoriesOf(at); len(got) != 2 || got[0] != "change-size" || got[1] != "testing" {
		t.Errorf("at categories = %v, want [change-size testing]", got)
	}
	wantEvidence := "acme/api:src/api.py accumulated 500 added/deleted lines."
	if got := pysem.AsObj(at[0]).Get("evidence"); got != wantEvidence {
		t.Errorf("evidence = %v, want %q", got, wantEvidence)
	}
}

func TestAnalyseChangedFilesNoneAdditionsCountAsZeroChurn(t *testing.T) {
	_, findings, _ := AnalyseChangedFiles([]any{chg("additions", nil, "deletions", nil)}, 120)
	if got := categoriesOf(findings); len(got) != 1 || got[0] != "testing" {
		t.Errorf("categories = %v, want [testing]", got)
	}
}

func TestAnalyseChangedFilesMissingTestsFindingIsExactAndIgnoresDocFiles(t *testing.T) {
	changes := []any{
		chg("path", "README.md", "change_id", "c9", "url", "https://example.test/c9"),
		chg("path", "src/pay.py", "change_id", "c9", "url", "https://example.test/c9"),
		chg("path", "notes.txt", "change_id", "c9", "url", "https://example.test/c9"),
	}
	_, findings, _ := AnalyseChangedFiles(changes, 120)
	assertDump(t, findings, `[{"id": "acme/api:c9:tests", "category": "testing", "title": "Add tests alongside production changes", "detail": "Add or update focused tests in the same commit or PR as the behavioural change.", "priority": "high", "impact": "Makes selected-user changes safer to review and release.", "confidence": "high", "evidence": "1 production file(s) changed without a test-file change.", "link": "https://example.test/c9", "affected_scope": ["acme/api:src/pay.py"], "next_steps": ["Identify the changed behaviour.", "Add a regression test that fails without the change."], "owner_role": "Selected contributor", "effort": "small", "completion_check": "The change has an automated test covering its intended behaviour."}]`)
}

func TestAnalyseChangedFilesDocsOnlyAndTestOnlyChangesYieldNoFindings(t *testing.T) {
	_, docsOnly, _ := AnalyseChangedFiles([]any{
		chg("path", "README.md", "change_id", "c1"),
		chg("path", "notes.txt", "change_id", "c1"),
	}, 120)
	if len(docsOnly) != 0 {
		t.Errorf("docs-only findings = %s, want []", dumps(docsOnly))
	}
	_, testsOnly, _ := AnalyseChangedFiles([]any{chg("path", "tests/test_edge.py", "change_id", "c2")}, 120)
	if len(testsOnly) != 0 {
		t.Errorf("tests-only findings = %s, want []", dumps(testsOnly))
	}
}

func TestAnalyseChangedFilesExcludedRepeatsDoNotFormHotspots(t *testing.T) {
	changes := []any{}
	for i := 0; i < 5; i++ {
		changes = append(changes, chg("path", "assets/logo.png", "change_id", fmt.Sprintf("c%d", i)))
	}
	reports, findings, _ := AnalyseChangedFiles(changes, 120)
	if len(findings) != 0 {
		t.Errorf("findings = %s, want []", dumps(findings))
	}
	for i, r := range reports {
		if got := pysem.AsObj(r).Get("analysis_status"); got != "excluded" {
			t.Errorf("reports[%d] analysis_status = %v, want excluded", i, got)
		}
	}
}

func TestAnalyseChangedFilesTruncatedChangeStaysEligibleButGapsCoverage(t *testing.T) {
	change := chg("truncated", true, "error", "partial diff")
	reports, findings, coverage := AnalyseChangedFiles([]any{change}, 120)
	assertDump(t, reports, `[{"provider": "github", "container": "acme", "repository": "acme/api", "path": "src/api.py", "status": "modified", "additions": 10, "deletions": 2, "attribution": "authored_commit", "confidence": "high", "change_id": "c1", "url": "https://example.test/c1", "truncated": true, "error": "partial diff", "analysis_status": "truncated", "reason": "partial diff"}]`)
	if got := categoriesOf(findings); len(got) != 1 || got[0] != "testing" {
		t.Errorf("categories = %v, want [testing]", got)
	}
	if got := coverage.Get("truncated"); got != int64(1) {
		t.Errorf("truncated = %v, want 1", got)
	}
	if got := coverage.Get("completed"); got != int64(0) {
		t.Errorf("completed = %v, want 0", got)
	}
	if got := coverage.Get("status"); got != "failed" {
		t.Errorf("status = %v, want failed", got)
	}
	if got := coverage.Get("completion_pct"); got != json.Number("0.0") {
		t.Errorf("completion_pct = %v, want 0.0", got)
	}
}

func TestAnalyseChangedFilesEmptyChangesAndWindowDaysPassthrough(t *testing.T) {
	reports, findings, coverage := AnalyseChangedFiles([]any{}, 7)
	if len(reports) != 0 || len(findings) != 0 {
		t.Errorf("reports/findings = %s / %s, want empty", dumps(reports), dumps(findings))
	}
	assertDump(t, coverage, `{"component": "code", "status": "no_data", "has_data": false, "completion_pct": 100.0, "window_days": 7, "discovered": 0, "eligible": 0, "attempted": 0, "succeeded": 0, "cached": 0, "failed": 0, "unchanged": 0, "inaccessible": 0, "truncated": 0, "completed": 0, "per_container": {}, "grouped_errors": [], "assets": []}`)
}

func TestAnalyseChangedFilesUnicodePathsFlowThroughScopesAndIDs(t *testing.T) {
	_, findings, _ := AnalyseChangedFiles([]any{chg("path", "src/h\u00e9llo/fi\u0142e.py", "change_id", "u1")}, 120)
	if len(findings) != 1 {
		t.Fatalf("len(findings) = %d, want 1", len(findings))
	}
	f := pysem.AsObj(findings[0])
	if got := f.Get("id"); got != "acme/api:u1:tests" {
		t.Errorf("id = %v, want acme/api:u1:tests", got)
	}
	scopes, _ := f.Get("affected_scope").([]any)
	if len(scopes) != 1 || scopes[0] != "acme/api:src/h\u00e9llo/fi\u0142e.py" {
		t.Errorf("affected_scope = %v", scopes)
	}
}

// stubFinding mirrors _stub_finding.
func stubFinding(category, title, priority string, scopes []any, evidence string) *pysem.Obj {
	return obj("category", category, "title", title, "priority", priority,
		"evidence", evidence, "affected_scope", scopes)
}

func TestPrioritizeActionsEmptyFindingsYieldNoActions(t *testing.T) {
	if got := PrioritizeActions([]any{}); len(got) != 0 {
		t.Errorf("actions = %s, want []", dumps(got))
	}
}

func TestPrioritizeActionsExactOrderingPriorityThenBreadthThenTitle(t *testing.T) {
	findings := []any{
		stubFinding("testing", "Beta wide", "high", []any{"r1"}, "E."),
		stubFinding("testing", "Beta wide", "high", []any{"r2"}, "E."),
		stubFinding("docs", "Alpha narrow", "high", []any{"r1"}, "E."),
		stubFinding("ops", "Zulu", "critical", []any{"r9"}, "E."),
		stubFinding("misc", "Aardvark", "urgent", []any{"r1"}, "E."), // unknown priority sorts last
		stubFinding("hygiene", "Mike", "low", []any{"r1"}, "E."),
		stubFinding("deps", "Lima", "medium", []any{"r1"}, "E."),
	}
	actions := PrioritizeActions(findings)
	wantTitles := []string{"Zulu", "Beta wide", "Alpha narrow", "Lima", "Mike", "Aardvark"}
	if len(actions) != len(wantTitles) {
		t.Fatalf("len(actions) = %d, want %d", len(actions), len(wantTitles))
	}
	for i, title := range wantTitles {
		if got := pysem.AsObj(actions[i]).Get("title"); got != title {
			t.Errorf("actions[%d] title = %v, want %q", i, got, title)
		}
	}
	assertDump(t, actions[1], `{"category": "testing", "title": "Beta wide", "priority": "high", "evidence": "E. Affects 2 repositories.", "affected_scope": ["r1", "r2"], "breadth": 2}`)
}

func TestPrioritizeActionsScopesAreSortedDedupedAndFalsyDropped(t *testing.T) {
	findings := []any{
		stubFinding("c", "T", "low", []any{"r1", "", "r1"}, "E."),
		stubFinding("c", "T", "low", []any{"r0"}, "E."),
	}
	actions := PrioritizeActions(findings)
	if len(actions) != 1 {
		t.Fatalf("len(actions) = %d, want 1", len(actions))
	}
	action := pysem.AsObj(actions[0])
	assertDump(t, action.Get("affected_scope"), `["r0", "r1"]`)
	if got := action.Get("breadth"); got != int64(2) {
		t.Errorf("breadth = %v, want 2", got)
	}
	if got := action.Get("evidence"); got != "E. Affects 2 repositories." {
		t.Errorf("evidence = %v", got)
	}
}

func TestPrioritizeActionsSingleScopeActionKeepsEvidenceVerbatim(t *testing.T) {
	actions := PrioritizeActions([]any{stubFinding("c", "T", "medium", []any{"r1"}, "E.")})
	assertDump(t, actions, `[{"category": "c", "title": "T", "priority": "medium", "evidence": "E.", "affected_scope": ["r1"], "breadth": 1}]`)
}

func TestPrioritizeActionsSameTitleDifferentCategoryStaysSeparate(t *testing.T) {
	actions := PrioritizeActions([]any{
		stubFinding("a", "T", "high", []any{"r1"}, "E."),
		stubFinding("b", "T", "high", []any{"r1"}, "E."),
	})
	if len(actions) != 2 {
		t.Fatalf("len(actions) = %d, want 2", len(actions))
	}
	for i, want := range []string{"a", "b"} {
		a := pysem.AsObj(actions[i])
		if a.Get("category") != want || a.Get("breadth") != int64(1) {
			t.Errorf("actions[%d] = (%v, %v), want (%q, 1)", i, a.Get("category"), a.Get("breadth"), want)
		}
	}
}

func TestPrioritizeActionsTitleTiebreakIsAlphabetical(t *testing.T) {
	actions := PrioritizeActions([]any{
		stubFinding("x", "Beta", "high", []any{"r1"}, "E."),
		stubFinding("y", "Alpha", "high", []any{"r1"}, "E."),
	})
	if got := pysem.AsObj(actions[0]).Get("title"); got != "Alpha" {
		t.Errorf("actions[0] title = %v, want Alpha", got)
	}
	if got := pysem.AsObj(actions[1]).Get("title"); got != "Beta" {
		t.Errorf("actions[1] title = %v, want Beta", got)
	}
}

func TestPrioritizeActionsInputFindingsAreNotMutated(t *testing.T) {
	findings := []any{
		stubFinding("testing", "T", "high", []any{"r1"}, "E."),
		stubFinding("testing", "T", "high", []any{"r2"}, "E."),
	}
	snapshot := dumps(findings)
	PrioritizeActions(findings)
	if got := dumps(findings); got != snapshot {
		t.Errorf("findings mutated:\n got: %s\nwant: %s", got, snapshot)
	}
}

func TestChangedFileSummaryEmptyInputsProduceExactZeroSummary(t *testing.T) {
	assertDump(t, ChangedFileSummary([]any{}, []any{}), `{"files_analysed": 0, "files_excluded": 0, "files_failed": 0, "repositories_touched": 0, "authored_commit_files": 0, "authored_pr_files": 0, "findings": 0, "by_category": {}}`)
}

func TestChangedFileSummaryMixedReportsSummariseExactly(t *testing.T) {
	reports := []any{
		obj("analysis_status", "succeeded", "repository", "api", "attribution", "authored_commit"),
		obj("analysis_status", "truncated", "repository", "web", "attribution", "authored_pr"),
		obj("analysis_status", "succeeded", "repository", "api", "attribution", "authored_pr"),
		obj("analysis_status", "excluded", "repository", "web", "attribution", "authored_commit"),
		obj("analysis_status", "failed", "repository", "api", "attribution", "authored_commit"),
		obj("analysis_status", "succeeded", "repository", "", "attribution", "authored_commit"),
	}
	findings := []any{obj("category", "testing"), obj("category", "hotspot"), obj("category", "testing")}
	// Attribution counts only cover eligible files: the failed authored
	// commit is out, and the empty repository never counts as touched. The
	// by_category key order (sorted) is part of the dump.
	assertDump(t, ChangedFileSummary(reports, findings), `{"files_analysed": 4, "files_excluded": 1, "files_failed": 1, "repositories_touched": 2, "authored_commit_files": 2, "authored_pr_files": 2, "findings": 3, "by_category": {"hotspot": 1, "testing": 2}}`)
}

func TestAnalyseRepositoryHealthFullyEquippedRepoYieldsNoFindings(t *testing.T) {
	reports, findings := AnalyseRepositoryHealth([]any{repoObj()}, map[[2]string]bool{})
	if len(findings) != 0 {
		t.Errorf("findings = %s, want []", dumps(findings))
	}
	assertDump(t, reports, `[{"provider": "github", "container": "acme", "repository": "acme/api", "url": "https://example.test/acme/api", "default_branch": "main", "files_scanned": 7, "status": "succeeded", "findings": []}]`)
}

func TestAnalyseRepositoryHealthBareRepoYieldsAllBaselineFindingsInOrder(t *testing.T) {
	_, findings := AnalyseRepositoryHealth([]any{repoObj("paths", []any{})}, map[[2]string]bool{})
	wantCategories := []string{
		"documentation", "architecture", "testing", "delivery", "maintainability", "ownership", "security",
	}
	if got := categoriesOf(findings); len(got) != len(wantCategories) {
		t.Fatalf("categories = %v, want %v", got, wantCategories)
	} else {
		for i, want := range wantCategories {
			if got[i] != want {
				t.Errorf("categories[%d] = %q, want %q", i, got[i], want)
			}
		}
	}
	assertDump(t, findings[0], `{"id": "github:acme/api:documentation:add-a-repository-readme", "category": "documentation", "title": "Add a repository README", "detail": "Document the repository purpose, setup, test, and release workflow.", "priority": "high", "impact": "Improves delivery safety and maintainability", "confidence": "high", "evidence": "No README was found on the default branch.", "link": "https://example.test/acme/api", "affected_scope": ["acme/api"], "next_steps": ["Document the repository purpose, setup, test, and release workflow."], "owner_role": "Repository maintainer", "effort": "small", "completion_check": "Confirm add a repository readme for acme/api."}`)
}

func TestAnalyseRepositoryHealthOperationsFindingRequiresInfraWithoutRunbook(t *testing.T) {
	withInfra := repoObj("paths", append(healthyPaths(), "Dockerfile"))
	_, findings := AnalyseRepositoryHealth([]any{withInfra}, map[[2]string]bool{})
	if got := categoriesOf(findings); len(got) != 1 || got[0] != "operations" {
		t.Fatalf("categories = %v, want [operations]", got)
	}
	f := pysem.AsObj(findings[0])
	if f.Get("priority") != "high" || f.Get("confidence") != "medium" {
		t.Errorf("operations finding = (%v, %v), want (high, medium)", f.Get("priority"), f.Get("confidence"))
	}
	withRunbook := repoObj("paths", append(healthyPaths(), "Dockerfile", "docs/runbook.md"))
	if _, clean := AnalyseRepositoryHealth([]any{withRunbook}, map[[2]string]bool{}); len(clean) != 0 {
		t.Errorf("with runbook findings = %s, want []", dumps(clean))
	}
}

func TestAnalyseRepositoryHealthAzdoActivityComesFromActiveNamesNotTheFlag(t *testing.T) {
	named := repoObj("provider", "azdo", "name", "Api", "active", false)
	flagged := repoObj("provider", "azdo", "name", "Other", "active", true)
	reports, _ := AnalyseRepositoryHealth([]any{named, flagged}, map[[2]string]bool{{"azdo", "api"}: true})
	if len(reports) != 2 {
		t.Fatalf("len(reports) = %d, want 2", len(reports))
	}
	first := pysem.AsObj(reports[0])
	if first.Get("status") != "succeeded" || first.Get("repository") != "Api" {
		t.Errorf("reports[0] = (%v, %v), want (succeeded, Api)", first.Get("status"), first.Get("repository"))
	}
	if got := pysem.AsObj(reports[1]).Get("status"); got != "unchanged" {
		t.Errorf("reports[1] status = %v, want unchanged", got)
	}
}

func TestAnalyseRepositoryHealthInactiveGithubRepoIsReportedUnchangedVerbatim(t *testing.T) {
	repo := repoObj("active", false)
	reports, findings := AnalyseRepositoryHealth([]any{repo}, map[[2]string]bool{})
	if len(findings) != 0 {
		t.Errorf("findings = %s, want []", dumps(findings))
	}
	assertDump(t, reports, `[{"provider": "github", "container": "acme", "name": "acme/api", "active": false, "url": "https://example.test/acme/api", "default_branch": "main", "paths": ["README.md", "docs/adr/0001-record-decisions.md", "tests/test_api.py", ".github/workflows/ci.yml", "pyproject.toml", ".github/CODEOWNERS", ".github/dependabot.yml"], "status": "unchanged", "findings": []}]`)
}

func TestAnalyseRepositoryHealthDiscoveryErrorRepoIsSkippedEntirely(t *testing.T) {
	reports, findings := AnalyseRepositoryHealth([]any{repoObj("discovery_error", "403")}, map[[2]string]bool{})
	if len(reports) != 0 || len(findings) != 0 {
		t.Errorf("reports/findings = %s / %s, want empty", dumps(reports), dumps(findings))
	}
}

func TestAnalyseRepositoryHealthListingErrorMarksReportTruncated(t *testing.T) {
	reports, _ := AnalyseRepositoryHealth([]any{repoObj("error", "timeout")}, map[[2]string]bool{})
	if got := pysem.AsObj(reports[0]).Get("status"); got != "truncated" {
		t.Errorf("status = %v, want truncated", got)
	}
}

func TestRepositoryHealthSummaryEmptyInputsProduceExactZeroSummary(t *testing.T) {
	assertDump(t, RepositoryHealthSummary([]any{}, []any{}), `{"repositories_analysed": 0, "repositories_unchanged": 0, "files_inventoried": 0, "findings": 0, "by_category": {}}`)
}

func TestRepositoryHealthSummaryCountsAndSortedCategoriesAreExact(t *testing.T) {
	reports := []any{
		obj("status", "succeeded", "files_scanned", json.Number("3")),
		obj("status", "truncated", "files_scanned", json.Number("2")),
		obj("status", "unchanged"),
	}
	findings := []any{obj("category", "testing"), obj("category", "delivery"), obj("category", "testing")}
	assertDump(t, RepositoryHealthSummary(reports, findings), `{"repositories_analysed": 2, "repositories_unchanged": 1, "files_inventoried": 5, "findings": 3, "by_category": {"delivery": 1, "testing": 2}}`)
}
